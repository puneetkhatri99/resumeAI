# resume_generation

**Purpose:** render the branded MinistryHub resume as a PDF from the details AI extracted from a CV (`resume_structuring`, stored by `resume_intake`). The recruiter opens it from the result screen; it is rendered on demand and **not stored**. Design: [docs/features/resume_generation.md](../../../../docs/features/resume_generation.md).

## API
`GET /api/resume_generation/{intake_id}`: no body. Answers **200** with the PDF bytes:

| Header | Value |
|---|---|
| `Content-Type` | `application/pdf` |
| `Content-Disposition` | `inline; filename*=UTF-8''<"MinistryHub - {name}.pdf", percent-encoded>`. The name is AI output, so `quote(..., safe="")` encodes every special character, including `/`, `\`, quotes, CR and LF. There is no plain `filename=`. |
| `Cache-Control` | `no-store` (personal data: the browser keeps no copy) |

**Errors.** The body is `{"detail": "<message>"}` (the frontend's link opens it as plain JSON in the new tab).

| Code | When | `detail` |
|---|---|---|
| 404 | No intake with that id | "Intake not found." |
| 404 | The intake has no `structured_data` (rows from before AI extraction) | "This intake has no extracted details." |
| 422 | `intake_id` isn't an integer | FastAPI validation list |
| 500 | The stored `structured_data` doesn't validate as `ResumeData` (logged with the intake id only; the `ValidationError` quotes CV data, so it is never logged or chained) | "The stored CV details can't be read." |
| 500 | Chromium failed: not installed, crashed or timed out (`PdfRenderError`, cause logged) | "Could not create the PDF. Try again." |
| 503 | The database is unavailable | "The database is unavailable. Try again later." |

## Files
- `router.py`: the endpoint, a plain sync `def` (Playwright's sync API fails inside an `async def`; FastAPI runs a sync endpoint in a worker thread with no event loop). It reads the row through `resume_intake.service.get_resume_intake`, validates it as `ResumeData`, renders, and maps the errors above.
- `service.py`:
  - `build_resume_html(data, today)`: renders `templates/resume.html.j2` with Jinja2 (`autoescape=True`; `select_autoescape()` would not escape a `.j2` file). The template gets three helpers: the `bullets` filter (`split_bullets`), the `http_url` test (`is_http_url`) and the `format_duration` global.
  - `render_pdf(html)`: one headless Chromium per call (`sync_playwright`, `chromium.launch()`), JavaScript off, every routed request aborted (inline `data:` assets still load), `set_content`, a wait for `document.fonts.ready` (`page.pdf` doesn't wait for web fonts), then `page.pdf` at exactly 210 × 297 mm (Playwright's `format="A4"` is 8.27 × 11.7 in, about 1 pt taller) with `PDF_MARGIN`, `print_background=True` and the header/footer templates. Any `playwright.sync_api.Error` becomes `PdfRenderError`.
  - `HEADER_TEMPLATE` / `FOOTER_TEMPLATE`: the branding images as `data:` URIs at 100 % width, with Chromium's default 15 pt header/footer padding overridden so they reach the paper edge on every page. `PDF_MARGIN` (top 44 mm, bottom 23.8 mm, sides 13.2 mm) leaves room for them: the header is 35.6 mm tall and the footer 17.8 mm; top and sides were measured from the sample.
  - Dates: `read_date` reads a date as written into (year, month or None), with "Present", "current", "Till date" and the other ongoing words meaning today. `format_duration` gives a job's length ("10 mos", "1 yr", "11 yrs"; months counted inclusively, whole years from 12 months). `total_years` gives "WORK EXPERIENCE IN YEARS": the earliest readable start to the latest readable end, gaps included, floored to whole years; `None` hides the section. Both share `measure_span`.
  - `split_bullets` splits a description on CR/LF only, removing one leading marker (`•`, `-`, `*`, `·`, `–`, `▪`, `●`, `◦`, `➢`, Word's U+F0B7) only when whitespace follows it, so "-30%" stays as written. `drop_blanks` removes blank values, entries, pills and links (and strips links) before rendering. Entries may break across pages, with each title row kept with its first bullet; long unbroken words wrap. Ids above 2,147,483,647 are a 422. `is_http_url` mirrors the frontend's `linkOrText`.
- `templates/resume.html.j2`: the page, matching the sample: name (yellow), contact grid (email, phone, current location), Professional Summary, Experience & Education (Employment History, Education, Projects), Licenses & Certifications, Skills (pills; no "AREAS OF GIFTING"), Additional Information ("LINK 1", "LINK 2"… only http(s) links are `<a>`). Empty sections and null fields are hidden. AI values go only into text nodes and quoted `href` attributes. Colours were sampled from the sample PDF. Icons are inline Lucide SVG paths.
- `templates/assets/`: `header.png` (1500 × 254) and `footer.png` (1500 × 127), cut once from page 2 of the client's sample PDF (no photo); `Inter-Regular/Medium/SemiBold.woff2` from the official Inter 4.1 release (static fonts: Chrome's PDF backend turns a variable font into Type 3 glyphs); `OFL.txt` (Inter's licence) and `LUCIDE-LICENSE.txt` (Lucide's ISC licence, plus the MIT notice for its Feather-derived icons such as `link`). Everything is inlined as `data:` URIs at import.

## Setup
`playwright` needs its browser once per machine, after `pip install`: `python -m playwright install --only-shell chromium` (the headless shell only, into `%LOCALAPPDATA%\ms-playwright`; no admin rights). Without it every request is a 500 and the log shows Playwright's "run playwright install" hint. A Docker image needs `playwright install --with-deps --only-shell chromium`.

## Data
No table and no column: the PDF isn't stored. It reads `resume_intakes.structured_data` through `get_resume_intake`. The `location` field it shows was added to `ResumeData` with this feature; migration `2349c371bb8a_add_location_to_structured_data.py` added `"location": null` to every stored object (a data migration, no schema change).

**Privacy:** the PDF is built in memory and returned; we write no files (Playwright creates and deletes Chromium's throwaway profile). Logs hold only the intake id, exception types and Playwright's messages, never CV text. The filename holds the candidate's name (user's choice). **No auth and sequential ids:** fine only while the app is localhost-only; auth must come before any deployment.

**Known limits (accepted):** one Chromium launch per PDF (about 1 s warm, 4 s cold); `page.pdf` has no timeout, so a hung Chromium holds one worker thread; the request's DB session stays open while rendering; the branding images are ~180 dpi rasters; Inter is a look-alike of the paid TT Interphases; dates the parser can't read hide the duration.

## Tests
`backend/tests/features/resume_generation/`
- `test_service.py`: tables for `read_date`, `format_duration`, `total_years` and `split_bullets`; `build_resume_html` escaping, http-only links with "LINK n" labels, hidden empty sections, every section with full data ("6 years", the location, bullets as `<li>`), the education date chip, and no external URLs (every asset is `data:`); a Playwright error becomes `PdfRenderError`. **One real render** (user-approved; needs the Chromium shell, and fails rather than skips without it): 12 synthetic jobs make 2+ A4 pages, each with the header and footer images, "Jane Doe" on page 1, and only Inter fonts embedded.
- `test_router.py`: `render_pdf` is faked on the router module in every test (autouse), so no other test starts Chromium. Rows go through `db_session`. Covers the 200 headers and body, the percent-encoded filename (`/`, `Ü`, CR LF), 404 (unknown id, SQL NULL `structured_data`), 422, 503 (database down), 500 (stored data without `location`, with no CV data in the log) and 500 (render failure).
- No test calls OpenAI (the autouse `fake_openai` fixture).
