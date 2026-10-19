#!/usr/bin/env python3
"""Tests for harness_probe.py's pure logic: no claude calls, no network. Mirrors test_harness_eval2.py's style."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location("hprobe", os.path.join(HERE, "harness_probe.py"))
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)


def write_jsonl(path, rows):
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def row(variant, ok=True, **gates):
    return dict(task="stock-event", guide=".claude/skills/x/SKILL.md", variant=variant, ok=ok, gates=gates)


class Compose(unittest.TestCase):
    def _run(self, rows, min_margin=0.34):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.jsonl")
            write_jsonl(p, rows)
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                hp.compose(p, min_margin)
            finally:
                sys.stdout = old
            return buf.getvalue()

    def test_candidate_clearly_better_recommends_adopt(self):
        rows = [row("baseline", **{"g": False}) for _ in range(3)] + [row("candidate", **{"g": True}) for _ in range(3)]
        out = self._run(rows)
        self.assertIn("ADOPT candidate", out)
        self.assertIn("RECOMMENDATION: adopt", out)

    def test_candidate_clearly_worse_recommends_keep(self):
        rows = [row("baseline", **{"g": True}) for _ in range(3)] + [row("candidate", **{"g": False}) for _ in range(3)]
        out = self._run(rows)
        self.assertIn("KEEP baseline", out)
        self.assertIn("RECOMMENDATION: keep", out)

    def test_narrow_margin_is_inconclusive_not_a_call(self):
        # 2/3 vs 1/3: a 33pp margin just under the default 34% threshold must not be reported as a winner.
        rows = [row("baseline", **{"g": True}), row("baseline", **{"g": True}), row("baseline", **{"g": False}),
                row("candidate", **{"g": True}), row("candidate", **{"g": False}), row("candidate", **{"g": False})]
        out = self._run(rows)
        self.assertNotIn("ADOPT candidate", out)
        self.assertNotIn("KEEP baseline", out)
        self.assertIn("inconclusive", out)

    def test_inconclusive_runs_excluded_from_rates(self):
        rows = [row("baseline", ok=None), row("baseline", **{"g": True}), row("baseline", **{"g": True}),
                row("candidate", **{"g": True}), row("candidate", **{"g": True})]
        out = self._run(rows)
        # both at 100% once the None (inconclusive) baseline run is excluded -> margin 0 -> inconclusive, not a crash
        self.assertIn("inconclusive", out)

    def test_mixed_result_across_gates_is_reported_as_mixed(self):
        rows = [row("baseline", a=True, b=False) for _ in range(3)] + \
               [row("candidate", a=False, b=True) for _ in range(3)]
        out = self._run(rows)
        self.assertIn("RECOMMENDATION: mixed result", out)

    def test_empty_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "empty.jsonl")
            open(p, "w").close()
            hp.compose(p, 0.34)  # must not raise


class MakeProbeVariant(unittest.TestCase):
    def test_missing_guide_path_raises_a_clear_error(self):
        # make_probe_variant should fail fast (not a cryptic shutil error) when --guide is typo'd.
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            hp.r1.sh(["git", "init", "-q"], repo, check=True)
            hp.r1.sh(["git", "commit", "--allow-empty", "-q", "-m", "init", "--no-verify"], repo, check=True,
                     env={**os.environ, "LEFTHOOK": "0", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            cand = os.path.join(tmp, "candidate.md")
            open(cand, "w").write("candidate text")
            with tempfile.TemporaryDirectory() as tmp2:
                with self.assertRaises(FileNotFoundError):
                    hp.make_probe_variant(repo, "no/such/guide.md", cand, tmp2)


if __name__ == "__main__":
    unittest.main()
