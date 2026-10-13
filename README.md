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
  numbers scrubbed before anything is stored. Names are kept (the brief is
  per-caller).
- **Resolved items auto-expire** (30 days default) so briefs stay lean.

No network calls. No API keys. No cloud. Works offline.

## Quick start

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
```


## API

```js
const { VoiceMemory, normalizeCallerId } = require('voice-memory');

const vm = new VoiceMemory({ storePath: './voice-memory.json' });

// Caller IDs are normalized: '+1 (555) 123-4567', '15551234567' and
// '555-123-4567' all map to one brief.
vm.recordCall('+15551234567', { transcript });   // end of call
vm.buildInjection('+15551234567');               // start of next call
vm.planFlow('+15551234567', steps);              // skippable flow steps
vm.backfill('+15551234567', [{ transcript, at }]); // replay old calls

// Same person, two numbers -> merge into one brief (source is deleted).
vm.mergeCallers('5559990000', '5551234567');

// Close an open thread by hand; files it as a resolved item.
vm.resolveThread('+15551234567', 'march invoice');

// Open threads untouched for N days (default 14) — "never got back" nudges.
vm.staleThreads('+15551234567');

vm.exportMarkdown('+15551234567');  // audit-friendly brief
vm.listCallers();                   // normalized caller IDs
vm.deleteCaller('+15551234567');
normalizeCallerId('+1 (555) 123-4567'); // -> '5551234567'
```

The Pipecat adapter mirrors the same lifecycle, including
`normalize_caller_id` and `vm.resolve_thread(text)`.

## Pipecat

`pipecat/voice_memory.py` is a stdlib-only adapter with the same lifecycle:

```python
from voice_memory import VoiceMemoryPipeline

vm = VoiceMemoryPipeline(store_path="voice-memory.json",
                         caller_id_fn=lambda: caller_phone)

system_prompt += vm.session_start()   # on pipeline start
vm.session_end(transcript_text)       # on pipeline end
vm.plan_flow([...])                   # before scripted flows
```

## Demo

```
npm run demo
```

Runs two mock calls: records a brief after call 1 (watch the PII redaction),
injects it at call 2 start, and shows skippable flow steps.

## Tests

```
npm run smoke   # 38 unit assertions
npm run e2e     # 10 end-to-end (multi-process persistence, backfill, Python adapter)
npm test        # both
```

## Design notes

- Extraction is heuristic and deterministic — no LLM, so behavior is testable
  and offline. See `SPEC.md` for the pattern tables.
- Storage is one JSON file with atomic writes (temp + rename).
- `redactPII` (default true), `recallOnInject` / `saveOnRecall` (default true)
  are independent toggles.

See [SPEC.md](SPEC.md) for the full specification.
