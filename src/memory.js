'use strict';

/**
 * VoiceMemory — middleware between a voice framework and a local file store.
 *
 *   const vm = new VoiceMemory({ storePath: './voice-memory.json' });
 *   vm.recordCall('+15551234567', { transcript });   // call end
 *   vm.buildInjection('+15551234567');               // next call start
 *   vm.planFlow('+15551234567', steps);              // skippable steps
 *   vm.backfill('+15551234567', [{ transcript, at }]);
 *
 * Options: storePath, redactPII (default true), redactNames (default false),
 * resolvedItemTTLdays (default 30), saveOnRecall / recallOnInject (default
 * true), staleThreadDays (default 14), extractor (optional LLM hook).
 */

const { FileStore, normalizeCallerId } = require('./store');
const { extract, normalizeTurns } = require('./extract');
const { redact, redactNames } = require('./redact');

const DAY_MS = 24 * 60 * 60 * 1000;
const DEFAULT_TTL_DAYS = 30;

// Topical matching for search(): near-synonyms canonicalized so "problem"
// matches "issue", "updates" matches "update", etc.
const SEARCH_SYNONYMS = {
  problem: 'issue', problems: 'issue',
  asking: 'ask', asked: 'ask',
  prefers: 'prefer', preferred: 'prefer', preference: 'prefer',
  updates: 'update', updated: 'update', callbacks: 'callback',
  likes: 'like', loves: 'like',
};
const STOPWORDS = new Set(
  'the a an and or of to for in on is it you your we do does what when how please me my with ask about get can i this that'.split(' ')
);
function searchTokens(s) {
  const out = new Set();
  for (const t of String(s).toLowerCase().match(/[a-z0-9]+/g) || []) {
    const c = SEARCH_SYNONYMS[t] || t;
    if (!STOPWORDS.has(c)) out.add(c);
  }
  return out;
}
function searchOverlap(a, b) {
  const ta = searchTokens(a);
  const tb = searchTokens(b);
  if (!ta.size || !tb.size) return 0;
  let shared = 0;
  for (const t of ta) if (tb.has(t)) shared++;
  return shared / Math.min(ta.size, tb.size);
}

// normalizeCallerId lives in store.js (it is the store's key function).

function nowIso() {
  return new Date().toISOString();
}

function norm(s) {
  return String(s).toLowerCase().replace(/\s+/g, ' ').trim();
}

function blankBrief(callerId) {
  return {
    callerId,
    displayName: null,
    createdAt: nowIso(),
    updatedAt: nowIso(),
    callCount: 0,
    preferences: [],
    openThreads: [],
    resolvedItems: [],
    facts: [],
  };
}

function upsertUnique(list, text, extra) {
  const n = norm(text);
  const existing = list.find((e) => norm(e.text) === n);
  if (existing) {
    existing.lastMentionedAt = nowIso();
    return existing;
  }
  const entry = { text, addedAt: nowIso(), lastMentionedAt: nowIso(), tags: [], ...extra };
  list.push(entry);
  return entry;
}

class VoiceMemory {
  constructor(options = {}) {
    this.store = new FileStore(options.storePath || './voice-memory.json');
    this.redactPII = options.redactPII !== false; // ON by default
    this.redactNames = options.redactNames === true; // opt-in stricter mode
    this.resolvedItemTTL = (options.resolvedItemTTLdays ?? DEFAULT_TTL_DAYS) * DAY_MS;
    this.saveOnRecall = options.saveOnRecall !== false; // separate toggles
    this.recallOnInject = options.recallOnInject !== false;
    this.staleThreadDays = options.staleThreadDays ?? 14;
    // Optional LLM extractor hook: fn({ transcript, turns }) -> partial
    // { preferences, facts, openThreads, resolvedItems } (string arrays).
    // Merged over the rule-based result; exceptions are caught so a
    // misbehaving extractor can never break a call.
    this.extractor = typeof options.extractor === 'function' ? options.extractor : null;
  }

  _id(callerId) {
    if (!callerId) throw new Error('callerId is required');
    return normalizeCallerId(callerId);
  }

  _brief(callerId) {
    const id = this._id(callerId);
    let b = this.store.getCaller(id);
    if (!b) {
      b = blankBrief(id);
      this.store.setCaller(id, b);
    }
    return b;
  }

  _prune(b) {
    const now = Date.now();
    b.resolvedItems = b.resolvedItems.filter(
      (r) => !r.expiresAt || new Date(r.expiresAt).getTime() > now
    );
  }

  _merge(b, extracted, source) {
    for (const p of extracted.preferences) upsertUnique(b.preferences, p, { source });
    for (const t of extracted.openThreads) {
      upsertUnique(b.openThreads, t, { status: 'open', source });
    }
    for (const r of extracted.resolvedItems) {
      // A resolved thread closes the matching open thread.
      const m = r.match(/^Resolved:\s*(.+)$/i);
      if (m) {
        const target = norm(m[1]);
        const idx = b.openThreads.findIndex((o) => norm(o.text) === target);
        if (idx >= 0) b.openThreads.splice(idx, 1);
      }
      upsertUnique(b.resolvedItems, r, {
        resolvedAt: nowIso(),
        expiresAt: new Date(Date.now() + this.resolvedItemTTL).toISOString(),
        source,
      });
    }
    for (const f of extracted.facts) upsertUnique(b.facts, f, { source });
    if (!b.displayName && extracted.displayName) b.displayName = extracted.displayName;
    b.updatedAt = nowIso();
  }

  _scrub(text) {
    let out = redact(text || '', { enabled: this.redactPII });
    if (this.redactNames) out = redactNames(out);
    return out;
  }

  /**
   * Rule-based extraction merged with the optional LLM extractor hook.
   * Hook strings are PII-scrubbed so an extractor can never smuggle raw
   * PII into the brief; a throwing extractor is ignored.
   */
  _extractWithHook(clean) {
    const base = extract(clean);
    if (!this.extractor) return base;
    let extra;
    try {
      extra = this.extractor({ transcript: clean, turns: normalizeTurns(clean) }) || {};
    } catch {
      return base; // an extractor must never break the call
    }
    if (!extra || typeof extra !== 'object') return base;
    const pick = (...keys) => {
      for (const k of keys) if (Array.isArray(extra[k])) return extra[k];
      return [];
    };
    const mergeStrs = (list, strs) => {
      const seen = new Set(list.map(norm));
      for (const s of strs) {
        const t = this._scrub(String(s || '').trim());
        if (t && !seen.has(norm(t))) {
          seen.add(norm(t));
          list.push(t);
        }
      }
    };
    mergeStrs(base.preferences, pick('preferences'));
    mergeStrs(base.facts, pick('facts'));
    mergeStrs(base.openThreads, pick('openThreads', 'open_threads'));
    mergeStrs(base.resolvedItems, pick('resolvedItems', 'resolved'));
    if (!base.displayName) {
      const name = extra.displayName || extra.display_name;
      if (name) base.displayName = this._scrub(String(name).trim());
    }
    return base;
  }

  /**
   * Record a finished call. Transcript is PII-scrubbed (unless disabled),
   * extracted, merged into the caller's brief, and persisted.
   */
  recordCall(callerId, { transcript, at } = {}) {
    const id = this._id(callerId);
    const clean = this._scrub(transcript || '');
    const extracted = this._extractWithHook(clean);
    const b = this._brief(id);
    const source = 'call-' + (b.callCount + 1);
    this._merge(b, extracted, source);
    b.callCount += 1;
    if (at) b.updatedAt = new Date(at).toISOString();
    this._prune(b);
    this.store.setCaller(id, b);
    return this.getBrief(id);
  }

  /** Replay old transcripts in order — same code path as live recording. */
  backfill(callerId, transcripts) {
    if (!callerId) throw new Error('backfill: callerId is required');
    if (!Array.isArray(transcripts)) throw new Error('backfill: transcripts must be an array');
    let brief = null;
    for (const t of transcripts) {
      brief = this.recordCall(callerId, { transcript: t.transcript, at: t.at });
    }
    return brief || this.getBrief(callerId);
  }

  /** Read-only view of a caller's brief (prunes expired resolved items). */
  getBrief(callerId) {
    const b = this.store.getCaller(this._id(callerId));
    if (!b) return null;
    this._prune(b);
    return JSON.parse(JSON.stringify(b));
  }

  /**
   * Build the session-start injection block. Returns "" for unknown callers
   * (safe no-op). `maxChars` caps length for tight system prompts.
   */
  buildInjection(callerId, { maxChars = 1200 } = {}) {
    if (!this.recallOnInject) return '';
    const b = this.getBrief(callerId);
    if (!b || b.callCount === 0) return '';
    const who = b.displayName ? ` (${b.displayName})` : '';
    const lines = [`[Caller memory — ${b.callCount} prior call${b.callCount === 1 ? '' : 's'}${who}]`];
    if (b.preferences.length) {
      lines.push('Preferences: ' + b.preferences.map((p) => p.text).join('; ') + '.');
    }
    if (b.openThreads.length) {
      lines.push('Open threads: ' + b.openThreads.map((t) => t.text).join('; ') + '.');
    }
    if (b.resolvedItems.length) {
      lines.push('Recently resolved: ' + b.resolvedItems.map((r) => r.text).join('; ') + '.');
    }
    if (b.facts.length) {
      lines.push('Facts: ' + b.facts.map((f) => f.text).join('; ') + '.');
    }
    let out = lines.join('\n');
    if (out.length > maxChars) out = out.slice(0, maxChars - 1) + '…';
    if (this.saveOnRecall) this.store.save(); // persist any pruning
    return out;
  }

  /**
   * Mark flow steps skippable when memory already covers them.
   * steps: [{ id, label, keywords: [..] }]
   * Open threads never make a step skippable — they need follow-up.
   */
  planFlow(callerId, steps) {
    const b = this.getBrief(callerId);
    const hayWords = new Set(
      b
        ? [...b.preferences, ...b.facts, ...b.resolvedItems]
            .flatMap((e) => norm(e.text).split(/[^a-z0-9]+/))
            .filter(Boolean)
        : []
    );
    const covered = (keyword) =>
      norm(keyword)
        .split(/[^a-z0-9]+/)
        .filter(Boolean)
        .every((w) => hayWords.has(w));
    return (steps || []).map((s) => {
      const kws = s.keywords || [];
      const hit = kws.find((k) => k && covered(k));
      return {
        ...s,
        skippable: Boolean(hit),
        skipReason: hit ? `Memory covers "${hit}"` : null,
      };
    });
  }

  /**
   * Keyword search across all briefs. Topical, synonym-aware matching
   * (e.g. "problem" finds "issue"). Returns [{ callerId, text }].
   */
  search(query, { limit = 5 } = {}) {
    const hits = [];
    for (const id of this.listCallers()) {
      const b = this.getBrief(id);
      if (!b) continue;
      for (const e of [...b.preferences, ...b.facts, ...b.openThreads, ...b.resolvedItems]) {
        const score = searchOverlap(query, e.text);
        if (score > 0) hits.push({ callerId: id, text: e.text, score });
      }
    }
    hits.sort((a, b2) => b2.score - a.score);
    return hits.slice(0, limit).map(({ callerId, text }) => ({ callerId, text }));
  }

  /** Export a brief as Markdown (for review / audit). */
  exportMarkdown(callerId) {
    const b = this.getBrief(callerId);
    if (!b) return null;
    const sec = (title, items, fmt) =>
      items.length ? `## ${title}\n${items.map((i) => `- ${fmt(i)}`).join('\n')}\n` : '';
    return (
      `# Caller brief: ${b.displayName || b.callerId}\n` +
      `Calls: ${b.callCount} · Updated: ${b.updatedAt}\n\n` +
      sec('Preferences', b.preferences, (p) => p.text) +
      sec('Open threads', b.openThreads, (t) => t.text) +
      sec('Recently resolved', b.resolvedItems, (r) => r.text) +
      sec('Facts', b.facts, (f) => f.text)
    );
  }

  listCallers() {
    return this.store.listCallers();
  }

  deleteCaller(callerId) {
    return this.store.deleteCaller(this._id(callerId));
  }

  /**
   * Merge one caller's brief into another's (e.g. they called from two
   * numbers). Lists dedupe by normalized text keeping the earliest
   * addedAt; call counts add up; the source brief is deleted.
   * Returns the merged brief, or null when the source brief is missing.
   */
  mergeCallers(fromId, intoId) {
    const from = this._id(fromId);
    const into = this._id(intoId);
    if (from === into) return this.getBrief(into);
    const src = this.store.getCaller(from);
    if (!src) return this.getBrief(into);
    const dst = this._brief(into);
    const mergeList = (key) => {
      for (const e of src[key] || []) {
        const n = norm(e.text);
        const ex = dst[key].find((x) => norm(x.text) === n);
        if (ex) {
          if (e.addedAt && (!ex.addedAt || e.addedAt < ex.addedAt)) ex.addedAt = e.addedAt;
          if (e.lastMentionedAt && (!ex.lastMentionedAt || e.lastMentionedAt > ex.lastMentionedAt)) {
            ex.lastMentionedAt = e.lastMentionedAt;
          }
        } else {
          dst[key].push({ tags: [], ...e });
        }
      }
    };
    for (const k of ['preferences', 'openThreads', 'resolvedItems', 'facts']) mergeList(k);
    dst.callCount = (dst.callCount || 0) + (src.callCount || 0);
    if (!dst.displayName && src.displayName) dst.displayName = src.displayName;
    if (src.createdAt && (!dst.createdAt || src.createdAt < dst.createdAt)) dst.createdAt = src.createdAt;
    if (src.updatedAt && (!dst.updatedAt || src.updatedAt > dst.updatedAt)) dst.updatedAt = src.updatedAt;
    this._prune(dst);
    this.store.setCaller(into, dst);
    this.store.deleteCaller(from);
    return this.getBrief(into);
  }

  /**
   * Manually close an open thread (matched by normalized text or substring).
   * Moves it to resolved items with the standard TTL. Returns the closed
   * thread entry, or null when nothing matched.
   */
  resolveThread(callerId, threadText) {
    const id = this._id(callerId);
    const b = this.store.getCaller(id);
    if (!b) return null;
    const needle = norm(threadText || '');
    if (!needle) return null;
    const idx = b.openThreads.findIndex(
      (t) => norm(t.text) === needle || norm(t.text).includes(needle) || needle.includes(norm(t.text))
    );
    if (idx === -1) return null;
    const [closed] = b.openThreads.splice(idx, 1);
    const entry = upsertUnique(b.resolvedItems, 'Resolved: ' + closed.text, {
      resolvedAt: nowIso(),
      expiresAt: new Date(Date.now() + this.resolvedItemTTL).toISOString(),
      source: 'manual',
    });
    b.updatedAt = nowIso();
    this._prune(b);
    this.store.setCaller(id, b);
    return JSON.parse(JSON.stringify(entry));
  }

  /**
   * Open threads not mentioned in `days` days (default: staleThreadDays).
   * Useful for "you never got back to them about X" nudges.
   */
  staleThreads(callerId, days) {
    const id = this._id(callerId);
    const b = this.store.getCaller(id);
    if (!b) return [];
    const cutoff = Date.now() - (days ?? this.staleThreadDays) * DAY_MS;
    return b.openThreads
      .filter((t) => new Date(t.lastMentionedAt || t.addedAt || 0).getTime() < cutoff)
      .map((t) => JSON.parse(JSON.stringify(t)));
  }
}

module.exports = { VoiceMemory, DEFAULT_TTL_DAYS, normalizeCallerId };
