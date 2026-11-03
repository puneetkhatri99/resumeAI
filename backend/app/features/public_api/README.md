# public_api

**Purpose:** let an outside system (e.g. Loxo) send a resume under its own `candidate_id` and `resume_id` and get the AI-extracted details back, then fetch the branded MinistryHub PDF for that pair; or send a resume and get its branded PDF straight back (`POST /api/public/pdf`, which also saves a candidate, source `"api"`, see [candidates](../candidates/README.md)). Design: [docs/features/public_api.md](../../../../docs/features/public_api.md).

## Auth
Every route needs the header `X-API-Key: <PUBLIC_API_KEY>`. A missing or wrong key, or no `PUBLIC_API_KEY` configured (unset or empty), is **401** `{"detail": "Invalid or missing API key."}`. In `/docs`, use "Authorize" to send it.

## API
**`POST /api/public/resumes`**: multipart form with `file` (PDF, DOCX, DOC or TXT), `candidate_id` and `resume_id` (each 1–100 characters, no control characters, no `/`). Answers **200**:

```json
{"candidate_id": "cand-1", "resume_id": "res-1", "data": { "...ResumeData..." }}
```

A repeat pair replaces the stored details.

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

**`POST /api/public/pdf`** (2026-11-03): multipart form with `file` and an optional integer `template_id` (default 1). Checks run in this order: rate limit (429, the upload bucket), body limit (413), key (401), spending cap (402/503), form (422, e.g. a non-integer `template_id`), then `template_id != 1` is **404** "Template not found." before any AI call. Then it reads the CV, runs the AI, renders the PDF and stores it as a candidate (`candidates.service.store_candidate`: the original in the private bucket, the PDF in the public bucket, a `candidates` row with `source="api"`). Answers **200** with the PDF bytes and the same headers as the GET above. Errors: 415, 422 (unusable CV), 500 (Chromium), **502** (an AI call, or storing the files: "Could not store the resume files. Try again."; no PDF is returned then), 503. Usually 5-30 s; the worst case is about 9 minutes (3 AI calls of up to 120 s, rendering, two PUTs with retries), so callers should set a **10-minute** timeout.

## Files
- `router.py`: `require_api_key` (router-wide), `ai_usage.require_ai_budget` (the POSTs only; the one shared copy), the three endpoints, and `pdf_response` (the PDF with its headers, shared by the GET and `POST /pdf`). The GET and `POST /pdf` are plain `def`s (sync Playwright).
- `service.py`: `save_public_resume` (`INSERT … ON CONFLICT DO UPDATE` plus the AI calls, one commit) and `get_public_resume`.
- `schemas.py`: `PublicId` (the id constraint) and `PublicResumeResponse`.
- `models.py`: `PublicResume`.

Reuses `resume_intake.service` (`extract_text`, `structure_resume`), `resume_structuring.service` (`ResumeData`, errors), `resume_generation.service` (`build_resume_html`, `render_pdf`) `ai_usage.service` (`require_ai_budget`, `add_ai_calls`, `save_ai_calls_without_intake`) and `candidates.service` (`store_candidate`, `FileStorageError`, `pdf_content_disposition`).

## Data
Table `public_resumes` (migration `a06140ee4e9b_create_public_resumes_table.py`): `id` identity, `candidate_id` text, `resume_id` text, `structured_data` JSONB NOT NULL, `created_at`, `updated_at` (moved on replace); unique `(candidate_id, resume_id)`. The CV file and text aren't kept. AI calls go to `ai_calls` with `resume_intake_id` NULL.

**Privacy:** logs hold exception types and the row id only, never CV data. The PDF isn't stored.

## Tests
`backend/tests/features/public_api/` (OpenAI and `render_pdf` faked; every request that can store something uses `db_session`):
- `test_router.py`: 401 (no, wrong, empty key; no key configured); POST 200 stores the row and an unlinked AI call; a repeat pair replaces; 415; 422 (no email; invalid ids; 100-character ids accepted); 502 (timeout; a rejected billed answer keeps its call); 402 at the cap; GET 200 headers, default template, 404 unknown pair, 404 `template_id=2`. `POST /pdf`: 401 (with the other routes); 200 with the PDF headers, an `api` candidate, both objects stored and an unlinked call; `template_id=1` accepted; `template_id=2` 404 with no AI call and nothing stored; a non-integer `template_id` 422; a storage failure 502 with no PDF and nothing left; an AI failure 502; 402 at the cap.
- `test_service.py`: save and read back, replace keeps other pairs, unknown pair is None.
- `tests/core/test_rate_limit.py`: the public POSTs share the upload bucket.
