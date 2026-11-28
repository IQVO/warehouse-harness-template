#!/usr/bin/env python3
"""Tests for tools/system-assets (the per-release system/<version>/ builder) and the system-assets-release
skill that ships to every service. Run: python3 tools/test_system_assets.py   (needs PyYAML)

Pins:
  * the resolver BITES: a dangling trigger, an unknown use case, a forbidden technology, an unmapped
    published or consumed event type each fail the build (a green-only validator proves nothing);
  * AsyncAPI 2.6 (either verb) and 3.x are indexed; retail-network CloudEvents types are seen;
  * write_folder replaces the version folder whole (a stale file never survives a rebuild);
  * the CLAUDE.md rule block in CLAUDE.md.template is the one migrate_v4 upserts;
  * the skill loads (name == dir, description with a trigger, within the OpenCode cap) and is template-managed.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

TPL = Path(__file__).resolve().parent.parent
SA = TPL / "tools" / "system-assets"
sys.path.insert(0, str(SA))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


B = load("build_system_bundle", SA / "build_system_bundle.py")
V4 = load("migrate_v4_for_sa", TPL / "tools" / "migrate_v4.py")

ETYPE = "com.warehouse.wes.demo.thing.ThingCreated"
CTYPE = "com.warehouse.wms.sibling.item.ItemMoved"
OWN = {
    "warehouse.demo.events": {"ops": {"receiveDemoEvents"}, "types": {ETYPE}},
    "warehouse.sibling.events": {"ops": {"consumeItemMoved"}, "types": {CTYPE}},
}
PINS = {"sibling": {"warehouse.sibling.events": {"ops": {"publishItems"}, "types": {CTYPE}}}}


def overlay() -> dict:
    return {
        "interactions": [{
            "id": "create-thing-publish-thing-created", "technology": "event.publish",
            "operation": {"asset": "demo/asyncapi", "id": "receiveDemoEvents", "effect": "write",
                          "event": {"channel": "warehouse.demo.events", "type": ETYPE}},
            "business": {"capability": "demo.things", "process": "thing-creation", "useCase": "CreateThing",
                         "purpose": "Announce a thing.",
                         "triggers": [{"kind": "api", "via": "rest", "ref": "createThing", "actor": "human:clerk"},
                                      {"kind": "event", "ref": CTYPE, "actor": "system"}]}}],
        "subscriptions": [{
            "id": "consume-sibling-item-moved",
            "operation": {"asset": "sibling/asyncapi", "id": "publishItems", "consumerOperation": "consumeItemMoved",
                          "event": {"channel": "warehouse.sibling.events", "types": [CTYPE]}},
            "business": {"capability": "demo.things", "process": "thing-creation", "useCases": ["CreateThing"],
                         "purpose": "Track item moves."}}],
    }


def resolve(ix: dict, **over) -> list[str]:
    args = dict(app="demo", ix=ix, own=OWN, producers=PINS, rest_ops={"createThing"}, tools=set(), skills=set(),
                use_cases={"CreateThing"}, ports=set(), publishes=["warehouse.demo.events"])
    args.update(over)
    return B.resolve_interactions(**args)


class Resolver(unittest.TestCase):
    def test_a_correct_overlay_resolves(self):
        self.assertEqual(resolve(overlay()), [])

    def test_dangling_trigger_unknown_use_case_and_forbidden_technology_fail(self):
        ix = overlay()
        ix["interactions"][0]["business"]["triggers"][0]["ref"] = "noSuchOperation"
        ix["interactions"][0]["business"]["useCase"] = "NoSuchUseCase"
        ix["interactions"].append({**overlay()["interactions"][0], "id": "second", "technology": "rest"})
        out = "\n".join(resolve(ix))
        self.assertIn("does not resolve", out)
        self.assertIn("NoSuchUseCase", out)
        self.assertIn("target resolution", out)

    def test_unmapped_published_and_consumed_events_fail_coverage(self):
        ix = overlay()
        ix["interactions"] = []
        ix["subscriptions"] = []
        out = "\n".join(resolve(ix))
        self.assertIn(f"coverage: {ETYPE}", out)
        self.assertIn(f"coverage: consumed {CTYPE}", out)

    def test_subscription_needs_a_pinned_producer_and_real_ops(self):
        ix = overlay()
        ix["subscriptions"][0]["operation"]["id"] = "ghost"
        self.assertIn("producer op 'ghost'", "\n".join(resolve(ix)))
        self.assertIn("no pinned AsyncAPI contract", "\n".join(resolve(overlay(), producers={})))


class AsyncIndex(unittest.TestCase):
    def test_2x_accepts_either_verb_and_reads_message_refs(self):
        doc = {"asyncapi": "2.6.0", "components": {"messages": {"M": {"x-type": "com.retailnetwork.commerce.o.order.OrderPlaced"}}},
               "channels": {"a": {"publish": {"operationId": "pubA", "message": {"$ref": "#/components/messages/M"}}},
                            "b": {"subscribe": {"operationId": "subB", "message": {"$ref": "#/components/messages/M"}}}}}
        got = B.async_channels(doc)
        self.assertEqual(got["a"]["ops"], {"pubA"})
        self.assertEqual(got["b"]["ops"], {"subB"})
        self.assertEqual(got["a"]["types"], {"com.retailnetwork.commerce.o.order.OrderPlaced"})

    def test_3x_operations_map_to_channel_addresses(self):
        doc = {"asyncapi": "3.0.0", "channels": {"c1": {"address": "warehouse.x.events", "messages": {"m": {"name": ETYPE}}}},
               "operations": {"sendX": {"action": "send", "channel": {"$ref": "#/channels/c1"}}}}
        got = B.async_channels(doc)
        self.assertEqual(got["warehouse.x.events"]["ops"], {"sendX"})
        self.assertEqual(got["warehouse.x.events"]["types"], {ETYPE})


class Folder(unittest.TestCase):
    def test_rebuild_replaces_the_folder_whole(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "system" / "1.2.3"
            B.write_folder(dest, {"a.yaml": "1", "mcp/x.json": "2", "old.yaml": "3"})
            B.write_folder(dest, {"a.yaml": "1b", "mcp/x.json": "2"})
            self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()),
                             ["a.yaml", "mcp/x.json"])
            self.assertEqual((dest / "a.yaml").read_text(), "1b")
            self.assertFalse((dest.parent / ".1.2.3.tmp").exists())

    def test_server_card_names_follow_the_cloudevents_org(self):
        self.assertEqual(B.server_card_name("x", "wes", {}), "com.warehouse.wes/x")
        self.assertEqual(B.server_card_name("x", "retail-network", {"ceSegment": "commerce"}), "com.retailnetwork.commerce/x")
        self.assertEqual(B.server_card_name("x", "storefront", {}), "com.retailnetwork.storefront/x")


class SkillAndRule(unittest.TestCase):
    skill = TPL / ".claude" / "skills" / "system-assets-release" / "SKILL.md"

    def test_skill_loads_and_is_template_managed(self):
        text = self.skill.read_text()
        m = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: (.+)\n---\n", text)
        self.assertTrue(m, "frontmatter must be name then description")
        self.assertEqual(m.group(1), "system-assets-release")
        self.assertIn("Use when", m.group(2))
        self.assertLessEqual(len(m.group(2)), 1024, "OpenCode description cap")
        self.assertIn(".claude/skills/system-assets-release/SKILL.md", V4.V4_MANAGED)

    def test_skill_states_the_rule(self):
        text = self.skill.read_text()
        self.assertIn("Every contract change produces a new asset", text)
        self.assertIn("system/<version>/", text)

    def test_claude_template_carries_the_exact_block_migrate_upserts(self):
        for name in ("CLAUDE.md.template", "CLAUDE.md"):
            self.assertIn(V4.SYSTEM_ASSETS_BLOCK, (TPL / name).read_text(), f"{name} lacks the system-assets block")


if __name__ == "__main__":
    unittest.main()
