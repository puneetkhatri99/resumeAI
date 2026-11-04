# Public API (outside systems such as Loxo)

**Status:** built (2026-11-02). The user approved this plan on 2026-11-02. The migration (`a06140ee4e9b_create_public_resumes_table.py`) is written but not yet applied to the Aiven database: that needs the user's go-ahead.

## Why
An outside system (e.g. Loxo) needs two calls, keyed by its own ids:
1. Send a resume with its `candidate_id` and `resume_id`, and get the AI-extracted details back.
2. Ask for the formatted MinistryHub PDF for those ids and a `template_id`.

## The user's decisions (2026-11-02)
- The caller sends **both ids**; a repeat `(candidate_id, resume_id)` pair **replaces** the stored details.
- **Resume only:** no interview answers.
- Auth by an **`X-API-Key` header** (one shared key, env `PUBLIC_API_KEY`).
- API 1 returns **plain JSON** (not Server-Sent Events); API 2 returns the **PDF**.
- `template_id`: only **1** (the MinistryHub resume) exists.

## Design
A new slice `backend/app/features/public_api/` with its own table. Not `resume_intakes`: those rows need the 7 interview answers and show in the web history page, and making the answers nullable would ripple through the UI.

**Table `public_resumes`** (`models.py`, `PublicResume`): `id` (identity), `candidate_id` text, `resume_id` text, `structured_data` JSONB NOT NULL (`ResumeData`), `created_at`, `updated_at`; unique `(candidate_id, resume_id)`. The CV file and its text aren't stored.

**Auth** (`require_api_key`, on the whole router): `X-API-Key` compared with `settings.public_api_key` by `secrets.compare_digest`. Missing or wrong key, or no key configured (unset or empty) → **401** `{"detail": "Invalid or missing API key."}`.

**`POST /api/public/resumes`** (multipart: `file`, `candidate_id`, `resume_id`; ids 1–100 characters, no control characters, no `/`, else 422):
- Same spending-cap check as the intake upload (402 / 503), before any AI call.
- Reuses `resume_intake.service.extract_text` + `structure_resume` (PDF, DOCX, DOC, TXT; OCR for scans), then `service.save_public_resume`: Postgres `INSERT … ON CONFLICT (candidate_id, resume_id) DO UPDATE` (new `structured_data`, `updated_at = now()`) and the AI calls, in one commit.
- **200** `{"candidate_id", "resume_id", "data": ResumeData}`.
- Errors as plain JSON, the same mapping as `POST /api/resume_intake`: 415, 422, 502, 503. On an error nothing is stored, but the AI calls OpenAI answered are stored unlinked (`save_ai_calls_without_intake`).
- AI calls are stored with `resume_intake_id = NULL` (that column only links to `resume_intakes`), so they count toward the spending cap and show on the usage panel, without a candidate name.
- Counts in the 30-uploads-an-hour rate-limit bucket together with `POST /api/resume_intake`.

**`GET /api/public/resumes/{candidate_id}/{resume_id}/pdf?template_id=1`** (`template_id` defaults to 1):
- Any other `template_id` → 404 "Template not found."; unknown pair → 404 "Resume not found.".
- Reuses `ResumeData.model_validate`, `build_resume_html` and `render_pdf` (`resume_generation`), with the same headers as `GET /api/resume_generation/{id}` (inline, percent-encoded filename, `Cache-Control: no-store`). 500 if the stored details don't validate or Chromium fails; 503 if the database is down.

## Not included
Storing the CV text or file; listing public resumes; a frontend; per-client keys.

## Known limits
- The spending cap is checked once per upload (as for the intake), so a running upload can overshoot by its own cost.

## CV by URL on `POST /api/public/pdf` (approved 2026-11-04)
Callers such as Loxo usually already have the resume at a URL. `POST /api/public/pdf` takes either a multipart `file` or a form field `file_url`, and the server downloads the CV itself.

**The user's decisions (2026-11-04, after the challenger's review):**
- Only `/api/public/pdf` gets `file_url`, not `/api/public/resumes`.
- Exactly one of `file` or `file_url` (an empty `file_url` counts as missing). Both or neither is a 422.
- Only public **https** URLs on **port 443**, resolved over **IPv4 only**. An IPv6-only host is a 422.
- **No redirects:** a 3xx is a 422 asking for the final URL. Revisit this if the client's links redirect.
- **Every download failure is a 422** (bad URL, blocked address, DNS, timeout, remote 4xx or 5xx). Only a file over 10 MB is a 413.
- The download uses **httpx** (already installed with FastAPI's extras), pinned as `httpx==0.28.1`.

**Design** (`public_api/service.py`: `download_cv(url) -> bytes` and `CvDownloadError`, which carries `status_code` of 422 or 413):
1. **Parse:** `httpx.URL(url)`; `httpx.InvalidURL` is a 422. The URL must be https, have a host, have no userinfo and use port 443 or no port. The host's ASCII (IDNA) form, `url.raw_host.decode("ascii")`, is used for DNS and SNI. `file_url` is at most 8,192 characters.
2. **Resolve:** `socket.getaddrinfo(host, 443, family=AF_INET, type=SOCK_STREAM)`. Every address must be `is_global` and not `is_multicast`. `gaierror`, `UnicodeError` and an empty result are each a 422.
3. **Pin the first checked IP** so a second DNS lookup can't swap in an internal address (DNS rebinding). The request URL is `url.copy_with(host=ip)`, with header `Host: <original netloc>` and `extensions={"sni_hostname": host}`. httpcore passes `sni_hostname` as `server_hostname`, so TLS still verifies the certificate against the real host name.
4. **Request:**
   - A **new `httpx.Client` for every download**, so a pooled connection verified for another host is never reused.
   - `trust_env=False`, so no proxy env vars or `.netrc` apply.
   - `follow_redirects=False`, with a 10 s timeout per operation.
   - `Accept-Encoding: identity`.
   - Any status other than 200 is a 422.
5. **Size and time:**
   - A `Content-Length` over 10 MB is a 413 straight away.
   - Otherwise the body is read with `iter_raw()` (no decompression, so no decompression bomb), counting raw bytes, and passing 10 MB is a 413.
   - An overall deadline of 30 s is checked in the read loop (`time.monotonic()`).
6. **Privacy:** `file_url` (presigned links carry tokens) never appears in an error message or a log. The host and the remote status code are enough.

**Router:** the order is key → spending cap → template 404 → one-of check → download, so a bad URL is never billed. `data = file.file.read() if file else download_cv(file_url)`, and the rest of the pipeline is unchanged. The downloaded bytes are stored as the original CV, the same as an upload. `CvDownloadError` gets its own `except` (it can't join `resume_intake.CV_ERRORS`, which would be a circular import).

**Tests:** pytest never reaches the network. `socket.getaddrinfo` is monkeypatched and `httpx.MockTransport` is used. The mock asserts the pinned IP, the `Host` header, `sni_hostname`, `Accept-Encoding: identity`, and that the client is built with `trust_env=False`.

**Known limits:**
- A URL that needs a login or token (an authenticated Loxo link) fails with a 422.
- Only the first resolved address is tried.
- DNS lookup time is bounded only by the OS resolver (`getaddrinfo` has no timeout).

**Source (user, 2026-11-04):** a candidate added through `file_url` is saved with source `url`, shown as "API (URL)" on the candidate page. A file sent to the same endpoint stays `api`. The value fits the existing `varchar(6)` column, so no migration is needed.
