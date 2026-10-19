"""Voice Memory — Pipecat pipeline adapter.

Compatibility shim: the canonical Python implementation now lives in
``python-sdk/voice_memory`` (shared rule-based extractor, unified v2 brief
schema, Pipecat + LiveKit adapters). This module keeps the original
single-file API working on top of it:

    from voice_memory import VoiceMemoryPipeline

    vm = VoiceMemoryPipeline(
        store_path="voice-memory.json",
        caller_id_fn=lambda: "+15551234567",   # however you identify callers
    )

    system_prompt += vm.session_start()   # on pipeline start ("" if new caller)
    vm.session_end(transcript_text)       # on pipeline end
    steps = vm.plan_flow([...])           # before a scripted flow

New code should import from the package instead::

    from voice_memory import VoiceMemory
    from voice_memory.adapters import VoiceMemoryPipecat, VoiceMemoryLiveKit
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.join(_HERE, "..", "python-sdk")

if __name__ == "voice_memory":
    # This file shadows the real voice_memory package (pipecat/ is first on
    # sys.path). Temporarily step aside — drop our half-initialized entry
    # from sys.modules and the pipecat dir from sys.path — so the names
    # below bind to the real package, then restore this module under its
    # own name before defining the shim API.
    _shim_module = sys.modules.pop(__name__)
    _saved_path = sys.path
    sys.path = [p for p in _saved_path
                if os.path.abspath(p) != os.path.abspath(_HERE)]
    sys.path.insert(0, _PKG_DIR)
    try:
        from voice_memory import VoiceMemory, normalize_caller_id, DEFAULT_TTL_DAYS
        from voice_memory.redact import redact_text as _redact_text
        from voice_memory.adapters.pipecat import VoiceMemoryPipecat
    finally:
        sys.path = _saved_path
        sys.modules[__name__] = _shim_module
    del _shim_module, _saved_path
else:
    # Loaded under another name (e.g. by file path): no shadowing, the
    # package resolves normally.
    sys.path.insert(0, _PKG_DIR)
    from voice_memory import (  # noqa: E402
        VoiceMemory,
        normalize_caller_id,
        DEFAULT_TTL_DAYS,
    )
    from voice_memory.redact import redact_text as _redact_text  # noqa: E402
    from voice_memory.adapters.pipecat import VoiceMemoryPipecat  # noqa: E402


def redact(text, enabled=True):
    """Scrub PII from transcript text. Names are intentionally kept."""
    return _redact_text(text, enabled=enabled)


class VoiceMemoryPipeline(VoiceMemoryPipecat):
    """Pipecat-side adapter for the voice-memory brief lifecycle.

    Same public API as the original single-file adapter; the store,
    extraction, and redaction now come from the shared ``voice_memory``
    package (brief schema v2 — legacy stores auto-upgrade on load).
    """

    def __init__(self, store_path="voice-memory.json", caller_id_fn=None,
                 redact_pii=True, resolved_ttl_days=DEFAULT_TTL_DAYS):
        mem = VoiceMemory(
            path=store_path,
            redact_pii=redact_pii,
            resolved_ttl_days=resolved_ttl_days,
        )
        super().__init__(mem, contact_id_fn=caller_id_fn or (lambda: "unknown"))

    @property
    def _data(self):
        """The raw store dict ({"version": 2, "callers": {...}})."""
        return self.memory.store.data

    def _current_id(self, caller_id=None):
        cid = caller_id if caller_id is not None else self.contact_id_fn()
        return normalize_caller_id(cid)

    # -- lifecycle -------------------------------------------------------
    def session_end(self, transcript):
        """Call when the pipeline ends. Extracts, redacts, persists."""
        return self.on_call_end(self._current_id(), transcript)

    def get_brief(self, caller_id=None):
        return self.memory.brief(self._current_id(caller_id))

    def resolve_thread(self, thread_text, caller_id=None):
        """Manually close an open thread (normalized-text or substring match).
        Returns the closed thread entry, or None when nothing matched."""
        return self.memory.resolve_thread(self._current_id(caller_id), thread_text)

    def session_start(self, max_chars=1200):
        """Injection block for the system prompt ("" when caller is new)."""
        return self.memory.build_injection(self._current_id(), max_chars=max_chars)

    def plan_flow(self, steps):
        """Mark steps skippable when the brief already covers them.

        Returns [{...step, "skippable": bool, "skip_reason": str|None}].
        """
        return self.memory.plan_flow(self._current_id(), steps)

    def backfill(self, transcripts):
        """Replay old transcripts: transcripts = [{"transcript": str, ...}]."""
        return self.memory.backfill(self._current_id(), transcripts)
