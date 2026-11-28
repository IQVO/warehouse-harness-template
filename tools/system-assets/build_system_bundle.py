#!/usr/bin/env python3
"""Build the System Assets folder of one application release.

    system/<version>/   (plain files, no archive)

The folder is the System Assets & Interaction Standard (STANDARD.md, beside
this script) packaged for ONE released version. Nothing in the application's
codebase is modified (only system/ is written) and every input is pinned to
the release, except the business overlay.

Inputs
  * git tag v<version> of the repo  - apis/openapi*.yaml, apis/asyncapi.yaml,
    chart values, use-case / port names, pinned producer AsyncAPI contracts
    (read with `git show <tag>:<path>`, no checkout).
  * the released Helm chart          - oci://ghcr.io/iqvo/<app> --version <version>
    (its version MUST equal <version>: MC-5, version == chart version).
  * the live k3s cluster              - MCP tools/resources/prompts lists and the
    served A2A Agent Card, fetched through `kubectl port-forward`, ONLY after
    proving every serving pod runs the tag's commit.
  * IQVO/ecosystem origin/develop     - subdomain, classification, tier, purpose,
    topics (catalog.contexts.mjs) and EventCatalog badges (tags).
  * system/interactions.overlay.yaml - in the APP repo (or --overlay PATH): the business facets
    (capability, process, use case, triggers, purpose) that no machine artifact carries. The caller
    owns the "why", so the repo versions it next to the release folders.

Every reference in the interactions overlay is resolved against those inputs;
any dangling reference or uncovered published/consumed event aborts the build.

Usage
  build_system_bundle.py --app <repo> [--version X.Y.Z] [--repo <clone or worktree of the app>]
                         [--overlay PATH] [--namespace warehouse-systems] [--strict]

Run it against a WORKTREE of the app off origin/develop (--repo): git objects of the tag are read
from it and system/<version>/ is written into it, ready for a PR. Never against a dirty main checkout.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import shutil
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import derive_tags  # noqa: E402  (same folder)

HOME = pathlib.Path.home()
FLEET = pathlib.Path(os.environ.get("WAREHOUSE_SYSTEMS_HOME", HOME / "warehouse-systems"))
ECOSYSTEM = FLEET / "ecosystem"
OVERLAY_NAME = "interactions.overlay.yaml"
OCI = "oci://ghcr.io/iqvo"
GITHUB_ORG = "IQVO"
AGENT_ROUTER_EDGE = "http://localhost:8010"  # warehouse-infra ADR 0015 (scripts/k3s-edges.sh)
MCP_PROTOCOL = "2026-07-28"
CE_TYPE = re.compile(r"com\.(?:warehouse|retailnetwork)\.[a-z]+\.[a-z-]+\.[a-z]+\.[A-Za-z]+(?:\.v\d+)?")
TECHNOLOGIES = {"rest", "event.publish", "mcp", "a2a"}


class BuildError(Exception):
    pass


class UnsupportedProtocol(Exception):
    def __init__(self, supported: list[str]):
        super().__init__(f"server supports {supported}")
        self.supported = supported


def run(*cmd: str, cwd: pathlib.Path | None = None) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise BuildError(f"{' '.join(cmd)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout


def dump_yaml(doc: object, header: str = "") -> str:
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
    return header + body


def dump_json(doc: object) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- release (git tag + chart)

class Release:
    def __init__(self, repo: pathlib.Path, app: str, version: str | None):
        self.repo, self.app = repo, app
        run("git", "-C", str(repo), "fetch", "--quiet", "--tags", "origin")
        if version is None:
            version = run("gh", "release", "view", "-R", f"{GITHUB_ORG}/{app}", "--json", "tagName",
                          "--jq", ".tagName").strip().removeprefix("v")
        self.version = version
        self.tag = f"v{version}"
        run("gh", "release", "view", self.tag, "-R", f"{GITHUB_ORG}/{app}", "--json", "tagName")
        self.commit = run("git", "-C", str(repo), "rev-parse", f"{self.tag}^{{commit}}").strip()
        self.files = set(run("git", "-C", str(repo), "ls-tree", "-r", "--name-only", self.commit).split())

    def show(self, path: str) -> str:
        return run("git", "-C", str(self.repo), "show", f"{self.commit}:{path}")

    def has(self, path: str) -> bool:
        return path in self.files

    def yaml(self, path: str) -> dict:
        return yaml.safe_load(self.show(path))

    def go_type_names(self, directory: str) -> set[str]:
        decl = re.compile(r"(?m)^type ([A-Z][A-Za-z0-9]*) (?:struct|interface)")
        names: set[str] = set()
        for f in self.files:
            if f.startswith(directory + "/") and f.endswith(".go") and not f.endswith("_test.go") \
                    and "/" not in f[len(directory) + 1:]:
                names.update(decl.findall(self.show(f)))
        return names

    def check_chart(self) -> dict:
        chart = yaml.safe_load(run("helm", "show", "chart", f"{OCI}/{self.app}", "--version", self.version))
        if str(chart.get("version")) != self.version:
            raise BuildError(f"released chart version {chart.get('version')} != {self.version} (MC-5)")
        return chart


# --------------------------------------------------------------------------- AsyncAPI 2.6 / 3.x index

def async_channels(doc: dict) -> dict[str, dict[str, set[str]]]:
    """channel -> {"ops": operation ids, "types": CloudEvents types of its messages}."""
    messages = (doc.get("components") or {}).get("messages") or {}
    msg_types = {name: set(CE_TYPE.findall(json.dumps(m))) for name, m in messages.items()}

    def types_of(node: object) -> set[str]:
        raw = json.dumps(node)
        out = set(CE_TYPE.findall(raw))
        for ref in re.findall(r"#/components/messages/([A-Za-z0-9_.-]+)", raw):
            out |= msg_types.get(ref, set())
        return out

    out: dict[str, dict[str, set[str]]] = {}
    if str(doc.get("asyncapi", "")).startswith("2."):
        for name, ch in (doc.get("channels") or {}).items():
            entry = out.setdefault(name, {"ops": set(), "types": set()})
            for verb in ("publish", "subscribe"):  # producers disagree on the 2.6 verb
                op = ch.get(verb)
                if isinstance(op, dict):
                    if op.get("operationId"):
                        entry["ops"].add(op["operationId"])
                    entry["types"] |= types_of(op.get("message"))
    else:  # 3.x: channels keyed by id with an address; operations reference channels
        for cid, ch in (doc.get("channels") or {}).items():
            out[ch.get("address", cid)] = {"ops": set(), "types": types_of(ch.get("messages"))}
        for oid, op in (doc.get("operations") or {}).items():
            ref = (op.get("channel") or {}).get("$ref", "").split("/")[-1]
            ch = (doc.get("channels") or {}).get(ref, {})
            out.setdefault(ch.get("address", ref), {"ops": set(), "types": set()})["ops"].add(oid)
    return out


# --------------------------------------------------------------------------- live cluster

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def port_forward(namespace: str, service: str, port: int):
    local = free_port()
    proc = subprocess.Popen(["kubectl", "port-forward", "-n", namespace, f"svc/{service}", f"{local}:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", local), timeout=1):
                break
            if proc.poll() is not None:
                raise BuildError(f"port-forward svc/{service}: {proc.stderr.read().strip()}")
            time.sleep(0.3)
        else:
            raise BuildError(f"port-forward svc/{service} did not become ready")
        yield f"http://127.0.0.1:{local}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def verify_pods_run_commit(namespace: str, service: str, commit: str) -> list[str]:
    svc = json.loads(run("kubectl", "get", "svc", service, "-n", namespace, "-o", "json"))
    selector = ",".join(f"{k}={v}" for k, v in svc["spec"]["selector"].items())
    pods = json.loads(run("kubectl", "get", "pods", "-n", namespace, "-l", selector, "-o", "json"))["items"]
    running = [p for p in pods if p["status"].get("phase") == "Running"]
    if not running:
        raise BuildError(f"no running pod behind svc/{service}")
    images = sorted({c["image"] for p in running for c in p["spec"]["containers"]})
    short = commit[:7]
    stale = [i for i in images if short not in i.rsplit(":", 1)[-1]]
    if stale:
        raise BuildError(f"svc/{service} serves {stale}, not the release commit {short}: "
                         "deploy the release first, the bundle must describe what runs")
    return images


def deployed_chart_labels(namespace: str, service: str) -> str:
    svc = json.loads(run("kubectl", "get", "svc", service, "-n", namespace, "-o", "json"))
    return svc["metadata"].get("labels", {}).get("helm.sh/chart", "")


class MCPClient:
    """Minimal Streamable HTTP client: initialize, then paginated list calls."""

    def __init__(self, base: str):
        self.url, self.session, self.next_id = base.rstrip("/") + "/mcp", None, 0

    protocol = MCP_PROTOCOL

    def _post(self, payload: dict) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": self.protocol}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                self.session = resp.headers.get("Mcp-Session-Id") or self.session
                body = resp.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            with contextlib.suppress(ValueError, KeyError, TypeError):
                err = json.loads(body)["error"]
                if payload.get("method") == "initialize" and err.get("data", {}).get("supported"):
                    raise UnsupportedProtocol(err["data"]["supported"]) from e
            raise BuildError(f"MCP {payload.get('method')}: HTTP {e.code} {body[:300]}") from e
        if "id" not in payload:
            return None
        if body.lstrip().startswith("{"):
            msg = json.loads(body)
        else:  # SSE: first data line carrying our id
            msg = next(json.loads(line[5:]) for line in body.splitlines()
                       if line.startswith("data:") and line[5:].strip())
        if "error" in msg:
            raise BuildError(f"MCP {payload['method']}: {msg['error']}")
        return msg["result"]

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        return self._post({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})

    def initialize(self) -> dict:
        params = {"capabilities": {}, "clientInfo": {"name": "system-bundle-builder", "version": "1"}}
        try:
            result = self.call("initialize", {"protocolVersion": self.protocol, **params})
        except UnsupportedProtocol as e:  # negotiate down to the newest version the release speaks
            self.protocol = max(e.supported)
            result = self.call("initialize", {"protocolVersion": self.protocol, **params})
        self.protocol = result["protocolVersion"]
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_all(self, method: str, key: str) -> list[dict]:
        items, cursor = [], None
        while True:
            res = self.call(method, {"cursor": cursor} if cursor else {})
            items += res.get(key, [])
            cursor = res.get("nextCursor")
            if not cursor:
                return items


def fetch_mcp(base: str, card_name: str) -> tuple[dict, dict]:
    client = MCPClient(base)
    init = client.initialize()
    caps = init.get("capabilities") or {}
    doc = {"apiVersion": "warehouse/mcp-primitives/v1", "server": card_name,
           "tools": sorted(client.list_all("tools/list", "tools"), key=lambda t: t["name"]),
           "resources": [], "resourceTemplates": [], "prompts": []}
    if "resources" in caps:
        doc["resources"] = sorted(client.list_all("resources/list", "resources"), key=lambda r: r["uri"])
        doc["resourceTemplates"] = sorted(client.list_all("resources/templates/list", "resourceTemplates"),
                                          key=lambda r: r["uriTemplate"])
    if "prompts" in caps:
        doc["prompts"] = sorted(client.list_all("prompts/list", "prompts"), key=lambda p: p["name"])
    return init, doc


def fetch_agent_card(base: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + "/.well-known/agent-card.json", timeout=20) as resp:
        return json.loads(resp.read().decode())


# --------------------------------------------------------------------------- ecosystem

def ecosystem_context(app: str) -> tuple[dict, dict, list[str], dict[str, str]]:
    run("git", "-C", str(ECOSYSTEM), "fetch", "--quiet", "origin")
    src = run("git", "-C", str(ECOSYSTEM), "show", "origin/develop:catalog.contexts.mjs")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
        f.write(src)
    try:
        js = (f"const m = await import({json.dumps(pathlib.Path(f.name).as_uri())});"
              "console.log(JSON.stringify({s: m.SUBDOMAINS, c: m.CONTEXTS}))")
        data = json.loads(run("node", "--input-type=module", "-e", js))
    finally:
        os.unlink(f.name)
    ctx = data["c"].get(app)
    if ctx is None:
        raise BuildError(f"{app} is not in ecosystem catalog.contexts.mjs CONTEXTS")
    sub = dict(data["s"][ctx["subdomain"]], id=ctx["subdomain"])
    tags = derive_tags.derive_all().get(app, {}).get("tags", [])
    topic_owner = {t: name for name, c in data["c"].items() for t in c.get("topics", [])}
    return ctx, sub, tags, topic_owner


# --------------------------------------------------------------------------- documents

def first_sentence(text: str) -> str:
    return re.split(r"(?<=\.)\s", text.strip(), maxsplit=1)[0]


def website_url(app: str) -> str | None:
    """The repo's GitHub Pages site, from the repo setting (deterministic, no HTTP probe)."""
    if run("gh", "api", f"repos/{GITHUB_ORG}/{app}", "--jq", ".has_pages").strip() == "true":
        return f"https://{GITHUB_ORG.lower()}.github.io/{app}/"
    return None


def build_application(app: str, version: str, ctx: dict, sub: dict, tags: list[str], chart: dict) -> dict:
    return {
        "apiVersion": "warehouse/application/v1",
        "application": {
            "id": app,
            "name": app.replace("-", " ").title(),
            "kind": "bounded-context",
            "version": version,
            "description": chart.get("description", ""),
            "repository": f"https://github.com/{GITHUB_ORG}/{app}",
            "owners": ["claudioed"],
            "lifecycle": "production",
        },
        "subdomain": {"id": sub["id"], "name": sub["name"], "summary": sub["summary"]},
        "boundedContext": {
            "id": app,
            "name": app.replace("-", " ").title(),
            "classification": ctx["classification"],
            "tier": ctx["tier"],
            "purpose": ctx["purpose"],
            "publishes": ctx.get("topics", []),
        },
        "tags": tags,
        "assets": "system-assets.yaml",
        "interactions": "interactions.yaml",
    }


def server_card_name(app: str, sub_id: str, ctx: dict) -> str:
    """Reverse-DNS card name. Retail-network contexts use the org and tier of their CloudEvents types."""
    if sub_id in ("retail-network", "storefront"):
        return f"com.retailnetwork.{ctx.get('ceSegment') or sub_id}/{app}"
    return f"com.warehouse.{sub_id}/{app}"


def build_server_card(card_name: str, app: str, version: str, purpose: str, route_path: str,
                      negotiated: str, site: str | None) -> dict:
    card = {
        "name": card_name,
        "version": version,
        "title": f"{app.replace('-', ' ').title()} MCP",
        "description": first_sentence(purpose),
        **({"websiteUrl": site} if site else {}),
        "repository": {"url": f"https://github.com/{GITHUB_ORG}/{app}", "source": "github"},
        "remotes": [{"type": "streamable-http", "url": AGENT_ROUTER_EDGE + route_path,
                     "protocolVersions": [negotiated]}],
    }
    return card


# --------------------------------------------------------------------------- producer contracts

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def rest_ops_of(doc: dict) -> dict[str, tuple[str, str]]:
    """operationId -> (METHOD, path) of one OpenAPI document (path items also carry `parameters:` arrays)."""
    out: dict[str, tuple[str, str]] = {}
    for path, item in (doc.get("paths") or {}).items():
        for method, op in (item or {}).items():
            if method in HTTP_METHODS and isinstance(op, dict) and op.get("operationId"):
                out[op["operationId"]] = (method.upper(), path)
    return out


class Producers:
    """Contracts of the contexts this app talks to, for resolving subscriptions and outbound calls.

    Resolution order, per producer: the copy this repo pinned under an adapter's testdata at the release tag
    (STANDARD A-5), else the producer's own contract at ITS latest GitHub release (read from its local clone).
    MCP tools and A2A skills have no pinned copy anywhere, so they are read live from the producer's Services."""

    def __init__(self, rel: "Release", namespace: str):
        self.rel, self.ns = rel, namespace
        self._cache: dict[tuple[str, str], object] = {}

    def _pin(self, producer: str, kind: str) -> str | None:
        want = f"{producer}.{kind}.yaml"
        for p in sorted(self.rel.files):
            if p.endswith("/testdata/" + want) and ("/inbound/" in p or "/outbound/" in p):
                return self.rel.show(p)
        return None

    def _released(self, producer: str, path: str) -> tuple[str | None, str | None]:
        clone = FLEET / producer
        if not (clone / ".git").exists():
            return None, None
        run("git", "-C", str(clone), "fetch", "--quiet", "--tags", "origin")
        try:
            tag = run("gh", "release", "view", "-R", f"{GITHUB_ORG}/{producer}", "--json", "tagName",
                      "--jq", ".tagName").strip()
            return run("git", "-C", str(clone), "show", f"{tag}:{path}"), tag
        except BuildError:
            return None, None

    def asyncapi(self, producer: str) -> dict | None:
        key = (producer, "asyncapi")
        if key not in self._cache:
            text = self._pin(producer, "asyncapi") or self._released(producer, "apis/asyncapi.yaml")[0]
            self._cache[key] = async_channels(yaml.safe_load(text)) if text else None
        return self._cache[key]

    def rest(self, producer: str) -> dict | None:
        key = (producer, "openapi")
        if key not in self._cache:
            text = self._pin(producer, "openapi") or self._released(producer, "apis/openapi.yaml")[0]
            self._cache[key] = rest_ops_of(yaml.safe_load(text)) if text else None
        return self._cache[key]

    def _live_port(self, producer: str, surface: str) -> int | None:
        text, _ = self._released(producer, f"charts/{producer}/values.yaml")
        return int(yaml.safe_load(text)[surface]["service"]["port"]) if text else None

    def mcp(self, producer: str) -> dict | None:
        """tool name -> readOnlyHint, from the producer's live MCP Service."""
        key = (producer, "mcp")
        if key not in self._cache:
            self._cache[key] = None
            port = self._live_port(producer, "mcp")
            if port and service_exists(self.ns, f"{producer}-mcp"):
                with port_forward(self.ns, f"{producer}-mcp", port) as base:
                    _, prims = fetch_mcp(base, producer)
                self._cache[key] = {t["name"]: bool((t.get("annotations") or {}).get("readOnlyHint"))
                                    for t in prims["tools"]}
        return self._cache[key]

    def a2a(self, producer: str) -> set[str] | None:
        key = (producer, "a2a")
        if key not in self._cache:
            self._cache[key] = None
            port = self._live_port(producer, "a2a")
            if port and service_exists(self.ns, f"{producer}-a2a"):
                with port_forward(self.ns, f"{producer}-a2a", port) as base:
                    self._cache[key] = {s["id"] for s in fetch_agent_card(base).get("skills", [])}
        return self._cache[key]


def service_exists(namespace: str, service: str) -> bool:
    res = subprocess.run(["kubectl", "get", "svc", service, "-n", namespace, "-o", "name"],
                         capture_output=True, text=True)
    return res.returncode == 0


def outbound_violations(where: str, tech: str, op: dict, producers: "Producers | None") -> list[str]:
    """rest / mcp / a2a: the operation must exist in the producer's contract and its effect must match."""
    kind = {"rest": "openapi", "mcp": "mcp", "a2a": "a2a"}[tech]
    asset = op.get("asset", "")
    producer = asset.removesuffix("/" + kind)
    if not asset.endswith("/" + kind) or not producer:
        return [f"{where}: {tech} must target <producer>/{kind}, got {asset!r}"]
    if producers is None:
        return [f"{where}: no producer index available to resolve {asset}"]
    if tech == "rest":
        ops = producers.rest(producer)
        if ops is None:
            return [f"{where}: no OpenAPI contract for {producer!r}: neither pinned in this repo nor in a local clone with a release"]
        if op.get("id") not in ops:
            return [f"{where}: operationId {op.get('id')!r} is not in the {producer} OpenAPI contract"]
        method, path = ops[op["id"]]
        loc = op.get("rest") or {}
        v = []
        if (loc.get("method"), loc.get("path")) != (method, path):
            v.append(f"{where}: rest locator {loc.get('method')} {loc.get('path')} != {method} {path} in the contract")
        if op.get("effect") != ("read" if method in ("GET", "HEAD") else "write"):
            v.append(f"{where}: effect {op.get('effect')!r} does not match HTTP {method} (O-2)")
        return v
    if tech == "mcp":
        tools = producers.mcp(producer)
        if tools is None:
            return [f"{where}: {producer}-mcp is not running (cannot read its tools)"]
        if op.get("id") not in tools:
            return [f"{where}: tool {op.get('id')!r} is not served by {producer}-mcp"]
        if op.get("effect") != ("read" if tools[op["id"]] else "write"):
            return [f"{where}: effect {op.get('effect')!r} does not match readOnlyHint of {op['id']} (O-2)"]
        return []
    skills = producers.a2a(producer)
    if skills is None:
        return [f"{where}: {producer}-a2a is not running (cannot read its agent card)"]
    return [] if op.get("id") in skills else [f"{where}: skill {op.get('id')!r} is not in the {producer} agent card"]


# --------------------------------------------------------------------------- interaction resolution

def resolve_interactions(app: str, ix: dict, own: dict, producers, rest_ops: set[str],
                         tools: set[str], skills: set[str], use_cases: set[str], ports: set[str],
                         publishes: list[str], outbound: "Producers | None" = None) -> list[str]:
    """`producers`: dict {context: channels} (tests) or a callable context -> channels | None."""
    lookup = producers.get if isinstance(producers, dict) else producers
    v: list[str] = []
    consumed: set[str] = set()
    covered_in: set[tuple[str, str]] = set()
    for s in ix.get("subscriptions", []):
        where, op = f"subscription {s.get('id')}", s.get("operation", {})
        producer = op.get("asset", "").removesuffix("/asyncapi")
        ev = op.get("event", {})
        ch = ev.get("channel")
        pin = lookup(producer)
        if pin is None:
            v.append(f"{where}: no AsyncAPI contract for producer {producer!r}: neither pinned in this repo "
                     "(A-5) nor in a local clone with a GitHub release")
            continue
        if op.get("id") not in pin.get(ch, {}).get("ops", set()):
            v.append(f"{where}: producer op {op.get('id')!r} is not on {ch} in the pinned {producer} contract")
        if op.get("consumerOperation") not in own.get(ch, {}).get("ops", set()):
            v.append(f"{where}: consumerOperation {op.get('consumerOperation')!r} is not on {ch} in apis/asyncapi.yaml")
        for t in ev.get("types", []):
            if t not in pin.get(ch, {}).get("types", set()) or t not in own.get(ch, {}).get("types", set()):
                v.append(f"{where}: type {t} missing from the producer pin or apis/asyncapi.yaml")
            consumed.add(t)
            covered_in.add((ch, t))
        b = s.get("business", {})
        if not (b.get("useCases") or b.get("projection")):
            v.append(f"{where}: needs useCases or a projection")
        v += [f"{where}: use case {u!r} not in internal/application/usecases (C-2)"
              for u in b.get("useCases", []) if u not in use_cases]
        if b.get("projection") and b["projection"] not in ports:
            v.append(f"{where}: projection port {b['projection']!r} not in internal/application/ports")
        v += [f"{where}: business.{k} is required" for k in ("capability", "process", "purpose") if not b.get(k)]

    seen: set[str] = set()
    covered_out: set[tuple[str, str]] = set()
    for i in ix.get("interactions", []):
        where, op, b = f"interaction {i.get('id')}", i.get("operation", {}), i.get("business", {})
        if i.get("id") in seen:
            v.append(f"{where}: duplicate id")
        seen.add(i.get("id"))
        tech = i.get("technology")
        if tech not in TECHNOLOGIES:
            v.append(f"{where}: technology {tech!r} not in {sorted(TECHNOLOGIES)}")
            continue
        if tech == "event.publish":
            ev = op.get("event") or {}
            ch = own.get(ev.get("channel"), {})
            if op.get("asset") != f"{app}/asyncapi" or op.get("effect") != "write":
                v.append(f"{where}: event.publish must target {app}/asyncapi with effect write")
            if op.get("id") not in ch.get("ops", set()) or ev.get("type") not in ch.get("types", set()):
                v.append(f"{where}: {op.get('id')} / {ev.get('type')} not declared on {ev.get('channel')}")
            covered_out.add((ev.get("channel"), ev.get("type")))
        else:
            v += outbound_violations(where, tech, op, outbound)
        if b.get("useCase") not in use_cases:
            v.append(f"{where}: use case {b.get('useCase')!r} not in internal/application/usecases (C-2)")
        v += [f"{where}: business.{k} is required" for k in ("capability", "process", "purpose", "triggers")
              if not b.get(k)]
        for t in b.get("triggers", []):
            kind, via, ref = t.get("kind"), t.get("via"), t.get("ref")
            ok = {("event", None): ref in consumed, ("api", "rest"): ref in rest_ops,
                  ("api", "mcp"): ref in tools, ("api", "a2a"): ref in skills}.get((kind, via))
            if ok is None or not t.get("actor"):
                v.append(f"{where}: malformed trigger {t}")
            elif not ok:
                v.append(f"{where}: trigger {kind}/{via or ''} {ref!r} does not resolve")

    # coverage: every published and every consumed type is mapped
    for topic in publishes:
        for t in sorted(own.get(topic, {}).get("types", set())):
            if (topic, t) not in covered_out:
                v.append(f"coverage: {t} on {topic} has no event.publish interaction")
    for ch, entry in sorted(own.items()):
        if ch in publishes:
            continue
        for t in sorted(entry["types"]):
            if (ch, t) not in covered_in:
                v.append(f"coverage: consumed {t} on {ch} has no subscription")
    return v


# --------------------------------------------------------------------------- packaging

def write_folder(dest: pathlib.Path, files: dict[str, str]) -> None:
    """Replace dest with exactly these files (stale files from an earlier build never survive)."""
    staging = dest.with_name(f".{dest.name}.tmp")
    if staging.exists():
        shutil.rmtree(staging)
    for rel_path in sorted(files):
        target = staging / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(files[rel_path])
    if dest.exists():
        shutil.rmtree(dest)
    staging.rename(dest)


def build(args: argparse.Namespace) -> pathlib.Path:
    app = args.app
    repo = pathlib.Path(args.repo or FLEET / app).expanduser()
    rel = Release(repo, app, args.version)
    version = rel.version
    print(f"[1/6] release {rel.tag} = {rel.commit[:7]} ({app})")
    chart = rel.check_chart()
    print(f"[2/6] released chart {OCI}/{app}:{chart['version']} ok")

    ctx, sub, tags, _topic_owner = ecosystem_context(app)
    print(f"[3/6] ecosystem: subdomain {sub['id']}, {ctx['classification']}, {len(tags)} tags")

    values = rel.yaml(f"charts/{app}/values.yaml")
    ns = args.namespace
    # An agent surface exists iff its Service is deployed: charts default mcp/a2a to disabled and the
    # platform values switch them on, so the chart alone cannot say. A deployed surface MUST be in the chart.
    surfaces = [s for s in ("mcp", "a2a") if service_exists(ns, f"{app}-{s}")]
    for s in surfaces:
        if not (values.get(s) or {}).get("service"):
            raise BuildError(f"{app}-{s} is deployed but charts/{app}/values.yaml has no {s}.service at {rel.tag}")
    images = verify_pods_run_commit(ns, app, rel.commit)
    for s in surfaces:
        images += verify_pods_run_commit(ns, f"{app}-{s}", rel.commit)
    print(f"[4/6] cluster runs the release ({'REST+events' + ''.join('+' + s.upper() for s in surfaces)}): "
          f"{sorted(set(images))}")
    card_name = server_card_name(app, sub["id"], ctx)
    init, primitives, agent_card, server_card = {}, {}, {}, {}
    drift = []  # runtime facts that break the standard; recorded in the bundle, fatal with --strict
    if "mcp" in surfaces:
        with port_forward(ns, f"{app}-mcp", int(values["mcp"]["service"]["port"])) as base:
            init, primitives = fetch_mcp(base, card_name)
        server_info = init.get("serverInfo", {})
        if server_info.get("version") != version:
            drift.append(f"MCP initialize serverInfo {server_info.get('name')}@{server_info.get('version')!r} "
                         f"!= release {version} (MC-5)")
        if init["protocolVersion"] != MCP_PROTOCOL:
            drift.append(f"MCP server negotiates {init['protocolVersion']}, fleet target is {MCP_PROTOCOL}")
        server_card = build_server_card(card_name, app, version, ctx["purpose"], values["mcp"]["route"]["path"],
                                        init["protocolVersion"], website_url(app))
    if "a2a" in surfaces:
        with port_forward(ns, f"{app}-a2a", int(values["a2a"]["service"]["port"])) as base:
            agent_card = fetch_agent_card(base)
        if agent_card.get("version") != version:
            drift.append(f"served A2A Agent Card version {agent_card.get('version')!r} != release {version} "
                         "(SERVICE_VERSION is rendered from the image tag)")
    deployed_chart = deployed_chart_labels(ns, app)
    if deployed_chart != f"{app}-{version}":
        drift.append(f"cluster runs chart {deployed_chart!r}, not the released chart {app}-{version}")
    if drift and args.strict:
        raise BuildError("runtime drift (--strict):\n  - " + "\n  - ".join(drift))
    for d in drift:
        print(f"      drift: {d}")
    print(f"[5/6] live: MCP {len(primitives.get('tools', []))} tools "
          f"({len(primitives.get('resources', []))} resources, {len(primitives.get('prompts', []))} prompts), "
          f"A2A {len(agent_card.get('skills', []))} skills; surfaces absent: "
          f"{[s for s in ('mcp', 'a2a') if s not in surfaces] or 'none'}")

    # contracts at the tag
    specs = {p.removeprefix("apis/"): rel.show(p) for p in sorted(rel.files)
             if re.fullmatch(r"apis/(openapi[\w-]*|asyncapi)\.yaml", p)}
    asyncapi = yaml.safe_load(specs["asyncapi.yaml"])
    own = async_channels(asyncapi)
    producers = Producers(rel, ns)
    rest_ops = {op_id for name, doc in specs.items() if name.startswith("openapi")
                for op_id in rest_ops_of(yaml.safe_load(doc))}
    overlay_path = pathlib.Path(args.overlay) if args.overlay else repo / "system" / OVERLAY_NAME
    if not overlay_path.exists():
        raise BuildError(f"no business overlay {overlay_path}: author it first (see the "
                         "system-assets-release skill and STANDARD.md Part B)")
    overlay_text = overlay_path.read_text()
    interactions = yaml.safe_load(overlay_text)
    violations = resolve_interactions(
        app, interactions, own, producers.asyncapi, rest_ops,
        {t["name"] for t in primitives.get("tools", [])}, {s["id"] for s in agent_card.get("skills", [])},
        rel.go_type_names("internal/application/usecases"), rel.go_type_names("internal/application/ports"),
        ctx.get("topics", []), outbound=producers)
    for topic in ctx.get("topics", []):
        if topic not in own:
            violations.append(f"ecosystem topic {topic} is not a channel of apis/asyncapi.yaml at {rel.tag}")
    if violations:
        raise BuildError("interactions do not resolve against the release:\n  - " + "\n  - ".join(violations))
    print(f"[6/6] interactions resolve: {len(interactions.get('interactions', []))} interactions, "
          f"{len(interactions.get('subscriptions', []))} subscriptions")

    application = build_application(app, version, ctx, sub, tags, chart)
    assets = {
        "apiVersion": "warehouse/system-assets/v1",
        "application": "application.yaml",
        "release": {"version": version, "tag": rel.tag, "commit": rel.commit,
                    "chart": f"{OCI}/{app}:{chart['version']}", "images": sorted(set(images)),
                    "drift": drift},
        "assets": [
            *({"id": f"{app}/openapi" if name == "openapi.yaml" else f"{app}/{name.removesuffix('.yaml')}",
               "kind": "openapi", "path": f"openapi/{name}",
               "spec": {"name": "openapi", "version": str(yaml.safe_load(doc)["openapi"])},
               "version": str(yaml.safe_load(doc)["info"]["version"]), "exposure": "kong"}
              for name, doc in specs.items() if name.startswith("openapi")),
            {"id": f"{app}/asyncapi", "kind": "asyncapi", "path": "asyncapi/asyncapi.yaml",
             "spec": {"name": "asyncapi", "version": str(asyncapi["asyncapi"])},
             "version": str(asyncapi["info"]["version"]), "exposure": "kafka"},
            *([{"id": f"{app}/mcp", "kind": "mcp",
                "documents": {"serverCard": "mcp/server-card.json", "primitives": "mcp/primitives.json"},
                "spec": {"name": "mcp", "version": init["protocolVersion"], "serverCard": "sep-2127"},
                "version": version, "exposure": "agent-router"}] if "mcp" in surfaces else []),
            *([{"id": f"{app}/a2a", "kind": "a2a", "path": "a2a/agent-card.json",
                "spec": {"name": "a2a", "version": "1.0.0"}, "version": version,
                "exposure": "agent-router"}] if "a2a" in surfaces else []),
        ],
    }
    header = f"# {app} {version} ({rel.tag}, {rel.commit[:7]}) - System Assets & Interaction Standard v0.4\n"
    files = {
        "application.yaml": dump_yaml(application, header),
        "system-assets.yaml": dump_yaml(assets, header),
        "interactions.yaml": header + re.sub(r"(?m)^application: .*$", "application: application.yaml",
                                              overlay_text),
        **({"mcp/server-card.json": dump_json(server_card), "mcp/primitives.json": dump_json(primitives)}
           if "mcp" in surfaces else {}),
        **({"a2a/agent-card.json": dump_json(agent_card)} if "a2a" in surfaces else {}),
        # one folder per asset kind, like mcp/ and a2a/
        **{f"{name.split('.')[0].split('-')[0]}/{name}": doc for name, doc in specs.items()},
    }
    files["SHA256SUMS"] = "".join(f"{hashlib.sha256(files[k].encode()).hexdigest()}  {k}\n" for k in sorted(files))
    dest = repo / "system" / version
    write_folder(dest, files)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True, help="application id == repo name, e.g. warehouse-planning")
    ap.add_argument("--version", help="release version without v (default: latest GitHub release)")
    ap.add_argument("--repo", help="clone or worktree of the app (default $WAREHOUSE_SYSTEMS_HOME/<app>); "
                                   "git objects of the tag are read, system/<version>/ is written here")
    ap.add_argument("--overlay", help=f"business overlay (default <repo>/system/{OVERLAY_NAME})")
    ap.add_argument("--namespace", default="warehouse-systems")
    ap.add_argument("--strict", action="store_true", help="fail on runtime drift instead of recording it")
    args = ap.parse_args()
    try:
        dest = build(args)
    except BuildError as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        return 1
    digest = hashlib.sha256((dest / "SHA256SUMS").read_bytes()).hexdigest()
    print(f"wrote {dest}/ ({sum(1 for f in dest.rglob('*') if f.is_file())} files, SHA256SUMS sha256 {digest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
