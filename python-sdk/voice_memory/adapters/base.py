"""Adapter interface: how voice-memory plugs into a voice-agent framework.

Every adapter wraps one VoiceMemory and exposes the same three hooks,
named for the framework's own lifecycle:

  session_context(contact_id) -> str     # call start: inject into the prompt
  on_call_end(contact_id, transcript)    # call end: extract + save
  skippable(contact_id, steps)           # flow: which steps can be skipped

contact_id should be stable per caller (phone number, account id) — derive
it from the *caller*, not the call.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    def __init__(self, memory, contact_id_fn=None):
        """
        memory: a VoiceMemory instance.
        contact_id_fn: optional fn(frame_or_event) -> contact_id. Defaults to
                       reading .contact_id / .caller_id / ["contact_id"].
        """
        self.memory = memory
        self.contact_id_fn = contact_id_fn or self._default_contact_id

    @staticmethod
    def _default_contact_id(source) -> str:
        for attr in ("contact_id", "caller_id", "phone_number", "user_id"):
            if isinstance(source, dict) and source.get(attr):
                return str(source[attr])
            if hasattr(source, attr) and getattr(source, attr):
                return str(getattr(source, attr))
        return ""

    @abstractmethod
    def session_context(self, contact_id: str) -> str:
        """Memory block to inject when a call starts ("" = new caller)."""

    @abstractmethod
    def on_call_end(self, contact_id: str, transcript, **kwargs):
        """Persist the finished call's transcript into the brief."""

    def skippable(self, contact_id: str, steps: list) -> list:
        return self.memory.skippable(contact_id, steps)

    def plan_flow(self, contact_id: str, steps: list) -> list:
        return self.memory.plan_flow(contact_id, steps)
