'use strict';

/**
 * Deterministic, heuristic transcript extraction.
 * No LLM, no network — pattern-based so behavior is testable and offline.
 *
 * Returns: { preferences[], openThreads[], resolvedItems[], facts[], displayName }
 * Each item is a plain string; the memory layer adds timestamps/sources.
 */

const PREFERENCE_RES = [
  /\bi prefer ([^.?!;]+)/i,
  /\bmy favo[u]?rite ([^.?!;]+)/i,
  /\bi like ([^.?!;]+)/i,
  /\bi love ([^.?!;]+)/i,
  /\bi (?:hate|dislike) ([^.?!;]+)/i,
  /\bdon'?t call me ([^.?!;]+)/i,
  /\bcall me ([^.?!;]+)/i,
  /\bplease (?:always|never) ([^.?!;]+)/i,
  /\bremind me to ([^.?!;]+)/i,
];

const OPEN_THREAD_RES = [
  /\bi'?ll follow up (?:on|about) ([^.?!;]+)/i,
  /\blet me check (?:on )?([^.?!;]+)/i,
  /\bpending ([^.?!;]+)/i,
  /\bstill waiting (?:on|for) ([^.?!;]+)/i,
  /\bwe'?ll get back to you (?:on|about) ([^.?!;]+)/i,
  /\btodo:?\s+([^.?!;]+)/i,
  /\bwaiting on ([^.?!;]+)/i,
];

const RESOLVED_RES = [
  /\bthat(?:'?s| is) resolved/i,
  /\ball set\b/i,
  /\bthat answers (?:it|my question)/i,
  /\bproblem solved/i,
  /\bthanks,? that'?s (?:all|everything)/i,
  /\bissue (?:is |has been )?resolved/i,
  /\bwe'?re good (?:now|here)/i,
];

// Caller asks a question that the agent never answers in the transcript.
// Applied to caller-only text, so no speaker prefix is needed.
const QUESTION_RE = /(^|[.?!]\s+)([^.?!]{8,}\?)/g;

const FACT_RES = [
  /\bmy account ([^.?!;]+)/i,
  /\bi'?m on the ([^.?!;]+?) plan/i,
  /\bmy timezone is ([^.?!;]+)/i,
  /\bi live in ([^.?!;]+)/i,
  // Note: "my name is X" is intentionally excluded — the name is captured
  // as displayName instead; storing the intro sentence as a fact is noise.
  /\bmy (?:identity|email|phone) is ([^.?!;]+)/i,
];

const NAME_RES = [
  /\bmy name is ([A-Z][a-z]+(?: [A-Z][a-z]+)?)/,
  /\bthis is ([A-Z][a-z]+(?: [A-Z][a-z]+)?) (?:calling|speaking)/,
  /\bcall me ([A-Z][a-z]+)/,
];

function sentences(text) {
  return text.split(/(?<=[.?!])\s+|\n+/).map((s) => s.trim()).filter(Boolean);
}

function cleanCapture(s) {
  return s.trim().replace(/\s+/g, ' ').replace(/[.,;!?]+$/, '');
}

function extractPreferences(text) {
  const out = [];
  for (const s of sentences(text)) {
    for (const re of PREFERENCE_RES) {
      const m = s.match(re);
      if (m && m[1] && cleanCapture(m[1]).length > 2) {
        const neg = /hate|dislike|don'?t|never/i.test(s) ? 'Avoid: ' : 'Prefers: ';
        out.push(neg + cleanCapture(m[1]));
        break;
      }
    }
  }
  return out;
}

function extractOpenThreads(text, { includeQuestions } = {}) {
  const out = [];
  for (const s of sentences(text)) {
    for (const re of OPEN_THREAD_RES) {
      const m = s.match(re);
      if (m && m[1] && cleanCapture(m[1]).length > 2) {
        out.push(cleanCapture(m[1]));
        break;
      }
    }
  }
  // Unanswered caller questions become open threads — only when we know
  // the text is caller-only (labeled transcripts); otherwise the speaker
  // is ambiguous and agent questions would pollute the threads.
  if (includeQuestions) {
    let qm;
    QUESTION_RE.lastIndex = 0;
    while ((qm = QUESTION_RE.exec(text)) !== null) {
      const q = cleanCapture(qm[2]);
      if (q.length > 8) out.push('Caller asked: ' + q);
    }
  }
  return out;
}

function extractResolved(text, openThreads) {
  const resolved = [];
  const resolutionSentences = sentences(text).filter((s) =>
    RESOLVED_RES.some((re) => re.test(s))
  );
  if (resolutionSentences.length) resolved.push('Marked resolved in call');
  // A known open thread is only "resolved" when a resolution sentence
  // specifically mentions it — not merely because something else resolved.
  const out = [];
  for (const t of openThreads) {
    const key = t
      .replace(/^caller asked:\s*/i, '')
      .split(/\s+/)
      .slice(0, 4)
      .join(' ')
      .toLowerCase();
    if (key.length > 6 && resolutionSentences.some((s) => s.toLowerCase().includes(key))) {
      out.push('Resolved: ' + t);
    }
  }
  return { generic: resolved, matched: out };
}

function extractFacts(text) {
  const out = [];
  for (const s of sentences(text)) {
    for (const re of FACT_RES) {
      const m = s.match(re);
      if (m && m[1] && cleanCapture(m[1]).length > 1) {
        out.push(cleanCapture(s));
        break;
      }
    }
  }
  return out;
}

function extractName(text) {
  for (const re of NAME_RES) {
    const m = text.match(re);
    if (m && m[1]) return cleanCapture(m[1]);
  }
  return null;
}

function extract(transcript) {
  const text = String(transcript || '');
  // Speaker-aware: caller lines drive preferences/threads/facts; either side
  // can signal resolution.
  const callerLines = [];
  const labeled = /(?:^|\n)\s*(caller|agent)\s*:/im.test(text);
  if (labeled) {
    for (const line of text.split('\n')) {
      const m = line.match(/^\s*caller\s*:\s*(.+)$/i);
      if (m) callerLines.push(m[1]);
    }
  }
  const callerText = labeled ? callerLines.join('\n') : text;
  const preferences = extractPreferences(callerText);
  const openThreads = extractOpenThreads(callerText, { includeQuestions: labeled });
  const facts = extractFacts(callerText);
  const { generic, matched } = extractResolved(text, openThreads);
  return {
    preferences,
    openThreads,
    resolvedItems: [...generic, ...matched],
    facts,
    displayName: extractName(text),
  };
}

module.exports = { extract };
