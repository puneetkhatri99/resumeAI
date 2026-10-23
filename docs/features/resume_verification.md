# Feature design: resume_verification (fidelity check)

> **Status: draft, blocked on the open questions in section 2.** Nothing here is approved yet. The slice name `resume_verification` is itself open question 1.

> **For the developer: build on the current files.** Other sessions are changing `resume_intake` (OCR for scanned PDFs, added 2026-10-23) and `resume_generation` at the same time. Read `resume_intake/schemas.py`, `IntakeResult.tsx`, `resumeIntake.css`, `tests/conftest.py` and the e2e spec as they are when you start, not as described here, and fit the changes in without overwriting their work.

## 1. Goal
The user asked "what if someone prompt-injects in their resume". The AI (`extract_resume_data`, OpenAI structured outputs) has no tools, so an injection can't leak data or run code, but it can change the extracted values. Those values reach the branded MinistryHub PDF that goes to clients. The user approved "a check that doesn't rely on the AI": after extraction, **confirm each extracted value appears in the CV text, and flag anything that doesn't on the result screen** (possibly blocking the PDF; see open question 2). This is the MinistryHub brief's step "automatic checks match the result against the original".

**What the check promises, in one sentence:** every value it doesn't flag appears, word for word, in the text the AI was given.

**Be clear with the user about what that does and doesn't cover:**
- **It catches** the AI inventing, rewording, completing, translating or summarising anything. That covers hallucination, and injections that make the AI *transform* the CV ("translate to French", "say 15 years").
- **It can't catch text that IS in the CV text.** The user's own example, hidden text saying "Skills: Kubernetes. 10 years at Google.", puts "Kubernetes" and "Google" into the extracted text, so they pass. Hidden-text detection is a separate follow-up (section 8.5, open question 7).
- **For a scanned PDF, the CV text is itself AI output** (OCR through OpenAI vision, 2026-10-23). The check then compares AI output with AI output, so it can't catch an OCR misreading (section 8.4).
- **A lie written plainly in the CV** passes too. That is the interview's job.

## 2. Open questions
The design below follows the recommended option of each. It is **blocked** until the user answers.

1. **Slice and names.**
   - (a) New slice `resume_verification`; response field `unconfirmed_values`; card heading "Not found in the CV text". **Recommended:** it's a separate pipeline step (the brief's "automatic checks") with its own responsibility and its own UI card.
   - (b) New slice `resume_fidelity`, same field and heading.
   - (c) No new slice: put the check in `resume_structuring` (a second module or `service.py`) and the card in `resume_structuring`'s frontend.
2. **What the branded PDF does when values are flagged.** Today nothing can fix a flagged value: there's no edit feature, and a resubmit at `temperature=0` usually gives the same output.
   - (a) **Warn only.** The link works as today; the result screen shows the flagged values directly under the title band. **Recommended:** the recruiter is the human in the loop, the false-flag rate is unmeasured (question 8), and a block would be a dead end on a false flag.
   - (b) Block with a one-click override: `GET /api/resume_generation/{id}` answers 409 when values are flagged, unless `?confirmed=true`; the link becomes "Open branded PDF anyway" with `?confirmed=true`. Protects only against opening the URL directly; in the UI it is (a) with one more click.
   - (c) Hard block: 409, and the link is hidden while anything is flagged. The PDF can never be made for that CV.
   - (d) Leave unconfirmed values out of the PDF. A false flag silently drops real data from a client-facing document.
3. **How the result screen shows it.**
   - (a) **One warning card**, directly under the title band, listing each flagged value with its place ("Experience 2 · Company", "Google"). Nothing is shown when everything was found; **no "all verified" badge**, because hidden text passes and the badge would give false assurance. **Recommended:** no change to `StructuredResume`, which joins fields into lines ("title · company") and would need restructuring to mark single fields.
   - (b) Inline marks next to each flagged value inside the extracted-details card.
   - (c) Both.
   - Also confirm the proposed texts: heading "Not found in the CV text"; intro "AI returned these details, but they don't appear in the text read from the CV. Check them against the CV before you use the branded PDF."
4. **The past-interviews list.**
   - (a) **No change.** **Recommended:** the list deliberately never reads the CV text.
   - (b) An orange "N not found" pill per row, computed per row: each page then reads up to 20 CV texts (up to ~1 MB) and runs 20 checks.
   - (c) The same pill from a stored count column (a migration, plus a value that goes stale when the rules change).
5. **How strict the matching is.**
   - (a) **As designed (section 6.2):** case, spacing, line breaks, punctuation (bullets, dashes, quotes, dots), Unicode look-alikes and a leading `http(s)://`/`www.` on links are ignored; every letter and digit must match, in order, starting and ending on word boundaries. **Recommended.**
   - (b) Also forgive month spellings ("Jan" vs "January") and accents ("José" vs "Jose"). Fewer false flags, slightly more rules.
   - (c) Stricter: exact text after only case and whitespace. Many false flags: bullets, hyphenation, phone formats, "B.Sc." vs "BSc".
6. **Scanned CVs (OCR).** The check can't tell an OCR misreading from the truth, and the stored row doesn't say whether its text came from OCR.
   - (a) **Document the limit only.** **Recommended** for this feature.
   - (b) Store `read_by_ocr` (one boolean column and a migration in `resume_intake`) and say on the screen that the CV text was read by AI.
   - Related finding for the OCR work, not this feature: the raw-text card's subtitle "Exactly as extracted, before AI" is no longer true for a scanned CV.
7. **Hidden-text detection** (white, tiny, invisible or off-page text), which is what the user's example actually needs (section 8.5).
   - (a) **Next, as its own design, after this check is built.** **Recommended.**
   - (b) Inside this feature now (adds about 1–2 days for PDF and DOCX).
   - (c) Not at all.
8. **Calibration on real data.** May the tester run the check over the rows already in the dev database (read-only `GET /api/resume_intake/{id}`) and report only **counts per field label**, never the values?
   - (a) **Yes for synthetic rows (e.g. 358); rows from real people's CVs only with the user's OK.** **Recommended:** it is the only way to measure the false-flag rate before deciding question 2 for good.
   - (b) Synthetic rows only.
   - (c) No.

## 3. Flow
**Submit (existing flow, one addition at the end):**
1. The recruiter submits the interview form. `POST /api/resume_intake` streams `reading` → `extract_text` (a scan is read by OCR) → `extracting` → `structure_resume` (with its OCR retry) → `save_resume_intake` → `done`. Unchanged.
2. The `done` event's `result` is `ResumeIntakeResponse.model_validate(intake)`. When it is serialised, its new computed field `unconfirmed_values` calls `find_unconfirmed_values(extracted_text, structured_data)` (`resume_verification.service`). That is pure Python on the two values already in hand: no AI, no database, no I/O, a few milliseconds.
3. The browser receives `result.unconfirmed_values`. `App` opens `#/history/{id}`, and `IntakeResult` shows the warning card above the extracted details when the list isn't empty.

**Opening a past interview:**
1. `GET /api/resume_intake/{id}` returns the same `ResumeIntakeResponse`, so the same computed field runs on the stored `extracted_text` and `structured_data`.
2. Old rows get the check with no backfill. Rows from before AI extraction (`structured_data` null) get `unconfirmed_values: null`.

**Branded PDF:** unchanged under option 2(a). The link opens the PDF as today.

**Database:** nothing new is read or written. There is no new table, column or migration.

## 4. API
**No new endpoint, status code or error.** One read-only field is added to `ResumeIntakeResponse`, so it appears in:
- the `done` event of `POST /api/resume_intake` (`result.unconfirmed_values`)
- `GET /api/resume_intake/{intake_id}`

It is **not** added to `IntakeSummary` (`GET /api/resume_intake`), per open question 4(a).

| Field | Type | Meaning |
|---|---|---|
| `unconfirmed_values` | `list[UnconfirmedValue] \| null` | `null`: the intake has no `structured_data`, so there was nothing to check. `[]`: every value was found. Otherwise one item per value, or per line of a multi-line value, that wasn't found, in `ResumeData` field order. |

`UnconfirmedValue` (`resume_verification/schemas.py`):

| Field | Type | Example |
|---|---|---|
| `label` | `str` | `"Name"`, `"Skills"`, `"Experience 2 · Company"`, `"Project 1 · Technologies"` |
| `value` | `str` | The line as the AI wrote it, stripped. A link keeps its `https://`. |

Example:
```json
"unconfirmed_values": [
  {"label": "Skills", "value": "Kubernetes"},
  {"label": "Experience 1 · Description", "value": "Led a team of 40 engineers"},
  {"label": "Experience 2 · Company", "value": "Google"}
]
```

**Labels** (exact; the tests check them):
- Top-level fields: the field name with its first letter capitalised: `Name`, `Email`, `Phone`, `Location`, `Links`, `Skills`. List items of `links` and `skills` carry no number.
- Entries: `"<Section> <n> · <Field>"`.
  - `<Section>` is `Experience`, `Education`, `Project` or `Certificate`.
  - `<n>` is the 1-based position in the AI's list, which is the order the result screen shows.
  - `<Field>` is the field name with `_` turned into a space and the first letter capitalised: `Company`, `Title`, `Start date`, `End date`, `Location`, `Description`, `Institution`, `Degree`, `Field of study`, `Name`, `Link`, `Technologies`, `Issuer`, `Date`.

**OpenAPI:** the field is a Pydantic `computed_field`, so it shows in the response schema (read-only) and is ignored on input.

**Frontend type** (`resume_verification/UnconfirmedValues.tsx`, mirroring the backend): `export type UnconfirmedValue = { label: string; value: string }`. `ResumeIntake` in `resume_intake/api.ts` gains `unconfirmed_values: UnconfirmedValue[] | null`.

## 5. Data
**No table, no column, no migration.**
- Both inputs are already stored: `resume_intakes.extracted_text` and `resume_intakes.structured_data`.
- `extracted_text` is exactly the text the AI structured. That holds after the OCR retry too: `structure_resume` returns the text it structured, and that is what is stored (`docs/features/resume_intake.md`, "OCR for scanned CVs").
- For rows with `structured_data`, the text is at most 50,000 characters (`MAX_CV_TEXT_CHARS`), so the check's input is bounded.
- **Consequence:** the flags always reflect the *current* rules. A rule fix changes the flags of old intakes. Nothing records what a recruiter saw at the time. That's fine while nothing is stored or sent automatically based on a flag.

## 6. Slice layout

### 6.1 Backend: `backend/app/features/resume_verification/` (new)
No router (not mounted in `app/main.py`) and no models, like `resume_structuring`.

| File | What it does |
|---|---|
| `__init__.py` | Empty, like the other slices. |
| `schemas.py` | `UnconfirmedValue(BaseModel)` with `label: str` and `value: str`, with a docstring. |
| `service.py` | The check (section 6.2). Imports `UnconfirmedValue`, so other features import both names from `service` (rule 7, the `resume_structuring` pattern). Module docstring: deterministic, no AI, never logs CV text or values. |
| `README.md` | Purpose, the rules and their examples table, the known false flags and misses, "no endpoint, no table", the tests. |

### 6.2 `service.py`: the matching rules
**Constants:**
- `WORD = re.compile(r"[^\W_]+")`: one run of letters and digits (Unicode-aware; `_` excluded).
- `URL_PREFIX = re.compile(r"^(?:https?://)?(?:www\.)?", re.IGNORECASE)`
- `ENTRY_LABELS = {"experience": "Experience", "education": "Education", "projects": "Project", "certificates": "Certificate"}`

**Functions:**
- **`index_words(text: str) -> tuple[str, set[int], set[int]]`:**
  1. `unicodedata.normalize("NFKC", text).casefold()`.
  2. Every `WORD` match, in order, is appended to one string.
  3. The position where each word starts and ends in that string is recorded.
  4. Returns `(joined, starts, ends)`.

  So `"J. O’Brien – ﬁnance\n"` becomes `"jobrienfinance"`, from the words `j`, `o`, `brien` and `finance`, with word starts `{0, 1, 2, 7}` and ends `{1, 2, 7, 14}`.
- **`labelled_lines(data: ResumeData) -> list[tuple[str, str]]`:** walks `data.model_dump()` in field order and returns `(label, line)` for every non-blank line (`str.splitlines()`, then `strip()`) of every string value.
  - Strings and lists of strings at the top level, plus every string and list-of-strings field of every entry.
  - Labels as in section 4.
  - `None` and blank lines are skipped.
  - Every string field of `ResumeData` is covered: name, email, phone, location, links, skills; experience company, title, start and end date, location and description; education institution, degree, field of study, start and end date; project name, description, link and technologies; certificate name, issuer and date.
- **`is_in_text(line: str, cv_words: tuple[str, set[int], set[int]]) -> bool`:**
  1. Drops a leading `http://` / `https://` and/or `www.` from the line (`URL_PREFIX`, once).
  2. Builds the line's own `index_words` and uses only its joined string.
  3. **If that is empty** (the line is only punctuation, e.g. "—"), returns `True`: there's nothing to check.
  4. Otherwise it is found when the CV's joined string contains it at a position that **is a word start**, and its end **is a word end**. Loop `cv_joined.find(value_joined, position)` until such a match is found or there are none left.
- **`find_unconfirmed_values(cv_text: str, data: ResumeData) -> list[UnconfirmedValue]`:** the public API.
  - Indexes `cv_text` once.
  - Runs `is_in_text` on each line from `labelled_lines`. Identical lines are checked once (a dict inside the call), so 8,000 copies of one short skill cost one check.
  - Returns an `UnconfirmedValue(label, line)` for each line not found, in order.
  - Docstring: `cv_text` must be the text the AI structured.

**Why one rule covers the formatting cases.** Comparing letters and digits only absorbs most of the formatting differences without a rule for each:
- case
- whitespace and line breaks (reflowed descriptions)
- letter-spaced headings ("J A N E")
- hyphenation at line ends ("soft-\nware")
- bullets of any kind (`•`, `-`, `*`, Word's U+F0B7), so the `resume_generation` rule "a marker is stripped only when followed by a space" doesn't matter here
- dashes, quotes and dots ("B.Sc." vs "BSc")
- phone formatting
- ligatures, full-width and compatibility characters (NFKC)
- soft hyphens and zero-width characters

Requiring a match to start and end on word boundaries stops short values matching inside longer words. Without it, "CTO" would match inside "director" and "Java" inside "JavaScript".

**Examples** (they go into the test table and the README):

| CV text contains | Extracted value | Result |
|---|---|---|
| `JANE DOE` | `Jane Doe` | found |
| `J A N E   D O E` | `Jane Doe` | found |
| `Senior Software\nEngineer` | `Senior Software Engineer` | found |
| `soft-\nware` | `software` | found |
| `ﬁnance` (ligature U+FB01) | `finance` | found |
| `2019 – 2021` | `2019 - 2021` | found |
| `O’Brien` | `O'Brien` | found |
| `• Led team\n• Built API` | description `Led team\nBuilt API` | found (each line checked) |
| `o Led team\no Built API` | description `Led team\nBuilt API` | found (each line checked) |
| `+1 (555) 010-0100` | `+1 555 010 0100`, `+15550100100` | found |
| `B.Sc.` | `BSc` | found |
| `github.com/janedoe` | `https://github.com/janedoe` | found (the value's scheme is dropped) |
| `Links in the PDF:\nhttps://github.com/janedoe` | `https://github.com/janedoe` | found |
| `José` (decomposed, NFD) | `José` (composed) | found |
| (absent) | skill `Kubernetes` | **flagged** |
| `JavaScript` only | skill `Java` | **flagged** |
| `Director` only | title `CTO` | **flagged** |
| `Jan 2020` | `January 2020` | **flagged** (open question 5) |
| `JS` | `JavaScript` | **flagged** |
| `LinkedIn` (a label, no address) | `https://linkedin.com/in/jane` | **flagged** (an invented address) |

**Cost:** indexing a 50,000-character CV is about a millisecond. Each line is one C-level `find` (tens of microseconds). A typical CV has 100–300 lines, so the check takes well under 20 ms. FastAPI may validate a returned response model again, so it can run twice per response; that is accepted.

### 6.3 Other backend changes
| File | Change |
|---|---|
| `resume_intake/schemas.py` | `ResumeIntakeResponse` gains a `@computed_field` property `unconfirmed_values -> list[UnconfirmedValue] \| None`: `None` when `structured_data` is `None`, else `find_unconfirmed_values(self.extracted_text, self.structured_data)`. Its docstring says it is computed on every response and never stored. Imports both names from `app.features.resume_verification.service`. |
| `resume_intake/README.md` | The new response field and where it comes from. |
| `docs/architecture.md` | "What the system does" gains item 4 (`resume_verification`: checks every AI-extracted value against the CV text, without AI; the result screen lists those not found; no endpoint, computed when an intake is returned). Add the Decisions rows (section 10). |

`resume_generation` is unchanged under option 2(a).

### 6.4 Frontend: `frontend/src/features/resume_verification/` (new)
| File | What it does |
|---|---|
| `UnconfirmedValues.tsx` | Exports the `UnconfirmedValue` type and `UnconfirmedValues({ values }: { values: UnconfirmedValue[] })`, with JSDoc. Renders **nothing** for an empty list. Otherwise it renders `<section className="card unconfirmed-values" aria-labelledby="unconfirmed-values-heading">` containing: a `card-header` with an orange icon tile (`icon-tile tile-orange`, an inline warning-triangle SVG in the existing style) and `<h3 id="unconfirmed-values-heading">Not found in the CV text</h3>`; the intro paragraph (texts per open question 3); and a `<ul>` with one `<li>` per value, holding the label (`<span className="unconfirmed-label">`) and the value (`<span className="unconfirmed-value">`). **Values are plain text only, never links**, even URLs, because they are AI output the check couldn't confirm. |
| `unconfirmedValues.css` | The card's own look only: list spacing, the label in bold muted text, `overflow-wrap: anywhere` on values. |
| `UnconfirmedValues.test.tsx` | Section 9. |
| `README.md` | Purpose, what it shows, tests. |

No `api.ts`: the slice makes no backend call.

### 6.5 Other frontend changes
| File | Change |
|---|---|
| `resume_intake/api.ts` | `ResumeIntake.unconfirmed_values: UnconfirmedValue[] \| null`. The type is imported from `../resume_verification/UnconfirmedValues.tsx`, the same pattern as `ResumeData`. |
| `resume_intake/IntakeResult.tsx` | First child of `.intake-result`, before `StructuredResume`: `{result.unconfirmed_values && <UnconfirmedValues values={result.unconfirmed_values} />}`. Update the JSDoc. |
| `resume_intake/resumeIntake.css` | A grid area for the card, added only when the card is present, so the layout without flags is unchanged and gains no empty row gap: `.intake-result:has(> .unconfirmed-values)` gets `grid-template-areas: 'unconfirmed unconfirmed' 'candidate candidate' 'details aside' 'raw raw'`, and the ≤ 900 px media query gets `'unconfirmed' 'candidate' 'aside' 'details' 'raw'`. Add `.intake-result .unconfirmed-values { grid-area: unconfirmed; }`. Grid placement stays in this file, as it does for `.candidate-card`. `:has()` is already used in `app.css`. |
| `resume_intake/IntakeResult.test.tsx`, `IntakeDetail.test.tsx`, `InterviewForm.test.tsx` | The `ResumeIntake` fixtures gain `unconfirmed_values` (`[]` where `structured_data` is set, `null` where it is null). New tests in section 9. |
| `resume_intake/README.md` | Mentions the card. |
| `e2e/resume_intake.spec.ts` | `STORED_INTAKE` gains `unconfirmed_values: []`, so the canned stream mirrors the backend. One new test (section 9). **Written, not run** (UI testing is paused). |

## 7. Cross-feature contracts
- **`resume_verification` uses** `resume_structuring.service.ResumeData` (the type only).
- **`resume_intake` uses** `resume_verification.service.find_unconfirmed_values(cv_text: str, data: ResumeData) -> list[UnconfirmedValue]` and `UnconfirmedValue`, both from `service` (rule 7), in `schemas.py`. There is no import cycle: `resume_verification` imports only `resume_structuring`.
- **The contract the check relies on:** `cv_text` is exactly the text the AI structured. That is true today, including the OCR retry. Anyone who later preprocesses the text before the AI, or stores a different text, breaks the check. The `resume_verification` README and `structure_resume`'s docstring should both say so.
- **Frontend:** `resume_intake` imports the `UnconfirmedValues` component and the `UnconfirmedValue` type from `resume_verification`, the same pattern as `StructuredResume` and `BrandedPdfLink`.
- **`resume_generation`:** nothing under 2(a). Under 2(b) or (c), its router would call `find_unconfirmed_values(intake.extracted_text, data)` after validating `data`, and answer 409 `{"detail": "Some extracted details weren't found in the CV text. Check them on the result screen."}`.

## 8. Failure modes and security

### 8.1 Invalid input
- There is no new endpoint and no new user input.
- Blank values and lines, and punctuation-only lines, are skipped. Name and email are never blank (`extract_resume_data` rejects that).
- A row without `structured_data` gives `null`.
- Stored data that doesn't validate as `ResumeData` already fails in `ResumeIntakeResponse.model_validate`, before the check runs. That behaviour is unchanged.

### 8.2 AI provider errors and timeouts
None. The check never calls OpenAI; that is its point. It adds no request to the live budget.

### 8.3 Large or malformed input
- **Bounded:** for rows with data, the CV text is at most 50,000 characters, and the AI output is bounded by its token limit.
- **No regex built from data:** the only regexes are the fixed `WORD` and `URL_PREFIX`; matching uses `str.find`.
- **Worst case:** thousands of tiny or identical values from a hostile CV. Duplicates are checked once, and distinct one- and two-character values are few, so the cost stays well under a second. An unexpected exception in the computed field would give a 500 on GET, or cut the SSE stream after the row is saved. The function is pure and the tests cover it, so there is no `try`.

### 8.4 What the check catches and misses
**Catches:** any extracted value or line whose letters and digits don't appear, in order and on word boundaries, in the CV text. That covers hallucinations, rewording, translation, summaries, expanded abbreviations, completed dates and invented links.

**Known false flags** (real values flagged):
- The AI reformats despite the instructions: "Jan" → "January", "JS" → "JavaScript", "Doe, Jane" → "Jane Doe", an added country code, dropped accents.
- The AI splits a range: "Jan–Mar 2020" → start "Jan 2020". Or it turns an open range "2020 –" into "Present".
- Multi-column text layers that interleave lines, so a value is split across them.
- The AI merges several bullet lines into one when the bullets are letters or digits ("o", "1."). Symbol bullets are fine.
- The AI repairs a broken text layer, e.g. a missing ligature mapping or cp1252 replacement characters.
- HTML saved as `.doc` is stored with its entities ("AT&amp;T"), so "AT&T" is flagged.
- Scripts written without spaces (Chinese, Japanese, Thai) have long "words", so word boundaries rarely line up.
- The card numbers entries by the AI's list order. If the AI returned an empty entry, which the screen hides, the numbers are off by one.

**Known misses** (wrong content not flagged):
- **Anything in the extracted text, including hidden text.** This is the main gap against prompt injection (section 8.5).
- **Misattribution:** every value exists somewhere, but they are combined wrongly. A title from job A under company B, dates swapped between jobs, or "Present" borrowed from another job (which also inflates the PDF's computed "years of experience").
- **Omissions:** the check looks only at what the AI returned. A dropped job isn't noticed.
- **Short values that occur as words anywhere:** "Go", "R", "C++" (its letters are just "c"), "AI".
- **Differences in punctuation or symbols only:** "C++" vs "C#", "+30%" vs "-30%", "$" vs "€", "3.5" vs "35".
- **Scanned CVs:** the text is OCR output (AI), so an OCR misreading or an OCR-level injection is invisible to the check (open question 6). Note that OCR sees only what is visible on the page image, so white-on-white text is usually not transcribed at all.
- **"Links in the PDF:"** is our own appended heading, so a value "PDF" or "Links" always passes.

### 8.5 Hidden-text detection (out of scope; follow-up sketch)
The check can't catch text that is in the CV but hidden from human readers. That is also the classic "ATS keyword stuffing" trick (white text full of job keywords), so it matters beyond prompt injection.

**Sketch:**
- **PDF** (pypdfium2 raw API, inside `PDFIUM_LOCK` in `read_pdf`): for each text object (`FPDFPage_CountObjects` / `FPDFPage_GetObject`, type `FPDF_PAGEOBJ_TEXT`), flag it as hidden when:
  - its render mode is invisible (`FPDFTextObj_GetTextRenderMode`)
  - its fill alpha is 0 (`FPDFPageObj_GetFillColor`)
  - its effective size is under about 2 pt (`FPDFTextObj_GetFontSize` × the scale from `FPDFPageObj_GetMatrix`)
  - its bounds lie outside the page box (`FPDFPageObj_GetBounds`)
  - its fill colour matches the background. This needs the page rendered (`page.render()`, raw BGR buffer, no Pillow) and the pixels under the text's bounds sampled. Without that sampling, white text on a coloured sidebar (common in Canva CVs) is a false positive.
- **DOCX** (`collect_docx_text`): runs with `w:vanish`, a white `w:color`, or a tiny `w:sz`.
- **DOC:** needs character-property parsing that our reader doesn't do. Leave it out.
- **HTML-as-.doc:** `display:none` CSS. Leave it out.
- **What to do with hidden text:**
  - **Drop it** before the AI sees it. The fidelity check then flags any value that came only from hidden text, so the two features work together.
  - Or **keep it and warn** ("this CV contains N hidden text fragments").
  - Dropping is stronger, but a wrong detection silently loses real text.
- **Trap:** "searchable" scanned PDFs carry an *invisible* OCR text layer over the page image. Dropping it would make our own OCR run instead. That's acceptable, but it costs one more OpenAI request.
- **Cost:** about 1–2 days for PDF and DOCX with tests, built on the `make_pdf` test helper. Background sampling adds a page render (roughly 50–200 ms per page) under the lock.

### 8.6 Personal data
- **Nothing new is stored, sent or written to disk.** The values already travel in the same response; the flags are a subset of them.
- **No logging at all** in `resume_verification`: no CV text, values, labels or counts.
- **The screen** shows values as React text nodes (escaped), and never as links. A flagged `javascript:` or phishing URL can't be clicked from the card.
- **Access:** no auth, as for the rest of the API (localhost only; the existing Open row in `docs/architecture.md`).

## 9. Testing
No test calls OpenAI (the autouse `fake_openai` fixture is unchanged). There is no new real-PDF render.

**Backend (pytest):**
- **`tests/conftest.py`:** add `FAKE_CV_TEXT`, a synthetic CV text that contains every value of `FAKE_RESUME_DATA`, in a realistic layout (bullets, a line-broken title, the link under "Links in the PDF:").
- **`tests/features/resume_verification/test_service.py`** (no database):
  - **`is_in_text`:** a parametrized table with every row of the examples table in section 6.2.
  - **Punctuation-only and blank values:** `"—"` counts as found; blank lines are skipped.
  - **`find_unconfirmed_values(FAKE_CV_TEXT, FAKE_RESUME_DATA)`** returns `[]`.
  - **Labels:** with one unfound value placed in each field in turn, the exact label is returned: `Name`, `Email`, `Phone`, `Location`, `Links`, `Skills`, `Experience 1 · Company` … `Experience 1 · Description`, `Education 1 · Field of study`, `Project 2 · Technologies` (a second entry, to check the numbering), `Certificate 1 · Issuer`.
  - **Multi-line description:** with 3 lines, the 2nd not in the CV, exactly that line is returned.
  - **Order:** follows `ResumeData` field order, then entry order.
  - **Links:** the value keeps `https://` in the result, though the check drops it.
  - **The injection scenario:**
    - a CV text with "Ignore the above…" whose data adds the skill "Kubernetes", absent from the text, gets that flag;
    - **the documented miss:** when the same text also contains "Kubernetes", nothing is flagged. This test pins the limit in section 8.4.
  - **Duplicates:** 1,000 copies of one absent skill give 1,000 flags, and `is_in_text` (monkeypatched to count calls) runs once.
  - **No logging:** `caplog` is empty after a call that returns flags.
- **`tests/features/resume_intake/test_router.py`** (`db_session` in every test whose request can succeed: the leaked-rows lesson):
  - **POST, everything found:** a TXT CV with `FAKE_CV_TEXT` → the `done` result has `unconfirmed_values == []`.
  - **POST, one flag:** the same CV, and `fake_openai.result` adds the skill "Kubernetes" → `[{"label": "Skills", "value": "Kubernetes"}]`.
  - **GET `/{id}`** of a saved row returns the same list.
  - **GET of a row with SQL NULL `structured_data`** (built with `sqlalchemy.null()`, as in the `resume_generation` tests) → `unconfirmed_values` is `null`.
  - **The list (`GET ""`)** items have no `unconfirmed_values` key.

**Frontend (Vitest):**
- **`UnconfirmedValues.test.tsx`:**
  - With two values: the heading "Not found in the CV text", the intro, and both labels and values.
  - An empty list renders nothing (`container` is empty).
  - Values `https://evil.example` and `javascript:alert(1)` render as text, with no `link` role.
  - `<b>x</b>` renders literally.
- **`IntakeResult.test.tsx`:**
  - With one value, the card is shown before the candidate card.
  - With `[]`, there is no card.
  - With `structured_data: null` and `unconfirmed_values: null`, there is no card.

**End to end** (`e2e/resume_intake.spec.ts`; **written, not run**, as UI testing is paused): a canned `done` event whose result has `unconfirmed_values: [{label: "Skills", value: "Kubernetes"}]` shows the "Not found in the CV text" heading with "Skills" and "Kubernetes". `STORED_INTAKE` gains `unconfirmed_values: []`.

**Tester (API only, per learning.md):** pytest, plus real `GET /api/resume_intake/{id}` requests against a backend on `127.0.0.1:8000`. No AI, no Playwright, no browser.
- Check row 358 (synthetic, live-check data). Expected: `[]`, or a short list that the tester explains.
- Calibration across other rows only as the user answers open question 8, reporting counts per label and never values.

## 10. Decisions
| Decision | Simpler alternative considered | Why this |
|---|---|---|
| Compare letters and digits only (NFKC, casefolded), aligned to word starts and ends | (1) Exact substring after case and whitespace normalisation. (2) The same letters-and-digits comparison without word alignment. | (1) flags bullets, hyphenation, phone formats, "B.Sc."/"BSc" and dashes, each needing its own rule. (2) is one line shorter, but lets "CTO" pass inside "director" and "Java" inside "JavaScript": the short, high-impact values an injection would plant. Word alignment costs two sets and a short loop. |
| Each line of a multi-line value is checked on its own | Check the whole description at once | The AI strips bullets and reflows lines; a whole-description match fails on any one changed line and doesn't say which. Per line is the same code for every field (`splitlines()`). |
| Drop a leading `http(s)://` / `www.` from values, not from the CV text | A separate URL-normalising rule for both sides | The AI often adds a scheme the CV left out. Dropping it from the value is enough, because a match may start at any word, including after "https" and "www" in the CV. |
| Deterministic, exact (after normalisation) | Fuzzy matching (`difflib`, a similarity threshold), or a second AI pass | The user asked for a check that doesn't rely on AI. Fuzzy thresholds let small but meaningful changes pass ("2015" vs "2018" is 75 % similar), are slow on a 50,000-character text, and are hard to explain. |
| Computed on read as a Pydantic `computed_field` on `ResumeIntakeResponse` | (1) Store a JSONB column filled at upload. (2) Call the check in both router endpoints. (3) Its own `GET /api/resume_verification/{id}`. | (1) needs a migration and a backfill, goes stale when the rules change, and adds a second source of truth. (2) has two call sites and a field that can silently stay unset. (3) means an extra request, plus loading and error states in the UI. The computed field is one place for every response, needs no migration, and covers old rows. The cost is milliseconds, possibly twice per response. |
| New slice `resume_verification` (open question 1) | Inside `resume_structuring` | A separate step of the MinistryHub pipeline with its own responsibility (rule 5) and its own UI card. `resume_structuring` stays "the AI call". |
| Warn on the result screen; the PDF isn't blocked (open question 2) | Block the PDF | Nothing can fix a flag today (no edit, deterministic resubmit), and the false-flag rate is unmeasured. Blocking becomes the right default when the unattended Loxo flow arrives: there, a flag should send the CV to the brief's review list. |
| One warning card listing label and value (open question 3) | Inline marks inside `StructuredResume` | No change to another slice's component, which joins fields into lines. The card sits right under the title band, so it is seen before "Open branded PDF". |
| No "all verified" badge | A green confirmation when nothing is flagged | Hidden text passes the check, so a badge would claim more than it proves. |
| Identical lines checked once per call | No cache | One dict. It bounds the worst case of a hostile CV that makes the AI repeat a short value thousands of times. |

**Rows for the Decisions table in `docs/architecture.md`.** The developer adds them in the build change, once the open questions are answered. I didn't edit that file, because other sessions are editing shared docs right now.

| Date | Decision | Why |
|---|---|---|
| 2026-10-23 | Deterministic fidelity check (`resume_verification`, no AI): every non-blank line of every extracted value must appear in the CV text, comparing letters and digits only (NFKC, casefolded), starting and ending on word boundaries; a leading `http(s)://`/`www.` is dropped from values | User asked for a check that doesn't rely on AI. One rule absorbs case, spacing, punctuation, bullets, dashes, quotes, ligatures, hyphenation and phone formats; word alignment keeps short values from matching inside longer words. |
| 2026-10-23 | The check's result is computed on every intake response (`ResumeIntakeResponse.unconfirmed_values`, a Pydantic computed field), not stored | Both inputs are stored; no migration; old rows covered; a few ms per response |
| 2026-10-23 | Flagged values are shown to the recruiter; the branded PDF isn't blocked (per the user's answer to open question 2) | No edit feature yet, so a block would be a dead end on a false flag |

## Docs updated with this change
- **This file.**
- **Slice READMEs:** `resume_verification` (backend and frontend, new), `resume_intake` (backend and frontend).
- **`docs/architecture.md`:** "What the system does" item 4 and the Decisions rows above.
- **`.claude/learning.md`:** the user's answers to section 2, and the "what it catches and misses" summary.

## Known limits (accepted)
- Section 8.4: the false flags and misses, above all **hidden text** and **scanned CVs**.
- **The flags follow the current rules.** Old intakes' flags change when the rules change; nothing records what the recruiter saw.
- **A flag can't be resolved in the app:** there is no edit, accept or dismiss. The recruiter decides whether to use the PDF.

## Not doing
- Hidden-text detection (open question 7; sketch in section 8.5).
- Detecting omissions, or checking that values belong together (title with its company, dates with their job).
- Editing, accepting or dismissing a flagged value.
- Storing the check's result, or an audit trail of it.
- A history-list indicator (open question 4).
- Blocking or changing the branded PDF (open question 2).
- Any AI-based verification.
