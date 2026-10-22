# Feature design: resume_generation

> **Source of truth:** the plan the user approved on 2026-10-22 ("branded MinistryHub PDF from the extracted CV data"), plus the user's follow-up answers the same day (recorded in `.claude/learning.md`). This document turns it into a buildable design and adds the technical checks made on 2026-10-22 (section "Renderer: verified details"). The `architecture-challenger` review is folded in. No user decision is changed. Two technical refinements are called out where they appear: static Inter files instead of `InterVariable.woff2`, and the extra `Cache-Control` / error-handling details.

> **For the developer: build on the current files.** Another session is changing `resume_intake` and the frontend at the same time (a progress overlay, link annotations in `read_pdf`, and a UI redesign with cards). Read `resume_intake/service.py`, `IntakeResult.tsx`, the tests and `app.css` as they are when you start, not as quoted here. Fit the changes below into them; don't overwrite their work.

## 1. Goal
The user wants the data AI extracts from a CV (`resume_structuring`, stored as JSONB in `resume_intakes.structured_data`) "poured into their predesigned MinistryHub resume and produced as a PDF", built quickly. The design reference is the sample MinistryHub PDF in the repository root (the only `*.pdf` there). The recruiter clicks **"Open branded PDF"** on the result screen, and a new tab shows the branded PDF, rendered on demand from the stored data.

**What the sample showed:**
- It was printed by **HeadlessChrome 128** (producer "Candidately"), so the design is an HTML page printed by Chromium.
- **Pages:** A4 (595 × 842 pt), 3 pages.
- **Header and footer:** two full-width raster images, repeated on every page, with the logo, the yellow bars and the MinistryHub contacts baked in. There is no live text in them.
  - **Header:** 1500 × 254 px, at the top (0–101 pt).
  - **Footer:** 1500 × 127 px, at the bottom (0–51 pt).
- **Font:** TT Interphases Regular, Medium and DemiBold. It's a paid font.
- **Icons:** vector paths.
- **Photo:** page 1 has a 128 × 128 candidate photo.

**What the user decided (2026-10-22):**
- **Renderer:** Playwright (Python) + Jinja2.
- **Font:** Inter, the free look-alike, bundled.
- **New data:**
  - **Current location:** a new AI field, `location`.
  - **Total years of experience:** computed.
  - **Duration per job:** computed.
  - **No photo.**
- **Delivery:** an "Open branded PDF" link on the result screen that opens a new tab. The PDF is rendered on demand by a GET endpoint returning `application/pdf`, and it is **not stored**.
- **Links:** under "Additional Information", labelled LINK 1, LINK 2…. Only http(s) links are clickable.
- **Projects:** a subsection after Education, styled like jobs.
- **Skills:** only the "Skills" heading, without "AREAS OF GIFTING".
- **Tests:**
  - **One real-render pytest is allowed.** Every other test fakes the renderer.
  - No UI or e2e browser tests.
  - No live AI in tests.

**Follow-up answers (2026-10-22):**
- **Link placement:** in the title band, before "New interview", styled as the existing outline button (`button button-outline`).
- **Errors in the new tab:** plain JSON, e.g. `{"detail": "Intake not found."}`.
- **Tester:** may make a few real GETs to the PDF endpoint. That runs headless Chromium on the server, with no UI and no AI.
- **The sample PDF:** delete it from the repository root once `header.png` and `footer.png` are cut out. The copy in Downloads stays.
- **"WORK EXPERIENCE IN YEARS":**
  - The span runs from the **earliest job start to the latest job end**, with "Present" and the other ongoing words meaning today.
  - When the months of both ends are known, count months inclusively and floor to whole years. Otherwise use the year difference.
  - Gaps between jobs still count.
  - The section is hidden when the value can't be computed or is 0.

## 2. Open questions
None. The user answered every question on 2026-10-22 (above).

## 3. Flow
1. The recruiter submits an interview (existing `resume_intake` flow). The result screen shows `IntakeResult`.
2. When `result.structured_data` is set, `IntakeResult` renders `<BrandedPdfLink intakeId={result.id} />`. That component is a plain `<a href="/api/resume_generation/{id}" target="_blank" rel="noopener noreferrer">Open branded PDF</a>`.
3. A click opens a new tab with `GET /api/resume_generation/{id}`. In dev, Vite proxies `/api` to `:8000`. No frontend JS is involved.
4. **`router.py` (`read_branded_resume`, sync `def`, so it runs in FastAPI's threadpool):**
   1. `get_resume_intake(db, intake_id)` from `resume_intake.service`.
      - A `SQLAlchemyError` → 503.
      - No row → 404.
      - `structured_data` is null → 404.
   2. `ResumeData.model_validate(intake.structured_data)`. A `pydantic.ValidationError` → 500 (stored data doesn't match the schema; see section 8).
   3. `build_resume_html(data, date.today())` → an HTML string with every asset inline.
   4. `render_pdf(html)` → PDF bytes. A `PdfRenderError` → 500.
   5. Return `Response(pdf, media_type="application/pdf")` with the `Content-Disposition` and `Cache-Control` headers (section 4).
5. **`render_pdf` (service).** One `try` wraps the whole `with sync_playwright()` block, so a failed launch also becomes `PdfRenderError`:
   1. `sync_playwright()` starts the Playwright driver.
   2. `chromium.launch()` (headless).
   3. `browser.new_page(java_script_enabled=False)`.
   4. `page.route("**/*", lambda route: route.abort())`.
   5. `page.set_content(html)`.
   6. `page.evaluate("document.fonts.ready.then(() => true)")`.
   7. `page.pdf(...)`.
   8. The browser closes when the `with` blocks exit.
6. The browser's PDF viewer shows the PDF. "Save" uses the `Content-Disposition` filename. Nothing is written to the database or to disk.

## 4. API
**`GET /api/resume_generation/{intake_id}`**

| | |
|---|---|
| Path parameter | `intake_id: int` (an id of `resume_intakes`) |
| Request body | none |
| Success | **200**, body = the PDF bytes |
| `Content-Type` | `application/pdf` |
| `Content-Disposition` | `inline; filename*=UTF-8''<quote("MinistryHub - {name}.pdf", safe="")>`. `name` is AI output, so it is always percent-encoded (`urllib.parse.quote` with `safe=""` also encodes `/`, `\`, quotes, CR and LF). There is no plain `filename=` fallback. |
| `Cache-Control` | `no-store`. The PDF holds personal data, so the browser keeps no copy in its HTTP cache. |

**Errors.** Every error body is `{"detail": "<message>"}` (`HTTPException`), except the FastAPI validation list.

| Code | When | `detail` |
|---|---|---|
| 404 | No intake with that id | "Intake not found." |
| 404 | The intake has no `structured_data` (rows from before AI extraction) | "This intake has no extracted details." |
| 422 | `intake_id` isn't an integer | FastAPI validation list |
| 500 | The stored `structured_data` doesn't validate as `ResumeData` | "The stored CV details can't be read." |
| 500 | Chromium failed: not installed, crashed or timed out (`PdfRenderError`) | "Could not create the PDF. Try again." |
| 503 | The database is unavailable (`SQLAlchemyError`) | "The database is unavailable. Try again later." (the same message and log pattern as intake) |

No `response_model`, and no `schemas.py`: the body is bytes.

## 5. Data
**No new table and no new column.** The PDF isn't stored.

**`resume_structuring` gains one field.** `ResumeData.location: str | None`, placed after `phone`.
- **Required with no default:** OpenAI strict mode needs that, and `test_every_field_is_required_with_no_default` enforces it.
- **`INSTRUCTIONS` gains one sentence:** "For location, give the candidate's current location as written in the CV, or null if it isn't stated."

**Data migration** (no schema change, so it is written by hand: `alembic revision -m "add location to structured_data"`, **not** `--autogenerate`). `down_revision = "a8d5bd5c3c32"`.
- **Why:** existing rows have no `location` key, so `ResumeData.model_validate` would fail on them. The dev DB has such rows (e.g. id 358, the live-check row).
- **Upgrade:**
  ```sql
  UPDATE resume_intakes
  SET structured_data = jsonb_build_object('location', NULL) || structured_data
  WHERE jsonb_typeof(structured_data) = 'object'
  ```
  - **Idempotent:** with the default on the **left** of `||`, a row that already has `location` keeps its value.
  - **No `?` operator and no JSON literal,** so there are no questions about bind-parameter parsing in `op.execute`.
  - **Why `jsonb_typeof`, not `IS NOT NULL`:** the column can hold a SQL NULL (rows from before AI extraction) or a JSON `null`. SQLAlchemy's JSON type stores Python `None` as JSON `null` unless told otherwise. `object || 'null'::jsonb` would turn the row into an **array**, and `'null'::jsonb - 'location'` raises "cannot delete from scalar". `jsonb_typeof(...) = 'object'` skips both kinds of null.
- **Downgrade:**
  ```sql
  UPDATE resume_intakes
  SET structured_data = structured_data - 'location'
  WHERE jsonb_typeof(structured_data) = 'object'
  ```
  This drops real `location` values too, which is accepted: the downgraded code has no such field.
- **Indexes:** none.

## 6. Slice layout

### Backend: `backend/app/features/resume_generation/`
| File | What it does |
|---|---|
| `router.py` (new) | `router = APIRouter(prefix="/resume_generation", tags=["resume_generation"])`, plus `@router.get("/{intake_id}")` on `def read_branded_resume(intake_id: int, db: Annotated[Session, Depends(get_db)]) -> Response`. HTTP only: it runs the steps in section 3, maps the errors in section 4, and builds the headers. It imports `render_pdf`, `build_resume_html` and `PdfRenderError` by name from `service` (the tests patch `router.render_pdf`). A one-line comment above the endpoint: `# Must stay a plain def: Playwright's sync API fails inside an async def (running event loop).` |
| `service.py` (new) | Everything else. See below. |
| `templates/resume.html.j2` (new) | The resume page: HTML plus one `<style>` block. See "Template" below. |
| `templates/assets/header.png`, `footer.png` (new) | The branding images, cut once from the sample (see "Assets"). |
| `templates/assets/Inter-Regular.woff2`, `Inter-Medium.woff2`, `Inter-SemiBold.woff2` (new) | From the official rsms/inter v4.1 release (`Inter-4.1.zip`, `web/` folder). Weights 400/500/600 stand in for TT Interphases Regular, Medium and DemiBold. |
| `templates/assets/OFL.txt` (new) | Inter's licence (`LICENSE.txt` from the same zip, SIL OFL 1.1). |
| `templates/assets/LUCIDE-LICENSE.txt` (new) | Lucide's ISC licence. The template copies Lucide icon paths, and ISC requires the notice to travel with copies. |
| `README.md` (update) | Purpose, the endpoint and its errors, "no table" plus the `location` note, the setup step, the tests. |

No `schemas.py` and no `models.py`.

**`service.py`:**
- **Module load:**
  - `TEMPLATES_DIR = Path(__file__).parent / "templates"`
  - `environment = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)`.
    - Use `autoescape=True`, **not** `select_autoescape()`: that one keys on the file extension, and `resume.html.j2` ends in `.j2`, so it would **not** escape.
  - Register three helpers:
    - `environment.filters["bullets"] = split_bullets`
    - `environment.tests["http_url"] = is_http_url`
    - `environment.globals["format_duration"] = format_duration`
  - The header and footer images and the three Inter files are read once and base64-encoded into data URIs:
    - `data:image/png;base64,…`
    - `data:font/woff2;base64,…`
  - **Constants:**
    - `HEADER_TEMPLATE` and `FOOTER_TEMPLATE` (exact text in "Renderer: verified details").
    - `PDF_MARGIN = {"top": "41.6mm", "bottom": "23.8mm", "left": "14mm", "right": "14mm"}`. The top is the header's 35.6 mm plus a 6 mm gap; the bottom is the footer's 17.8 mm plus 6 mm. These are starting values, tuned in the side-by-side check (section 9).
      - **Built (2026-10-22):** tuned to the sample's measured content box: `top` 44 mm (content starts 8.4 mm under the header, as in the sample), `left`/`right` 13.2 mm; `bottom` stays 23.8 mm.
    - `PDF_FAILED_MESSAGE = "Could not create the PDF. Try again."`
- **`class PdfRenderError(Exception)`:** "Chromium couldn't render the PDF (HTTP 500)."
- **`read_date(text: str | None, today: date) -> tuple[int, int | None] | None`:** (year, month or None). Checked in this order, all case-insensitive:
  1. `None` or blank → `None`.
  2. Ongoing words, `\b(present|current|currently|now|today|ongoing|till date|to date)\b` → `(today.year, today.month)`. A bare "date" never matches.
  3. Numeric month and year, with these two patterns (anchored, and `1[0-2]` first so "10" isn't read as "1"):
     - `MM/YYYY` or `MM-YYYY`: `\b(1[0-2]|0?[1-9])[/-]((?:19|20)\d{2})\b`
     - `YYYY-MM`: `\b((?:19|20)\d{2})-(1[0-2]|0?[1-9])\b`
  4. Otherwise the year is the first `\b(19|20)\d{2}\b`. With no year → `None`.
  5. The month comes from a month name, matched on the first three letters at a word start (`jan` … `dec`, so "Sept" and "September" both work). Otherwise the month is `None`.
- **`format_duration(start: str | None, end: str | None, today: date) -> str | None`:**
  - **Either side unreadable** (including null) → `None`.
  - **Both months known:**
    - Count months inclusively: `(end_year - start_year) * 12 + end_month - start_month + 1`. Jan → Oct 2026 = 10, as in the sample.
    - Below 1 → `None`.
    - Under 12 → `"N mos"` / `"1 mo"`.
    - Otherwise whole years (`months // 12`) → `"N yrs"` / `"1 yr"`.
  - **A month missing:**
    - The year difference. 2021–2023 = 2, as in the sample.
    - Below 1 → `None`.
    - Otherwise `"N yrs"` / `"1 yr"`.
- **`total_years(spans: list[tuple[str | None, str | None]], today: date) -> str | None`:** the user's rule. It takes `(start_date, end_date)` pairs, so it needs no `Experience` import (`resume_structuring.service` re-exports only `ResumeData`).
  1. **Earliest start:** the smallest readable `start_date`, by `(year, month or 0)`.
  2. **Latest end:** the largest readable `end_date`, by `(year, month or 13)`. "Present" and the other ongoing words are today.
  3. **Span:**
     - Both chosen ends have a month → the inclusive month count (as in `format_duration`) floored to whole years (`// 12`).
     - Otherwise → the year difference.
  4. **Gaps between jobs count;** overlaps never count twice, because only the two ends matter.
  5. **No readable start or end, or a span below 1** → `None`, which hides the Professional Summary.
  6. Otherwise `"N years"` / `"1 year"`.
  - Share the month/year arithmetic with `format_duration` rather than writing it twice.
- **`split_bullets(text: str | None) -> list[str]`:** split on `\r\n`, `\r` or `\n` only; remove one leading marker **only when whitespace (or nothing) follows it**, so "-30% latency" and "*Important* thing" stay as written (user decision, 2026-10-22); strip, and drop blank lines. `None` → `[]`. (Built QA fixes, 2026-10-22: `MM.YYYY` dates; month names as whole words only; blank values, entries, pills and links are dropped before rendering (`drop_blanks`), and links are stripped before the http(s) check; ids above 2,147,483,647 are a 422.)
  - **Markers, an explicit set:** `•`, `-`, `*`, `·`, `–`, `▪`, `●`, `◦`, `➢` and `` (Word's Symbol-font bullet, which text extraction leaves in the private-use area).
- **`is_http_url(url: str | None) -> bool`:** the URL starts with `http://` or `https://`, case-insensitive. It mirrors `linkOrText` in `StructuredResume.tsx`.
- **`build_resume_html(data: ResumeData, today: date) -> str`:** renders `resume.html.j2` with `data`, `today`, `total_years=total_years([(job.start_date, job.end_date) for job in data.experience], today)` and the asset data URIs.
- **`render_pdf(html: str) -> bytes`:** runs the steps in section 3, item 5. The `page.pdf` call is `page.pdf(format="A4", print_background=True, margin=PDF_MARGIN, display_header_footer=True, header_template=HEADER_TEMPLATE, footer_template=FOOTER_TEMPLATE)`.
  - **Built (2026-10-22):** `width="210mm", height="297mm"` (`PAGE_SIZE`) instead of `format="A4"`. Playwright's A4 is 8.27 × 11.7 in, which Chromium prints as 595.92 × 842.88 pt (about 1 pt taller than A4); 210 × 297 mm gives 595.92 × 841.92 pt, exactly the sample's size.
  - **The `try` wraps the whole `with sync_playwright() as playwright:` block,** including `chromium.launch()`. Any `playwright.sync_api.Error` (its `TimeoutError` is a subclass) → `logger.exception("Could not render the PDF")`, then `raise PdfRenderError(PDF_FAILED_MESSAGE) from None`. A failed launch (Chromium missing) is therefore a `PdfRenderError` too.
  - Playwright's messages hold no CV text: the call log names the step, not the HTML. A missing browser logs Playwright's "run playwright install" hint, which is what the developer needs.
  - **A non-Playwright error** (e.g. an `OSError` if the bundled driver can't start) isn't caught. It is a plain FastAPI 500 "Internal Server Error", and its traceback holds no CV data.
  - Comment: `# shortcut: one browser launch per PDF (~1–2 s); keep a shared browser if volume grows`.

**Template (`resume.html.j2`)**, matching the sample:
- **Document:** `<!doctype html>`, `<html lang="en">`, `<meta charset="utf-8">`, `<title>MinistryHub - {{ data.name }}</title>`. Chrome copies the title into the PDF's Title, so the viewer tab shows it.
- **One `<style>` block:**
  - `@font-face` × 3 (family `Inter`; weights 400, 500, 600; `src: url(<data URI>) format("woff2")`).
  - `html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }`.
  - Colour custom properties (yellow, navy, green) sampled from `header.png`.
  - Entries may break across pages; each entry's title row stays with its first bullet (`break-inside: avoid` on the title row and on each bullet, `break-after: avoid` on the title row), and `break-after: avoid` on headings (user decision, 2026-10-22). `overflow-wrap: anywhere` on `body`, so a long unbroken word wraps instead of making Chrome shrink every page.
  - **No `@page` margin rule.** A CSS `@page` margin overrides `PDF_MARGIN`, and the header and footer boxes would no longer match the image heights. Margins live only in `PDF_MARGIN`.
- **AI values go only into text nodes and quoted `href="…"` attributes,** never into `<style>`, `style=""` or any unquoted attribute. Autoescape then covers every value.
- **Sections, in order.** Every section and field is hidden when empty or null. The heading texts below are exact (the tests check them); upper case, where the sample shows it, comes from CSS `text-transform`.
  1. **Name** (`data.name`): in yellow.
  2. **Contact:** a 2-column grid of email, phone and location, each with an icon tile (Lucide `mail`, `phone`, `map-pin`). Email is plain text.
  3. **"Professional Summary":** the label "WORK EXPERIENCE IN YEARS" with `total_years`. It is hidden when `total_years` is `None`.
  4. **"Experience & Education":** hidden when all three subsections are empty.
     - **"Employment History":** per job:
       - a navy icon tile (`briefcase`)
       - the title
       - "company | location"
       - `format_duration(job.start_date, job.end_date, today)`, plus start and end chips with the dates as written
       - `job.description | bullets` as a `<ul>`
     - **"Education":** per entry:
       - the institution
       - degree and field joined with ", "
       - a date chip: `end_date` as written, else `start_date` as written (like the certificate chip); hidden when both are null.
     - **"Projects":** per project:
       - the name
       - the link (`<a>` only when it is an `http_url`)
       - `description | bullets`
       - technology pills
  5. **"Licenses & Certifications":** name, issuer, and a date chip as written (Lucide `award`).
  6. **"Skills":** pills. There is no "AREAS OF GIFTING" text anywhere.
  7. **"Additional Information":** two columns of "LINK {n}" (`loop.index`) with the URL. Only `http_url` links are `<a href>`; the others are plain text.
- **Icons:** inline `<svg>` with paths copied from Lucide. There's no package and no network.

**Assets (one-time, by the developer):**
- **`header.png` / `footer.png`:**
  - **Source:** the two full-width images on page 2 of the sample, the page without the photo. Cut them with `pypdfium2` in a throwaway script in the scratchpad; the script isn't committed.
  - **Never** take the candidate photo.
  - **Saving:** `PdfImage.extract()` writes a JPEG directly. Any other format needs Pillow, which **isn't installed**. Then either write the PNG from `get_bitmap(render=True)` with the standard library (`zlib` + `struct`), or ask the user before installing Pillow anywhere.
  - **If the sample stores them as JPEG:** keep `.jpg` and `image/jpeg`. Don't re-encode.
  - **Check:** compare both visually against the sample before committing.
  - They are 1500 px wide (~180 dpi). If the user has the original artwork, it can be swapped in later.
  - **Then delete the sample PDF from the repository root** (user-approved, 2026-10-22). The copy in the user's Downloads folder stays.
    - The project folder **isn't a git repository**, so there's no history to clean.
    - The root `.gitignore` has **no `*.pdf` rule**, so nothing would keep the file out of a future commit. Deleting it is the only protection.
- **Inter files:** from the official release zip only.

### Other backend changes
| File | Change |
|---|---|
| `app/main.py` | `app.include_router(resume_generation_router, prefix="/api")` |
| `app/features/resume_structuring/schemas.py` | `ResumeData.location: str \| None` after `phone` |
| `app/features/resume_structuring/service.py` | The `INSTRUCTIONS` sentence (section 5) |
| `app/features/resume_structuring/README.md` | `location` in the field table |
| `app/features/resume_intake/service.py` | `get_resume_intake(db: Session, intake_id: int) -> ResumeIntake \| None`, which is `db.get(ResumeIntake, intake_id)`, with a docstring |
| `app/features/resume_intake/README.md` | Lists `get_resume_intake` as the slice's read API for other features |
| `alembic/versions/<rev>_add_location_to_structured_data.py` | Section 5 |
| `requirements.txt` | `playwright==1.64.0` and `jinja2==3.1.6` (already installed through `fastapi[standard…]`, now imported directly). `greenlet` and `pyee` stay unpinned transitive dependencies, as elsewhere in the file. |

### Frontend
| File | Change |
|---|---|
| `src/features/resume_generation/BrandedPdfLink.tsx` (new) | `export function BrandedPdfLink({ intakeId }: { intakeId: number })` renders `<a className="button button-outline" href={`/api/resume_generation/${intakeId}`} target="_blank" rel="noopener noreferrer">Open branded PDF</a>`, with a JSDoc. The class is the existing outline button (user's choice). If the parallel UI redesign has renamed it, use the current outline-button class. |
| `src/features/resume_generation/README.md` | Purpose, what it shows, tests |
| `src/features/resume_intake/IntakeResult.tsx` | Renders `<BrandedPdfLink intakeId={result.id} />` in the title band, **before** the "New interview" button, when `result.structured_data` is set (user's choice) |
| `src/features/resume_intake/IntakeResult.test.tsx` | The fixture gains `location: null`. New test: the link has `href="/api/resume_generation/1"`, `target="_blank"` and a `rel` containing `noopener`, and there's no link when `structured_data` is null. |
| `src/features/resume_intake/README.md` | Mentions the link |
| `src/features/resume_structuring/StructuredResume.tsx` | `ResumeData.location: string \| null`. The candidate card shows it as a third pill (`pill pill-violet`, with an sr-only "Location: " prefix like the email and phone pills) when it's set. |
| `src/features/resume_structuring/StructuredResume.test.tsx` | `FULL_DATA` gains a location, and one assertion checks it's shown. `EMPTY_DATA` gains `location: null`. |
| `src/features/resume_structuring/README.md` | `location` |
| `e2e/resume_intake.spec.ts` | `location: null` in `STORED_INTAKE`, so the canned stream mirrors the backend. Not run (UI tests are paused). |

## 7. Cross-feature contracts
- **`resume_intake.service.get_resume_intake(db, intake_id) -> ResumeIntake | None`:** new, and the only way `resume_generation` reads a row (rule 7). It only reads `.structured_data` from the returned model.
- **`resume_structuring.service.ResumeData`:** existing (it's imported into `service.py` for this purpose). `resume_generation` imports only `ResumeData` from there; the nested models are reached through its fields.
- **Frontend:** `resume_intake/IntakeResult.tsx` imports the exported component `BrandedPdfLink` from `resume_generation`. That's the same pattern as its use of `StructuredResume`.
- **Nothing in other slices imports `resume_generation`'s backend.**

## Renderer: verified details (2026-10-22)
1. **Package versions** (PyPI, GitHub):
   - **`playwright`:** 1.64.0 (2026-10-13). It ships `py3-none-win_amd64` wheels (the bundled Node driver, independent of the Python version). `requires-python >=3.10`, with a **Python 3.14 classifier** in its `pyproject.toml`. It depends on `greenlet>=3.1.1,<4` and `pyee>=13,<15`.
   - **`greenlet`:** 3.5.6 (2026-09-14) has a **`cp314-cp314-win_amd64` wheel**, so no compiler is needed.
   - **Browser:** the Python 1.64.0 release targets **Chromium 156.0.8078.4**, which is revision **1248**. That's what the frontend's Node `playwright-core` 1.64.1 pins too.
   - **This machine already has** `chromium-1248`, `chromium_headless_shell-1248` and `ffmpeg-1013` in `%LOCALAPPDATA%\ms-playwright`.
     - Headless `launch()` uses `chromium_headless_shell-1248`.
     - So the setup step is **`python -m playwright install --only-shell chromium`**. It installs only the headless shell, and downloads nothing here.
   - **Shared cache:** Node and Python Playwright share that cache. Upgrading one without the other downloads a second Chromium; that's harmless and only costs disk.
2. **Header and footer templates** (Chromium's `print_header_footer_template_page.html`, the Playwright docs and `crPdf.ts`):
   - **Default padding:** Chromium wraps the templates in `#header { padding-top: 15pt }` and `#footer { padding-bottom: 15pt }`. Both are flex boxes; the header aligns to the top and the footer to the bottom.
     - The template page has the **full paper size**, and the header box height is the top margin (the footer's is the bottom margin). Left and right page margins don't inset it.
     - So an image can reach the page edge only once that padding is overridden.
   - **Template rules** (Playwright docs): page styles aren't visible inside templates, and script tags in them don't run.
   - **Data URIs:** with network blocked, only `data:` images can load.
   - **Scale:** the template's coordinate scale differs from the page's (header text is known to come out smaller), so size the image in **percent**, not mm or px.
   - **Exact templates** (load-bearing; this is the rendered text, so double the CSS braces if it's built with an f-string):
     - `HEADER_TEMPLATE = '<style>#header{padding:0 !important}#header img{display:block;width:100%}</style><img src="{header data URI}" alt="">'`
     - `FOOTER_TEMPLATE = '<style>#footer{padding:0 !important}#footer img{display:block;width:100%}</style><img src="{footer data URI}" alt="">'`
   - **What lands where:** the header image's natural height at full A4 width is 210 × 254 / 1500 = 35.6 mm, at the top of a 41.6 mm margin. The footer is 17.8 mm, at the bottom of a 23.8 mm margin.
   - **Colours:** images print without `print_background`, but `print_background=True` is still needed for the page's own pills, chips and tiles.
3. **Fonts:**
   - **Playwright's `page.pdf` doesn't wait for web fonts.** `crPdf.ts` sends `Page.printToPDF` straight away; Puppeteer's `waitForFonts` has no Playwright equivalent. Chrome loads `@font-face` lazily, so `render_pdf` awaits `document.fonts.ready` first. `page.evaluate` still works with JavaScript off; Playwright tests "should not hang on promises after disabling javascript".
   - **Static fonts, not `InterVariable.woff2`** (a change from the plan's file name; the font is still Inter). Skia, Chrome's PDF backend, forces any font with `kVariable_FontFlag` to a **Type 3 fallback** (`SkPDFFont.cpp`): glyphs become drawn shapes, with no embedded font program and no hinting. Static weights embed as real TrueType subsets.
4. **Sync API in the threadpool:**
   - **Why it works:** `sync_playwright().__enter__` uses `asyncio.get_running_loop()` and, on `RuntimeError`, `asyncio.new_event_loop()`. It raises only when a loop is already running in the calling thread. A FastAPI sync endpoint runs in an anyio worker thread with no loop, so it works.
   - **The endpoint must stay a plain `def`.** In an `async def` endpoint it would raise "using Playwright Sync API inside the asyncio loop".
   - **On Windows,** the new loop is a `ProactorEventLoop` (the default), which supports the subprocess Playwright starts.
     - Checked: uvicorn **0.54.0** picks its own loop through a `loop_factory` (a `SelectorEventLoop` under `--reload`) and **never sets a global event-loop policy**, so the worker thread's loop is unaffected.
     - Anything that sets `WindowsSelectorEventLoopPolicy` process-wide would break Chromium launch with `NotImplementedError`.
   - **Thread affinity:** Playwright objects must stay in the thread that made them. One browser per call satisfies that, and it's why a shared browser isn't a one-line change.
   - **pytest:** the real-render test calls `render_pdf` on pytest's main thread, which has no running loop. It must stay a sync test.
5. **Blocking everything but inline content** (Playwright tests in v1.64.0):
   - **Data URLs:** a `**/*` route never sees `data:` URLs ("should navigate to dataURL and not fire dataURL requests", "should be able to fetch dataURL and not fire dataURL requests"). Aborting every routed request therefore still lets the inline font and image data load.
   - **`set_content`:** it works with JavaScript disabled ("setContent should work after disabling javascript").

## 8. Failure modes and security
- **Invalid input:**
  - A non-integer id → 422 (FastAPI).
  - An unknown id → 404.
  - A row with no `structured_data` (SQL NULL or JSON `null`; both load as Python `None`) → 404. The UI never offers the link for those rows.
  - Errors show in the new tab as plain JSON (user's choice).
- **Stored data that doesn't match `ResumeData`** (e.g. the migration hasn't been run): → 500 "The stored CV details can't be read."
  - Logged as `logger.error("Stored structured_data for intake %s doesn't match ResumeData", intake_id)` and raised `from None`.
  - **A `ValidationError`'s message contains the input values (CV data), so it is never logged or chained** (the same lesson as in `resume_structuring`).
- **AI provider errors and timeouts:** this endpoint calls no AI.
  - The only AI change is one more field in the existing extraction, which keeps its 422/502 handling, `store=False` and the 120 s timeout.
  - `location` is "as written, or null", so nothing is invented.
- **Renderer failures:**
  - Chromium missing (setup step skipped), a crash, or a `set_content` timeout (30 s default) → `PdfRenderError` → 500, and the cause is logged. The `try` covers the launch too.
  - A non-Playwright error (e.g. an `OSError` starting the driver) → a plain FastAPI 500, with no CV data in the traceback.
  - `page.pdf` has no timeout option, so a hung Chromium would hold one worker thread (accepted; see Known limits).
- **Large or malformed files:** there's no upload.
  - The input is the stored JSON, bounded by the 50,000-character CV cap upstream.
  - The PDF's page count isn't capped. A long TXT CV makes a long PDF.
  - JSONB can't hold NUL, and Jinja escapes everything else.
- **Untrusted AI text in the page:**
  - Jinja `autoescape=True`, and values only in text nodes and quoted `href` attributes.
  - Only http(s) URLs become links, so a `javascript:` URL can't become a PDF link action.
  - JavaScript is off, so an injected `<script>` couldn't run even if escaping failed.
  - Every routed request is aborted, so nothing can be fetched over the network (no tracking pixels, no local-network calls).
  - `file:` loads are blocked by Chromium's origin rules (the page's origin can't read local files), not by `page.route`.
- **Personal data:**
  - **Not stored:** the PDF is rendered in memory and returned. We write no temporary files; Chromium's throwaway profile directory is created and deleted by Playwright.
  - **`Cache-Control: no-store`:** the recruiter's browser doesn't cache the PDF.
  - **Logs:** only the intake id, exception types and Playwright's messages, never CV text. The access log shows `GET /api/resume_generation/<id>`, with no personal data.
  - **The filename contains the candidate's name** (the user's choice). It shows up in the browser's downloads list if saved.
  - **No authentication, and ids are sequential.** Anyone who can reach the API can fetch every candidate's PDF. That's acceptable only while the app is localhost-only (existing "Open" row in `docs/architecture.md`); **auth must come before any deployment.**
  - **The sample PDF is a real person's CV** in the repository root. Only the branding images are cut from it, and it's never used in tests. It is deleted from the repository root once they're cut (user-approved). The folder isn't a git repository, and `.gitignore` doesn't exclude `*.pdf`.

## 9. Testing
**Backend (pytest).** There is no live AI (the autouse `fake_openai` fixture is unchanged). Exactly one test renders for real.

**`tests/features/resume_generation/test_service.py`**, with `TODAY = date(2026, 10, 22)`:
- **`read_date`, as a parametrized table:**

  | Input | Result |
  |---|---|
  | `"2021"` | `(2021, None)` |
  | `"Jan 2026"` | `(2026, 1)` |
  | `"Sept 2019"`, `"September 2019"` | `(2019, 9)` |
  | `"03/2020"`, `"3-2020"`, `"2020-03"` | `(2020, 3)` |
  | `"2020-10"` | `(2020, 10)` |
  | `"2020-12-01"` | `(2020, 12)` |
  | `"2019-2021"` | `(2019, None)` |
  | `"2019-20"` | `(2019, None)` |
  | `"15/03/2020"` | `(2020, 3)` |
  | `"03/15/2020"` | `(2020, None)` |
  | `"Present"`, `"current"` | `(2026, 10)` |
  | `"Currently"` | `(2026, 10)` |
  | `"Till date"` | `(2026, 10)` |
  | `"n/a"`, `""`, `None` | `None` |
- **`format_duration`, as a parametrized table:**

  | Start → end | Result |
  |---|---|
  | Jan 2026 → Present | `"10 mos"` |
  | Oct 2026 → Oct 2026 | `"1 mo"` |
  | Jan 2020 → Nov 2020 | `"11 mos"` |
  | Jan 2020 → Dec 2020 | `"1 yr"` |
  | Mar 2015 → Present | `"11 yrs"` |
  | 2021 → 2023 | `"2 yrs"` |
  | 2022 → 2023 | `"1 yr"` |
  | 2021 → Jan 2023 | `"2 yrs"` (a month missing) |
  | 2023 → 2023 | `None` |
  | 2023 → 2021 | `None` |
  | May 2022 → Jan 2022 | `None` |
  | n/a → 2020 | `None` |
  | None → 2020 | `None` |
  | 2020 → None | `None` |
- **`total_years`, as a parametrized table:**

  | Spans (start, end) | Result | Why |
  |---|---|---|
  | `("Dec 2024", "Present")` | `"1 year"` | 23 months, floored |
  | `("2010", "2020")`, `("2015", "2018")` | `"10 years"` | A job that ended in 2020 stops the count at 2020, not today |
  | `("Jan 2003", "Dec 2010")`, `("2012", "Present")` | `"23 years"` | 286 months; the 2011 gap counts |
  | `("Mar 2026", "Present")` | `None` | 8 months floors to 0 |
  | `("2020", None)` | `None` | No readable end |
  | `("n/a", "2020")` | `None` | No readable start |
  | (no jobs) | `None` | |
- **`split_bullets`:**
  - `"• Led team\n- Built API\n\n * Wrote docs\n· Shipped"` → the four lines without markers.
  - `"– Planned\n▪ Built\n● Tested\n◦ Fixed\n➢ Shipped\n Reported"` → the six words without markers.
  - `None` → `[]`. One paragraph stays one item.
- **`build_resume_html`:**
  - **Escaping:** a name `<script>alert(1)</script>` appears as `&lt;script&gt;`, and the output has no `<script`.
  - **Links:** with links `https://example.com/jane`, `javascript:alert(1)` and `www.janedoe.dev`, only the first is an `href`. The other two appear as text, and "LINK 1", "LINK 2" and "LINK 3" are present.
  - **Empty data:** a `ResumeData` with only name and email (everything else null or `[]`) contains none of the headings "Professional Summary", "Experience & Education", "Employment History", "Education", "Projects", "Licenses & Certifications", "Skills" or "Additional Information".
  - **Full data:** `FAKE_RESUME_DATA` contains them, plus "WORK EXPERIENCE IN YEARS" with "6 years" (Jan 2020 → Present), the location and the job's bullets as `<li>`.
  - **Education chip:** it shows `end_date` as written ("2019"). With `end_date` null and `start_date` "Sept 2015", it shows "Sept 2015".
  - **No "AREAS OF GIFTING"** in either.
  - **No external URLs:** the output has no `http` URL other than the AI's own links. Every `src=` starts with `data:`.
- **`render_pdf` error wrapping:** with `sync_playwright` monkeypatched to raise `playwright.sync_api.Error("boom")`, it raises `PdfRenderError`.
- **The one real render** (user-approved, needs Chromium; it fails rather than skips, because the endpoint needs Chromium too):
  - **Input:** `FAKE_RESUME_DATA` with 12 synthetic jobs of 5 bullets each, so it spans at least 2 pages.
  - **Checks:**
    - The bytes start with `%PDF-`, and `pypdfium2.PdfDocument` opens them.
    - Page 1 is A4, within 1 pt of (595.3, 841.9).
    - Page 1's text contains "Jane Doe".
    - **Every page** has at least 2 image objects (`get_objects(filter=[FPDF_PAGEOBJ_IMAGE])`), which shows the header and footer repeat.
  - The document is closed at the end.

**`tests/features/resume_generation/test_router.py`:**
- **Setup:**
  - Every test whose request can reach the database takes `db_session` (the leaked-rows lesson).
  - `render_pdf` is monkeypatched **on the router module** to return `b"%PDF-fake"` and record the HTML it got.
  - Rows are created with `save_resume_intake(db_session, …, FAKE_RESUME_DATA, answers)`.
  - The row without data is built directly as `ResumeIntake(..., structured_data=sqlalchemy.null())`, so it holds a SQL NULL like the rows from before AI extraction. A plain `None` would store a JSON `null`.
- **Cases:**
  - **200:** `content-type` is `application/pdf`, the body is the fake bytes, and `content-disposition` is `inline; filename*=UTF-8''MinistryHub%20-%20Jane%20Doe.pdf`. `cache-control` is `no-store`, and the recorded HTML contains "Jane Doe".
  - **A name like `"Ana/Ünal\r\nX"`:** the header holds `%2F`, `%C3%9C` and `%0D%0A`, and no raw CR or LF.
  - **Unknown id** (`-1`) → 404 "Intake not found."
  - **`structured_data` null** → 404 "This intake has no extracted details."
  - **DB down:** an override session whose `get` raises `OperationalError` (the health-test pattern) → 503.
  - **Stored data missing `location`** (written directly) → 500 "The stored CV details can't be read." `caplog` doesn't contain "Jane Doe".
  - **The fake render raises `PdfRenderError`** → 500 "Could not create the PDF. Try again."

**Other backend tests:**
- **`tests/conftest.py`:** `FAKE_RESUME_DATA` gains `location="Springfield, USA"`. It's synthetic.
- **`tests/features/resume_structuring/test_schemas.py`:**
  - The `ResumeData(...)` call gains `location=None`.
  - The null-name JSON literal gains `"location": null`, so it still fails only on the name.
  - The "every field is required" test covers `location` automatically.
- **`tests/features/resume_intake/test_service.py`:** `get_resume_intake` returns the saved row, and `None` for `-1`. Both use `db_session` and need Postgres.
- **Migration (manual, by the developer):**
  1. `alembic upgrade head`, then check that no row with data lacks `location`: `SELECT count(*) FROM resume_intakes WHERE jsonb_typeof(structured_data) = 'object' AND NOT (structured_data ? 'location')` returns 0. Also check `SELECT count(*) FROM resume_intakes WHERE jsonb_typeof(structured_data) = 'array'` returns 0.
  2. `alembic downgrade -1`, then `alembic upgrade head` again.

**Frontend (Vitest only, no browser tests):** the `IntakeResult.test.tsx` and `StructuredResume.test.tsx` changes in section 6.

**Tester:** the API only (user decision). No Playwright UI or e2e tests, and no live AI. The tester **may make a few real GETs** to the PDF endpoint (user-approved). Those run headless Chromium on the server; they open no browser window and only read rows.

**End to end:** no automated e2e (UI tests paused). Manual check by the main session or the user:
1. Restart the dev backend.
2. Open `http://127.0.0.1:8000/api/resume_generation/358`.
3. Compare it side by side with the sample: the header and footer on every page, the sections, chips and pills, and the font. Tune `PDF_MARGIN` and the colours here.
4. Optional, and only with the user's permission (it uses 1 of the 4 remaining live AI requests): upload a synthetic CV with a location line, check that `location` is in the result, and open the PDF from the result screen's link.

## 10. Decisions
| Decision | Simpler alternative considered | Why this |
|---|---|---|
| Headless Chromium via Playwright (Python) + Jinja2 | WeasyPrint (needs GTK/Pango on Windows; its CSS differs from Chrome's), ReportLab (no HTML/CSS) | **User choice.** The sample was printed by HeadlessChrome, so the same engine reproduces it. No system install: `pip` plus `playwright install`. |
| Header and footer through `page.pdf`'s `header_template` / `footer_template`, with Chromium's padding overridden | `position: fixed` elements in the page, which Chrome repeats on every printed page | Fixed elements sit inside the page margins, so they can't reach the paper edge and still leave room for content on pages 2+. The templates live in the margins, at full paper size. |
| Static Inter 400/500/600 woff2 | One `InterVariable.woff2`, as the plan named | Skia embeds variable fonts as Type 3 (no font program, no hinting). Still Inter, so the user's choice stands. |
| Assets inlined as `data:` URIs; every routed request aborted; JavaScript off | `file://` assets, or trusting autoescape alone | The page holds AI output. With nothing fetchable and nothing runnable, an escaping slip can't call out. `data:` URLs bypass routing (verified). |
| On demand, not stored; `Cache-Control: no-store` | Store the PDF (a `bytea` column, plus retention questions) | **User choice** (not stored). `no-store` keeps the PII-bearing PDF out of the browser cache; it's one header. |
| One browser launch per request (~1–2 s) | A shared browser | **User-approved shortcut.** Sync Playwright objects are bound to their thread, and endpoints run on many threadpool threads, so sharing needs a dedicated thread or the async API. |
| Await `document.fonts.ready` before `page.pdf` | Nothing | Playwright doesn't wait for fonts (verified in `crPdf.ts`). Without it, text may print in a fallback font or invisible. One line. |
| Helpers registered on the Jinja environment (a `bullets` filter, an `http_url` test, a `format_duration` global) | Precomputed view-model dicts per job | Less Python glue. The template reads the `ResumeData` models directly. Dates in chips are shown as written, so the template needs no `read_date`. |
| Total years = earliest start → latest end, month-aware, floored | `today.year - earliest start year` (the plan's first example) | **User's rule** (2026-10-22). The first example overstated the figure for someone not working now. |
| Data migration that adds `"location": null` to old rows | `model_validate({"location": None} \| data)` in the router | The stored data matches the schema once, so no reader has to remember the patch. |
| `ValidationError` → 500 with an id-only log; `PdfRenderError` → 500 | Let them escape as an unhandled 500 | An unhandled `ValidationError` would write CV text to the log through its traceback. The render error gets a clear message and a logged cause. |
| A plain `<a target="_blank">` link | `fetch` the PDF and open a blob URL | No JS, no object-URL lifecycle, and the browser's PDF viewer handles saving. Errors show as plain JSON, which the user accepted. |

**Rows to add to the Decisions table in `docs/architecture.md`** (the developer adds them in the build change):

| Date | Decision | Why |
|---|---|---|
| 2026-10-22 | Branded PDF rendered by headless Chromium through Playwright for Python (`playwright==1.64.0`, Chromium 1248), from a Jinja2 template with `autoescape=True` | User choice. The sample was Chrome-printed; no system install. Setup: `python -m playwright install --only-shell chromium`. |
| 2026-10-22 | Font Inter (SIL OFL), static 400/500/600 woff2 bundled as data URIs, instead of the paid TT Interphases | User choice. Static, because Chrome's PDF backend turns variable fonts into Type 3. |
| 2026-10-22 | Header and footer are the sample's raster images, drawn through `page.pdf`'s header and footer templates | Repeats them on every page, edge to edge |
| 2026-10-22 | PDF rendered on demand by `GET /api/resume_generation/{intake_id}`, not stored; `Cache-Control: no-store` | User choice; personal data |
| 2026-10-22 | Rendering runs with JavaScript off and every network request aborted | The page holds AI output |
| 2026-10-22 | One Chromium launch per PDF | Simplicity; revisit if volume grows |

Also in `docs/architecture.md`:
- "What the system does", item 3, becomes "renders the branded MinistryHub PDF from the structured data".
- "`resume_generation` is still empty" becomes "is designed and built".

## Docs updated with this change
- **This file.**
- **`docs/features/resume_structuring.md`:** the `location` field.
- **`docs/architecture.md`:** as above.
- **READMEs:** all three slices, backend and frontend.
- **`README.md` and `CLAUDE.md`:** the backend setup step `python -m playwright install --only-shell chromium`, run once after `pip install`. It needs no admin rights and downloads only the headless shell into `%LOCALAPPDATA%\ms-playwright`.
- **`.claude/learning.md`:**
  - the user's decisions
  - the TT Interphases licence finding
  - the Type 3 / variable-font finding
  - the header-template padding fix
  - "`page.pdf` doesn't wait for fonts"

## Known limits (accepted)
- **Speed:** each open renders afresh: a Playwright driver start plus a Chromium launch, ~1–2 s.
- **Memory:** many simultaneous opens each start a Chromium (~100–200 MB each). That's fine for one recruiter on localhost.
- **Hangs:** a hung Chromium during `page.pdf` (it has no timeout) holds one threadpool thread.
- **DB connection:** the request's DB session stays open while the PDF renders.
- **Branding images:** they are rasters at ~180 dpi, from the sample.
- **Font:** Inter is a look-alike, not the brand font.
- **Durations:** dates the parser can't read hide the duration.
- **Total years:** the span from the earliest start to the latest end. Gaps count (user's rule). If no job has a readable start or end, the section is hidden.
- **Server size:** the headless shell adds a few hundred MB to any future server image. Docker needs `playwright install --with-deps --only-shell chromium`.
- **Access:** no auth, sequential ids (section 8).

## Not doing
- The candidate photo
- Storing PDFs
- Loxo upload
- A shared or pooled browser
- The TT Interphases font
- An "AREAS OF GIFTING" label
- DOCX output
- Tagged (accessible) PDF output
- A client-side PDF preview
- A page cap on the output
