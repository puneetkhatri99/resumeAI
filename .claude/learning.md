# Learnings

This log records what the user wants, the decisions they made, and the root cause and fix of significant bugs. Newest entries first. Read it before starting work.

Entry format:

```
### YYYY-MM-DD: <short title>
- **Context:** what happened / what was asked
- **Learning:** what we now know (for a bug: the root cause and the fix)
- **Applies to:** where this matters from now on
```

---

### 2026-10-22: Library traps found while building resume_generation
- **Context:** building the branded PDF (Playwright + Jinja2) and cutting the header/footer out of the sample with pypdfium2.
- **Learning:**
  - **Playwright `page.pdf(format="A4")` isn't A4:** it is 8.27 × 11.7 in, which Chromium prints as 595.92 × 842.88 pt (1 pt too tall). Use `width="210mm", height="297mm"`: 595.92 × 841.92 pt, exactly the sample's size.
  - **pypdfium2 `PdfImage.get_bitmap(render=True, scale_to_original=True)` is off by a pixel** (1500 × 254 came out 1500 × 255, and 1500 × 127 came out 1506 × 127). `render=False` gives the exact decoded pixels (BGR); write the PNG with `zlib` + `struct`, no Pillow. The sample's images were Flate (not JPEG), so `extract()` couldn't save them directly.
  - **Verified:** awaiting `document.fonts.ready` (with JavaScript off) before `page.pdf` embeds Inter as real subsets (`Inter-Regular/Medium/SemiBold`), and the real-render test asserts it. Sync Playwright works in the `fastapi dev` server's worker thread on Windows (row 358: 200 in about 1 s warm, 4 s cold).
  - **Lucide's licence** also carries an MIT notice for its Feather-derived icons (e.g. `link`), so the whole `LICENSE` file is kept as `LUCIDE-LICENSE.txt`.
- **Applies to:** `resume_generation` and any future PDF or image work.

### 2026-10-22: resume_generation (branded MinistryHub PDF) decisions made by the user
- **Context:** the user wants the AI-extracted CV data poured into their predesigned MinistryHub resume as a PDF, and asked "WeasyPrint + Jinja2 or Playwright + Jinja2", wanting it built fast. They supplied a sample MinistryHub PDF (a real candidate's CV, since deleted from the repo). The plan is in `docs/features/resume_generation.md`.
- **Learning (what the sample is):**
  - It was printed by **HeadlessChrome 128** (producer "Candidately"), A4.
  - The header and footer are **two full-width raster images** on every page, with the logo, yellow bars and MinistryHub contacts baked in; there's no live text in them. We reuse them, cut out of the sample.
  - Its font is **TT Interphases**, a paid font (TypeType).
  - The sample is a real person's CV: use it only for the header/footer images, never in tests.
- **Learning (the user's decisions):**
  - **Renderer:** Playwright (Python) + Jinja2. Chromium matches the Chrome-printed sample, and WeasyPrint would need an MSYS2 + Pango system install on Windows.
  - **Font:** Inter (OFL), bundled, instead of the paid TT Interphases.
  - **Data:**
    - Add a **`location`** field to the AI schema.
    - **Compute** total years of experience and each job's duration from the dates.
    - **No photo.**
  - **Delivery:** an "Open branded PDF" link on the result screen opens a new tab. The PDF is rendered **on demand** from the stored data and **not stored**.
  - **Layout:**
    - Links are labelled "LINK 1, LINK 2…" (only http(s) clickable).
    - Projects get a subsection after Education.
    - Only a "Skills" heading (no "AREAS OF GIFTING").
  - **Tests:** one real-render pytest (headless Chromium, synthetic data) is allowed. Every other test fakes the renderer. Still no UI or e2e browser tests.
  - **Follow-up answers (same day):**
    - The link goes in the title band before "New interview", styled as the outline button.
    - An error in the new tab shows plain JSON (`{"detail": ...}`).
    - The tester may make a few real GETs to the PDF endpoint (headless Chromium on the server, no UI, no AI).
    - **Delete the sample PDF from the repo root** once the header/footer images are cut out (the Downloads copy stays).
    - **Bullet markers are stripped only when followed by a space**, so "-30% latency" and "*Important*" stay as written (user, after QA).
    - **Page breaks:** keep a job's title row (title, company, dates) together with its first bullet, and let long bullet lists continue on the next page. Don't keep whole entries unbroken; that left page 1 mostly empty (user, after QA).
    - **"Work experience in years" = earliest job start → latest job end**, with "Present" as today. Months count when they are known. Gaps between jobs still count. (This replaces the earlier "earliest start → today" example, which overstated for someone not working now.)
- **Applies to:** `resume_generation`, `resume_structuring` (the `location` field), and any future template.

### 2026-10-22: Past-interviews page: the user's choices
- **Context:** the user asked for a page to see past operations and open previous results.
- **Learning:**
  - **Rows:** candidate name and email, date and time, rating and work mode (no file-name column; the file name only stands in when there is no AI data).
  - **Navigation:** header links with real addresses (`#/`, `#/history`, `#/history/{id}`); no React Router. After a submit, the result opens at `#/history/{id}`.
  - **Size:** the newest 20, then "Load more".
  - **Working alongside the other session:** it builds `resume_generation` in the same slice at the same time. It owns `get_resume_intake` (reused here), `IntakeResult.tsx` and the e2e `STORED_INTAKE` fixture; this work added only targeted edits to shared files after re-reading them.
  - **e2e not run** (UI testing still paused): the new history test and the `#/history/1` URL check are written but unverified.
  - **Bug: blank page.** `useEffect(() => window.scrollTo(0, 0), [path])` returned scrollTo's value. In the user's browser that wasn't `undefined`, so React threw "destroy is not a function" and unmounted the whole app. Fix: braces, so the effect returns nothing. jsdom's scrollTo returns `undefined`, so unit tests can't catch this. Never write an effect as an expression-bodied arrow.
- **Applies to:** `resume_intake`, `src/app/App.tsx`.

### 2026-10-22: Progress was off-screen, and Overleaf CV links were lost
- **Context:** the user only saw "Submitting…" and a spinner after submitting, and wanted "submitting -> extracting text -> ai cleaning" before the result. Their Overleaf CV's links never came through.
- **Learning:**
  - **Off-screen progress:** the progress card sat at the top of a long form while the user clicked Submit at the bottom. It is now a full-page overlay. The step names are the user's own: "Submitting", "Extracting text", "AI cleaning". Each stage is held for at least 700 ms so the fast text step is visible.
  - **Links in LaTeX PDFs:** the visible text is only a word ("GitHub", "LinkedIn") or an icon. The address lives in the PDF's link annotation, which a text layer never contains, so the AI saved the labels as `links` (row 359: 4 entries of 6 to 9 characters, no dot or slash). `read_pdf` now appends the annotations' web addresses under "Links in the PDF:" (pypdfium2 raw API: `FPDFLink_Enumerate`, `FPDFLink_GetAction`, `FPDFAction_GetURIPath`).
  - **DOCX has the same gap** (hyperlink addresses live in `word/_rels/document.xml.rels`); not fixed, since the user asked about PDF.
  - PDFium squeezes repeated spaces in a text layer to one.
- **Applies to:** `read_pdf`, the progress UI, and any future DOCX link work.

### 2026-10-22: Colourful UI redesign, built straight from the design canvas
- **Context:** the user asked to make the UI beautiful, then "add some colours so that this website looks modern", then "just do it don't think too much" to build it.
- **Learning:**
  - **The user approved the whole design at once:** radio pills for work mode and rating (still nothing chosen at first), live character counters, a progress card while sending, the candidate/assessment result layout, an "extracted by AI, check against the CV text" note, and Google Fonts. Backend errors still show next to Submit, not in the CV card.
  - **The user wants speed on UI work:** when they say "just do it", build without another round of questions.
  - **Label clash:** a `<section aria-labelledby>` whose heading wraps the file input's `<label>` gets the same accessible name ("CV"), so `getByLabelText('CV')` / Playwright `getByLabel('CV')` find two elements. The CV card has no `aria-labelledby`.
  - **e2e not run** (UI testing is still paused). `resume_intake.spec.ts` was updated for radios, the "Interview summary" heading and drops sent to the file input, but has not been run since.
- **Applies to:** `resume_intake`, `resume_structuring`, `src/app/app.css`, and the next e2e run.

### 2026-10-22: API QA of resume_generation: shrink-to-fit, out-of-range ids, and what is safe
- **Context:** the tester's API-only pass on the branded PDF (about 15 real renders through `render_pdf`, 20 real GETs to :8000, no live AI, 30 synthetic rows inserted and deleted by id).
- **Learning:**
  - **One long unbroken word shrinks the whole PDF.** Chromium's print shrinks the entire page to fit horizontal overflow. A 100-character unbroken job title scaled every page to 76% (80 characters: 88%; a 100-character certificate name: 91%). Beyond that limit it clips at the right margin instead (project names and bullets of about 100 characters). Only `.value` and `.entry-subtitle` have `overflow-wrap: anywhere`. Check a PDF's scale by comparing the name text object's width with a normal render (83 pt for "Quinn T").
  - **Out-of-range id gives a false 503.** `GET /api/resume_generation/99999999999` hits Postgres' 32-bit `id` column, psycopg raises `NumericValueOutOfRange`, SQLAlchemy wraps it as `DataError` (a `SQLAlchemyError`), and the router answers "The database is unavailable". Any `except SQLAlchemyError` around a primary-key read by a user-supplied integer has this trap. The failing test is `test_id_beyond_the_database_integer_range_is_not_reported_as_database_down`.
  - **Safe, verified:** every field is escaped; only http(s) links become PDF link annotations (checked with pypdfium2 `FPDFAnnot_GetLink` and a raw scan: no `/JS`, `/Launch` or `/OpenAction`); hostile names stay inside the percent-encoded `filename*=`; header and footer images are on every page of a 49-page PDF; 3 concurrent GETs all succeed (about 1.6 s each); a missing Chromium gives `PdfRenderError` with an install hint and no CV text in the log.
  - **Tooling:** `PdfTextObj.extract()` needs `page.get_objects(filter=[...], textpage=page.get_textpage())`. PowerShell `Replace()` writes a literal `\n` into Python files; use the Edit tool.
- **Applies to:** `resume_generation` template CSS and router, and any endpoint that reads a row by a user-supplied integer id.

### 2026-10-22: API QA of resume_structuring + SSE intake: test rows leaked into the dev database
- **Context:** the tester ran the API-only pass (no Playwright, no browser, zero live OpenAI calls).
- **Learning:**
  - **Leak:** every pytest run added 5 rows to the real `resume_intakes` table. A test that posts a successful upload through `client` without the `db_session` fixture commits to the real database (`get_db` is only overridden by `db_session`). The culprit was `test_answer_length_limit_is_inclusive`. Fix: any test whose request can succeed takes `db_session`. Stale rows (`original_filename = 'cv.txt'`, `structured_data->>'name' = 'Jane Doe'`, created 2026-10-22 16:34 to 17:28 IST) were left in the dev DB; the tester's delete was denied, so the main session or user has to remove them.
  - **Safe real-HTTP checks:** run a second backend on `127.0.0.1:8001` with `OPENAI_API_KEY=sk-test-not-real` and `OPENAI_BASE_URL=http://127.0.0.1:8001/no-openai/v1`. The SDK reads the base URL, so the AI call 404s on the same server and comes back as a 502 event; the server log shows `POST /no-openai/v1/responses 404`.
  - **`extracting` is sent before the 50,000-character cap is checked** (the cap lives in `extract_resume_data`, which the router calls after yielding `extracting`). A too-long CV therefore gets `reading`, `extracting`, `error 422`, and the "Extracting details with AI…" badge flashes although no AI call is made. This matches the design; it is only a UX quirk.
  - **PowerShell:** `$env:NAME = ""` deletes the variable instead of setting it empty. To test an empty `OPENAI_API_KEY`, set it from Python's `subprocess` `env=`. An empty key is accepted by `Settings` but the app still fails to start, because `OpenAI(api_key="")` raises "Missing credentials" when `service.py` is imported.
  - **Keepalive test:** `fastapi.routing._PING_INTERVAL` (15 s) is read at call time, so `monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.05)` plus a slow fake `parse` makes `: ping` appear in about 0.4 s.
- **Applies to:** every backend test that posts a CV, and any future real-HTTP check that must not reach OpenAI.

### 2026-10-22: resume_structuring (AI extraction) decisions made by the user, and what building it taught us
- **Context:** the user asked for AI (OpenAI) to pull the important details out of the CV text (name, email, phone, links, skills, experience, education, projects, certificates) with progress shown to the recruiter. The approved design is `docs/features/resume_structuring.md`; it is built.
- **Learning (the user's decisions):**
  - **Progress via SSE, not a webhook:** the user asked for "SSE or webhook, whatever is best". A webhook is server-to-server and can't reach a browser, so `POST /api/resume_intake` streams Server-Sent Events (`reading`, `extracting`, then `done` or `error`). Polling was rejected because it needs a stored job before processing.
  - **Model:** `gpt-4.1-mini`, kept by the user although OpenAI removes it from the API on **2027-01-14** (it recommends `gpt-6-luna`). **It must be switched before 2027-01-14.**
  - **CV text cap:** 50,000 characters sent to the AI; longer text gets a 422 before any AI call.
  - **`OPENAI_API_KEY` is now required** (the app won't start without it). This supersedes the entry below, "An unknown key in backend/.env breaks startup", where it was optional and unused.
  - **The result screen now shows the AI-extracted details** as readable sections above the raw text. This supersedes "The result screen shows the raw extracted text, not AI-structured fields" in the resume_intake entry below.
  - **Live AI budget:** at most **5 live OpenAI requests in total** while building and testing this feature, and **each one needs the user's permission first**. Only the main session can ask for it.
    - **Used: 1 of 5 (2026-10-22, approved).** One synthetic CV (`live_check_cv.txt`) went through the API on a separate backend on :8001. It streamed reading, extracting, done.
    - Every field came back as written: 2 jobs, 2 projects, 2 certificates, education, links and dates.
    - The row (id 358) was stored with matching `structured_data`.
  - **Dev DB cleanup (user-approved):** all 81 leaked `cv.txt` test rows were deleted on 2026-10-22.
  - **Result screen:** the user approved the section layout (e.g. "title · company", then "start – end · location"). Links are **clickable only when they are http(s)**, opening in a new tab; anything else stays plain text, because the links come from AI output.
  - **Testing scope (user, 2026-10-22):** "just test the api, leave the Playwright and other Chrome-related tests". The tester runs API tests only: no Playwright and no browser checks, including the gated live e2e. (On 2026-10-22 the main session wrongly had the developer run Playwright once, with no AI calls, while UI testing was paused. Read this file in full before delegating.)
  - **Tests never call the live AI:** pytest uses the autouse `fake_openai` fixture (`backend/tests/conftest.py`). In e2e, every test that submits answers the upload with a canned `text/event-stream` (`route.fulfill`), and a `beforeEach` aborts any other upload unless `E2E_LIVE_AI=1`. Only the one gated live test (`$env:E2E_LIVE_AI=1; npm run test:e2e`) reaches OpenAI.
- **Learning (found while building):**
  - **`openai` 3.36.1 uses `httpx2`, not `httpx`:** build SDK errors in tests with `openai.APITimeoutError(request=httpx2.Request(...))`. Starlette's `TestClient` uses `httpx2` too, so it's already installed.
  - **FastAPI SSE:** `response_class=EventSourceResponse` on a sync generator annotated `-> Iterator[Model]`. Don't add `response_model` (it turns off per-event validation) or `status_code` (it applies to the whole stream). FastAPI sends `: ping` keepalive comments while a step runs, so the client must skip comment lines and buffer events split across chunks.
  - **Playwright routes:** the route registered last wins, so a `beforeEach` guard `page.route(..., route.abort())` can be overridden per test with `route.fulfill`. `page.on('request')` still counts requests that a route aborts.
  - **Tooling on this machine:** the Bash tool's heredocs mangle backslashes (`\\n` arrives as a newline) and can fail on nested quotes ("unexpected EOF"). Edit code with the Edit/Write tools instead. `oxlint` prints nothing when the code is clean (exit code 0).
- **Applies to:** `resume_structuring`, `resume_intake`, every test that could reach OpenAI, and the model switch before 2027-01-14.

### 2026-10-22: Oversized upload never shows the "larger than 10 MB" message in dev (Vite proxy resets it)
- **Context:** QA of `resume_intake`. The backend answers an upload over 10 MB with a correct 413 (`Content Too Large`), and the form maps 413 to "The CV is larger than 10 MB." But in the browser at `[::1]:5173` the request fails with `net::ERR_CONNECTION_RESET`, so the form shows "Could not reach the server..." after about 6.5 s.
- **Learning:**
  - **Cause:** the reset happens in the Vite dev proxy (`/api` → `:8000`). The same-origin browser `fetch` straight to `http://127.0.0.1:8000` gets the 413 for 10.5 to 50 MB, and so does a Python client. Through the proxy, every size over the limit fails; 9 MB goes through fine. The backend answers early, before the body is read, and the proxy hop turns that into a reset.
  - **Open:** not fixed, so the 413 branch in `InterviewForm` can't be reached in dev. The e2e test `a CV over 10 MB shows "The CV is larger than 10 MB."` fails until it is fixed (for example a client-side size check, which the design chose not to do, or a proxy fix).
  - **Test tip:** `httpx` percent-encodes a NUL byte in a multipart filename (`a%00b.txt`), so it never reaches the backend. Use a hand-built multipart body to test it.
- **Applies to:** any early-rejecting endpoint (size limits, auth) behind a proxy: test through the proxy in a real browser, not only with a direct HTTP client.

### 2026-10-22: resume_intake decisions made by the user
- **Context:** the user asked for an API that takes a resume plus some text and extracts the data from PDF, Word, TXT or DOC, with OCR as a backup. Later they added a frontend interview form with a CV drop zone that shows everything after submitting. The design is in `docs/features/resume_intake.md`.
- **Learning:**
  - **The "some text":** interview answers from the recruiter.
    - The user chose only **Availability & pay** (notice period / start date, salary expectation, work mode) and **Interviewer assessment** (a 1–5 rating, strengths, concerns, overall notes).
    - There are no candidate name or contact fields.
    - **Every field is required.**
    - The Work mode and Rating dropdowns **start blank** ("Choose…"), so the interviewer must pick a value.
    - Dropping several files at once shows "Drop one CV at a time." and is ignored.
  - **OCR is deferred** ("leave OCR for now"): a scanned PDF gets a 422.
  - **Legacy `.doc`:** our own parser on `olefile`. No LibreOffice; nothing on this machine reads `.doc` (no Word, LibreOffice or Tesseract).
  - **PDF library:** `pypdfium2`.
  - **HTML/MHTML saved as `.doc`:** accepted as text.
  - **Storage:** the extracted text and the answers go in Postgres. The original file is **not** kept.
  - **The result screen** shows the raw extracted text, not AI-structured fields (AI comes later).
  - **Limits:**
    - A CV over **4 pages is rejected (422)**. The client brief said "flag, never cut", and the user chose rejection.
    - Pages are counted exactly for PDF, from Word's saved metadata for DOCX/DOC (skipped if missing), and not at all for TXT.
    - Notes are capped at 5,000 characters. The whole request is capped at 10 MB.
  - **Testing:** use synthetic files. Use the user's own files only when they name them, and delete those rows afterwards.
  - **UI testing is paused** at the user's request ("leave the UI test for now"), and reconfirmed for resume_structuring ("just test the api"). Don't run Playwright or browser checks until they ask again. The last run (2026-10-22) passed 13 e2e tests, with the live AI test skipped.
  - **Workflow:** the user wants plans saved as Markdown files when they ask ("make an MD file of this plan"). They also want to see what's going on: they asked "what are you doing?" during a long review step, so give short progress updates.
- **Applies to:** `resume_intake` and later phases. Revisit OCR and the page-limit behaviour when the pipeline is built.

### 2026-10-22: An unknown key in backend/.env breaks startup
- **Context:** the user added `OPENAI_API_KEY` to `backend/.env`. After that, the app, `pytest` and `alembic` all failed on import with pydantic "Extra inputs are not permitted", and the error printed part of the key's value.
- **Learning:**
  - pydantic-settings rejects any `.env` key that `Settings` doesn't declare.
  - The user chose to declare the key as `openai_api_key: SecretStr | None = None`, which isn't used yet.
  - Every new `.env` key must be added to `Settings` and to `backend/.env.example`. Use `SecretStr` for secrets.
- **Applies to:** `app/core/config.py` and any new setting.

### 2026-10-22: Two library traps found while designing resume_intake
- **Context:** the `architecture-challenger` review of the extraction design.
- **Learning:**
  - **`olefile.OleFileIO(data)`** treats a `bytes` value shorter than 1536 as a **file path** and opens it from disk, so a crafted upload could make the server read a local file. Always pass `io.BytesIO(data)`.
  - **PDFium (`pypdfium2`)** isn't thread-safe, not even across different documents. Sync FastAPI endpoints run in a thread pool, so every PDFium call goes behind one module-level lock, and objects are closed explicitly inside it.
  - A failed SQLAlchemy INSERT puts the bound parameters, including CV text, into the error message and so into the logs. The fix is `hide_parameters=True` on the engine.
  - FastAPI receives the whole upload before the endpoint runs, so a size check inside the endpoint can't stop a huge upload. Use Starlette's `RequestBodyLimitMiddleware` in `app/main.py`.
- **Applies to:** any code that reads uploaded files or touches the engine.

### 2026-10-22: The real project is MinistryHub branded resumes
- **Context:** the user pasted the client proposal "Branded Resume Automation for MinistryHub" (2026-10-21) and asked for it to be split into phases.
- **Learning:**
  - **Flow:** when a candidate is moved to **Endorsed** in the Loxo ATS:
    1. The service fetches the original resume and reads it (PDF, Word, text or scan).
    2. AI structures it and lightly tidies it, inventing nothing.
    3. Automatic checks match the result against the original.
    4. A branded MinistryHub PDF is rendered from a fixed template and uploaded to Loxo.
    5. The original is archived and hidden from hiring clients.

    Failures go to a review list.
  - **Phase plan:** was `docs/phases.md` (phases 0–7, about 6 weeks). **The user deleted it on 2026-10-22**, so don't recreate it or point to it unless they ask.
  - `resume_intake` was Phase 1.
- **Applies to:** every feature decision.

### 2026-10-21: Health check hung ~4 minutes when Postgres was down
- **Context:** No Postgres was running. Every connection attempt to `localhost:5432` on this Windows machine sat until it timed out, about 2 minutes per address (IPv6, then IPv4). As a result, `GET /api/health` and `alembic` hung instead of failing.
- **Learning:** psycopg has no useful default connect timeout. Fix: `connect_args={"connect_timeout": 5}` on the engine in `backend/app/core/database.py`. `alembic/env.py` reuses that same engine, so the setting lives in one place. The DB-down case now fails in about 10 seconds.
- **Applies to:** every DB connection. Reuse `app.core.database.engine` / `get_db`. Never create a second engine.

### 2026-10-21: Setup choices made by the user
- **Context:** project setup.
- **Learning:**
  - Frontend: React + **TypeScript** (Vite).
  - Postgres: **local Windows install** (no Docker). Postgres 18.6 is installed system-wide in `C:\Program Files\PostgreSQL\18` and runs as the Windows service `postgresql-x64-18` (automatic start). The user chose the superuser password, which is stored only in `backend/.env`. `psql` is not on PATH, so call it as `& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost`.
  - Python dependencies: **pip + venv** with `requirements*.txt` (not uv or poetry).
  - **Scaffold all three feature slices up front:** `resume_intake`, `resume_structuring`, `resume_generation`. They stay empty until requirements arrive.
  - Agents must **not use the Fable model**.
  - The user wants to be **asked rather than have anything assumed** (rule 1). They will give feature requirements later.
- **Applies to:** all work.

### 2026-10-21: Tooling facts discovered during setup
- **Context:** project setup.
- **Learning:**
  - **FastAPI extra:** `fastapi[standard]` pulls in `fastapi-cloud-cli`, which brings the Sentry SDK. We use `fastapi[standard-no-fastapi-cloud-cli]` instead: resumes are personal data and we don't deploy to FastAPI Cloud. That extra still installs the OpenTelemetry SDK, which does nothing unless it is configured.
  - **Linter:** `create-vite` 9 scaffolds **oxlint**, not ESLint. `npm run lint` runs oxlint.
  - **PowerShell BOM:** in Windows PowerShell 5.1, `Set-Content -Encoding utf8` writes a BOM, which can break config parsers (e.g. `alembic.ini`). Edit files with the editor tools or Python instead.
  - **localhost on this machine:**
    - On this Windows machine, `localhost` resolves to IPv6 `::1` first.
    - A connection to a port that nothing is listening on there **times out instead of being refused** (also the root cause of the 2-minute Postgres hang).
    - In dev, uvicorn listens only on `127.0.0.1:8000` and Vite only on `[::1]:5173`. Browsers and Node fall back between the two automatically, but PowerShell's `Invoke-WebRequest http://localhost:8000` hangs.
    - In scripts and checks, use `http://127.0.0.1:8000` for the backend and `http://[::1]:5173` (or a browser) for the frontend.
  - **Playwright on Windows:** Playwright runs `webServer` commands through `cmd.exe`, so `.venv/Scripts/python.exe` fails with "'.venv' is not recognized". Use backslash paths on Windows (see `frontend/playwright.config.ts`).
- **Applies to:** dependency changes, linting, editing files on this machine.
