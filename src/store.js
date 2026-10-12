'use strict';

/**
 * Single-file local store, Limelit Open-style.
 * Whole store = { version: 1, callers: { [callerId]: brief } }.
 * Atomic writes: temp file + rename, so no torn writes on crash.
 */

const fs = require('fs');
const path = require('path');

const STORE_VERSION = 1;

function blankStore() {
  return { version: STORE_VERSION, callers: {} };
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
      if (parsed && typeof parsed === 'object' && parsed.callers) return parsed;
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

module.exports = { FileStore, STORE_VERSION };
