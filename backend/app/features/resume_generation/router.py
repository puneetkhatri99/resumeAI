"""Branded resume endpoint: renders a stored intake's AI-extracted CV details as the MinistryHub
PDF, on demand. The PDF isn't stored.
"""

import logging
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.resume_generation.service import (
    PdfRenderError,
    build_resume_html,
    render_pdf,
)
from app.features.resume_intake.service import get_resume_intake
from app.features.resume_structuring.service import ResumeData

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/resume_generation", tags=["resume_generation"])

# resume_intakes.id is a 32-bit integer: a bigger id is a 422, not a DataError reported as a 503.
MAX_INTAKE_ID = 2_147_483_647


# Must stay a plain def: Playwright's sync API fails inside an async def (running event loop).
@router.get("/{intake_id}")
def read_branded_resume(
    intake_id: Annotated[int, Path(le=MAX_INTAKE_ID)], db: Annotated[Session, Depends(get_db)]
) -> Response:
    """Return the intake's branded resume as an inline `application/pdf` (200).

    404 when the intake doesn't exist or has no extracted details; 500 when the stored
    details don't match ResumeData or Chromium fails; 503 when the database is unavailable.
    """
    try:
        intake = get_resume_intake(db, intake_id)
    except SQLAlchemyError:
        logger.exception("Could not read the resume intake: database error")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The database is unavailable. Try again later."
        ) from None
    if intake is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intake not found.")
    if intake.structured_data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This intake has no extracted details.")

    try:
        data = ResumeData.model_validate(intake.structured_data)
    except ValidationError:
        # The error's message quotes the stored values (CV data): log the id only, never chain.
        logger.error("Stored structured_data for intake %s doesn't match ResumeData", intake_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "The stored CV details can't be read."
        ) from None

    try:
        pdf = render_pdf(build_resume_html(data, date.today()))
    except PdfRenderError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from None

    # The name is AI output: always percent-encoded (also /, \, quotes, CR and LF).
    filename = quote(f"MinistryHub - {data.name}.pdf", safe="")
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            # Personal data: the browser keeps no copy in its HTTP cache.
            "Cache-Control": "no-store",
        },
    )
