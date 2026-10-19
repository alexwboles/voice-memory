"""Pipecat adapter: drop-in memory for Pipecat voice pipelines.

Follows the placement the ecosystem converged on (Mem0MemoryService,
HindsightMemoryService): a processor between the user context aggregator
and the LLM service. Recall runs when the session starts; the finished
transcript is retained when the call ends.

Without pipecat installed you still get the full logic through the plain
methods (session_context / on_call_end) plus a dependency-free shim
processor you can unit-test. With pipecat installed, .processor()
returns a real FrameProcessor subclass.

    from voice_memory import VoiceMemory
    from voice_memory.adapters import VoiceMemoryPipecat

    mem = VoiceMemory()
    recall = VoiceMemoryPipecat(mem, contact_id_fn=lambda frame: frame.caller_phone)

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        recall.processor(),   # <-- memory goes here
        llm,
        assistant_aggregator,
        tts,
        transport.output(),
    ])
    # when the call ends:
    recall.on_call_end(caller_phone, transcript_frames_or_text)
"""
from __future__ import annotations

from .base import BaseAdapter

try:  # real FrameProcessor when pipecat is installed
    from pipecat.frames.frames import Frame as _PipecatFrame  # noqa: F401
    from pipecat.processors.frame_processor import FrameProcessor as _Base
    _HAVE_PIPECAT = True
except Exception:  # pragma: no cover
    _HAVE_PIPECAT = False
    _Base = object


class _ShimProcessor:
    """Dependency-free stand-in for a pipecat FrameProcessor.

    Accepts simple frame dicts/objects:
      {"type": "session_start"}            -> emits {"type": "context", "text": ...}
      {"type": "user_message", "text": t}  -> forwarded unchanged
      {"type": "call_end", "transcript": t} -> persists, emits {"type": "saved"}
    """

    def __init__(self, adapter: "VoiceMemoryPipecat"):
        self.adapter = adapter
        self.contact_id = ""
        self.emitted: list = []

    def process_frame(self, frame) -> list:
        ftype = frame.get("type") if isinstance(frame, dict) else getattr(frame, "type", "")
        if ftype == "session_start":
            src = frame if isinstance(frame, dict) else frame
            self.contact_id = self.adapter.contact_id_fn(src) or self.contact_id
            ctx = self.adapter.session_context(self.contact_id)
            out = [{"type": "context", "text": ctx}] if ctx else []
            self.emitted.extend(out)
            return out
        if ftype == "call_end":
            transcript = frame.get("transcript") if isinstance(frame, dict) else getattr(frame, "transcript", [])
            self.adapter.on_call_end(self.contact_id, transcript)
            out = [{"type": "saved", "contact_id": self.contact_id}]
            self.emitted.extend(out)
            return out
        return [frame]  # pass anything else straight through


class VoiceMemoryPipecat(BaseAdapter):
    """Memory hooks for a Pipecat pipeline."""

    def session_context(self, contact_id: str) -> str:
        return self.memory.build_injection(contact_id)

    def on_call_end(self, contact_id: str, transcript, **kwargs):
        return self.memory.record_call(contact_id, transcript, **kwargs)

    def processor(self, contact_id: str = ""):
        """Return a pipeline processor.

        With pipecat installed: a real FrameProcessor that injects the
        caller brief as a system-context message on session start and
        retains the transcript on call end. Without it: the dependency-free
        shim (same behavior, plain dicts).
        """
        if _HAVE_PIPECAT:
            return self._real_processor(contact_id)
        shim = _ShimProcessor(self)
        shim.contact_id = contact_id
        return shim

    def _real_processor(self, contact_id: str):
        adapter = self

        class VoiceMemoryProcessor(_Base):
            async def process_frame(self, frame, direction):  # type: ignore[override]
                await super().process_frame(frame, direction)
                ftype = type(frame).__name__
                if ftype in ("InputAudioRawFrame", "StartFrame") or "Start" in ftype:
                    cid = contact_id or adapter.contact_id_fn(frame)
                    ctx = adapter.session_context(cid)
                    if ctx:
                        # inject as a system message ahead of the LLM turn
                        from pipecat.frames.frames import LLMMessagesAppendFrame
                        await self.push_frame(
                            LLMMessagesAppendFrame(
                                [{"role": "system", "content": ctx}], run_llm=False
                            ),
                            direction,
                        )
                await self.push_frame(frame, direction)

        return VoiceMemoryProcessor()


# Back-compat alias for the pre-merge callrecall-ai API.
CallRecallPipecat = VoiceMemoryPipecat
