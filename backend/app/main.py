"""FastAPI application entry point. Every feature router is mounted under /api.

In the deployed Docker image the built frontend sits in backend/static and is served from /,
so the app and the API share one origin. In local dev that folder doesn't exist and Vite
serves the frontend instead.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from app.core.rate_limit import RateLimitMiddleware
from app.features.ai_usage.router import router as ai_usage_router
from app.features.health.router import router as health_router
from app.features.resume_generation.router import router as resume_generation_router
from app.features.resume_intake.router import router as resume_intake_router

# Largest request body accepted (a CV upload plus its form fields); bigger requests get 413.
MAX_REQUEST_BYTES = 10 * 1024 * 1024
# The frontend's `npm run build` output, copied here by the Dockerfile.
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

app = FastAPI(title="ResumeAI API")
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BYTES)
# Added last, so it runs first: a request over the rate limit is refused before its body is read.
app.add_middleware(RateLimitMiddleware)

app.include_router(health_router, prefix="/api")
app.include_router(resume_intake_router, prefix="/api")
app.include_router(resume_generation_router, prefix="/api")
app.include_router(ai_usage_router, prefix="/api")

# Mounted last, so every /api route above wins. html=True serves index.html at /; the app's
# pages are hash addresses (#/history), so no other fallback is needed.
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
