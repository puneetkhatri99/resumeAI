"""Resume intake endpoints: the upload receives a CV file plus the interview answers, has AI
extract the CV's details, stores everything, and streams its progress as Server-Sent Events.
Two GETs list past intakes and return one of them.
"""

import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from fastapi.sse import EventSourceResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.ai_usage.service import AiCall, save_ai_calls_without_intake
from app.features.resume_intake.schemas import (
    IntakeEvent,
    IntakeList,
    IntakeSummary,
    InterviewAnswers,
    LongAnswer,
    Rating,
    ResumeIntakeResponse,
    ShortAnswer,
    WorkMode,
)
from app.features.resume_intake.service import (
    ResumeExtractionError,
    UnsupportedFileTypeError,
    extract_text,
    get_resume_intake,
    list_resume_intakes,
    save_resume_intake,
    structure_resume,
)
from app.features.resume_structuring.service import AiExtractionError, ResumeStructuringError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/resume_intake", tags=["resume_intake"])

DATABASE_DOWN_MESSAGE = "The database is unavailable. Try again later."
PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@router.get("", response_model=IntakeList)
def list_intakes(
    db: Annotated[Session, Depends(get_db)],
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE,
) -> IntakeList:
    """Past intakes, newest first: `limit` of them, only those older than `before_id` when given.

    503 if the database is unavailable.
    """
    try:
        rows, has_more = list_resume_intakes(db, before_id, limit)
    except SQLAlchemyError:
        logger.exception("Could not list resume intakes: database error")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE) from None
    return IntakeList(items=[IntakeSummary.model_validate(row) for row in rows], has_more=has_more)


@router.get("/{intake_id}", response_model=ResumeIntakeResponse)
def read_intake(intake_id: int, db: Annotated[Session, Depends(get_db)]) -> ResumeIntakeResponse:
    """One stored intake, in the same shape as the upload's "done" result.

    404 if there is no intake with that id; 503 if the database is unavailable.
    """
    try:
        intake = get_resume_intake(db, intake_id)
    except SQLAlchemyError:
        logger.exception("Could not read a resume intake: database error")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE) from None
    if intake is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intake not found.")
    return ResumeIntakeResponse.model_validate(intake)


# Separate Form parameters: FastAPI 0.156.8 stops flattening a form model once an UploadFile
# sits next to it (it then expects one field named "answers").
# No response_model and no status_code: the Iterator[IntakeEvent] annotation makes FastAPI
# validate each event, and a status code would apply to the whole stream.
@router.post("", response_class=EventSourceResponse)
def create_intake(
    file: UploadFile,
    notice_period: Annotated[ShortAnswer, Form()],
    salary_expectation: Annotated[ShortAnswer, Form()],
    work_mode: Annotated[WorkMode, Form()],
    rating: Annotated[Rating, Form()],
    strengths: Annotated[LongAnswer, Form()],
    concerns: Annotated[LongAnswer, Form()],
    overall_notes: Annotated[LongAnswer, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> Iterator[IntakeEvent]:
    """Stream "reading", "extracting", then "done" with the stored record.

    A scanned PDF is read by OCR during "reading"; a PDF whose text has no name or email is
    OCR'd once more during "extracting" (see service.structure_resume).
    Every expected failure ends the stream with one "error" event instead: 415 if the CV isn't
    a PDF, DOCX, DOC or TXT; 422 if its text can't be used or has no name or email; 502 if an
    AI call (OCR or extraction) fails; 503 if the database is unavailable. No intake is stored
    on any error, but the AI calls OpenAI answered (and billed) are stored unlinked (ai_usage).
    Invalid answers (422) and an oversized request (413) are plain HTTP errors, sent before
    the stream starts.
    """
    answers = InterviewAnswers(
        notice_period=notice_period,
        salary_expectation=salary_expectation,
        work_mode=work_mode,
        rating=rating,
        strengths=strengths,
        concerns=concerns,
        overall_notes=overall_notes,
    )
    # Every AI call OpenAI answers, collected for the usage panel (ai_usage).
    ai_calls: list[AiCall] = []
    # An exception escaping here would cut the stream with no final event, so every expected
    # one becomes an "error" event.
    try:
        yield IntakeEvent(stage="reading")
        data = file.file.read()
        file_type, text, read_by_ocr = extract_text(data, ai_calls)
        yield IntakeEvent(stage="extracting")
        text, structured_data = structure_resume(data, file_type, text, read_by_ocr, ai_calls)
        intake = save_resume_intake(
            db, file.filename or "", file_type, text, structured_data, answers, ai_calls
        )
    except UnsupportedFileTypeError as exc:
        status_code, detail = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)
    except (ResumeExtractionError, ResumeStructuringError) as exc:
        status_code, detail = status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
    except AiExtractionError as exc:
        status_code, detail = status.HTTP_502_BAD_GATEWAY, str(exc)
    except SQLAlchemyError:
        # hide_parameters on the engine keeps the CV text and answers out of this log.
        logger.exception("Could not store the resume intake: database error")
        status_code, detail = status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE
    else:
        yield IntakeEvent(stage="done", result=ResumeIntakeResponse.model_validate(intake))
        return
    save_ai_calls_without_intake(db, ai_calls)
    yield IntakeEvent(stage="error", status=status_code, detail=detail)
