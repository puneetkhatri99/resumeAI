# Feature design: ai_usage

> **Source of truth:** the plan the user approved on 2026-10-23 ("AI usage panel: OpenAI calls and cost, 5 models"). This document is that plan, plus the details the developer settled while building it (marked "Built:").

## Context
The user wants a panel showing AI usage and cost. OpenAI shuts gpt-4.1-mini down on 2027-01-14, so the cost must stay right after a switch to another model. Before this feature nothing recorded usage: `resume_structuring` dropped `response.usage`.

**What the user decided (2026-10-23):**
- **Price table:** 5 models, USD per 1M tokens, from OpenAI's pricing and model pages on 2026-10-23.

  | Model | Input | Cached input | Cache write | Output |
  |---|---|---|---|---|
  | gpt-4.1-mini (today) | 0.40 | 0.10 | 0.40 | 1.60 |
  | gpt-5.4-mini | 0.75 | 0.075 | 0.75 | 4.50 |
  | gpt-5.4-nano | 0.20 | 0.02 | 0.20 | 1.25 |
  | gpt-5.6-luna | 0.20 | 0.02 | 0.25 | 1.20 |
  | gpt-6-luna (OpenAI's named replacement) | 0.10 | 0.01 | 0.125 | 0.50 |

  Cache writes are charged at 1.25× input only from GPT-5.6 on. For older models a cache write costs the input rate.
- **Cost view:** actual cost only. Each call is priced at the model that made it, with no what-if comparison.
- **The panel shows:**
  - Totals for **this month and all time**: calls, tokens, cost.
  - A per-model breakdown.
  - **Cost per day** for the last 30 days.
  - A split **by call type** (structuring, OCR, OCR retry).
  - **Recent AI calls**: time, model, type, tokens, cost, and the candidate name when the upload succeeded.
  - **Cost per CV** in each row of the past-interviews page.
- **"Mini or nano, whatever works best":** which extracts CVs better needs a live test on real CVs, which needs separate permission. Both are priced, so the panel is right either way. Switching `OPENAI_MODEL` is not part of this work.

**Follow-up decisions (user, 2026-10-23, after the first build):**
- **A period or group with no calls costs $0.0000, not "—".** Totals, groups and days always carry a number (`COALESCE(SUM(cost_usd), 0)`). "—" stays only for the per-CV cost on the past-interviews page when an intake has no recorded calls (`IntakeSummary.ai_cost_usd` stays nullable).
- **A mixed priced/unpriced total shows the priced sum plus a warning.** Every total, group and day carries `unpriced_calls` (`COUNT(*) FILTER (WHERE cost_usd IS NULL)`), and the panel shows e.g. "+ 3 unpriced calls" next to the cost. This replaces "no price" for totals and groups; a single recent call with a null cost still shows "no price".

**Defaults (approved with the plan):**
- Slice `ai_usage`, table `ai_calls`, page `#/usage`, header link "AI usage".
- Money is in USD, shown to 4 decimals (a CV costs about $0.004).
- Days and "this month" follow the browser's time zone.
- Per-model and per-type totals cover all time; recent calls are the newest 50.
- Uploads made before this feature have no usage and show "—".

## Design

### Backend: slice `backend/app/features/ai_usage/`
**`models.py`: `AiCall`, table `ai_calls`**
- Columns:
  - `id` (Identity PK)
  - `created_at` (timestamptz, server default now)
  - `model` (Text)
  - `call_type` (String(11): `structuring` | `ocr` | `ocr_retry`)
  - `input_tokens`, `cached_tokens`, `cache_write_tokens`, `output_tokens` (int)
  - `cost_usd` (plain `Numeric`, exact; NULL when the model has no price)
  - `resume_intake_id` (nullable FK → `resume_intakes.id`, `ondelete="SET NULL"`, `index=True`)
- No CV text, no PII.
- `cost_usd` is computed when the call is recorded, so history keeps the price that applied then.
- Registered in `alembic/env.py`. Migration `7e1f0a3f150f_create_ai_calls_table.py` (down_revision `2349c371bb8a`), with the FK's `ondelete` and the index checked.

**`service.py`** (never imports `resume_intake`, to avoid an import cycle)
- `ModelPrice(NamedTuple)` (input, cached_input, cache_write, output as `Decimal`) and `PRICES: dict[str, ModelPrice]`: the 5 rows, with a comment giving the source and the date.
- `call_cost(model, input_tokens, cached_tokens, cache_write_tokens, output_tokens) -> Decimal | None`:
  - `((input - cached - write)·in + cached·ci + write·cw + output·out) / 1e6`. OpenAI's three input counts don't overlap; output includes reasoning tokens.
  - Returns None for a model with no price.
- `record_ai_call(ai_calls, model, call_type, usage) -> None`: appends an `AiCall`. It skips a call whose `usage` is None. Built: anything the API leaves out (the SDK reads it as None) counts as 0: a token count, or the whole `input_tokens_details` object. It runs outside the upload's error handling, so it must never raise; QA found that a null `input_tokens_details` raised `AttributeError` and cut the event stream after `extracting` (fixed 2026-10-23).
- `add_ai_calls(db, ai_calls, resume_intake_id) -> None`: sets the FK and runs `add_all`, with no commit.
- `save_ai_calls_without_intake(db, ai_calls) -> None`: for failed uploads. If there are calls, it rolls back (the session may be dirty after a failed save), adds them with no intake and commits. A `SQLAlchemyError` is logged (type only) and swallowed, so it never hides the upload's own error.
- `intake_cost_column(intake_id)`: a scalar subquery, `SUM(cost_usd) WHERE resume_intake_id = intake_id`.
- `usage_report(db, tz) -> AiUsageReport`:
  - First `SELECT set_config('TimeZone', :tz, true)` (this transaction only). Postgres rejects an unknown zone with a `DataError` (SQLSTATE 22023), which becomes `UnknownTimeZoneError`.
  - Then `date_trunc('month', now())`, `created_at::date` and `current_date` all follow the browser's zone.
  - Queries: this month, all time, group by model, group by call_type, group by day (last 30 days, days with calls only), and the newest 50 calls.
  - tokens = input + output; cost = `COALESCE(SUM(cost_usd), 0)` (the priced calls only); `unpriced_calls` = `COUNT(*) FILTER (WHERE cost_usd IS NULL)`.
  - Built: "the last 30 days" is today and the 29 days before (`day > current_date - 30`). Days are listed newest first; models and call types alphabetically; recent calls newest first (`created_at`, then `id`).

**`schemas.py`:** `UsageTotals` (calls, tokens, `cost_usd: float`, `unpriced_calls: int`), `UsageGroup(key)`, `DayUsage(day)`, `RecentAiCall` (created_at, model, call_type, tokens, `cost_usd: float | None`, resume_intake_id, candidate_name) and `AiUsageReport`. The schemas use `float`, because Pydantic would send a `Decimal` as a JSON string; the sums stay exact in SQL.

**`router.py`:** `GET /api/ai_usage?tz=<IANA zone>` (`Query(min_length=1, max_length=64)`).
- It fills `candidate_name` from `resume_intake.service.list_candidate_names(db, ids) -> dict[int, str | None]`. The router → service import is allowed by rule 7 and makes no cycle.
- `UnknownTimeZoneError` → 422 (`{"detail": "Unknown time zone."}`); `SQLAlchemyError` → 503.
- Mounted in `app/main.py`.

### Recording the calls: a collector list (a required parameter)
- `resume_structuring/service.py`:
  - `transcribe_pdf(data, ai_calls, call_type: Literal["ocr", "ocr_retry"])` and `extract_resume_data(cv_text, ai_calls)`.
  - Each calls `record_ai_call(ai_calls, OPENAI_MODEL, ..., response.usage)` right after the response arrives, before the status and refusal checks. That way an incomplete OCR answer, which OpenAI still bills, is recorded.
  - It records `OPENAI_MODEL`, not `response.model`, which can be a dated snapshot name that `PRICES` doesn't know.
- `resume_intake/service.py`:
  - `extract_text(data, ai_calls)` and `structure_resume(..., read_by_ocr, ai_calls)`; the retry passes `"ocr_retry"`.
  - `save_resume_intake(..., answers, ai_calls)` runs add → flush → `add_ai_calls(db, ai_calls, intake.id)` → commit → refresh. That is one commit, so the intake and its calls are stored together.
  - `list_resume_intakes` adds `intake_cost_column(ResumeIntake.id).label("ai_cost_usd")`.
- `resume_intake/router.py`:
  - Creates `ai_calls: list[AiCall] = []` before the `try`.
  - On every error path (415/422/502/503), once, before yielding the error event, calls `save_ai_calls_without_intake(db, ai_calls)`. A failed upload's billed calls are then still counted.
- `resume_intake/schemas.py`: `IntakeSummary.ai_cost_usd: float | None`.
- **Why a collector:**
  - Returning usage would lose an incomplete OCR answer's usage, because it raises.
  - A ContextVar doesn't survive between the SSE generator's thread-pool steps.
  - A required parameter means a forgotten argument fails loudly instead of undercounting silently.

### Frontend
- **`src/shared/format.ts`:** `formatUsd(cost: number | null)` returns "—" for null and `$0.0044` style otherwise. It is shared because 2 features use it. Built: always 4 decimals and no thousands separator, so a bigger total reads `$12.3456`.
- **`src/features/ai_usage/`:**
  - `api.ts`: types plus `getAiUsage()`, which sends `Intl.DateTimeFormat().resolvedOptions().timeZone`, URL-encoded.
  - `AiUsagePanel.tsx`: the hero band, totals cards, and plain tables for model, type, day and recent calls. A total, group or day with unpriced calls shows its cost plus "+ N unpriced call(s)"; a single recent call with a null cost shows "no price". Recent calls with an intake link to `#/history/{id}`.
  - Loading and error states follow `IntakeHistory` ("Loading…", an alert with "Try again").
  - `aiUsage.css` for the table layout only.
  - `README.md`.
- **`src/app/App.tsx`:** a `/usage` route and an "AI usage" header link. `aria-current` handles 3 pages ("New interview" is current on every page that is neither history nor usage).
- **`resume_intake`:** `api.ts` (`IntakeSummary.ai_cost_usd`) and `IntakeHistory.tsx` (shows "AI cost" with `formatUsd(item.ai_cost_usd)` per row).

### Docs (rule 3)
- This file.
- Both `ai_usage` READMEs.
- The `resume_intake` and `resume_structuring` READMEs (new parameters, recording, cost per CV).
- `docs/architecture.md`: the feature list, plus a decision row.
- `.claude/learning.md`: the user's choices, the price table and its date, the cache-write rule, and the undercount gap.

## Tests (pytest and Vitest only; no live OpenAI, no Playwright)
- **`tests/conftest.py`:** `FakeOpenAI.parse` and `.create` return a fixed `usage` (`FAKE_USAGE`: 1,000 input with 200 cached, 300 output, so `FAKE_CALL_COST` = $0.00082 at gpt-4.1-mini prices).
- **Existing tests that change:**
  - `[]` (or `[], "ocr"`) at about 45 call sites: `resume_structuring/test_service.py`, `resume_intake/test_service.py`, `resume_generation/test_router.py` (`save_resume_intake`).
  - `FailingCommitSession` gets `flush`, `add_all` and `rollback`.
  - The keepalive test's `slow_parse` sleeps, then returns `fake_openai.parse(**kwargs)`.
  - `test_list_is_newest_first_and_pages_by_id` expects `"ai_cost_usd"`.
- **`tests/features/ai_usage/test_service.py`:**
  - The exact `call_cost` for gpt-6-luna (four different rates).
  - A gpt-4.1-mini cache write costs the input rate.
  - An unknown model → None.
  - **`assert OPENAI_MODEL in PRICES`**: it fails the day someone switches to an unpriced model.
  - A `None` usage is skipped; a missing detail count counts as 0; a null `input_tokens_details` counts as 0 cached and 0 cache-write tokens (the QA bug).
- **`tests/features/ai_usage/test_router.py`** (all with `db_session`; a fixture starts with `delete(AiCall)` inside the rolled-back transaction). The QA pass's 7 extra tests (first in `test_report.py`) were merged here, to keep the `test_<module>.py` naming (rule 5):
  - No calls: zero totals with a $0 cost, empty lists.
  - This month vs a call from 40 days ago; the month boundary in Asia/Tokyo (a minute before and after); the 30-day window (29 days ago is in, 30 is out); days newest first.
  - A model with no price costs $0 with `unpriced_calls` 1, and its single recent call keeps a null cost; a mixed group sums the priced calls and counts the unpriced one; a day of only unpriced calls costs $0 and counts them.
  - Grouping by call type.
  - Recent calls are the newest 50; calls stored in one transaction list the last stored first; deleting an intake keeps its calls, unlinked.
  - A call at 20:00 UTC yesterday is the next day in Asia/Tokyo (the expected dates come from the database's UTC date, so the test can't flake near midnight).
  - Recent calls carry the name, or null when no intake is linked.
  - A bad tz → 422; a missing or badly sized tz → 422; DB down → 503.
- **Recording:**
  - Structuring: an incomplete OCR answer and a refusal are recorded; a call with no answer isn't.
  - The retry records `structuring`, `ocr_retry`, `structuring`; a scan records `ocr`.
  - Router: a success links the calls to the intake.
  - A scan whose OCR comes back `incomplete` → 502 and one unlinked `ocr` row; a timeout stores no row.
  - With the database down, the unlinked save fails, is logged by type, and the 503 still comes.
  - The list's cost per CV is the sum of its calls, or null when there are none.
- **Vitest:**
  - `AiUsagePanel.test.tsx`: totals with the "+ 1 unpriced call" warning, $0 plus the warning for an unpriced model, "no price" for a single unpriced call, the zone in the fetched URL, the error state.
  - `IntakeHistory.test.tsx` fixtures get `ai_cost_usd`; check "$0.0044" and "—".
  - `src/shared/format.test.ts` for `formatUsd`.

## Verification
1. From `backend/`:
   - `.venv\Scripts\python.exe -m alembic upgrade head`
   - `-m pytest`
   - `-m ruff check .`
   - `-m ruff format --check .`
2. **Leak check:** after the full pytest run, `ai_calls` in the dev database still has 0 rows (read-only count). Every router test that reaches the fake AI uses `db_session`; the DB-down tests use fake sessions.
3. From `frontend/`: `npm run lint`, `npm run build`, `npm test`.
4. **Tester API pass:** use the `:8001` server with the fake `OPENAI_BASE_URL` (see learning.md).
   - `GET /api/ai_usage?tz=Asia/Kolkata` returns the right shape, and a bad tz gives 422.
   - A failed upload adds an unlinked row only when OpenAI answered. With the fake base URL every call 404s, so no row is added; that confirms the "no response, no row" gap.
   - Clean up by id any rows it creates.
5. No live OpenAI requests and no Playwright. The user can look at `#/usage` in their browser after the next real upload.

**What the QA pass found (2026-10-23):**
- **Real costs match by hand.** The user's real upload (intake 962, `ai_calls` 122 and 123): OCR 1,355 in / 357 out = $0.0011132; structuring 1,165 in / 371 out = $0.0010596.
- **A fake OpenAI server for real-HTTP checks:** a stdlib `http.server` on `:8002` with a `POST /control` queue of behaviours, and the backend started with `OPENAI_BASE_URL=http://127.0.0.1:8002/v1`. The scripts are `au_fake_openai.py`, `au_lib.py` and `au_cleanup.py` in that session's scratchpad. **Trap:** a handler method named `responses` shadows `BaseHTTPRequestHandler.responses`, and every reply comes back empty.
- **A safe database-down check:** a throwaway backend whose `DATABASE_URL` points at a closed `127.0.0.1` port answers 503 in about 5 s without touching Postgres.
- **The bug it found** (a null `input_tokens_details` cut the stream) is fixed; see `record_ai_call` above.

## Known limits
- **Undercount:** a call with no response can't be recorded: a timeout, a network error, or cut-off JSON (the SDK raises inside `parse`). The panel can slightly undercount; OpenAI's billing dashboard is the source of truth.
- **Prices are edited by hand.** OpenAI changes prices sometimes; an edit to `PRICES` prices new calls only.
- **Switching models:** the 4 newer models are reasoning models. Switching `OPENAI_MODEL` may need code changes (e.g. `temperature=0` may be rejected) and a live check. That is separate from this panel. `test_the_model_in_use_has_a_price` fails if the new model isn't in `PRICES`.
- **Unpriced calls cost $0 in totals.** A total over priced and unpriced calls sums only the priced ones; `unpriced_calls` and the panel's "+ N unpriced calls" warning say how many were left out.
- **Per-CV cost:** an intake whose calls are all unpriced also shows "—" on the past-interviews page (its `SUM` is null, like an intake with no calls). Only an unpriced `OPENAI_MODEL` causes that, and `test_the_model_in_use_has_a_price` guards against it.
- **A call's time is the save's time:** `created_at` is the `now()` of the transaction that stores the calls (the intake save, or the failed upload's save), not the moment of the call. All calls of one upload share a timestamp; `id` orders them.
- **Time zones:** Postgres also accepts POSIX-style zone strings, not only IANA names, and never answers 500 for one: `UTC+5` (which POSIX reads as 5 hours *west* of UTC), `+25:00` and `1` all give 200. Only a string it rejects is a 422.
- **Database down:** a failed upload takes about 10 s to send its 503, because two saves each wait out the 5 s connect timeout (the intake, then its unlinked calls).
- **No access control,** like the rest of the API: fine on localhost; auth is needed before hosting.
