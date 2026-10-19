"""Single-file local store. Whole store = {"version": 2, "callers": {...}}.

Writes are atomic (temp file + rename). Legacy stores are upgraded on load:
  - voice-memory v1  ({"version": 1, "callers": {...}}) — used as-is.
  - callrecall-ai    ({"version": 1, "contacts": {id: snake_case brief}}) —
    converted to the v2 shape (see migrate.py for the field mapping).
Corrupt / unparseable files start empty (same policy as the JS SDK).
"""
from __future__ import annotations

import json
import os
import tempfile

STORE_VERSION = 2
DEFAULT_PATH = "./voice-memory.json"


def normalize_caller_id(caller_id) -> str:
    """Collapse phone-like IDs to bare digits ("+1 (555) 123-4567" ==
    "15551234567" == "5551234567"); anything else is trimmed + lowercased.
    (Lives here because it is the store's key function.)"""
    import re
    raw = str(caller_id or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 7:
        if len(digits) == 11 and digits[0] == "1":
            return digits[1:]
        return digits
    return raw.lower()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _upgrade_callrecall_brief(contact_id: str, raw: dict) -> dict:
    """Convert one legacy callrecall-ai brief (snake_case) to the v2 shape."""
    now = _now_iso()
    created = now
    for it in (raw.get("preferences") or []) + (raw.get("facts") or []):
        c = str(it.get("created_at", "") or "")
        if c and (not created or c < created):
            created = c

    def items(key: str, extra: dict | None = None) -> list[dict]:
        out = []
        for it in raw.get(key) or []:
            c = str(it.get("created_at", "") or now)
            entry = {
                "text": str(it.get("text", "")),
                "addedAt": c,
                "lastMentionedAt": c,
                "source": str(it.get("source", "")),
                "tags": list(it.get("tags") or []),
            }
            if extra:
                entry.update(extra)
            out.append(entry)
        return out

    resolved = []
    for it in raw.get("resolved") or []:
        c = str(it.get("created_at", "") or now)
        resolved.append({
            "text": str(it.get("text", "")),
            "addedAt": c,
            "lastMentionedAt": c,
            "resolvedAt": c,
            "expiresAt": str(it.get("valid_until", "") or ""),
            "source": str(it.get("source", "")),
            "tags": list(it.get("tags") or []),
        })

    return {
        "callerId": contact_id,
        "displayName": raw.get("display_name") or None,
        "createdAt": created,
        "updatedAt": str(raw.get("last_call_at") or now),
        "lastCallAt": str(raw.get("last_call_at") or now),
        "callCount": int(raw.get("call_count", 0) or 0),
        "summary": str(raw.get("summary", "") or ""),
        "preferences": items("preferences"),
        "openThreads": items("open_threads", {"status": "open"}),
        "resolvedItems": resolved,
        "facts": items("facts"),
    }


def upgrade_legacy_store(parsed: dict) -> dict:
    """Upgrade any legacy store dict to the v2 shape.

    Legacy callrecall-ai keys were never normalized; they are normalized on
    the way in, and briefs whose keys collide after normalization are merged.
    """
    data = {"version": STORE_VERSION, "callers": {}}
    if isinstance(parsed.get("callers"), dict):
        data["callers"] = parsed["callers"]
    elif isinstance(parsed.get("contacts"), dict):
        for cid, raw in parsed["contacts"].items():
            if not isinstance(raw, dict):
                continue
            nid = normalize_caller_id(cid)
            nb = _upgrade_callrecall_brief(nid, raw)
            if nid in data["callers"]:
                data["callers"][nid] = _merge_upgraded(data["callers"][nid], nb)
            else:
                data["callers"][nid] = nb
    return data


def _merge_upgraded(a: dict, b: dict) -> dict:
    """Merge two upgraded briefs whose legacy keys normalized to one id."""
    def cat(x: list, y: list) -> list:
        seen = {str(e.get("text", "")).lower() for e in x or []}
        out = list(x or [])
        for e in y or []:
            if str(e.get("text", "")).lower() not in seen:
                seen.add(str(e.get("text", "")).lower())
                out.append(e)
        return out

    return {
        "callerId": a["callerId"],
        "displayName": a.get("displayName") or b.get("displayName"),
        "createdAt": min(a.get("createdAt") or "", b.get("createdAt") or ""),
        "updatedAt": max(a.get("updatedAt") or "", b.get("updatedAt") or ""),
        "lastCallAt": max(a.get("lastCallAt") or "", b.get("lastCallAt") or ""),
        "callCount": (a.get("callCount") or 0) + (b.get("callCount") or 0),
        "summary": a.get("summary") or b.get("summary"),
        "preferences": cat(a.get("preferences"), b.get("preferences")),
        "openThreads": cat(a.get("openThreads"), b.get("openThreads")),
        "resolvedItems": cat(a.get("resolvedItems"), b.get("resolvedItems")),
        "facts": cat(a.get("facts"), b.get("facts")),
    }


def blank_store() -> dict:
    return {"version": STORE_VERSION, "callers": {}}


class JsonFileStore:
    """Atomic single-file JSON store keyed by normalized caller id."""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                parsed = json.load(f)
            if isinstance(parsed, dict) and (parsed.get("callers") or parsed.get("contacts")):
                return upgrade_legacy_store(parsed)
        except (OSError, ValueError):
            pass
        return blank_store()

    def save(self) -> None:
        self.data["version"] = STORE_VERSION
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".voice-memory-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- caller level ----------------------------------------------------
    def get_caller(self, caller_id: str) -> dict | None:
        return self.data["callers"].get(caller_id)

    def set_caller(self, caller_id: str, brief: dict) -> None:
        self.data["callers"][caller_id] = brief
        self.save()

    def delete_caller(self, caller_id: str) -> bool:
        existed = caller_id in self.data["callers"]
        if existed:
            del self.data["callers"][caller_id]
            self.save()
        return existed

    def list_callers(self) -> list[str]:
        return sorted(self.data["callers"].keys())
