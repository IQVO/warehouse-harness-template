#!/usr/bin/env python3
"""Tests for the opt-in GS1 package distribution in tools/migrate_v3.py (stdlib only).

Run: python3 tools/test_migrate_gs1.py
"""
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
GS1_DIR = "internal/domain/shared/gs1"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


v3 = load("migrate_v3", os.path.join(HERE, "migrate_v3.py"))
health = load("harness_health", os.path.join(ROOT, "scripts", "harness-health.py"))


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


class Gs1PackageDistribution(unittest.TestCase):
    def migrator(self, repo, **kw):
        m = v3.Migrator(repo, ROOT, kw.pop("dry", False), **kw)
        m.say = lambda msg: None
        return m

    def test_list_covers_every_go_file_of_the_package(self):
        on_disk = sorted(f"{GS1_DIR}/{n}" for n in os.listdir(os.path.join(ROOT, GS1_DIR)) if n.endswith(".go"))
        self.assertEqual(sorted(v3.GS1_PACKAGE), on_disk,
                         "every .go file of the gs1 package must be in GS1_PACKAGE (or drift goes unnoticed)")

    def test_not_installed_unless_asked(self):
        with tempfile.TemporaryDirectory() as repo:
            self.migrator(repo).gs1_package()
            self.assertFalse(os.path.exists(os.path.join(repo, GS1_DIR)))

    def test_with_gs1_installs_byte_identical_copy(self):
        with tempfile.TemporaryDirectory() as repo:
            self.migrator(repo, with_gs1=True).gs1_package()
            for rel in v3.GS1_PACKAGE:
                self.assertEqual(read(os.path.join(repo, rel)), read(os.path.join(ROOT, rel)), rel)

    def test_existing_copy_is_refreshed_without_the_flag(self):
        with tempfile.TemporaryDirectory() as repo:
            self.migrator(repo, with_gs1=True).gs1_package()
            drifted = os.path.join(repo, GS1_DIR, "gtin.go")
            with open(drifted, "ab") as fh:
                fh.write(b"// local edit\n")
            self.migrator(repo).gs1_package()
            self.assertEqual(read(drifted), read(os.path.join(ROOT, GS1_DIR, "gtin.go")))

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as repo:
            self.migrator(repo, dry=True, with_gs1=True).gs1_package()
            self.assertFalse(os.path.exists(os.path.join(repo, GS1_DIR)))

    def test_platform_profile_never_gets_go_code(self):
        with tempfile.TemporaryDirectory() as repo:
            self.migrator(repo, profile="platform", with_gs1=True).gs1_package()
            self.assertFalse(os.path.exists(os.path.join(repo, GS1_DIR)))

    def test_health_drift_check_lists_the_package(self):
        self.assertTrue(set(v3.GS1_PACKAGE) <= set(health.managed_list()))
        self.assertTrue(set(v3.GS1_PACKAGE).isdisjoint(health.CORE_MANAGED),
                        "gs1 is opt-in: a repo without it must not be reported as drifted")


if __name__ == "__main__":
    unittest.main()
