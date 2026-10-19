# Migration guide: callrecall-ai → voice-memory

**callrecall-ai has been merged into voice-memory.** Active development
continues at <https://github.com/alexwboles/voice-memory>; the callrecall-ai
repo is archived and read-only.

The merge is a feature union, not just a dedupe:

| Kept from voice-memory | Kept from callrecall-ai |
|---|---|
| JS SDK, single brief schema + SPEC | Python SDK, Pipecat + LiveKit adapters |
| Caller-ID normalization | LLM extractor hook (now in **both** SDKs) |
| `mergeCallers` / `staleThreads` (now in **both** SDKs) | `search()` across briefs (now in **both** SDKs) |
| `resolveThread`, `planFlow` keywords, `exportMarkdown` | `skippable()` topical-overlap matching, save/recall toggles |
| `redactPII` on by default | `redact_names` opt-in, address/zip redaction |

## Store migration

Your existing `memory.json` keeps working — **both SDKs auto-upgrade legacy
stores on load**:

- voice-memory v1 (`{version: 1, callers}`) → used as-is, rewritten as v2.
- callrecall-ai (`{version: 1, contacts}`) → converted to the v2 shape.

To migrate explicitly (e.g. before switching SDKs):

```bash
# from the voice-memory repo:
python3 -m voice_memory.migrate /path/to/callrecall-memory.json ./voice-memory.json
# (add python-sdk/ to PYTHONPATH, or pip install ./python-sdk)
```

### Field mapping (callrecall-ai → voice-memory v2)

| callrecall-ai | voice-memory v2 |
|---|---|
| `contacts` (store key) | `callers` |
| `contact_id` | `callerId` |
| `display_name` | `displayName` |
| `call_count` | `callCount` |
| `last_call_at` | `updatedAt` / `lastCallAt` |
| `summary` | `summary` |
| `open_threads[]` | `openThreads[]` (+ `status: "open"`) |
| `resolved[]` | `resolvedItems[]` |
| `BriefItem.text / .source / .tags` | unchanged |
| `BriefItem.created_at` | `addedAt` / `lastMentionedAt` |
| `BriefItem.valid_until` | `expiresAt` |

Legacy contact keys are **normalized on upgrade** (`+15550001111` →
`5550001111`), because the merged product normalizes all caller IDs. If two
legacy keys normalize to the same id, their briefs merge (lists dedupe,
call counts add up).

## Python API mapping

`CallRecallMemory` still imports — it is now an alias for `VoiceMemory`:

```python
from voice_memory import VoiceMemory          # new canonical name
from voice_memory import CallRecallMemory      # still works (alias)
```

| callrecall-ai | voice-memory |
|---|---|
| `CallRecallMemory(path=...)` | `VoiceMemory(path=...)` (default `./voice-memory.json`) |
| `context_block(id)` | `build_injection(id)` (`context_block` kept as alias) |
| `skippable(id, steps)` | unchanged |
| `brief(id)` | unchanged |
| `backfill(id, transcripts)` | unchanged |
| `forget(id)` | `delete_caller(id)` (`forget` kept as alias) |
| `CallRecallPipecat` / `CallRecallLiveKit` | `VoiceMemoryPipecat` / `VoiceMemoryLiveKit` (old names kept as aliases) |
| `record_call(id, transcript)` | unchanged, plus `display_name=` / `call_id=` kwargs |

### Behavior changes to know about

1. **Caller IDs are now normalized** (`+1 (555) 123-4567` → `5551234567`).
   callrecall-ai used raw IDs; after migration, lookups normalize — which is
   what the key upgrade does for you.
2. **Names are kept by default.** callrecall-ai masked person names in stored
   text (`[NAME]`); the merged default keeps them (the brief is per-caller).
   Pass `redact_names=True` for the old stricter behavior.
3. **One rule-based extractor.** The pattern tables are unified (SPEC.md §3):
   preference polarity is `Prefers:`/`Avoid:` (not `prefers {}`/`dislikes {}`),
   issues extract as `issue: …` facts, and the redundant-pattern dedupe from
   callrecall-ai's `_dedupe` is replaced by exact normalized-text dedupe.
4. **Redaction tokens are lowercase** (`[email]`, `[phone]`, …) in both SDKs
   so JS- and Python-written stores are interchangeable.
5. **No 50-item cap.** callrecall-ai capped each list at 50 items; the merged
   brief keeps everything (resolved items still auto-expire).
6. The store default path is `./voice-memory.json` (was `~/.callrecall/memory.json`).

## JS API mapping

The JS SDK is unchanged apart from additions — existing code keeps working:

- New options: `extractor` (LLM hook), `redactNames` (opt-in).
- New method: `search(query, { limit })`.
- New exports: `normalizeTurns`, `redactNames`.
- Store version is now 2 (legacy stores auto-upgrade on load).
