"""The caller brief: the unit of memory. Dict-based, schema version 2.

One JSON object per caller (SPEC.md section 2):

    {
      "callerId": "+15551234567",
      "displayName": "Maya",            # best-known name, optional
      "createdAt": "2026-10-01T…Z",
      "updatedAt": "2026-10-12T…Z",
      "lastCallAt": "2026-10-12T…Z",    # optional
      "callCount": 3,
      "summary": "Prefers: … | Open: …", # optional, refreshed on write
      "preferences": [{"text","addedAt","lastMentionedAt","source","tags"}],
      "openThreads": [{"text","addedAt","lastMentionedAt","status":"open",
                       "source","tags"}],
      "resolvedItems": [{"text","addedAt","lastMentionedAt","resolvedAt",
                         "expiresAt","source","tags"}],
      "facts": [{"text","addedAt","lastMentionedAt","source","tags"}],
    }

preferences never auto-expire; resolvedItems auto-expire via ``expiresAt``;
openThreads are re-mentioned across calls and closed when a later transcript
resolves them (moved to resolvedItems).
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def blank_brief(caller_id: str) -> dict:
    now = utcnow_iso()
    return {
        "callerId": caller_id,
        "displayName": None,
        "createdAt": now,
        "updatedAt": now,
        "lastCallAt": now,
        "callCount": 0,
        "summary": "",
        "preferences": [],
        "openThreads": [],
        "resolvedItems": [],
        "facts": [],
    }


_ITEM_KEYS = ("preferences", "openThreads", "resolvedItems", "facts")


def normalize_brief(raw: dict, caller_id: str) -> dict:
    """Coerce a stored dict into the v2 shape (fills missing fields)."""
    b = blank_brief(caller_id)
    if not isinstance(raw, dict):
        return b
    for key in ("displayName", "createdAt", "updatedAt", "lastCallAt",
                "callCount", "summary"):
        if raw.get(key) not in (None, ""):
            b[key] = raw[key]
    b["callCount"] = int(b["callCount"] or 0)
    for key in _ITEM_KEYS:
        items = raw.get(key) or []
        normed = []
        for it in items:
            if not isinstance(it, dict):
                continue
            entry = {
                "text": str(it.get("text", "")),
                "addedAt": str(it.get("addedAt") or it.get("created_at") or b["createdAt"]),
                "lastMentionedAt": str(
                    it.get("lastMentionedAt") or it.get("created_at") or b["createdAt"]),
                "source": str(it.get("source", "")),
                "tags": list(it.get("tags") or []),
            }
            if key == "openThreads":
                entry["status"] = str(it.get("status", "open"))
            if key == "resolvedItems":
                entry["resolvedAt"] = str(
                    it.get("resolvedAt") or it.get("created_at") or b["createdAt"])
                entry["expiresAt"] = str(it.get("expiresAt") or "")
            normed.append(entry)
        b[key] = normed
    return b


def prune_expired(brief: dict) -> int:
    """Drop expired resolved items. Returns the number removed."""
    now = datetime.now(timezone.utc)
    before = len(brief["resolvedItems"])

    def alive(r: dict) -> bool:
        exp = r.get("expiresAt")
        if not exp:
            return True
        try:
            until = datetime.fromisoformat(exp)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            return until > now
        except ValueError:
            return True

    brief["resolvedItems"] = [r for r in brief["resolvedItems"] if alive(r)]
    return before - len(brief["resolvedItems"])


def all_items(brief: dict) -> list[dict]:
    return (brief["preferences"] + brief["facts"]
            + brief["openThreads"] + brief["resolvedItems"])


def summarize(brief: dict) -> str:
    parts = []
    if brief["preferences"]:
        parts.append("Prefers: " + "; ".join(i["text"] for i in brief["preferences"][-3:]))
    if brief["openThreads"]:
        parts.append("Open: " + "; ".join(i["text"] for i in brief["openThreads"][-3:]))
    if brief["resolvedItems"]:
        parts.append("Resolved: " + "; ".join(i["text"] for i in brief["resolvedItems"][-2:]))
    return " | ".join(parts)[:600]


def copy_brief(brief: dict) -> dict:
    return deepcopy(brief)
