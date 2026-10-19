"""Smoke test: package imports, one record/recall roundtrip, store file exists."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python-sdk"))

from voice_memory import (  # noqa: E402
    VoiceMemory, CallRecallMemory, normalize_caller_id, JsonFileStore,
)
from voice_memory.adapters import (  # noqa: E402
    VoiceMemoryPipecat, VoiceMemoryLiveKit, BaseAdapter,
    CallRecallPipecat, CallRecallLiveKit,
)


def main() -> int:
    tmp = tempfile.mkdtemp()
    mem = VoiceMemory(path=os.path.join(tmp, "memory.json"))
    mem.record_call("+15550001111", [
        {"role": "assistant", "text": "Hi, how can I help?"},
        {"role": "user", "text": "Hi, I prefer text updates over calls."},
    ], display_name="Smoke Test")
    block = mem.build_injection("+15550001111")
    assert "text updates over calls" in block, block
    assert os.path.exists(os.path.join(tmp, "memory.json"))
    # back-compat aliases are the same classes
    assert CallRecallMemory is VoiceMemory
    assert CallRecallPipecat is VoiceMemoryPipecat
    assert CallRecallLiveKit is VoiceMemoryLiveKit
    # adapters construct
    VoiceMemoryPipecat(mem)
    VoiceMemoryLiveKit(mem)
    assert issubclass(VoiceMemoryPipecat, BaseAdapter)
    # caller-id normalization
    assert normalize_caller_id("+1 (555) 123-4567") == "5551234567"
    print("smoke: OK — import, record, recall, adapters, normalize")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
