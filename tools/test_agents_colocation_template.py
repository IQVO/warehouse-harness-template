#!/usr/bin/env python3
"""Self-test of the agents.colocate reference (templates/packaging/charts/*.tmpl).

Instantiates agents-deployment.yaml.tmpl and test_agents_colocation.py.tmpl into a copy of the fixture
chart tools/testdata/agents-colocation/agents-fixture (the fleet chart shape with the companion edits of
templates/packaging/charts/README.md applied), then proves:
  1. no placeholder is left after substitution;
  2. the canonical test PASSES on the fixture;
  3. with the flag off, adding agents-deployment.yaml changes nothing (the template is inert);
  4. the render REFUSES when the mcp and a2a pod specs differ, and the canonical test fails;
  5. the canonical test bites: a composition that keeps both ports named "http" fails it, and so does a
     standalone Deployment without the PruneLast or the sync-wave -1 annotation, or an agent pod that mounts
     a chart ConfigMap still at wave 0, or an mcp/a2a container without the preStop sleep, or a grace
     period that does not exceed the sleep plus the HTTP drain.
Needs helm (HELM overrides the binary) and PyYAML. Run from the template root:
  python3 tools/test_agents_colocation_template.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "templates/packaging/charts"
FIXTURE = ROOT / "tools/testdata/agents-colocation/agents-fixture"
NAME = "agents-fixture"
HELM = os.environ.get("HELM", "helm")


def instantiate(dest: Path) -> Path:
    chart = dest / NAME
    shutil.copytree(FIXTURE, chart)
    (chart / "tests").mkdir(exist_ok=True)
    for src, dst in (("agents-deployment.yaml.tmpl", "templates/agents-deployment.yaml"),
                     ("test_agents_colocation.py.tmpl", "tests/test_agents_colocation.py")):
        (chart / dst).write_text((TPL / src).read_text().replace("{{SERVICE_REPO}}", NAME))
    return chart


def helm(chart: Path, *sets: str) -> subprocess.CompletedProcess:
    cmd = [HELM, "template", NAME, str(chart), "-f", str(chart / "ci/all-components-values.yaml")]
    for s in sets:
        cmd += ["--set", s]
    return subprocess.run(cmd, capture_output=True, text=True)


def canonical(chart: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(chart / "tests/test_agents_colocation.py")],
                          capture_output=True, text=True, env={**os.environ, "HELM": HELM})


class AgentsColocationTemplate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.chart = instantiate(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, rel: str, old: str, new: str) -> None:
        f = self.chart / rel
        text = f.read_text()
        self.assertIn(old, text, f"fixture anchor {old!r} not found in {rel}")
        f.write_text(text.replace(old, new, 1))

    def test_no_placeholder_left(self) -> None:
        for rel in ("templates/agents-deployment.yaml", "tests/test_agents_colocation.py"):
            self.assertNotIn("{{SERVICE_REPO}}", (self.chart / rel).read_text(), rel)

    def test_canonical_test_passes_on_the_fixture(self) -> None:
        r = canonical(self.chart)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)

    def test_flag_off_is_inert(self) -> None:
        with_tpl = helm(self.chart, "mcp.enabled=true", "a2a.enabled=true")
        (self.chart / "templates/agents-deployment.yaml").unlink()
        without = helm(self.chart, "mcp.enabled=true", "a2a.enabled=true")
        self.assertEqual(with_tpl.returncode, 0, with_tpl.stderr)
        self.assertEqual(with_tpl.stdout, without.stdout)

    def test_refuses_when_pod_specs_differ(self) -> None:
        self.edit("templates/a2a-deployment.yaml", "    spec:\n", "    spec:\n      enableServiceLinks: false\n")
        r = helm(self.chart, "agents.colocate=true")
        self.assertNotEqual(r.returncode, 0, "render must fail when the mcp and a2a pod specs differ")
        self.assertIn("pod specs differ", r.stderr)
        self.assertEqual(helm(self.chart).returncode, 0, "flag off must still render")
        self.assertNotEqual(canonical(self.chart).returncode, 0)

    def test_bites_when_both_ports_stay_http(self) -> None:
        self.edit("templates/agents-deployment.yaml",
                  '{{ if eq $p.name "http" }}{{ $_ := set $p "name" $comp }}{{ end }}', "")
        r = canonical(self.chart)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("targetPort", r.stdout)

    def test_bites_without_prune_last(self) -> None:
        self.edit("templates/mcp-deployment.yaml", "    argocd.argoproj.io/sync-options: PruneLast=true\n", "")
        r = canonical(self.chart)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("PruneLast", r.stdout)

    def test_bites_without_standalone_sync_wave(self) -> None:
        self.edit("templates/a2a-deployment.yaml", '    argocd.argoproj.io/sync-wave: "-1"\n', "")
        r = canonical(self.chart)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("sync-wave", r.stdout)

    def test_bites_without_prestop_sleep(self) -> None:
        self.edit("templates/a2a-deployment.yaml", "          lifecycle:\n            preStop:\n              sleep:\n"
                  "                seconds: 10\n", "")
        r = canonical(self.chart)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("preStop", r.stdout)

    def test_bites_when_grace_does_not_cover_sleep_plus_drain(self) -> None:
        self.edit("values.yaml", "terminationGracePeriodSeconds: 30\n", "terminationGracePeriodSeconds: 20\n")
        r = canonical(self.chart)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("terminationGracePeriodSeconds 20", r.stdout)

    def test_bites_when_a_consumed_configmap_syncs_after_the_pods(self) -> None:
        mount = ("      volumes:\n        - name: tmp\n          emptyDir: {}\n"
                 "        - name: config\n          configMap:\n"
                 "            name: {{ include \"agents-fixture.fullname\" . }}\n")
        self.edit("templates/mcp-deployment.yaml", "      volumes:\n        - name: tmp\n          emptyDir: {}\n", mount)
        r = canonical(self.chart)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("ConfigMap", r.stdout)
        self.edit("templates/configmap.yaml", "  labels:\n",
                  "  annotations:\n    argocd.argoproj.io/sync-wave: \"-2\"\n  labels:\n")
        r = canonical(self.chart)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    if shutil.which(HELM) is None:
        sys.exit(f"FAIL: {HELM} not found (this self-test never skips)")
    unittest.main()
