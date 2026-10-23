# ai_usage

**Purpose:** record every OpenAI call the app makes (model, call type, tokens, cost) and report usage and cost for the AI usage panel. Each call is priced when it is recorded, at the model that made it, so the cost stays right after a switch to another model (OpenAI removes gpt-4.1-mini on 2027-01-14). Design: [docs/features/ai_usage.md](../../../../docs/features/ai_usage.md).

## API
`GET /api/ai_usage?tz=<IANA time zone>` (`tz` is required, 1–64 characters, e.g. `Asia/Kolkata`). Days and "this month" follow that zone. Answers `AiUsageReport`:

| Field | What |
|---|---|
| `this_month`, `all_time` | `UsageTotals`: `calls`, `tokens` (input + output), `cost_usd`, `unpriced_calls` |
| `by_model`, `by_call_type` | All time, one `UsageGroup` (`key` plus the totals) per model / call type, alphabetical |
| `by_day` | `DayUsage` (`day`, e.g. `"2026-10-23"`, plus the totals): today and the 29 days before, days with calls only, newest first |
| `recent_calls` | The newest 50 `RecentAiCall`s: `created_at`, `model`, `call_type`, `tokens`, `cost_usd`, `resume_intake_id`, `candidate_name` (from the linked intake; null when none is linked) |

`cost_usd` is a float (Pydantic would send a `Decimal` as a string; the sums are exact in SQL). In totals, groups and days it is always a number: the priced calls' sum, `0` when there are none (user decision, 2026-10-23). `unpriced_calls` counts the calls whose model has no price, so the panel can warn that the cost leaves them out. Only a single recent call's `cost_usd` can be null (its model has no price).

| Code | When |
|---|---|
| 422 | `tz` is missing, empty or over 64 characters; or Postgres doesn't know the zone (`{"detail": "Unknown time zone."}`). Postgres also accepts POSIX-style zones and never answers 500 for one: `UTC+5` (5 hours *west* of UTC, the POSIX way), `+25:00` and `1` all give 200. |
| 503 | The database is unavailable. |

**No access control,** like the rest of the API: fine on localhost; auth is needed before hosting.

### Service (used by other features, rule 7)
Calls are collected in a list while an upload runs, then stored with the intake (or without one if the upload fails):
- `record_ai_call(ai_calls, model, call_type, usage)`: appends an `AiCall` priced by `call_cost`; skips a response with no `usage`. Anything the API leaves out counts as 0: a token count, or the whole `input_tokens_details` object. It runs outside the upload's error handling, so it must never raise (QA found a null `input_tokens_details` cut the event stream; fixed 2026-10-23). `resume_structuring` calls it right after each OpenAI answer, before checking it, because OpenAI bills a refusal or a cut-short answer too.
- `add_ai_calls(db, ai_calls, resume_intake_id)`: links the calls and adds them to the session; the caller commits (`resume_intake.save_resume_intake`, in the intake's own commit).
- `save_ai_calls_without_intake(db, ai_calls)`: for a failed upload; rolls back, adds the calls unlinked and commits. A database error is logged (type only) and swallowed.
- `intake_cost_column(intake_id)`: a scalar subquery summing an intake's call costs (`resume_intake`'s past-interviews list uses it for `ai_cost_usd`).
- `call_cost(model, input, cached, cache_write, output) -> Decimal | None`: `((input − cached − write)·input rate + cached·cached rate + write·cache-write rate + output·output rate) / 1M`; None for a model not in `PRICES`.
- `usage_report(db, tz)`: sets the zone for the transaction (`set_config('TimeZone', tz, true)`) and runs the report queries. An unknown zone is a Postgres `DataError` (SQLSTATE 22023), raised as `UnknownTimeZoneError`.
- `AiCall` is re-exported here, so other features type their lists without importing `models.py`.

This module never imports `resume_intake` (which imports it). The router fills `candidate_name` through `resume_intake.service.list_candidate_names`.

**Prices (`PRICES`, USD per 1M tokens, from OpenAI's pricing and model pages on 2026-10-23):**

| Model | Input | Cached input | Cache write | Output |
|---|---|---|---|---|
| gpt-4.1-mini (in use) | 0.40 | 0.10 | 0.40 | 1.60 |
| gpt-5.4-mini | 0.75 | 0.075 | 0.75 | 4.50 |
| gpt-5.4-nano | 0.20 | 0.02 | 0.20 | 1.25 |
| gpt-5.6-luna | 0.20 | 0.02 | 0.25 | 1.20 |
| gpt-6-luna | 0.10 | 0.01 | 0.125 | 0.50 |

Cache writes cost 1.25× input from GPT-5.6 on; for older models they cost the input rate. The table is edited by hand when OpenAI changes a price; an edit prices new calls only.

**Known gap:** a call that gets no response (a timeout, a network error, or cut-off JSON, where the SDK raises inside `parse`) can't be recorded, so the panel can slightly undercount. OpenAI's billing dashboard is the source of truth.

**Verified (QA, 2026-10-23):** the formula matches the user's real upload by hand (intake 962, `ai_calls` 122 and 123): OCR 1,355 in / 357 out = $0.0011132; structuring 1,165 in / 371 out = $0.0010596.

**Other limits:** `created_at` is the `now()` of the transaction that stores the calls, not the moment of each call, so one upload's calls share a timestamp (`id` orders them). An intake whose calls are all unpriced shows a null `ai_cost_usd` ("—"), like an intake with no calls. With the database down, a failed upload takes about 10 s to send its 503 (the intake save and the unlinked-calls save each wait out the 5 s connect timeout).

## Data
Table `ai_calls` (migration `alembic/versions/7e1f0a3f150f_create_ai_calls_table.py`). No CV text or personal data.

| Column | Type | Notes |
|---|---|---|
| `id` | integer | Primary key, `GENERATED BY DEFAULT AS IDENTITY` |
| `created_at` | timestamptz | Server default `now()` |
| `model` | text | The model asked for (`OPENAI_MODEL`), not the response's snapshot name |
| `call_type` | varchar(11) | `structuring`, `ocr` or `ocr_retry` |
| `input_tokens`, `cached_tokens`, `cache_write_tokens`, `output_tokens` | integer | From the response's `usage`; cached and cache-write tokens are parts of the input |
| `cost_usd` | numeric, nullable | Exact; null when the model has no price |
| `resume_intake_id` | integer, nullable | FK → `resume_intakes.id`, `ON DELETE SET NULL`, indexed. Null for a failed upload |

## Tests
`backend/tests/features/ai_usage/`
- `test_service.py` (no database): the exact gpt-6-luna cost with four rates; a gpt-4.1-mini cache write costs the input rate; an unknown model costs None; `OPENAI_MODEL` is in `PRICES` (fails the day the model is switched to an unpriced one); `record_ai_call` stores tokens and cost, skips a missing `usage`, and reads a missing detail count, or a null `input_tokens_details`, as 0.
- `test_router.py` (Postgres, through `db_session`; the `usage_db` fixture deletes every AI call inside the rolled-back transaction first): the empty report with $0 costs; this month vs a call 40 days ago, and the month boundary in Asia/Tokyo; the 30-day window and days newest first; an unpriced model costs $0 with `unpriced_calls` 1 while its recent call keeps a null cost; a mixed group and an all-unpriced day; grouping by call type; Asia/Tokyo vs UTC days; recent calls with the candidate's name or null, capped at 50, the last stored first on a tie; deleting an intake keeps its calls unlinked; 422 for an unknown, missing or oversized zone; 503 with the database down. The QA pass's extra tests were merged in here (rule 5 naming).
- The recording itself is tested in `resume_structuring` and `resume_intake` (see their READMEs).
