"""FastAPI application entry point. Every feature router is mounted under /api."""

from fastapi import FastAPI
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from app.features.health.router import router as health_router
from app.features.resume_generation.router import router as resume_generation_router
from app.features.resume_intake.router import router as resume_intake_router

# Largest request body accepted (a CV upload plus its form fields); bigger requests get 413.
MAX_REQUEST_BYTES = 10 * 1024 * 1024

app = FastAPI(title="ResumeAI API")
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BYTES)

app.include_router(health_router, prefix="/api")
app.include_router(resume_intake_router, prefix="/api")
app.include_router(resume_generation_router, prefix="/api")
