#!/usr/bin/env node
'use strict';

/**
 * Demo: caller brief written after mock call 1, injected at call 2 start,
 * and skippable flow steps derived from memory.
 *
 * Run: node demo/demo.mjs
 */

const fs = require('fs');
const path = require('path');
const { VoiceMemory } = require('../src/index');
const { CALL_1 } = require('./transcripts');

const STORE = path.join(__dirname, 'demo-store.json');
try { fs.unlinkSync(STORE); } catch { /* fresh */ }

const vm = new VoiceMemory({ storePath: STORE });
const CALLER = '+15551234567';

console.log('=== CALL 1 ends — recording transcript ===\n');
const brief = vm.recordCall(CALLER, { transcript: CALL_1 });
console.log('Stored brief (PII redacted — note the email):');
console.log(JSON.stringify(brief, null, 2));

console.log('\n=== CALL 2 starts — injection block for the system prompt ===\n');
console.log(vm.buildInjection(CALLER));

console.log('\n=== Flow planning — which scripted steps can be skipped? ===\n');
const steps = vm.planFlow(CALLER, [
  { id: 'greet', label: 'Greet caller', keywords: [] },
  { id: 'verify', label: 'Verify identity', keywords: ['identity verified'] },
  { id: 'callback-prefs', label: 'Ask callback preferences', keywords: ['morning callbacks'] },
  { id: 'billing-review', label: 'Review billing issue', keywords: ['duplicate charge', 'resolved'] },
]);
for (const s of steps) {
  console.log(`${s.skippable ? '[SKIP]' : '[ASK] '} ${s.label}` +
    (s.skipReason ? ` — ${s.skipReason}` : ''));
}

console.log('\n=== Markdown export ===\n');
console.log(vm.exportMarkdown(CALLER));
