'use strict';

/** Mock transcripts for the demo: two calls from the same caller. */

const CALL_1 = `
Agent: Hi, thanks for calling Northwind support. May I have your name?
Caller: Hi, my name is Maya. I'm calling about my bill.
Agent: Sure Maya, I can help with that. What's the issue?
Caller: I was charged twice for last month. My email is maya.chen@example.com if you need it.
Agent: I see the duplicate charge. I'll reverse the second one now.
Caller: Great, that's resolved. Also, I prefer morning callbacks if you ever need to reach me — please never call after 6pm.
Agent: Noted. Anything else?
Caller: One more thing — I'm still waiting on the insurance quote we discussed last week. Can you follow up on that?
Agent: I'll follow up on the insurance quote and get back to you.
Caller: Thanks, that's all.
`.trim();

const CALL_2_TRANSCRIPT_BEFORE_INJECTION = `
Agent: Hi Maya, welcome back to Northwind support. How can I help today?
Caller: Just checking whether there's any update on the insurance quote.
`.trim();

module.exports = { CALL_1, CALL_2_TRANSCRIPT_BEFORE_INJECTION };
