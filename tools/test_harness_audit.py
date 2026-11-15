#!/usr/bin/env python3
"""Tests for scripts/harness-audit.py's gremlins threshold sensor. Run: python3 tools/test_harness_audit.py

The incident it pins: `.gremlins.yaml` thresholds written as quoted strings (`efficacy: "95"`) are read as 0 by
gremlins v0.6.0, so the mutation gate of most of the fleet never failed. The audit must call such a file not plain.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "harness_audit", Path(__file__).resolve().parent.parent / "scripts" / "harness-audit.py")
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules["harness_audit"] = AUDIT  # @dataclass resolves its module through sys.modules
SPEC.loader.exec_module(AUDIT)


class GremlinsPlainTest(unittest.TestCase):
    def plain(self, body: str) -> bool:
        return AUDIT.gremlins_thresholds_plain(body)

    def test_plain_numbers_pass(self):
        self.assertTrue(self.plain("threshold:\n  efficacy: 99\n  mutant-coverage: 99\n"))
        self.assertTrue(self.plain("threshold:\n  efficacy: 90.0\n  mutant-coverage: 89.5\n"))

    def test_quoted_values_fail(self):
        self.assertFalse(self.plain('threshold:\n  efficacy: "99"\n  mutant-coverage: 99\n'))
        self.assertFalse(self.plain("threshold:\n  efficacy: 99\n  mutant-coverage: '99'\n"))

    def test_unfilled_placeholder_and_missing_keys_fail(self):
        self.assertFalse(self.plain('efficacy: "{{MEASURED_EFFICACY_MINUS_1}}"\nmutant-coverage: 5\n'))
        self.assertFalse(self.plain("threshold:\n  efficacy: 99\n"))
        self.assertFalse(self.plain(""))

    def test_commented_example_does_not_count(self):
        body = '# efficacy: "95"\nthreshold:\n  efficacy: 99\n  mutant-coverage: 99\n'
        self.assertTrue(self.plain(body))


if __name__ == "__main__":
    unittest.main()
