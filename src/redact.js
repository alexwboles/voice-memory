'use strict';

/**
 * PII redaction — ON by default.
 * Scrubs emails, phone numbers, SSNs, credit cards, and account/policy
 * numbers from transcript text before anything is stored.
 * Names are intentionally NOT redacted: the brief is per-caller.
 */

const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;
const SSN_RE = /\b\d{3}-\d{2}-\d{4}\b/g;
// US-style phones: (555) 123-4567, 555-123-4567, 555.123.4567, +1 555 123 4567
const PHONE_RE = /(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b/g;
const ACCOUNT_RE = /\b(?:account|acct|policy|member)(?:\s*(?:number|no|#))?\s*[:#]?\s*[A-Za-z0-9-]{4,}\b/gi;

function luhnOk(digits) {
  let sum = 0;
  let dbl = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = digits.charCodeAt(i) - 48;
    if (dbl) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
    dbl = !dbl;
  }
  return sum % 10 === 0;
}

// 13–19 digit runs that pass Luhn → credit card numbers.
const CARD_RE = /\b\d(?:[\s-]*\d){12,18}\b/g;

function redactCards(text) {
  return text.replace(CARD_RE, (m) => {
    const digits = m.replace(/[\s-]/g, '');
    return luhnOk(digits) ? '[card]' : m;
  });
}

function redact(text, { enabled = true } = {}) {
  if (!enabled) return text;
  if (typeof text !== 'string') return text;
  return redactCards(text)
    .replace(EMAIL_RE, '[email]')
    .replace(SSN_RE, '[ssn]')
    .replace(PHONE_RE, '[phone]')
    .replace(ACCOUNT_RE, (m) => {
      // Keep the label, drop the number: "account #12345" -> "account [account]"
      const label = m.match(/^(account|acct|policy|member)/i);
      return (label ? label[1] : 'account') + ' [account]';
    });
}

module.exports = { redact, EMAIL_RE, SSN_RE, PHONE_RE, CARD_RE, ACCOUNT_RE };
