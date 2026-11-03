# candidates

**Purpose:** a recruiter adds a candidate by dropping one CV. AI reads it, the branded MinistryHub PDF is rendered, both files are stored in Neon Object Storage, and the upload streams its progress as Server-Sent Events. The candidate page lists every candidate (added here, or sent to the public PDF API) with links to both files, the time added, the source and the AI cost, and can delete one. Design: [docs/features/candidates.md](../../../../docs/features/candidates.md).

## API
**`POST /api/candidates`** (SSE): multipart form, field `file` (PDF, DOCX, DOC or TXT). Before the stream: 422 (no `file`), 413 (over 10 MB, plain text), 402 `{"detail": "AI spending limit reached. Uploads are paused."}` (`ai_usage.require_ai_budget`), 503 (database down during that check), 429 (shares the 30-uploads-an-hour bucket). Then **200** `text/event-stream`, each event a `CandidateEvent`:

```
{"stage": "reading" | "extracting" | "generating" | "uploading" | "done" | "error",
 "new_resume_url": str | null,   # only on "done": the PDF's permanent public URL
 "status": int | null, "detail": str | null}   # only on "error"
```

`done` carries **only** `new_resume_url` (no candidate id). Error events:

| Status | Cause |
|---|---|
| 415 | Not a PDF, DOCX, DOC or TXT |
| 422 | The CV text can't be used (no text, over 4 pages, too long, no name or email) |
| 502 | An AI call failed, or storing the files failed ("Could not store the resume files. Try again.") |
| 500 | Chromium couldn't render the PDF ("Could not create the PDF. Try again.") |
| 503 | The database is unavailable on save |

On any error nothing is stored: objects already written are deleted (best effort), and the AI calls OpenAI answered are stored unlinked so they count toward the spending cap.

**`GET /api/candidates?before_id=&limit=`**: `before_id` 1–2,147,483,647 (optional), `limit` 1–100 (default 20). **200** `{"items": [CandidateSummary], "has_more": bool}`, newest first. `CandidateSummary`: `id`, `name`, `created_at`, `source` (`"manual"` / `"api"`), `ai_cost_usd` (null when every call was unpriced), `original_resume_url` (presigned GET, expires after 1 hour), `new_resume_url` (permanent public URL). 422 bad paging; 503 database down.

**`DELETE /api/candidates/{candidate_id}`** (1–2,147,483,647): deletes both objects, then the row. **204**; 404 "Candidate not found."; 502 "Could not delete the resume files. Try again." (the row is kept); 503 database down (if the objects were already deleted the row stays with dead links, and a retry finishes: deleting a missing key succeeds in S3); 422 out-of-range id.

## Files
- `router.py`: the three endpoints. The POST orchestrates `extract_text` → `structure_resume` → `build_resume_html` + `render_pdf` → `store_candidate`, like `resume_intake/router.py`. It is a plain `def` (sync Playwright).
- `service.py`: the module-level boto3 client `s3`, `FileStorageError`, `store_candidate` (two PUTs, then the row with `ai_cost_usd = total_cost(ai_calls)` and the unlinked calls in one commit; cleanup on failure), `list_candidates`, `delete_candidate`, `original_url` (presigned), `public_url`, `pdf_content_disposition` (also used by `public_api`).
- `schemas.py`: `Source`, `CandidateEvent`, `CandidateSummary`, `CandidateList`.
- `models.py`: `Candidate`.

Uses `resume_intake.service` (`extract_text`, `structure_resume`, errors), `resume_structuring.service` (errors), `resume_generation.service` (`build_resume_html`, `render_pdf`, `PdfRenderError`) and `ai_usage.service` (`require_ai_budget`, `add_ai_calls`, `total_cost`, `save_ai_calls_without_intake`). `public_api` uses `store_candidate`, `FileStorageError` and `pdf_content_disposition`.

## Data
Table `candidates` (migration `3aee3f10cd78_create_candidates_table.py`): `id` identity PK, `created_at` timestamptz default `now()`, `name` text (AI-extracted), `source` varchar(6) (`manual` = web app, `api` = `POST /api/public/pdf`; validated in code), `file_type` varchar(4), `original_key` text, `resume_key` text, `ai_cost_usd` numeric NULL (the successful upload's calls only, at save time). Not stored: the CV text and the structured data. The upload's `ai_calls` rows have `resume_intake_id` NULL, so they show unlinked on the AI usage panel.

## Storage (Neon Object Storage, S3-compatible)
- **Settings** (all required; the app won't start without them): `AWS_ENDPOINT_URL_S3`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `RESUME_BUCKET` (private: the originals), `PUBLIC_RESUME_BUCKET` (public-read, set in the Neon Console: the branded PDFs).
- **Keys:** one random `uuid4` prefix per candidate: `candidates/{uuid}/original.{pdf|docx|doc|txt}` (private bucket) and `candidates/{uuid}/resume.pdf` (public bucket). No personal data in keys.
- **PUT metadata:** the original gets its type's `ContentType`, `ContentDisposition: attachment` (an HTML file saved as `.doc` must never render) and `CacheControl: no-store`; the PDF gets `application/pdf` and `inline; filename*=UTF-8''MinistryHub%20-%20<name>.pdf`.
- **URLs:** the original through a presigned GET valid for `ORIGINAL_URL_EXPIRY_SECONDS = 3600` (signed locally; a page left open longer needs a reload); the PDF through the path-style `{AWS_ENDPOINT_URL_S3}/{PUBLIC_RESUME_BUCKET}/{key}`.
- **Client:** path-style addressing, 5 s connect / 30 s read timeouts, 2 attempts. Neon answers 501 to ACL, encryption and bucket-policy calls, so none are ever sent.
- **Order:** storage is written before the row (a row never points at a missing file); a failed second PUT deletes the first object, a failed commit deletes both. If that cleanup fails, the orphan's key is logged (it holds no personal data).
- **Neon's DeleteObject is a soft delete:** the object leaves the bucket but stays recoverable through Neon's branch history until that history expires. The candidate page says nothing about this.

**Privacy:** no CV content, name or presigned URL is logged; storage errors log the S3 error code only. The branded PDFs are public by the user's choice (anyone with the URL can read them); the UUID keys are the only protection. Every route here needs a login (`auth.require_user`, attached in `app/main.py`). A presigned original-CV link already handed out stays valid for its hour (accepted).

## Tests
`backend/tests/features/candidates/` (OpenAI faked by `fake_openai`, storage by the autouse `fake_s3` in `tests/conftest.py`, `render_pdf` by `FakeRenderer`; presigning stays real; every request that can store something uses `db_session`):
- `test_router.py`: the stages in order and `done` with only the public URL; the stored row (`source="manual"`, cost, keys) and both objects with their metadata; a null cost when every call is unpriced; a scan (OCR) and the OCR retry; error events 415, 422 (empty CV; no email), 502 AI, 500 render, 502 for either PUT failing (nothing left behind), 503 on commit (both objects deleted), a failed cleanup logging the orphan keys; 402, 503 cap check, 413, 422 no file; list paging, fields, presigned and public URLs, 422 bad paging, 503; delete 204, 404, 422, 502 (row kept), 503 then a retry 204. Logs never hold the candidate's name.
- `test_service.py`: the PDF file name is percent-encoded; the public URL has no double slash.
- `tests/features/public_api/test_router.py`: `POST /api/public/pdf`. `tests/core/test_rate_limit.py`: both new upload paths hit 429 on the 31st upload in an hour.
