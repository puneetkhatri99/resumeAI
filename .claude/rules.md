# Project rules

These rules come from the user. They apply to every session and every agent. Follow them exactly.

## 1. No assumptions: always ask
If something isn't stated by the user or already decided in `docs/` or `.claude/learning.md`, ask the user before acting on it. That covers requirements, names, behaviour, data fields, edge cases and tech choices.
Subagents can't ask the user directly. They stop and return their open questions to the main session, which asks the user.

## 2. Simplicity over complexity
If something can be done simply, do it simply. Build the smallest thing that fully meets the requirement. No speculative features, options, layers, abstractions or dependencies added "for later".

## 3. Document everything
- **Feature README:** every feature folder has a `README.md` covering purpose, API endpoints, data (tables and fields), and where its tests live. Update it in the same change as the code.
- **Feature designs** live in `docs/features/<feature>.md`.
- **Architecture doc:** `docs/architecture.md` is updated whenever the structure, a convention or a decision changes.
- **Docstrings:** every module has a docstring. Every public function, class and React component has a docstring or JSDoc saying what it does, and why when that isn't obvious.
- **Setup docs:** setup and commands stay current in `README.md` and `CLAUDE.md`.

## 4. Stack
- **Backend:** FastAPI (Python 3.13). Dependencies go through pip + venv with exact pins in `backend/requirements.txt` / `requirements-dev.txt`.
- **Database:** PostgreSQL, accessed through SQLAlchemy 2. Schema changes go through Alembic migrations.
- **Frontend:** React + TypeScript (Vite).

Ask before adding any other framework, database, language or package manager (rule 1).

## 5. Naming and modularity
Keep naming consistent:

| Thing | Convention | Example |
|---|---|---|
| Feature folder (same name in backend and frontend) | snake_case | `resume_intake` |
| Python module / function / variable | snake_case | `parse_resume_file` |
| Python class | PascalCase | `ResumeSection` |
| Constant (Python and TS) | UPPER_SNAKE_CASE | `MAX_UPLOAD_BYTES` |
| React component and its file | PascalCase, `.tsx` | `UploadForm.tsx` |
| Other TS file / function / variable | camelCase | `client.ts`, `apiGet` |
| TS type | PascalCase | `HealthResponse` |
| Test file | backend `test_<module>.py`; frontend `<Component>.test.tsx`; e2e `<feature>.spec.ts` | `test_router.py` |

Write modular code that is easy to use:
- Each function is a meaningful unit of work with a clear name.
- Don't break logic into chains of 2–3-line helper functions, and don't write giant functions either.
- Each module has one responsibility.

## 6. Record learnings
When you learn something new, add a dated entry to `.claude/learning.md` in the same session. That includes what the user exactly wants, a preference or decision, or the root cause and fix of a significant bug.

## 7. Feature-based (vertical slice) structure
Code is organised by feature, not by technical layer. Full details are in `docs/architecture.md`.

**Backend:** `backend/app/features/<feature>/`
- `router.py`: HTTP only.
- `schemas.py`: Pydantic models.
- `service.py`: business logic.
- `models.py`: SQLAlchemy tables.
- Create a file only when the slice needs it.
- Mount the router in `app/main.py` under `/api`.

**Frontend:** `frontend/src/features/<feature>/` holds that feature's components, `api.ts` (backend calls), hooks and types.

**Shared code** goes only in `backend/app/core/` and `frontend/src/shared/`, and only once 2 or more features need it.

**Between features:** a feature uses another feature only through its `service.py` (backend) or its `api.ts` / exported components (frontend), never through its internals.

**Tests mirror features:**
- Backend: `backend/tests/features/<feature>/`
- Frontend: colocated `*.test.tsx`
- End to end: `frontend/e2e/<feature>.spec.ts`
