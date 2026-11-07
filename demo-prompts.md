# Demo prompts

Copy and paste each prompt into the box on http://localhost:8000/pipeline. Each was run through the real pipeline and gives the verdict shown (Gemini on, 7 Nov 2026).

Before recording: delete the `engine/runtime/` folder for a clean "Waiting for approval" and "Needs review" list, then run the prompts in this order.

## Main flow

**1. EXTEND: the opener (show it end to end)**
```
Can we have a new dashboard for net sales and forecast variance for Kerendia?
```
Point at: the evidence, "New dashboard derived from AST-0218, which stays unchanged for its users", then approve. Try approving with "— choose a person —" as accountable owner to show the block, then pick one, approve, open the generated dashboard, and show the trace.

**2. RETIRE: the best dashboard is the one you don't build**
```
Replace the legacy Tableau Kerendia workbook
```
Point at: "Do not migrate waste" (RULE-006). The owner is asked and the old asset keeps running. Approving shows "Retirement approved" and no dashboard is built.

**3. NEW BUILD: no governed KPI yet, but the pack has the data**
```
Compare Tableau and Power BI report views and exports
```
Point at: "Not a governed KPI yet, but the data pack holds the data. A first working version is built now and marked not governed until an owner defines the KPI." Approve it and open the dashboard: four tiles, Power BI against Tableau (report views and exports), and each chip says "no governed KPI yet" / "not governed". It also ties into the migration story: how much is each platform really used?


## More requests (one per remaining verdict)

**EXTEND: a comparison shown side by side**
```
Compare the sales of Kerendia, Xarelto and Beyonttra
```
Point at: "Compare" means side by side, so there is one tile per product, not one added-up number. Below the tiles, a Comparison chart draws all three products in one chart, one colour each; the Indexed button starts every line at 100 so the movement can be compared even when the levels differ. The engine finds a dashboard that covers only Xarelto (AST-0191) and says plainly it is not a full match: it builds a new dashboard derived from it that also covers the other two, and leaves the original unchanged. Only the Xarelto tile says "existing on AST-0191". Gives the same result with Gemini on or off.

**NEW BUILD: the factory rebuilds the home page's numbers ("Supply operations overview")**
```
Show on-time in full, inventory coverage and units shipped for all Pharma, plus the routes at red supply risk
```
Point at: "The factory can rebuild the home page from one prompt: the numbers match exactly, in the same calm Overview layout." Approve it and open the dashboard: eight tiles. The three number tiles (on-time in full 81.6%, inventory coverage 6.3 weeks, units shipped 5.23M), the red-routes count (28 of 87 routes, with bars per product), and the four worst red routes (Beyonttra · Japan 65.2%, Xarelto · Japan 69.7%, Xarelto · Sweden 70.0%, Beyonttra · United Kingdom 70.1%) with the dashed portfolio average at 81.6. All from the data pack, the same as the home page. Gives the same result with Gemini on or off.

**NEW BUILD: supply risk on its own**
```
Which routes are at red supply risk this month?
```
```
Show the red supply risk routes for Kerendia
```
The first gives the red-routes count plus the worst red routes for the whole portfolio. The second is scoped to Kerendia (4 of 14 routes, bars per market).

**EXTEND: a whole therapeutic area**
```
Show net sales and forecast variance for cardiovascular medicines
```
Point at: "Cardiovascular" is a group of products the pack knows (Kerendia, Xarelto, Beyonttra), so the factory builds one tile per product, plus the side-by-side Comparison chart: six tiles for two measures. A bare "Show a dashboard with the data for cardiovascular medicines" asks one question, which measure, on purpose.

**NEW BUILD, more working examples (all from the pack, all marked not governed)**
```
Show report views and query errors for Kerendia reports
```
```
I need a dashboard of report load times and query errors
```
```
How many people subscribe to our Nubeqa reports?
```

**REUSE: nothing to build**
```
Reimbursement status coverage and market access coverage for Nubeqa in France
```

**CONSOLIDATE: a proposal only, within one market**
```
What should we do with the Tableau workbook AST-0183?
```

**MIGRATE: rebuild on a shared model, not 1:1**
```
Please create a Power BI replacement for our legacy Tableau Xarelto commercial workbook.
```
This one also shows the inactive data model owner and the failed refresh on the generated dashboard.

## Edge cases: it never invents data

**A measure the pack has no data for: it says so and invents nothing**
```
Build a dashboard for employee attrition and headcount by department
```
Point at: no governed KPI and no data in the pack, so the generated dashboard shows "No data in the pack" tiles that explain why, instead of made-up numbers. It shows the factory works beyond manufacturing and pharma.

**It asks instead of guessing**

```
Net sales for Zentrafil in Atlantis
```
Zentrafil and Atlantis are fictional on purpose. Approval is blocked until the question is answered. Then paste this answer into the "Your answer" box:
```
Kerendia in Germany
```
The request is rebuilt as "Net sales for Kerendia in Germany" and gives EXTEND.
