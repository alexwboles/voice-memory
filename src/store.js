'use strict';

/**
 * Single-file local store, Limelit Open-style.
 * Whole store = { version: 2, callers: { [callerId]: brief } }.
 * Atomic writes: temp file + rename, so no torn writes on crash.
 *
 * Legacy stores upgrade on load:
 *  - voice-memory v1 ({ version: 1, callers }) — used as-is.
 *  - callrecall-ai ({ version: 1, contacts: { id: snake_case brief } }) —
 *    converted to the v2 shape (see MIGRATION.md for the field mapping).
 */

const fs = require('fs');
const path = require('path');

const STORE_VERSION = 2;

function blankStore() {
  return { version: STORE_VERSION, callers: {} };
}

function nowIso() {
  return new Date().toISOString();
}

/**
 * Normalize a caller ID so the same person reached via different formats
 * maps to one brief. Phone-like IDs collapse to bare digits (NANP leading-1
 * dropped: "+1 (555) 123-4567" == "15551234567" == "5551234567").
 * Anything else is trimmed and lowercased.
 * (Lives here because it is the store's key function; memory.js re-exports.)
 */
function normalizeCallerId(id) {
  const raw = String(id || '').trim();
  const digits = raw.replace(/\D/g, '');
  if (digits.length >= 7) {
    if (digits.length === 11 && digits[0] === '1') return digits.slice(1);
    return digits;
  }
  return raw.toLowerCase();
}

function normText(s) {
  return String(s).toLowerCase().replace(/\s+/g, ' ').trim();
}

/** Convert one legacy callrecall-ai brief (snake_case) to the v2 shape. */
function upgradeCallrecallBrief(contactId, raw) {
  const now = nowIso();
  let created = now;
  for (const it of [...(raw.preferences || []), ...(raw.facts || [])]) {
    const c = String(it.created_at || '');
    if (c && c < created) created = c;
  }
  const items = (key, extra) =>
    (raw[key] || []).map((it) => ({
      text: String(it.text || ''),
      addedAt: String(it.created_at || now),
      lastMentionedAt: String(it.created_at || now),
      source: String(it.source || ''),
      tags: Array.isArray(it.tags) ? it.tags : [],
      ...(extra || {}),
    }));
  return {
    callerId: contactId,
    displayName: raw.display_name || null,
    createdAt: created,
    updatedAt: String(raw.last_call_at || now),
    lastCallAt: String(raw.last_call_at || now),
    callCount: Number(raw.call_count || 0),
    summary: String(raw.summary || ''),
    preferences: items('preferences'),
    openThreads: items('open_threads', { status: 'open' }),
    resolvedItems: (raw.resolved || []).map((it) => ({
      text: String(it.text || ''),
      addedAt: String(it.created_at || now),
      lastMentionedAt: String(it.created_at || now),
      resolvedAt: String(it.created_at || now),
      expiresAt: String(it.valid_until || ''),
      source: String(it.source || ''),
      tags: Array.isArray(it.tags) ? it.tags : [],
    })),
    facts: items('facts'),
  };
}

function upgradeLegacyStore(parsed) {
  const data = { version: STORE_VERSION, callers: {} };
  if (parsed.callers && typeof parsed.callers === 'object') {
    data.callers = parsed.callers;
  } else if (parsed.contacts && typeof parsed.contacts === 'object') {
    // Legacy callrecall-ai keys were never normalized; normalize on the way
    // in, merging briefs whose keys collide after normalization.
    for (const [id, raw] of Object.entries(parsed.contacts)) {
      if (!raw || typeof raw !== 'object') continue;
      const nid = normalizeCallerId(String(id));
      const nb = upgradeCallrecallBrief(nid, raw);
      data.callers[nid] = data.callers[nid]
        ? mergeUpgradedBriefs(data.callers[nid], nb)
        : nb;
    }
  }
  return data;
}

/** Merge two upgraded briefs whose legacy keys normalized to the same id. */
function mergeUpgradedBriefs(a, b) {
  const cat = (x, y) => {
    const seen = new Set((x || []).map((e) => normText(e.text)));
    const out = [...(x || [])];
    for (const e of y || []) {
      if (!seen.has(normText(e.text))) {
        seen.add(normText(e.text));
        out.push(e);
      }
    }
    return out;
  };
  return {
    callerId: a.callerId,
    displayName: a.displayName || b.displayName,
    createdAt: a.createdAt < b.createdAt ? a.createdAt : b.createdAt,
    updatedAt: a.updatedAt > b.updatedAt ? a.updatedAt : b.updatedAt,
    lastCallAt: (a.lastCallAt || '') > (b.lastCallAt || '') ? a.lastCallAt : b.lastCallAt,
    callCount: (a.callCount || 0) + (b.callCount || 0),
    summary: a.summary || b.summary,
    preferences: cat(a.preferences, b.preferences),
    openThreads: cat(a.openThreads, b.openThreads),
    resolvedItems: cat(a.resolvedItems, b.resolvedItems),
    facts: cat(a.facts, b.facts),
  };
}

class FileStore {
  constructor(storePath) {
    this.path = path.resolve(storePath);
    this.data = this._load();
  }

  _load() {
    try {
      const raw = fs.readFileSync(this.path, 'utf8');
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && (parsed.callers || parsed.contacts)) {
        return upgradeLegacyStore(parsed);
      }
      return blankStore();
    } catch {
      return blankStore();
    }
  }

  save() {
    this.data.version = STORE_VERSION;
    const dir = path.dirname(this.path);
    fs.mkdirSync(dir, { recursive: true });
    const tmp = this.path + '.tmp.' + process.pid;
    fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2), 'utf8');
    fs.renameSync(tmp, this.path);
  }

  getCaller(callerId) {
    return this.data.callers[callerId] || null;
  }

  setCaller(callerId, brief) {
    this.data.callers[callerId] = brief;
    this.save();
  }

  deleteCaller(callerId) {
    const existed = Boolean(this.data.callers[callerId]);
    delete this.data.callers[callerId];
    if (existed) this.save();
    return existed;
  }

  listCallers() {
    return Object.keys(this.data.callers);
  }
}

module.exports = { FileStore, STORE_VERSION, normalizeCallerId, upgradeLegacyStore, upgradeCallrecallBrief };
