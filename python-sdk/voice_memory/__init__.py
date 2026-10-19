"""voice-memory: free, self-hosted voice-agent memory layer (Python SDK).

Per-caller briefs stored in one human-inspectable JSON file.
MIT licensed. Zero dependencies. No network calls, no API keys, no cloud.

Quick start:
    from voice_memory import VoiceMemory

    mem = VoiceMemory()  # ./voice-memory.json
    mem.record_call("+15551234567", transcript, display_name="Sam")
    print(mem.build_injection("+15551234567"))  # inject into your system prompt

Framework adapters (Pipecat, LiveKit) live in ``voice_memory.adapters``.
"""
from .memory import (
    VoiceMemory,
    CallRecallMemory,
    normalize_caller_id,
    DEFAULT_TTL_DAYS,
    DEFAULT_STALE_THREAD_DAYS,
)
from .store import JsonFileStore, STORE_VERSION, DEFAULT_PATH
from . import brief, redact, extract, migrate
from . import adapters

__version__ = "2.0.0"
__all__ = [
    "VoiceMemory",
    "CallRecallMemory",  # pre-merge callrecall-ai alias
    "normalize_caller_id",
    "DEFAULT_TTL_DAYS",
    "DEFAULT_STALE_THREAD_DAYS",
    "JsonFileStore",
    "STORE_VERSION",
    "DEFAULT_PATH",
    "brief",
    "redact",
    "extract",
    "migrate",
    "adapters",
]
