"""LiveKit adapter (interface-ready): memory hooks for LiveKit Agents.

LiveKit Agents sessions expose lifecycle events (session start, transcript
via `AgentSession` history / `conversation_item_added`). This adapter maps
the same three voice-memory hooks onto them without taking a hard dependency
on livekit-agents — wire the two one-liners into your own callbacks:

    from voice_memory import VoiceMemory
    from voice_memory.adapters import VoiceMemoryLiveKit

    mem = VoiceMemory()
    recall = VoiceMemoryLiveKit(mem)

    @session.on("session_started")          # your LiveKit Agents wiring
    def _start(ev):
        agent.instructions = recall.session_instructions(
            caller_phone, base_instructions=agent.instructions)

    @session.on("session_ended")
    def _end(ev):
        recall.on_call_end(caller_phone, session.history)
"""
from __future__ import annotations

from .base import BaseAdapter


class VoiceMemoryLiveKit(BaseAdapter):
    """Memory hooks for a LiveKit Agents session."""

    def session_context(self, contact_id: str) -> str:
        return self.memory.build_injection(contact_id)

    def session_instructions(self, contact_id: str, base_instructions: str = "") -> str:
        """Prepend the caller brief to the agent's instructions at session start."""
        ctx = self.session_context(contact_id)
        if not ctx:
            return base_instructions
        head = "Caller memory (from previous calls):\n" + ctx
        return f"{head}\n\n{base_instructions}" if base_instructions else head

    def on_call_end(self, contact_id: str, transcript, **kwargs):
        # LiveKit history items look like {"role": ..., "content": ...};
        # normalize to the transcript shape record_call expects.
        norm = []
        for t in transcript or []:
            if isinstance(t, dict):
                text = t.get("text", t.get("content", ""))
                if isinstance(text, list):  # content blocks
                    text = " ".join(
                        b.get("text", "") for b in text if isinstance(b, dict))
                norm.append({"role": t.get("role", ""), "text": str(text)})
            else:
                norm.append({"role": getattr(t, "role", ""), "text": str(t)})
        return self.memory.record_call(contact_id, norm, **kwargs)


# Back-compat alias for the pre-merge callrecall-ai API.
CallRecallLiveKit = VoiceMemoryLiveKit
