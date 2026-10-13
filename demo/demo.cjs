#!/usr/bin/env node
'use strict';

/**
 * Demo: caller brief written after mock call 1, injected at call 2 start,
 * and skippable flow steps derived from memory.
 *
 * Run: node demo/demo.cjs  (or: npm run demo)
 */

const fs = require('fs');
const path = require('path');
const { VoiceMemory } = require('../src/index');
const { CALL_1 } = require('./transcripts');

const STORE = path.join(__dirname, 'demo-store.json');
try { fs.unlinkSync(STORE); } catch { /* fresh */ }

const vm = new VoiceMemory({ storePath: STORE });
const CALLER = '+15551234567';

const H = '─'.repeat(62);

function banner() {
  console.log('┌' + H + '┐');
  console.log('│ voice-memory — live demo' + ' '.repeat(62 - 25) + '│');
  console.log('│ Free, self-hosted voice-agent memory · zero dependencies' + ' '.repeat(62 - 58) + '│');
  console.log('└' + H + '┘');
}

function step(n, title) {
  console.log(`\n▸ STEP ${n} — ${title}\n`);
}

/** Render a stored brief as a designed summary instead of a raw JSON dump. */
function printBrief(brief) {
  const name = brief.displayName ? ` (${brief.displayName})` : '';
  console.log(`  Caller ${brief.callerId}${name} · ${brief.callCount} call(s) recorded`);
  console.log('  Stored brief — PII redacted before anything was written:\n');
  const sections = [
    ['preferences', brief.preferences],
    ['open threads', brief.openThreads],
    ['resolved', brief.resolvedItems],
    ['facts', brief.facts],
  ];
  for (const [label, items] of sections) {
    const open = (items || []).filter((i) => !i.status || i.status === 'open');
    console.log(`  ${label} (${open.length})`);
    if (!open.length) {
      console.log('    – none –');
    } else {
      for (const it of open) {
        const when = it.source ? `  [${it.source}]` : '';
        console.log(`    · ${it.text}${when}`);
      }
    }
    console.log('');
  }
}

function printBoxed(title, text) {
  console.log(`  ┌─ ${title} ` + '─'.repeat(Math.max(0, 56 - title.length)) + '┐');
  for (const line of String(text).split('\n')) {
    const chunks = line.match(/.{1,58}/g) || [''];
    for (const c of chunks) console.log('  │ ' + c.padEnd(58) + ' │');
  }
  console.log('  └' + '─'.repeat(60) + '┘');
}

banner();

step(1, 'Call 1 ends — recording the transcript');
const brief = vm.recordCall(CALLER, { transcript: CALL_1 });
printBrief(brief);

step(2, 'Call 2 starts — injection block for the system prompt');
printBoxed('system prompt · memory block', vm.buildInjection(CALLER));

step(3, 'Flow planning — which scripted steps can be skipped?');
const steps = vm.planFlow(CALLER, [
  { id: 'greet', label: 'Greet caller', keywords: [] },
  { id: 'verify', label: 'Verify identity', keywords: ['identity verified'] },
  { id: 'callback-prefs', label: 'Ask callback preferences', keywords: ['morning callbacks'] },
  { id: 'billing-review', label: 'Review billing issue', keywords: ['duplicate charge', 'resolved'] },
]);
for (const s of steps) {
  const tag = s.skippable ? '[SKIP]' : '[ASK] ';
  console.log(`  ${tag} ${s.label}` + (s.skipReason ? ` — ${s.skipReason}` : ''));
}
console.log('\n  Open threads never skip — they need follow-up.');

step(4, 'Markdown export — the brief as a portable document');
printBoxed('brief.md', vm.exportMarkdown(CALLER));

console.log('\nDone. The store lives at demo/demo-store.json — one JSON file, human-readable.');
console.log('PII redaction is on by default; names are kept because the brief is per-caller.');
