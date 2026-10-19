#!/usr/bin/env node
'use strict';

/**
 * Smoke tests — unit assertions over redact, extract, store, memory.
 * Run: node test/smoke.mjs   (or npm run smoke)
 */

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { redact } = require('../src/redact');
const { extract } = require('../src/extract');
const { FileStore } = require('../src/store');
const { VoiceMemory } = require('../src/memory');

let passed = 0;
function t(name, fn) {
  try {
    fn();
    passed++;
    console.log('  ok -', name);
  } catch (e) {
    console.error('  FAIL -', name, '\n   ', e.message);
    process.exitCode = 1;
  }
}

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'vm-')), 'store.json');

console.log('redact:');
t('redacts email', () => {
  assert.strictEqual(redact('mail me at jane@doe.com ok'), 'mail me at [email] ok');
});
t('redacts US phone formats', () => {
  assert.ok(redact('call (555) 123-4567').includes('[phone]'));
  assert.ok(redact('call 555-123-4567').includes('[phone]'));
});
t('redacts SSN', () => {
  assert.ok(redact('ssn 123-45-6789').includes('[ssn]'));
});
t('redacts Luhn-valid card, keeps other digit runs', () => {
  assert.ok(redact('card 4111111111111111').includes('[card]'));
  assert.strictEqual(redact('order 12345'), 'order 12345');
});
t('redacts account numbers, keeps label', () => {
  assert.ok(redact('account #98765').includes('[account]'));
});
t('disabled flag returns text untouched', () => {
  assert.strictEqual(redact('jane@doe.com', { enabled: false }), 'jane@doe.com');
});
t('keeps names', () => {
  assert.ok(redact('my name is Maya').includes('Maya'));
});

console.log('extract:');
t('extracts preference', () => {
  const e = extract('Caller: I prefer morning callbacks.');
  assert.ok(e.preferences.some((p) => p.includes('morning callbacks')));
});
t('extracts negative preference', () => {
  const e = extract('Caller: please never call after 6pm.');
  assert.ok(e.preferences.some((p) => p.startsWith('Avoid:')));
});
t('extracts open thread', () => {
  const e = extract('Caller: I am still waiting on the refund.');
  assert.ok(e.openThreads.some((x) => x.includes('refund')));
});
t('extracts unanswered caller question as thread', () => {
  const e = extract('Caller: what is my current balance?');
  assert.ok(e.openThreads.some((x) => x.includes('balance')));
});
t('does not treat agent promises as caller threads', () => {
  const e = extract('Agent: I will follow up on that tomorrow.\nCaller: ok thanks.');
  assert.strictEqual(e.openThreads.length, 0);
});
t('extracts resolution', () => {
  const e = extract('Caller: great, that is resolved. Thanks.');
  assert.ok(e.resolvedItems.length > 0);
});
t('extracts fact', () => {
  const e = extract('Caller: my timezone is America/New_York.');
  assert.ok(e.facts.length > 0);
});
t('extracts display name', () => {
  const e = extract('Caller: Hi, my name is Maya.');
  assert.strictEqual(e.displayName, 'Maya');
});
t('name intro is not stored as a fact (displayName covers it)', () => {
  const e = extract('Caller: Hi, my name is Maya. I prefer morning calls.');
  assert.strictEqual(e.displayName, 'Maya');
  assert.ok(!e.facts.some((f) => /maya/i.test(f)), 'name must not leak into facts');
});
t('index re-exports normalizeCallerId and DEFAULT_TTL_DAYS', () => {
  const idx = require('../src/index');
  assert.strictEqual(typeof idx.VoiceMemory, 'function');
  assert.strictEqual(typeof idx.normalizeCallerId, 'function');
  assert.strictEqual(idx.DEFAULT_TTL_DAYS, 30);
});
t('empty transcript extracts nothing', () => {
  const e = extract('');
  assert.deepStrictEqual([e.preferences, e.openThreads, e.resolvedItems, e.facts], [[], [], [], []]);
});

console.log('store:');
t('round-trips a brief', () => {
  const s = new FileStore(tmp());
  s.setCaller('a', { callerId: 'a', callCount: 1 });
  assert.strictEqual(new FileStore(s.path).getCaller('a').callCount, 1);
});
t('missing file starts empty', () => {
  const s = new FileStore(path.join(os.tmpdir(), 'vm-nope-' + Date.now(), 's.json'));
  assert.deepStrictEqual(s.listCallers(), []);
});
t('deleteCaller removes and reports', () => {
  const s = new FileStore(tmp());
  s.setCaller('a', { callerId: 'a' });
  assert.strictEqual(s.deleteCaller('a'), true);
  assert.strictEqual(s.deleteCaller('a'), false);
});

console.log('memory:');
t('recordCall stores redacted brief', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  const b = vm.recordCall('c1', { transcript: 'Caller: my email is a@b.com. I prefer texts.' });
  assert.strictEqual(b.callCount, 1);
  assert.ok(!JSON.stringify(b).includes('a@b.com'));
  assert.ok(b.preferences.some((p) => p.text.includes('texts')));
});
t('second call merges, bumps lastMentionedAt, no dupes', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: I prefer texts.' });
  const b2 = vm.recordCall('c1', { transcript: 'Caller: I prefer texts.' });
  assert.strictEqual(b2.callCount, 2);
  assert.strictEqual(b2.preferences.length, 1);
});
t('buildInjection empty for unknown caller', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  assert.strictEqual(vm.buildInjection('nobody'), '');
});
t('buildInjection contains brief content', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: Hi, my name is Sam. I prefer email.' });
  const inj = vm.buildInjection('c1');
  assert.ok(inj.includes('Sam') && inj.includes('email'));
});
t('buildInjection respects maxChars', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: I prefer email.' });
  assert.ok(vm.buildInjection('c1', { maxChars: 40 }).length <= 40);
});
t('planFlow marks covered steps skippable', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: great, that is resolved. My identity is verified.' });
  const steps = vm.planFlow('c1', [
    { id: 'v', label: 'Verify identity', keywords: ['identity verified'] },
    { id: 'g', label: 'Greet', keywords: [] },
  ]);
  assert.strictEqual(steps.find((s) => s.id === 'v').skippable, true);
  assert.strictEqual(steps.find((s) => s.id === 'g').skippable, false);
});
t('open threads never make steps skippable', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: still waiting on the refund.' });
  const steps = vm.planFlow('c1', [{ id: 'r', label: 'Refund', keywords: ['refund'] }]);
  assert.strictEqual(steps[0].skippable, false);
});
t('expired resolved items are pruned', () => {
  const vm = new VoiceMemory({ storePath: tmp(), resolvedItemTTLdays: 0 });
  vm.recordCall('c1', { transcript: 'Caller: all set, thanks.' });
  const b = vm.getBrief('c1');
  assert.strictEqual(b.resolvedItems.length, 0);
});
t('backfill replays transcripts in order', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  const b = vm.backfill('c1', [
    { transcript: 'Caller: I prefer email.' },
    { transcript: 'Caller: still waiting on the refund.' },
  ]);
  assert.strictEqual(b.callCount, 2);
  assert.ok(b.preferences.length === 1 && b.openThreads.length === 1);
});
t('exportMarkdown renders sections', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: Hi, my name is Jo. I prefer email.' });
  const md = vm.exportMarkdown('c1');
  assert.ok(md.includes('# Caller brief: Jo') && md.includes('## Preferences'));
});
t('redactPII:false keeps PII in the brief', () => {
  const vm = new VoiceMemory({ storePath: tmp(), redactPII: false });
  const b = vm.recordCall('c1', { transcript: 'Caller: I prefer email at a@b.com.' });
  assert.ok(JSON.stringify(b).includes('a@b')); // raw address fragment survives
});
t('redactPII:true (default) scrubs PII from the brief', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  const b = vm.recordCall('c1', { transcript: 'Caller: I prefer email at a@b.com.' });
  assert.ok(!JSON.stringify(b).includes('a@b.com'));
  assert.ok(JSON.stringify(b).includes('[email]'));
});
t('requires callerId', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  assert.throws(() => vm.recordCall('', { transcript: 'hi' }), /callerId/);
});

t('normalizeCallerId collapses phone formats, trims/lowercases the rest', () => {
  const { normalizeCallerId } = require('../src/index');
  assert.strictEqual(normalizeCallerId('+1 (555) 123-4567'), '5551234567');
  assert.strictEqual(normalizeCallerId('15551234567'), '5551234567');
  assert.strictEqual(normalizeCallerId('555-123-4567'), '5551234567');
  assert.strictEqual(normalizeCallerId('  Alex@Example.com '), 'alex@example.com');
  assert.strictEqual(normalizeCallerId('support-desk'), 'support-desk');
});
t('recordCall with different caller-ID formats lands on one brief', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('+1 (555) 123-4567', { transcript: 'Caller: I prefer text messages.' });
  const b = vm.recordCall('5551234567', { transcript: 'Caller: My name is Priya.' });
  assert.strictEqual(b.callCount, 2, 'two calls on one brief');
  assert.strictEqual(b.callerId, '5551234567', 'brief keyed by normalized id');
  assert.deepStrictEqual(vm.listCallers(), ['5551234567']);
  assert.strictEqual(vm.getBrief('15551234567').callCount, 2, 'lookup normalizes too');
});
t('mergeCallers combines briefs and deletes the source', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('aaa', { transcript: 'Caller: I prefer text messages. I am still waiting on the March invoice.' });
  vm.recordCall('bbb', { transcript: 'Caller: I prefer TEXT messages. My timezone is Eastern.' });
  const merged = vm.mergeCallers('bbb', 'aaa');
  assert.strictEqual(merged.callCount, 2, 'call counts add up');
  const prefs = merged.preferences.filter((p) => /text messages/i.test(p.text));
  assert.strictEqual(prefs.length, 1, 'duplicate preference deduped: ' + JSON.stringify(merged.preferences));
  assert.ok(merged.openThreads.some((t) => /march invoice/i.test(t.text)), 'threads carried over');
  assert.ok(merged.facts.some((f) => /eastern/i.test(f.text)), 'facts carried over');
  assert.strictEqual(vm.getBrief('bbb'), null, 'source brief deleted');
  assert.deepStrictEqual(vm.listCallers(), ['aaa']);
});
t('mergeCallers with missing source is a no-op', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('aaa', { transcript: 'Caller: I prefer text messages.' });
  const merged = vm.mergeCallers('ghost', 'aaa');
  assert.strictEqual(merged.callCount, 1);
});
t('resolveThread closes an open thread and files it as resolved', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: I am still waiting on the March invoice.' });
  assert.ok(vm.getBrief('c1').openThreads.some((t) => /march invoice/i.test(t.text)), 'thread starts open');
  const closed = vm.resolveThread('c1', 'march invoice');
  assert.ok(closed && /Resolved: /i.test(closed.text), 'returns the closed entry');
  const b = vm.getBrief('c1');
  assert.strictEqual(b.openThreads.length, 0, 'thread removed from open');
  assert.ok(b.resolvedItems.some((r) => /march invoice/i.test(r.text)), 'filed as resolved');
  assert.strictEqual(vm.resolveThread('c1', 'no such thread'), null, 'no match -> null');
});
t('staleThreads flags threads untouched for N days', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: I am still waiting on the March invoice.' });
  const b = vm.store.getCaller('c1');
  b.openThreads[0].lastMentionedAt = new Date(Date.now() - 20 * 24 * 3600 * 1000).toISOString();
  vm.store.save();
  assert.strictEqual(vm.staleThreads('c1', 14).length, 1, 'stale at 14 days');
  assert.strictEqual(vm.staleThreads('c1', 30).length, 0, 'fresh at 30 days');
  assert.deepStrictEqual(vm.staleThreads('ghost'), [], 'unknown caller -> []');
});

t('deleteCaller removes the brief', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: I prefer texts.' });
  assert.strictEqual(vm.deleteCaller('c1'), true);
  assert.strictEqual(vm.getBrief('c1'), null);
  assert.strictEqual(vm.deleteCaller('c1'), false);
});

console.log('extractor hook (from callrecall-ai):');
t('hook strings merge over rule-based extraction', () => {
  const vm = new VoiceMemory({
    storePath: tmp(),
    extractor: ({ transcript, turns }) => {
      assert.ok(typeof transcript === 'string' && transcript.length > 0);
      assert.ok(Array.isArray(turns) && turns[0].role === 'caller');
      return { facts: ['vip customer since 2019'], openThreads: ['llm-found thread'] };
    },
  });
  const b = vm.recordCall('c1', { transcript: 'Caller: I prefer email.' });
  assert.ok(b.facts.some((f) => f.text === 'vip customer since 2019'));
  assert.ok(b.openThreads.some((x) => x.text === 'llm-found thread'));
  assert.ok(b.preferences.some((p) => p.text.includes('email')));
});
t('hook dedupes against rule-based results', () => {
  const vm = new VoiceMemory({
    storePath: tmp(),
    extractor: () => ({ preferences: ['Prefers: email'] }),
  });
  const b = vm.recordCall('c1', { transcript: 'Caller: I prefer email.' });
  assert.strictEqual(b.preferences.filter((p) => /email/i.test(p.text)).length, 1);
});
t('a throwing extractor never breaks the call', () => {
  const vm = new VoiceMemory({
    storePath: tmp(),
    extractor: () => { throw new Error('llm down'); },
  });
  const b = vm.recordCall('c1', { transcript: 'Caller: I prefer email.' });
  assert.strictEqual(b.callCount, 1);
  assert.ok(b.preferences.some((p) => p.text.includes('email')));
});
t('hook output is PII-scrubbed before storage', () => {
  const vm = new VoiceMemory({
    storePath: tmp(),
    extractor: () => ({ facts: ['reached at sneaky@example.com'] }),
  });
  const b = vm.recordCall('c1', { transcript: 'Caller: hi.' });
  assert.ok(!JSON.stringify(b).includes('sneaky@example.com'));
  assert.ok(b.facts.some((f) => f.text.includes('[email]')));
});

console.log('search:');
t('search finds topical hits across callers', () => {
  const vm = new VoiceMemory({ storePath: tmp() });
  vm.recordCall('c1', { transcript: 'Caller: my package never arrived, the issue is the shipment.' });
  vm.recordCall('c2', { transcript: 'Caller: I prefer email.' });
  const hits = vm.search('problem with delivery');
  assert.ok(hits.length > 0);
  assert.strictEqual(hits[0].callerId, 'c1', 'issue/problem synonym match: ' + JSON.stringify(hits));
  assert.ok(vm.search('nonexistent zebra query qqq').length === 0);
});

console.log('redactNames (opt-in stricter mode):');
t('names masked when redactNames:true, kept by default', () => {
  const strict = new VoiceMemory({ storePath: tmp(), redactNames: true });
  const b = strict.recordCall('c1', { transcript: 'Caller: I prefer that John Smith handles my account.' });
  assert.ok(JSON.stringify(b).includes('[name]'));
  assert.ok(!JSON.stringify(b).includes('John Smith'));
  const plain = new VoiceMemory({ storePath: tmp() });
  const b2 = plain.recordCall('c1', { transcript: 'Caller: I prefer that John Smith handles my account.' });
  assert.ok(JSON.stringify(b2).includes('John Smith'), 'names kept by default');
});
t('redactNames export works standalone', () => {
  const { redactNames: rn } = require('../src/index');
  assert.ok(rn('met with John Smith').includes('[name]'));
});

console.log('store v2 / legacy upgrade:');
t('legacy callrecall-ai store auto-upgrades on load', () => {
  const p = tmp();
  fs.writeFileSync(p, JSON.stringify({
    version: 1,
    contacts: {
      '+15550001111': {
        contact_id: '+15550001111',
        display_name: 'Sam',
        preferences: [{ text: 'prefers text updates', source: 'call', created_at: '2026-10-01T00:00:00', valid_until: '', tags: [] }],
        facts: [],
        open_threads: [{ text: 'waiting on refund', source: 'call', created_at: '2026-10-02T00:00:00', valid_until: '', tags: [] }],
        resolved: [{ text: 'resolved: login fixed', source: 'call', created_at: '2026-10-03T00:00:00', valid_until: '2026-11-03T00:00:00', tags: [] }],
        call_count: 3,
        last_call_at: '2026-10-03T00:00:00',
        summary: 'Prefers: prefers text updates',
      },
    },
  }));
  const vm = new VoiceMemory({ storePath: p });
  const b = vm.getBrief('+15550001111');
  assert.ok(b, 'upgraded brief loads');
  assert.strictEqual(b.callerId, '5550001111', 'legacy keys normalize on upgrade');
  assert.strictEqual(b.displayName, 'Sam');
  assert.strictEqual(b.callCount, 3);
  assert.ok(b.openThreads.some((x) => x.text === 'waiting on refund' && x.status === 'open'));
  assert.ok(b.resolvedItems.some((r) => r.expiresAt === '2026-11-03T00:00:00'));
  const inj = vm.buildInjection('+15550001111');
  assert.ok(inj.includes('Sam') && inj.includes('waiting on refund'));
});
t('new stores are written as version 2', () => {
  const p = tmp();
  const vm = new VoiceMemory({ storePath: p });
  vm.recordCall('c1', { transcript: 'Caller: I prefer email.' });
  const data = JSON.parse(fs.readFileSync(p, 'utf8'));
  assert.strictEqual(data.version, 2);
  assert.ok(data.callers.c1.preferences[0].tags, 'entries carry tags');
});

console.log(`\nsmoke: ${passed} passed${process.exitCode ? ' (FAILURES)' : ''}`);
