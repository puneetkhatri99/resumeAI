# Architecture

## What the system does
1. **resume_intake:** a recruiter drops in a CV (PDF, DOCX, DOC or TXT) and fills in an interview form. The CV text is extracted, structured by AI (below) and stored with the answers, and all of it is shown back to the recruiter. The upload streams its progress as Server-Sent Events.
2. **resume_structuring:** uses AI (OpenAI) to turn the CV text into structured data: contact details, links, skills, experience, education, projects and certificates. It runs inside the intake upload; it has no endpoint of its own.
3. **resume_generation:** renders the branded MinistryHub PDF from the structured data, on demand (`GET /api/resume_generation/{intake_id}`, opened from the result screen's "Open branded PDF" link). The PDF isn't stored.

The client project is MinistryHub branded resumes, triggered from Loxo.

The detailed design for each feature lives in `docs/features/<feature>.md`, written once the user gives the requirements.
- `resume_intake` and `resume_structuring` (AI extraction) are designed and built.
- `resume_generation` (the branded PDF) is designed and built.

## Layout
```
backend/
  app/
    main.py                 # FastAPI app; mounts every feature router under /api
    core/                   # shared: config.py (settings from .env), database.py (engine, Base, get_db)
    features/<feature>/     # one vertical slice per feature (see below)
  alembic/                  # migrations; env.py reuses app.core.database.engine
  tests/features/<feature>/ # pytest, mirrors app/features
frontend/
  src/
    main.tsx, app/App.tsx   # entry + root component
    shared/                 # shared: api/client.ts (fetch helpers, incl. the event-stream reader), test/setup.ts
    features/<feature>/     # one vertical slice per feature (see below)
  e2e/<feature>.spec.ts     # Playwright end-to-end flows
docs/                       # this file + docs/features/<feature>.md designs
.claude/                    # rules.md, learning.md, agents/
```

## Request flow
1. The browser loads the app from the Vite dev server (`:5173`).
2. Vite proxies `/api/*` to FastAPI (`:8000`).
3. The feature's `router.py` receives the request.
4. The feature's `service.py` does the work (optional; added once a slice has logic).
5. A SQLAlchemy session from `get_db` talks to Postgres.

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
| 2026-10-22 | Extracted text and answers are stored; the original file isn't kept. CVs over 4 pages are rejected. | User choice |
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
| 2026-10-22 | Frontend pages by hash address (`#/`, `#/history`, `#/history/{id}`) with a small `useHashPath` hook in `App.tsx`; no router package | User choice: Back, refresh and bookmarks work without adding React Router (rule 2). |
| 2026-10-22 | The past-interviews list pages by id (`before_id`), not offset, and returns summaries without the CV text | A submit made while paging can't shift rows into duplicates; the list stays light. |
| 2026-10-22 | Branded PDF rendered by headless Chromium through Playwright for Python (`playwright==1.64.0`, Chromium 1248), from a Jinja2 template with `autoescape=True` | User choice. The sample was Chrome-printed; no system install. Setup: `python -m playwright install --only-shell chromium`. |
| 2026-10-22 | Font Inter (SIL OFL), static 400/500/600 woff2 bundled as data URIs, instead of the paid TT Interphases | User choice. Static, because Chrome's PDF backend turns variable fonts into Type 3. |
| 2026-10-22 | Header and footer are the sample's raster images, drawn through `page.pdf`'s header and footer templates | Repeats them on every page, edge to edge |
| 2026-10-22 | PDF rendered on demand by `GET /api/resume_generation/{intake_id}`, not stored; `Cache-Control: no-store` | User choice; personal data |
| 2026-10-22 | Rendering runs with JavaScript off and every network request aborted | The page holds AI output |
| 2026-10-22 | One Chromium launch per PDF | Simplicity; revisit if volume grows |
| Open | Production hosting/deployment, auth, OCR, output formats, retention of stored CVs, a queue/retries for AI extraction | Not decided yet. Ask the user. |
