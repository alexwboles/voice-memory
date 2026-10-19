"""Unit tests for the Pipecat + LiveKit adapters."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python-sdk"))

from voice_memory import VoiceMemory  # noqa: E402
from voice_memory.adapters import (  # noqa: E402
    VoiceMemoryPipecat, VoiceMemoryLiveKit, BaseAdapter,
)


def tmp_mem(**kw):
    return VoiceMemory(path=os.path.join(tempfile.mkdtemp(), "m.json"), **kw)


class TestPipecatAdapter(unittest.TestCase):
    def test_session_context_and_call_end(self):
        mem = tmp_mem()
        ad = VoiceMemoryPipecat(mem, contact_id_fn=lambda f: f["caller"])
        self.assertEqual(ad.session_context("new-caller"), "")
        ad.on_call_end("c1", "Caller: Hi, my name is Bea. I prefer texts.")
        ctx = ad.session_context("c1")
        self.assertIn("Bea", ctx)
        self.assertIn("texts", ctx)

    def test_shim_processor_lifecycle(self):
        mem = tmp_mem()
        ad = VoiceMemoryPipecat(mem)
        proc = ad.processor()
        out = proc.process_frame({"type": "session_start", "caller_id": "shim-1"})
        self.assertEqual(out, [])  # new caller -> no context
        proc.process_frame({"type": "call_end",
                            "transcript": "Caller: I prefer morning calls."})
        out = proc.process_frame({"type": "session_start", "caller_id": "shim-1"})
        self.assertEqual(len(out), 1)
        self.assertIn("morning calls", out[0]["text"])

    def test_shim_passes_other_frames_through(self):
        ad = VoiceMemoryPipecat(tmp_mem())
        proc = ad.processor()
        frame = {"type": "user_message", "text": "hi"}
        self.assertEqual(proc.process_frame(frame), [frame])

    def test_default_contact_id_fn(self):
        mem = tmp_mem()
        ad = VoiceMemoryPipecat(mem)
        self.assertEqual(ad.contact_id_fn({"caller_id": "x"}), "x")

        class Obj:
            phone_number = "y"
        self.assertEqual(ad.contact_id_fn(Obj()), "y")

    def test_skippable_passthrough(self):
        mem = tmp_mem()
        mem.record_call("c1", "Caller: my package never arrived.")
        ad = VoiceMemoryPipecat(mem)
        self.assertIn("Ask about the delivery problem",
                      ad.skippable("c1", ["Ask about the delivery problem"]))


class TestLiveKitAdapter(unittest.TestCase):
    def test_session_instructions_prepends_brief(self):
        mem = tmp_mem()
        mem.record_call("c1", "Caller: I prefer texts.")
        ad = VoiceMemoryLiveKit(mem)
        out = ad.session_instructions("c1", base_instructions="Be helpful.")
        self.assertIn("Caller memory", out)
        self.assertIn("Be helpful.", out)

    def test_session_instructions_empty_for_new_caller(self):
        ad = VoiceMemoryLiveKit(tmp_mem())
        self.assertEqual(ad.session_instructions("new", base_instructions="Hi."), "Hi.")

    def test_on_call_end_normalizes_history(self):
        mem = tmp_mem()
        ad = VoiceMemoryLiveKit(mem)
        ad.on_call_end("c1", [
            {"role": "user", "content": "I prefer texts."},
            {"role": "assistant", "content": [{"text": "Noted."}]},
        ])
        self.assertIn("texts", ad.session_context("c1"))

    def test_is_base_adapter(self):
        self.assertTrue(issubclass(VoiceMemoryLiveKit, BaseAdapter))


if __name__ == "__main__":
    unittest.main()
