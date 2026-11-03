# candidates (frontend)

**Purpose:** add a candidate from one CV and list the candidates. Design: [docs/features/candidates.md](../../../../docs/features/candidates.md); backend: [backend/app/features/candidates/README.md](../../../../backend/app/features/candidates/README.md).

## Pages
- **`#/candidates/new`: `AddCandidateForm`.** The `CvDropzone` exported by `resume_intake` and an **Add candidate** button. A CV over 10 MB is refused before sending. While sending, the drop zone and the button are disabled (a double submit would pay the AI twice) and a progress card covers the page: Extracting text, AI cleaning, Creating resume, Uploading, each shown for at least 700 ms (the stage queue is copied from `InterviewForm`, which is untouched). Errors show next to the button: 402 and 429 with the backend's message, 413 as "The CV is larger than 10 MB.", error events with their detail, and the network (or a stream that ends without a result) as "Could not reach the server…". When done: **Open new resume** (new tab) and **View candidates** (`#/candidates`).
- **`#/candidates`: `CandidateList`.** A table, newest first, 20 at a time with **Load more**: name, old resume (presigned link, expires after 1 hour), new resume (public URL), time added (browser time zone), source (Manual / API), AI cost (`formatUsd`, "—" when null) and **Delete**. Both links open in a new tab (`rel="noopener noreferrer"`). Delete asks `window.confirm("Delete this candidate and both resume files?")`, then removes the row on 204; a failure shows the backend's message and keeps the row.

## Files
- `api.ts`: types mirroring the backend schemas; `addCandidate(file, onStage)` (reads the event stream with `apiPostEventStream`), `listCandidates(beforeId?)`, `deleteCandidate(id)` (a plain `fetch`: `shared/api/client.ts` has no DELETE helper and no other feature needs one).
- `AddCandidateForm.tsx`, `CandidateList.tsx`, `candidates.css` (the action bar, done card and table). The progress overlay's styles are shared in `src/app/app.css`.

## Tests
Vitest, colocated (Playwright stays paused):
- `AddCandidateForm.test.tsx`: sends the file, disables the form, shows each stage, then the done links; 402, 413, 429, network, an error event and a stream with no result each show their message.
- `CandidateList.test.tsx`: every column with "—" for a null cost and new-tab links; Load more; the empty state; Delete confirms, calls the API and removes the row; a cancelled confirm does nothing; a failed delete shows the message; a load error with Try again.
