"""LiveKit example: wire voice-memory into a LiveKit Agents session.

LiveKit Agents sessions expose lifecycle events (session start, transcript
via AgentSession history). Wire the two one-liners below into your own
callbacks — no hard dependency on livekit-agents.
"""
from voice_memory import VoiceMemory
from voice_memory.adapters import VoiceMemoryLiveKit

memory = VoiceMemory(path="./voice-memory.json")
recall = VoiceMemoryLiveKit(memory)


def on_session_started(session, agent, caller_phone):
    # Prepend the caller brief to the agent's instructions.
    agent.instructions = recall.session_instructions(
        caller_phone, base_instructions=agent.instructions)


def on_session_ended(session, caller_phone):
    # Persist the finished transcript into the caller's brief.
    # session.history items look like {"role": ..., "content": ...};
    # the adapter normalizes them for you.
    recall.on_call_end(caller_phone, session.history)


# Skippable flow steps before a scripted flow:
#
#   for step in recall.skippable(caller_phone, ["Ask for the order number"]):
#       flow.mark_skipped(step)
