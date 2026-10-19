"""Deterministic, heuristic transcript extraction — no LLM, no network.

This is the Python port of the JS extractor (src/extract.js); both SDKs
implement the same pattern tables so behavior is identical across languages.
See SPEC.md section 3.

Returns: {"preferences", "openThreads", "resolvedItems", "facts", "displayName"}.
Items are plain strings; the memory layer adds timestamps/sources.

An optional LLM extractor can be layered on top via ``extract_with_hook``:
any callable ``fn({"transcript": str, "turns": [...]})`` returning string
lists. It can never break a call — exceptions are caught and the rule-based
result stands.
"""
from __future__ import annotations

import re
from typing import Callable

# -- preference patterns (whole-sentence negation decides Avoid:/Prefers:) --
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
    re.compile(r"\bpending ([^.?!;]+)", re.I),
    re.compile(r"\bstill waiting (?:on|for) ([^.?!;]+)", re.I),
    re.compile(r"\bwe'?ll get back to you (?:on|about) ([^.?!;]+)", re.I),
    re.compile(r"\btodo:?\s+([^.?!;]+)", re.I),
    re.compile(r"\bwaiting on ([^.?!;]+)", re.I),
]

_RESOLVED_RES = [
    re.compile(r"\bthat(?:'?s| is) resolved", re.I),
    re.compile(r"\ball set\b", re.I),
    re.compile(r"\bthat answers (?:it|my question)", re.I),
    re.compile(r"\bproblem solved", re.I),
    re.compile(r"\bthanks,? that'?s (?:all|everything)", re.I),
    re.compile(r"\bissue (?:is |has been )?resolved", re.I),
    re.compile(r"\bwe'?re good (?:now|here)", re.I),
]

# Caller asks a question the agent never answers -> open thread.
# Applied to caller-only text, so no speaker prefix is needed.
_QUESTION_RE = re.compile(r"(?:^|[.?!]\s+)([^.?!]{8,}\?)")

_FACT_RES = [
    re.compile(r"\bmy account ([^.?!;]+)", re.I),
    re.compile(r"\bi'?m on the ([^.?!;]+?) plan", re.I),
    re.compile(r"\bmy timezone is ([^.?!;]+)", re.I),
    re.compile(r"\bi live in ([^.?!;]+)", re.I),
    # "my name is X" is excluded — captured as displayName instead.
    re.compile(r"\bmy (?:identity|email|phone) is ([^.?!;]+)", re.I),
]

_NAME_RES = [
    re.compile(r"\bmy name is ([A-Z][a-z]+(?: [A-Z][a-z]+)?)"),
    re.compile(r"\bthis is ([A-Z][a-z]+(?: [A-Z][a-z]+)?) (?:calling|speaking)"),
    re.compile(r"\bcall me ([A-Z][a-z]+)"),
]

# Caller-reported problems: "my package never arrived" -> issue facts.
_ISSUE_RES = [
    re.compile(
        r"\bmy ([\w][\w\s]*?) "
        r"(never arrived|hasn'?t arrived|is broken|isn'?t working|doesn'?t work|"
        r"was lost|is missing|died|stopped working)\b",
        re.I,
    ),
]

_LABELED_RE = re.compile(r"^\s*(caller|agent)\s*:", re.I | re.M)
_CALLER_LINE_RE = re.compile(r"^\s*caller\s*:\s*(.+)$", re.I)
_AGENT_LINE_RE = re.compile(r"^\s*agent\s*:\s*(.+)$", re.I)

_USER_ROLES = {"user", "caller", "customer", "human"}
_AGENT_ROLES = {"assistant", "agent", "system", "bot"}


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n+", text) if s.strip()]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().rstrip(".,;!?")


def normalize_turns(transcript) -> list[dict]:
    """Normalize a transcript to [{"role": "caller"|"agent", "text": ...}].

    Accepts a list of {"role","text"} dicts (role aliases user/caller/...,
    assistant/agent/...) or plain text with ``Caller:``/``Agent:`` labels.
    Unlabeled text is treated as caller speech.
    """
    if isinstance(transcript, str):
        if _LABELED_RE.search(transcript):
            turns: list[dict] = []
            for line in transcript.splitlines():
                m = _CALLER_LINE_RE.match(line)
                if m:
                    turns.append({"role": "caller", "text": m.group(1).strip()})
                    continue
                m = _AGENT_LINE_RE.match(line)
                if m:
                    turns.append({"role": "agent", "text": m.group(1).strip()})
                    continue
                if turns:
                    turns[-1]["text"] += " " + line.strip()
            return turns
        return [{"role": "caller", "text": transcript}] if transcript.strip() else []
    out = []
    for t in transcript or []:
        role = str(t.get("role", "")).strip().lower()
        if role in _USER_ROLES:
            role = "caller"
        elif role in _AGENT_ROLES:
            role = "agent"
        out.append({"role": role, "text": str(t.get("text", ""))})
    return out


def _extract_preferences(caller_text: str) -> list[str]:
    out = []
    for s in _sentences(caller_text):
        for rx in _PREF_RES:
            m = rx.search(s)
            if m and len(_clean(m.group(1))) > 2:
                prefix = "Avoid: " if _NEG_RE.search(s) else "Prefers: "
                out.append(prefix + _clean(m.group(1)))
                break
    return out


def _extract_open_threads(caller_text: str, labeled: bool) -> list[str]:
    out = []
    for s in _sentences(caller_text):
        for rx in _THREAD_RES:
            m = rx.search(s)
            if m and len(_clean(m.group(1))) > 2:
                out.append(_clean(m.group(1)))
                break
    # Unanswered caller questions become open threads — only when we know
    # the text is caller-only (labeled transcripts); otherwise agent
    # questions would pollute the threads.
    if labeled:
        for m in _QUESTION_RE.finditer(caller_text):
            q = _clean(m.group(1))
            if len(q) > 8:
                out.append("Caller asked: " + q)
    return out


def _extract_resolved(text: str, open_threads: list[str]) -> tuple[list[str], list[str]]:
    resolution_sentences = [
        s for s in _sentences(text) if any(rx.search(s) for rx in _RESOLVED_RES)
    ]
    generic = ["Marked resolved in call"] if resolution_sentences else []
    # A known open thread is only "resolved" when a resolution sentence
    # specifically mentions it — not merely because something else resolved.
    matched = []
    for t in open_threads:
        key = " ".join(
            re.sub(r"^caller asked:\s*", "", t, flags=re.I).split()[:4]
        ).lower()
        if len(key) > 6 and any(key in s.lower() for s in resolution_sentences):
            matched.append("Resolved: " + t)
    return generic, matched


def _extract_facts(caller_text: str) -> list[str]:
    out = []
    for s in _sentences(caller_text):
        for rx in _FACT_RES:
            m = rx.search(s)
            if m and len(_clean(m.group(1))) > 1:
                out.append(_clean(s))
                break
    return out


def _extract_issues(caller_text: str) -> list[str]:
    out = []
    for s in _sentences(caller_text):
        m = _ISSUE_RES[0].search(s)
        if m:
            out.append("issue: " + _clean(m.group(1)) + " " + _clean(m.group(2)))
    return out


def _extract_name(text: str) -> str | None:
    for rx in _NAME_RES:
        m = rx.search(text)
        if m:
            return _clean(m.group(1))
    return None


def extract_rule_based(transcript) -> dict:
    """Deterministic extraction over a string or list-of-turns transcript."""
    text = transcript if isinstance(transcript, str) else "\n".join(
        f"{t['role']}: {t['text']}" for t in normalize_turns(transcript)
    )
    text = str(text or "")
    labeled = bool(_LABELED_RE.search(text))
    if labeled:
        caller_lines = [
            m.group(1) for line in text.splitlines()
            for m in [_CALLER_LINE_RE.match(line)] if m
        ]
        caller_text = "\n".join(caller_lines)
    else:
        caller_text = text
    preferences = _extract_preferences(caller_text)
    open_threads = _extract_open_threads(caller_text, labeled)
    facts = _extract_facts(caller_text) + _extract_issues(caller_text)
    generic, matched = _extract_resolved(text, open_threads)
    return {
        "preferences": preferences,
        "openThreads": open_threads,
        "resolvedItems": [*generic, *matched],
        "facts": facts,
        "displayName": _extract_name(text),
    }


ExtractorFn = Callable[[dict], dict]
"""Optional LLM extractor: fn({"transcript": str, "turns": [...]}) ->
{"preferences": [...], "facts": [...], "openThreads": [...],
"resolvedItems": [...]} (plain strings; snake_case keys also accepted)."""


def extract_with_hook(transcript, extractor: ExtractorFn | None = None,
                      source: str = "", scrub=None) -> dict:
    """Rule-based extraction merged with an optional LLM extractor's strings.

    ``scrub`` (fn str->str) is applied to hook-contributed strings so an
    extractor can never smuggle raw PII into the brief. An extractor that
    raises is ignored — it must never break the call.
    """
    result = extract_rule_based(transcript)
    if extractor is None:
        return result
    turns = normalize_turns(transcript)
    text = transcript if isinstance(transcript, str) else "\n".join(
        f"{t['role']}: {t['text']}" for t in turns
    )
    try:
        extra = extractor({"transcript": text, "turns": turns}) or {}
    except Exception:
        return result
    if not isinstance(extra, dict):
        return result

    def pick(*keys):
        for k in keys:
            v = extra.get(k)
            if isinstance(v, list):
                return v
        return []

    def merge(bucket: list, strs: list):
        seen = {s.lower() for s in bucket}
        for s in strs:
            t = str(s or "").strip()
            if not t or t.lower() in seen:
                continue
            seen.add(t.lower())
            bucket.append(scrub(t) if scrub else t)

    merge(result["preferences"], pick("preferences"))
    merge(result["facts"], pick("facts"))
    merge(result["openThreads"], pick("openThreads", "open_threads"))
    merge(result["resolvedItems"], pick("resolvedItems", "resolved"))
    if not result["displayName"]:
        name = extra.get("displayName") or extra.get("display_name")
        if name:
            result["displayName"] = scrub(str(name).strip()) if scrub else str(name).strip()
    return result
