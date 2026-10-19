"""VoiceMemory — middleware between a voice framework and a local file store.

    from voice_memory import VoiceMemory

    mem = VoiceMemory(path="./voice-memory.json")

    mem.record_call("+15551234567", transcript)   # call end
    mem.build_injection("+15551234567")           # next call start
    mem.plan_flow("+15551234567", steps)          # skippable flow steps
    mem.backfill("+15551234567", [transcript, ...])

Two toggles, like Retell's "use contact memory" / per-agent save settings:
  save_enabled   — record_call() writes/updates the brief (default True)
  recall_enabled — brief()/build_injection() return memory (default True)

Turn either off and that direction becomes a no-op, so one memory instance
can serve agents that only save, only recall, or both.

A ``CallRecallMemory`` alias is kept for code written against callrecall-ai;
it is the same class.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import brief as brief_mod
from . import extract as extract_mod
from . import redact as redact_mod
from .store import JsonFileStore, normalize_caller_id

DAY_SECONDS = 24 * 60 * 60
DEFAULT_TTL_DAYS = 30
DEFAULT_STALE_THREAD_DAYS = 14

_WORD = re.compile(r"[a-z0-9]+")

# canonicalize near-synonyms so "problem" matches "issue", "updates" matches
# "update", etc. — search/skippable matching is topical, not just lexical.
_SYNONYMS = {
    "problem": "issue", "problems": "issue",
    "asking": "ask", "asked": "ask",
    "prefers": "prefer", "preferred": "prefer", "preference": "prefer",
    "updates": "update", "updated": "update", "callbacks": "callback",
    "likes": "like", "loves": "like",
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "it",
    "you", "your", "we", "do", "does", "what", "when", "how", "please", "me",
    "my", "with", "ask", "about", "get", "can", "i", "this", "that",
}


def _tokens(s: str) -> set[str]:
    return {_SYNONYMS.get(t, t) for t in _WORD.findall(str(s).lower())}


def _overlap(a: str, b: str) -> float:
    """Topical overlap: fraction of the *smaller* side's tokens that are shared."""
    ta, tb = _tokens(a) - _STOPWORDS, _tokens(b) - _STOPWORDS
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).lower()).strip()


# normalize_caller_id lives in store.py (it is the store's key function).


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoiceMemory:
    def __init__(
        self,
        path: str | None = None,
        *,
        save_enabled: bool = True,
        recall_enabled: bool = True,
        redact_pii: bool = True,
        redact_names: bool = False,
        resolved_ttl_days: int = DEFAULT_TTL_DAYS,
        stale_thread_days: int = DEFAULT_STALE_THREAD_DAYS,
        extractor=None,  # optional LLM hook: fn({"transcript","turns"}) -> dict
        store: JsonFileStore | None = None,
    ):
        self.store = store or JsonFileStore(path or "./voice-memory.json")
        self.save_enabled = save_enabled
        self.recall_enabled = recall_enabled
        self.redact_pii = redact_pii
        self.redact_names = redact_names
        self.resolved_ttl = timedelta(days=resolved_ttl_days)
        self.stale_thread_days = stale_thread_days
        self.extractor = extractor

    # -- internals --------------------------------------------------------
    def _id(self, caller_id: str) -> str:
        cid = normalize_caller_id(caller_id)
        if not cid:
            raise ValueError("caller_id is required")
        return cid

    def _get_brief(self, caller_id: str) -> dict:
        cid = self._id(caller_id)
        raw = self.store.get_caller(cid)
        if raw is None:
            b = brief_mod.blank_brief(cid)
            self.store.set_caller(cid, b)
            return b
        return brief_mod.normalize_brief(raw, cid)

    def _upsert(self, items: list[dict], text: str, **extra) -> dict:
        n = _norm(text)
        for e in items:
            if _norm(e["text"]) == n:
                e["lastMentionedAt"] = _utcnow()
                return e
        entry = {"text": text, "addedAt": _utcnow(),
                 "lastMentionedAt": _utcnow(), "tags": [], **extra}
        items.append(entry)
        return entry

    def _scrub(self, text: str) -> str:
        return redact_mod.redact_text(text, enabled=self.redact_pii,
                                      names=self.redact_names)

    def _merge(self, b: dict, extracted: dict, source: str) -> None:
        for p in extracted["preferences"]:
            self._upsert(b["preferences"], p, source=source)
        for t in extracted["openThreads"]:
            self._upsert(b["openThreads"], t, status="open", source=source)
        for r in extracted["resolvedItems"]:
            # A resolved thread closes the matching open thread.
            m = re.match(r"^Resolved:\s*(.+)$", r, re.I)
            if m:
                target = _norm(m.group(1))
                b["openThreads"] = [o for o in b["openThreads"]
                                    if _norm(o["text"]) != target]
            self._upsert(b["resolvedItems"], r, resolvedAt=_utcnow(),
                         expiresAt=(datetime.now(timezone.utc)
                                    + self.resolved_ttl).isoformat(),
                         source=source)
        for f in extracted["facts"]:
            self._upsert(b["facts"], f, source=source)
        if not b["displayName"] and extracted.get("displayName"):
            b["displayName"] = extracted["displayName"]
        b["updatedAt"] = _utcnow()
        b["lastCallAt"] = _utcnow()

    # -- write path -------------------------------------------------------
    def record_call(self, caller_id: str, transcript,
                    *, display_name: str = "",
                    call_id: str = "",
                    metadata: dict | None = None) -> dict | None:
        """Extract a transcript into the caller's brief. Returns the brief."""
        if not self.save_enabled:
            return self.brief(caller_id)
        cid = self._id(caller_id)
        clean = self._scrub(transcript if isinstance(transcript, str)
                            else self._turns_text(transcript))
        extracted = extract_mod.extract_with_hook(
            clean, self.extractor, source=call_id or "call",
            scrub=self._scrub)
        b = self._get_brief(cid)
        source = call_id or "call-%d" % (b["callCount"] + 1)
        self._merge(b, extracted, source)
        if display_name:
            b["displayName"] = display_name
        b["callCount"] += 1
        b["summary"] = brief_mod.summarize(b)
        brief_mod.prune_expired(b)
        self.store.set_caller(cid, b)
        return self.brief(cid)

    @staticmethod
    def _turns_text(transcript) -> str:
        turns = extract_mod.normalize_turns(transcript)
        return "\n".join(f"{t['role']}: {t['text']}" for t in turns)

    def backfill(self, caller_id: str, transcripts: list,
                 **kwargs) -> dict | None:
        """Build a brief from old transcripts. Same pipeline as record_call."""
        brief = None
        for i, t in enumerate(transcripts):
            text = t["transcript"] if isinstance(t, dict) and "transcript" in t else t
            brief = self.record_call(caller_id, text,
                                     **{"call_id": f"backfill-{i}", **kwargs})
        return brief or self.brief(caller_id)

    def merge_callers(self, from_id: str, into_id: str) -> dict | None:
        """Merge one caller's brief into another's (e.g. two numbers, one
        person). Lists dedupe by normalized text keeping the earliest
        addedAt; call counts add up; the source brief is deleted."""
        src_id, dst_id = self._id(from_id), self._id(into_id)
        if src_id == dst_id:
            return self.brief(dst_id)
        src = self.store.get_caller(src_id)
        if src is None:
            return self.brief(dst_id)
        src = brief_mod.normalize_brief(src, src_id)
        dst = self._get_brief(dst_id)

        def merge_list(key: str):
            for e in src[key]:
                n = _norm(e["text"])
                ex = next((x for x in dst[key] if _norm(x["text"]) == n), None)
                if ex:
                    if e["addedAt"] < (ex.get("addedAt") or ""):
                        ex["addedAt"] = e["addedAt"]
                    if (e.get("lastMentionedAt") or "") > (ex.get("lastMentionedAt") or ""):
                        ex["lastMentionedAt"] = e["lastMentionedAt"]
                else:
                    dst[key].append(dict(e))

        for k in ("preferences", "openThreads", "resolvedItems", "facts"):
            merge_list(k)
        dst["callCount"] = (dst["callCount"] or 0) + (src["callCount"] or 0)
        if not dst["displayName"] and src["displayName"]:
            dst["displayName"] = src["displayName"]
        if (src["createdAt"] or "") < (dst["createdAt"] or ""):
            dst["createdAt"] = src["createdAt"]
        if (src["updatedAt"] or "") > (dst["updatedAt"] or ""):
            dst["updatedAt"] = src["updatedAt"]
        brief_mod.prune_expired(dst)
        self.store.set_caller(dst_id, dst)
        self.store.delete_caller(src_id)
        return self.brief(dst_id)

    def resolve_thread(self, caller_id: str, thread_text: str) -> dict | None:
        """Manually close an open thread (normalized-text or substring match).
        Files it as a resolved item with the standard TTL."""
        cid = self._id(caller_id)
        b = self.store.get_caller(cid)
        if b is None:
            return None
        b = brief_mod.normalize_brief(b, cid)
        needle = _norm(thread_text or "")
        if not needle:
            return None
        idx = next(
            (i for i, t in enumerate(b["openThreads"])
             if _norm(t["text"]) == needle
             or needle in _norm(t["text"])
             or _norm(t["text"]) in needle),
            None,
        )
        if idx is None:
            return None
        closed = b["openThreads"].pop(idx)
        entry = self._upsert(
            b["resolvedItems"], "Resolved: " + closed["text"],
            resolvedAt=_utcnow(),
            expiresAt=(datetime.now(timezone.utc) + self.resolved_ttl).isoformat(),
            source="manual",
        )
        b["updatedAt"] = _utcnow()
        brief_mod.prune_expired(b)
        self.store.set_caller(cid, b)
        return brief_mod.copy_brief(entry)

    # -- read path ----------------------------------------------------------
    def brief(self, caller_id: str) -> dict | None:
        """Read-only view of a caller's brief (prunes expired resolved items)."""
        if not self.recall_enabled:
            return None
        raw = self.store.get_caller(self._id(caller_id))
        if raw is None:
            return None
        b = brief_mod.normalize_brief(raw, self._id(caller_id))
        brief_mod.prune_expired(b)
        return brief_mod.copy_brief(b)

    def build_injection(self, caller_id: str, max_chars: int = 1200) -> str:
        """Session-start injection block. "" for unknown callers (safe no-op)."""
        if not self.recall_enabled:
            return ""
        b = self.brief(caller_id)
        if not b or not b["callCount"]:
            return ""
        who = f" ({b['displayName']})" if b["displayName"] else ""
        n = b["callCount"]
        lines = [f"[Caller memory — {n} prior call{'s' if n != 1 else ''}{who}]"]
        if b["preferences"]:
            lines.append("Preferences: " + "; ".join(p["text"] for p in b["preferences"]) + ".")
        if b["openThreads"]:
            lines.append("Open threads: " + "; ".join(t["text"] for t in b["openThreads"]) + ".")
        if b["resolvedItems"]:
            lines.append("Recently resolved: " + "; ".join(r["text"] for r in b["resolvedItems"]) + ".")
        if b["facts"]:
            lines.append("Facts: " + "; ".join(f["text"] for f in b["facts"]) + ".")
        out = "\n".join(lines)
        return out if len(out) <= max_chars else out[: max_chars - 1] + "…"

    # Back-compat alias for the pre-merge callrecall-ai API.
    def context_block(self, caller_id: str, max_chars: int = 1200) -> str:
        return self.build_injection(caller_id, max_chars=max_chars)

    def plan_flow(self, caller_id: str, steps: list[dict]) -> list[dict]:
        """Mark flow steps skippable when memory already covers them.

        steps: [{"id", "label", "keywords": [...]}]. A step is skippable when
        every word of one of its keywords appears in the brief's
        preferences/facts/resolved items. Open threads never make a step
        skippable — they need follow-up, not skipping.
        """
        b = self.brief(caller_id)
        hay = set()
        if b:
            for e in b["preferences"] + b["facts"] + b["resolvedItems"]:
                hay.update(w for w in _WORD.findall(_norm(e["text"])) if w)

        def covered(keyword: str) -> bool:
            words = [w for w in _WORD.findall(_norm(keyword)) if w]
            return bool(words) and all(w in hay for w in words)

        planned = []
        for s in steps or []:
            hit = next((k for k in (s.get("keywords") or []) if k and covered(k)), None)
            planned.append({**s, "skippable": bool(hit),
                            "skip_reason": f'Memory covers "{hit}"' if hit else None})
        return planned

    def skippable(self, caller_id: str, steps: list[str],
                  threshold: float = 0.3) -> list[str]:
        """Which plain-description flow steps does memory already cover?
        Topical-overlap matching (synonym-aware); open threads included."""
        b = self.brief(caller_id)
        if not b:
            return []
        known = [i["text"] for i in brief_mod.all_items(b)]
        return [s for s in steps
                if any(_overlap(s, k) >= threshold for k in known)]

    def search(self, query: str, limit: int = 5) -> list[tuple[str, str]]:
        """Keyword search across all briefs. Returns [(caller_id, snippet)]."""
        if not self.recall_enabled:
            return []
        hits = []
        for cid in self.store.list_callers():
            b = self.brief(cid)
            if not b:
                continue
            for item in brief_mod.all_items(b):
                score = _overlap(query, item["text"])
                if score > 0:
                    hits.append((score, cid, item["text"]))
        hits.sort(key=lambda h: h[0], reverse=True)
        return [(cid, text) for _, cid, text in hits[:limit]]

    def stale_threads(self, caller_id: str, days: int | None = None) -> list[dict]:
        """Open threads not mentioned in ``days`` days (default: the
        ``stale_thread_days`` constructor option). "Never got back" nudges."""
        cid = self._id(caller_id)
        raw = self.store.get_caller(cid)
        if raw is None:
            return []
        b = brief_mod.normalize_brief(raw, cid)
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=self.stale_thread_days if days is None else days))

        def mentioned_at(t: dict) -> datetime:
            for key in ("lastMentionedAt", "addedAt"):
                try:
                    dt = datetime.fromisoformat(t.get(key) or "")
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    continue
            return datetime(1970, 1, 1, tzinfo=timezone.utc)

        return [brief_mod.copy_brief(t) for t in b["openThreads"]
                if mentioned_at(t) < cutoff]

    def export_markdown(self, caller_id: str) -> str | None:
        """Render a brief as Markdown (review / audit)."""
        b = self.brief(caller_id)
        if not b:
            return None

        def sec(title: str, items: list[dict]) -> str:
            if not items:
                return ""
            return "## %s\n%s\n" % (title, "\n".join("- " + i["text"] for i in items))

        return (
            "# Caller brief: %s\nCalls: %d · Updated: %s\n\n%s%s%s%s"
            % (b["displayName"] or b["callerId"], b["callCount"], b["updatedAt"],
               sec("Preferences", b["preferences"]),
               sec("Open threads", b["openThreads"]),
               sec("Recently resolved", b["resolvedItems"]),
               sec("Facts", b["facts"]))
        )

    def list_callers(self) -> list[str]:
        return self.store.list_callers()

    def delete_caller(self, caller_id: str) -> bool:
        return self.store.delete_caller(self._id(caller_id))

    # Back-compat alias for the pre-merge callrecall-ai API.
    def forget(self, caller_id: str) -> bool:
        return self.delete_caller(caller_id)


# Back-compat alias for the pre-merge callrecall-ai API.
CallRecallMemory = VoiceMemory
