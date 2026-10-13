# REDESIGN-AUDIT — voice-memory (2026-10-13)

## Verdict: N/A-for-app-redesign

voice-memory is a **Node.js library** (`main: src/index.js`, zero
dependencies). It has **no web UI and no web-facing surface**: the demo
(`demo/demo.cjs`) is a console script, the Pipecat adapter is Python-side
middleware, and distribution is via npm. Per the sweep's scope rule, no web
app is invented where none existed.

Competitors researched (developer-tool demo/docs presentation):
1. **Twilio** — tutorial-first docs: "In five minutes you will…" →
   prerequisites → numbered steps with runnable code → verify → where-next.
   Copy-paste code samples are central; the use case is named before the
   implementation.
2. **Stripe** — three-panel reference layout, persistent language selection,
   minimal request first with optional parameters revealed progressively.
3. **Retell AI** — the paid product this library undercuts; its demo value is
   "watch memory work across two calls", which the console demo already
   demonstrates.

## Gaps found (docs/demo presentation)
1. The console demo dumped the entire stored brief as raw
   `JSON.stringify(brief, null, 2)` — ~50 lines of nested objects with
   timestamps. A developer evaluating the library in five minutes sees noise,
   not the product story (Twilio's "name the use case first" violated).
2. No step framing: the demo printed `=== CALL 1 ends ===` headers but never
   explained what the viewer should notice (PII redaction, the injection
   block, skip logic).
3. The demo file header said `Run: node demo/demo.mjs` — wrong filename
   (it's `demo.cjs`), a small papercut for first-run.
4. README had no pointer to the runnable demo.

## Changes made (in scope)
1. `demo/demo.cjs` rewritten as a designed console presentation:
   - Banner naming the product and its promise.
   - Numbered steps (record → inject → plan flow → export) with one-line
     "what to notice" framing each.
   - The stored brief renders as grouped sections
     (preferences / open threads / resolved / facts) with source tags —
     no raw JSON.
   - The injection block and Markdown export render inside bordered panels.
   - Fixed the run-command comment (`node demo/demo.cjs`, `npm run demo`).
2. README: added a "Demo" section (`npm run demo`) describing the four
   steps, so the Twilio-style five-minute path is explicit.

## Out of scope (deliberately not built)
- A browser-based interactive demo page. That would be a new web app, not a
  redesign of an existing surface. If Alex wants one, the sibling project
  `callrecall-ai/demo/index.html` is the template.

## Regression contract
- `npm test` (smoke + e2e) green; demo still exercises recordCall,
  buildInjection, planFlow, exportMarkdown.
