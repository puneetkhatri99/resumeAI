# resume_intake

**Purpose:** the recruiter screen's backend. The interviewer uploads a candidate's CV with the interview answers. The API reads the CV's text (a scanned PDF by OCR), has AI extract its details (through `resume_structuring`), stores both with the answers in Postgres, and returns the stored record. It streams its progress as Server-Sent Events. The original file is not kept. Designs: [docs/features/resume_intake.md](../../../../docs/features/resume_intake.md), and [docs/features/resume_structuring.md](../../../../docs/features/resume_structuring.md) for the AI step and the event stream.

## API
`POST /api/resume_intake`: a `multipart/form-data` body. Answers **200** with `text/event-stream` (Server-Sent Events); the stream closes after the result or an error.

| Form field | Type | Rule |
|---|---|---|
| `file` | file | The CV: PDF, DOCX, DOC (Word 97+) or TXT, detected by content, not by name. At most 4 pages. |
| `notice_period` | text | 1–200 characters |
| `salary_expectation` | text | 1–200 characters (free text, e.g. "$60k/year") |
| `work_mode` | text | `remote`, `hybrid` or `onsite` |
| `rating` | integer | 1–5 |
| `strengths` | text | 1–5,000 characters |
| `concerns` | text | 1–5,000 characters |
| `overall_notes` | text | 1–5,000 characters |

All fields are required. Before the length check, every text answer goes through `clean_text`: `\r\n` and `\r` become `\n` (so a browser's line break counts as one character, as `maxLength` counts it), C0 control characters other than tab and newline are dropped, and surrounding whitespace is stripped. An answer that is empty after that is rejected.

**Events.** Each event's `data:` is one JSON `IntakeEvent`. Every event carries all four fields, with `null` for the unused ones. Between events, FastAPI sends `: ping` keepalive comments while a step runs.

```
{"stage": "reading",    "result": null, "status": null, "detail": null}   (reading the text; OCR for a scan)
{"stage": "extracting", "result": null, "status": null, "detail": null}   (the AI step, and the OCR retry)
{"stage": "done",  "result": ResumeIntakeResponse, ...}
{"stage": "error", "status": 415|422|502|503, "detail": "<message>", ...}
```

The stream is `reading`, `extracting`, then `done`; or it ends early with one `error` event. "Uploading" isn't an event: the endpoint only runs once the whole upload has arrived.

**Result (`ResumeIntakeResponse`):** every answer, plus `id`, `original_filename`, `file_type` (`pdf`, `docx`, `doc` or `txt`), `extracted_text`, `structured_data` (a `ResumeData`, see `resume_structuring`; `null` only on rows stored before AI extraction existed) and `created_at`.

**Errors.** No intake is stored on any error. The AI calls OpenAI did answer (and bill) are still stored, unlinked, in `ai_calls` (see `ai_usage`).

| Code | When | How it arrives |
|---|---|---|
| 413 | The request is over 10 MB (`MAX_REQUEST_BYTES` in `app/main.py`, Starlette `RequestBodyLimitMiddleware`). | Plain HTTP, text `Content Too Large` |
| 422 | A missing or invalid answer. | Plain HTTP, FastAPI validation list |
| 415 | By content, the CV isn't a PDF, DOCX, DOC or TXT (images, RTF, `.xls`, other zips or OLE files). | `error` event, after `reading` |
| 422 | Supported type but unusable: over 4 pages, no text (including a scan whose OCR finds none), password-protected (PDF, encrypted Office file, encrypted `.doc`), damaged, Word 6/95. | `error` event, after `reading` (the AI extraction isn't called) |
| 502 | The OCR call for a scanned PDF failed or was cut short. | `error` event, after `reading` |
| 422 | The CV text is over 50,000 characters, or the AI found no name or email (for a text-layer PDF: even after the OCR retry). | `error` event, after `extracting` |
| 502 | The AI call failed (timeout, bad key, outage, refusal, unusable output), or the OCR retry failed. | `error` event, after `extracting` |
| 503 | The database is unavailable. | `error` event, after `extracting` |

FastAPI resolves the form before the stream starts, so the 413 and the answer 422s never start a stream.

**Form parameters:** the router declares the seven answers as separate `Form` parameters and builds `InterviewAnswers` from them. FastAPI 0.156.8 doesn't flatten a form model (`Annotated[InterviewAnswers, Form()]`) once an `UploadFile` parameter sits next to it; it then expects a single field named `answers`.

### Past interviews (reads)
| Endpoint | Returns | Errors |
|---|---|---|
| `GET /api/resume_intake?before_id=&limit=` | `IntakeList`: `items` (newest first) and `has_more`. Each `IntakeSummary` has `id`, `created_at`, `original_filename`, `candidate_name`, `candidate_email` (both from `structured_data`, `null` on rows from before AI extraction), `rating`, `work_mode` and `ai_cost_usd` (the summed cost of the intake's AI calls in USD, `null` for intakes from before AI usage was recorded); never the CV text. `limit` is 1–100 (default 20). With `before_id`, only older intakes (smaller ids) are listed: paging by id, so a submit made while paging can't cause duplicates. | 422 (bad `limit`/`before_id`), 503 |
| `GET /api/resume_intake/{intake_id}` | `ResumeIntakeResponse`, the same shape as the upload's `done` result | 404 "Intake not found.", 503 |

Both are read by the frontend's past-interviews page. `service.list_resume_intakes(db, before_id, limit)` does the list query (`ai_cost_usd` comes from `ai_usage.service.intake_cost_column`); `service.get_resume_intake(db, intake_id)` (also used by `resume_generation`) reads one row; `service.list_candidate_names(db, intake_ids)` returns `{id: name}` for the AI usage panel's recent calls.

**No access control:** anyone who can reach the API can list every candidate's name and email. Fine while the app runs on localhost only; auth is needed before hosting.

## Files
- `router.py`: the endpoint, a sync generator with `response_class=EventSourceResponse` and no `response_model` or `status_code`. The `Iterator[IntakeEvent]` return annotation makes FastAPI validate and serialise each event; FastAPI runs the generator in the thread pool and keeps the DB session and the uploaded file open until the stream ends. One `try` maps `UnsupportedFileTypeError` → 415, `ResumeExtractionError` and `ResumeStructuringError` → 422, `AiExtractionError` → 502 and `SQLAlchemyError` → 503 into one `error` event. An exception escaping the generator would cut the stream with no final event (the frontend treats that as a failed submission).
  - **AI usage:** the endpoint creates `ai_calls: list[AiCall] = []` before the `try` and passes it to `extract_text`, `structure_resume` and `save_resume_intake`, which collect every answered OpenAI call in it. On success the calls are stored linked to the intake. On every error, before the `error` event, `ai_usage.service.save_ai_calls_without_intake(db, ai_calls)` stores them unlinked (it rolls back first, and a database error there is only logged, so the upload's own error still comes).
- `schemas.py`: `InterviewAnswers`, `ResumeIntakeResponse`, `IntakeEvent`, the shared answer types and `clean_text`. `clean_text` lives here because the answer validators need it; the service imports it.
- `service.py`: `save_resume_intake(db, filename, file_type, text, structured_data, answers, ai_calls)` (stores the row, then flushes for its id and adds the upload's AI calls linked to it, all in one commit), the text extraction (`extract_text`), the AI step with its OCR retry (`structure_resume`), and the reads. `extract_text` and `structure_resume` take the `ai_calls` list as a required last parameter, so a forgotten argument fails loudly instead of undercounting. `get_resume_intake(db, intake_id) -> ResumeIntake | None` is the slice's read API for other features (rule 7): `resume_generation` reads a row's `structured_data` only through it.
  - **`extract_text(data, ai_calls) -> (file_type, text, read_by_ocr)`:** detects the type and reads the text. A PDF with no text layer (`read_pdf` returns `""`) is read by OCR (`resume_structuring.transcribe_pdf`), after the page and password checks, outside the parser `try` (so an OCR failure stays a 502) and outside the PDFium lock. Empty OCR text is the usual 422 "No text was found in the CV."
  - **`structure_resume(data, file_type, text, read_by_ocr, ai_calls) -> (text, ResumeData)`:** calls `extract_resume_data`. When it raises `MissingContactError` for a PDF read from its text layer, the PDF is OCR'd once and structured again: the name or email may sit in an image header, or the text layer may be garbled or hold only a watermark. The "Links in the PDF" block (`LINKS_HEADING`) is re-appended to the OCR text, because OCR can't see link annotations. Empty OCR text re-raises the original error; a second miss is a 422. The router stores the returned text, so it matches the stored data.
  - **OpenAI requests per upload:** 1 for a text CV; 2 for a scanned PDF (OCR plus structuring); 3 for a text PDF that needs the retry. Each answered one is recorded as `structuring`, `ocr` or `ocr_retry`.
  - **Type detection, in order:** OLE magic (then `EncryptedPackage` → 422, `WordDocument` → `doc`, anything else → 415), ZIP (`word/document.xml` → `docx`, else 415), `{\rtf` → 415, `%PDF-` in the first 1,024 bytes → `pdf`, no NUL bytes (or a UTF-16 BOM) → `txt`, else 415. HTML saved as `.doc` is read as text.
  - **PDF:** `pypdfium2`, behind a module-level lock (PDFium isn't thread-safe). Page text is joined with blank lines; a PDF with no text layer returns `""` (then OCR, above). The web addresses of the PDF's link annotations (http(s) or `www.`, not `mailto:`/`tel:`) that aren't already in the text are appended once each under "Links in the PDF:", because a LaTeX/Overleaf CV shows links as words like "GitHub" and the address is only in the annotation. That way the AI can fill `links`.
  - **DOCX:** standard library only (`zipfile`, `xml.etree`). Reads headers, body, then footers. Enforces `<Pages>` from `docProps/app.xml` when present, and a 20 MB cap on the uncompressed XML read.
  - **DOC:** `olefile` plus our own Word 97 piece-table reader (`read_doc_streams`, `clean_doc_text`). Uses the page count from the summary information when present.
  - **TXT:** BOM encoding, else UTF-8, else cp1252.
  - Any unexpected parser error becomes a 422 "damaged or unreadable"; only the exception type is logged.
- `models.py`: `ResumeIntake`.

## Data
Table `resume_intakes` (migrations `alembic/versions/3203d1dc9ae4_create_resume_intakes_table.py` and `a8d5bd5c3c32_add_structured_data_to_resume_intakes.py`; `2349c371bb8a_add_location_to_structured_data.py` is a data migration that adds `"location": null` to every stored `structured_data` object). Every column except `structured_data` is NOT NULL.

| Column | Type | Notes |
|---|---|---|
| `id` | integer | Primary key, `GENERATED BY DEFAULT AS IDENTITY` |
| `original_filename` | text | As uploaded, after `clean_text` |
| `file_type` | varchar(4) | `pdf`, `docx`, `doc` or `txt` |
| `extracted_text` | text | The CV's text after `clean_text` |
| `structured_data` | jsonb, nullable | `ResumeData` from the AI step. Null only on rows stored before AI extraction existed; new rows always have it. |
| `notice_period`, `salary_expectation` | text | |
| `work_mode` | varchar(6) | |
| `rating` | smallint | |
| `strengths`, `concerns`, `overall_notes` | text | |
| `created_at` | timestamptz | Server default `now()` |

`ai_calls.resume_intake_id` (the `ai_usage` table) references `resume_intakes.id` with `ON DELETE SET NULL`, so deleting an intake keeps its AI calls, unlinked.

**Privacy:** CV text and answers are never logged. The engine sets `hide_parameters=True`, so SQL errors don't include them either. No temporary files of our own are written. The CV text, and a PDF that needs OCR (under the fixed name `cv.pdf`), go to OpenAI with `store=False` (see `resume_structuring`).

**Known limits (accepted):** if the browser leaves mid-extraction, the AI call still finishes and the row is saved though nobody sees it, so a resubmit creates a duplicate. When the database is down, each attempt spends its AI requests (1 to 3, see above) before the 503, and those calls can't be recorded either. A scan read by OCR up front loses the addresses of any link annotations it has (OCR sees only the page images; the retry path keeps them). Real paper scans have none, so the user chose to leave this (2026-10-23).

## Tests
`backend/tests/features/resume_intake/`
- `test_service.py`: extraction for every type, the page and size limits, the `.doc` piece-table reader, and rejections, with no database; OCR in `extract_text` (a blank PDF is OCR'd, a text PDF isn't, empty OCR is a 422, a 5-page or password-protected PDF is rejected before any OCR call); `structure_resume` (one OCR retry for a text-layer PDF that keeps the links block and records `structuring`, `ocr_retry`, `structuring`, no retry for TXT/DOCX/DOC or an OCR'd PDF, a second miss and empty OCR are `MissingContactError`); `get_resume_intake` returns a saved row and `None` for an unknown id (Postgres, through `db_session`). The real Word 97 test uses `C:\Windows\System32\MSDRM\MsoIrmProtector.doc` and is skipped when that file is missing.
- `test_router.py`: the endpoint, against Postgres, through the `db_session` fixture (rolled back after each test). `read_events` checks for a 200 event stream and returns each event's data. Covers: the `reading` → `extracting` → `done` stream and the stored `structured_data`; the NUL and CRLF answer cases; 415, 422 (empty file) and 503 events; a scanned PDF is OCR'd and stored, an OCR failure is a 502 event that stores nothing, and a text PDF without an email is OCR'd and its OCR text (with the links) is stored; an unreadable CV stops before `extracting`, so the AI isn't called and nothing is stored; a blank email (422 event) and an AI timeout (502 event) store nothing; the 413s and the answer 422s are still plain HTTP. AI usage: a stored scan has its `ocr` and `structuring` calls linked to it with their tokens and cost; a cut-short OCR answer is a 502 that stores one unlinked `ocr` call; a timeout stores no call; with the database down the unlinked save fails, is logged by type, and the 503 still comes; the list's `ai_cost_usd` sums an intake's calls, or is null without any. Every test whose request can store something (including a failed upload that got an AI answer) uses `db_session`.
- `conftest.py`: `make_pdf`, a builder for small hand-written PDFs.
- OpenAI is never called: the autouse `fake_openai` fixture in `backend/tests/conftest.py` stands in for it.
