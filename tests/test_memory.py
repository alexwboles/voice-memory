"""Unit tests for voice_memory.memory (VoiceMemory)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python-sdk"))

from voice_memory import VoiceMemory, normalize_caller_id  # noqa: E402


def tmp_path(name="store.json"):
    d = tempfile.mkdtemp()
    return os.path.join(d, name)


class TestRecordRecall(unittest.TestCase):
    def test_record_stores_redacted_brief(self):
        vm = VoiceMemory(path=tmp_path())
        b = vm.record_call("c1", "Caller: my email is a@b.com. I prefer texts.")
        self.assertEqual(b["callCount"], 1)
        self.assertNotIn("a@b.com", json.dumps(b))
        self.assertTrue(any("texts" in p["text"] for p in b["preferences"]))

    def test_second_call_merges_no_dupes(self):
        vm = VoiceMemory(path=tmp_path())
        vm.record_call("c1", "Caller: I prefer texts.")
        b = vm.record_call("c1", "Caller: I prefer texts.")
        self.assertEqual(b["callCount"], 2)
        self.assertEqual(len(b["preferences"]), 1)

    def test_caller_id_required(self):
        vm = VoiceMemory(path=tmp_path())
        with self.assertRaises(ValueError):
            vm.record_call("", "hi")

    def test_normalization_collapses_formats(self):
        vm = VoiceMemory(path=tmp_path())
        vm.record_call("+1 (555) 123-4567", "Caller: I prefer texts.")
        b = vm.record_call("5551234567", "Caller: My name is Priya.")
        self.assertEqual(b["callCount"], 2)
        self.assertEqual(b["callerId"], "5551234567")
        self.assertEqual(vm.list_callers(), ["5551234567"])

    def test_save_toggle(self):
        vm = VoiceMemory(path=tmp_path(), save_enabled=False)
        b = vm.record_call("c1", "Caller: I prefer texts.")
        self.assertIsNone(b)
        self.assertEqual(vm.list_callers(), [])

    def test_recall_toggle(self):
        vm = VoiceMemory(path=tmp_path())
        vm.record_call("c1", "Caller: I prefer texts.")
        vm2 = VoiceMemory(path=vm.store.path, recall_enabled=False)
        self.assertIsNone(vm2.brief("c1"))
        self.assertEqual(vm2.build_injection("c1"), "")

    def test_backfill(self):
        vm = VoiceMemory(path=tmp_path())
        b = vm.backfill("c1", ["Caller: I prefer email.",
                               {"transcript": "Caller: still waiting on the refund."}])
        self.assertEqual(b["callCount"], 2)
        self.assertTrue(len(b["preferences"]) == 1 and len(b["openThreads"]) == 1)

    def test_display_name_kwarg(self):
        vm = VoiceMemory(path=tmp_path())
        b = vm.record_call("c1", "Caller: hi.", display_name="Sam")
        self.assertEqual(b["displayName"], "Sam")

    def test_extractor_hook(self):
        vm = VoiceMemory(path=tmp_path(),
                         extractor=lambda p: {"facts": ["vip since 2019"]})
        b = vm.record_call("c1", "Caller: I prefer email.")
        self.assertTrue(any("vip since 2019" in f["text"] for f in b["facts"]))

    def test_list_of_turns_transcript(self):
        vm = VoiceMemory(path=tmp_path())
        b = vm.record_call("c1", [{"role": "user", "text": "I prefer texts."}])
        self.assertTrue(any("texts" in p["text"] for p in b["preferences"]))


class TestInjectionFlow(unittest.TestCase):
    def setUp(self):
        self.vm = VoiceMemory(path=tmp_path())
        self.vm.record_call("c1", "Caller: Hi, my name is Sam. I prefer email. "
                                  "I am still waiting on the refund.")

    def test_injection_empty_for_unknown(self):
        self.assertEqual(self.vm.build_injection("nobody"), "")

    def test_injection_contains_brief(self):
        inj = self.vm.build_injection("c1")
        self.assertIn("Sam", inj)
        self.assertIn("email", inj)
        self.assertIn("refund", inj)

    def test_injection_max_chars(self):
        self.assertLessEqual(len(self.vm.build_injection("c1", max_chars=40)), 40)

    def test_context_block_alias(self):
        self.assertEqual(self.vm.context_block("c1"), self.vm.build_injection("c1"))

    def test_plan_flow(self):
        self.vm.record_call("c1", "Caller: all set, my identity is verified.")
        steps = self.vm.plan_flow("c1", [
            {"id": "v", "label": "Verify identity", "keywords": ["identity verified"]},
            {"id": "g", "label": "Greet", "keywords": []},
        ])
        self.assertTrue(next(s for s in steps if s["id"] == "v")["skippable"])
        self.assertFalse(next(s for s in steps if s["id"] == "g")["skippable"])
        self.assertIn("skip_reason", steps[0])

    def test_open_threads_never_skippable_in_plan_flow(self):
        steps = self.vm.plan_flow("c1", [{"id": "r", "label": "Refund", "keywords": ["refund"]}])
        self.assertFalse(steps[0]["skippable"])

    def test_skippable_overlap(self):
        self.vm.record_call("c2", "Caller: my package never arrived, the issue is shipping.")
        skippable = self.vm.skippable("c2", ["Ask about the delivery problem"])
        self.assertIn("Ask about the delivery problem", skippable)

    def test_search(self):
        self.vm.record_call("cx", "Caller: my package never arrived.")
        hits = self.vm.search("problem with delivery")
        self.assertTrue(len(hits) > 0)
        self.assertEqual(hits[0][0], "cx")
        self.assertEqual(self.vm.search("zzz-no-such-thing-qqq"), [])

    def test_export_markdown(self):
        md = self.vm.export_markdown("c1")
        self.assertIn("# Caller brief: Sam", md)
        self.assertIn("## Preferences", md)
        self.assertIsNone(self.vm.export_markdown("nobody"))


class TestLifecycle(unittest.TestCase):
    def test_merge_callers(self):
        vm = VoiceMemory(path=tmp_path())
        vm.record_call("aaa", "Caller: I prefer texts. I am still waiting on the March invoice.")
        vm.record_call("bbb", "Caller: I prefer TEXTS. My timezone is Eastern.")
        merged = vm.merge_callers("bbb", "aaa")
        self.assertEqual(merged["callCount"], 2)
        prefs = [p for p in merged["preferences"] if "texts" in p["text"].lower()]
        self.assertEqual(len(prefs), 1)
        self.assertTrue(any("march invoice" in t["text"].lower() for t in merged["openThreads"]))
        self.assertTrue(any("eastern" in f["text"].lower() for f in merged["facts"]))
        self.assertIsNone(vm.brief("bbb"))
        self.assertEqual(vm.list_callers(), ["aaa"])

    def test_merge_missing_source_noop(self):
        vm = VoiceMemory(path=tmp_path())
        vm.record_call("aaa", "Caller: I prefer texts.")
        self.assertEqual(vm.merge_callers("ghost", "aaa")["callCount"], 1)

    def test_resolve_thread(self):
        vm = VoiceMemory(path=tmp_path())
        vm.record_call("c1", "Caller: I am still waiting on the March invoice.")
        closed = vm.resolve_thread("c1", "march invoice")
        self.assertTrue(closed and closed["text"].lower().startswith("resolved:"))
        b = vm.brief("c1")
        self.assertEqual(len(b["openThreads"]), 0)
        self.assertTrue(any("march invoice" in r["text"].lower() for r in b["resolvedItems"]))
        self.assertIsNone(vm.resolve_thread("c1", "no such thread"))

    def test_stale_threads(self):
        vm = VoiceMemory(path=tmp_path())
        vm.record_call("c1", "Caller: I am still waiting on the March invoice.")
        raw = vm.store.get_caller("c1")
        raw["openThreads"][0]["lastMentionedAt"] = (
            datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        vm.store.save()
        self.assertEqual(len(vm.stale_threads("c1", 14)), 1)
        self.assertEqual(len(vm.stale_threads("c1", 30)), 0)
        self.assertEqual(vm.stale_threads("ghost"), [])

    def test_expired_resolved_pruned(self):
        vm = VoiceMemory(path=tmp_path(), resolved_ttl_days=0)
        vm.record_call("c1", "Caller: all set, thanks.")
        self.assertEqual(len(vm.brief("c1")["resolvedItems"]), 0)

    def test_delete_and_forget(self):
        vm = VoiceMemory(path=tmp_path())
        vm.record_call("c1", "Caller: I prefer texts.")
        self.assertTrue(vm.delete_caller("c1"))
        self.assertIsNone(vm.brief("c1"))
        self.assertFalse(vm.delete_caller("c1"))
        vm.record_call("c2", "Caller: hi.")
        self.assertTrue(vm.forget("c2"))


class TestNormalize(unittest.TestCase):
    def test_phone_formats(self):
        self.assertEqual(normalize_caller_id("+1 (555) 123-4567"), "5551234567")
        self.assertEqual(normalize_caller_id("15551234567"), "5551234567")
        self.assertEqual(normalize_caller_id("555-123-4567"), "5551234567")

    def test_other_ids(self):
        self.assertEqual(normalize_caller_id("  Alex@Example.com "), "alex@example.com")
        self.assertEqual(normalize_caller_id("support-desk"), "support-desk")


if __name__ == "__main__":
    unittest.main()
