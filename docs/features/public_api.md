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
