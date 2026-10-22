# resume_generation (UI)

The link that opens the branded MinistryHub PDF. It is the frontend half of `backend/app/features/resume_generation/` (that README has the purpose and the endpoint), and the design is `docs/features/resume_generation.md`.

**What it shows:** `BrandedPdfLink` renders "Open branded PDF", a plain `<a href="/api/resume_generation/{intakeId}" target="_blank" rel="noopener noreferrer">` styled as the shared outline button (`button button-outline`). There is no JavaScript: the backend renders the PDF on demand, the browser's PDF viewer shows and saves it, and an error (e.g. `{"detail": "Intake not found."}`) shows as plain JSON in that tab. In dev, Vite proxies `/api` to the backend.
- `resume_intake`'s `IntakeResult` shows it in the title band, before "New interview", only when the intake has `structured_data` (so also on a past interview opened from the history).

**Files:** `BrandedPdfLink.tsx`.

**Tests:** `src/features/resume_intake/IntakeResult.test.tsx` (Vitest): the link's `href`, `target="_blank"` and `rel` with `noopener`, and no link when `structured_data` is null. No browser or e2e test (UI tests are paused).
