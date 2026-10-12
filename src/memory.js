'use strict';

/**
 * VoiceMemory — middleware between a voice framework and a local file store.
 *
 *   const vm = new VoiceMemory({ storePath: './voice-memory.json' });
 *   vm.recordCall('+15551234567', { transcript });   // call end
 *   vm.buildInjection('+15551234567');               // next call start
 *   vm.planFlow('+15551234567', steps);              // skippable steps
 *   vm.backfill('+15551234567', [{ transcript, at }]);
 */

const { FileStore } = require('./store');
const { extract } = require('./extract');
const { redact } = require('./redact');

const DAY_MS = 24 * 60 * 60 * 1000;
const DEFAULT_TTL_DAYS = 30;

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
  const entry = { text, addedAt: nowIso(), lastMentionedAt: nowIso(), ...extra };
  list.push(entry);
  return entry;
}

class VoiceMemory {
  constructor(options = {}) {
    this.store = new FileStore(options.storePath || './voice-memory.json');
    this.redactPII = options.redactPII !== false; // ON by default
    this.resolvedItemTTL = (options.resolvedItemTTLdays ?? DEFAULT_TTL_DAYS) * DAY_MS;
    this.saveOnRecall = options.saveOnRecall !== false; // separate toggles
    this.recallOnInject = options.recallOnInject !== false;
  }

  _brief(callerId) {
    let b = this.store.getCaller(callerId);
    if (!b) {
      b = blankBrief(callerId);
      this.store.setCaller(callerId, b);
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

  /**
   * Record a finished call. Transcript is PII-scrubbed (unless disabled),
   * extracted, merged into the caller's brief, and persisted.
   */
  recordCall(callerId, { transcript, at } = {}) {
    if (!callerId) throw new Error('recordCall: callerId is required');
    const clean = redact(transcript || '', { enabled: this.redactPII });
    const extracted = extract(clean);
    const b = this._brief(callerId);
    const source = 'call-' + (b.callCount + 1);
    this._merge(b, extracted, source);
    b.callCount += 1;
    if (at) b.updatedAt = new Date(at).toISOString();
    this._prune(b);
    this.store.setCaller(callerId, b);
    return this.getBrief(callerId);
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
    const b = this.store.getCaller(callerId);
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
    return this.store.deleteCaller(callerId);
  }
}

module.exports = { VoiceMemory, DEFAULT_TTL_DAYS };
