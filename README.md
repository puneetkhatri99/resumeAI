# ResumeAI

ResumeAI takes an existing resume, uses AI to turn it into structured data, and generates a new resume from a predefined template. The features are still being specified. See [docs/architecture.md](docs/architecture.md).

## Prerequisites
- Python 3.14
- Node.js 24 and npm
- PostgreSQL, installed locally (see step 1)

## 1. PostgreSQL (one time)
1. Install PostgreSQL in either of these ways:
   - Run `winget install PostgreSQL.PostgreSQL.18`.
   - Use the installer from https://www.postgresql.org/download/windows/.

   Set a password for the `postgres` user when asked, and keep port `5432`.
2. Create the database in *SQL Shell (psql)* or pgAdmin. The installer doesn't add `psql` to PATH, so in a terminal use `& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost`.
   ```sql
   CREATE DATABASE resume_ai;
   ```
3. Copy `backend/.env.example` to `backend/.env`, then put your password into `DATABASE_URL` and your OpenAI API key into `OPENAI_API_KEY`. Both are required: the backend won't start without them.

## 2. Backend
```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m playwright install --only-shell chromium   # once: the headless browser that prints the branded PDF
alembic upgrade head        # creates / updates the tables (resume_intakes, ai_calls)
fastapi dev app/main.py
```
The Playwright step needs no admin rights and downloads only Chromium's headless shell into `%LOCALAPPDATA%\ms-playwright` (shared with the frontend's Playwright). Without it, "Open branded PDF" answers 500.
- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

## 3. Frontend
```powershell
cd frontend
npm install
npx playwright install chromium   # once, for end-to-end tests
npm run dev
```
Open http://localhost:5173. It should show **Backend OK · Database OK**.

## Checks and tests
| What | Run in | Command |
|---|---|---|
| Backend lint / format | `backend/` | `ruff check .` / `ruff format --check .` |
| Backend tests | `backend/` | `pytest` (the DB tests need Postgres; one test renders a real PDF and needs the Playwright Chromium shell) |
| Frontend lint | `frontend/` | `npm run lint` |
| Frontend type-check + build | `frontend/` | `npm run build` |
| Frontend unit / component tests | `frontend/` | `npm test` |
| End-to-end (starts both servers) | `frontend/` | `npm run test:e2e` (needs Postgres and `backend/.env`; makes no OpenAI calls) |
| End-to-end, including the live AI test | `frontend/` | `$env:E2E_LIVE_AI=1; npm run test:e2e` (1 real OpenAI request; run only with the user's permission) |

No test calls OpenAI except the e2e live test above: pytest replaces the client with a fake, and the other e2e tests answer the upload with canned responses.

## Project docs
- [docs/architecture.md](docs/architecture.md): structure, feature-slice conventions, decisions.
- [.claude/rules.md](.claude/rules.md): project rules, for people and for Claude.
- [.claude/learning.md](.claude/learning.md): learnings log.
- [CLAUDE.md](CLAUDE.md): the Claude Code entry point, with the agents and the workflow.
