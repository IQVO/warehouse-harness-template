#!/usr/bin/env python3
"""migrate_v4.py: bring ONE already-v3 Go bounded-context repo to harness-template v4.

Idempotent. Run AFTER migrate_v3.py (or on a repo that's already on v3):
    python3 migrate_v4.py --repo <path> [--template <path>] [--dry-run]

v4 closes the three gaps found comparing this harness against Google-Research's Harness²
(arXiv:2610.14224) -- see HARNESS.md's "Harness² comparison (v4)" section for the reasoning:

  1. subagents  .claude/agents/{code-review,architecture-review,domain-review}.md: the three review
                skills promoted to isolated-context subagent invocations (byte-identical, template-managed,
                like the skills' generic wording already is -- they only point at each repo's OWN
                .claude/rules and .claude/skills files, never duplicate domain content).
  2. memory     .claude/memory/MEMORY.md: created ONLY if missing. Durable, repo-specific heuristics that
                aren't incidents. NEVER overwritten once it exists -- unlike the MANAGED files, this one is
                the repo's own and grows independently after first instantiation.
  3. red_issue.py --draft (+ its test): already MANAGED by v3's copy mechanism, this just adds the new test
                file to that list and wires it into `make harness-test` / the `guide-lint` CI job, the same
                way v3 wired test_hook.py and test_repo_lint.py.
  4. version marker: every repo should say which harness-template version it's on in one place a fleet
                audit can grep for. v3 rollout left this inconsistent (some repos never got a plain-text
                marker, only the generated scoped-rules HTML comment). v4 normalizes it.

What it deliberately does NOT do (same boundary as v3): build the probe loop's candidate guide content,
decide what goes in MEMORY.md beyond the one seeded fleet-wide lesson, or touch tools/ (harness_probe.py,
harness_eval*.py stay template-only -- see HARNESS.md's "must NOT be copied" list, unchanged in v4).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("migrate_v3", os.path.join(HERE, "migrate_v3.py"))
v3 = importlib.util.module_from_spec(spec)
sys.path.insert(0, HERE)
spec.loader.exec_module(v3)

V4_MANAGED = [
    ".claude/agents/code-review.md",
    ".claude/agents/architecture-review.md",
    ".claude/agents/domain-review.md",
    "scripts/harness/red_issue.py",        # also in v3.MANAGED, but re-listed here so migrate_v4 alone
                                            # (without re-running migrate_v3) still picks up --draft.
    "scripts/harness/test_red_issue.py",
]
VERSION_RX = re.compile(
    r"harness-template:?\s*v[234]\b(?:\s+is in effect)?|Scaffolded from `warehouse-harness-template`"
    r"(?:; harness-template v[234] is in effect)?",
    re.I,
)


class MigratorV4(v3.Migrator):
    # 1 + 3b ------------------------------------------------------------------
    def managed_v4(self):
        rels = list(V4_MANAGED)
        for rel in rels:
            src = os.path.join(self.template, rel)
            if not os.path.isfile(src):
                self.say(f"WARN template lacks {rel}")
                continue
            if os.path.isfile(self.p(rel)) and open(src, "rb").read() == open(self.p(rel), "rb").read():
                continue
            self.say(f"managed (v4): {rel}")
            if not self.dry:
                os.makedirs(os.path.dirname(self.p(rel)), exist_ok=True)
                import shutil
                shutil.copyfile(src, self.p(rel))

    # 2 -------------------------------------------------------------------------
    def memory(self):
        dest = self.p(".claude", "memory", "MEMORY.md")
        if os.path.isfile(dest):
            return  # repo's own file once it exists; v4 never overwrites it
        src = os.path.join(self.template, ".claude", "memory", "MEMORY.md")
        if not os.path.isfile(src):
            self.say("WARN template lacks .claude/memory/MEMORY.md")
            return
        self.say(".claude/memory/MEMORY.md: created (seeded, first instantiation only)")
        if not self.dry:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(src, encoding="utf8") as fh:
                text = fh.read()
            self.write(".claude/memory/MEMORY.md", text)

    # 4 -------------------------------------------------------------------------
    def version_marker(self):
        claude = self.p("CLAUDE.md")
        if not os.path.exists(claude):
            return
        real = os.path.realpath(claude)
        with open(real, encoding="utf8") as fh:
            text = fh.read()
        if VERSION_RX.search(text):
            new = VERSION_RX.sub("harness-template: v4", text, count=1)
        else:
            lines = text.splitlines(keepends=True)
            insert_at = 1 if lines else 0
            new = "".join(lines[:insert_at]) + "\n<!-- harness-template: v4 -->\n" + "".join(lines[insert_at:])
        if new != text:
            self.say(f"version marker: harness-template: v4 ({os.path.basename(real)})")
            if not self.dry:
                with open(real, "w", encoding="utf8") as fh:
                    fh.write(new)

    # 3b: wire the new test into make + CI, mirroring v3's own idempotent job-patching ------------------
    def harness_test_target(self):
        if not os.path.isfile(self.p("Makefile")):
            return
        text = self.read("Makefile")
        if "harness-test:" not in text or "test_red_issue.py" in text:
            return
        new = text.replace(
            "python3 scripts/harness/test_hook.py\n",
            "python3 scripts/harness/test_hook.py\n\tpython3 scripts/harness/test_red_issue.py\n",
            1,
        )
        if new != text:
            self.say("Makefile: harness-test + test_red_issue.py")
            self.write("Makefile", new)

    def ci_v4(self):
        rel = ".github/workflows/ci.yml"
        if not os.path.isfile(self.p(rel)) and os.path.isfile(self.p(rel + ".template")):
            rel += ".template"
        if not os.path.isfile(self.p(rel)):
            return
        lines = self.read(rel).splitlines()
        b = self._job_bounds(lines, "guide-lint")
        if not b:
            return
        start, end = b
        block = "\n".join(lines[start:end])
        if "test_red_issue.py" in block:
            return
        for i in range(start, end):
            if "python3 scripts/harness/test_hook.py" in lines[i]:
                indent = re.match(r"\s*", lines[i]).group(0)
                lines.insert(i + 1, f"{indent}- run: python3 scripts/harness/test_red_issue.py")
                self.say("ci.yml: + test_red_issue.py step in `guide-lint`")
                self.write(rel, "\n".join(lines) + "\n")
                return

    def run(self):
        self.managed_v4()
        self.memory()
        self.version_marker()
        self.harness_test_target()
        self.ci_v4()
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--template", default=v3.DEFAULT_TEMPLATE)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    return MigratorV4(a.repo, a.template, a.dry_run).run()


if __name__ == "__main__":
    sys.exit(main())
