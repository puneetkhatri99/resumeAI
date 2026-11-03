# auth: login, roles and the admin page

Status: **approved and built** (2026-11-03). It includes the user's answers and the architecture-challenger's review. Section 11 was answered with (a): documented in `backend/app/features/auth/README.md`, no code. Migration `381a21f5f9fb` ran on the local Postgres only.

**Slice name: `auth`.** It covers login, logout, the "who am I" check, the route guards, changing your own password, the bootstrap admin and the admin page's account management. `login` would name only one of those. A separate `admin` slice would split one table's logic across two slices. The API prefix follows the folder: `/api/auth/...`.

## 1. Goal
The user asked (2026-11-03), verbatim: **"add login functionality"**. Their answers settle the rest:
- **Accounts:** one account per recruiter, identified by email.
- **Roles:** `admin` and `recruiter`. An admin can do everything a recruiter can, plus use the admin page.
- **Admin page:** an admin can add a recruiter (email plus a password the admin sets), reset a recruiter's password (this ends their sessions) and remove a recruiter (the account and its sessions). There is no promote or demote: the only admin is the bootstrap admin.
- **Bootstrap admin:** created on startup from `ADMIN_EMAIL` and `ADMIN_PASSWORD`.
- **Own password:** every user can change it (current password plus new password); a change ends their other sessions.
- **Sessions:** last 12 hours from login.

Today anyone with the Render address can read every interview and candidate, and open real people's original CVs. After this feature, the web app and every `/api` route it uses answer only to a logged-in user. `/api/public/*` (`X-API-Key`) and `/api/health` stay as they are.

## 2. Settled answers (no longer open)
| Topic | Answer |
|---|---|
| Who logs in | One account per recruiter, by email. Roles `admin` and `recruiter`. Only the bootstrap admin is an admin. |
| Account management | Admin page: add, reset password, remove. No sign-up, no CLI command, no promote or demote. |
| First admin | Created on startup from `ADMIN_EMAIL` and `ADMIN_PASSWORD` (section 3, "Bootstrap"). |
| Own password | Change form for everyone; it ends that user's other sessions. |
| Password hashing | Stdlib `hashlib.scrypt`, `n=2**15, r=8, p=3, maxmem=64*1024*1024, dklen=32`, with a 16-byte random salt (OWASP-equivalent; no new package). |
| Password rules | At least 12 characters, at most 1024, nothing else. This applies to every password set anywhere: add, reset, change and `ADMIN_PASSWORD`. |
| Session | 12 hours from login, fixed (no sliding). |
| Login rate limit | 10 attempts per 15 minutes per client IP. On Render it is shared by everyone (accepted limit, section 8). |
| `/docs`, `/redoc`, `/openapi.json` | Unchanged (open; they show the API's shape, never data). |
| e2e | Left alone while UI testing is paused (known gap: the specs that use the real backend will get 401). |
| Header | "Log out", "Change password", and "Admin" for admins only. The email isn't shown. |
| Who-did-what | No `user_id` on stored rows (out of scope). |
| Rollout | (1) The main session runs the auth migration on Aiven (with the user's go-ahead at that time); (2) the admin is created from `ADMIN_*` at the next startup; (3) push to Render. |

## 3. Flow

### Bootstrap admin (every app start)
1. `app/main.py` gets a FastAPI `lifespan` that calls `auth.service.ensure_admin(db)` once at startup, with its own session from `app.core.database`.
2. **Both `ADMIN_EMAIL` and `ADMIN_PASSWORD` unset or empty:** nothing happens. The app starts; if no admin exists, nobody can manage accounts (and before the first admin, nobody can log in: it fails closed). Pytest and alembic need neither variable.
3. **Only one of them set:** a warning is logged ("ADMIN_EMAIL and ADMIN_PASSWORD must both be set"), nothing is created, and the app starts.
4. **Both set:**
   - The email is trimmed and lower-cased.
   - If `ADMIN_PASSWORD` is shorter than 12 characters, startup **fails** with a clear message (the password itself is never printed). A short admin password must not slip through silently.
   - Otherwise the admin is inserted with `INSERT … ON CONFLICT (email) DO NOTHING`. That creates it only if missing, and it is safe if two processes start at once.
5. **The email already exists:** nothing changes, ever. A changed `ADMIN_PASSWORD` has **no effect** once the admin exists; the admin changes their password in the app. If the existing row is a recruiter (someone pointed `ADMIN_EMAIL` at a recruiter), it isn't promoted and a warning is logged.
6. **Database unavailable at startup:** the error is logged (no parameters: `hide_parameters=True`) and the app still starts; `/api/health` shows 503. The next start tries again.

### Login
1. The browser loads `/` (the static app; it holds no data and isn't protected).
2. `App` calls `GET /api/auth/me`. A 401 shows `LoginPage` in place of the page for the current hash. The hash is kept, so after login the user lands where they meant to go.
3. The user submits their email and password with `POST /api/auth/login` (JSON). The rate-limit middleware counts it in the `login` bucket (and in `api`); over the limit gets 429.
4. `auth.service.authenticate(db, email, password)`:
   - Trims and lower-cases the email, loads the user and verifies the scrypt hash (`hmac.compare_digest`).
   - **Unknown email:** it still verifies against `DUMMY_HASH`, made once with the same scrypt parameters, so the timing matches.
   - Wrong either way: 401 "Email or password is incorrect."
5. `auth.service.start_session(db, user)`:
   - Deletes this user's expired sessions.
   - Makes a token with `secrets.token_urlsafe(32)`.
   - Stores only its SHA-256 hex, with `expires_at = now() + 12 h`, and commits.
6. The router sets the cookie `session=<token>; HttpOnly; Secure; SameSite=Lax; Path=/api; Max-Age=43200` and returns 200 `{"email", "role"}`.
7. `App` keeps the user in state and renders the page for the current hash.

### Every protected request
This covers `fetch` calls, the SSE uploads and the branded PDF opened in a new tab. The browser sends the cookie itself: the origin is the same, and a new tab opened from our page is a same-site top-level GET, which `Lax` allows.
- **`require_user`** hashes the cookie value and finds an unexpired session joined to its user:
  - None: 401 `{"detail": "Not logged in."}`.
  - A database error: 503.
  - It runs before `require_ai_budget` and before any event stream, so an upload without login gets a plain 401, never a 402, and makes no AI call.
- **`require_admin`** (admin routes only) runs `require_user`, then answers 403 `{"detail": "Admins only."}` unless `role == "admin"`.

### 401 later
- Every helper in `shared/api/client.ts` dispatches the `window` event `UNAUTHORIZED_EVENT` on a 401, and `App` then shows `LoginPage`. Unsaved form input is lost (rare with 12-hour sessions).
- A PDF tab opened after the session ended shows plain JSON `{"detail": "Not logged in."}`, as other errors in that tab already do.
- **No endpoint answers 401 for anything but "not logged in"**, so a wrong *current* password on the change form never logs the user out (it gets 400).

### Logout
`POST /api/auth/logout` deletes the session row and clears the cookie (204). `App` then shows `LoginPage`.

### Change own password (`#/password`, every user)
1. The user submits the form with `POST /api/auth/password` `{current_password, new_password}`. It counts in the `login` bucket: it checks a password, so a stolen session can't use it to guess.
2. A wrong current password gets 400 "Current password is incorrect." A new password shorter than 12 characters gets 422.
3. Otherwise the hash is replaced and **every other session of this user is deleted**; the current session stays. 204.

### Admin page (`#/admin`, admins only)
The list shows **recruiters only**: the admin's own row isn't listed and can't be targeted. So the admin can't remove themself, or reset their own password through the page, and lock out the only admin.
- **Add a recruiter:** `POST /api/auth/recruiters` `{email, password}` creates a `role="recruiter"` user (201). 409 if the email exists, including the admin's.
- **Reset a password:** `PUT /api/auth/recruiters/{id}/password` `{password}` replaces the hash and deletes all that recruiter's sessions (204). 404 if the id isn't a recruiter (also for the admin's own id).
- **Remove a recruiter:** after a confirm step (the same pattern as the candidate delete), `DELETE /api/auth/recruiters/{id}` removes the user; their sessions go by `ON DELETE CASCADE` (204). 404 as above.
- Recruiters see no Admin link. If they open `#/admin` by hand, the backend answers 403 and the page shows "Admins only."

## 4. API
Everything is under `/api/auth`, in JSON. The 503 body is always `{"detail": "The database is unavailable. Try again later."}`.

| Method and path | Guard | Request | Success | Errors |
|---|---|---|---|---|
| `POST /login` | none (`login` bucket) | `LoginRequest {email: str 1–254, password: str 1–1024}` | 200 `CurrentUser {email, role}` + Set-Cookie | 401 "Email or password is incorrect." · 422 · 429 · 503 |
| `POST /logout` | none | none | 204; deletes the session if any, always clears the cookie | 503 (the cookie is still cleared) |
| `GET /me` | `require_user` | none | 200 `CurrentUser` | 401 · 503 |
| `POST /password` | `require_user` (`login` bucket) | `PasswordChange {current_password: str 1–1024, new_password: str 12–1024}` | 204 | 400 "Current password is incorrect." · 401 · 422 · 429 · 503 |
| `GET /recruiters` | `require_admin` | none | 200 `RecruiterList {items: [Recruiter {id, email, created_at}]}`, sorted by email; no paging (a handful of recruiters) | 401 · 403 · 503 |
| `POST /recruiters` | `require_admin` | `NewRecruiter {email: str, password: str 12–1024}` | 201 `Recruiter` | 401 · 403 · 409 "An account with this email already exists." · 422 · 503 |
| `PUT /recruiters/{recruiter_id}/password` | `require_admin` | `PasswordReset {password: str 12–1024}` | 204 | 401 · 403 · 404 "Recruiter not found." · 422 · 503 |
| `DELETE /recruiters/{recruiter_id}` | `require_admin` | none | 204 | 401 · 403 · 404 · 503 |

- **`email` on `NewRecruiter`:**
  - Trimmed and lower-cased, then checked against `^[^@\s]+@[^@\s]+$` and 3–254 characters; anything else is a 422.
  - No `email-validator`: it isn't pinned, and a typo only hurts the admin, who can remove the account and add it again.
  - The same normalisation applies at login and to `ADMIN_EMAIL`.
- **`recruiter_id`** is `Path(ge=1, le=MAX_ID)` (2,147,483,647), as in `candidates`, so an out-of-range id is a 422, not a false 503 (learning 2026-10-22).
- **A 409 on a race** (two adds at once) comes from the unique constraint's `IntegrityError`, caught before the generic `SQLAlchemyError` → 503.
- **Login-only fields:** at login the lengths are 1–1024 and the email is not format-checked: a bad email is simply a wrong login.

**Which routes need login** (the HTTP test in section 9 enforces it):

| Route | Guard |
|---|---|
| `/api/health` | none |
| `/api/public/*` | `X-API-Key` only (unchanged) |
| `/api/auth/login`, `/api/auth/logout` | none |
| `/api/auth/me`, `/api/auth/password` | `require_user` |
| `/api/auth/recruiters…` | `require_admin` |
| `/api/resume_intake…`, `/api/resume_generation/{id}`, `/api/ai_usage`, `/api/candidates…` | `require_user` |
| `/`, `/docs`, `/redoc`, `/openapi.json` | none (unchanged) |

**Wiring:**
- In `app/main.py`, `app.include_router(<router>, prefix="/api", dependencies=[Depends(require_user)])` for `resume_intake`, `resume_generation`, `ai_usage` and `candidates`.
- In the `auth` router, guards are set per route.
- FastAPI runs include-time dependencies first, so a missing login gets 401 before the route's own 402.

## 5. Data
One migration. The session model is `UserSession` (not `Session`, which clashes with SQLAlchemy's class).

### `users` (model `User`)
| Column | Type | Constraints |
|---|---|---|
| `id` | integer `Identity()` | PK |
| `created_at` | `timestamptz` | not null, `server_default now()` |
| `email` | `String(254)` | not null, **unique** (stored trimmed and lower-cased) |
| `password_hash` | `Text` | not null; `scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>` (verifying needs the parameters) |
| `role` | `String(9)` | not null, `CHECK (role IN ('admin', 'recruiter'))` |

The CHECK is in the database (unlike `candidates.source`, which is validated only in code) because an unexpected role would be an authorisation bug.

### `user_sessions` (model `UserSession`)
| Column | Type | Constraints |
|---|---|---|
| `id` | integer `Identity()` | PK |
| `user_id` | integer | not null, FK `users.id` **`ON DELETE CASCADE`**, indexed |
| `token_hash` | `String(64)` | not null, **unique** (the SHA-256 hex; its index serves the per-request lookup) |
| `created_at` | `timestamptz` | not null, `server_default now()` |
| `expires_at` | `timestamptz` | not null |

### Migration and settings
- **Migration:**
  - Import both models in `alembic/env.py`.
  - Run `alembic revision --autogenerate -m "create users and user_sessions tables"`; check that the CHECK constraint and `ondelete="CASCADE"` are in it (autogenerate needs the constraint declared on the model).
  - Upgrade the **local** Postgres first.
  - **Aiven** is upgraded by the main session with the user's go-ahead (rollout step 1). Aiven is already at `3aee3f10cd78` (candidates), so this migration is the only one pending there. The Docker `CMD` also runs `alembic upgrade head` on every start.
- **`Settings`** (`core/config.py`) gets `admin_email: str | None = None` and `admin_password: SecretStr | None = None`, both optional (the rules in section 3). They are added to `backend/.env.example` with a comment.
- **Rollout:**
  1. Migrate Aiven (with the user's go-ahead).
  2. The user sets `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `backend/.env` and on Render. The admin is created at the first start that has them: the local `fastapi dev` against Aiven, or Render's first start, whichever comes first.
  3. Push to Render.
- **No other new env var or secret:** sessions are rows, so there is no signing key.

## 6. Slice layout

### Backend `backend/app/features/auth/`
| File | What it does |
|---|---|
| `__init__.py` | empty |
| `models.py` | `User`, `UserSession` |
| `schemas.py` | `LoginRequest`, `CurrentUser`, `PasswordChange`, `NewRecruiter`, `PasswordReset`, `Recruiter`, `RecruiterList`; `MIN_PASSWORD_LENGTH = 12`, `MAX_PASSWORD_LENGTH = 1024` |
| `service.py` | No HTTP. Contents below the table. |
| `router.py` | `SESSION_COOKIE = "session"`; the dependencies `require_user` (cookie read with `APIKeyCookie(name="session", auto_error=False)`) and `require_admin`; the 8 endpoints; setting and clearing the cookie. The guards live here, like `public_api.require_api_key`, because they are HTTP-only; `main.py` imports `require_user`. |
| `README.md` | Purpose, API, data, bootstrap env vars, tests. |

`service.py` holds:
- **Password functions:**
  - `hash_password` (scrypt `n=2**15, r=8, p=3, maxmem=64 MiB, dklen=32`, 16-byte salt).
  - `verify_password` (a malformed stored string → False, never an exception).
  - `DUMMY_HASH`.
- **Sessions:** `authenticate`, `start_session`, `find_session_user`, `end_session`.
- **Own password:** `change_password(db, user, current, new, keep_token)`, which raises `WrongPasswordError`.
- **Admin:**
  - `list_recruiters`.
  - `add_recruiter`, which raises `EmailTakenError`.
  - `reset_recruiter_password` and `remove_recruiter`, which return False when the id isn't a recruiter.
- **Bootstrap:** `ensure_admin(db)`.
- **Helper:** `normalize_email`.
- **Constant:** `SESSION_HOURS = 12`.

### Backend, changed
| File | Change |
|---|---|
| `app/main.py` | A `lifespan` that runs `ensure_admin`; mount the `auth` router; `dependencies=[Depends(require_user)]` on the four data routers. |
| `app/core/config.py`, `backend/.env.example` | `ADMIN_EMAIL`, `ADMIN_PASSWORD`. |
| `app/core/rate_limit.py` | A bucket `"login": (LOGIN_LIMIT=10, LOGIN_WINDOW_SECONDS=900)` for `POST /api/auth/login` and `POST /api/auth/password` (`LOGIN_PATHS`), counted on top of `api`. The docstring states the Render shared-IP limit. |
| `alembic/env.py` | Import the models. |
| `tests/conftest.py` | The fixtures in section 9. |

### Frontend `frontend/src/features/auth/`
| File | What it does |
|---|---|
| `api.ts` | Types `CurrentUser`, `Recruiter`; `login`, `logout`, `getCurrentUser`, `changePassword`, `listRecruiters`, `addRecruiter`, `resetRecruiterPassword`, `removeRecruiter`, all through `shared/api/client.ts`. |
| `LoginPage.tsx` | Email and password (`autocomplete` `username` / `current-password`), a busy state, the backend's `detail` for 401 and 429, a network-error message; calls `onLoggedIn(user)`. |
| `ChangePasswordForm.tsx` | Current, new and repeat-new (`new-password`). The repeat is checked in the browser; 12 characters minimum as `minLength`. Shows "Password changed." on 204 and the 400/422/429 details. |
| `AdminPage.tsx` | A recruiter table (email, added on) with "Reset password" (an inline password field + Save) and "Remove" (confirm) per row, plus an "Add recruiter" form (email, password). Shows the 409/404/422 details, and "Admins only." on 403. |
| `LogoutButton.tsx` | Calls `logout()`, then `onLoggedOut()` (also on failure; a stale cookie only yields 401). |
| `*.test.tsx` | One per component, colocated. |
| `README.md` | Purpose, pages, tests. |

### Frontend, changed
| File | Change |
|---|---|
| `src/shared/api/client.ts` | Export `UNAUTHORIZED_EVENT = 'resumeai:unauthorized'`; `apiGet` and `apiPostEventStream` dispatch it on 401. A new `apiRequest<T>(method: 'POST' \| 'PUT' \| 'DELETE', path, jsonBody?)` sends a JSON body when given and resolves to the parsed JSON (or `undefined` on 204), with the same error and 401 handling. It is shared because `auth` and `candidates` both use it. |
| `src/features/candidates/api.ts` | `deleteCandidate` uses `apiRequest('DELETE', …)` instead of its own `fetch`, so its 401 reaches the login page. |
| `src/app/App.tsx` | User state (`'loading' \| CurrentUser \| null`); `getCurrentUser()` on mount; listens for `UNAUTHORIZED_EVENT`; `null` → the header without nav, and `LoginPage`. Logged in → today's nav plus "Change password" (`#/password`), "Admin" (`#/admin`, admins only) and `LogoutButton`. A recruiter on `#/admin` still gets `AdminPage`, which shows the backend's 403. |
| `src/app/App.test.tsx` (new), `src/shared/api/client.test.ts` (new) | Section 9. |
| `src/app/app.css` | Only if the forms need layout beyond the existing card, field and button classes. |

## 7. Cross-feature contracts
- **`auth`** uses only `app.core` (`database`, `config`, `rate_limit`). No other feature calls it, except that `main.py` attaches `auth.router.require_user` to the four data routers, whose code doesn't change.
- **Frontend:** `App` composes `auth`'s components and `api.ts`. `candidates/api.ts` only switches to the shared `apiRequest`.
- **`public_api`** is untouched and still works without a cookie.

## 8. Failure modes and security
- **Invalid input:** 422 for types, lengths and the email shape. Passwords are capped at 1024 characters.
- **Brute force:**
  - The `login` bucket allows 10 attempts per 15 minutes per client IP (login and password change), on top of 120/min.
  - **Accepted limit:** on Render the app sees only Render's proxy IP, so the bucket is **shared by everyone**. An attacker gets about 960 guesses a day in total, and while they keep trying, real users can't log in (users already logged in keep working).
  - Trusting the real client IP is the **next step**, separate from this feature. It needs a live look at the headers Render actually sends; `X-Forwarded-For`'s leftmost entry can be faked.
  - The 12-character minimum plus scrypt keeps both online and offline guessing slow.
- **User enumeration:** the same message, status and timing (via `DUMMY_HASH`) for an unknown email and a wrong password. The admin's 409 does reveal that an email exists, but only to the admin.
- **Privilege:**
  - Admin routes check the role on the server; the hidden link is only cosmetic.
  - Admin routes act on `role = 'recruiter'` rows only, so the admin can't be removed or reset through them, and no route creates an admin or changes a role.
  - **The only ways to an admin account are `ADMIN_*` at startup or the database itself.** `ADMIN_*` live in `backend/.env` and in Render's env settings, so anyone with access to those can create the admin; that matches who can already change the deploy.
- **Sessions:**
  - The cookie is `HttpOnly`, `Secure` and `Path=/api`.
  - The database stores only the token's SHA-256, so a database read leak can't replay a session.
  - Every login gets a new token (no session fixation).
  - Logout, a reset, a password change (other sessions) and a removal all delete rows on the server, so they take effect on the next request.
- **CSRF:**
  - `SameSite=Lax`: the browser doesn't send the cookie on a cross-site POST, PUT or DELETE, and no GET changes state.
  - The origin is the same, so there are no CORS headers and other sites can't read responses.
  - `onrender.com` is on the Public Suffix List, so other `*.onrender.com` apps count as cross-site.
  - **If the app moves to a custom domain**, its sibling subdomains (e.g. `blog.example.com`) become *same-site*, and `Lax` no longer separates them. CSRF needs a fresh look then (an `Origin` check on writes, or `SameSite=Strict`).
- **AI errors and timeouts:** unchanged. Login is checked before any AI call, so an anonymous upload costs nothing.
- **Large or malformed files:** unchanged (10 MB, 413). Known limit: FastAPI reads a multipart body before it runs dependencies, so an anonymous upload is received before its 401; the rate limit bounds this.
- **Personal data:**
  - Every CV-bearing route needs login, and the section 9 test enforces it.
  - New PII: recruiter emails (shown only to the admin) and password hashes.
  - Never logged: passwords (including `ADMIN_PASSWORD`, which is a `SecretStr`), tokens and hashes; the engine has `hide_parameters=True`.
  - **Still not covered by login (accepted):** a presigned original-CV link that was already handed out stays valid for its hour, and branded PDFs in the public bucket stay reachable by their unguessable URL (listing them is blocked).
- **Database down:** guards answer 503; bootstrap logs the error and the app starts anyway; a `/me` failure on load shows the login page, whose submit then shows the 503 message.
- **No admin:** with `ADMIN_*` unset and no admin row, nobody can add recruiters. It fails closed.

## 9. Testing
No live calls (no OpenAI, no Neon, no Aiven). The scrypt work is real; at about 64 MiB and well under a second per hash it is fine in tests, but use few hashes per test.

**Fixtures (`tests/conftest.py`):**
- **Fake recruiter (autouse):** overrides `require_user` with a fake **recruiter** `User` (synthetic email), so the existing ~400 tests pass unchanged.
- **`real_auth`:** removes that override.
- **No bootstrap in tests (autouse):** sets `settings.admin_email` and `settings.admin_password` to `None`. **Without it, every `TestClient(app)` lifespan would insert the `.env` admin into the real database (Aiven).** Tests that write take `db_session`.
- **Admin cookie:** helpers log in a test admin or recruiter that the test inserted under `db_session`.
- **HTTPS base URL:** TestClient uses `base_url="https://testserver"`, or httpx won't send the `Secure` cookie back.

**API (`backend/tests/features/auth/`):**
- **`test_service.py`:**
  - Hash and verify round trip; a wrong password; salts differ.
  - A malformed stored hash → False.
  - The stored string carries `n=32768, r=8, p=3`.
  - An unknown email still runs one verify.
  - Expired sessions are pruned at login.
  - `change_password` keeps only the current session.
  - Reset and remove refuse the admin's id.
  - **`ensure_admin`:**
    - Creates when missing.
    - Leaves an existing admin's hash untouched when `ADMIN_PASSWORD` changes.
    - Doesn't promote an existing recruiter (and warns).
    - Does nothing when unset, and warns when only one variable is set.
    - Raises on a password under 12 characters.
    - Logs and continues on `SQLAlchemyError`.
    - A second call is a no-op.
- **`test_router.py`:**
  - **Login:**
    - 200 with the cookie's `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/api` and `Max-Age=43200`; the raw token isn't in the database.
    - The same 401 body for an unknown email and a wrong password.
    - 422s; the 11th attempt → 429 with `Retry-After`; the password change shares that bucket.
  - **`/me`:** 200; 401 with no, unknown or expired cookie.
  - **Logout:** 204; the old cookie then gets 401; without a cookie, still 204.
  - **Password change:**
    - A wrong current password → 400 (not 401).
    - A short new password → 422.
    - Success → 204, the old password fails, the new one works, the other session gets 401 and this one doesn't.
  - **Admin:**
    - Add → 201; a duplicate (including the admin's email, with different case or spaces) → 409; a bad email → 422.
    - The list excludes the admin.
    - Reset → the recruiter's session gets 401 and the new password works.
    - Remove → the sessions are gone and login fails.
    - The admin's id and an unknown id → 404; an id over `MAX_ID` → 422.
  - **Route guard, one HTTP test driven by `app.openapi()["paths"]`:**
    - For every path and method under `/api`, path parameters are filled with `1` and no cookie and no body are sent.
    - Allow-list:
      - `/api/health` → 200.
      - `/api/public/*` → 401 with the `X-API-Key` detail ("Invalid or missing API key.").
      - `/api/auth/login` and `/api/auth/logout` → not 401.
    - **Every other path and method → 401 "Not logged in."**
    - With a **recruiter** cookie, every `/api/auth/recruiters…` path and method → 403.
    - The new admin routes are included automatically because the test reads the schema.
  - **Upload without login:** 401 with `fake_openai.calls` empty, and 401 still wins when the spending cap is reached.

**UI (Vitest, mocked `fetch`):**
- **`LoginPage`:** JSON body; 401 and 429 details; network error; busy state; labels.
- **`ChangePasswordForm`:** a repeat mismatch is blocked in the browser; the 400 detail shows and does **not** fire the unauthorized event; success message.
- **`AdminPage`:**
  - List.
  - Add → the row appears; the 409 detail.
  - Reset → success.
  - Remove asks for confirmation, then the row goes.
  - 403 → "Admins only."
- **`App`:**
  - `/me` 401 → login.
  - A recruiter sees no Admin link; an admin does.
  - The unauthorized event → login.
  - Logout → login.
  - After login, the page for the hash shows.
- **`client.test.ts`:** each helper fires the event on 401 only; `apiRequest` handles 204 and the PUT and DELETE methods.

**End-to-end:** not touched while UI testing is paused. **Known gap:** the specs that call the real backend will fail with 401 until they log in. When UI testing resumes, a test account for e2e is the user's call (the backend there uses Aiven).

**Tester's manual API check (no live AI):** a throwaway backend on :8001 against the **local** database, with `ADMIN_EMAIL` and `ADMIN_PASSWORD` set for that process only. Check:
- The admin is created once, and a restart with a different password leaves it unchanged.
- The cookie attributes in a real response.
- A 429 after 10 tries.
- Add, reset and remove.
- A cross-site form POST from another origin arrives without the cookie (401).

## 10. Decisions
1. **Server-side sessions in Postgres, not a signed stateless cookie or JWT.** These give real logout, and resets and removals that end sessions at once. They need no signing secret and no package. The cost is one indexed lookup per request.
2. **A cookie, not a bearer token.** A token can't ride on the PDF link opened in a new tab, would need every `fetch` changed, and is readable by XSS.
3. **`SameSite=Lax`, no CSRF token.** The origin is the same and no GET changes state. `Strict` would block a PDF link opened from another site for no gain. To revisit if a custom domain arrives (section 8).
4. **Guards via `include_router(dependencies=…)` and per-route `Depends`, not a middleware.** A middleware would bypass the dependency overrides that tests use. The OpenAPI-driven HTTP test gives the default-deny guarantee instead.
5. **Stdlib scrypt `n=2**15, r=8, p=3`** (OWASP-equivalent at 32 MiB per lane) instead of argon2 or bcrypt (new packages) or `n=2**17` (128 MiB at once on a small Render instance).
6. **The bootstrap admin from env, created only if missing and never overwritten.** Startup can't silently change a password the admin has since changed in the app, and a leaked old `.env` value stops working once the admin changes it. The simpler alternative, overwriting on every start, would make the env the only place to change the admin's password and would undo in-app changes.
7. **Admin routes act on recruiters only.** One rule covers "the admin can't remove or lock out themself" and "no promote or demote" with no special cases.
8. **A wrong current password is 400, not 401,** because 401 means "go to the login page" in the client.
9. **The password change shares the login rate-limit bucket,** so a stolen session can't be used to guess the password faster than the login form allows.
10. **The role CHECK is in the database,** because a bad role value is an authorisation bug, not just bad data.
11. **One `auth` slice for accounts, sessions and admin:** they share one table and its rules.

The Decisions row in `docs/architecture.md` is updated to match (approved and built 2026-11-03).

**Docs to update when built:**
- `backend/app/features/auth/README.md` and `frontend/src/features/auth/README.md`.
- The candidates frontend README (the delete goes through `apiRequest`).
- `docs/architecture.md`: what the system does, a "login check" step in the request flow, the lifespan bootstrap, and the Decisions row moving from proposed to built.
- `CLAUDE.md` and `README.md`: `ADMIN_EMAIL` and `ADMIN_PASSWORD`; e2e needs login once resumed.
- The `core/rate_limit.py` docstring.
- `.claude/learning.md`.

## 11. New question (doesn't block approval)
1. **If the admin forgets their password, how do they get back in?** No route can reset the admin, and `ADMIN_PASSWORD` is never re-applied. Options:
   - (a) The main session, with the user's go-ahead, deletes the admin's row in the database and restarts the app; `ensure_admin` then recreates it from `ADMIN_*`. Recruiters are untouched, and the admin's sessions go by cascade.
   - (b) An env flag, e.g. `ADMIN_RESET=1`, that makes startup overwrite the admin's password once. This adds a setting and a footgun if it's left on.
   - (c) Overwrite on every start whenever `ADMIN_PASSWORD` differs. This undoes in-app changes (decision 6).

   **Recommend (a):** it is rare, needs no code, and gets documented in the auth README.
