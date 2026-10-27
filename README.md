# ResumeAI

ResumeAI takes an existing resume, uses AI to turn it into structured data, and generates a new resume from a predefined template. The features are still being specified. See [docs/architecture.md](docs/architecture.md).

## Prerequisites
- Python 3.13 (see [Python version and the venv](#python-version-and-the-venv))
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

   Optional: `AI_SPEND_CAP_USD` (default `5`) is the all-time AI spending cap in USD. Once the recorded AI cost reaches it, uploads answer HTTP 402 ("AI spending limit reached. Uploads are paused.") until you raise it and restart the backend. Every `/api` endpoint is also rate-limited per IP (120 requests a minute, plus 30 uploads an hour; HTTP 429 when over). See [docs/features/rate_limit_and_spend_cap.md](docs/features/rate_limit_and_spend_cap.md).

## 2. Backend
```powershell
cd backend
py -V:3.13 -m venv .venv    # always name 3.13; see the next section
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

If Windows says "An Application Control policy has blocked this file" for `fastapi`, run it through the venv's Python instead: `python -m fastapi dev app/main.py`.

### Python version and the venv
The backend uses **Python 3.13**. Several Python versions can be installed side by side, and each project's `.venv` stays tied to the version that created it. The `py` command (the Python install manager, from https://www.python.org/downloads/windows/) handles the versions.

**Install a Python version** (once per version, no admin rights):
```powershell
py install 3.13
py list               # shows every installed version
```

**Create the venv with 3.13.** Always name the version. A bare `python -m venv` or `py -m venv` uses the default Python, which is usually the newest installed one, not 3.13.
```powershell
cd backend
py -V:3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python --version      # should print Python 3.13.x
```
Once the venv is active, `python` and `pip` are its 3.13, whatever other versions are installed.

**Check which version an existing venv uses:**
```powershell
Get-Content backend\.venv\pyvenv.cfg    # the "version =" line
```

**Clear the venv and rebuild it with 3.13** (for example after it was created with the wrong version):
```powershell
cd backend
deactivate                                # only if the venv is active
Remove-Item -Recurse -Force .venv
py -V:3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m playwright install --only-shell chromium   # quick if Chromium is already downloaded
```
The database and `backend/.env` aren't touched by this.

**Remove a Python version you no longer need:** `py uninstall 3.15`. Any venv created with it stops working and must be rebuilt.

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

## Deploy (Render)
The app deploys as one Docker web service from the root [Dockerfile](Dockerfile). It builds the frontend, and FastAPI serves it at `/` with the API at `/api`. The container runs `alembic upgrade head` on every start.

1. On Render, go to **New → Web Service**, pick this GitHub repo, and set **Language** to **Docker**.
2. Under **Environment**, set `DATABASE_URL` (the Aiven URL from `backend/.env`, starting `postgresql+psycopg://` and ending `?sslmode=require`) and `OPENAI_API_KEY`. `AI_SPEND_CAP_USD` is optional (default 5). `.env` isn't in the image.
3. Deploy, then check that `https://<service>.onrender.com/api/health` shows `{"status":"ok","database":"ok"}`.

There's no login yet, so anyone with the URL can use the app.

## Project docs
- [docs/architecture.md](docs/architecture.md): structure, feature-slice conventions, decisions.
- [.claude/rules.md](.claude/rules.md): project rules, for people and for Claude.
- [.claude/learning.md](.claude/learning.md): learnings log.
- [CLAUDE.md](CLAUDE.md): the Claude Code entry point, with the agents and the workflow.
