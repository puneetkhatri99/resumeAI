# auth

**Purpose:** login for the web app. One account per recruiter (by email), roles `admin` and `recruiter`, server-side sessions in a cookie, everyone can change their own password, and the admin page adds, resets and removes recruiters. The only admin is the bootstrap admin from `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Design: [docs/features/auth.md](../../../../docs/features/auth.md); frontend: [frontend/src/features/auth/README.md](../../../../frontend/src/features/auth/README.md).

## Which routes need login
`app/main.py` attaches `require_user` to the `resume_intake`, `resume_generation`, `ai_usage` and `candidates` routers (`include_router(..., dependencies=[Depends(require_user)])`), so it runs before their own dependencies: an upload without login is a plain **401** `{"detail": "Not logged in."}`, before the spending cap (402) and any AI call. Open: `/api/health`, `/api/auth/login`, `/api/auth/logout`, and `/`, `/docs`, `/redoc`, `/openapi.json`. `/api/public/*` keeps its own `X-API-Key` and needs no login. `tests/features/auth/test_router.py` checks every `/api` route from the OpenAPI schema, so a new router without a guard fails the tests.

## Session cookie
`session=<token>; HttpOnly; Secure; SameSite=Lax; Path=/api; Max-Age=43200`. The token is `secrets.token_urlsafe(32)`; only its SHA-256 hex is stored. Sessions last 12 hours from login (fixed, not sliding). A login prunes that user's expired sessions. `Secure` works on `http://localhost` in dev (browsers treat it as a secure context).

## API
All under `/api/auth`, JSON. The 503 body is always `{"detail": "The database is unavailable. Try again later."}`. 401 only ever means "not logged in" (the browser then shows the login page).

| Method and path | Guard | Request | Success | Errors |
|---|---|---|---|---|
| `POST /login` | none (login rate limit) | `{email: 1–254, password: 1–1024}` | 200 `{email, role}` + cookie | 401 "Email or password is incorrect." (same for an unknown email: same status, body and timing) · 422 · 429 · 503 |
| `POST /logout` | none | none | 204; deletes the session if any and always clears the cookie | 503 (cookie still cleared) |
| `GET /me` | user | none | 200 `{email, role}` | 401 · 503 |
| `POST /password` | user (login rate limit) | `{current_password: 1–1024, new_password: 12–1024}` | 204; the user's other sessions end, this one stays | 400 "Current password is incorrect." · 401 · 422 · 429 · 503 |
| `GET /recruiters` | admin | none | 200 `{items: [{id, email, created_at}]}`, sorted by email, recruiters only | 401 · 403 "Admins only." · 503 |
| `POST /recruiters` | admin | `{email, password: 12–1024}` | 201 `{id, email, created_at}` | 401 · 403 · 409 "An account with this email already exists." · 422 · 503 |
| `PUT /recruiters/{id}/password` | admin | `{password: 12–1024}` | 204; all that recruiter's sessions end | 401 · 403 · 404 "Recruiter not found." · 422 · 503 |
| `DELETE /recruiters/{id}` | admin | none | 204; sessions go by `ON DELETE CASCADE` | 401 · 403 · 404 · 503 |

- **Emails** are trimmed and lower-cased everywhere (login, add, `ADMIN_EMAIL`). On add they must match `^[^@\s]+@[^@\s]+$` and be 3–254 characters (422 otherwise); at login they aren't format-checked.
- **`{id}`** is 1 to 2,147,483,647 (422 beyond, not a false 503). The admin's own id is 404: the admin routes act on `role = 'recruiter'` rows only, so the admin can't be removed, reset or demoted through them, and no route creates an admin.
- **Rate limit:** `POST /login` and `POST /password` share the `login` bucket in `core/rate_limit.py`: 10 per 15 minutes per client IP, on top of the 120/min overall limit. On Render every visitor shares one IP until the forwarded-header fix, so the bucket is shared by everyone (accepted).

## Passwords
Stdlib `hashlib.scrypt` (`n=2**15, r=8, p=3, maxmem=64 MiB, dklen=32`, 16-byte random salt), stored as `scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>`. 12 to 1024 characters, no other rules, for every password set anywhere (add, reset, change, `ADMIN_PASSWORD`). About 0.4 s per hash here. Never logged: passwords, tokens, hashes or emails.

## Bootstrap admin (`ADMIN_EMAIL`, `ADMIN_PASSWORD`)
Both optional, in `backend/.env` and on Render. At every startup, `app/main.py`'s lifespan calls `ensure_admin`:
- Both unset or empty: nothing happens (and no database work). With no admin, nobody can manage accounts: it fails closed.
- Only one set: a warning, nothing created.
- `ADMIN_PASSWORD` outside 12–1024 characters: **startup fails** with a message (the password isn't printed).
- Otherwise the admin is created only if no account has that email (`INSERT … ON CONFLICT (email) DO NOTHING`, safe if two processes start at once). An existing account is **never changed**: a later `ADMIN_PASSWORD` change has no effect (the admin changes their password in the app), and a recruiter with that email isn't promoted (a warning is logged).
- Database down: the error is logged and the app starts anyway; the next start tries again.

**Forgotten admin password:** there is no route for it. With the user's go-ahead, delete the admin's row (`DELETE FROM users WHERE email = '<admin email>' AND role = 'admin';`; their sessions go by cascade, recruiters are untouched), make sure `ADMIN_EMAIL` / `ADMIN_PASSWORD` hold the new password, and restart the app: `ensure_admin` recreates the admin.

## Data
**`users`** (`User`): `id` (identity PK), `created_at` (`timestamptz`, `now()`), `email` (`varchar(254)`, unique), `password_hash` (`text`), `role` (`varchar(9)`, `CHECK (role IN ('admin', 'recruiter'))`).

**`user_sessions`** (`UserSession`): `id` (identity PK), `user_id` (FK `users.id` `ON DELETE CASCADE`, indexed), `token_hash` (`varchar(64)`, unique: the per-request lookup), `created_at`, `expires_at` (`timestamptz`).

Migration `381a21f5f9fb` (create users and user_sessions tables).

## Files
- `router.py`: `require_user` (cookie read with `APIKeyCookie(name="session", auto_error=False)`), `require_admin`, the 8 endpoints, setting and clearing the cookie.
- `service.py`: `hash_password`, `verify_password` (a malformed stored hash is False), `DUMMY_HASH`, `authenticate`, `start_session`, `find_session_user`, `end_session`, `change_password` (`WrongPasswordError`), `list_recruiters`, `add_recruiter` (`EmailTakenError`, also on a race via the unique constraint), `reset_recruiter_password`, `remove_recruiter`, `ensure_admin`, `normalize_email`, `hash_token`. Uses only `app.core`.
- `schemas.py`: `LoginRequest`, `CurrentUser`, `PasswordChange`, `NewRecruiter`, `PasswordReset`, `Recruiter`, `RecruiterList`; `MIN_PASSWORD_LENGTH = 12`, `MAX_PASSWORD_LENGTH = 1024`.
- `models.py`: `User`, `UserSession`.

## Tests
`backend/tests/features/auth/` (synthetic accounts, `db_session`, no live calls):
- `test_service.py`: hashing round trip, salts, stored parameters, malformed hashes; the unknown-email dummy verify; expired sessions pruned and the 12-hour lifetime; `change_password` keeps only the current session; reset/remove refuse the admin; every `ensure_admin` case.
- `test_router.py`: cookie attributes and the hashed token; the same 401 for unknown email and wrong password; 422s; the 11th attempt is 429 and the password change shares the bucket; `/me` with no, unknown or expired cookie; logout; password change (400 not 401, 422, other sessions end); add (201, 409 incl. the admin's email in other case, 422), list, reset, remove, 404 for the admin's and unknown ids, 422 beyond the integer range; **the OpenAPI-driven guard test** (every `/api` route 401 without login unless allow-listed, every admin route 403 for a recruiter); an upload without login is 401 before the spending cap and any AI call; startup runs `ensure_admin`.

`tests/conftest.py`: the autouse `fake_recruiter` fixture answers `require_user` with a fake recruiter for every other test; `real_auth` removes it; the autouse `no_admin_bootstrap` fixture unsets `ADMIN_*` so no `TestClient` startup writes the `.env` admin to the real database; the `client` fixture uses `https://testserver` so the `Secure` cookie is sent back.
