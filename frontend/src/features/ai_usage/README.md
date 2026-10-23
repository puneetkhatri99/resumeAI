# ai_usage (UI)

The AI usage panel at `#/usage` (header link "AI usage"): what the app's OpenAI calls cost. The backend half, with the API and the price table, is in `backend/app/features/ai_usage/`; the design is `docs/features/ai_usage.md`.

**What it shows (`AiUsagePanel`):**
- A title band ("AI usage and cost") saying each call is priced at the model that made it, days and months follow the browser's time zone, and calls that got no answer aren't counted (OpenAI's billing page has the final figures).
- Two totals cards, **This month** and **All time**: the cost, the number of calls and the tokens (input + output).
- Tables **By model** and **By call type** (Structuring, OCR, OCR retry), all time; **Cost per day (last 30 days)**, days with calls only, newest first; **Recent AI calls** (the newest 50): time, model, type, tokens, cost and the candidate. The candidate links to `#/history/{id}` when the upload succeeded, else shows "—".
- Costs use `formatUsd` (`src/shared/format.ts`): `$0.0044`, always 4 decimals. A total, group or day always has a cost (`$0.0000` with no calls). When some of its calls have no price (a model missing from the backend's price table), the cost covers the priced ones and an orange note says how many were left out, e.g. "+ 3 unpriced calls" (`UnpricedNote`). A single recent call with no price shows "no price". An empty table shows "No AI calls."
- "Loading…" while it loads, and an error ("Could not load the AI usage.") with "Try again", like the past-interviews page.

**Files:** `api.ts` (the report types and `getAiUsage()`, which sends `Intl.DateTimeFormat().resolvedOptions().timeZone` URL-encoded as `tz`), `AiUsagePanel.tsx`, `aiUsage.css` (layout of the cards and tables only; a wide table scrolls inside its card on a phone). Composed in `src/app/App.tsx`.

**Tests:** `AiUsagePanel.test.tsx` (Vitest, fetch stubbed): the totals of both cards, with "+ 1 unpriced call" only where there is one; `$0.0000 + 1 unpriced call` for an unpriced model; call types by name; "no price" for a single unpriced call; the candidate link and "—" with no link for a failed upload; the browser's time zone, URL-encoded, in the fetched URL; the error with "Try again". `src/shared/format.test.ts` covers `formatUsd`. No browser or e2e test (UI testing is paused).
