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

console.log(`\nsmoke: ${passed} passed${process.exitCode ? ' (FAILURES)' : ''}`);
