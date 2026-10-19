#!/usr/bin/env node
'use strict';

/**
 * E2E tests — full caller lifecycle across separate processes (file store),
 * backfill path, and the Python Pipecat adapter.
 * Run: node test/e2e.cjs   (or npm run e2e)
 */

const assert = require('assert');
const { execFileSync, execSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

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

const ROOT = path.join(__dirname, '..');
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vm-e2e-'));
const STORE = path.join(tmpDir, 'store.json');

function runNode(script) {
  return execFileSync('node', ['-e', script], { cwd: ROOT, encoding: 'utf8' });
}

const CALL_1 = 'Caller: Hi, my name is Priya. I prefer text messages, never voicemail. ' +
  'My email is priya@example.com. I am still waiting on the March invoice.';
const CALL_2 = 'Caller: Just checking on the March invoice. Also that is resolved about the login issue, thanks.';

console.log('e2e: two-call lifecycle across processes');
t('process 1 records call 1 (PII redacted)', () => {
  const out = runNode(`
    const {VoiceMemory} = require('./src/index');
    const vm = new VoiceMemory({storePath: '${STORE}'});
    const b = vm.recordCall('caller-1', {transcript: ${JSON.stringify(CALL_1)}});
    console.log(JSON.stringify({calls: b.callCount, prefs: b.preferences.length,
      threads: b.openThreads.length, hasEmail: JSON.stringify(b).includes('priya@example.com')}));
  `);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.strictEqual(r.calls, 1);
  assert.strictEqual(r.prefs, 1);
  assert.strictEqual(r.threads, 1);
  assert.strictEqual(r.hasEmail, false, 'email must be redacted in store');
});

t('process 2 (restart) sees the brief and injects it', () => {
  const out = runNode(`
    const {VoiceMemory} = require('./src/index');
    const vm = new VoiceMemory({storePath: '${STORE}'});
    const inj = vm.buildInjection('caller-1');
    console.log(JSON.stringify({hasName: inj.includes('Priya'), hasPref: inj.includes('text messages'),
      hasThread: inj.includes('March invoice'), empty: inj === ''}));
  `);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.strictEqual(r.hasName, true);
  assert.strictEqual(r.hasPref, true);
  assert.strictEqual(r.hasThread, true);
  assert.strictEqual(r.empty, false);
});

t('process 3 records call 2, open thread persists, resolution recorded', () => {
  const out = runNode(`
    const {VoiceMemory} = require('./src/index');
    const vm = new VoiceMemory({storePath: '${STORE}'});
    const b = vm.recordCall('caller-1', {transcript: ${JSON.stringify(CALL_2)}});
    console.log(JSON.stringify({calls: b.callCount,
      threads: b.openThreads.map(t => t.text), resolved: b.resolvedItems.length}));
  `);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.strictEqual(r.calls, 2);
  assert.ok(r.threads.some((x) => x.includes('March invoice')), 'invoice thread stays open');
  assert.ok(r.resolved >= 1, 'login resolution recorded');
});

t('store file is valid JSON with expected shape', () => {
  const data = JSON.parse(fs.readFileSync(STORE, 'utf8'));
  assert.strictEqual(data.version, 2);
  assert.ok(data.callers['caller-1']);
  assert.strictEqual(data.callers['caller-1'].displayName, 'Priya');
});

t('planFlow skips the preferences step on call 3', () => {
  const out = runNode(`
    const {VoiceMemory} = require('./src/index');
    const vm = new VoiceMemory({storePath: '${STORE}'});
    const steps = vm.planFlow('caller-1', [
      {id: 'prefs', label: 'Ask contact preferences', keywords: ['text messages']},
      {id: 'invoice', label: 'Invoice follow-up', keywords: ['march invoice']}
    ]);
    console.log(JSON.stringify(steps.map(s => [s.id, s.skippable])));
  `);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.deepStrictEqual(r, [['prefs', true], ['invoice', false]]);
});

console.log('e2e: backfill path');
t('backfill builds a brief from old transcripts', () => {
  const store2 = path.join(tmpDir, 'store2.json');
  const out = runNode(`
    const {VoiceMemory} = require('./src/index');
    const vm = new VoiceMemory({storePath: '${store2}'});
    const b = vm.backfill('caller-9', [
      {transcript: 'Caller: I prefer email.'},
      {transcript: 'Caller: still waiting on the contract.'}
    ]);
    console.log(JSON.stringify({calls: b.callCount, prefs: b.preferences.length,
      threads: b.openThreads.length, inj: vm.buildInjection('caller-9').length > 0}));
  `);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.deepStrictEqual([r.calls, r.prefs, r.threads, r.inj], [2, 1, 1, true]);
});

console.log('e2e: python pipecat adapter');
function runPython(script) {
  const f = path.join(tmpDir, 'py-' + Math.random().toString(36).slice(2) + '.py');
  fs.writeFileSync(f, script, 'utf8');
  return execFileSync('python3', [f], { encoding: 'utf8' });
}
t('adapter records, injects, and plans flow', () => {
  const store3 = path.join(tmpDir, 'store3.json');
  const script = `
import sys; sys.path.insert(0, '${path.join(ROOT, 'pipecat')}')
from voice_memory import VoiceMemoryPipeline
vm = VoiceMemoryPipeline(store_path='${store3}', caller_id_fn=lambda: 'py-1')
vm.session_end('Caller: Hi, my name is Ana. I prefer morning calls. My ssn is 123-45-6789.')
inj = vm.session_start()
brief = vm.get_brief()
steps = vm.plan_flow([{'id': 'x', 'label': 'Ask time', 'keywords': ['morning calls']}])
import json
print(json.dumps({
  'calls': brief['callCount'], 'name': brief['displayName'],
  'injHasPref': 'morning calls' in inj, 'ssnGone': '123-45-6789' not in json.dumps(brief),
  'skip': steps[0]['skippable'],
}))
`;
  const out = runPython(script);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.strictEqual(r.calls, 1);
  assert.strictEqual(r.name, 'Ana');
  assert.strictEqual(r.injHasPref, true);
  assert.strictEqual(r.ssnGone, true);
  assert.strictEqual(r.skip, true);
});

t('adapter: negation words flip preference polarity', () => {
  const script = `
import sys; sys.path.insert(0, '${path.join(ROOT, 'pipecat')}')
from voice_memory import VoiceMemoryPipeline
import json
store = '${path.join(tmpDir, 'store-py-neg.json')}'
vm = VoiceMemoryPipeline(store_path=store, caller_id_fn=lambda: 'py-neg')
b = vm.session_end('Caller: Hi, my name is Ana. Please never call after 6pm.')
print(json.dumps({'prefs': b['preferences']}))
`;
  const out = runPython(script);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.ok(r.prefs.some((p) => p['text'].startsWith('Avoid:') && /after 6pm/.test(p['text'])),
    'negated preference must be an Avoid: ' + JSON.stringify(r.prefs));
});

t('adapter: backfill builds a brief from old transcripts', () => {
  const script = `
import sys; sys.path.insert(0, '${path.join(ROOT, 'pipecat')}')
from voice_memory import VoiceMemoryPipeline
import json
store = '${path.join(tmpDir, 'store-py-backfill.json')}'
vm = VoiceMemoryPipeline(store_path=store, caller_id_fn=lambda: 'py-bf')
b = vm.backfill([{'transcript': 'Caller: I prefer email.'},
                 {'transcript': 'Caller: still waiting on the contract.'}])
print(json.dumps({'calls': b['callCount'],
                  'prefs': len(b['preferences']),
                  'threads': len(b['openThreads']),
                  'inj': len(vm.session_start()) > 0}))
`;
  const out = runPython(script);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.deepStrictEqual([r.calls, r.prefs, r.threads, r.inj], [2, 1, 1, true]);
});

t('adapter: redact_pii=False keeps PII in the brief', () => {
  const script = `
import sys; sys.path.insert(0, '${path.join(ROOT, 'pipecat')}')
from voice_memory import VoiceMemoryPipeline
import json
store = '${path.join(tmpDir, 'store-py-noredact.json')}'
vm = VoiceMemoryPipeline(store_path=store, caller_id_fn=lambda: 'py-nr', redact_pii=False)
b = vm.session_end('Caller: my email is ana@example.com.')
print(json.dumps({'raw': 'ana@example.com' in json.dumps(b)}))
`;
  const out = runPython(script);
  assert.strictEqual(JSON.parse(out.trim().split('\n').pop()).raw, true);
});

t('adapter persists across instances', () => {
  const store3 = path.join(tmpDir, 'store3.json');
  const script = `
import sys; sys.path.insert(0, '${path.join(ROOT, 'pipecat')}')
from voice_memory import VoiceMemoryPipeline
vm = VoiceMemoryPipeline(store_path='${store3}', caller_id_fn=lambda: 'py-1')
print(vm.session_start() != '')
`;
  const out = runPython(script);
  assert.strictEqual(out.trim().split('\n').pop(), 'True');
});

t('adapter: caller-id normalization collapses phone formats', () => {
  const script = `
import sys; sys.path.insert(0, '${path.join(ROOT, 'pipecat')}')
from voice_memory import normalize_caller_id, VoiceMemoryPipeline
assert normalize_caller_id('+1 (555) 123-4567') == '5551234567'
assert normalize_caller_id('15551234567') == '5551234567'
assert normalize_caller_id(' Support-Desk ') == 'support-desk'
store = '${path.join(tmpDir, 'store-py-norm.json')}'
vm = VoiceMemoryPipeline(store_path=store, caller_id_fn=lambda: '+1 (555) 123-4567')
vm.session_end('Caller: I prefer text messages.')
vm2 = VoiceMemoryPipeline(store_path=store, caller_id_fn=lambda: '5551234567')
b = vm2.get_brief()
import json
print(json.dumps({'calls': b['callCount'], 'key': b['callerId'], 'callers': len(vm2._data['callers'])}))
`;
  const out = runPython(script);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.strictEqual(r.calls, 1);
  assert.strictEqual(r.key, '5551234567');
  assert.strictEqual(r.callers, 1);
});

t('adapter: resolve_thread closes an open thread', () => {
  const script = `
import sys; sys.path.insert(0, '${path.join(ROOT, 'pipecat')}')
from voice_memory import VoiceMemoryPipeline
import json
store = '${path.join(tmpDir, 'store-py-resolve.json')}'
vm = VoiceMemoryPipeline(store_path=store, caller_id_fn=lambda: 'py-resolve')
vm.session_end('Caller: I am still waiting on the March invoice.')
closed = vm.resolve_thread('march invoice')
b = vm.get_brief()
print(json.dumps({
  'closed': bool(closed) and closed['text'].startswith('Resolved: '),
  'openLeft': len(b['openThreads']),
  'resolvedHas': any('march invoice' in r['text'].lower() for r in b['resolvedItems']),
  'miss': vm.resolve_thread('no such thread') is None,
}))
`;
  const out = runPython(script);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.strictEqual(r.closed, true);
  assert.strictEqual(r.openLeft, 0);
  assert.strictEqual(r.resolvedHas, true);
  assert.strictEqual(r.miss, true);
});

console.log('e2e: python-sdk (voice_memory package)');
function runSdkPython(script) {
  const f = path.join(tmpDir, 'sdk-' + Math.random().toString(36).slice(2) + '.py');
  const wrapped = `import sys; sys.path.insert(0, '${path.join(ROOT, 'python-sdk')}')\n` + script;
  fs.writeFileSync(f, wrapped, 'utf8');
  return execFileSync('python3', [f], { encoding: 'utf8' });
}
t('sdk: record/inject/search across instances', () => {
  const store = path.join(tmpDir, 'sdk.json');
  const out = runSdkPython(`
from voice_memory import VoiceMemory
vm = VoiceMemory(path='${store}')
vm.record_call('+1 (555) 000-1111', 'Caller: Hi, my name is Zoe. I prefer texts. My package never arrived.')
vm2 = VoiceMemory(path='${store}')
inj = vm2.build_injection('5550001111')
hits = vm2.search('problem with delivery')
import json
print(json.dumps({'inj': 'Zoe' in inj and 'texts' in inj,
                  'hit': hits and hits[0][0] == '5550001111',
                  'callers': vm2.list_callers()}))
`);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.strictEqual(r.inj, true);
  assert.strictEqual(r.hit, true);
  assert.deepStrictEqual(r.callers, ['5550001111']);
});
t('sdk: merge_callers + stale_threads + resolve_thread', () => {
  const store = path.join(tmpDir, 'sdk2.json');
  const out = runSdkPython(`
from voice_memory import VoiceMemory
import json
vm = VoiceMemory(path='${store}')
vm.record_call('aaa', 'Caller: I prefer texts. I am still waiting on the March invoice.')
vm.record_call('bbb', 'Caller: I prefer TEXTS.')
merged = vm.merge_callers('bbb', 'aaa')
closed = vm.resolve_thread('aaa', 'march invoice')
stale = vm.stale_threads('aaa', 0)  # 0 days -> everything is stale
print(json.dumps({'calls': merged['callCount'], 'gone': vm.brief('bbb') is None,
                  'closed': closed['text'].startswith('Resolved:'),
                  'openLeft': len(vm.brief('aaa')['openThreads'])}))
`);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.deepStrictEqual([r.calls, r.gone, r.closed, r.openLeft], [2, true, true, 0]);
});
t('sdk: livekit adapter session instructions + call end', () => {
  const store = path.join(tmpDir, 'sdk3.json');
  const out = runSdkPython(`
from voice_memory import VoiceMemory
from voice_memory.adapters import VoiceMemoryLiveKit
import json
mem = VoiceMemory(path='${store}')
ad = VoiceMemoryLiveKit(mem)
ad.on_call_end('lk-1', [{'role': 'user', 'content': 'I prefer morning calls.'}])
instr = ad.session_instructions('lk-1', base_instructions='Be helpful.')
print(json.dumps({'hasMem': 'morning calls' in instr, 'hasBase': 'Be helpful.' in instr,
                  'empty': ad.session_instructions('new', base_instructions='Hi.') == 'Hi.'}))
`);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.deepStrictEqual([r.hasMem, r.hasBase, r.empty], [true, true, true]);
});
t('sdk: legacy callrecall store migrates end to end', () => {
  const legacy = path.join(tmpDir, 'legacy.json');
  const dst = path.join(tmpDir, 'migrated.json');
  fs.writeFileSync(legacy, JSON.stringify({ version: 1, contacts: {
    '+15550002222': { contact_id: '+15550002222', display_name: 'Jo',
      preferences: [], facts: [], open_threads: [], resolved: [],
      call_count: 1, last_call_at: '2026-10-01T00:00:00', summary: '' },
  } }));
  const out = runSdkPython(`
from voice_memory import VoiceMemory, migrate
import json
migrate.main(['${legacy}', '${dst}'])
vm = VoiceMemory(path='${dst}')
b = vm.record_call('+1 (555) 000-2222', 'Caller: I prefer email.')
print(json.dumps({'calls': b['callCount'], 'name': b['displayName'],
                  'inj': 'email' in vm.build_injection('5550002222')}))
`);
  const r = JSON.parse(out.trim().split('\n').pop());
  assert.deepStrictEqual([r.calls, r.name, r.inj], [2, 'Jo', true]);
});

console.log(`\ne2e: ${passed} passed${process.exitCode ? ' (FAILURES)' : ''}`);
