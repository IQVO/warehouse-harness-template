#!/usr/bin/env python3
"""Tests for spec_breaking_render.py (the substitution contract) and the spec-breaking job block.

Stdlib only; PyYAML optional (the YAML-validity cases skip without it). actionlint, when installed,
also lints the rendered template. Run: python3 scripts/harness/test_spec_breaking_render.py
"""
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
spec = importlib.util.spec_from_file_location("sbr", os.path.join(HERE, "spec_breaking_render.py"))
sbr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbr)

try:
    import yaml
except ImportError:
    yaml = None

CO = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7"
SP = "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7"
CI = f"""name: CI
on: [pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: {CO}
      - uses: {SP}
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: {CO}

  # Shift-left container scan.
  # ============================================================
  # CLASSIFICATION: SECURITY SCANNER (container image)
  # ============================================================
  trivy-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: {CO}
"""


def block():
    with open(sbr.BLOCK, encoding="utf-8") as fh:
        return fh.read()


class Contract(unittest.TestCase):
    def test_block_has_exactly_the_two_documented_placeholders(self):
        self.assertEqual(sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", block()))),
                         ["{{ACTIONS_CHECKOUT}}", "{{ACTIONS_SETUP_PYTHON}}"])

    def test_the_target_s_own_sha_pinned_values_are_used_not_the_unpinned_or_the_kits(self):
        out = sbr.render(block(), CI)
        self.assertIn(f"- uses: {CO}", out)
        self.assertIn(f"- uses: {SP}", out)
        self.assertNotIn("{{", out.replace("${{", ""))
        self.assertNotIn("checkout@v7", out)

    def test_the_commonest_pin_wins_when_a_repo_has_two(self):
        other = "actions/checkout@" + "a" * 40 + " # v6"
        ci = CI + f"      - uses: {other}\n"
        self.assertEqual(sbr.pinned_uses(ci, "actions/checkout"), CO)

    def test_a_target_without_a_sha_pinned_setup_python_is_a_contract_error_never_an_invented_pin(self):
        ci = CI.replace(f"      - uses: {SP}\n", "      - uses: actions/setup-python@v7\n")
        with self.assertRaises(sbr.ContractError):
            sbr.render(block(), ci)
        self.assertIn(SP, sbr.render(block(), ci, fallback_text=CI))

    def test_insertion_goes_above_trivy_scan_and_above_its_comment_block(self):
        out = sbr.insert(CI, sbr.render(block(), CI))
        job, comment, trivy = (out.index(s) for s in ("\n  spec-breaking:", "# Shift-left container scan.", "\n  trivy-scan:"))
        self.assertLess(job, comment)
        self.assertLess(comment, trivy)
        self.assertEqual(out.count("\n  spec-breaking:"), 1)

    def test_insertion_is_idempotent(self):
        once = sbr.insert(CI, sbr.render(block(), CI))
        self.assertEqual(sbr.insert(once, sbr.render(block(), once)), once)

    def test_no_trivy_scan_job_is_a_contract_error(self):
        with self.assertRaises(sbr.ContractError):
            sbr.insert(CI.replace("trivy-scan:", "scan:"), "x")

    def test_cli_exit_codes_and_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".github", "workflows"))
            path = os.path.join(tmp, ".github", "workflows", "ci.yml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(CI)
            self.assertEqual(sbr.main([tmp, "--dry-run"]), 0)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), CI)
            self.assertEqual(sbr.main([tmp]), 0)
            with open(path, encoding="utf-8") as fh:
                self.assertIn("\n  spec-breaking:", fh.read())
            self.assertEqual(sbr.main([os.path.join(tmp, "missing")]), 3)


class Job(unittest.TestCase):
    """What the job must never regress (each line is a review finding waiting to happen)."""

    def test_safety_properties_of_the_block(self):
        b = block()
        self.assertIn("oasdiff breaking --fail-on ERR", b)
        self.assertIn("613037fd6a1d3362a1b742d6d86c338c0eb5ab3511f4942ce3948e78720ea2da", b)
        self.assertIn("sha256sum --check --strict", b)
        self.assertIn("if: github.event_name == 'pull_request'", b)
        self.assertIn("fetch-depth: 0", b)
        self.assertIn("continue-on-error: true", b)
        self.assertIn("GH_TOKEN: ${{ github.token }}", b)
        self.assertIn("--require-hashes -r .github/requirements/asyncapi-breaking.txt", b)

    def test_no_github_expression_is_interpolated_into_a_run_script(self):
        in_run, bad = False, []
        for line in block().split("\n"):
            m = re.match(r"^(\s*)run:\s*(.*)$", line)
            if m:
                in_run, indent = True, len(m.group(1))
                if "${{" in m.group(2):
                    bad.append(line)
                continue
            if in_run and line.strip() and (len(line) - len(line.lstrip())) <= indent:
                in_run = False
            if in_run and "${{" in line:
                bad.append(line)
        self.assertEqual(bad, [])

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_rendered_block_is_valid_yaml_with_the_expected_job_shape(self):
        ci = yaml.safe_load(sbr.insert(CI, sbr.render(block(), CI)))
        job = ci["jobs"]["spec-breaking"]
        self.assertEqual(job["if"], "github.event_name == 'pull_request'")
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertEqual(job["steps"][0]["with"], {"fetch-depth": 0})
        self.assertEqual([s.get("id") for s in job["steps"] if s.get("id")], ["apis", "consumers"])

    @unittest.skipUnless(shutil.which("actionlint"), "actionlint not installed")
    def test_actionlint_is_clean_on_a_rendered_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ci.yml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(sbr.insert(CI, sbr.render(block(), CI)))
            proc = subprocess.run(["actionlint", "-no-color", path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_the_templates_own_ci_carries_the_job_rendered_with_its_own_pins(self):
        path = os.path.join(ROOT, ".github", "workflows", "ci.yml.template")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        want = sbr.render(block(), text).rstrip("\n")
        self.assertIn(want, text, "ci.yml.template drifted from templates/ci/spec-breaking.job.yml: "
                                  "re-run spec_breaking_render.py (delete the job first)")


if __name__ == "__main__":
    unittest.main()
