"""PII redaction — ON by default.

Unified policy (SPEC.md section 7): emails, phone numbers, SSNs, credit-card
numbers (Luhn-checked), account/policy numbers, and street addresses are
scrubbed before anything is stored. Person names are intentionally NOT
redacted by default — the brief is per-caller and names are the point.
Pass ``names=True`` for the stricter mode (person-name masking), e.g. for
shared / multi-tenant deployments.

Token style is lowercase ([email], [phone], ...) so stores written by the
JS SDK and the Python SDK are interchangeable.
"""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# US-style phones: (555) 123-4567, 555-123-4567, 555.123.4567, +1 555 123 4567.
# A match only counts when it holds 10+ digits (years / fragments survive).
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b")
# 13-19 digit runs; only Luhn-valid ones are cards.
CARD_RE = re.compile(r"\b\d(?:[\s-]*\d){12,18}\b")
ACCOUNT_RE = re.compile(
    r"\b(?:account|acct|policy|member)(?:\s*(?:number|no|#))?\s*[:#]?\s*[A-Za-z0-9-]{4,}\b",
    re.IGNORECASE,
)
# Crude street address: number + words + street suffix.
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+(?:[A-Za-z]+\s+){1,4}"
    r"(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd|court|ct|"
    r"place|pl|terrace|ter|way|circle|cir|parkway|pkwy)\b\.?",
    re.IGNORECASE,
)
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")

# Conservative person-name heuristic (opt-in): 2+ consecutive capitalized words.
# Common false positives (days, months) are allowlisted out.
_NAME_STOPWORDS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "main street", "high street", "first avenue",
}
NAME_PAIR_RE = re.compile(r"\b([A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,})+)\b")


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


def _mask_name(match: re.Match) -> str:
    phrase = match.group(1)
    if phrase.lower() in _NAME_STOPWORDS:
        return phrase
    return "[name]"


def redact_text(text: str, *, enabled: bool = True, names: bool = False) -> str:
    """Scrub PII from free text. ``names=True`` also masks person names."""
    if not enabled or not isinstance(text, str):
        return text

    def _card(m: re.Match) -> str:
        digits = re.sub(r"[\s-]", "", m.group(0))
        return "[card]" if _luhn_ok(digits) else m.group(0)

    def _phone(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[phone]" if len(digits) >= 10 else m.group(0)

    def _account(m: re.Match) -> str:
        label = re.match(r"(account|acct|policy|member)", m.group(0), re.IGNORECASE)
        return (label.group(1) if label else "account") + " [account]"

    out = CARD_RE.sub(_card, text)
    out = EMAIL_RE.sub("[email]", out)
    out = SSN_RE.sub("[ssn]", out)
    out = PHONE_RE.sub(_phone, out)
    out = ACCOUNT_RE.sub(_account, out)
    out = ADDRESS_RE.sub("[address]", out)
    # a zip glued to a redacted address line is part of the address
    out = re.sub(r"\[address\][,\s]*" + ZIP_RE.pattern, "[address]", out)
    if names:
        out = NAME_PAIR_RE.sub(_mask_name, out)
    return out


# Back-compat alias for the pre-merge callrecall-ai API.
def redact(text: str, enabled: bool = True) -> str:
    return redact_text(text, enabled=enabled)
