"""Unit tests for voice_memory.extract (rule-based + LLM hook)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python-sdk"))

from voice_memory import extract as ex  # noqa: E402


class TestExtract(unittest.TestCase):
    def test_preference(self):
        e = ex.extract_rule_based("Caller: I prefer morning callbacks.")
        self.assertTrue(any("morning callbacks" in p for p in e["preferences"]))

    def test_negative_preference(self):
        e = ex.extract_rule_based("Caller: please never call after 6pm.")
        self.assertTrue(any(p.startswith("Avoid:") for p in e["preferences"]), e)

    def test_hate_is_avoid(self):
        e = ex.extract_rule_based("Caller: I hate robocalls.")
        self.assertTrue(any(p.startswith("Avoid:") for p in e["preferences"]))

    def test_open_thread(self):
        e = ex.extract_rule_based("Caller: I am still waiting on the refund.")
        self.assertTrue(any("refund" in t for t in e["openThreads"]))

    def test_unanswered_caller_question(self):
        e = ex.extract_rule_based("Caller: what is my current balance?")
        self.assertTrue(any("balance" in t for t in e["openThreads"]))

    def test_agent_lines_not_caller_threads(self):
        e = ex.extract_rule_based("Agent: I will follow up on that tomorrow.\nCaller: ok thanks.")
        self.assertEqual(e["openThreads"], [])

    def test_resolution(self):
        e = ex.extract_rule_based("Caller: great, that is resolved. Thanks.")
        self.assertTrue(len(e["resolvedItems"]) > 0)

    def test_resolved_closes_matching_thread(self):
        e = ex.extract_rule_based(
            "Caller: I am still waiting on the March invoice.\n"
            "Caller: that is resolved about the March invoice, thanks.")
        self.assertTrue(any(r.startswith("Resolved:") and "March invoice" in r
                            for r in e["resolvedItems"]), e)

    def test_fact(self):
        e = ex.extract_rule_based("Caller: my timezone is America/New_York.")
        self.assertTrue(len(e["facts"]) > 0)

    def test_issue_fact(self):
        e = ex.extract_rule_based("Caller: my package never arrived.")
        self.assertTrue(any(f == "issue: package never arrived" for f in e["facts"]), e)

    def test_display_name(self):
        e = ex.extract_rule_based("Caller: Hi, my name is Maya.")
        self.assertEqual(e["displayName"], "Maya")

    def test_name_not_stored_as_fact(self):
        e = ex.extract_rule_based("Caller: Hi, my name is Maya. I prefer morning calls.")
        self.assertFalse(any("maya" in f.lower() for f in e["facts"]))

    def test_list_of_turns_input(self):
        e = ex.extract_rule_based([
            {"role": "user", "text": "I prefer text updates."},
            {"role": "assistant", "text": "Noted."},
        ])
        self.assertTrue(any("text updates" in p for p in e["preferences"]))

    def test_empty(self):
        e = ex.extract_rule_based("")
        self.assertEqual(
            [e["preferences"], e["openThreads"], e["resolvedItems"], e["facts"]],
            [[], [], [], []])

    def test_normalize_turns_labeled(self):
        turns = ex.normalize_turns("Caller: hi\nAgent: hello\ncontinued")
        self.assertEqual(turns[0], {"role": "caller", "text": "hi"})
        self.assertEqual(turns[1], {"role": "agent", "text": "hello continued"})

    def test_normalize_turns_unlabeled(self):
        self.assertEqual(ex.normalize_turns("just talking"),
                         [{"role": "caller", "text": "just talking"}])


class TestExtractorHook(unittest.TestCase):
    def test_hook_merges(self):
        def hook(payload):
            assert payload["turns"][0]["role"] == "caller"
            return {"facts": ["vip since 2019"], "open_threads": ["llm thread"]}
        e = ex.extract_with_hook("Caller: I prefer email.", hook, source="call-1")
        self.assertIn("vip since 2019", e["facts"])
        self.assertIn("llm thread", e["openThreads"])
        self.assertTrue(any("email" in p for p in e["preferences"]))

    def test_hook_dedupes(self):
        e = ex.extract_with_hook("Caller: I prefer email.",
                                 lambda p: {"preferences": ["Prefers: email"]})
        self.assertEqual(len([p for p in e["preferences"] if "email" in p.lower()]), 1)

    def test_hook_exception_is_safe(self):
        def bad(payload):
            raise RuntimeError("llm down")
        e = ex.extract_with_hook("Caller: I prefer email.", bad)
        self.assertTrue(any("email" in p for p in e["preferences"]))

    def test_hook_output_scrubbed(self):
        e = ex.extract_with_hook("Caller: hi.",
                                 lambda p: {"facts": ["at sneaky@example.com"]},
                                 scrub=lambda s: s.replace("sneaky@example.com", "[email]"))
        self.assertIn("[email]", e["facts"][0])


if __name__ == "__main__":
    unittest.main()
