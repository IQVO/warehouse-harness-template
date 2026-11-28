#!/usr/bin/env python3
"""Tests for tools/system-assets (the per-release system/<version>/ builder) and the system-assets-release
skill that ships to every service. Run: python3 tools/test_system_assets.py   (needs PyYAML)

Pins:
  * the resolver BITES: a dangling trigger, an unknown use case, a forbidden technology, an unmapped
    published or consumed event type each fail the build (a green-only validator proves nothing);
  * AsyncAPI 2.6 (either verb) and 3.x are indexed; retail-network CloudEvents types are seen;
  * write_folder replaces the version folder whole (a stale file never survives a rebuild);
  * the CLAUDE.md rule block in CLAUDE.md.template is the one migrate_v4 upserts;
  * the skill loads (name == dir, description with a trigger, within the OpenCode cap) and is template-managed.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TPL = Path(__file__).resolve().parent.parent
SA = TPL / "tools" / "system-assets"
sys.path.insert(0, str(SA))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


B = load("build_system_bundle", SA / "build_system_bundle.py")
V4 = load("migrate_v4_for_sa", TPL / "tools" / "migrate_v4.py")

ETYPE = "com.warehouse.wes.demo.thing.ThingCreated"
CTYPE = "com.warehouse.wms.sibling.item.ItemMoved"
OWN = {
    "warehouse.demo.events": {"ops": {"receiveDemoEvents"}, "types": {ETYPE}},
    "warehouse.sibling.events": {"ops": {"consumeItemMoved"}, "types": {CTYPE}},
}
PINS = {"sibling": {"warehouse.sibling.events": {"ops": {"publishItems"}, "types": {CTYPE}}}}


def overlay() -> dict:
    return {
        "interactions": [{
            "id": "create-thing-publish-thing-created", "technology": "event.publish",
            "operation": {"asset": "demo/asyncapi", "id": "receiveDemoEvents", "effect": "write",
                          "event": {"channel": "warehouse.demo.events", "type": ETYPE}},
            "business": {"capability": "demo.things", "process": "thing-creation", "useCase": "CreateThing",
                         "purpose": "Announce a thing.",
                         "triggers": [{"kind": "api", "via": "rest", "ref": "createThing", "actor": "human:clerk"},
                                      {"kind": "event", "ref": CTYPE, "actor": "system"}]}}],
        "subscriptions": [{
            "id": "consume-sibling-item-moved",
            "operation": {"asset": "sibling/asyncapi", "id": "publishItems", "consumerOperation": "consumeItemMoved",
                          "event": {"channel": "warehouse.sibling.events", "types": [CTYPE]}},
            "business": {"capability": "demo.things", "process": "thing-creation", "useCases": ["CreateThing"],
                         "purpose": "Track item moves."}}],
    }


def resolve(ix: dict, **over) -> list[str]:
    args = dict(app="demo", ix=ix, own=OWN, producers=PINS, rest_ops={"createThing"}, tools=set(), skills=set(),
                use_cases={"CreateThing"}, ports=set(), publishes=["warehouse.demo.events"], outbound=None)
    args.update(over)
    return B.resolve_interactions(**args)


class Resolver(unittest.TestCase):
    def test_a_correct_overlay_resolves(self):
        self.assertEqual(resolve(overlay()), [])

    def test_dangling_trigger_unknown_use_case_and_forbidden_technology_fail(self):
        ix = overlay()
        ix["interactions"][0]["business"]["triggers"][0]["ref"] = "noSuchOperation"
        ix["interactions"][0]["business"]["useCase"] = "NoSuchUseCase"
        ix["interactions"].append({**overlay()["interactions"][0], "id": "second", "technology": "rest"})
        out = "\n".join(resolve(ix))
        self.assertIn("does not resolve", out)
        self.assertIn("NoSuchUseCase", out)
        self.assertIn("rest must target <producer>/openapi", out)

    def test_a_scheduled_job_is_a_valid_named_trigger(self):
        ix = overlay()
        ix["interactions"][0]["business"]["triggers"].append({"kind": "schedule", "ref": "abandon-inactive-sweep", "actor": "system"})
        self.assertEqual(resolve(ix), [])
        ix["interactions"][0]["business"]["triggers"][-1]["ref"] = ""
        self.assertIn("does not resolve", "\n".join(resolve(ix)))

    def test_unmapped_published_and_consumed_events_fail_coverage(self):
        ix = overlay()
        ix["interactions"] = []
        ix["subscriptions"] = []
        out = "\n".join(resolve(ix))
        self.assertIn(f"coverage: {ETYPE}", out)
        self.assertIn(f"coverage: consumed {CTYPE}", out)

    def test_subscription_needs_a_pinned_producer_and_real_ops(self):
        ix = overlay()
        ix["subscriptions"][0]["operation"]["id"] = "ghost"
        self.assertIn("producer op 'ghost'", "\n".join(resolve(ix)))
        self.assertIn("no AsyncAPI contract for producer", "\n".join(resolve(overlay(), producers={})))


class FakeProducers:
    """Stands in for build_system_bundle.Producers: rest / mcp / a2a contracts of the sibling `shop`."""

    def rest(self, p):
        return {"getItem": ("GET", "/items/{id}"), "reserve": ("POST", "/reservations")} if p == "shop" else None

    def mcp(self, p):
        return {"get_item": True, "reserve_item": False} if p == "shop" else None

    def a2a(self, p):
        return {"availability"} if p == "shop" else None


def call(tech: str, asset: str, op_id: str, effect: str, **extra) -> dict:
    return {"id": f"c-{op_id}", "technology": tech,
            "operation": {"asset": asset, "id": op_id, "effect": effect, **extra},
            "business": {"capability": "demo.things", "process": "thing-creation", "useCase": "CreateThing",
                         "purpose": "Ask the shop.", "triggers": [{"kind": "api", "via": "rest", "ref": "createThing", "actor": "system"}]}}


class OutboundCalls(unittest.TestCase):
    def run_one(self, interaction: dict) -> str:
        ix = overlay()
        ix["interactions"].append(interaction)
        return "\n".join(resolve(ix, outbound=FakeProducers()))

    def test_correct_calls_resolve(self):
        self.assertEqual(self.run_one(call("rest", "shop/openapi", "getItem", "read", rest={"method": "GET", "path": "/items/{id}"})), "")
        self.assertEqual(self.run_one(call("rest", "shop/openapi", "reserve", "write", rest={"method": "POST", "path": "/reservations"})), "")
        self.assertEqual(self.run_one(call("mcp", "shop/mcp", "get_item", "read", mcp={"primitive": "tool"})), "")
        self.assertEqual(self.run_one(call("a2a", "shop/a2a", "availability", "read")), "")

    def test_wrong_operation_locator_or_effect_fails(self):
        self.assertIn("is not in the shop OpenAPI", self.run_one(call("rest", "shop/openapi", "ghost", "read", rest={"method": "GET", "path": "/x"})))
        self.assertIn("rest locator", self.run_one(call("rest", "shop/openapi", "getItem", "read", rest={"method": "GET", "path": "/wrong"})))
        self.assertIn("does not match HTTP POST", self.run_one(call("rest", "shop/openapi", "reserve", "read", rest={"method": "POST", "path": "/reservations"})))
        self.assertIn("readOnlyHint", self.run_one(call("mcp", "shop/mcp", "reserve_item", "read", mcp={"primitive": "tool"})))
        self.assertIn("is not served by shop-mcp", self.run_one(call("mcp", "shop/mcp", "ghost", "read")))
        self.assertIn("not in the shop agent card", self.run_one(call("a2a", "shop/a2a", "ghost", "read")))

    def test_unknown_producer_or_wrong_asset_kind_fails(self):
        self.assertIn("no OpenAPI contract for 'other'", self.run_one(call("rest", "other/openapi", "getItem", "read")))
        self.assertIn("must target <producer>/openapi", self.run_one(call("rest", "shop/asyncapi", "getItem", "read")))
        self.assertIn("no producer index", "\n".join(resolve({**overlay(), "interactions": overlay()["interactions"] + [call("rest", "shop/openapi", "getItem", "read")]})))

    def test_rest_ops_of_ignores_path_level_parameters(self):
        doc = {"paths": {"/a/{id}": {"parameters": [{"name": "id"}], "get": {"operationId": "getA"}, "post": {"summary": "no id"}}}}
        self.assertEqual(B.rest_ops_of(doc), {"getA": ("GET", "/a/{id}")})


class FakeMCP(BaseHTTPRequestHandler):
    """/mcp of a stateless (2026-07-28) or a stateful server, picked by the class attribute `stateless`."""
    stateless = True
    discover_versions = ["2026-07-28", "2025-11-25"]
    seen: list = []

    def log_message(self, *a):
        pass

    def reply(self, code, obj, sse=False):
        raw = ("event: message\ndata: " + json.dumps(obj) + "\n\n") if sse else json.dumps(obj)
        self.send_response(code)
        self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
        self.end_headers()
        self.wfile.write(raw.encode())

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method, meta = msg["method"], (msg.get("params") or {}).get("_meta") or {}
        type(self).seen.append((method, self.headers.get("Mcp-Method"), meta))
        if "id" not in msg:
            return self.reply(202, {})
        ok = lambda res: self.reply(200, {"jsonrpc": "2.0", "id": msg["id"], "result": res}, sse=self.stateless)
        err = lambda code, text: self.reply(200, {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": code, "message": text}})
        if self.stateless:
            if self.headers.get("Mcp-Method") != method or "io.modelcontextprotocol/protocolVersion" not in meta \
                    or "io.modelcontextprotocol/clientCapabilities" not in meta:
                return self.reply(400, {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32602, "message": "missing _meta"}})
            if method == "server/discover":
                return ok({"supportedVersions": self.discover_versions, "capabilities": {"tools": {}},
                           "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "demo-mcp", "version": "1.0.0"}}})
        elif method == "server/discover":
            if self.discover_versions == ["2025-11-25"]:  # answers discover but only speaks the stateful generation
                return ok({"supportedVersions": ["2025-11-25"], "capabilities": {"tools": {}},
                           "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "demo-mcp", "version": "1.0.0"}}})
            return err(-32601, "method not found")
        elif method == "initialize":
            return ok({"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                       "serverInfo": {"name": "demo-mcp", "version": "1.0.0"}})
        if method == "tools/list":
            return ok({"tools": [{"name": "get_thing", "annotations": {"readOnlyHint": True}}]})
        return err(-32601, "method not found")


class MCPGenerations(unittest.TestCase):
    def serve(self, stateless: bool, discover_versions=None) -> str:
        handler = type("H", (FakeMCP,), {"stateless": stateless, "seen": [],
                                         "discover_versions": discover_versions or ["2026-07-28", "2025-11-25"]})
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.handler = handler
        return f"http://127.0.0.1:{srv.server_port}"

    def test_stateless_server_is_read_with_discover_and_meta(self):
        init, prims = B.fetch_mcp(self.serve(True), "com.warehouse.wes/demo")
        self.assertEqual(init["protocolVersion"], "2026-07-28")
        self.assertEqual(init["serverInfo"], {"name": "demo-mcp", "version": "1.0.0"})
        self.assertEqual([t["name"] for t in prims["tools"]], ["get_thing"])
        self.assertNotIn("initialize", [m for m, _, _ in self.handler.seen], "stateless servers have no initialize")

    def test_server_that_answers_discover_with_old_versions_is_read_statefully(self):
        init, prims = B.fetch_mcp(self.serve(False, ["2025-11-25"]), "com.warehouse.wes/demo")
        self.assertEqual(init["protocolVersion"], "2025-11-25")
        self.assertIn("initialize", [m for m, _, _ in self.handler.seen])
        self.assertEqual([t["name"] for t in prims["tools"]], ["get_thing"])

    def test_stateful_server_falls_back_to_initialize(self):
        init, prims = B.fetch_mcp(self.serve(False), "com.warehouse.wes/demo")
        self.assertEqual(init["protocolVersion"], "2025-11-25")
        self.assertEqual([t["name"] for t in prims["tools"]], ["get_thing"])
        self.assertIn("initialize", [m for m, _, _ in self.handler.seen])


class AsyncIndex(unittest.TestCase):
    def test_2x_accepts_either_verb_and_reads_message_refs(self):
        doc = {"asyncapi": "2.6.0", "components": {"messages": {"M": {"x-type": "com.retailnetwork.commerce.o.order.OrderPlaced"}}},
               "channels": {"a": {"publish": {"operationId": "pubA", "message": {"$ref": "#/components/messages/M"}}},
                            "b": {"subscribe": {"operationId": "subB", "message": {"$ref": "#/components/messages/M"}}}}}
        got = B.async_channels(doc)
        self.assertEqual(got["a"]["ops"], {"pubA"})
        self.assertEqual(got["b"]["ops"], {"subB"})
        self.assertEqual(got["a"]["types"], {"com.retailnetwork.commerce.o.order.OrderPlaced"})

    def test_3x_operations_map_to_channel_addresses(self):
        doc = {"asyncapi": "3.0.0", "channels": {"c1": {"address": "warehouse.x.events", "messages": {"m": {"name": ETYPE}}}},
               "operations": {"sendX": {"action": "send", "channel": {"$ref": "#/channels/c1"}}}}
        got = B.async_channels(doc)
        self.assertEqual(got["warehouse.x.events"]["ops"], {"sendX"})
        self.assertEqual(got["warehouse.x.events"]["types"], {ETYPE})


class Folder(unittest.TestCase):
    def test_rebuild_replaces_the_folder_whole(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "system" / "1.2.3"
            B.write_folder(dest, {"a.yaml": "1", "mcp/x.json": "2", "old.yaml": "3"})
            B.write_folder(dest, {"a.yaml": "1b", "mcp/x.json": "2"})
            self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()),
                             ["a.yaml", "mcp/x.json"])
            self.assertEqual((dest / "a.yaml").read_text(), "1b")
            self.assertFalse((dest.parent / ".1.2.3.tmp").exists())

    def test_server_card_names_follow_the_cloudevents_org(self):
        self.assertEqual(B.server_card_name("x", "wes", {}), "com.warehouse.wes/x")
        self.assertEqual(B.server_card_name("x", "retail-network", {"ceSegment": "commerce"}), "com.retailnetwork.commerce/x")
        self.assertEqual(B.server_card_name("x", "storefront", {}), "com.retailnetwork.storefront/x")


class SkillAndRule(unittest.TestCase):
    skill = TPL / ".claude" / "skills" / "system-assets-release" / "SKILL.md"

    def test_skill_loads_and_is_template_managed(self):
        text = self.skill.read_text()
        m = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: (.+)\n---\n", text)
        self.assertTrue(m, "frontmatter must be name then description")
        self.assertEqual(m.group(1), "system-assets-release")
        self.assertIn("Use when", m.group(2))
        self.assertLessEqual(len(m.group(2)), 1024, "OpenCode description cap")
        self.assertIn(".claude/skills/system-assets-release/SKILL.md", V4.V4_MANAGED)

    def test_skill_states_the_rule(self):
        text = self.skill.read_text()
        self.assertIn("Every contract change produces a new asset", text)
        self.assertIn("system/<version>/", text)

    def test_skill_and_block_pass_guide_lint_in_a_service_shaped_repo(self):
        """The shipped guide text must pass guide-lint in the barest repo (no scripts/harness-health.py, no tools/, no
        fleet rules: warehouse-console has none): it may cite no path that exists only somewhere else."""
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".claude" / "skills" / "system-assets-release").mkdir(parents=True)
            (root / ".claude" / "skills" / "system-assets-release" / "SKILL.md").write_text(self.skill.read_text())
            (root / "CLAUDE.md").write_text("# svc\n\n" + V4.SYSTEM_ASSETS_BLOCK)
            r = subprocess.run([sys.executable, str(TPL / "scripts" / "harness" / "guide_lint.py"), "--root", str(root)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_claude_template_carries_the_exact_block_migrate_upserts(self):
        for name in ("CLAUDE.md.template", "CLAUDE.md"):
            self.assertIn(V4.SYSTEM_ASSETS_BLOCK, (TPL / name).read_text(), f"{name} lacks the system-assets block")


if __name__ == "__main__":
    unittest.main()
