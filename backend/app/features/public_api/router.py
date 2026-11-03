"""Public API for outside systems (e.g. Loxo), behind the X-API-Key header:
POST a resume under the caller's own candidate and resume ids and get the AI-extracted details
back as JSON; GET the branded MinistryHub PDF for a stored pair; or POST a resume and get its
branded PDF's permanent public URL and its details (it is also saved as a candidate, source
"api").
"""

import logging
import secrets
from datetime import date
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Path,
    Response,
    Security,
    UploadFile,
    status,
)
from fastapi.security import APIKeyHeader
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.features.ai_usage.service import (
    AiCall,
    require_ai_budget,
    save_ai_calls_without_intake,
)
from app.features.candidates.service import (
    FileStorageError,
    pdf_content_disposition,
    public_url,
    store_candidate,
)
from app.features.public_api.schemas import PublicId, PublicPdfResponse, PublicResumeResponse
from app.features.public_api.service import get_public_resume, save_public_resume
from app.features.resume_generation.service import PdfRenderError, build_resume_html, render_pdf
from app.features.resume_intake.service import (
    CV_ERRORS,
    cv_error_status,
    extract_text,
    structure_resume,
)
from app.features.resume_structuring.service import ResumeData

logger = logging.getLogger(__name__)

DATABASE_DOWN_MESSAGE = "The database is unavailable. Try again later."
INVALID_KEY_MESSAGE = "Invalid or missing API key."
# The one template that exists today: the MinistryHub branded resume.
MINISTRYHUB_TEMPLATE_ID = 1

# auto_error=False: a missing header is our own 401, not FastAPI's 403. Security() also shows
# the key in /docs' "Authorize" dialog.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: Annotated[str | None, Security(api_key_header)]) -> None:
    """Refuse the request with 401 unless X-API-Key matches PUBLIC_API_KEY.

    Also 401 when PUBLIC_API_KEY isn't set (or is empty), so the public API is closed until
    it is configured. Compared in constant time.
    """
    expected = settings.public_api_key.get_secret_value() if settings.public_api_key else ""
    if not expected or not api_key or not secrets.compare_digest(api_key, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            INVALID_KEY_MESSAGE,
            headers={"WWW-Authenticate": "ApiKey"},
        )


router = APIRouter(prefix="/public", tags=["public_api"], dependencies=[Depends(require_api_key)])


@router.post(
    "/resumes", response_model=PublicResumeResponse, dependencies=[Depends(require_ai_budget)]
)
def create_public_resume(
    file: UploadFile,
    candidate_id: Annotated[PublicId, Form()],
    resume_id: Annotated[PublicId, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> PublicResumeResponse:
    """Read the CV, have AI extract its details, store them under the pair and return them (200).

    A repeat pair replaces the stored details. 401 bad key; 402 spending cap; 415 not a PDF,
    DOCX, DOC or TXT; 422 invalid form or unusable CV text (no name or email, too long, over
    4 pages); 502 an AI call failed; 503 database unavailable. On any error nothing is stored,
    but the AI calls OpenAI answered (and billed) are stored unlinked.
    """
    # Every AI call OpenAI answers, collected for the usage panel and the spending cap.
    ai_calls: list[AiCall] = []
    try:
        data = file.file.read()
        file_type, text, read_by_ocr = extract_text(data, ai_calls)
        _, structured_data = structure_resume(data, file_type, text, read_by_ocr, ai_calls)
        save_public_resume(db, candidate_id, resume_id, structured_data, ai_calls)
    except CV_ERRORS as exc:
        status_code, detail = cv_error_status(exc), str(exc)
    except SQLAlchemyError:
        # hide_parameters on the engine keeps the CV data out of this log.
        logger.exception("Could not store the public resume: database error")
        status_code, detail = status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE
    else:
        return PublicResumeResponse(
            candidate_id=candidate_id, resume_id=resume_id, data=structured_data
        )
    save_ai_calls_without_intake(db, ai_calls)
    raise HTTPException(status_code, detail)


# Must stay a plain def: Playwright's sync API fails inside an async def (running event loop).
@router.get("/resumes/{candidate_id}/{resume_id}/pdf")
def read_public_resume_pdf(
    candidate_id: Annotated[PublicId, Path()],
    resume_id: Annotated[PublicId, Path()],
    db: Annotated[Session, Depends(get_db)],
    template_id: int = MINISTRYHUB_TEMPLATE_ID,
) -> Response:
    """Return the pair's details as the branded PDF for `template_id` (only 1, MinistryHub).

    200 inline `application/pdf`; 401 bad key; 404 unknown template or pair; 500 stored details
    unreadable or Chromium failed; 503 database unavailable.
    """
    if template_id != MINISTRYHUB_TEMPLATE_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    try:
        row = get_public_resume(db, candidate_id, resume_id)
    except SQLAlchemyError:
        logger.exception("Could not read the public resume: database error")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE) from None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")

    try:
        data = ResumeData.model_validate(row.structured_data)
    except ValidationError:
        # The error's message quotes the stored values (CV data): log the row id only, never chain.
        logger.error("Stored structured_data for public resume %s doesn't match ResumeData", row.id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "The stored CV details can't be read."
        ) from None

    try:
        pdf = render_pdf(build_resume_html(data, date.today()))
    except PdfRenderError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from None

    return pdf_response(pdf, data.name)


# Must stay a plain def: Playwright's sync API fails inside an async def (running event loop).
@router.post("/pdf", response_model=PublicPdfResponse, dependencies=[Depends(require_ai_budget)])
def create_pdf(
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    template_id: Annotated[int, Form()] = MINISTRYHUB_TEMPLATE_ID,
) -> PublicPdfResponse:
    """Read the CV, have AI extract its details, render the branded PDF for `template_id` (only
    1, MinistryHub), save it as a candidate (source "api") with both files stored, and return
    JSON (200) with the PDF's permanent public URL and the extracted details.

    401 bad key; 402 spending cap; 404 unknown template (before any AI call); 415 not a PDF,
    DOCX, DOC or TXT; 422 invalid form or unusable CV text; 500 Chromium failed; 502 an AI call
    or the file storage failed (no PDF then); 503 database unavailable. On any error nothing is
    stored, but the AI calls OpenAI answered (and billed) are stored unlinked. Usually 5-30 s;
    callers should allow 10 minutes.
    """
    if template_id != MINISTRYHUB_TEMPLATE_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    # Every AI call OpenAI answers, collected for the usage panel and the candidate's cost.
    ai_calls: list[AiCall] = []
    try:
        data = file.file.read()
        file_type, text, read_by_ocr = extract_text(data, ai_calls)
        _, resume = structure_resume(data, file_type, text, read_by_ocr, ai_calls)
        pdf = render_pdf(build_resume_html(resume, date.today()))
        candidate = store_candidate(db, data, file_type, pdf, resume.name, "api", ai_calls)
        # Read inside the try: after the commit, reading the key reloads the row.
        url = public_url(candidate.resume_key)
    except CV_ERRORS as exc:
        status_code, detail = cv_error_status(exc), str(exc)
    except FileStorageError as exc:
        status_code, detail = status.HTTP_502_BAD_GATEWAY, str(exc)
    except PdfRenderError as exc:
        status_code, detail = status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)
    except SQLAlchemyError:
        # hide_parameters on the engine keeps the name out of this log.
        logger.exception("Could not store the candidate: database error")
        status_code, detail = status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE
    else:
        return PublicPdfResponse(url=url, data=resume)
    save_ai_calls_without_intake(db, ai_calls)
    raise HTTPException(status_code, detail)


def pdf_response(pdf: bytes, name: str) -> Response:
    """The PDF inline, named "MinistryHub - <name>.pdf", never cached (personal data)."""
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": pdf_content_disposition(name),
            "Cache-Control": "no-store",
        },
    )
