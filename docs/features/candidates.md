# Candidates: add a candidate, candidate page, public PDF API

**Status:** approved by the user on 2026-11-03 and built (migration `3aee3f10cd78`, run on the local Postgres only). Still to do: the approved live checks (9), the Aiven migration (needs the user's go-ahead), and `PUBLIC_RESUME_BUCKET` in `backend/.env` and on Render.

**One doc, not several:** the asks share one flow (read the CV, AI, render the PDF, store both files, record the cost) and one table. The public endpoint and the shared spending-cap dependency are small changes to existing slices (`public_api`, `ai_usage`), and they only make sense with the `candidates` slice.

## 1. Goal
The user, verbatim (2026-11-03): *"create new API's, i want 3 things. 1: add candidates (a candidate will add it's resume, my single api will fetch data from the ai and then create a resume and upload it to the aws s3 and only send the new resume s3 url. 2: candidate page, it will show the candidate name, old resume, new resume, time added, source and ai cost. 3: ai costing page. i also want a public api that will only work with a secret key and user will just send the resume file with template number(default 1) and they will get new resume pdf. i want to keep it simple so not adding queue and workers for now. for application use SSE so that i can get constant updates of what is happening at what stage. i have also added the S3 url in .env"*

## 2. The user's answers (2026-11-03)
1. **Storage is Neon Object Storage** (S3-compatible). It uses two buckets:
   - the original CVs go in the private `RESUME_BUCKET`;
   - the branded PDFs go in a second, **public** bucket that the user creates in the Neon Console (new setting `PUBLIC_RESUME_BUCKET`).

   The existing setting names stay. Every storage setting becomes required, and Render needs the same values.
2. **The add-candidate stream's `done` event carries only the new resume's permanent public URL** (no candidate id). The candidate page shows the original through a presigned link that expires after 1 hour, and the new resume through its permanent public URL.
3. **Recruiters use Add candidate; there is no login yet.** Login is the next feature. **Accepted for now:** anyone with the Render address can open the candidate page and download every original CV.
4. **The source is set by the system:**
   - `"manual"`: the web app upload;
   - `"api"`: the new public PDF API.

   The new public API also saves a candidate with both files stored. If storage fails, the caller gets an error, not the PDF. The existing `candidate_id`/`resume_id` public API stays unchanged.
5. **Retention:** the candidate page gets a delete action, which removes the row and both objects.
6. **"AI costing page"** is the existing `#/usage` panel, unchanged. Each candidate's cost shows on the candidate page.
7. **`boto3` is approved,** with an exact pin. **Live checks are approved,** and the main session runs them: 1 Neon storage round trip with a synthetic file, and 1 full synthetic upload through the add-candidate API with a text CV (1 OpenAI request).
8. **Settled defaults:**
   - additive to the interview flow (that flow is untouched);
   - template 1 only, with no picker in the app;
   - the form field is named `template_id`.

## 3. Flow

### 3.1 Add a candidate (web app, SSE)
1. The recruiter opens `#/candidates/new`, drops one CV and clicks **Add candidate**. The button and dropzone are disabled while sending: a double submit would pay the AI twice.
2. The browser sends `POST /api/candidates` (multipart, field `file`) and reads the event stream (`apiPostEventStream`).
3. Before the stream starts, in this order:
   1. the rate-limit middleware (429; counts in the `api` and `upload` buckets);
   2. the 10 MB body-limit middleware (413);
   3. `require_ai_budget` (402, or 503 if the database is down).
4. Stream:
   1. `reading`: `extract_text` (OCR for a scan).
   2. `extracting`: `structure_resume` (with the OCR retry).
   3. `generating`: `build_resume_html` + `render_pdf`, template 1.
   4. `uploading`: `store_candidate`:
      1. a new `uuid4` key prefix;
      2. PUT the original to the private bucket;
      3. PUT the PDF to the public bucket;
      4. insert the row with `ai_cost_usd` and the upload's AI calls (unlinked), in one commit.
   5. `done` with `new_resume_url` only.
5. On a failure the stream ends with one `error` event. AI calls OpenAI answered are stored unlinked (`save_ai_calls_without_intake`), and objects already written are deleted (best effort).
6. On `done` the page shows **Open new resume** (new tab) and a **View candidates** link to `#/candidates`.

### 3.2 Candidate page
1. `#/candidates` calls `GET /api/candidates` (newest 20, **Load more** by `before_id`).
2. The table shows:
   - name;
   - old resume (presigned link, 1 hour);
   - new resume (public URL);
   - time added (browser time zone);
   - source (`manual` / `api`);
   - AI cost (`formatUsd`, "—" when null);
   - **Delete**.
3. **Delete** asks `window.confirm("Delete this candidate and both resume files?")`. It then sends `DELETE /api/candidates/{id}` and removes the row on 204.

### 3.3 AI costing page
The existing `#/usage` panel, unchanged. Candidate uploads' calls appear there unlinked: no candidate name, and the same as public API calls today.

### 3.4 Public PDF endpoint
The steps run in this order:
1. Rate-limit middleware (429, upload bucket).
2. Body-limit middleware (413).
3. The router-wide `require_api_key` (401).
4. `require_ai_budget` (402/503).
5. Form validation (422).
6. `template_id != 1` gives 404, **before any AI call**.
7. Read, AI and render as in 3.1.
8. `store_candidate(..., source="api")`.
9. **200** with the PDF bytes.

If storage fails, the caller gets 502 and no PDF.

**Duration:** usually 5–30 s. The worst case is bounded by the timeouts:
- AI: a text PDF that needs the OCR retry makes 3 calls of up to 120 s each, so 360 s;
- rendering takes up to about 30 s;
- two PUTs at 2 attempts of up to 35 s each take up to 140 s.

So the worst case is about **9 minutes**, and the guide tells callers to set a **10-minute** client timeout.

## 4. API

### 4.1 `POST /api/candidates` (SSE)
- **Request:** `multipart/form-data`, field `file` (required).
- **Plain HTTP errors before the stream:**
  - 402 `{"detail": "AI spending limit reached. Uploads are paused."}`;
  - 413 (plain text);
  - 422 (no `file`);
  - 429 with `Retry-After`;
  - 503 `{"detail": "The database is unavailable. Try again later."}`.
- **200** `text/event-stream`, each event a `CandidateEvent`:

```
CandidateEvent {
  stage: "reading" | "extracting" | "generating" | "uploading" | "done" | "error"
  new_resume_url: str | null   # only on "done": the PDF's permanent public URL
  status: int | null           # only on "error"
  detail: str | null           # only on "error"
}
```

- **Error events:**

| Status | Cause | Detail |
|---|---|---|
| 415 | `UnsupportedFileTypeError` | existing message |
| 422 | `ResumeExtractionError`, `ResumeStructuringError` | existing messages |
| 502 | `AiExtractionError` | existing messages |
| 500 | `PdfRenderError` | "Could not create the PDF. Try again." |
| 502 | `FileStorageError` (a PUT failed or timed out) | "Could not store the resume files. Try again." |
| 503 | `SQLAlchemyError` on save | "The database is unavailable. Try again later." |

### 4.2 `GET /api/candidates`
- **Query:**
  - `before_id`: int, 1 to 2,147,483,647, optional. The upper bound avoids the out-of-range false 503.
  - `limit`: int, 1–100, default 20.
- **200:**

```
CandidateList { items: CandidateSummary[], has_more: bool }
CandidateSummary {
  id: int
  name: str
  created_at: datetime
  source: "manual" | "api"
  ai_cost_usd: float | null       # null when every call was unpriced
  original_resume_url: str        # presigned GET, private bucket, expires in 1 hour
  new_resume_url: str             # permanent public URL
}
```

- **Errors:** 422, 429, 503.

### 4.3 `DELETE /api/candidates/{candidate_id}`
- `candidate_id`: int, 1 to 2,147,483,647.
- **204** when deleted. Order:
  1. delete both objects (`delete_object` on each bucket);
  2. delete the row and commit.

  Deleting a missing key succeeds in S3, so a retry after any failure is safe.
- **Errors:**
  - 404 "Candidate not found.";
  - 502 "Could not delete the resume files. Try again." The row is kept, so the page still shows the candidate.
  - 503 database. If the objects were already deleted, the row stays with dead links, and a retry finishes the delete.
  - 429.
- **Neon's DeleteObject is a soft delete** on the current branch: the object disappears from the bucket but stays recoverable through Neon's branch history until that history expires. The candidate page says nothing about this. The README and this doc record it.

### 4.4 `POST /api/public/pdf` (public_api slice)
- **Headers:** `X-API-Key`.
- **Request:** `multipart/form-data`:
  - `file` (required);
  - `template_id` int form field (optional, default 1).
- **200** `application/pdf`, with the same headers as the existing public PDF:
  - `Content-Disposition: inline; filename*=UTF-8''MinistryHub%20-%20<name>.pdf`;
  - `Cache-Control: no-store`.
- **Errors** (`{"detail": ...}`):
  - 401;
  - 402;
  - 404 "Template not found." (before any AI call);
  - 413, 415, 422, 429;
  - 500 render;
  - 502 AI or storage;
  - 503.

### 4.5 Unchanged
`POST /api/public/resumes`, `GET /api/public/resumes/{candidate_id}/{resume_id}/pdf` and `GET /api/ai_usage`.

## 5. Data

### 5.1 New table `candidates` (`Candidate`, `candidates/models.py`)
| Column | Type | Constraints | Why |
|---|---|---|---|
| `id` | integer identity | PK | paging, delete |
| `created_at` | timestamptz | NOT NULL, default `now()` | "time added" |
| `name` | text | NOT NULL | AI-extracted; a blank name is already a 422 |
| `source` | varchar(6) | NOT NULL | `"manual"` or `"api"`, validated in code (like `work_mode`) |
| `file_type` | varchar(4) | NOT NULL | the original's extension and Content-Type |
| `original_key` | text | NOT NULL | key in `RESUME_BUCKET` |
| `resume_key` | text | NOT NULL | key in `PUBLIC_RESUME_BUCKET` |
| `ai_cost_usd` | numeric | NULL | the upload's AI cost at save time; null when every call was unpriced |

- Not stored: the CV text and the structured data.
- Indexes: the PK only.
- **`ai_cost_usd`** covers the successful upload's calls only. A failed earlier attempt's calls are unlinked and show only on the usage panel.
- The upload's `ai_calls` rows are stored unlinked (`resume_intake_id = NULL`), like `public_api`, so they still count toward the spending cap.

### 5.2 Migration
- One Alembic revision, `create_candidates_table`, which creates `candidates`. It is additive.
- `alembic/env.py` imports `app.features.candidates.models`.
- Running it on Aiven needs the user's go-ahead.

### 5.3 Storage (Neon Object Storage)
Source: https://neon.com/docs/storage/s3-compatibility (read 2026-11-03).

- **Buckets:**
  - `RESUME_BUCKET`: Neon access **private**; holds the originals.
  - `PUBLIC_RESUME_BUCKET`: Neon access **public_read**; holds the branded PDFs.

  Access is set per bucket in the Neon Console. Bucket and object policy, ACL and encryption writes return 501 on Neon, so the code never sends `ACL`, `ServerSideEncryption` or policy calls.
- **Keys:** one random `uuid4` prefix per candidate, so keys hold no personal data and can't be guessed.
  - `candidates/{uuid}/original.{pdf|docx|doc|txt}` in the private bucket;
  - `candidates/{uuid}/resume.pdf` in the public bucket.
- **PUT metadata** (PutObject supports `ContentType`, `ContentDisposition` and `CacheControl`):

| Object | `ContentType` | `ContentDisposition` | `CacheControl` |
|---|---|---|---|
| original | pdf `application/pdf`, docx `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, doc `application/msword`, txt `text/plain` | `attachment` (an HTML file saved as `.doc` counts as "txt" and must never render) | `no-store` |
| resume.pdf | `application/pdf` | `inline; filename*=UTF-8''MinistryHub%20-%20<name>.pdf` | none: the URL is permanent, and the PDF is public by the user's choice |

- **URLs:**
  - **Original:** a presigned GET, valid for `ORIGINAL_URL_EXPIRY_SECONDS = 3600` (a code constant). Neon supports SigV4 presigned GET and doesn't state a maximum expiry; 1 hour is well inside AWS's 7 days. A page left open longer needs a reload. Presigned URLs are never logged.
  - **New resume:** the path-style public URL `f"{aws_endpoint_url_s3}/{public_resume_bucket}/{resume_key}"` (Neon is path-style only). The key is a UUID path, so no encoding is needed. Neon's docs don't spell out the anonymous URL form; the live round trip confirms it.
- **Client:** one module-level boto3 client in `candidates/service.py`:

```
boto3.client("s3", endpoint_url=..., region_name=..., aws_access_key_id=...,
             aws_secret_access_key=...,
             config=Config(s3={"addressing_style": "path"}, connect_timeout=5,
                           read_timeout=30, retries={"mode": "standard", "max_attempts": 2}))
```

  boto3's default CRC32 PUT checksum is unverified on Neon. The live round trip checks it. Only if PUTs fail do we add `request_checksum_calculation="when_required"` to `Config`.
- **Settings** (`core/config.py`; the names stay as they are; all now **required**, with no `None` default):
  - `aws_endpoint_url_s3: str`
  - `aws_access_key_id: SecretStr`
  - `aws_secret_access_key: SecretStr`
  - `aws_region: str`
  - `resume_bucket: str`
  - `public_resume_bucket: str` (new)

  They are passed to the client explicitly. `backend/.env.example` gets all six. Render needs the same values.
- **Package:** `boto3==1.43.123` (latest on PyPI, 2026-11-03), which pulls `botocore>=1.43.123,<1.44`, `s3transfer` and `jmespath`. All are pinned in `requirements.txt` per the exact-pins rule.

## 6. Slice layout

### Backend `backend/app/features/candidates/` (new)
| File | Does |
|---|---|
| `models.py` | `Candidate` (5.1). |
| `schemas.py` | `Source = Literal["manual", "api"]`, `CandidateEvent`, `CandidateSummary`, `CandidateList`. |
| `service.py` | The storage client and `FileStorageError`, plus four functions. **`store_candidate(db, original, file_type, pdf, name, source, ai_calls) -> Candidate`:** two PUTs, the insert with `ai_cost_usd=total_cost(ai_calls)`, `add_ai_calls(db, ai_calls, None)` and the commit. On a PUT or DB failure it deletes what it wrote (best effort; logs the error type only) and re-raises. **`list_candidates(db, before_id, limit)`**. **`original_url(key)`** (presigned) and **`public_url(key)`**. **`delete_candidate(db, candidate_id) -> bool`**: objects first, then the row; False when the candidate doesn't exist. |
| `router.py` | `POST ""` (SSE; orchestrates like `resume_intake/router.py`), `GET ""` and `DELETE "/{candidate_id}"`. |
| `README.md` | Purpose, API, data, storage layout (incl. Neon's soft delete and 501s), tests. |

### Backend changes elsewhere
| File | Change |
|---|---|
| `ai_usage/service.py` | **`require_ai_budget`** moves here (one copy), with `AI_SPEND_CAP_MESSAGE` and `DATABASE_DOWN_MESSAGE`. It raises `HTTPException`, the one HTTP concern in a service, accepted to keep a single copy. Also new: `total_cost(ai_calls) -> Decimal \| None` (the sum of priced calls; None if none is priced). |
| `resume_intake/router.py`, `public_api/router.py` | Delete their copies and import `require_ai_budget` from `ai_usage.service`. Each router keeps its own `DATABASE_DOWN_MESSAGE` for its other errors (same text). |
| `tests/core/test_rate_limit.py`, `tests/features/resume_intake/test_router.py`, `tests/features/public_api/test_router.py` | Import `require_ai_budget` / `AI_SPEND_CAP_MESSAGE` / `DATABASE_DOWN_MESSAGE` from `app.features.ai_usage.service`. |
| `public_api/router.py` | `POST /pdf` (4.4). |
| `app/main.py` | Mount the candidates router under `/api`. |
| `app/core/config.py`, `backend/.env.example` | Settings (5.3). |
| `app/core/rate_limit.py` | `UPLOAD_PATHS` += `/api/candidates`, `/api/public/pdf`. The module docstring lists all four upload POSTs. |
| `alembic/env.py`, `alembic/versions/` | 5.2. |
| `requirements.txt` | `boto3` and its dependencies, pinned (5.3). |
| `public_api/README.md`, `docs/public_api_guide.md` | The new endpoint, its errors and the 10-minute client timeout. |

### Frontend `frontend/src/features/candidates/` (new)
| File | Does |
|---|---|
| `api.ts` | Types; `addCandidate(file, onEvent)`, `listCandidates(beforeId?)`, `deleteCandidate(id)`. `shared/api/client.ts` has no DELETE helper, so a single `fetch` call here is enough; nothing goes into shared until a second feature needs it. |
| `AddCandidateForm.tsx` | `CvDropzone` (exported by resume_intake), submit (disabled while sending), and a progress overlay. Stages "Extracting text", "AI cleaning", "Creating resume", "Uploading", each held ≥ 700 ms as in `InterviewForm` (the logic is copied; `InterviewForm` is untouched). Errors for 402/413/429, the network and error events show next to the button, and the done view is in 3.1. |
| `CandidateList.tsx` | The table, Load more and Delete (3.2). Links `target="_blank" rel="noopener noreferrer"`. |
| `*.test.tsx`, `README.md` | Tests and docs. |

`app/App.tsx` gets the routes `#/candidates/new` and `#/candidates`, and the nav links "Add candidate" and "Candidates".

## 7. Cross-feature contracts
| Used | From | Through |
|---|---|---|
| `extract_text`, `structure_resume`, `UnsupportedFileTypeError`, `ResumeExtractionError` | resume_intake | `service.py` |
| `ResumeData`, `AiExtractionError`, `ResumeStructuringError` | resume_structuring | `service.py` |
| `build_resume_html`, `render_pdf`, `PdfRenderError` | resume_generation | `service.py` |
| `AiCall`, `add_ai_calls`, `total_cost`, `require_ai_budget`, `save_ai_calls_without_intake` | ai_usage | `service.py` |
| `store_candidate`, `FileStorageError` | candidates (used by public_api) | `service.py` |
| `CvDropzone` | resume_intake (frontend) | exported component |
| `formatUsd`, `apiGet`, `apiPostEventStream` | shared | `shared/` |

`ai_usage` imports nothing from `candidates`.

## 8. Failure modes and security
- **Invalid input:** the existing rules (type by content, 4 pages, 50,000 characters, 10 MB, name and email required). A non-integer `template_id` gives 422; any other integer gives 404 before the AI is paid.
- **AI errors and timeouts:** unchanged (120 s, no retry, 502). Answered calls are stored unlinked. SSE pings keep the stream alive. Public callers: 10-minute client timeout (3.4).
- **Storage down after the AI was paid:** the AI cost is recorded (unlinked) and the user gets 502 and resubmits, paying again. Accepted: there is no queue. The cleanup:
  - storage calls are bounded (5 s connect, 30 s read, 2 attempts);
  - a failed second PUT deletes the first object;
  - a failed commit deletes both.

  If that cleanup also fails, an orphan object stays. Its key is logged and holds no personal data, but a public-bucket orphan stays readable by anyone who has its URL. Nobody does, because the URL was never sent.
- **Double submit:** the button is disabled while sending. A second tab or a repeated API call still pays twice (accepted).
- **Chromium failure:** 500. Nothing is uploaded, and AI calls are stored unlinked.
- **Personal data:**
  - **The original CV file is now kept**, in the private bucket. This supersedes "the original file isn't kept".
  - **The branded PDFs are public by the user's choice:** anyone with a URL can read the PDF forever, until it is deleted. They hold the name, contacts and work history. Unguessable UUID keys are the only protection. The live round trip must confirm that **anonymous listing of the public bucket is refused**; if Neon allows it, every PDF is discoverable, and the user must decide before go-live.
  - **No login (accepted by the user until the next feature):** anyone with the Render address can list candidates, download originals and delete candidates.
  - **Delete is a soft delete on Neon** (4.3).
  - No presigned URL, CV text or name is logged; storage errors log the error code only.
  - The data lives in Neon's `us-east-2` region (AWS). Data residency with the client is still open, as OpenAI's retention already is.
- **Public endpoint:** constant-time key check (unchanged); template 404 before any cost.

## 9. Testing
No test calls OpenAI (`fake_openai`) or Neon. A new autouse `fake_s3` fixture in `backend/tests/conftest.py` replaces the module client's `put_object` / `delete_object` with an in-memory dict keyed by `(bucket, key)`, and can be told to fail. Presigning stays real, since it works offline. The settings come from `backend/.env` (now required).

**API (pytest):**
- **Happy path:**
  - the stages arrive in order;
  - **`done` holds only `new_resume_url`**: its other fields are null, and it has no candidate id;
  - the URL equals `{endpoint}/{public bucket}/{resume_key}`;
  - the row is stored with `source="manual"` and `ai_cost_usd` equal to the sum of the fake calls' costs (null when all are unpriced);
  - the calls are stored unlinked;
  - the original goes to the private bucket and the PDF to the public one, with the 5.3 metadata.
  - Also covered: a scan (OCR) and the OCR retry.
- **Error events:**
  - 415, each 422, 502 AI and 500 render: no objects and no row, calls stored unlinked;
  - first or second PUT fails: 502, nothing left in the fake buckets;
  - commit fails: 503, both objects deleted.
- **Before the stream:**
  - the cap gives 402 with no AI call;
  - the database down during the cap check gives 503;
  - 413;
  - the 31st upload in an hour gives 429, for both new paths.
- **List:**
  - order, `before_id`, `has_more`;
  - the original URL is presigned for the private bucket;
  - the new URL is public;
  - `before_id` beyond int32 gives 422;
  - DB down gives 503.
- **Delete:**
  - 204 removes both objects and the row;
  - unknown id gives 404;
  - an object delete failure gives 502 and keeps the row;
  - a DB failure after the objects are deleted gives 503, then a retry gives 204;
  - an out-of-range id gives 422.
- **Public `POST /pdf`:**
  - 401 for a missing, wrong or unset key;
  - `template_id=2` gives 404 with zero AI calls;
  - the default is template 1;
  - 200 with the PDF headers and a row with `source="api"`;
  - a storage failure gives 502 with no PDF.
- **ai_usage:**
  - `require_ai_budget` and `total_cost` unit tests;
  - the existing intake and public cap tests pass through the moved dependency.
- **Logs:** no presigned URL or candidate name appears in captured logs on any failure path.
- Every test whose request can succeed takes `db_session`.

**UI (Vitest only; Playwright stays paused):**
- **Form:**
  - shows each stage;
  - disables submit while sending;
  - maps 402/413/429, network and error events to messages;
  - shows the done link.
- **List:**
  - renders every column, with "—" for a null cost;
  - Load more;
  - links open in a new tab;
  - Delete confirms, calls the API and removes the row; a cancelled confirm does nothing; an error shows a message.

**Live checks (approved; the main session runs them):**
1. **Neon round trip** with a synthetic file:
   - a PUT to each bucket with boto3's default checksum (if it fails, retry with `when_required` and record it);
   - an anonymous GET of the public URL works;
   - an anonymous GET of the private object is refused;
   - the presigned GET works and returns `attachment`;
   - **an anonymous list of the public bucket is refused** (8);
   - delete both objects; afterwards an anonymous GET of the public URL gives 404.
2. **One full upload** through `POST /api/candidates` with a synthetic text CV (1 OpenAI request), then delete that candidate.

## 10. Decisions
| Decision | Simpler alternative considered | Why this |
|---|---|---|
| New `candidates` slice and table | Reuse `resume_intakes` | Intakes need the interview answers and show in the interview history. |
| SSE stages `reading`, `extracting`, `generating`, `uploading`, then `done`/`error`; sync, no queue | Plain JSON | User asked for SSE and no queue. |
| The router orchestrates the steps; the service stores | A service generator that yields stages | Matches `resume_intake/router.py`. The public endpoint repeats about 5 lines without events. |
| Write storage first, then the row, with a best-effort delete on failure | Row first | A row must never point at a missing file. |
| Delete removes the objects first, then the row | Row first | A failure leaves a visible row and a safe retry, not hidden orphans. |
| Originals private (presigned, 1 h); PDFs public (permanent URL) | Everything private | User choice (2026-11-03). |
| `candidates.ai_cost_usd` set at save time; calls stored unlinked | An `ai_calls.candidate_id` FK | Challenger: no `ai_usage` schema change. Cost is fixed once saved, and the user wants the usage panel unchanged. |
| One `require_ai_budget` in `ai_usage/service.py` | A third copy | Challenger: three copies would drift. It is moved, not rewritten. |
| Store the name only | Store `structured_data` | Nothing asked for needs it, and it keeps less personal data. |
| `boto3` | Hand-written SigV4 | User-approved. Signing is security code. |
| The overlay stage queue is copied into `AddCandidateForm` | Move it to `shared/` | Leaves `InterviewForm` untouched. |

The lasting rows are in `docs/architecture.md` (Decisions, 2026-11-03).

## 11. Open questions
None that block approval. One check could reopen a decision: if the live round trip shows Neon lets anyone **list** the public bucket, every branded PDF becomes discoverable, and the user must choose again (e.g. a private bucket with presigned links) before go-live.
