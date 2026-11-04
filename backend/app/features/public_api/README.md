# public_api

**Purpose:** let an outside system (e.g. Loxo) send a resume under its own `candidate_id` and `resume_id` and get the AI-extracted details back, then fetch the branded MinistryHub PDF for that pair; or send a resume and get a permanent link to its branded PDF plus the extracted details (`POST /api/public/pdf`, which also saves a candidate, source `"api"`, or `"url"` when sent as a `file_url`, see [candidates](../candidates/README.md)). Design: [docs/features/public_api.md](../../../../docs/features/public_api.md).

## Auth
Every route needs the header `X-API-Key: <PUBLIC_API_KEY>`. A missing or wrong key, or no `PUBLIC_API_KEY` configured (unset or empty), is **401** `{"detail": "Invalid or missing API key."}`. In `/docs`, use "Authorize" to send it.

## API
**`POST /api/public/resumes`**: multipart form with `file` (PDF, DOCX, DOC or TXT), `candidate_id` and `resume_id` (each 1–100 characters, no control characters, no `/`). Answers **200**:

```json
{"candidate_id": "cand-1", "resume_id": "res-1", "data": { "...ResumeData..." }}
```

A repeat pair replaces the stored details. `data` includes `summary` and `inferred_skills` (added 2026-11-03, `docs/features/resume_upgrade.md`); pairs stored before then read with `null` / `[]`.

| Code | When |
|---|---|
| 401 | Bad or missing key |
| 402 | The AI spending cap (`AI_SPEND_CAP_USD`) is reached; no AI call is made |
| 413 | Request over 10 MB |
| 415 | Not a PDF, DOCX, DOC or TXT |
| 422 | Invalid form fields, or the CV can't be used (no text, over 4 pages, too long, no name or email) |
| 429 | Rate limit: shares the 30-uploads-an-hour bucket with `POST /api/resume_intake`, `POST /api/candidates` and `POST /api/public/pdf` |
| 502 | An AI call (OCR or extraction) failed |
| 503 | The database is unavailable |

Errors are `{"detail": ...}`. Nothing is stored on an error, but AI calls OpenAI answered are stored (unlinked) so they count toward the cap.

**`GET /api/public/resumes/{candidate_id}/{resume_id}/pdf?template_id=1`**: `template_id` defaults to 1 (MinistryHub, the only template). Answers **200** with the PDF, inline, with the same headers as `GET /api/resume_generation/{id}` (`Content-Disposition: inline; filename*=UTF-8''MinistryHub%20-%20<name>.pdf`, `Cache-Control: no-store`). 404 "Template not found." for any other `template_id`; 404 "Resume not found." for an unknown pair; 500 if the stored details can't be read or Chromium fails; 503 if the database is down.

**`POST /api/public/pdf`** (2026-11-03): form with **exactly one of** `file` (multipart upload) or `file_url` (a public https link, at most 8,192 characters; added 2026-11-04), and an optional integer `template_id` (default 1). An empty `file_url` counts as missing; both or neither is **422** "Send exactly one of file or file_url.". Checks run in this order: rate limit (429, the upload bucket), body limit (413), key (401), spending cap (402/503), form (422, e.g. a non-integer `template_id`), then `template_id != 1` is **404** "Template not found.", then the one-of check, then the download, all before any AI call (a bad URL is never billed). Then it reads the CV, runs the AI, renders the PDF and stores it as a candidate (`candidates.service.store_candidate`: the original in the private bucket, the PDF in the public bucket, a `candidates` row with `source="api"`). Answers **200** JSON `PublicPdfResponse` `{url, data}`: `url` is the branded PDF's permanent public URL (`candidates.service.public_url`, the same link `POST /api/candidates` sends in `done`) and `data` is the `ResumeData` it was rendered from. It returned the PDF bytes until 2026-11-03; the user changed it to the link because a PDF body was confusing next to the other JSON endpoints. Errors: 415, 422 (unusable CV), 500 (Chromium), **502** (an AI call, or storing the files: "Could not store the resume files. Try again."; no link is returned then), 503. Usually 5-30 s; the worst case is about 9 minutes (3 AI calls of up to 120 s, rendering, two PUTs with retries), so callers should set a **10-minute** timeout.

**`file_url` rules** (`service.download_cv`, design section "CV by URL"): only `https`, port 443 (or none), no user name or password. The host is resolved over **IPv4 only** (an IPv6-only host fails) and **every** address must be public (`is_global`, not multicast): loopback, private, link-local (cloud metadata), shared `100.64/10` and `0.0.0.0` are refused. The request goes to the first checked IP with the real `Host` header and TLS server name (`sni_hostname`), so a second DNS answer can't swap in an internal address; a new `httpx.Client` per download, `trust_env=False`, no redirects, 10 s per operation, `Accept-Encoding: identity`, a 30 s overall deadline, and the raw body counted against 10 MB. **Every download failure is a 422** (bad URL, blocked address, DNS, timeout, a 3xx: "Send the final URL of the CV.", any other non-200); only a CV over 10 MB (by `Content-Length` or while reading) is **413**. The URL never appears in a message or a log (presigned links carry tokens), only its host. The downloaded bytes are stored as the original CV, like an upload. Known limits: links that need a login fail (422); only the first address is tried; DNS time is bounded only by the OS resolver.

## Files
- `router.py`: `require_api_key` (router-wide), `ai_usage.require_ai_budget` (the POSTs only; the one shared copy), the three endpoints, and `pdf_response` (the PDF with its headers, shared by the GET and `POST /pdf`). The GET and `POST /pdf` are plain `def`s (sync Playwright).
- `service.py`: `save_public_resume` (`INSERT … ON CONFLICT DO UPDATE` plus the AI calls, one commit), `get_public_resume`, and `download_cv` / `resolve_public_ip` / `CvDownloadError` (`status_code` 422 or 413; its own `except` in the router, since joining `resume_intake.CV_ERRORS` would be a circular import). Uses `httpx` (pinned `0.28.1`).
- `schemas.py`: `PublicId` (the id constraint) and `PublicResumeResponse`.
- `models.py`: `PublicResume`.

Reuses `resume_intake.service` (`extract_text`, `structure_resume`), `resume_structuring.service` (`ResumeData`, errors), `resume_generation.service` (`build_resume_html`, `render_pdf`) `ai_usage.service` (`require_ai_budget`, `add_ai_calls`, `save_ai_calls_without_intake`) and `candidates.service` (`store_candidate`, `FileStorageError`, `pdf_content_disposition`).

## Data
Table `public_resumes` (migration `a06140ee4e9b_create_public_resumes_table.py`): `id` identity, `candidate_id` text, `resume_id` text, `structured_data` JSONB NOT NULL, `created_at`, `updated_at` (moved on replace); unique `(candidate_id, resume_id)`. The CV file and text aren't kept. AI calls go to `ai_calls` with `resume_intake_id` NULL.

**Privacy:** logs hold exception types and the row id only, never CV data. The PDF isn't stored.

## Tests
`backend/tests/features/public_api/` (OpenAI and `render_pdf` faked; every request that can store something uses `db_session`):
- `test_router.py`: 401 (no, wrong, empty key; no key configured); POST 200 stores the row and an unlinked AI call; a repeat pair replaces; 415; 422 (no email; invalid ids; 100-character ids accepted); 502 (timeout; a rejected billed answer keeps its call); 402 at the cap; GET 200 headers, default template, 404 unknown pair, 404 `template_id=2`. `POST /pdf`: 401 (with the other routes); 200 with the PDF headers, an `api` candidate, both objects stored and an unlinked call; `template_id=1` accepted; `template_id=2` 404 with no AI call and nothing stored; a non-integer `template_id` 422; a storage failure 502 with no PDF and nothing left; an AI failure 502; 402 at the cap.
  `file_url` (2026-11-04): a good URL is 200 with a `url` candidate whose original is the downloaded bytes; an empty `file_url` next to a file is ignored; both, neither, an empty `file_url` alone are 422 with no AI call and no request; a URL over 8,192 characters 422; a 404 download 422 with nothing stored; an internal address 422; a `Content-Length` over 10 MB 413; template 404 and the spending cap 402 come before any lookup.
- `test_service.py`: save and read back, replace keeps other pairs, unknown pair is None. `download_cv` (fake DNS and `httpx.MockTransport` from `conftest.py`'s `fake_web`, no network): the request goes to the pinned IP with the `Host` header, `sni_hostname`, `Accept-Encoding: identity`, a `trust_env=False` client without redirects, a new client per download; `:443` and an IDNA host accepted; http, ftp, file, userinfo, ports 8443/80, no host, not a URL and `InvalidURL` are 422 with no lookup; 127.0.0.1, ::ffff:127.0.0.1, 169.254.169.254, 100.64.0.1, 0.0.0.0, 10/8, 192.168/16, multicast, mixed public/private, localhost and an IP literal are 422 with no request; gaierror, UnicodeError and an empty answer 422; 301/302/307/308 422 and not followed; 204/403/404/500/503 422; oversized `Content-Length` and streamed body 413, exactly 10 MB accepted; the 30 s deadline 422; connection errors 422. No message holds the URL.
- `tests/core/test_rate_limit.py`: the public POSTs share the upload bucket; a valid key is counted by the key, not the IP.
