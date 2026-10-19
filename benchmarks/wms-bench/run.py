#!/usr/bin/env python3
"""run.py: thin wrapper that resolves a WMS-Bench tasks/*.yaml into a tools/harness_eval*.py invocation
and names the output after the target repo's harness-template version (see HARNESS.md: benchmarks/wms-bench).

Usage: python3 benchmarks/wms-bench/run.py --task tasks/stock-event.yaml [--variants full,bare] [--runs 3]
       (run from the warehouse-harness-template root; --fleet-root defaults to the sibling checkouts
       layout: <fleet-root>/<repo-name>)
No YAML dependency: the manifest format is flat enough for a 5-line hand parser (stdlib only, matching the
rest of this template's tooling).
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def parse_manifest(path):
    text = open(path, encoding="utf8").read()
    m = {}
    for line in text.splitlines():
        if not line or line.startswith(" ") or line.strip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        if v.strip() and v.strip() != ">":
            m[k.strip()] = v.strip().strip('"')
    return m


def repo_version(repo_path):
    for guide in ("CLAUDE.md", "AGENTS.md"):
        p = os.path.join(repo_path, guide)
        if os.path.islink(p) or os.path.isfile(p):
            text = open(os.path.realpath(p), encoding="utf8").read()
            m = re.search(r"harness-template:?\s*(v\d+)", text, re.I)
            if m:
                return m.group(1).lower()
    return "unknown-version"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="path to a tasks/*.yaml manifest")
    ap.add_argument("--variants", default="full,bare")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--budget", type=float, default=6.0)
    ap.add_argument("--fleet-root", default=os.path.abspath(os.path.join(TEMPLATE_ROOT, "..")))
    ap.add_argument("--dry-run", action="store_true", help="print the resolved command, do not run it")
    a = ap.parse_args()

    manifest = parse_manifest(a.task)
    for required in ("id", "repo"):
        if required not in manifest:
            sys.exit(f"{a.task}: missing required field {required!r}")

    repo_path = os.path.join(a.fleet_root, manifest["repo"])
    if not os.path.isdir(repo_path):
        sys.exit(f"resolved repo path does not exist: {repo_path} (wrong --fleet-root?)")

    version = repo_version(repo_path)
    results_dir = os.path.join(HERE, "results")
    os.makedirs(results_dir, exist_ok=True)
    out = os.path.join(results_dir, f"{manifest['id']}-{version}.jsonl")

    cmd = [sys.executable, os.path.join(TEMPLATE_ROOT, "tools", "harness_eval2.py"), "--repo", repo_path,
           "--tasks", manifest["id"], "--variants", a.variants, "--runs", str(a.runs),
           "--budget", str(a.budget), "--out", out]
    if manifest.get("kind") == "temptation":
        cmd[1] = os.path.join(TEMPLATE_ROOT, "tools", "harness_eval.py")  # temptation tasks live in round-1's runner

    print("WMS-Bench:", manifest["id"], "->", manifest["repo"], f"(harness-template {version})")
    print(" ".join(cmd))
    if a.dry_run:
        return 0
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
