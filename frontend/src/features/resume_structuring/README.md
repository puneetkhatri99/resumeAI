# resume_structuring (UI)

Shows the details AI extracted from a CV. It is the frontend half of `backend/app/features/resume_structuring/` (that README has the purpose), and the design is in `docs/features/resume_structuring.md`.

**What it shows:** `StructuredResume` renders a `ResumeData` as two cards: the candidate (initials avatar, name, and email, phone and current-location pills under a screen-reader "Contact" heading, each pill with a screen-reader prefix such as "Location: ", and a note to check the AI's details against the CV text), and headed sections, each with its own colour and icon: Links, Skills, Experience, Education, Projects and Certificates (`structuredResume.css`).
- Empty sections, entries with nothing in them, and null fields are left out.
- Skills are coloured chips. An experience entry shows "title · company", then "start – end · location", then the description; education shows "degree, field of study", then "institution · start – end".
- Only http(s) links are clickable (they open in a new tab). Anything else, e.g. a `javascript:` URL, stays plain text, because links come from AI output.
- `resume_intake`'s `IntakeResult` renders it under the CV's file name, when the intake has structured data.

**Files:** `StructuredResume.tsx` exports the component and the `ResumeData` type (which mirrors `ResumeData` in the backend's `schemas.py`).

**Tests:** `StructuredResume.test.tsx` (Vitest): every section renders with its details, including the location pill; empty sections, empty entries and null fields are hidden.
