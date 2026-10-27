# Rate limiting and the AI spending cap

**Status:** built (2026-10-27). The user approved this plan on 2026-10-27, as the first step before deploying to Render. Auth and the Render setup are separate steps that come after it.

## Why
Once the API is public, anyone who can reach it can send requests and start paid OpenAI calls. These two guards limit both abuse and runaway OpenAI cost.

## The user's decisions (2026-10-27)
- **AI spending cap:** **all time**, not monthly. It is set by the env var `AI_SPEND_CAP_USD` (default **$5**). Once the recorded total reaches it, uploads get **HTTP 402**.
- **Rate limiting:** **every `/api` endpoint**, per client IP, built in-house in memory (no new package).
  - **120 requests per minute** overall.
  - **30 uploads per hour** (`POST /api/resume_intake`), counted on top of the overall limit.

## 1. AI spending cap (`ai_usage` + `resume_intake`)
- **Setting:** `ai_spend_cap_usd: Decimal = Decimal("5")` in `app/core/config.py`, read from `AI_SPEND_CAP_USD` (also listed in `backend/.env.example`). pydantic-settings rejects an undeclared `.env` key, so it is declared even though it has a default.
- **Check:** `ai_usage.service.ai_spend_cap_reached(db) -> bool` returns `COALESCE(SUM(cost_usd), 0) >= settings.ai_spend_cap_usd` over all `ai_calls`. That is the same sum as the AI usage panel's `all_time` cost.
  - Unpriced calls (`cost_usd` NULL) count as $0. This is acceptable because `test_the_model_in_use_has_a_price` fails as soon as the model in use has no price.
- **Where:** `resume_intake.router.require_ai_budget`, a FastAPI dependency on `POST /api/resume_intake` (`dependencies=[Depends(require_ai_budget)]`). Dependencies run before the event stream starts, so the refusal is a plain HTTP error and no AI call is made.

  | Code | When | Body |
  |---|---|---|
  | 402 | The cap is reached | `{"detail": "AI spending limit reached. Uploads are paused."}` |
  | 503 | The database fails during the check | `{"detail": "The database is unavailable. Try again later."}` |

  FastAPI runs the dependency before it reports form validation errors, so once the cap is reached an upload with invalid answers also gets the 402. Only the 413 (body over 10 MB) and the 429 (rate limit) come earlier, from middleware.
- **No frontend change:** the form already shows a plain HTTP error's JSON `detail` next to Submit (`shared/api/client.ts`, `InterviewForm.tsx`). The past-interviews list, the stored results, the branded PDF and the usage panel keep working when the cap is reached.
- **Raising the cap:** set a higher `AI_SPEND_CAP_USD` and restart the backend.

## 2. Rate limiting (`backend/app/core/rate_limit.py`)
It's an app-wide middleware, so it lives in `core/`.
- **`RateLimitMiddleware`** (Starlette `BaseHTTPMiddleware`, which passes the upload's streaming response through) is mounted in `app/main.py`. It is added last, so it runs first: a request over the limit is refused before its body is read.
- **Scope:** only `/api` and paths under `/api/` are counted (`/apiary` isn't).
- **Buckets:** every `/api` request counts in `api` (`API_LIMIT = 120` per `API_WINDOW_SECONDS = 60`). A `POST /api/resume_intake` also counts in `upload` (`UPLOAD_LIMIT = 30` per `UPLOAD_WINDOW_SECONDS = 3600`).
- **State:** `RateLimiter` keeps a sliding window per `(client IP, bucket)`: a `dict` of `deque[float]` request times (`time.monotonic`) behind one `threading.Lock`. The app's one instance is `rate_limiter`.
  - A request is allowed only if every one of its buckets has room. Only allowed requests are recorded, so a refused upload doesn't use up the overall limit, and a client that waits for `Retry-After` gets in.
  - **Pruning:** a key's old request times are pruned on each hit. Once a minute (`SWEEP_INTERVAL_SECONDS`), every key is pruned and empty keys are dropped, so IPs that stop sending don't stay in memory.
- **Over the limit:** `429` with `{"detail": "Too many requests. Try again in N seconds."}` and a `Retry-After: N` header. N is the whole seconds until the oldest request in the full bucket leaves its window (at least 1). When both buckets are full, the longer wait is used.
- **Client IP:** `request.client.host`. On Render this is the proxy's address unless uvicorn trusts the proxy headers; that has to be set up and verified in the deploy step (see below).
- **Tests:** `rate_limiter.reset()` clears the counts. The autouse `reset_rate_limits` fixture in `backend/tests/conftest.py` calls it before each test, so the whole pytest run (hundreds of requests from `testclient`) never reaches 120 a minute.

## Known limits (accepted)
- **The cap can be overshot:** the check runs once, when an upload starts. An upload already running can push the total past the cap by its own cost (about $0.002–0.005), and so can several uploads that start at the same moment.
- **The cap misses calls that got no answer:** like the usage panel, it only sums recorded calls; a timed-out call has no usage to record (see `ai_usage`). OpenAI's billing dashboard is the source of truth.
- **The counts live in memory:** they reset when the backend restarts, and each instance (or worker process) counts on its own. That's enough for one Render instance.
- **One IP for many people:** recruiters behind one office NAT share the limits.

## Not in this step (next, for Render)
- **Auth:** required before going public. Every CV is readable today.
- **Render setup:** the service, Postgres, the Chromium install, and the frontend build.
- **Client IP behind Render's proxy:** trust only Render's forwarded header. Uvicorn's `--forwarded-allow-ips='*'` takes the leftmost `X-Forwarded-For`, which a client can spoof, so the deploy step must pick and test the right setting.

## Tests (pytest only; no UI or Playwright, per the standing instruction)
- `backend/tests/core/test_rate_limit.py` (no database; a fake clock): the 121st `/api` request in a minute is a 429 with `Retry-After: 60`; the 31st upload in an hour is a 429 with `Retry-After: 3600` while other `/api` requests still pass; requests outside `/api` aren't counted; two IPs are counted separately; the window slides; a refused request isn't counted; a refused upload doesn't use up the overall limit; idle IPs are dropped by the sweep.
- `backend/tests/features/ai_usage/test_service.py` (Postgres, through `db_session`): below the cap is not reached; at and above it is; an unpriced call counts as $0. The cap is set relative to the total already in the dev database.
- `backend/tests/features/resume_intake/test_router.py` (Postgres, through `db_session`): an upload below the cap works; at the cap it is a plain JSON 402, OpenAI isn't called, and nothing is stored; a $0 cap from settings is a 402; the database failing during the check is a 503.
- `backend/tests/core/test_config.py`: `AI_SPEND_CAP_USD` defaults to 5 and is read exactly from the environment.

## Verified (2026-10-27)
On a throwaway backend at `127.0.0.1:8001` with `AI_SPEND_CAP_USD=0` and the OpenAI base URL pointed at itself: an upload got the 402 with the message (no OpenAI request was made); the 121st `/api` request within a minute got a 429 with `Retry-After`; `/docs` still answered 200 while `/api` was limited.
