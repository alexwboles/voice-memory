# Voice Memory — Spec

**Free, self-hosted voice-agent memory layer.** MIT licensed. Zero dependencies.
Fills the gap Retell AI charges for: per-contact memory for voice agents, on the
free stacks (Pipecat, LiveKit) that have no equivalent.

## 1. Architecture

```
┌──────────────┐   transcript    ┌────────────────┐   brief JSON   ┌──────────────┐
│ Voice agent  │ ──────────────▶ │  VoiceMemory   │ ─────────────▶ │  Local file  │
│ (Pipecat /   │ ◀────────────── │  middleware    │ ◀───────────── │  store       │
│  LiveKit /   │  injection      │  (this lib)    │                │  (1 file)    │
│  any)        │  prompt         └────────────────┘                └──────────────┘
└──────────────┘
```

The middleware sits **between** the voice framework and a local store:

- **On call end** → `recordCall(callerId, transcript)` — extract preferences, open
  threads, resolved items, facts; redact PII; merge into the caller's brief; persist.
- **On call start** → `buildInjection(callerId)` — return a compact prompt block the
  agent injects into the system prompt so it "remembers" the caller.
- **Before running a flow** → `planFlow(callerId, steps)` — mark steps skippable
  when memory already covers them.
- **One-off** → `backfill(callerId, transcripts[])` — build a brief from old
  transcripts without a live call.

No network calls, no API keys, no cloud. Works offline.

## 2. Data model — the caller brief

One JSON object per caller, keyed by `callerId` (phone, user id, whatever the
framework provides):

```jsonc
{
  "callerId": "+15551234567",
  "displayName": "Maya",          // best-known name, optional
  "createdAt": "2026-10-01T…Z",
  "updatedAt": "2026-10-12T…Z",
  "callCount": 3,
  "preferences": [
    { "text": "Prefers morning callbacks", "addedAt": "…", "source": "call-2" }
  ],
  "openThreads": [
    { "text": "Waiting on insurance quote", "status": "open",
      "addedAt": "…", "lastMentionedAt": "…" }
  ],
  "resolvedItems": [
    { "text": "Password reset completed", "resolvedAt": "…", "expiresAt": "…" }
  ],
  "facts": [
    { "text": "Account tier: gold", "addedAt": "…" }
  ]
}
```

- **preferences** — durable likes/dislikes/how-to-treat-me. Never auto-expire.
- **openThreads** — unresolved items. Re-mentioned across calls; closed when a
  later transcript resolves them (moved to `resolvedItems`).
- **resolvedItems** — completed items. Auto-expire after `resolvedItemTTL`
  (default 30 days) so the brief stays lean.
- **facts** — stable caller facts (tier, plan, timezone). Kept until contradicted.

## 3. Transcript extraction (heuristic, deterministic)

No LLM needed — pattern-based, so behavior is testable and offline:

| Signal | Patterns (case-insensitive) |
|---|---|
| Preference | `i prefer …`, `my favorite …`, `i like …`, `don't call me …`, `call me …`, `i hate …`, `please (always\|never) …` |
| Open thread | `i'll follow up`, `let me check`, `pending`, `still waiting`, `we'll get back`, `todo`, unanswered `?` from caller |
| Resolved | `that's resolved`, `all set`, `that answers`, `problem solved`, `thanks, (that's\|that) (all\|everything)` |
| Fact | `my account …`, `i'm on the … plan`, `my timezone is …` |

Dedup: normalized text compared case-insensitively; re-mention bumps
`lastMentionedAt` instead of duplicating.

## 4. Session-start injection

`buildInjection(callerId, { maxChars })` returns a plain-text block:

```
[Caller memory — 3 prior calls]
Preferences: prefers morning callbacks; wants text summaries, not email.
Open threads: waiting on insurance quote (mentioned Oct 10).
Recently resolved: password reset completed.
Facts: account tier gold.
```

Agent drops it into the system prompt. If no brief exists, returns `""`
(no-op). `recallOnInject` (default true) keeps "saving" and "recalling" as
separate toggles per the product brief.

## 5. Skippable flow steps

`planFlow(callerId, steps)` where `steps = [{ id, label, keywords[] }]` returns
each step with `skippable: true/false` + `skipReason`:

- A step is skippable when any brief entry (preference/fact/resolved item)
  contains one of its keywords — e.g. the "verify identity" step is skippable
  if a resolved item says "identity verified Oct 12".
- Open threads never make a step skippable (they need follow-up, not skipping).

## 6. Transcript backfill

`backfill(callerId, [{ transcript, at }])` replays old transcripts in order
through the same extractor, so teams with existing call logs get day-one memory.
Backfill and live recording share one code path.

## 7. PII redaction — ON by default

Before anything is stored, the transcript text is scrubbed:

- emails → `[email]`
- phone numbers (US formats) → `[phone]`
- SSNs (`123-45-6789`) → `[ssn]`
- credit cards (13–19 digits, Luhn-checked) → `[card]`
- account/policy numbers (`account #…`, `policy …`) → `[account]`

`new VoiceMemory({ redactPII: false })` disables it. Names are intentionally NOT
redacted — the brief is per-caller and names are the point.

## 8. Storage — single local file

One JSON file (default `./voice-memory.json`), Limelit Open-style:

- Whole store = `{ version: 1, callers: { [id]: brief } }`.
- Atomic writes: write temp file → `fs.renameSync` (no torn writes).
- No database, no server, no migration story needed at v1.

## 9. Pipecat plugin (first integration target)

`pipecat/voice_memory.py` — thin adapter, stdlib only:

```python
from voice_memory import VoiceMemoryPipeline

vm = VoiceMemoryPipeline(store_path="voice-memory.json", caller_id_fn=lambda: "+1555…")

# on pipeline start:
system_prompt += vm.session_start()      # injection block ("" if new caller)
# on pipeline end with transcript:
vm.session_end(transcript_text)          # record + persist
# before a scripted flow:
steps = vm.plan_flow([{"id": "verify", "label": "Verify identity",
                       "keywords": ["identity verified"]}])
```

Works with any framework that can call two hooks — LiveKit adapter is the same
shape.

## 10. Non-goals (v1)

- No vector search / semantic recall (heuristics only; deterministic > clever).
- No multi-tenant auth (single-file store is per-deployment).
- No cloud sync.

## 11. Acceptance

- `npm run smoke` — unit assertions, all green.
- `npm run e2e` — two-call flow across process restarts, green.
- Demo runs clean: brief written after mock call 1, injected at call 2 start,
  skippable steps shown.
