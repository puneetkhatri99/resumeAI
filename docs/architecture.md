# Architecture

## What the system does
1. **resume_intake:** a recruiter drops in a CV (PDF, DOCX, DOC or TXT) and fills in an interview form. The CV text is extracted (a scanned PDF by OCR), structured by AI (below) and stored with the answers, and all of it is shown back to the recruiter. The upload streams its progress as Server-Sent Events.
2. **resume_structuring:** uses AI (OpenAI) to turn the CV text into structured data: contact details, links, skills, experience, education, projects and certificates. It also reads scanned PDFs by OCR (OpenAI vision). It runs inside the intake upload; it has no endpoint of its own.
3. **resume_generation:** renders the branded MinistryHub PDF from the structured data, on demand (`GET /api/resume_generation/{intake_id}`, opened from the result screen's "Open branded PDF" link). The PDF isn't stored.
4. **public_api:** the same extraction and branded PDF for outside systems (e.g. Loxo), behind an `X-API-Key` header and keyed by the caller's own `candidate_id` + `resume_id`: `POST /api/public/resumes` (JSON) and `GET /api/public/resumes/{candidate_id}/{resume_id}/pdf?template_id=1`.
5. **ai_usage:** records every answered OpenAI call (model, call type, tokens, cost priced at that model) and shows usage and cost on the AI usage panel (`#/usage`, `GET /api/ai_usage`); the past-interviews list shows each CV's AI cost.
6. **candidates:** a recruiter adds a candidate from one CV (`POST /api/candidates`, SSE): AI extraction, the branded PDF, and both files stored in Neon Object Storage (the original private, the PDF public). The candidate page (`#/candidates`) lists them with links, source and AI cost, and deletes them. `POST /api/public/pdf` (public_api) does the same for outside systems and returns JSON with the PDF's public link and the extracted data; since 2026-11-04 it also takes the CV as `file_url` (public https, port 443, IPv4, the checked IP pinned, no redirects, 10 MB / 30 s, via `httpx`).
7. **auth:** login for the web app. One account per recruiter (by email), roles `admin` and `recruiter`, a 12-hour server-side session in an `HttpOnly` cookie. Every `/api` route needs a login except `/api/health`, `/api/public/*` (its own `X-API-Key`) and login/logout. Everyone can change their own password (`#/password`); the admin adds, resets and removes recruiters (`#/admin`). The only admin is created at startup from `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

The client project is MinistryHub branded resumes, triggered from Loxo.

The detailed design for each feature lives in `docs/features/<feature>.md`, written once the user gives the requirements.
- `resume_intake` and `resume_structuring` (AI extraction) are designed and built.
- `resume_generation` (the branded PDF) is designed and built.
- `ai_usage` (the AI usage panel) is designed and built.
- The switch to `gpt-5.6-luna` (`docs/features/model_switch.md`) is built, and its live check passed on 2026-11-03. It isn't pushed yet.
- Rate limiting and the AI spending cap (`docs/features/rate_limit_and_spend_cap.md`) are designed and built.
- `public_api` (`docs/features/public_api.md`) is designed and built; its migration awaits the user's approval to run on Aiven.
- `candidates` (add a candidate with S3 storage, the candidate page, the public PDF endpoint; `docs/features/candidates.md`) is approved (2026-11-03) and built; its migration `3aee3f10cd78` ran on the local Postgres and on Aiven (2026-11-03), and its live Neon and OpenAI checks passed.
- `loxo_sync` (fetch Endorsed candidates from Loxo every 10 minutes; `docs/features/loxo_sync.md`) is designed, not approved: blocked on its open questions and a read-only Loxo live check.
- `auth` (login, roles admin/recruiter, the admin page; `docs/features/auth.md`) is approved (2026-11-03) and built; its migration `381a21f5f9fb` ran on the local Postgres only (Aiven needs the user's go-ahead). Not pushed.

## Layout
```
backend/
  app/
    main.py                 # FastAPI app; mounts every feature router under /api (the data routers
                            #   with auth.require_user), the built frontend at / when backend/static
                            #   exists (the Docker image), and a lifespan that creates the bootstrap admin
    core/                   # shared: config.py (settings from .env), database.py (engine, Base, get_db),
                            #   rate_limit.py (per-IP rate-limit middleware for /api)
    # features/*/service.py may raise HTTPException only for ai_usage.require_ai_budget (one shared copy)
    features/<feature>/     # one vertical slice per feature (see below)
  alembic/                  # migrations; env.py reuses app.core.database.engine
  tests/features/<feature>/ # pytest, mirrors app/features
frontend/
  src/
    main.tsx, app/App.tsx   # entry + root component (top bar, page by address, footer)
    app/NavMenu.tsx, app/ThemeToggle.tsx  # the top bar's dropdowns and the light/dark switch
    shared/                 # shared: api/client.ts (fetch helpers: apiGet, apiRequest, the event-stream reader; UNAUTHORIZED_EVENT on 401), format.ts (formatUsd), test/setup.ts
                            # (app/app.css holds the shared look, incl. the upload progress overlay)
    features/<feature>/     # one vertical slice per feature (see below)
  e2e/<feature>.spec.ts     # Playwright end-to-end flows
docs/                       # this file + docs/features/<feature>.md designs
.claude/                    # rules.md, learning.md, agents/
```

## Request flow
0. At startup, the `lifespan` in `app/main.py` runs `auth.service.ensure_admin` once (creates the admin from `ADMIN_*` if missing).
1. The browser loads the app from the Vite dev server (`:5173`). Deployed, FastAPI serves the built app itself from `backend/static`. The app calls `GET /api/auth/me`; a 401 shows the login page.
2. Vite proxies `/api/*` to FastAPI (`:8000`). Deployed, `/api` is on the same origin.
3. Middleware in `app/main.py`: the per-IP rate limit (`core/rate_limit.py`, 429 when over; a request with a valid public `X-API-Key` is counted by the key instead), then the 10 MB body limit (413).
4. The login check: `auth.router.require_user` (attached with `include_router(..., dependencies=...)` to every data router) finds the session from the `session` cookie, or answers 401 before the route's own dependencies. `/api/health`, `/api/public/*` and login/logout skip it.
5. The feature's `router.py` receives the request.
6. The feature's `service.py` does the work (optional; added once a slice has logic).
7. A SQLAlchemy session from `get_db` talks to Postgres.

## Feature slice conventions
**Backend: `backend/app/features/<feature>/`.** Create a file only when the slice needs it.

| File | Holds |
|---|---|
| `router.py` | HTTP endpoints only: parse the request, call the service, return a schema. |
| `schemas.py` | Pydantic request/response models. These are the API contract. |
| `service.py` | Business logic. Uses the DB session directly; there is no repository layer. |
| `models.py` | SQLAlchemy models (inherit `app.core.database.Base`). |
| `README.md` | Purpose, API, data, tests. |

**Frontend: `frontend/src/features/<feature>/`.**

| File | Holds |
|---|---|
| `<Component>.tsx` | Components for this feature. |
| `<Component>.test.tsx` | Vitest + Testing Library tests, next to the component. |
| `api.ts` | This feature's backend calls (through `shared/api/client.ts`), once there is more than a single inline call. |
| `README.md` | Purpose, what it shows, tests. |

**Rules:**
- A feature uses another feature only through its `service.py` / `api.ts`.
- Code moves to `core/` / `shared/` only once two or more features need it.
- The `health` slice is the working reference example.

## Adding a feature (checklist)
1. The design is approved (`docs/features/<feature>.md`).
2. Backend: create the slice and its README, then mount its router in `app/main.py` with `prefix="/api"`.
3. If the feature has models:
   1. Import them in `alembic/env.py`.
   2. Run `alembic revision --autogenerate -m "<message>"`.
   3. Review the migration.
   4. Run `alembic upgrade head`.
4. Frontend: create the slice and its README, then compose its components in `app/App.tsx`.
5. Tests:
   - `backend/tests/features/<feature>/`
   - colocated `*.test.tsx`
   - `frontend/e2e/<feature>.spec.ts` for the user flow
6. Update this file if a convention or decision changed.

## Naming
See `.claude/rules.md`, rule 5. In addition:
- **API paths:** the router prefix is the feature folder name, e.g. `/api/health` and `/api/resume_intake`.
- **Tables:** the plural of the model, in snake_case, e.g. `ResumeIntake` → `resume_intakes`.

## Decisions
| Date | Decision | Why |
|---|---|---|
| 2026-10-21 | Feature-based (vertical slice) structure | User rule 7 |
| 2026-10-21 | FastAPI + PostgreSQL + React/TypeScript (Vite) | User rule 4 / user choice |
| 2026-10-21 | Local Postgres install, pip + venv | User choice |
| 2026-10-21 | Sync SQLAlchemy sessions | Simpler than async. FastAPI runs sync endpoints in a threadpool. |
| 2026-10-21 | No repository layer; the service uses the session directly | Simplicity (rule 2) |
| 2026-10-21 | Vite dev proxy instead of CORS | One origin in dev. Production hosting isn't decided yet. |
| 2026-10-21 | Exact version pins in `requirements*.txt` | pip has no lockfile |
| 2026-10-21 | DB `connect_timeout=5`; Alembic reuses the app engine | Fail fast when Postgres is down (see learning.md) |
| 2026-10-21 | `fastapi[standard-no-fastapi-cloud-cli]` | Avoids the cloud CLI and its Sentry dependency |
| 2026-10-22 | CV input formats: PDF, DOCX, DOC and TXT, detected by content. HTML saved as `.doc` is accepted as text. | User choice (`docs/features/resume_intake.md`) |
| 2026-10-22 | PDF text via `pypdfium2`, behind one module-level lock | Best text quality (user choice). PDFium isn't thread-safe, and sync endpoints run in a thread pool. |
| 2026-10-22 | DOCX text via the standard library (`zipfile` + `xml.etree`) | No dependency needed. Bundled Expat is safe against entity attacks. |
| 2026-10-22 | Legacy `.doc` via our own piece-table parser on `olefile` | User choice: no LibreOffice system install. |
| 2026-10-22 | No OCR yet. Scanned PDFs get a 422. | User choice: deferred |
| 2026-10-22 | Extracted text and answers are stored; the original file isn't kept. CVs over 4 pages are rejected. (The candidates feature keeps the original file: see 2026-11-03.) | User choice |
| 2026-10-22 | 10 MB request limit via Starlette `RequestBodyLimitMiddleware` in `app/main.py` | FastAPI reads the whole upload before the endpoint runs, so only middleware can stop a huge upload. |
| 2026-10-22 | Engine `hide_parameters=True` | A failed INSERT would otherwise log CV text. |
| 2026-10-22 | Native drag-and-drop on a visible `<input type="file">`; no upload library | Simplicity (rule 2). Keyboard access and native required-field validation come free. |
| 2026-10-22 | `OPENAI_API_KEY` is a **required** `SecretStr` in `Settings`; the app won't start without it. (Supersedes "optional, not used yet".) | `resume_structuring` needs it on every upload. `SecretStr` keeps it out of logs. |
| 2026-10-22 | AI extraction with OpenAI `gpt-4.1-mini` through the official `openai` SDK (`responses.parse` with a Pydantic schema, `temperature=0`) | User choice. OpenAI removes `gpt-4.1-mini` on **2027-01-14** (it recommends `gpt-6-luna`); the user kept it for now, so it **must be switched before that date**. |
| 2026-10-22 | `store=False` on every OpenAI call; CV text is never logged | Personal data. OpenAI may still keep API data up to 30 days for abuse monitoring without Zero Data Retention (open with the client: their data rules). |
| 2026-10-22 | OpenAI client with `timeout=120` and `max_retries=0`; a failure is a 502 the recruiter resubmits | The SDK's 600 s default is too long, but a dense CV takes ~35–70 s. A retry would spend a paid request. No queue yet. |
| 2026-10-22 | CV text sent to the AI is capped at 50,000 characters (422 before any AI call) | User choice. Bounds cost and time. |
| 2026-10-22 | Extracted data stored as JSONB in `resume_intakes.structured_data` (nullable only for rows from before AI extraction) | User choice. One schema in Pydantic; no extra tables. |
| 2026-10-22 | `POST /api/resume_intake` answers with Server-Sent Events (`reading`, `extracting`, then `done` or `error`) via FastAPI's built-in `EventSourceResponse`; the browser reads it with `fetch` | The user asked for "SSE or webhook, whatever is best". A webhook can't reach a browser, and polling would need a stored job before processing. |
| 2026-10-22 | Tests never call OpenAI: an autouse `fake_openai` fixture in pytest; e2e answers uploads with canned event streams. One e2e test calls the live API, only with `E2E_LIVE_AI=1`. | User rule: at most 5 live requests while building the feature, each with the user's permission. |
| 2026-10-22 | UI look: colour tokens and shared classes (band, cards, buttons, fields, radio pills) in `frontend/src/app/app.css`; feature CSS only for its own layout. Fonts Instrument Sans + IBM Plex Mono from Google Fonts (`index.html`). | User asked for a colourful, modern UI and approved the design canvas. Google Fonts needs no npm package; it makes the recruiter's browser call Google. |
| 2026-11-03 | **UI redesign (supersedes the 2026-10-22 look row):** light and dark colour tokens in `app/app.css` (dark by the system setting, or by `<html data-theme>` once the user clicks the toggle; the pick is remembered in `localStorage` under `theme` and applied by an inline script in `index.html` before first paint). Neutral surfaces with one accent, MinistryHub yellow, on the "Add candidate" action and on data marks only (progress, rating). No band, eyebrows, step numbers or rainbow tiles. Top bar: Candidates, an Interview menu (New interview, Past interviews), AI usage, Add candidate, the theme switch and an Account menu (Change password, Admin, Log out); a menu sheet on phones. `#/` is now the candidate page and the interview form moved to `#/interview`; the backend health line moved to the footer. IBM Plex Mono dropped (the raw CV text uses the system monospace). | User asked for a beautiful UI with a dark/light toggle that shows off Add candidate and Candidates, with the interview pages grouped; they chose the top bar with an Interview menu, the candidate page at `#/`, follow-system-then-remember, and the whole app restyled. |
| 2026-10-22 | Frontend pages by hash address (`#/`, `#/history`, `#/history/{id}`) with a small `useHashPath` hook in `App.tsx`; no router package | User choice: Back, refresh and bookmarks work without adding React Router (rule 2). |
| 2026-10-22 | The past-interviews list pages by id (`before_id`), not offset, and returns summaries without the CV text | A submit made while paging can't shift rows into duplicates; the list stays light. |
| 2026-10-22 | Branded PDF rendered by headless Chromium through Playwright for Python (`playwright==1.64.0`, Chromium 1248), from a Jinja2 template with `autoescape=True` | User choice. The sample was Chrome-printed; no system install. Setup: `python -m playwright install --only-shell chromium`. |
| 2026-10-22 | Font Inter (SIL OFL), static 400/500/600 woff2 bundled as data URIs, instead of the paid TT Interphases | User choice. Static, because Chrome's PDF backend turns variable fonts into Type 3. |
| 2026-10-22 | Header and footer are the sample's raster images, drawn through `page.pdf`'s header and footer templates | Repeats them on every page, edge to edge |
| 2026-10-22 | PDF rendered on demand by `GET /api/resume_generation/{intake_id}`, not stored; `Cache-Control: no-store` | User choice; personal data |
| 2026-10-22 | Rendering runs with JavaScript off and every network request aborted | The page holds AI output |
| 2026-10-22 | One Chromium launch per PDF | Simplicity; revisit if volume grows |
| 2026-10-23 | OCR for scanned PDFs through OpenAI vision: the PDF goes as a Responses API `input_file` (`transcribe_pdf` in `resume_structuring`), with the same client, model and `store=False`. It runs when a PDF has no text layer, plus one retry when the AI finds no name or email in a text-layer PDF. (Supersedes "No OCR yet".) | User choice. No install (Application Control blocks `.exe` launchers here), good with two-column CVs, and no new vendor sees CV data. A scanned upload costs 2 OpenAI requests. |
| 2026-10-23 | AI usage and cost in the `ai_usage` slice: one `ai_calls` row per answered OpenAI call, priced when recorded from a hand-edited `PRICES` table (5 models, OpenAI's prices on 2026-10-23), linked to its intake (`ON DELETE SET NULL`; unlinked for a failed upload). Calls are collected in a required `ai_calls` list parameter and stored in the intake's own commit. The panel's days and months follow the browser's time zone (`set_config('TimeZone', tz, true)` per request). | User choice (`docs/features/ai_usage.md`). The cost must stay right after the switch away from gpt-4.1-mini. A collector keeps the usage of answers that are then rejected (OpenAI bills them); a ContextVar doesn't survive the SSE generator's thread-pool steps. Calls with no answer (timeouts) can't be recorded: OpenAI's billing is the source of truth. |
| 2026-10-27 | All-time AI spending cap: `AI_SPEND_CAP_USD` (default $5, `settings.ai_spend_cap_usd`). Once the summed `ai_calls.cost_usd` reaches it, `POST /api/resume_intake` gets a plain 402 from the `require_ai_budget` dependency, before the event stream and any AI call (503 if the database fails during the check). | User choice (`docs/features/rate_limit_and_spend_cap.md`), before the public deploy. All time, not monthly. The check runs once per upload, so a running upload can overshoot by its own cost (accepted). |
| 2026-10-27 | Per-IP rate limit on every `/api` request: 120 per minute, plus 30 uploads per hour on top. In-memory sliding window (`core/rate_limit.py`, Starlette `BaseHTTPMiddleware`, a lock-guarded dict of deques), 429 with `Retry-After`. | User choice: no new package. Counts reset on restart and are per instance (enough for one Render instance). The client IP is `request.client.host`; trusting Render's forwarded header is set up in the deploy step. |
| 2026-10-27 | Database on Aiven for PostgreSQL (`sslmode=require`); `backend/.env` points at it, and the local Postgres line is kept commented out. (Supersedes "Local Postgres install" for the database.) | User choice, for the deploy. |
| 2026-10-27 | Deploy on Render as **one Docker web service** (root `Dockerfile`): stage 1 builds the frontend, stage 2 is `python:3.13-slim` with `playwright install --with-deps --only-shell chromium`. FastAPI serves the built frontend from `backend/static` at `/` (mounted only when that folder exists) and the API at `/api`. The container runs `alembic upgrade head` on every start. | One origin, so no CORS and no API URL to configure; one service. Docker because Chromium needs system libraries that Render's native Python runtime can't install. |
| 2026-11-02 | Public API in the `public_api` slice: one shared key in the `X-API-Key` header (`PUBLIC_API_KEY`; unset = 401), its own table `public_resumes` keyed by the caller's unique `(candidate_id, resume_id)` (a repeat replaces, by `INSERT … ON CONFLICT DO UPDATE`), plain JSON instead of SSE, the PDF by `template_id` (only 1). Its AI calls are stored unlinked and count toward the spending cap; its POST shares the upload rate-limit bucket. | User choice (`docs/features/public_api.md`). A separate table, because `resume_intakes` rows need the interview answers and show in the web history. |
| 2026-11-03 | **Built 2026-11-03** (`docs/features/model_switch.md`; live check passed 2026-11-03): every OpenAI call (structuring, OCR, OCR retry, and so `public_api`) switches from `gpt-4.1-mini` to `gpt-5.6-luna`, with `reasoning={"effort": "none"}` added next to `temperature=0`. `OPENAI_MODEL` stays a code constant, not an env var. (Supersedes the 2026-10-22 `gpt-4.1-mini` row.) | User choice: gpt-4.1-mini is removed on 2027-01-14 and failed both prompt-injection benchmark cases; gpt-5.6-luna passed them and costs less per CV. Luna rejects `temperature` at its default effort; `"none"` is the benchmarked setting. A constant keeps the model, its call parameters and its `PRICES` entry changing together. |
| 2026-11-03 | `candidates` slice (approved and built 2026-11-03) (`docs/features/candidates.md`). **Storage:** Neon Object Storage (S3-compatible, path-style) through `boto3`. The original CVs go in the private `RESUME_BUCKET` (presigned links, 1 h); the branded PDFs go in the public `PUBLIC_RESUME_BUCKET` (permanent URL). Keys are random `candidates/{uuid}/`; storage is written before the row, with a best-effort delete on failure; delete removes the objects, then the row (Neon soft-deletes). **Data:** a `candidates` table (name, `source` `manual`/`api`, file type, two keys, `ai_cost_usd` set at save time); the AI calls are stored unlinked. **API:** `POST /api/candidates` streams `reading`, `extracting`, `generating`, `uploading`, then `done` (only the public URL) or `error`; `POST /api/public/pdf` (`template_id`, default 1) also saves a candidate. One `require_ai_budget`, in `ai_usage/service.py`. **Supersedes** "the original file isn't kept". | User choices 2026-11-03 plus the challenger's review. No queue (user). No login yet (accepted; next feature). |
| 2026-11-03 (approved and built 2026-11-03) | Login in the `auth` slice (`docs/features/auth.md`). **Data:** `users` (email unique, scrypt hash, role `admin`/`recruiter` with a CHECK) and `user_sessions` (SHA-256 of the token, 12 h fixed expiry, cascade on user delete). **Sessions:** server-side; the token goes in an `HttpOnly; Secure; SameSite=Lax; Path=/api` cookie. **Hashing:** stdlib `hashlib.scrypt` (n=2^15, r=8, p=3), min 12 characters everywhere. **Accounts:** one bootstrap admin from `ADMIN_EMAIL`/`ADMIN_PASSWORD` at startup (created only if missing, never overwritten). The admin page adds, resets and removes recruiters only; everyone can change their own password (other sessions end). **Guards:** `require_user` on every `/api` router except `health`, `public_api` and login/logout, and `require_admin` on admin routes, enforced by an OpenAPI-driven 401/403 test. **Rate limit:** login and password change share a 10/15 min bucket. No CSRF token. | User's answers 2026-11-03 plus the challenger's review. No new package. Cookies work for fetch, the SSE uploads and the PDF in a new tab; DB sessions make logout, resets and removals immediate; Lax + same origin covers CSRF (revisit with a custom domain). The login bucket is shared by all on Render until the client-IP fix (next step). |
| 2026-11-03 (**approved and built 2026-11-03; the look awaits the sample-PDF review**) | Client feedback on the branded resume (`docs/features/resume_upgrade.md`). `ResumeData` gains `summary` (the CV's own, copied, or 2–3 AI-written sentences when there is none; an Objective doesn't count) and `inferred_skills` (name plus a quoted `evidence`, dropped by `keep_proven_skills` when the quote isn't in the CV text). Both have defaults, so old rows read without a migration; a test keeps `"default"` out of the strict schema. Inferred skills are mixed in with the listed ones, unmarked. Bullets: a prompt rule only. Presentation rules run at render time: current roles first, then newest; ongoing ends shown as "Present"; 10-digit/+1 phones as (XXX) XXX-XXXX; links grouped by web address into "Social Media", "Teaching & Worship Videos" and "Other Links"; the education chip shows the graduation date only. The public JSON stays additive. Stored candidate PDFs aren't regenerated. | User's answers plus the challenger's review. Deterministic and testable, covers old rows without AI calls or a DB step, and the stored data stays as written. The look waits for the user's approval of one synthetic sample PDF. |
| 2026-11-04 (**proposed, not approved**; blocked on its open questions) | `loxo_sync` (`docs/features/loxo_sync.md`): every 10 minutes, list the people currently in Loxo's "Endorsed" workflow stage(s) (`GET /{slug}/candidates?workflow_stage_ids[]=`), skip those in a `loxo_candidates` table (unique Loxo person id, status `done`/`retry`/`failed`), and run each new one through the existing CV → AI → branded PDF → Neon pipeline as a candidate with source `loxo`. One run at a time via a Postgres advisory lock; the spend cap checked per person; settings `LOXO_API_TOKEN` (SecretStr) and `LOXO_AGENCY_SLUG`, optional (sync off when unset). Scheduler (in-process thread vs Render Cron Job) open. | Loxo's webhooks have undocumented payloads, signing and retries, and it has no documented "changed since" filter, so polling the current state and diffing it is the documented, self-healing option. |
| Open | Render forwarded-header trust for the client IP (until then every visitor shares one rate-limit bucket, the login bucket included), OCR for images and DOCX, output formats, retention of stored CVs, a queue/retries for AI extraction | Not decided yet. Ask the user. |
