"""FastAPI application entry point. Every feature router is mounted under /api.

In the deployed Docker image the built frontend sits in backend/static and is served from /,
so the app and the API share one origin. In local dev that folder doesn't exist and Vite
serves the frontend instead.

Every data router needs a logged-in user (auth.require_user); /api/health, /api/public (its own
X-API-Key) and the auth router's own routes set their guards themselves. At startup the
lifespan creates the bootstrap admin from ADMIN_EMAIL and ADMIN_PASSWORD if it is missing.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from app.core.database import SessionLocal
from app.core.rate_limit import RateLimitMiddleware
from app.features.ai_usage.router import router as ai_usage_router
from app.features.auth.router import require_user
from app.features.auth.router import router as auth_router
from app.features.auth.service import ensure_admin
from app.features.candidates.router import router as candidates_router
from app.features.health.router import router as health_router
from app.features.public_api.router import router as public_api_router
from app.features.resume_generation.router import router as resume_generation_router
from app.features.resume_intake.router import router as resume_intake_router

# Largest request body accepted (a CV upload plus its form fields); bigger requests get 413.
MAX_REQUEST_BYTES = 10 * 1024 * 1024
# The frontend's `npm run build` output, copied here by the Dockerfile.
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Create the bootstrap admin once at startup (no database work when ADMIN_* are unset)."""
    with SessionLocal() as db:
        ensure_admin(db)
    yield


app = FastAPI(title="ResumeAI API", lifespan=lifespan)
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BYTES)
# Added last, so it runs first: a request over the rate limit is refused before its body is read.
app.add_middleware(RateLimitMiddleware)

# Open, or guarded per route: health, the public API (X-API-Key) and auth itself.
app.include_router(health_router, prefix="/api")
app.include_router(public_api_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
# Login required. Include-time dependencies run before the routes' own (e.g. the spending cap).
LOGIN_REQUIRED = [Depends(require_user)]
app.include_router(resume_intake_router, prefix="/api", dependencies=LOGIN_REQUIRED)
app.include_router(resume_generation_router, prefix="/api", dependencies=LOGIN_REQUIRED)
app.include_router(ai_usage_router, prefix="/api", dependencies=LOGIN_REQUIRED)
app.include_router(candidates_router, prefix="/api", dependencies=LOGIN_REQUIRED)

# Mounted last, so every /api route above wins. html=True serves index.html at /; the app's
# pages are hash addresses (#/history), so no other fallback is needed.
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
