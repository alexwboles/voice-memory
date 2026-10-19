# Voice Memory

**Free, self-hosted voice-agent memory layer.** MIT licensed. Zero dependencies.

Retell AI charges for per-contact voice-agent memory. The free stacks (Pipecat,
LiveKit) have nothing like it. Voice Memory fills the gap: a middleware that
sits between your voice framework and a single local JSON file.

- **Caller briefs** — preferences, open threads, recently resolved items, facts.
  Written after each call, from the transcript.
- **Session-start injection** — a compact prompt block so the agent "remembers"
  the caller on the next call.
- **Skippable flow steps** — mark scripted steps skippable when memory already
  covers them (open threads never skip — they need follow-up).
- **Transcript backfill** — replay old call logs for day-one memory.
- **PII redaction, on by default** — emails, phones, SSNs, cards, account
  numbers, addresses scrubbed before anything is stored. Names are kept (the
  brief is per-caller); `redactNames` opts into stricter masking.
- **Resolved items auto-expire** (30 days default) so briefs stay lean.
- **Caller lifecycle tools** — merge two numbers' briefs (`mergeCallers`),
  manually close threads (`resolveThread`), nudge on stale threads
  (`staleThreads`), keyword search across briefs (`search`).
- **Bring your own LLM (optional)** — rule-based extraction covers the common
  patterns deterministically; plug any callable in as an extractor hook for
  the long tail. It can never break a call.

No network calls. No API keys. No cloud. Works offline. **Two SDKs, one
schema** — a store written by one SDK is fully usable by the other.

> **callrecall-ai users:** that project has been merged into this one.
> See [MIGRATION.md](MIGRATION.md) — your store auto-upgrades on load, and
> `CallRecallMemory` still imports as an alias.

## Quick start — JS

```js
const { VoiceMemory } = require('voice-memory');
const vm = new VoiceMemory({ storePath: './voice-memory.json' });

// when a call ends:
vm.recordCall('+15551234567', { transcript: callTranscript });

// when the next call starts — drop this into the system prompt:
const memoryBlock = vm.buildInjection('+15551234567');

// before a scripted flow — skip what's already known:
const steps = vm.planFlow('+15551234567', [
  { id: 'verify', label: 'Verify identity', keywords: ['identity verified'] },
  { id: 'prefs',  label: 'Ask preferences', keywords: ['morning calls'] },
]);

// one-off: build memory from old transcripts:
vm.backfill('+15551234567', [{ transcript: oldCall1 }, { transcript: oldCall2 }]);

// search every brief:
const hits = vm.search('problem with delivery');
```

## Quick start — Python

```python
from voice_memory import VoiceMemory  # python-sdk/ on your PYTHONPATH

mem = VoiceMemory(path="./voice-memory.json")

# when a call ends:
mem.record_call("+15551234567", transcript, display_name="Sam")

# when the next call starts — inject into the system prompt:
prompt = mem.build_injection("+15551234567")

# skippable flow steps (topical matching):
mem.skippable("+15551234567", ["Ask for the order number", "Ask their name"])

# same person, two numbers -> merge into one brief:
mem.merge_callers("5559990000", "5551234567")
```

Caller IDs are normalized: `'+1 (555) 123-4567'`, `'15551234567'` and
`'555-123-4567'` all map to one brief.

## Adapters (Python)

**Pipecat** — drop the processor between your user context aggregator and the
LLM service (same slot Mem0MemoryService uses). Full example:
`examples/pipecat_example.py`.

```python
from voice_memory import VoiceMemory
from voice_memory.adapters import VoiceMemoryPipecat

mem = VoiceMemory()
recall = VoiceMemoryPipecat(mem, contact_id_fn=lambda frame: frame.caller_phone)

pipeline = Pipeline([
    transport.input(), stt, user_aggregator,
    recall.processor(),   # <-- memory goes here
    llm, assistant_aggregator, tts, transport.output(),
])
recall.on_call_end(caller_phone, transcript)  # when the call ends
```

**LiveKit** — `voice_memory.adapters.VoiceMemoryLiveKit` (same three hooks,
no hard dependency). Full example: `examples/livekit_example.py`.

```python
agent.instructions = recall.session_instructions(caller_phone,
                                                 base_instructions=agent.instructions)
recall.on_call_end(caller_phone, session.history)  # history items normalized for you
```

## API

| Job | JS | Python |
|---|---|---|
| record | `recordCall(id, {transcript, at})` | `record_call(id, transcript, display_name=, call_id=)` |
| inject | `buildInjection(id, {maxChars})` | `build_injection(id, max_chars=)` |
| flow | `planFlow(id, steps)` | `plan_flow(id, steps)` / `skippable(id, steps)` |
| backfill | `backfill(id, [...])` | `backfill(id, [...])` |
| search | `search(query, {limit})` | `search(query, limit=)` |
| merge | `mergeCallers(from, into)` | `merge_callers(from_id, into_id)` |
| resolve | `resolveThread(id, text)` | `resolve_thread(id, text)` |
| stale | `staleThreads(id, days)` | `stale_threads(id, days=)` |
| export | `exportMarkdown(id)` | `export_markdown(id)` |
| list/delete | `listCallers()` / `deleteCaller(id)` | `list_callers()` / `delete_caller(id)` |

JS options: `storePath`, `redactPII` (default true), `redactNames` (default
false), `resolvedItemTTLdays` (30), `saveOnRecall` / `recallOnInject` (true),
`staleThreadDays` (14), `extractor` (optional LLM hook).

Python options: `path`, `save_enabled` / `recall_enabled` (true),
`redact_pii` (true), `redact_names` (false), `resolved_ttl_days` (30),
`stale_thread_days` (14), `extractor` (optional LLM hook).

Pre-merge callrecall-ai names (`CallRecallMemory`, `CallRecallPipecat`,
`CallRecallLiveKit`, `context_block`, `forget`) are kept as aliases.

## Demo

```
npm run demo
```

A guided tour with two mock calls from the same caller: record → inject →
plan → export. The store is a single human-readable JSON file
(`demo/demo-store.json`).

## Tests

```
npm run smoke   # JS unit assertions
npm run e2e     # JS e2e: multi-process lifecycle, backfill, Python shim, Python SDK, migration
npm test        # both

# Python SDK:
python3 -m unittest discover -s tests -p "test_*.py"
python3 tests/smoke.py
```

## Design notes

- Extraction is heuristic and deterministic — no LLM, so behavior is testable
  and offline. The optional extractor hook covers the long tail. See
  `SPEC.md` for the pattern tables.
- Storage is one JSON file with atomic writes (temp + rename). Schema v2;
  legacy voice-memory v1 and callrecall-ai stores auto-upgrade on load.
- `redactPII` (default true), `recallOnInject` / `saveOnRecall` (default true)
  are independent toggles.

See [SPEC.md](SPEC.md) for the full specification and
[MIGRATION.md](MIGRATION.md) for the callrecall-ai upgrade notes.
