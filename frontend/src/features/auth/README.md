# auth (frontend)

**Purpose:** the login page, logging out, changing your own password, and the admin page. Design: [docs/features/auth.md](../../../../docs/features/auth.md); backend: [backend/app/features/auth/README.md](../../../../backend/app/features/auth/README.md).

## How the app uses it
`src/app/App.tsx` calls `getCurrentUser()` (`GET /api/auth/me`) on load. While nobody is logged in (that call fails, or any later call answers 401), the header shows no navigation and `LoginPage` replaces the page; the hash address is kept, so after login the user lands where they meant to go. Every helper in `shared/api/client.ts` dispatches `UNAUTHORIZED_EVENT` on a 401, and `App` listens for it. Unsaved form input is lost then (rare with 12-hour sessions). Logged in, the top bar's **Account** menu (the user's initial) holds their email, **Change password** (`#/password`), **Admin** (`#/admin`, admins only) and **Log out**. The session is an `HttpOnly` cookie the browser sends itself, including on the branded PDF opened in a new tab; no code reads it.

## Pages and components
- **`LoginPage`**: email (`autocomplete="username"`) and password (`current-password`), **Log in** (disabled, "Logging in…", while waiting). Shows the backend's message for 401 ("Email or password is incorrect."), 429 and 503, and "Could not reach the server…" on a network failure.
- **`ChangePasswordForm`** (`#/password`): current, new and repeat-new (`new-password`, `minLength` 12). A mismatched repeat is caught in the browser, without a request. On 204: "Password changed." and the form clears (the user's other sessions end). 400 "Current password is incorrect." and 429 show the backend's message, 422 shows "The new password needs at least 12 characters."; none of them logs the user out.
- **`AdminPage`** (`#/admin`): the recruiters (email, added on), sorted by email, each with **Reset password** (an inline new-password field, **Save** / **Cancel**) and **Remove** (`window.confirm` first), plus an **Add recruiter** form (email, password). Success shows a notice ("Added …", "Password reset for …", "Removed …"); errors show the backend's detail (409, 404), "Enter a valid email and a password of at least 12 characters." for 422, or the network message. The admin's own account isn't listed. A recruiter who opens `#/admin` by hand gets the backend's 403, shown as "Admins only."
- **`LogoutButton`**: `POST /api/auth/logout`, then `onLoggedOut()`, also when the request fails.

## Files
- `api.ts`: types `CurrentUser`, `Recruiter`; `MIN_PASSWORD_LENGTH`; `login`, `logout`, `getCurrentUser`, `changePassword`, `listRecruiters`, `addRecruiter`, `resetRecruiterPassword`, `removeRecruiter`, all through `shared/api/client.ts` (`apiGet`, `apiRequest`).
- `LoginPage.tsx`, `ChangePasswordForm.tsx`, `AdminPage.tsx`, `LogoutButton.tsx`, `auth.css` (form width, the notice and the recruiter table). The Account menu's styles are in `src/app/app.css`.

## Tests
Vitest, colocated, mocked `fetch` (Playwright stays paused):
- `LoginPage.test.tsx`: labels and autocomplete; the JSON body and `onLoggedIn`; the busy state; 401 and 429 messages; network error.
- `ChangePasswordForm.test.tsx`: `minLength`; a mismatch is blocked without a request; success; 400 shows and does **not** fire the unauthorized event; 422 and 429.
- `AdminPage.test.tsx`: the list; add (row appears in order) and 409; reset and 404; remove with confirm, and a cancelled confirm; 403 → "Admins only."
- `LogoutButton.test.tsx`: logs out, and still calls back on failure.
- Also `src/app/App.test.tsx` (login page and no nav on 401; Admin link for admins only; the unauthorized event; logout; the hash's page after login) and `src/shared/api/client.test.ts` (each helper fires the event on 401 only; `apiRequest` JSON, 204, PUT and DELETE).

**End-to-end:** not updated while UI testing is paused. The specs that use the real backend will get 401 until they log in; an e2e test account is the user's call when UI testing resumes.
