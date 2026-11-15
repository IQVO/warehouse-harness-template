#!/usr/bin/env python3
"""Render and insert the spec-breaking CI job (templates/ci/spec-breaking.job.yml) into a repo's ci.yml.

The substitution contract (docs/spec-breaking-kit.md):
  {{ACTIONS_CHECKOUT}}      <- the `uses:` value (ref AND trailing `# vN` comment) of the
  {{ACTIONS_SETUP_PYTHON}}     commonest SHA-pinned `actions/checkout@<40 hex>` /
                               `actions/setup-python@<40 hex>` step ALREADY in the target ci.yml.
                               A target with no SHA-pinned setup-python is an error (exit 3):
                               never invent a pin; pass --fallback-from FILE to borrow it
                               from another workflow file you have reviewed.
Insertion point: immediately above `  trivy-scan:` (above its contiguous comment block, if any).
Idempotent: a ci.yml that already has `  spec-breaking:` is left byte-identical (exit 0).

Usage: spec_breaking_render.py REPO_DIR [--workflow REL_PATH] [--block FILE] [--fallback-from FILE] [--dry-run]
Exit: 0 inserted or already present, 3 contract violation (no pin / no insertion point).
"""
import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BLOCK = os.path.join(HERE, "..", "..", "templates", "ci", "spec-breaking.job.yml")
TOKENS = {"{{ACTIONS_CHECKOUT}}": "actions/checkout", "{{ACTIONS_SETUP_PYTHON}}": "actions/setup-python"}
JOB = re.compile(r"^  spec-breaking:\s*$", re.M)


class ContractError(Exception):
    pass


def pinned_uses(text, action):
    """The commonest `action@<40 hex> # comment` value in `text`, or None."""
    pat = re.compile(r"^\s*(?:-\s*)?uses:\s*(" + re.escape(action) + r"@[0-9a-f]{40}(?:[ \t]+#[^\n]*)?)[ \t]*$", re.M)
    found = collections.Counter(m.group(1).rstrip() for m in pat.finditer(text))
    return found.most_common(1)[0][0] if found else None


def render(block, ci_text, fallback_text=None):
    out = block
    for token, action in TOKENS.items():
        uses = pinned_uses(ci_text, action) or (pinned_uses(fallback_text, action) if fallback_text else None)
        if not uses:
            raise ContractError(f"no SHA-pinned `{action}@<40 hex>` step in the target ci.yml for {token}")
        out = out.replace(token, uses)
    if "{{ACTIONS_" in out:
        raise ContractError("unsubstituted {{ACTIONS_*}} token left in the block")
    return out


def insert(ci_text, rendered):
    """ci_text with `rendered` above `  trivy-scan:` (and its comment block); idempotent."""
    if JOB.search(ci_text):
        return ci_text
    lines = ci_text.split("\n")
    idx = [i for i, ln in enumerate(lines) if re.match(r"^  trivy-scan:\s*$", ln)]
    if len(idx) != 1:
        raise ContractError(f"expected exactly one `  trivy-scan:` job, found {len(idx)}")
    at = idx[0]
    while at > 0 and lines[at - 1].startswith("  #"):
        at -= 1
    return "\n".join(lines[:at] + rendered.rstrip("\n").split("\n") + [""] + lines[at:])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--block", default=BLOCK)
    ap.add_argument("--workflow", default=os.path.join(".github", "workflows", "ci.yml"),
                    help="workflow file relative to REPO_DIR (the template itself uses ci.yml.template)")
    ap.add_argument("--fallback-from")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ci = os.path.join(a.repo, a.workflow)
    if not os.path.isfile(ci):
        print(f"spec-breaking render: {ci} not found", file=sys.stderr)
        return 3
    with open(ci, encoding="utf-8") as fh:
        text = fh.read()
    with open(a.block, encoding="utf-8") as fh:
        block = fh.read()
    fallback = open(a.fallback_from, encoding="utf-8").read() if a.fallback_from else None
    try:
        new = insert(text, render(block, text, fallback))
    except ContractError as exc:
        print(f"spec-breaking render: {exc}", file=sys.stderr)
        return 3
    if new == text:
        print(f"{ci}: spec-breaking already present, unchanged")
    elif a.dry_run:
        print(f"{ci}: would insert the spec-breaking job ({len(new.splitlines()) - len(text.splitlines())} lines)")
    else:
        with open(ci, "w", encoding="utf-8") as fh:
            fh.write(new)
        print(f"{ci}: spec-breaking job inserted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
