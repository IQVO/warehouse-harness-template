#!/usr/bin/env python3
"""Derive application.yaml `tags` from EventCatalog badges (STANDARD.md Part 0.3).

Reads IQVO/ecosystem origin/develop (run `git -C ~/warehouse-systems/ecosystem fetch` first)
and writes application-tags.json next to this script.
"""
import json
import pathlib
import re
import subprocess

ECO = pathlib.Path.home() / "warehouse-systems" / "ecosystem"
OUT = pathlib.Path(__file__).with_name("application-tags.json")
KEEP_S = {"asrs", "analytics", "status", "process", "address", "sales", "logistics"}
DROP = {"consumed", "analytics", "analytic", "system", "health", "published",
        "core", "supporting", "generic", "stateless"}
DROP_PREFIX = ("consumed-", "published-")


def git(*a):
    return subprocess.run(["git", "-C", str(ECO), *a], capture_output=True, text=True, check=True).stdout


def kebab(b):
    b = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", b.strip())
    return re.sub(r"[^a-z0-9]+", "-", b.lower()).strip("-")


def sing(t):
    parts = t.split("-")
    w = parts[-1]
    if w not in KEEP_S:
        if w.endswith("ies"):
            w = w[:-3] + "y"
        elif w.endswith(("sses", "ches", "shes", "xes")):
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            w = w[:-1]
    parts[-1] = w
    return "-".join(parts)


def key(t):
    return sing(t.replace("-", "")).replace("billsoflading", "billoflading")


def derive_all():
    """Return {app: {"domain": <EventCatalog domain>, "tags": [...]}} for every catalogued app."""
    files = git("ls-tree", "-r", "--name-only", "origin/develop", "domains").split()
    apps = {}
    for f in files:
        m = re.fullmatch(r"domains/([^/]+)/services/([^/]+)/index.mdx", f)
        if not m:
            continue
        domain, svc = m.groups()
        fm = git("show", f"origin/develop:{f}").split("---")[1]
        badges = [b.strip().strip("\"'") for b in re.findall(r"- content:\s*(.+)", fm)]
        app = svc[:-4] if svc.endswith("-api") else svc
        e = apps.setdefault(app, {"domain": domain, "events": [], "api": []})
        e["api" if svc.endswith("-api") else "events"].extend(badges)

    ids = set(apps)
    ctxn = ids | {sing(i) for i in ids}
    out = {}
    for app, e in sorted(apps.items()):
        best, order = {}, []
        for b in e["events"] + e["api"]:
            t = kebab(b)
            if not t or t in DROP or t.startswith(DROP_PREFIX) or t in ids:
                continue
            t = sing(t)
            if t in DROP or t in ctxn:
                continue
            k = key(t)
            if k not in best:
                best[k] = t
                order.append(k)
            elif "-" in t and "-" not in best[k]:
                best[k] = t
        out[app] = {"domain": e["domain"], "tags": [best[k] for k in order]}
    return out


def main():
    out = derive_all()
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{len(out)} applications, {sum(len(v['tags']) for v in out.values())} tags -> {OUT}")


if __name__ == "__main__":
    main()
