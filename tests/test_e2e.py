"""E2E: persistence across instances, legacy migration, compat shim."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python-sdk"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipecat"))

from voice_memory import VoiceMemory, normalize_caller_id  # noqa: E402
from voice_memory import migrate  # noqa: E402


def tmp_path(name="store.json"):
    return os.path.join(tempfile.mkdtemp(), name)


class TestPersistence(unittest.TestCase):
    def test_brief_survives_reinstantiation(self):
        p = tmp_path()
        VoiceMemory(path=p).record_call("c1", "Caller: I prefer texts.")
        vm2 = VoiceMemory(path=p)
        self.assertIn("texts", vm2.build_injection("c1"))
        self.assertEqual(vm2.brief("c1")["callCount"], 1)


class TestLegacyMigrationE2E(unittest.TestCase):
    def test_callrecall_store_migrates_and_serves(self):
        src = tmp_path("legacy.json")
        json.dump({"version": 1, "contacts": {
            "+15550002222": {
                "contact_id": "+15550002222", "display_name": "Jo",
                "preferences": [], "facts": [], "open_threads": [],
                "resolved": [], "call_count": 1,
                "last_call_at": "2026-10-01T00:00:00", "summary": "",
            }}}, open(src, "w"))
        dst = tmp_path("new.json")
        self.assertEqual(migrate.main([src, dst]), 0)
        vm = VoiceMemory(path=dst)
        # new calls land on the migrated brief via normalized id
        b = vm.record_call("+1 (555) 000-2222", "Caller: I prefer email.")
        self.assertEqual(b["callCount"], 2)
        self.assertEqual(b["displayName"], "Jo")
        self.assertIn("email", vm.build_injection("5550002222"))


class TestCompatShim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # pipecat/voice_memory.py is a *module* named voice_memory, which
        # collides with the python-sdk *package* — load it by file path.
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), "..", "pipecat", "voice_memory.py")
        spec = importlib.util.spec_from_file_location("voice_memory_shim", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.VoiceMemoryPipeline = mod.VoiceMemoryPipeline
        cls.shim_normalize = staticmethod(mod.normalize_caller_id)
        cls.shim_redact = staticmethod(mod.redact)

    def test_shim_lifecycle(self):
        VoiceMemoryPipeline = self.VoiceMemoryPipeline
        p = tmp_path()
        vm = VoiceMemoryPipeline(store_path=p, caller_id_fn=lambda: "py-1")
        vm.session_end("Caller: Hi, my name is Ana. I prefer morning calls. "
                       "My ssn is 123-45-6789.")
        inj = vm.session_start()
        brief = vm.get_brief()
        steps = vm.plan_flow([{"id": "x", "label": "Ask time",
                               "keywords": ["morning calls"]}])
        self.assertEqual(brief["callCount"], 1)
        self.assertEqual(brief["displayName"], "Ana")
        self.assertIn("morning calls", inj)
        self.assertNotIn("123-45-6789", json.dumps(brief))
        self.assertTrue(steps[0]["skippable"])
        self.assertIn("skip_reason", steps[0])

    def test_shim_negation(self):
        VoiceMemoryPipeline = self.VoiceMemoryPipeline
        vm = VoiceMemoryPipeline(store_path=tmp_path(), caller_id_fn=lambda: "n")
        b = vm.session_end("Caller: Hi, my name is Ana. Please never call after 6pm.")
        self.assertTrue(any(p["text"].startswith("Avoid:") and "after 6pm" in p["text"]
                            for p in b["preferences"]), b["preferences"])

    def test_shim_backfill_and_resolve(self):
        VoiceMemoryPipeline = self.VoiceMemoryPipeline
        vm = VoiceMemoryPipeline(store_path=tmp_path(), caller_id_fn=lambda: "b")
        b = vm.backfill([{"transcript": "Caller: I prefer email."},
                         {"transcript": "Caller: still waiting on the contract."}])
        self.assertEqual(b["callCount"], 2)
        closed = vm.resolve_thread("contract")
        self.assertTrue(closed["text"].startswith("Resolved:"))
        self.assertEqual(len(vm.get_brief()["openThreads"]), 0)

    def test_shim_normalize(self):
        self.assertEqual(self.shim_normalize("+1 (555) 123-4567"), "5551234567")
        self.assertEqual(self.shim_normalize(" Support-Desk "), "support-desk")
        self.assertIn("[email]", self.shim_redact("a@b.com"))

    def test_shim_persists_across_instances(self):
        VoiceMemoryPipeline = self.VoiceMemoryPipeline
        p = tmp_path()
        VoiceMemoryPipeline(store_path=p, caller_id_fn=lambda: "py-1").session_end(
            "Caller: I prefer texts.")
        vm2 = VoiceMemoryPipeline(store_path=p, caller_id_fn=lambda: "py-1")
        self.assertNotEqual(vm2.session_start(), "")
        self.assertEqual(len(vm2._data["callers"]), 1)

    def test_shim_redact_pii_false(self):
        VoiceMemoryPipeline = self.VoiceMemoryPipeline
        vm = VoiceMemoryPipeline(store_path=tmp_path(), caller_id_fn=lambda: "r",
                                 redact_pii=False)
        b = vm.session_end("Caller: my email is ana@example.com.")
        self.assertIn("ana@example.com", json.dumps(b))


if __name__ == "__main__":
    unittest.main()
