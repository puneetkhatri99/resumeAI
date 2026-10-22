# ResumeAI

ResumeAI takes a user's existing resume, uses AI to turn it into structured data, and generates a new resume from a predefined template. The user supplies feature requirements as work goes on. Don't build on assumptions; ask.

@.claude/rules.md
@.claude/learning.md

## Stack
- **Backend:** `backend/`. FastAPI, SQLAlchemy 2, Alembic, Postgres (psycopg 3), Python 3.14, pip + venv.
- **Frontend:** `frontend/`. React 19, TypeScript, Vite, Vitest, Playwright, oxlint.
- **Structure, conventions and decisions:** [docs/architecture.md](docs/architecture.md).

## Commands (Windows / PowerShell)
**Backend** (run from `backend/`, venv at `backend/.venv`):
- Activate the venv: `.venv\Scripts\Activate.ps1`
- Once after `pip install`: `python -m playwright install --only-shell chromium` (the headless browser that renders the branded PDF; no admin rights)
- Run: `fastapi dev app/main.py` → http://localhost:8000/docs
- Lint / format: `ruff check .` / `ruff format .`
- Test: `pytest`. The DB tests need Postgres and `backend/.env`. pytest never calls OpenAI (autouse `fake_openai` fixture). Exactly one test renders a real PDF with headless Chromium (user-approved); every other test fakes `render_pdf`.
- `backend/.env` needs `DATABASE_URL` and `OPENAI_API_KEY`; both are required.
- Migrations: `alembic revision --autogenerate -m "<message>"`, then `alembic upgrade head`

**Frontend** (run from `frontend/`):
- Run: `npm run dev` → http://localhost:5173. It proxies `/api` to `:8000`.
- Lint: `npm run lint`
- Type-check + build: `npm run build`
- Unit / component tests: `npm test` (Vitest)
- End-to-end: `npm run test:e2e` (Playwright; starts both servers, needs Postgres; no OpenAI calls)
- End-to-end with the live AI test: `$env:E2E_LIVE_AI=1; npm run test:e2e`. It makes 1 real OpenAI request; never run it without the user's permission.

## Agents (`.claude/agents/`)
| Agent | Model | Use for |
|---|---|---|
| `senior-architect` | opus | Designing a feature or system change. Writes `docs/features/<feature>.md` and no app code. |
| `architecture-challenger` | opus | Questioning the architect's choices: necessity, simpler options, failure modes, PII, cost. Read-only. |
| `software-developer` | opus | Building approved designs and fixing bugs: production code, docs, tests for the logic. |
| `tester` | sonnet | API, UI and end-to-end automated tests and usage checks. Reports bugs and does not edit app code. |

## Workflow for a new feature
1. `senior-architect` designs it in `docs/features/<feature>.md` and lists open questions.
2. `architecture-challenger` reviews the design, and `senior-architect` revises it.
3. All open questions go to the user. Nothing is built on an assumption.
4. The user approves the design.
5. `software-developer` builds the slice, its docs, and tests for its logic.
6. `tester` writes and runs the API, UI and end-to-end tests and reports bugs, which go back to `software-developer`.
7. Anything learned goes into `.claude/learning.md`.

Subagents can't ask the user directly. When an agent returns open questions, ask the user (AskUserQuestion) before continuing.
