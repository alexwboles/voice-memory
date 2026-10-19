# Voice Memory — Spec (v2)

**Free, self-hosted voice-agent memory layer.** MIT licensed. Zero dependencies.
Fills the gap Retell AI charges for: per-contact memory for voice agents, on the
free stacks (Pipecat, LiveKit) that have no equivalent.

Two SDKs, one spec, one brief schema:

- **JS SDK** (`src/`) — `VoiceMemory` class, Node.
- **Python SDK** (`python-sdk/voice_memory/`) — `VoiceMemory` class, plus
  Pipecat and LiveKit adapters (`voice_memory.adapters`).

## 1. Architecture

```
┌──────────────┐   transcript    ┌────────────────┐   brief JSON   ┌──────────────┐
│ Voice agent  │ ──────────────▶ │  VoiceMemory   │ ─────────────▶ │  Local file  │
│ (Pipecat /   │ ◀────────────── │  middleware    │ ◀───────────── │  store       │
│  LiveKit /   │  injection      │  (JS or Python │                │  (1 file)    │
│  any)        │  prompt         │   SDK)         └────────────────┘              │
└──────────────┘                                                                │
         ▲  Pipecat / LiveKit adapters wrap the Python SDK ▲                    │
└──────────────────────────────────────────────────────────────────────────────┘
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

## 2. Data model — the caller brief (schema v2)

One JSON object per caller, keyed by **normalized** `callerId` (phone, user id,
whatever the framework provides). Phone-like IDs collapse to bare digits
(`+1 (555) 123-4567` == `15551234567` == `5551234567`); anything else is
trimmed and lowercased.

```jsonc
{
  "callerId": "+15551234567",
  "displayName": "Maya",          // best-known name, optional
  "createdAt": "2026-10-01T…Z",
  "updatedAt": "2026-10-12T…Z",
  "lastCallAt": "2026-10-12T…Z",  // optional
  "callCount": 3,
  "summary": "Prefers: … | Open: …",  // optional, refreshed on write
  "preferences": [
    { "text": "Prefers morning callbacks", "addedAt": "…",
      "lastMentionedAt": "…", "source": "call-2", "tags": [] }
  ],
  "openThreads": [
    { "text": "Waiting on insurance quote", "status": "open",
      "addedAt": "…", "lastMentionedAt": "…", "source": "call-2", "tags": [] }
  ],
  "resolvedItems": [
    { "text": "Password reset completed", "addedAt": "…",
      "lastMentionedAt": "…", "resolvedAt": "…", "expiresAt": "…",
      "source": "call-3", "tags": [] }
  ],
  "facts": [
    { "text": "Account tier: gold", "addedAt": "…",
      "lastMentionedAt": "…", "source": "call-1", "tags": [] }
  ]
}
```

- **preferences** — durable likes/dislikes/how-to-treat-me. Never auto-expire.
- **openThreads** — unresolved items. Re-mentioned across calls (bumps
  `lastMentionedAt` instead of duplicating); closed when a later transcript
  resolves them (moved to `resolvedItems`).
- **resolvedItems** — completed items. Auto-expire after `resolvedItemTTL`
  (default 30 days) so the brief stays lean.
- **facts** — stable caller facts (tier, plan, timezone, reported issues).
  Kept until contradicted.
- **tags** — string list for classifier/LLM-extractor provenance (e.g. `["llm"]`).

Both SDKs read and write this exact shape; a store written by one SDK is
fully usable by the other.

## 3. Transcript extraction (heuristic, deterministic)

No LLM needed — pattern-based, so behavior is testable and offline.
Both SDKs implement the same tables.

| Signal | Patterns (case-insensitive) |
|---|---|
| Preference | `i prefer …`, `my favorite …`, `i like …`, `don't call me …`, `call me …`, `i hate …`, `please (always\|never) …` |
| Open thread | `i'll follow up`, `let me check`, `pending`, `still waiting`, `we'll get back`, `todo`, unanswered `?` from caller |
| Resolved | `that's resolved`, `all set`, `that answers`, `problem solved`, `thanks, (that's\|that) (all\|everything)` |
| Fact | `my account …`, `i'm on the … plan`, `my timezone is …` |
| Issue | `my … (never arrived\|is broken\|doesn't work\|was lost\|…)` → `issue: …` |

Speaker-aware: caller lines drive preferences/threads/facts; either side can
signal resolution. Polarity: a negation word (`hate`, `dislike`, `don't`,
`never`) in the sentence flips a preference to `Avoid: …`.

Dedup: normalized text compared case-insensitively; re-mention bumps
`lastMentionedAt` instead of duplicating.

### 3a. LLM extractor hook (optional)

Rule-based extraction covers the common patterns deterministically. For the
long tail, pass any callable:

```js
// JS
const vm = new VoiceMemory({
  storePath: './voice-memory.json',
  extractor: ({ transcript, turns }) => myLLM(transcript), // -> { preferences, facts, openThreads, resolvedItems }
});
```

```python
# Python
mem = VoiceMemory(extractor=lambda payload: my_llm(payload["transcript"]))
```

The hook receives `{ transcript, turns }` (`turns` = `[{role: 'caller'|'agent',
text}]`) and returns string lists, merged over the rule-based result with
dedupe. Hook output is PII-scrubbed before storage. **An extractor can never
break a call** — exceptions are caught and the rule-based result stands.

## 4. Session-start injection

`buildInjection(callerId, { maxChars })` returns a plain-text block:

```
[Caller memory — 3 prior calls (Maya)]
Preferences: prefers morning callbacks; wants text summaries, not email.
Open threads: waiting on insurance quote (mentioned Oct 10).
Recently resolved: password reset completed.
Facts: account tier gold.
```

Agent drops it into the system prompt. If no brief exists, returns `""`
(no-op). `recallOnInject` / `saveOnRecall` (JS) and `recall_enabled` /
`save_enabled` (Python) keep "saving" and "recalling" as separate toggles.

## 5. Skippable flow steps

`planFlow(callerId, steps)` where `steps = [{ id, label, keywords[] }]` returns
each step with `skippable: true/false` + `skipReason`:

- A step is skippable when any brief entry (preference/fact/resolved item)
  contains one of its keywords — e.g. the "verify identity" step is skippable
  if a resolved item says "identity verified Oct 12".
- Open threads never make a step skippable (they need follow-up, not skipping).

The Python SDK additionally offers `skippable(callerId, steps, threshold)` for
plain-description steps, matched by topical token overlap (synonym-aware:
"problem" matches "issue"). `search(query)` — keyword search across all
briefs — exists in both SDKs.

## 6. Caller lifecycle extras

- `mergeCallers(fromId, intoId)` — same person, two numbers → one brief.
  Lists dedupe keeping the earliest `addedAt`; call counts add up; the source
  brief is deleted.
- `resolveThread(callerId, text)` — manually close an open thread
  (normalized-text or substring match); filed as a resolved item with the
  standard TTL.
- `staleThreads(callerId, days)` — open threads not mentioned in `days` days
  (default 14) — "never got back" nudges.
- `exportMarkdown(callerId)` — the brief as a portable Markdown document.

## 7. Transcript backfill

`backfill(callerId, [{ transcript, at }])` replays old transcripts in order
through the same extractor, so teams with existing call logs get day-one memory.
Backfill and live recording share one code path.

## 8. PII redaction — ON by default

Before anything is stored, the transcript text is scrubbed (both SDKs, same
tokens so stores are interchangeable):

- emails → `[email]`
- phone numbers (US formats, 10+ digits) → `[phone]`
- SSNs (`123-45-6789`) → `[ssn]`
- credit cards (13–19 digits, Luhn-checked) → `[card]`
- account/policy numbers (`account #…`, `policy …`) → `account [account]`
- street addresses (+ glued zip) → `[address]`

`new VoiceMemory({ redactPII: false })` disables it. **Names are intentionally
NOT redacted by default** — the brief is per-caller and names are the point.
`redactNames: true` opts into person-name masking for stricter deployments.

## 9. Storage — single local file (v2)

One JSON file (default `./voice-memory.json`):

- Whole store = `{ version: 2, callers: { [callerId]: brief } }`.
- Atomic writes: write temp file → rename (no torn writes).
- No database, no server.
- **Legacy upgrade on load:** voice-memory v1 stores (`callers`) are used
  as-is; callrecall-ai stores (`contacts`, snake_case briefs) are converted
  to the v2 shape automatically — contact keys are normalized (colliding keys
  merge), `valid_until` → `expiresAt`, `resolved` → `resolvedItems`. A
  standalone CLI is also available:
  `python -m voice_memory.migrate legacy.json voice-memory.json`.

## 10. SDK API map

| Job | JS (`VoiceMemory`) | Python (`VoiceMemory`) |
|---|---|---|
| record | `recordCall(id, {transcript, at})` | `record_call(id, transcript, display_name=, call_id=)` |
| inject | `buildInjection(id, {maxChars})` | `build_injection(id, max_chars=)` (`context_block` alias) |
| flow | `planFlow(id, steps)` | `plan_flow(id, steps)` / `skippable(id, steps)` |
| backfill | `backfill(id, [{transcript, at}])` | `backfill(id, transcripts)` |
| search | `search(query, {limit})` | `search(query, limit=)` |
| merge | `mergeCallers(from, into)` | `merge_callers(from_id, into_id)` |
| resolve | `resolveThread(id, text)` | `resolve_thread(id, text)` |
| stale | `staleThreads(id, days)` | `stale_threads(id, days=)` |
| export | `exportMarkdown(id)` | `export_markdown(id)` |
| list/delete | `listCallers()` / `deleteCaller(id)` | `list_callers()` / `delete_caller(id)` (`forget` alias) |
| read | `getBrief(id)` | `brief(id)` |
| normalize | `normalizeCallerId(id)` | `normalize_caller_id(id)` |
| toggles | `redactPII, redactNames, saveOnRecall, recallOnInject, resolvedItemTTLdays, staleThreadDays, extractor` | `redact_pii, redact_names, save_enabled, recall_enabled, resolved_ttl_days, stale_thread_days, extractor` |

Pre-merge callrecall-ai names (`CallRecallMemory`, `CallRecallPipecat`,
`CallRecallLiveKit`, `context_block`, `forget`) are kept as aliases.

## 11. Adapters (Python)

`voice_memory.adapters`:

- **Pipecat** (`VoiceMemoryPipecat`) — `session_context(id)` /
  `on_call_end(id, transcript)` / `skippable(id, steps)`, plus
  `.processor()` returning a real Pipecat `FrameProcessor` when pipecat is
  installed (injects the brief as a system message on session start) or a
  dependency-free shim otherwise.
- **LiveKit** (`VoiceMemoryLiveKit`) — same three hooks, plus
  `session_instructions(id, base_instructions)` which prepends the brief to
  the agent's instructions; `on_call_end` normalizes LiveKit history items.

The legacy single-file adapter `pipecat/voice_memory.py`
(`VoiceMemoryPipeline`) is kept as a compatibility shim over the package.

## 12. Non-goals (v2)

- No vector search / semantic recall (heuristics only; deterministic > clever).
- No multi-tenant auth (single-file store is per-deployment).
- No cloud sync.

## 13. Acceptance

- `npm run smoke` — JS unit assertions, all green.
- `npm run e2e` — two-call flow across process restarts, backfill, Python
  Pipecat shim, Python SDK (record/inject/search/merge/streams/adapters),
  legacy migration — all green.
- `python3 -m unittest discover -s tests -p "test_*.py"` — Python unit + e2e,
  all green.
- Demo runs clean: brief written after mock call 1, injected at call 2 start,
  skippable steps shown.

See [MIGRATION.md](MIGRATION.md) for the callrecall-ai → voice-memory
upgrade notes.
