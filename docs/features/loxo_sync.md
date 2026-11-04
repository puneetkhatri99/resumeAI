# loxo_sync: fetch Endorsed candidates from Loxo every 10 minutes

Status: **design, blocked on the open questions (section 2) and on one read-only live check (section 2, Q1).** Nothing here is approved or built.

## 1. Goal
The user: "I want to create a cron that runs every 10 minutes and fetches the candidates from the Loxo ATS endorsed stage. I have a token and agency slug. [...] see how we can fetch the candidates' resumes from the endorsed stage." This is step 1 of the MinistryHub flow in `.claude/learning.md` (2026-10-22, "The real project is MinistryHub branded resumes"): when a candidate is moved to **Endorsed** in Loxo, the service fetches the original resume, AI structures it, and a branded MinistryHub PDF is made. Every 10 minutes the backend asks Loxo which people currently sit in the Endorsed stage, and for each one it hasn't handled yet, downloads their resume and runs it through the existing pipeline (CV → AI → branded PDF → Neon storage → a row on the candidate page). What happens afterwards (upload back to Loxo, stage moves) isn't stated, so it is an open question.

## 0. Loxo API research (all from https://loxo.readme.io/llms.txt and the `.md` form of each reference page, read 2026-11-04)
The reference is an OpenAPI spec where **almost every response schema is empty (`{}`)**. Paths, methods and query parameters are documented; response field names mostly are not. Anything marked "undocumented" below needs the live check (Q1).

| Topic | What the docs say | Source |
|---|---|---|
| Base URL | Server `https://{domain}/api`, `domain` default `app.loxo.co`, "domain assigned by Loxo". Every path starts with `/{agency_slug}/...`, so a call is `https://app.loxo.co/api/{agency_slug}/workflow_stages`. | every reference page, e.g. https://loxo.readme.io/reference/workflow_stagesindex.md |
| Auth | "All endpoints use bearer token authentication. Generate one in Settings > API Keys." Scheme `loxo_bearer_token`, `type: http, scheme: bearer`, so `Authorization: Bearer <token>`. | https://loxo.readme.io/reference/loxo-api.md |
| Pipeline model | A **workflow** is "pipeline stage configurations for jobs"; **workflow stages** belong to a workflow; a **candidate** is "a person entry in a job's hiring pipeline" and its id "is not a person ID". | https://loxo.readme.io/reference/workflowsindex.md, https://loxo.readme.io/reference/candidatesshow.md |
| Find "Endorsed" | `GET /{agency_slug}/workflow_stages`, optional `workflow_id`. No name filter; the response fields (id, name, ...) are undocumented. So: list all stages, match the name in code. Several workflows may each have their own "Endorsed" stage (different ids): undocumented. | https://loxo.readme.io/reference/workflow_stagesindex.md |
| Candidates in a stage, across all jobs | `GET /{agency_slug}/candidates` with `workflow_stage_ids[]` ("Current candidate workflow stage IDs"), `per_page`, `scroll_id` ("A cursor used to retrieve the next page of results. Returned in each response."), `query` (Lucene), `job_status_ids[]`, `exclude_rejected`, owner filters. "Results include each candidate's person, job, and current workflow stage." One call (plus pages) covers every job. | https://loxo.readme.io/reference/candidatesindex.md |
| Candidates per job | `GET /{agency_slug}/jobs/{job_id}/candidates` (`per_page`, `scroll_id`, `query`, `activity_type_id`, `person_id`); no stage filter. Not needed. | https://loxo.readme.io/reference/candidatesjob_index.md |
| "Newly moved into Endorsed" | **No `updated_since`-style filter on candidates, and no documented stage-change timestamp.** `GET /{agency_slug}/person_events` has `created_at_start`/`created_at_end`/`after`/`before`, `activity_type_ids[]`, `job_ids[]`, `person_id`, `created_at_sort`, but it isn't documented whether a stage move creates a person event. So we detect "new" ourselves: current Endorsed list minus the people we already handled. | https://loxo.readme.io/reference/person_eventsindex.md |
| A person's resumes | `GET /{agency_slug}/people/{person_id}/resumes`. "Each entry includes download_url / download_url_expires_at (a short-lived S3 URL for the original file)." Other fields (id, file name, created date) undocumented. | https://loxo.readme.io/reference/resumesindex.md |
| Resume text | `GET /{agency_slug}/people/{person_id}/resumes/{id}`: Loxo's own extracted text, "reports whether the extracted text was truncated", also returns `download_url`. Not used: we read the original file ourselves. | https://loxo.readme.io/reference/resumesshow.md |
| Download the file | `GET /{agency_slug}/people/{person_id}/resumes/{resume_id}/download`: "Download the original resume file bytes for a person." The spec declares `application/json` with an empty schema, which contradicts "bytes". The page says "Prefer the download_url ... unless the raw bytes must come through this API." File formats: undocumented. | https://loxo.readme.io/reference/resumesdownload.md |
| Person | `GET /{agency_slug}/people/{id}`: "Nested resumes and documents include download_url / download_url_expires_at". | https://loxo.readme.io/reference/peopleshow.md |
| Pagination | Cursor (`scroll_id` + `per_page`) on candidates and person events; `page` + `per_page` on jobs ("Paginating past 10000 total results via page/per_page is not supported"). Max `per_page`, the response key holding the rows, and how the last page is signalled: undocumented. | candidatesindex, https://loxo.readme.io/reference/jobsindex.md |
| Rate limits | **Undocumented.** A third-party guide (100Hires, a competitor, July 2026) also found no published number and advises backoff on 429/5xx. | https://100hires.com/loxo-api.html |
| Error format | **Undocumented** (only `200 success` is listed on every page). | all reference pages |
| Webhooks (alternative to polling; reported, not chosen) | `POST /{agency_slug}/webhooks` with `webhook[item_type]` (`candidate`, `person`, `person_event`, `resume`, ... 16 values), `webhook[action]` (`create`/`update`/`destroy`), `webhook[endpoint_url]`. "API-key authentication only". **Payload format, signing/secret and retry policy are undocumented.** | https://loxo.readme.io/reference/webhookscreate.md, https://loxo.readme.io/reference/webhooksindex.md |
| Upload back to Loxo (reported only) | `POST /{agency_slug}/people/{person_id}/resumes` ("add or replace a candidate resume", multipart `document` or JSON `document_base64` + `name`, `source_type_id`, `skip_parse`). `POST /{agency_slug}/people/{person_id}/documents` ("Attach a document file to a person", same fields). No visibility or document-type field is documented on either. | https://loxo.readme.io/reference/resumescreate.md, https://loxo.readme.io/reference/person_documentscreate.md |
| Change a candidate's stage | `PUT /{agency_slug}/jobs/{job_id}/candidates/{id}` documents only `csr_highlight` and `highlights`: **no documented way to move a stage.** | https://loxo.readme.io/reference/candidatesupdate.md |

## 2. Open questions
The design below is complete for everything else; each answer changes only the part named. My recommendation is in **bold**.

1. **Read-only live check (blocks the developer).** The response field names (stage `id`/`name`, the candidate row's person id, the `scroll_id` location, the rows' key, the resume entry's `id`/`download_url`/date) are undocumented. May the main session make one read-only check with the user's token? Proposed calls, all GET, nothing written: `/users/me` (token works), `/workflow_stages` (find "Endorsed"; how many), `/candidates?workflow_stage_ids[]=<id>&per_page=2` (row shape, cursor, total), `/people/{one id}/resumes` (entry shape). Print **key names, types and counts only, never values** (they are PII). Optionally one download of that resume's `download_url` to confirm it is the file's bytes (saved to the scratchpad, deleted after). **Recommend: yes, all GETs plus the one download.**
2. **Scheduler.** (a) **In-process**: a daemon thread started by the `lifespan` in `app/main.py`, run, sleep 600 s, repeat. No new service, no cost, no new package. Needs the Render web service to stay awake: a Free instance "spins down ... after 15 minutes without receiving any inbound traffic" (https://render.com/docs/free), and then nothing runs. (b) **Render Cron Job** `*/10 * * * *` (UTC) from the same Dockerfile with the command `python -m app.features.loxo_sync`: Render "guarantees that at most one run of a given cron job is active", min $1/month plus run time, needs every env var copied (an environment group), pulls the image on each run (https://render.com/docs/cronjobs). (c) **External cron** (e.g. cron-job.org) calling a new key-protected `POST /api/loxo_sync/run`: a new endpoint, a new secret, a third-party service, and the request must hand off to a background thread because a run can take minutes. **Recommend (a) if the Render web service is on a paid, always-on plan; (b) if it is Free.** Which plan is it on?
3. **What happens after a candidate is processed?** (a) **Nothing: the branded PDF shows on our candidate page (source "Loxo")**; (b) upload the PDF to the person as a document (`POST .../people/{id}/documents`); (c) upload it as a resume (`POST .../people/{id}/resumes`; risk: the next run could pick our own PDF as "the resume", see Q5); (d) move the stage: not possible, the API documents no stage field (section 0). **Recommend (a) now; (b) as the next feature once the user confirms with MinistryHub how the PDF should appear in Loxo.** The proposal's "archive the original and hide it from clients" isn't possible from the documented API either (no visibility field).
4. **What makes a candidate "already handled"?** (a) **Each Loxo person once, ever** (key: person id). A person endorsed for two jobs, or re-entering Endorsed, isn't processed again; a later new resume isn't either. Cheapest: a run costs only the list pages plus 2 calls per new person. (b) Each (person, resume) once: a new resume is processed again, but every run then calls `/people/{id}/resumes` for **every** person in Endorsed (the stage only grows), with no documented rate limit. (c) Each Loxo candidate (person-in-job) once: two jobs give two PDFs and two AI costs. **Recommend (a).**
5. **Which resume when a person has several?** (a) **The newest** (by the entry's date or highest id, whichever the live check shows); (b) the first in Loxo's order; (c) every one. **Recommend (a).** A person with no resume: **not recorded; checked again on the next run** (one call per run each). Alternative: record it as failed and never check again.
6. **First run (backfill):** people already sitting in Endorsed when the sync is switched on. (a) Process all of them (AI cost about $0.0005-0.002 each, capped by the $5 spending cap; one run may take long: about 10-30 s per CV); (b) **record them as skipped on the first run and only process people who arrive later.** The live check (Q1) gives the count. **Recommend: decide after the count; (b) if it is over about 50.** (b) needs one more status value, `skipped`.
7. **Which jobs?** (a) **Endorsed in every job**; (b) only jobs with certain statuses (`job_status_ids[]`, e.g. active); (c) a list of job ids. **Recommend (a).**
8. **Which stage(s) is "Endorsed"?** Match stage names in code, exactly "Endorsed" ignoring case and surrounding spaces, **all matching stages across all workflows** (recommend), or a fixed list of stage ids in an env var.
9. **Retries.** A failure that may pass later (Loxo or storage down, AI call failed, Chromium failed, database down) is retried on the next runs. (a) **Up to 3 attempts, then it stays failed**; (b) forever. A CV that can't work (wrong file type, no name/email, over 4 pages, too long) **fails at once, never retried** (recommend: retrying would pay OpenAI again for the same answer).
10. **Where does a recruiter see failures?** The proposal mentions "a review list". (a) **Nowhere for now: the server log and the `loxo_candidates` table (failures are rare; ask again when they happen)**; (b) a small admin page listing failed Loxo people with the reason. **Recommend (a).**
11. **The `source` value and label** for these candidates: **`loxo`, shown as "Loxo"** (fits `varchar(6)`, no migration of `candidates`).
12. **Loxo domain.** Is the agency on `app.loxo.co` (the default) or its own domain? **Recommend a code constant `https://app.loxo.co/api`; add a setting only if the answer is another domain.**
13. **Consent and PII.** Until now a recruiter chose each CV sent to OpenAI and Neon. This sends every Endorsed person's CV automatically, and the branded PDF goes to the **public** bucket with a permanent, unguessable URL (the existing candidates decision). Is that OK with MinistryHub? **Recommend: the user confirms with the client; the design changes nothing about storage.**

## 3. Flow
One **run** (the same code whatever the scheduler, Q2):

1. **Enabled?** If `LOXO_API_TOKEN` or `LOXO_AGENCY_SLUG` is unset or empty, the run does nothing (in-process: the thread isn't started; logged once at startup "Loxo sync off"). Same pattern as `PUBLIC_API_KEY` unset.
2. **One run at a time.** Open a dedicated connection `engine.connect()` and `SELECT pg_try_advisory_lock(LOXO_SYNC_LOCK_ID)`. False: log "Loxo sync skipped: another run is active" and end. The lock is per connection, so it is released when the run ends or the process dies. (The work itself uses a separate `SessionLocal()`, because a session can switch pooled connections between commits.) This covers both instances during a Render deploy, and a run longer than 10 minutes. Second guard: the unique `loxo_person_id` (step 5e).
3. **Find the stage ids.** `GET /workflow_stages`; keep every stage whose name matches "Endorsed" (Q8). None: log an error "No Endorsed stage in Loxo" and end.
4. **List Endorsed people.** `GET /candidates?workflow_stage_ids[]=<id>...&per_page=LOXO_PAGE_SIZE` (100, or Loxo's max per the live check), following `scroll_id` until a page has no rows. Collect the distinct person ids in Loxo's order. One DB query then drops the ids that already have a row with status `done` or `failed` (or `skipped`, Q6). What's left: new people, and people with status `retry`.
5. **For each remaining person, one at a time:**
   1. **Spend cap:** `ai_spend_cap_reached(db)` (ai_usage). Reached: log a warning "AI spending cap reached; Loxo sync paused" and end the run (no attempt counted; the next run checks again).
   2. **Pick the resume:** `GET /people/{person_id}/resumes`; choose one (Q5). None: skip (Q5) and go to the next person.
   3. **Download:** `download_cv(download_url)` from `public_api.service` (https only, public IPv4, pinned IP, no redirects, 10 MB, 30 s). Reused rather than written again; the presigned S3 URL needs no Loxo token, so the token never leaves `app.loxo.co`. (If the live check shows `download_url` missing or redirecting, the fallback is the `/download` endpoint through our Loxo client.)
   4. **Process:** `extract_text` and `structure_resume` (resume_intake), `render_pdf(build_resume_html(resume, date.today()))` (resume_generation): exactly the steps of `POST /api/public/pdf`, collecting `ai_calls`.
   5. **Save:** add the person's `LoxoCandidate` row (new, or the existing `retry` row) to the session with `status="done"`, `attempts += 1`, `last_error=None`, then call `store_candidate(db, data, file_type, pdf, resume.name, "loxo", ai_calls)`. Its one commit writes the candidate row, the AI calls and our row together, so a crash can never leave a stored candidate that the next run processes again. Then set `row.candidate_id = candidate.id` and commit (a crash between the two commits leaves a `done` row with a null `candidate_id`: no duplicate, only a missing link).
   6. **On error:** `db.rollback()`; then in one commit write the row (`attempts += 1`, `last_error` = our message, status by section 8) and the answered AI calls unlinked (`add_ai_calls(db, ai_calls, None)`), so paid calls still count toward the cap. Go on with the next person, unless the error says Loxo itself is unusable (401, 403, 429): then end the run.
6. Release the lock (close the connection). Log one summary line: counts of listed, new, done, failed, retry, skipped-no-resume. **No names, emails or URLs.**

A web recruiter sees the result on the candidate page (`#/candidates`): a new row with source "Loxo", its original and branded PDF links, and its AI cost, exactly like other sources.

## 4. API
**No new HTTP endpoint** (with scheduler (a) or (b)). With scheduler (c) only, one endpoint would be added; it is not designed until Q2 picks it.

Outbound calls to Loxo (all `GET`, `Authorization: Bearer <LOXO_API_TOKEN>`, base `https://app.loxo.co/api/{LOXO_AGENCY_SLUG}`):

| Call | Used for |
|---|---|
| `GET /workflow_stages` | find the Endorsed stage ids (once per run) |
| `GET /candidates?workflow_stage_ids[]=..&per_page=..[&scroll_id=..]` | the Endorsed list, all pages |
| `GET /people/{person_id}/resumes` | the resume to process and its `download_url` (per new person) |
| `GET <download_url>` (S3, no token) | the file, via `public_api.service.download_cv` |

**Loxo client** (`loxo_sync/service.py`): one module-level `httpx.Client` (`httpx==0.28.1` is already pinned) with the base URL, the bearer header, `timeout=httpx.Timeout(10, read=30)`, `follow_redirects=False`, `trust_env=False`, built only when the settings are set. No retries inside a run (the next run is the retry). Every Loxo response is parsed with small Pydantic models in `schemas.py` (fields from the live check), so an unexpected shape is a clear `LoxoError`, not a `KeyError`.

| Loxo answer | Handling |
|---|---|
| 200 with the expected shape | go on |
| 401, 403 | `LoxoError("Loxo rejected the API token.")`; end the run |
| 429 | `LoxoError("Loxo rate limit reached.")`; end the run (the next run continues; `Retry-After` is ignored: 10 minutes is longer than any sane value) |
| 404 on a person's resumes | that person: `retry` (the person may have been merged or deleted) |
| other 4xx/5xx, timeout, network error, invalid JSON or shape | during steps 3-4: end the run; during step 5: that person `retry` |

Error messages carry the HTTP status and the path template only (`"Loxo answered 500 for /people/{id}/resumes."`), never the response body (it can hold PII) nor the token.

## 5. Data
New table **`loxo_candidates`**: one row per Loxo person the sync has tried.

| Column | Type | Constraints | Meaning |
|---|---|---|---|
| `id` | `integer` | PK, identity | |
| `created_at` | `timestamptz` | not null, default `now()` | first attempt |
| `updated_at` | `timestamptz` | not null, default `now()`, set to `now()` on every update | last attempt |
| `loxo_person_id` | `bigint` | not null, **unique** | Loxo's person id (the dedup key, Q4) |
| `loxo_resume_id` | `bigint` | null | the resume used in the last attempt (null when the failure came before picking one) |
| `status` | `varchar(7)` | not null, CHECK in (`done`, `retry`, `failed`) (+ `skipped` if Q6 = b) | |
| `attempts` | `integer` | not null, default 0 | |
| `last_error` | `text` | null | our own message for the last failure; never Loxo's response body |
| `candidate_id` | `integer` | null, FK `candidates.id` **ON DELETE SET NULL** | the candidate made from it |

The unique constraint on `loxo_person_id` is the only index needed besides the PK (step 4's lookup is `WHERE loxo_person_id = ANY(:ids)`). Deleting the candidate on the candidate page keeps the `loxo_candidates` row, so the person is **not** processed again (matches Q4 (a)). If Q4 picks (b) or (c), the unique key becomes `(loxo_person_id, loxo_resume_id)` or a `loxo_candidate_id` column.

**Migration:** `alembic revision --autogenerate -m "create loxo_candidates table"` after importing `app.features.loxo_sync.models` in `alembic/env.py`; review that the CHECK and the FK's `ondelete` are in it. Run on the local Postgres; Aiven only with the user's go-ahead (Render's `alembic upgrade head` on start would also run it).

**`candidates`:** no schema change. `source` gains the value `loxo` (validated in code: `Source` Literal).

**New settings** (`app/core/config.py`, `backend/.env.example`; every `.env` key must be declared, learning 2026-10-22):
- `loxo_api_token: SecretStr | None = None`
- `loxo_agency_slug: str | None = None`

Both optional: unset, the sync is off and the app, pytest and alembic start as today. Code constants (not settings, rule 2): `LOXO_BASE_URL = "https://app.loxo.co/api"` (Q12), `ENDORSED_STAGE_NAME = "Endorsed"`, `SYNC_INTERVAL_SECONDS = 600`, `LOXO_PAGE_SIZE`, `MAX_ATTEMPTS = 3` (Q9), `LOXO_SYNC_LOCK_ID` (a fixed bigint).

## 6. Slice layout
**Backend `backend/app/features/loxo_sync/`:**

| File | Does |
|---|---|
| `__init__.py` | slice docstring |
| `models.py` | `LoxoCandidate` (table above) |
| `schemas.py` | Pydantic models of the Loxo responses used: `LoxoStage`, `LoxoCandidateRow`, `LoxoCandidatePage` (rows + `scroll_id`), `LoxoResume`. Field names from the live check (Q1). |
| `service.py` | the Loxo `httpx.Client` and `LoxoError`; `run_sync(db)` (steps 2-6); `process_person(db, person_id, row)` (step 5); `list_endorsed_person_ids()`; `pick_resume(resumes)`; `start_sync_thread()` (scheduler (a) only). |
| `__main__.py` | scheduler (b) only: `python -m app.features.loxo_sync` runs one `run_sync` and exits (exit code 1 when the run ended on an error, so Render shows the run failed). |
| `README.md` | purpose, the Loxo calls, the table, settings, scheduler, tests |

No `router.py` (no endpoint, unless Q2 = c).

**Changed:**
- `app/main.py` (scheduler (a) only): the `lifespan` calls `loxo_sync.service.start_sync_thread()` after `ensure_admin`; the thread is a daemon and stops with the process.
- `app/core/config.py`, `backend/.env.example`: the two settings.
- `alembic/env.py`: import the model; the new migration.
- `app/features/candidates/schemas.py`: `Source = Literal["manual", "api", "url", "loxo"]`; `models.py` comment on `source`; candidates `README.md`.
- `frontend/src/features/candidates/api.ts`: `source` type and its JSDoc gain `'loxo'`; `CandidateList.tsx`: `SOURCE_LABELS` gains `loxo: 'Loxo'`.
- `tests/conftest.py`: autouse `fake_loxo` and `no_loxo_sync` (below).
- Docs: `docs/architecture.md` (feature list, layout, Decisions), `CLAUDE.md` and `README.md` (env vars; the Render cron job if Q2 = b), `docs/features/candidates.md` (a note on the new source).

**Frontend `frontend/src/features/loxo_sync/`:** not created (no UI, Q10).

## 7. Cross-feature contracts
| From | Used | Why |
|---|---|---|
| `resume_intake.service` | `extract_text`, `structure_resume`, `CV_ERRORS`, `cv_error_status` | read the CV and AI-structure it, map CV errors |
| `resume_generation.service` | `build_resume_html`, `render_pdf`, `PdfRenderError` | the branded PDF (the existing 2-Chromium semaphore applies) |
| `candidates.service` | `store_candidate`, `FileStorageError` | both files in Neon plus the candidate row. **Contract relied on:** `store_candidate` commits whatever else is pending in the session in its one commit (it does `db.add`, `add_ai_calls`, `db.commit()`), and deletes the objects and re-raises on a failed commit. Its docstring gets one line saying so. |
| `public_api.service` | `download_cv`, `CvDownloadError` | the file from `download_url`. Its 413 message mentions "file_url"; acceptable in our `last_error` (internal only). |
| `ai_usage.service` | `ai_spend_cap_reached`, `add_ai_calls`, `AiCall` | the cap check per person (not `require_ai_budget`: no request here), and storing a failure's paid calls |
| `core.database` | `engine`, `SessionLocal` | the advisory-lock connection and the run's session |

No feature imports `loxo_sync`, except `app/main.py` (scheduler (a)).

## 8. Failure modes and security
**Status after an error in step 5** (`attempts` counts every attempt; `retry` turns into `failed` once `attempts` reaches `MAX_ATTEMPTS`, Q9):

| Error | Status | Run continues? |
|---|---|---|
| `CV_ERRORS` with status 415 or 422 (not a PDF/DOCX/DOC/TXT, unreadable, no name/email, over 4 pages, over 50,000 characters) | `failed` at once | yes |
| `AiExtractionError` (502: OpenAI failed or timed out) | `retry` | yes |
| `CvDownloadError` 413 (over 10 MB) | `failed` at once | yes |
| `CvDownloadError` 422 (S3 link expired, 403, network) | `retry` | yes |
| `PdfRenderError` | `retry` | yes |
| `FileStorageError` | `retry` | yes |
| `LoxoError` 404 on resumes | `retry` | yes |
| `LoxoError` 401/403/429 | row not touched | **no**, end run |
| `SQLAlchemyError` (database down) | not writable: logged | **no**, end run |
| `IntegrityError` on the unique person id (a parallel run won) | none (the other run's row stands; `store_candidate` already deleted our objects) | yes |
| spend cap reached | row not touched | **no**, end run |
| any other exception in one person | `retry`, `last_error` "Unexpected error." (type logged) | yes. The thread must never die: `run_sync` is wrapped so the loop survives, logging the type only |

**Invalid input from Loxo:** every response goes through the Pydantic models; ids are integers; the stage name match is exact. Loxo can't make us call another host: the Loxo client has a fixed base URL, and `download_url` goes through `download_cv`'s public-IP and https checks.

**AI errors and timeouts:** the existing client (`timeout=120`, `max_retries=0`); retried on the next run, at most `MAX_ATTEMPTS` times. **Cost:** the spend cap is checked before each person, so a run overshoots by at most one CV's cost (the same accepted limit as today). Permanent CV failures are never retried, so no repeated payment for the same answer.

**Large or malformed files:** `download_cv` caps 10 MB / 30 s and never decompresses; `extract_text` detects type by content, caps pages (4) and text (50,000); PDFium stays behind its lock (the sync thread and web requests share it safely).

**Overlap and load:** the advisory lock (one run at a time across processes and instances); persons processed one at a time, so the sync uses at most one of the two Chromium render slots and one OpenAI call at a time. A long backfill run (Q6) simply holds the lock; the 10-minute ticks during it are skipped.

**Secrets:** `LOXO_API_TOKEN` is a `SecretStr`, sent only to `app.loxo.co` in the `Authorization` header, never logged; `httpx` errors (which can hold URLs) are never chained or logged, only their type. `download_url` is a presigned link: never logged or stored.

**Personal data:** Loxo responses hold names, emails, phones and job history. Nothing from them is logged; the log has counts, Loxo person ids and error types only. `loxo_candidates` stores Loxo ids, a status and our own message, no CV data. The CV and the PDF are stored exactly as for other candidates (original private with 1-hour presigned links; PDF public with an unguessable permanent URL), and the CV goes to OpenAI with `store=False`. New: this now happens **without a recruiter's click**, for every Endorsed person (Q13). Retention of stored CVs stays an open item in architecture.md.

**Prompt injection:** unchanged: the same prompts and guards as every upload; the branded PDF stays on our side unless Q3 adds an upload.

## 9. Testing
**pytest never calls Loxo:** an autouse `fake_loxo` fixture in `tests/conftest.py` replaces the module's client with an `httpx.Client(transport=httpx.MockTransport(handler))` whose handler answers from a per-test dict of canned routes and **fails the test on any unexpected request**; canned bodies use `httpx.Response(200, json=...)`. `download_cv` is monkeypatched in `loxo_sync.service` (it isn't hit through the fake). An autouse `no_loxo_sync` fixture unsets both settings, so the lifespan never starts the thread in any test (same as `no_admin_bootstrap`). `fake_openai` and `fake_s3` stay as they are; `render_pdf` is faked.

**Service tests** (`backend/tests/features/loxo_sync/test_service.py`, with `db_session`):
- settings unset: `run_sync` makes no request and writes nothing.
- the happy path: two stages, one matching "Endorsed" (case and spaces); two pages via `scroll_id`; one new person → a `candidates` row with source `loxo`, a `done` row linked to it, the AI calls stored; the request log shows exactly the expected calls with `Authorization: Bearer ...` and `workflow_stage_ids[]`.
- dedup: a person with `done` or `failed` makes no resumes call; a `retry` row is retried and its `attempts` grows; `retry` at `MAX_ATTEMPTS` becomes `failed`.
- the same person in two jobs on the list is processed once.
- `pick_resume`: several resumes → the chosen one (Q5); none → no row, no AI call.
- every row of the section 8 table: the right status, `last_error`, run continues or ends; failure AI calls stored unlinked.
- spend cap reached → no AI call, rows untouched, run ends.
- no Endorsed stage → no candidates call.
- Loxo 401, 429, 500, timeout, invalid JSON, wrong shape → `LoxoError` with no body/token in its message; the run ends where section 8 says.
- advisory lock held by another connection → the run does nothing.
- a candidate deleted afterwards: the row's `candidate_id` becomes null and the person isn't processed again.
- the log never holds a name, email, token or URL (`caplog`, with a fake person whose name and email are distinctive).
- scheduler (a): `start_sync_thread` doesn't start with the settings unset; an exception inside one run doesn't stop the loop (patched sleep). Scheduler (b): `python -m app.features.loxo_sync` exit codes.

**Candidates:** `CandidateList.test.tsx` renders a `loxo` row as "Loxo"; the backend list test accepts `source="loxo"`.

**UI / e2e:** no new screens; UI testing is paused by the user. No e2e.

**Live (only with the user's permission, run by the main session):** the read-only check in Q1 before building; after building, one real run against Loxo on a throwaway local backend with the local DB, limited to **one** Endorsed person (the user names which), costing 1-3 OpenAI requests and one Neon round trip; then delete that candidate.

## 10. Decisions
| Decision | Simpler alternative considered | Why |
|---|---|---|
| Poll the current Endorsed list and diff against our own table | Loxo webhooks; or `person_events` since the last run | The user asked for a cron. Webhook payloads, signing and retries are undocumented, and a missed webhook is lost for good. Whether a stage move creates a person event is undocumented. The current-state list is documented (`workflow_stage_ids[]`), needs no timestamps and heals itself after downtime. |
| One `/candidates` call across all jobs filtered by stage | list jobs, then each job's candidates | Documented filter; 1 call per 100 rows instead of 1 per job. |
| Dedup table `loxo_candidates` keyed by Loxo person id | a `loxo_person_id` column on `candidates` | The table also holds failures and retry counts; a deleted candidate isn't reprocessed. A column alone can't record failures. |
| Our row is committed in `store_candidate`'s own commit | write our row after `store_candidate` returns | No window where a stored candidate isn't marked done (which would create a duplicate on the next run). Costs one documented contract on `store_candidate`. |
| Postgres advisory lock per run | rely on the unique key alone | Two overlapping runs would both pay OpenAI for the same CV before the unique key stops one. The lock is one query and frees itself if the process dies. |
| `download_url` through `download_cv` | the `/download` endpoint with the token | The docs say to prefer `download_url`; the `/download` response is self-contradictory (JSON schema, "bytes"); `download_cv` already has every guard; the token isn't sent to a second host. |
| Sequential, one person at a time, no per-run limit | parallel processing; a per-run batch size | Matches "no queue or workers"; the spend cap and the advisory lock already bound cost and overlap. |
| Retries = the next run; no retry inside a run | in-run backoff | The next run is 10 minutes later anyway; no new code. |
| Settings optional (sync off when unset) | required like the storage keys | The app, pytest and local dev must start without a Loxo token. |
| Scheduler | (see Q2) | Depends on the Render plan. |

The lasting rows go into `docs/architecture.md` once the user approves (a "proposed" row is there now).
