# Review issues (2026-11-04)

A devil's-advocate review of the whole app (backend, CV pipeline, frontend), done by reading the code and running small offline probes. No live OpenAI, Neon or database calls were made, and no code was changed. This file lists every issue found so they can be fixed later.

**Status: items 3, 4 and 9 fixed on 2026-11-04; every other item open.** Mark an item done here (or delete it) when its fix ships, with a test.

Severity: **High** can take the live app down or lose data or money; **Medium** breaks a real flow; **Low** is an edge case or hardening. Confidence: **confirmed** (reproduced, or plainly visible in the code) or **plausible** (follows from reading the code or the framework, not reproduced).

## Summary

| # | Issue | Area | Severity | Confidence |
|---|---|---|---|---|
| 1 | A small PDF can use gigabytes of memory | Backend, CV reading | High | Confirmed |
| 2 | Paid AI calls aren't recorded when the client disconnects | Backend, uploads | High | Confirmed |
| 3 | PDF renders wait for a slot forever | Backend, PDF | Medium | Plausible |
| 4 | Every upload holds a database connection for its whole run | Backend, uploads | Medium | Confirmed |
| 5 | An expired session wipes the interview form | Frontend | High | Confirmed |
| 6 | A failed logout looks like a logout | Frontend | Medium | Confirmed |
| 7 | A finished upload navigates even after the user left | Frontend | Medium | Confirmed |
| 8 | A file dropped outside the drop zone opens in the tab | Frontend | Medium | Plausible |
| 9 | A non-ASCII API key gives a 500 | Backend, public API | Low | Confirmed |
| 10 | A text CV containing "%PDF-" is rejected as damaged | Backend, CV reading | Low | Confirmed |
| 11 | A DOCX with a non-integer page count is rejected | Backend, CV reading | Low | Confirmed |
| 12 | "CV from a link" has no overall deadline; port 0 slips through | Backend, public API (in progress) | Medium | Confirmed |
| 13 | 7 AddCandidateForm tests fail | Frontend tests (in progress) | Low | Confirmed |
| 14 | The container runs as root, and the shell eats SIGTERM | Deploy | Low | Plausible |
| 15 | Any startup error shows the login page | Frontend | Low | Confirmed |
| 16 | The progress overlay isn't modal | Frontend, accessibility | Medium | Confirmed |
| 17 | Admin actions can be double-submitted | Frontend | Low | Confirmed |
| 18 | Keyboard focus is lost after deleting a candidate; a double delete gives a false error | Frontend | Low | Confirmed / plausible |
| 19 | Expired sessions are only cleaned up when that user logs in | Backend, auth | Low | Confirmed |
| 20 | A database blip at save can orphan files or report a false error | Backend, candidates | Low | Plausible |
| 21 | A CV just under 10 MB passes the browser check but the server refuses it | Frontend | Low | Plausible |
| 22 | The SSE parser only handles LF framing | Frontend | Low | Plausible |
| 23 | `/docs` and `/openapi.json` are public in production | Backend | Low | Confirmed (decision needed) |

Suggested order: 1–5 first (they can take the live app down, lose cost records or lose a recruiter's work), then 6–11. Items 12 and 13 belong to the uncommitted "CV from a link" work and should be fixed before that is pushed.

---

## Fix first

### 1. A small PDF can use gigabytes of memory
- **Where:** `backend/app/features/resume_intake/service.py`, `read_pdf` (about lines 254–275).
- **Problem:** PDFium decompresses a page's content stream with no size limit, while holding the global `PDFIUM_LOCK`.
- **Scenario:** a 1-page PDF whose content stream is a zlib bomb. In a probe, a 1 MB file that inflates to 1 GB peaked at 2.1 GB of memory; 3 MB inflating to 3 GB took 31 s with the machine swapping. A 10 MB upload (still under the limit) would inflate to about 10 GB and get the Render instance killed, taking every upload in flight with it. Other PDF uploads wait on the lock meanwhile. It happens before any AI call, so the spending cap doesn't stop it; any logged-in user or API-key holder can send it.
- **Fix:** check the inflated size of the PDF's streams before PDFium opens it.
  ```python
  MAX_INFLATED_PDF_BYTES = 64 * 1024 * 1024
  PDF_STREAM = re.compile(rb"stream\r?\n")

  # in read_pdf, before PdfDocument(data):
  # shortcut: covers plain FlateDecode only, not chained filters/LZW; move PDFium into a memory-limited subprocess if those show up.
  view, total = memoryview(data), 0
  for match in PDF_STREAM.finditer(data):
      try:
          total += len(zlib.decompressobj().decompress(view[match.end():], MAX_INFLATED_PDF_BYTES - total + 1))
      except zlib.error:
          continue
      if total > MAX_INFLATED_PDF_BYTES:
          raise ResumeExtractionError(UNREADABLE_MESSAGE)
  ```
- **Test:** a synthetic 1-page Flate-bomb PDF gets a 422, quickly and without a memory spike.

### 2. Paid AI calls aren't recorded when the client disconnects
- **Where:** `backend/app/features/candidates/router.py` (about lines 69–95) and `backend/app/features/resume_intake/router.py` (about lines 127–146). The public API routers likely share it.
- **Problem:** `save_ai_calls_without_intake` runs only after the `except` blocks. When the browser disconnects, Starlette cancels the stream and the generator is closed with `GeneratorExit`, which skips them.
- **Scenario:** a client reads events until "generating" (or "uploading") and disconnects. The structuring call (and any OCR) is paid at OpenAI but never stored. A script can repeat this, so the $5 all-time spending cap (which reads our `ai_calls` table) never trips, and the usage panel under-reports. This isn't the accepted "no answer, no record" gap: these calls did get an answer.
- **Fix:** save the unlinked calls in a `finally`, in both routers.
  ```python
  saved = False
  try:
      ...  # the existing body; set saved = True right after store_candidate / save_resume_intake
  finally:
      if not saved:
          save_ai_calls_without_intake(db, ai_calls)  # also runs on GeneratorExit
  ```
  Remove the current `save_ai_calls_without_intake` line after the `except` blocks.
- **Test:** close the stream after the "generating" event and check that the calls are in `ai_calls`.

### 3. PDF renders wait for a slot forever
- **Status: fixed 2026-11-04.** `render_slots.acquire(timeout=RENDER_WAIT_SECONDS)` (120 s), then the usual 500 "Could not create the PDF" (not a new 503). Test: `test_a_render_that_waits_too_long_for_a_slot_fails_and_frees_nothing`.
- **Where:** `backend/app/features/resume_generation/service.py`, `render_pdf` (`with render_slots:`, about line 329). Reached from `GET /api/resume_generation/{id}`, the public PDF routes and the upload flows.
- **Problem:** the semaphore has no timeout, and each waiting request holds one of anyio's 40 worker threads. Every sync endpoint (login and health included) runs on those threads.
- **Scenario:** a render takes about 10 s on Render with 2 slots (about 12 per minute), while the general rate limit allows 120 requests per minute. One user refreshing PDF links (or an API caller) can fill all 40 threads in about 20 s, and the whole app stops answering.
- **Fix:**
  ```python
  if not render_slots.acquire(timeout=30):
      raise PdfRenderError("The PDF renderer is busy. Try again shortly.")
  try:
      ...  # the existing render
  finally:
      render_slots.release()
  ```
  Consider mapping "busy" to a 503, and counting `/api/resume_generation` against the upload rate limit (`UPLOAD_PATHS`).
- **Test:** with both slots held, a third render fails after the timeout instead of hanging.

### 4. Every upload holds a database connection for its whole run
- **Status: fixed 2026-11-04** with `db.commit()`, not `db.rollback()`: under the tests' `db_session` a rollback undoes the test's own rows. Test: `test_require_ai_budget_returns_the_connection_to_the_pool`.
- **Where:** `backend/app/features/ai_usage/service.py`, `require_ai_budget` (about lines 169–184), together with auth's `require_user` on the same request session.
- **Problem:** the login lookup and the budget check open a transaction that stays open (idle in transaction) for the whole stream: OCR, the AI call (up to 120 s), Chromium and storage.
- **Scenario:** the engine uses the default pool (5 + 10 overflow, 30 s wait, `core/database.py`). About 15 concurrent uploads use every connection, and login, lists and health then wait 30 s and get a false 503 "The database is unavailable". It also eats into Aiven's connection limit.
- **Fix:** end the read-only transaction at the end of `require_ai_budget`:
  ```python
  db.rollback()  # end the read-only transaction so a long upload doesn't pin a pooled connection
  ```
  The later save gets a fresh connection automatically.
- **Test:** after `require_ai_budget`, the session holds no connection (`db.in_transaction()` is false).

### 5. An expired session wipes the interview form
- **Where:** `frontend/src/app/App.tsx` (the `UNAUTHORIZED_EVENT` listener, about lines 86–90, and `page = <LoginPage …>` about line 115), with `frontend/src/shared/api/client.ts`.
- **Problem:** a 401 replaces the whole page with `LoginPage`, which unmounts `InterviewForm` and its inputs.
- **Scenario:** a recruiter who logged in at 9 a.m. starts an interview at 8:55 p.m. and types the 7 answers (up to 5,000 characters each). Submit gets a 401 (the 12-hour session ended), the login page appears, and after logging in the form is empty.
- **Fix:** keep the page mounted and show the login over it when the session ends mid-use (only replace the page on the very first load, when there's no user yet):
  ```tsx
  {sessionExpired && (
    <div className="progress-overlay">
      <LoginPage onLoggedIn={(u) => { setUser(u); setSessionExpired(false) }} />
    </div>
  )}
  ```
  The recruiter then presses Submit again.
- **Test:** fill the form, fire `UNAUTHORIZED_EVENT`, log in, and the answers are still there.

## Next

### 6. A failed logout looks like a logout
- **Where:** `frontend/src/features/auth/LogoutButton.tsx` (about lines 8–15).
- **Problem:** on a network error it still calls `onLoggedOut()`, so the login page shows while the session cookie stays valid. The docstring's "a cookie left behind only gets a 401" is wrong in this case.
- **Scenario:** on a shared office PC, a recruiter clicks Log out during a network blip and walks away. The next person reloads and is logged in as them.
- **Fix:**
  ```tsx
  } catch (caught) {
    if (!(caught instanceof ApiError)) {
      setError('Could not log out. Check your connection and try again.')
      return
    }
  }
  onLoggedOut()
  ```
  Only an answer from the server (any `ApiError`, a 401 included) counts as logged out.
- **Test:** a rejected fetch shows the error and doesn't call `onLoggedOut`.

### 7. A finished upload navigates even after the user left
- **Where:** `frontend/src/features/resume_intake/InterviewForm.tsx` (about lines 94–96) with `App.tsx` (`showResult` → `navigate`).
- **Problem:** `submitResumeIntake` has no abort or "still mounted" guard.
- **Scenario:** the AI step takes 20–30 s; the recruiter presses Back (or tabs past the overlay, see 16) and starts working elsewhere. When the stream ends they are pulled to `#/history/{id}`, and anything half-typed on the other page is lost.
- **Fix:**
  ```tsx
  const mounted = useRef(true)
  useEffect(() => () => { mounted.current = false }, [])
  // ...
  if (mounted.current) onSubmitted(result)
  ```
  Better: pass an `AbortSignal` through `apiPostEventStream` and abort on unmount. Check `AddCandidateForm` for the same pattern.
- **Test:** unmount during the upload, resolve it, and no navigation happens.

### 8. A file dropped outside the drop zone opens in the tab
- **Where:** `frontend/src/features/resume_intake/CvDropzone.tsx` (about lines 35–53).
- **Problem:** only the zone calls `preventDefault` on `dragover`/`drop`, so the browser's default (open the file) applies everywhere else, including the progress overlay.
- **Scenario:** after filling in the answers, the recruiter drags the PDF and misses the zone by a few pixels. Chrome opens the PDF in the same tab and the form is gone. Dropping onto the overlay mid-upload abandons the upload.
- **Fix:** once, in `App` or `main.tsx`:
  ```ts
  window.addEventListener('dragover', (event) => event.preventDefault())
  window.addEventListener('drop', (event) => event.preventDefault())
  ```
  The zone's own handler still runs first.

### 9. A non-ASCII API key gives a 500
- **Status: fixed 2026-11-04** in `core.config.is_public_api_key` (bytes compare), now shared by the key check and the rate limiter. Test: `test_a_non_ascii_api_key_is_a_401_not_a_500`.
- **Where:** `backend/app/features/public_api/router.py`, `require_api_key` (about line 211).
- **Problem:** `secrets.compare_digest(str, str)` raises `TypeError` for non-ASCII strings, and Starlette decodes headers as latin-1. Reproduced: `compare_digest('\xe9', 'abc')` raises.
- **Scenario:** `X-API-Key: é` gives a 500 with a traceback in the logs instead of a 401.
- **Fix:** `secrets.compare_digest(api_key.encode(), expected.encode())`.
- **Test:** a non-ASCII key gets a 401.

### 10. A text CV containing "%PDF-" is rejected as damaged
- **Where:** `backend/app/features/resume_intake/service.py`, the type check (about line 200: `elif b"%PDF-" in data[:1024]`).
- **Scenario:** `b"Jane Doe jane@x.com\nI wrote a tool that parses %PDF-1.7 headers\n"` is sent to PDFium, which fails, and the user gets 422 "The CV file is damaged or unreadable" instead of the text being read.
- **Fix:** treat it as a PDF only when the data starts with `%PDF-` or has binary bytes:
  ```python
  elif data.lstrip()[:5] == b"%PDF-" or (b"%PDF-" in data[:1024] and b"\x00" in data):
  ```
  (Or: when PDFium can't open it and there's no NUL byte, fall back to `read_txt`.)

### 11. A DOCX with a non-integer page count is rejected
- **Where:** `backend/app/features/resume_intake/service.py` (about line 332: `int(pages) > MAX_PAGES`).
- **Scenario:** `docProps/app.xml` holding `<Pages>2.0</Pages>` (or any non-integer) raises `ValueError`, and a readable CV gets 422 "damaged". The decision was that a missing page count is skipped, so a malformed one should be too.
- **Fix:** `if pages is not None and pages.strip().isdigit() and int(pages) > MAX_PAGES:`

## For the uncommitted "CV from a link" work

### 12. "CV from a link" has no overall deadline; port 0 slips through
- **Where:** `backend/app/features/public_api/service.py`, `download_cv` (about lines 93–111). This is the other session's in-progress code; fix it before it is pushed.
- **Problem (a), Medium:** the 30 s deadline is only checked while reading the body. While waiting for the response headers, httpcore only applies the 10 s per-read timeout, so a server sending 1 header byte every 9 s (or endless `100 Continue` responses) holds the request for days. Each one pins a worker thread; enough of them hang the whole app. `socket.getaddrinfo` has no timeout either.
- **Fix (a):** close the client when the overall deadline passes:
  ```python
  with httpx.Client(...) as client:
      watchdog = threading.Timer(DOWNLOAD_DEADLINE_SECONDS, client.close)  # caps the header phase too
      watchdog.start()
      try:
          with client.stream(...) as response:
              ...
      finally:
          watchdog.cancel()
  ```
  Catch the resulting `httpx.HTTPError` / `OSError` as "took too long". Verify with a local slow server.
- **Problem (b), Low:** the "no explicit port" check uses `parsed.port`, which is `0` (falsy) for `https://example.com:0/`, so it passes and fails later with a misleading 422. **Fix:** `parsed.port is not None`.
- **Checked and solid:** https only, every resolved address checked with `is_global`, the connection pinned to the checked IP with the TLS hostname kept, no redirects, no proxy env, no decompression, the 10 MB cap enforced while streaming.

### 13. 7 AddCandidateForm tests fail
- **Where:** `frontend/src/features/candidates/AddCandidateForm.test.tsx`.
- **Problem:** the uncommitted change renamed the submit button from "Add candidate" to "Start", but the tests still look for "Add candidate". Update the tests with that change.

## Later

### 14. The container runs as root, and the shell eats SIGTERM
- **Where:** `Dockerfile` (the last line).
- **Problem:** the shell-form `CMD` makes `sh` PID 1, which doesn't forward SIGTERM, so on every Render redeploy uvicorn is killed after the grace period instead of shutting down cleanly: in-flight uploads die after OpenAI is paid, and their calls aren't saved (see 2). The image also runs as root while Chromium renders AI-derived HTML (JavaScript is off and the network blocked, so this is defence in depth).
- **Fix:** `CMD alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`, and a non-root `USER` (make sure Playwright's browser path is readable by that user).

### 15. Any startup error shows the login page
- **Where:** `frontend/src/app/App.tsx` (about lines 86–87: `.catch(() => setUser(null))`).
- **Scenario:** the database is down. A logged-in recruiter opening the app gets the login page, logs in, then gets a database error, and thinks their password or session is broken.
- **Fix:** only a 401 means "not logged in"; anything else shows "Could not reach the server" with a Retry button:
  ```tsx
  .catch((error) => setUser(error instanceof ApiError && error.status === 401 ? null : 'error'))
  ```

### 16. The progress overlay isn't modal
- **Where:** `InterviewForm.tsx` (`SubmitProgress`) and `AddCandidateForm.tsx` (the progress overlay).
- **Problem:** no focus trap, no `inert` on the content behind it, no `role="dialog"` / `aria-modal`. Keyboard users can tab into the header and leave mid-upload (which leads to 7); screen readers read the page behind the scrim.
- **Fix:** `inert={isSubmitting}` on the page container and the header; `role="dialog" aria-modal="true" aria-labelledby=…` on the card, and focus its heading on mount.

### 17. Admin actions can be double-submitted
- **Where:** `frontend/src/features/auth/AdminPage.tsx` (about lines 48–68).
- **Scenario:** a double-click on Add recruiter shows "Added x@y." and "An account with this email already exists." at the same time.
- **Fix:** an `isBusy` state; return early while busy and set `disabled={isBusy}` on the action buttons.

### 18. Keyboard focus is lost after deleting a candidate; a double delete gives a false error
- **Where:** `frontend/src/features/candidates/CandidateList.tsx` (about lines 258–267).
- **Scenario A (confirmed):** after a delete, the focused row is gone and focus drops to the top of the page.
- **Scenario B (plausible):** two quick confirms on a slow network: the first delete works, the second gets a 404 and alerts "Candidate not found".
- **Fix:** a `deletingId` state that disables that row's button; after removing the row, focus the next row's delete button (or the list heading, with `tabIndex={-1}`).

### 19. Expired sessions are only cleaned up when that user logs in
- **Where:** `backend/app/features/auth/service.py`, `start_session` (about line 366).
- **Problem:** expired rows are deleted only for the user who is logging in, so sessions of removed or idle recruiters stay in `user_sessions` forever (slow growth).
- **Fix:** in `start_session`, delete every expired row: `delete(UserSession).where(UserSession.expires_at <= func.now())`.

### 20. A database blip at save can orphan files or report a false error
- **Where:** `backend/app/features/candidates/service.py` (about lines 263–270) and `candidates/router.py` (the `public_url(candidate.resume_key)` line).
- **Case (a):** the commit reaches Postgres but the connection drops before the reply. `SQLAlchemyError` is raised, both stored files are deleted, and the committed row points at files that no longer exist (the PDF link gives 404).
- **Case (b):** after a successful commit, reading `candidate.resume_key` reloads the row; if that read fails, the user gets a 503 although the candidate and both files were saved.
- **Fix (b):** have `store_candidate` return the key (or the public URL) it already holds, instead of reading it back from the row. **Fix (a), optional:** on an `OperationalError` at commit, check whether the row exists before deleting the files, or log the keys and skip the delete.

### 21. A CV just under 10 MB passes the browser check but the server refuses it
- **Where:** `InterviewForm.tsx` (about line 67) and `AddCandidateForm.tsx` (about line 46).
- **Problem:** the browser compares only the file with 10 MiB; the server's limit counts the whole multipart body, answers included.
- **Scenario:** a 10 MiB − 2 KB CV passes the check, the server answers 413, and in dev the Vite proxy turns that into a reset, so the user sees "Could not reach the server" instead of "The CV is larger than 10 MB." (In production the 413 is shown correctly.)
- **Fix:** leave a margin: `file.size > MAX_CV_BYTES - 64 * 1024`.

### 22. The SSE parser only handles LF framing
- **Where:** `frontend/src/shared/api/client.ts` (about lines 70–89).
- **Problem:** it splits only on `\n\n` and parses each `data:` line on its own, so CRLF framing, a multi-line `data:` or a final event without its blank line would be missed. When `onEvent` throws, the reader isn't cancelled. FastAPI always writes LF and single-line JSON, so nothing fails today; it would break if a proxy rewrote the stream.
- **Fix:** `buffer = (buffer + value).replace(/\r\n?/g, '\n')` before splitting, and wrap the loop in `try { … } finally { reader.cancel().catch(() => {}) }`.

### 23. `/docs` and `/openapi.json` are public in production
- **Where:** `backend/app/main.py` (the `FastAPI(...)` call).
- **Problem:** they list every admin and public route and their schemas to anonymous visitors. Every route still needs a login or the API key.
- **Decision needed:** keep them public (Loxo's developers may use them), or hide them in production with `FastAPI(..., docs_url=None, redoc_url=None, openapi_url=None)` when `STATIC_DIR` exists.

---

## What held up

- **Authentication:** scrypt checks run in constant time (with a dummy hash for unknown emails), and only token hashes are stored. Sessions are created fresh at login and expire on the server after 12 hours. The cookie is HttpOnly, Secure, SameSite=Lax and limited to `/api`, so there's no CSRF. Admins can't reset or delete themselves or another admin, and the admin bootstrap is race-safe.
- **Rate limiter:** its memory is bounded, and URL encoding doesn't get around it.
- **CV parsing:** a DOCX entity bomb, deeply nested XML or a zip bomb gets a 422. So do a malformed OLE `.doc` and odd-length UTF-16 text, and the piece-table guard holds.
- **Dates, phone and links:** `read_date`, the experience sort, `format_phone` and `link_group` can't crash.
- **PDF template and screen:** both escape every value; only http(s) links become `href`, and there's no `dangerouslySetInnerHTML`. Chromium is always closed.
- **NUL characters:** stripped on every write path.
- **Frontend paging and listeners:** paging with `before_id` has no duplicates or skipped rows, past-interview detail can't show a stale reply, every listener is removed, and the theme doesn't flash on load.

## Not covered

- **The public API routers** were not reviewed in depth. They share the upload pipeline, so they probably share issues 2 and 3.
- **The AI usage panel:** left alone by choice; it is a developer page.
- **PDFs with very large numbers of link annotations.**
- **Render's real memory limit:** not measured, so the upload size that kills the instance (issue 1) is an estimate.

Re-run the review after fixing 1–5, since they change the upload path.
