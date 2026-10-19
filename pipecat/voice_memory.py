"""
Voice Memory — Pipecat pipeline adapter (stdlib only).

Thin glue between a Pipecat pipeline and the voice-memory store concept.
The canonical store logic lives in the Node package; this adapter mirrors the
same brief lifecycle for Python pipelines:

    from voice_memory import VoiceMemoryPipeline

    vm = VoiceMemoryPipeline(
        store_path="voice-memory.json",
        caller_id_fn=lambda: "+15551234567",   # however you identify callers
    )

    # on pipeline start (before the LLM runs):
    system_prompt += vm.session_start()

    # on pipeline end, with the full transcript text:
    vm.session_end(transcript_text)

    # before a scripted flow:
    steps = vm.plan_flow([
        {"id": "verify", "label": "Verify identity",
         "keywords": ["identity verified"]},
    ])
    for s in steps:
        if s["skippable"]:
            print("skip:", s["id"], "-", s["skip_reason"])

PII redaction is ON by default (emails, phones, SSNs, cards, account numbers).
Storage is a single JSON file; writes are atomic (temp file + rename).
"""

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

STORE_VERSION = 1
DEFAULT_TTL_DAYS = 30

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b")
_ACCOUNT_RE = re.compile(
    r"\b(?:account|acct|policy|member)(?:\s*(?:number|no|#))?\s*[:#]?\s*[A-Za-z0-9-]{4,}\b",
    re.IGNORECASE,
)
_CARD_RE = re.compile(r"\b\d(?:[\s-]*\d){12,18}\b")


def _luhn_ok(digits: str) -> bool:
    total, dbl = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if dbl:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        dbl = not dbl
    return total % 10 == 0


def redact(text: str, enabled: bool = True) -> str:
    """Scrub PII from transcript text. Names are intentionally kept."""
    if not enabled or not isinstance(text, str):
        return text

    def _card(m):
        digits = re.sub(r"[\s-]", "", m.group(0))
        return "[card]" if _luhn_ok(digits) else m.group(0)

    def _account(m):
        label = re.match(r"(account|acct|policy|member)", m.group(0), re.IGNORECASE)
        return (label.group(1) if label else "account") + " [account]"

    text = _CARD_RE.sub(_card, text)
    text = _EMAIL_RE.sub("[email]", text)
    text = _SSN_RE.sub("[ssn]", text)
    text = _PHONE_RE.sub("[phone]", text)
    text = _ACCOUNT_RE.sub(_account, text)
    return text


# Mirror the Node extractor: preference polarity is decided by negation words
# in the whole sentence ("hate", "dislike", "don't", "never" -> Avoid).
_NEG_RE = re.compile(r"hate|dislike|don'?t|never", re.I)
_PREF_RES = [
    re.compile(r"\bi prefer ([^.?!;]+)", re.I),
    re.compile(r"\bmy favo[u]?rite ([^.?!;]+)", re.I),
    re.compile(r"\bi like ([^.?!;]+)", re.I),
    re.compile(r"\bi love ([^.?!;]+)", re.I),
    re.compile(r"\bi (?:hate|dislike) ([^.?!;]+)", re.I),
    re.compile(r"\bdon'?t call me ([^.?!;]+)", re.I),
    re.compile(r"\bcall me ([^.?!;]+)", re.I),
    re.compile(r"\bplease (?:always|never) ([^.?!;]+)", re.I),
    re.compile(r"\bremind me to ([^.?!;]+)", re.I),
]
_THREAD_RES = [
    re.compile(r"\bi'?ll follow up (?:on|about) ([^.?!;]+)", re.I),
    re.compile(r"\blet me check (?:on )?([^.?!;]+)", re.I),
    re.compile(r"\bstill waiting (?:on|for) ([^.?!;]+)", re.I),
    re.compile(r"\bwaiting on ([^.?!;]+)", re.I),
    re.compile(r"\btodo:?\s+([^.?!;]+)", re.I),
]
_RESOLVED_RES = [
    re.compile(r"\bthat'?s resolved", re.I),
    re.compile(r"\ball set\b", re.I),
    re.compile(r"\bproblem solved", re.I),
    re.compile(r"\bthanks,? that'?s (?:all|everything)", re.I),
]
_FACT_RES = [
    re.compile(r"\bmy account ([^.?!;]+)", re.I),
    re.compile(r"\bi'?m on the ([^.?!;]+?) plan", re.I),
    re.compile(r"\bmy timezone is ([^.?!;]+)", re.I),
    # Mirrors the Node extractor: "my name is" is excluded — displayName covers it.
    re.compile(r"\bmy (?:identity|email|phone) is ([^.?!;]+)", re.I),
]
_NAME_RES = [
    re.compile(r"\bmy name is ([A-Z][a-z]+(?: [A-Z][a-z]+)?)"),
    re.compile(r"\bcall me ([A-Z][a-z]+)"),
]


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n+", text) if s.strip()]


def _clean(s):
    return re.sub(r"\s+", " ", s).strip().rstrip(".,;!?")


def _extract(transcript):
    text = str(transcript or "")
    preferences, threads, facts = [], [], []
    for s in _sentences(text):
        for rx in _PREF_RES:
            m = rx.search(s)
            if m and len(_clean(m.group(1))) > 2:
                prefix = "Avoid: " if _NEG_RE.search(s) else "Prefers: "
                preferences.append(prefix + _clean(m.group(1)))
                break
        for rx in _THREAD_RES:
            m = rx.search(s)
            if m and len(_clean(m.group(1))) > 2:
                threads.append(_clean(m.group(1)))
                break
        for rx in _FACT_RES:
            m = rx.search(s)
            if m and len(_clean(m.group(1))) > 1:
                facts.append(_clean(s))
                break
    resolved = ["Marked resolved in call"] if any(rx.search(text) for rx in _RESOLVED_RES) else []
    name = None
    for rx in _NAME_RES:
        m = rx.search(text)
        if m:
            name = _clean(m.group(1))
            break
    return preferences, threads, resolved, facts, name


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def normalize_caller_id(caller_id):
    """Collapse phone-like IDs to bare digits ("+1 (555) 123-4567" ==
    "15551234567" == "5551234567"); anything else is trimmed+lowercased."""
    raw = str(caller_id or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 7:
        if len(digits) == 11 and digits[0] == "1":
            return digits[1:]
        return digits
    return raw.lower()


def _blank_brief(caller_id):
    return {
        "callerId": caller_id,
        "displayName": None,
        "createdAt": _utcnow(),
        "updatedAt": _utcnow(),
        "callCount": 0,
        "preferences": [],
        "openThreads": [],
        "resolvedItems": [],
        "facts": [],
    }


class VoiceMemoryPipeline:
    """Pipecat-side adapter for the voice-memory brief lifecycle."""

    def __init__(self, store_path="voice-memory.json", caller_id_fn=None,
                 redact_pii=True, resolved_ttl_days=DEFAULT_TTL_DAYS):
        self.store_path = os.path.abspath(store_path)
        self.caller_id_fn = caller_id_fn or (lambda: "unknown")
        self.redact_pii = redact_pii
        self.ttl = timedelta(days=resolved_ttl_days)
        self._data = self._load()

    # -- store -----------------------------------------------------------
    def _load(self):
        try:
            with open(self.store_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "callers" in data:
                return data
        except (OSError, ValueError):
            pass
        return {"version": STORE_VERSION, "callers": {}}

    def _save(self):
        self._data["version"] = STORE_VERSION
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.store_path) or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self.store_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _brief(self, caller_id):
        caller_id = normalize_caller_id(caller_id)
        b = self._data["callers"].get(caller_id)
        if b is None:
            b = _blank_brief(caller_id)
            self._data["callers"][caller_id] = b
        self._prune(b)
        return b

    def _prune(self, brief):
        now = datetime.now(timezone.utc)
        brief["resolvedItems"] = [
            r for r in brief["resolvedItems"]
            if not r.get("expiresAt")
            or datetime.fromisoformat(r["expiresAt"]) > now
        ]

    @staticmethod
    def _norm(s):
        return re.sub(r"\s+", " ", str(s).lower()).strip()

    def _upsert(self, items, text, **extra):
        n = self._norm(text)
        for e in items:
            if self._norm(e["text"]) == n:
                e["lastMentionedAt"] = _utcnow()
                return e
        entry = {"text": text, "addedAt": _utcnow(),
                 "lastMentionedAt": _utcnow(), **extra}
        items.append(entry)
        return entry

    # -- lifecycle -------------------------------------------------------
    def session_end(self, transcript):
        """Call when the pipeline ends. Extracts, redacts, persists."""
        caller_id = normalize_caller_id(self.caller_id_fn())
        clean = redact(transcript or "", enabled=self.redact_pii)
        prefs, threads, resolved, facts, name = _extract(clean)
        b = self._brief(caller_id)
        source = "call-%d" % (b["callCount"] + 1)
        for p in prefs:
            self._upsert(b["preferences"], p, source=source)
        for t in threads:
            self._upsert(b["openThreads"], t, status="open", source=source)
        for r in resolved:
            self._upsert(
                b["resolvedItems"], r, resolvedAt=_utcnow(),
                expiresAt=(datetime.now(timezone.utc) + self.ttl).isoformat(),
                source=source,
            )
        for f in facts:
            self._upsert(b["facts"], f, source=source)
        if not b.get("displayName") and name:
            b["displayName"] = name
        b["callCount"] += 1
        b["updatedAt"] = _utcnow()
        self._save()
        return self.get_brief(caller_id)

    def get_brief(self, caller_id=None):
        caller_id = normalize_caller_id(caller_id or self.caller_id_fn())
        b = self._data["callers"].get(caller_id)
        if not b:
            return None
        self._prune(b)
        return json.loads(json.dumps(b))

    def resolve_thread(self, thread_text, caller_id=None):
        """Manually close an open thread (normalized-text or substring match).
        Returns the closed thread entry, or None when nothing matched."""
        caller_id = normalize_caller_id(caller_id or self.caller_id_fn())
        b = self._data["callers"].get(caller_id)
        if not b:
            return None
        needle = self._norm(thread_text or "")
        if not needle:
            return None
        idx = next(
            (i for i, t in enumerate(b["openThreads"])
             if self._norm(t["text"]) == needle
             or needle in self._norm(t["text"])
             or self._norm(t["text"]) in needle),
            None,
        )
        if idx is None:
            return None
        closed = b["openThreads"].pop(idx)
        entry = self._upsert(
            b["resolvedItems"], "Resolved: " + closed["text"],
            resolvedAt=_utcnow(),
            expiresAt=(datetime.now(timezone.utc) + self.ttl).isoformat(),
            source="manual",
        )
        b["updatedAt"] = _utcnow()
        self._save()
        return json.loads(json.dumps(entry))

    def session_start(self, max_chars=1200):
        """Injection block for the system prompt ("" when caller is new)."""
        b = self.get_brief()
        if not b or not b["callCount"]:
            return ""
        who = " (%s)" % b["displayName"] if b.get("displayName") else ""
        n = b["callCount"]
        lines = ["[Caller memory — %d prior call%s%s]" % (n, "" if n == 1 else "s", who)]
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

    def plan_flow(self, steps):
        """Mark steps skippable when the brief already covers them."""
        b = self.get_brief()
        hay_words = set()
        if b:
            for e in b["preferences"] + b["facts"] + b["resolvedItems"]:
                hay_words.update(w for w in re.split(r"[^a-z0-9]+", self._norm(e["text"])) if w)

        def covered(keyword):
            words = [w for w in re.split(r"[^a-z0-9]+", self._norm(keyword)) if w]
            return bool(words) and all(w in hay_words for w in words)

        planned = []
        for s in steps or []:
            hit = next((k for k in s.get("keywords", []) if k and covered(k)), None)
            planned.append({**s, "skippable": bool(hit),
                            "skip_reason": ('Memory covers "%s"' % hit) if hit else None})
        return planned

    def backfill(self, transcripts):
        """Replay old transcripts: transcripts = [{"transcript": str, ...}]."""
        brief = None
        for t in transcripts or []:
            brief = self.session_end(t.get("transcript", ""))
        return brief or self.get_brief()
