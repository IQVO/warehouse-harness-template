#!/usr/bin/env python3
"""Tests for spec_breaking_render.py (the substitution contract) and the spec-breaking job block.

Stdlib only; PyYAML optional (the YAML-validity cases and the cases that run the job's oasdiff step
skip without it). actionlint, when installed, also lints the rendered template. The oasdiff step is
run for real twice: against a stub `oasdiff` (always, proves the step's own shell), and against a
real oasdiff v1.38.0 when one is on PATH (proves the severity override on real specs; the selftest
downloads nothing, so that half runs where the binary already is).
Run: python3 scripts/harness/test_spec_breaking_render.py
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import stat
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

# An older revision of the kit block, as an adopting repo carries it (different length on purpose).
OLD_BLOCK = """  # ============================================================
  # CLASSIFICATION: CONTRACT TESTING (producer breaking-change gate)
  # ============================================================
  # An older revision of the kit block.
  spec-breaking:
    runs-on: ubuntu-latest
    steps:
      - uses: {{ACTIONS_CHECKOUT}}
  # a comment at job depth, inside the job
      - uses: {{ACTIONS_SETUP_PYTHON}}
      - run: |
          oasdiff breaking --fail-on ERR base.yaml head.yaml

          echo done
"""

LEVELS = "response-property-enum-value-added\twarn\n"
ENUM_ADDED_REPORT = ("1 changes: 0 error, 1 warning, 0 info\n"
                     "warning\t[response-property-enum-value-added] at apis/openapi.yaml\n"
                     "\tin API GET /things\n")
SPEC = """openapi: 3.0.3
info: {title: t, version: "1.0.0"}
paths:
  /things:
    get:
      operationId: listThings
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                type: object
                properties:
                  status: {type: string, enum: [OPEN, CLOSED]}
    post:
      operationId: createThing
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                kind: {type: string, enum: [A, B]}
      responses:
        "201": {description: created}
  /other:
    get:
      operationId: getOther
      responses:
        "200": {description: ok}
"""
ENUM_ADDED = SPEC.replace("enum: [OPEN, CLOSED]", "enum: [OPEN, CLOSED, HELD]")
PATH_REMOVED = SPEC[:SPEC.index("  /other:")]


def block():
    with open(sbr.BLOCK, encoding="utf-8") as fh:
        return fh.read()


def old_ci():
    """CI as an adopting repo has it: the older block, inserted by the kit."""
    return sbr.insert(CI, sbr.render(OLD_BLOCK, CI))


def oasdiff_step_script():
    """The `run:` script of the job's oasdiff step, exactly as a rendered workflow carries it."""
    job = yaml.safe_load(sbr.insert(CI, sbr.render(block(), CI)))["jobs"]["spec-breaking"]
    (step,) = [s for s in job["steps"] if str(s.get("name", "")).startswith("oasdiff breaking")]
    return step["run"]


def run_oasdiff_step(base, head, oasdiff_dir=None, env=None):
    """Run the step in a throwaway git repo: base spec in the index's tree, head in the work tree.

    Returns (CompletedProcess, the severity-levels file the step wrote or None).
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo, runner_temp = os.path.join(tmp, "repo"), os.path.join(tmp, "runner-temp")
        os.makedirs(os.path.join(repo, "apis"))
        os.makedirs(runner_temp)
        spec_path = os.path.join(repo, "apis", "openapi.yaml")

        def git(*args):
            return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout.strip()

        git("init", "-q")
        with open(spec_path, "w", encoding="utf-8") as fh:
            fh.write(base)
        git("add", "apis/openapi.yaml")
        base_tree = git("write-tree")          # a tree-ish is all `git show BASE:path` needs
        with open(spec_path, "w", encoding="utf-8") as fh:
            fh.write(head)
        with open(os.path.join(runner_temp, "openapi-changed.txt"), "w", encoding="utf-8") as fh:
            fh.write("apis/openapi.yaml\n")
        full_env = dict(os.environ, RUNNER_TEMP=runner_temp, BASE_SHA=base_tree, BASE_REF="develop",
                        CONSUMERS="consumer-a", **(env or {}))
        if oasdiff_dir:
            full_env["PATH"] = oasdiff_dir + os.pathsep + full_env["PATH"]
        proc = subprocess.run(["bash", "-e", "-c", oasdiff_step_script()], cwd=repo, env=full_env,
                              capture_output=True, text=True)
        levels_path = os.path.join(runner_temp, "oasdiff-severity-levels.txt")
        if not os.path.isfile(levels_path):
            return proc, None
        with open(levels_path, encoding="utf-8") as fh:
            return proc, fh.read()


def stub_oasdiff(tmp, report, exit_code):
    """A fake `oasdiff` in `tmp`: records its argv, prints `report`, exits `exit_code`."""
    with open(os.path.join(tmp, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(report)
    path = os.path.join(tmp, "oasdiff")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$STUB_DIR/argv.txt"\ncat "$STUB_DIR/report.txt"\n'
                 f"exit {exit_code}\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return {"STUB_DIR": tmp}


def real_oasdiff():
    exe = shutil.which("oasdiff")
    if not exe:
        return False
    return "1.38.0" in subprocess.run([exe, "--version"], capture_output=True, text=True).stdout


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


class Update(unittest.TestCase):
    """--update: an existing block is replaced by the current one, and nothing else moves."""

    def test_update_replaces_the_old_block_and_equals_a_fresh_insert(self):
        before = old_ci()
        self.assertIn("An older revision of the kit block.", before)
        after = sbr.update(before, sbr.render(block(), before))
        self.assertEqual(after, sbr.insert(CI, sbr.render(block(), CI)))
        self.assertNotIn("An older revision of the kit block.", after)
        self.assertNotIn("a comment at job depth, inside the job", after)
        self.assertEqual(after.count("\n  spec-breaking:"), 1)

    def test_the_rest_of_the_file_is_byte_identical(self):
        before = old_ci()
        after = sbr.update(before, sbr.render(block(), before))
        head = before[:before.index("  # ====")]
        tail = before[before.index("\n  # Shift-left container scan."):]
        self.assertTrue(after.startswith(head))
        self.assertTrue(after.endswith(tail))
        self.assertEqual(after[len(head):len(after) - len(tail)], sbr.render(block(), CI).rstrip("\n") + "\n")

    def test_the_blank_lines_and_comments_between_the_job_and_trivy_scan_are_kept(self):
        before = old_ci().replace("\n  # Shift-left container scan.", "\n\n  # a loose note\n\n  # Shift-left container scan.")
        after = sbr.update(before, sbr.render(block(), before))
        tail = before[before.index("\n\n\n  # a loose note"):]
        self.assertTrue(after.endswith(tail))

    def test_update_is_idempotent(self):
        once = sbr.update(old_ci(), sbr.render(block(), old_ci()))
        self.assertEqual(sbr.update(once, sbr.render(block(), once)), once)

    def test_update_inserts_when_the_job_is_absent(self):
        self.assertEqual(sbr.update(CI, sbr.render(block(), CI)), sbr.insert(CI, sbr.render(block(), CI)))

    def test_ambiguous_boundaries_are_a_contract_error(self):
        before = old_ci()
        job = before[before.index("  spec-breaking:"):before.index("\n  # Shift-left container scan.")]
        cases = {
            "no trivy-scan at all": before.replace("  trivy-scan:", "  scan:"),
            "trivy-scan above the job": CI + "\n" + sbr.render(OLD_BLOCK, CI),
            "another job between": before.replace("\n  # Shift-left container scan.",
                                                  "\n  extra:\n    runs-on: ubuntu-latest\n\n  # Shift-left container scan."),
            "the job twice": before.replace("\n  # Shift-left container scan.", "\n" + job + "\n  # Shift-left container scan."),
            "a top-level key between": before.replace("\n  # Shift-left container scan.", "\nenv:\n  # Shift-left container scan."),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(sbr.ContractError):
                sbr.update(text, sbr.render(block(), text))

    def _repo(self, tmp, text):
        os.makedirs(os.path.join(tmp, ".github", "workflows"))
        path = os.path.join(tmp, ".github", "workflows", "ci.yml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_cli_without_update_leaves_an_existing_block_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._repo(tmp, old_ci())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(sbr.main([tmp]), 0)
            self.assertEqual(self._read(path), old_ci())
            self.assertIn("spec-breaking already present, unchanged", out.getvalue())

    def test_cli_update_dry_run_then_update_then_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._repo(tmp, old_ci())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(sbr.main([tmp, "--update", "--dry-run"]), 0)
            self.assertEqual(self._read(path), old_ci())
            self.assertIn("-  # An older revision of the kit block.\n", out.getvalue())   # the diff is the dry run
            self.assertIn("would update the spec-breaking job block", out.getvalue())
            with contextlib.redirect_stdout(out):
                self.assertEqual(sbr.main([tmp, "--update"]), 0)
            updated = self._read(path)
            self.assertEqual(updated, sbr.insert(CI, sbr.render(block(), CI)))
            proc = subprocess.run(["python3", os.path.join(HERE, "spec_breaking_render.py"), tmp, "--update"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("already current, unchanged", proc.stdout)
            self.assertEqual(self._read(path), updated)

    def test_cli_update_on_ambiguous_boundaries_exits_3_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = old_ci().replace("  trivy-scan:", "  scan:")
            path = self._repo(tmp, text)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(sbr.main([tmp, "--update"]), 3)
            self.assertEqual(self._read(path), text)
            self.assertIn("cannot tell where the block ends", err.getvalue())


class Job(unittest.TestCase):
    """What the job must never regress (each line is a review finding waiting to happen)."""

    def test_safety_properties_of_the_block(self):
        b = block()
        self.assertIn("oasdiff breaking --fail-on ERR", b)
        # The one severity override is written by the step itself into RUNNER_TEMP: no repo file can add another.
        self.assertIn("printf 'response-property-enum-value-added\\twarn\\n' > \"$RUNNER_TEMP/oasdiff-severity-levels.txt\"", b)
        self.assertIn('oasdiff breaking --fail-on ERR --severity-levels "$RUNNER_TEMP/oasdiff-severity-levels.txt" ', b)
        self.assertEqual(b.count("oasdiff-severity-levels.txt"), 2)
        self.assertEqual(len(re.findall(r"\\t(?:warn|info|none)\b", b)), 1)
        for flag in ("--warn-ignore", "--err-ignore", "--config"):
            self.assertNotIn(flag, b)
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

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_oasdiff_step_passes_the_severity_file_it_wrote_and_warns_on_a_response_enum_value_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, levels = run_oasdiff_step(SPEC, ENUM_ADDED, tmp, stub_oasdiff(tmp, ENUM_ADDED_REPORT, 0))
            with open(os.path.join(tmp, "argv.txt"), encoding="utf-8") as fh:
                argv = fh.read().split("\n")[:-1]
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(levels, LEVELS)
        self.assertEqual(argv[:4], ["breaking", "--fail-on", "ERR", "--severity-levels"])
        self.assertTrue(argv[4].endswith("/oasdiff-severity-levels.txt"))
        self.assertTrue(argv[5].endswith("/base-openapi.yaml"))
        self.assertEqual(argv[6:], ["apis/openapi.yaml"])
        self.assertIn(ENUM_ADDED_REPORT, proc.stdout)        # the report itself stays in the log
        warnings = [ln for ln in proc.stdout.split("\n") if ln.startswith("::warning::")]
        self.assertEqual(len(warnings), 1, proc.stdout)
        self.assertIn("apis/openapi.yaml", warnings[0])
        self.assertIn("tolerate unknown enum values", warnings[0])
        self.assertNotIn("::error::", proc.stdout)

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_oasdiff_step_fails_on_any_non_zero_oasdiff_exit_and_stays_quiet_on_a_clean_diff(self):
        for report, code, want in (("1 changes: 1 error, 0 warning, 0 info\n", 1, 1),
                                   ("Error: failed to load custom severity levels\n", 106, 1),
                                   ("No changes detected\n", 0, 0)):
            with self.subTest(report), tempfile.TemporaryDirectory() as tmp:
                proc, _ = run_oasdiff_step(SPEC, SPEC, tmp, stub_oasdiff(tmp, report, code))
                self.assertEqual(proc.returncode, want, proc.stdout + proc.stderr)
                self.assertIn(report, proc.stdout)
                self.assertNotIn("::warning::", proc.stdout)
                self.assertEqual("::error::Breaking REST change in apis/openapi.yaml vs develop. Pinning consumers: consumer-a."
                                 in proc.stdout, bool(want))

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    @unittest.skipUnless(real_oasdiff(), "oasdiff v1.38.0 not on PATH")
    def test_real_oasdiff_a_response_enum_value_added_is_a_warning_and_everything_else_still_fails(self):
        cases = (
            ("response enum value added", ENUM_ADDED, 0, "1 changes: 0 error, 1 warning", True),
            ("path removed", PATH_REMOVED, 1, "1 changes: 1 error, 0 warning", False),
            ("path removed and response enum value added", PATH_REMOVED.replace("[OPEN, CLOSED]", "[OPEN, CLOSED, HELD]"),
             1, "2 changes: 1 error, 1 warning", True),
            ("request enum value removed", SPEC.replace("enum: [A, B]", "enum: [A]"), 1, "1 changes: 1 error, 0 warning", False),
            ("identical", SPEC, 0, "No changes detected", False),
        )
        for name, head, code, summary, advisory in cases:
            with self.subTest(name):
                proc, levels = run_oasdiff_step(SPEC, head)
                self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
                self.assertIn(summary, proc.stdout)
                self.assertEqual(levels, LEVELS)
                self.assertEqual(proc.stdout.count("::warning::"), int(advisory), proc.stdout)
                self.assertEqual("::error::Breaking REST change" in proc.stdout, bool(code))

    def test_the_templates_own_ci_carries_the_job_rendered_with_its_own_pins(self):
        path = os.path.join(ROOT, ".github", "workflows", "ci.yml.template")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        want = sbr.render(block(), text).rstrip("\n")
        self.assertIn(want, text, "ci.yml.template drifted from templates/ci/spec-breaking.job.yml: "
                                  "re-run spec_breaking_render.py . --workflow .github/workflows/ci.yml.template --update")


if __name__ == "__main__":
    unittest.main()
