# Demo video script (about 2 minutes)

Structure from the organisers' guide: Problem 0:00-0:15, Solution 0:15-0:35, Live demo 0:35-1:35, Business impact 1:35-2:00. Voice narration plus English subtitles. The exact prompt text is in `demo-prompts.md`; the numbers are the engine's current ones.

Pace: the narration below is about 275 words, which is roughly 115 seconds at 2.4 words per second. That leaves very little room for pauses, so if you run long, cut in the order given below. If your take runs long, cut in this order: the trace page, the conflict click, the quick Retire.

## Before you record

- Heads-up for the screen recording: "Open the generated dashboard" and "Trace this decision" open in a new browser tab, so switch to it, then come back to the first tab. The same trace page is called "Trace this dashboard" inside the dashboard's ownership panel.

- Delete the `engine/runtime/` folder so "Waiting for approval" and "Needs review" start empty. Run one throwaway request to warm Gemini, then delete the folder again.
- Only one person on the Gemini key, and no bursts: one request every few seconds. If the limit trips, the app answers with the rules and the demo prompts still give the right verdict.
- Run `.venv/bin/uvicorn engine.serve:app --port 8000`. Use a full-width browser window at 100% zoom, and wait about a second after each page loads so the charts finish animating.
- Open these tabs first: `/pipeline` and `/` (home). Have the opener prompt copied.
- The first 20 seconds should already show the product working: run footage of the finished dashboard and trace under the problem and solution narration (record the demo first, then lay the voice-over on top).

## Shot list

| Time | On screen | Narration | Subtitles (same words, two short lines) |
|---|---|---|---|
| 0:00-0:15 | Quick cuts of the finished product: the generated dashboard with its charts, then the trace page | "Dashboards pile up. Nobody knows what already exists, nobody owns them, and if a decision goes wrong, nobody can say who built it or who approved it." | Dashboards pile up. Nobody knows what exists, / nobody owns them, and nobody can say who approved what. |
| 0:15-0:35 | The pipeline page (`/pipeline`), the six steps across the top | "We built a dashboard factory that asks first: do you already have this? You type a request in plain words and get a governed decision: reuse, extend, consolidate, migrate, retire, or only then build new. With evidence, a human approval and an audit log." | A dashboard factory that asks first: / "Do you already have this?" |
| 0:35-0:50 | Paste the opener: `Can we have a new dashboard for net sales and forecast variance for Kerendia?` Press Ask. Land on the recommendation: EXTEND and the line "New dashboard derived from AST-0218. AST-0218 stays unchanged for its users." | "I ask for a Kerendia dashboard. The factory checks 360 existing assets and says: don't edit the old one. Derive a new one from it. The original stays unchanged for its users, and its owner is told." | It checks 360 existing assets. / Extend never edits: it derives, and the owner is told. |
| 0:50-1:05 | Step 6 Approve. Open the "Accountable owner" dropdown, choose "- choose a person -", press Approve: the red block "someone has to answer for the outcome". Then pick a person and press Approve | "Nothing goes live without a person. If nobody is named as accountable owner, it's blocked: someone has to answer for the outcome. Now I name one, and approve." | No owner, no approval. / Someone has to answer for the outcome. |
| 1:05-1:20 | Click "Open the generated dashboard". Two real charts. Click a red "conflicting KPI variants" line to open the explanation. Then click "Trace this decision": the six sections and "Chain intact" | "The dashboard comes straight from the data pack: two charts. And one page answers the investigator: who asked, what evidence was shown, who approved, who owns it now, in a tamper-evident audit chain." | One page for the investigator: who asked, what evidence, / who approved, who owns it. Tamper-evident. |
| 1:20-1:35 | Back on `/pipeline`. Paste `Replace the legacy Tableau Kerendia workbook` and show RETIRE and the line "the old asset keeps running". Then paste `Compare Tableau and Power BI report views and exports` and show the four "not governed" tiles on its dashboard | "Ask to replace a legacy workbook that the migration backlog marks for retirement, and it says: don't migrate waste. Retire it, with its owner's agreement. Ask for something with no governed KPI, like Tableau against Power BI usage, and it builds a first version, marked 'not governed'." | Don't migrate waste: retire it, with the owner's agreement. / No governed KPI? A first version, marked "not governed". |
| 1:35-2:00 | The home page (`/`, "Supply operations overview") for a few seconds, then the README "Why you can trust the output" section or the evaluation output | "The result: fewer duplicate dashboards, clear owners, and an audit trail. The verdicts come from rules, so the same question gets the same answer. 32 scenarios, 150 requests and 287 flow checks pass. The data is the synthetic data pack, nothing invented. Approver identity and the owner's acceptance need single sign-on: designed, not built. Build less, build better." | Same question, same answer: 32 scenarios, / 150 requests, 287 checks. Synthetic data, nothing invented. / Sign-on and owner acceptance: designed, not built. |

## The narration on its own (read straight through)

Dashboards pile up. Nobody knows what already exists, nobody owns them, and if a decision goes wrong, nobody can say who built it or who approved it.

We built a dashboard factory that asks first: do you already have this? You type a request in plain words and get a governed decision: reuse, extend, consolidate, migrate, retire, or only then build new. With evidence, a human approval and an audit log.

I ask for a Kerendia dashboard. The factory checks 360 existing assets and says: don't edit the old one. Derive a new one from it. The original stays unchanged for its users, and its owner is told.

Nothing goes live without a person. If nobody is named as accountable owner, it's blocked: someone has to answer for the outcome. Now I name one, and approve.

The dashboard comes straight from the data pack: two charts. And one page answers the investigator: who asked, what evidence was shown, who approved, who owns it now, in a tamper-evident audit chain.

Ask to replace a legacy workbook that the migration backlog marks for retirement, and it says: don't migrate waste. Retire it, with its owner's agreement. Ask for something with no governed KPI, like Tableau against Power BI usage, and it builds a first version, marked "not governed".

The result: fewer duplicate dashboards, clear owners, and an audit trail. The verdicts come from rules, so the same question gets the same answer. 32 scenarios, 150 requests and 287 flow checks pass. The data is the synthetic data pack, nothing invented. Approver identity and the owner's acceptance need single sign-on: designed, not built. Build less, build better.

## What is real and what is simulated (say it, or keep it in the video description)

- Real: the engine, the rules, the verdicts, the approval and audit flow, and the structure and ownership of the dashboards.
- Synthetic: all data, from the synthetic data pack provided for the challenge. Nothing is invented; where the pack has no data, the tile says so.
- Stand-in: the approver dropdown stands in for single sign-on. Not built: sign-on, and the named owner confirming they accept.

## Optional extras if you have 10 spare seconds

- The warnings pill: build the Xarelto migration (`Please create a Power BI replacement for our legacy Tableau Xarelto commercial workbook.`), approve it, and the dashboard shows a red pill for the inactive data model owner and the failed refresh, straight from the pack. The Kerendia opener has no warnings, so no pill appears there.
- Making the home page: `Show on-time in full, inventory coverage and units shipped for all Pharma, plus the routes at red supply risk` builds an eight-tile dashboard with the same numbers as the home page (81.6%, 6.3 weeks, 5.23M units, 28 of 87 routes, the same four worst routes). Say: "The factory can rebuild the home page from one prompt: same numbers, same calm Overview layout." Generated dashboards open in Overview, with Charts and Table one click away.
- The side-by-side comparison: `Compare the sales of Kerendia, Xarelto and Beyonttra` shows three tiles, never added into one number.
- The edge case: `Net sales for Zentrafil in Atlantis` asks which product and market instead of guessing; answer `Kerendia in Germany`.

## Video description (paste into the submission)

Analytics Dashboard Factory: an AI agent that turns a plain-language analytics request into a governed decision (reuse, extend, consolidate, migrate, retire or build new) with cited evidence, a human approval and a tamper-evident audit log. Built for the Since AI Hackathon 2026, Bayer challenge "Build Less, Build Better". All data is the synthetic data pack provided for the challenge; nothing is invented. Verdicts come from rules, with Gemini only reading the request. The approver dropdown is a stand-in for single sign-on, which is designed but not built.
