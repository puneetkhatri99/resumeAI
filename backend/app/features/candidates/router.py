"""Candidate endpoints: the add-candidate upload reads the CV, has AI extract its details, renders
the branded PDF, stores both files and the row, and streams its progress as Server-Sent Events.
A GET lists candidates with links to their files; a DELETE removes one with both files.
"""

import logging
from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, UploadFile, status
from fastapi.sse import EventSourceResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.ai_usage.service import (
    DATABASE_DOWN_MESSAGE,
    AiCall,
    require_ai_budget,
    save_ai_calls_without_intake,
)
from app.features.candidates.schemas import CandidateEvent, CandidateList, CandidateSummary
from app.features.candidates.service import (
    FileStorageError,
    delete_candidate,
    list_candidates,
    original_url,
    public_url,
    store_candidate,
)
from app.features.resume_generation.service import PdfRenderError, build_resume_html, render_pdf
from app.features.resume_intake.service import (
    ResumeExtractionError,
    UnsupportedFileTypeError,
    extract_text,
    structure_resume,
)
from app.features.resume_structuring.service import AiExtractionError, ResumeStructuringError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/candidates", tags=["candidates"])

PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
# Postgres' integer id column: a bigger id would be a DataError, wrongly reported as a 503.
MAX_ID = 2_147_483_647


# Must stay a plain def: Playwright's sync API fails inside an async def (running event loop).
# No response_model and no status_code: the Iterator[CandidateEvent] annotation makes FastAPI
# validate each event, and a status code would apply to the whole stream.
@router.post("", response_class=EventSourceResponse, dependencies=[Depends(require_ai_budget)])
def create_candidate(
    file: UploadFile, db: Annotated[Session, Depends(get_db)]
) -> Iterator[CandidateEvent]:
    """Stream "reading", "extracting", "generating", "uploading", then "done" with only the
    branded PDF's permanent public URL.

    Every expected failure ends the stream with one "error" event instead: 415 not a PDF, DOCX,
    DOC or TXT; 422 unusable CV text or no name or email; 502 an AI call failed; 500 the PDF
    couldn't be rendered; 502 the files couldn't be stored; 503 the database is unavailable.
    Nothing is stored on any error (objects already written are deleted, best effort), but the
    AI calls OpenAI answered (and billed) are stored unlinked. A missing file (422), an
    oversized request (413), the AI spending cap (402) and the rate limit (429) are plain HTTP
    errors, sent before the stream starts.
    """
    # Every AI call OpenAI answers, collected for the usage panel and the candidate's cost.
    ai_calls: list[AiCall] = []
    # An exception escaping here would cut the stream with no final event, so every expected
    # one becomes an "error" event.
    try:
        yield CandidateEvent(stage="reading")
        data = file.file.read()
        file_type, text, read_by_ocr = extract_text(data, ai_calls)
        yield CandidateEvent(stage="extracting")
        _, resume = structure_resume(data, file_type, text, read_by_ocr, ai_calls)
        yield CandidateEvent(stage="generating")
        pdf = render_pdf(build_resume_html(resume, date.today()))
        yield CandidateEvent(stage="uploading")
        candidate = store_candidate(db, data, file_type, pdf, resume.name, "manual", ai_calls)
        # Read inside the try: after the commit, reading the key reloads the row.
        new_resume_url = public_url(candidate.resume_key)
    except UnsupportedFileTypeError as exc:
        status_code, detail = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)
    except (ResumeExtractionError, ResumeStructuringError) as exc:
        status_code, detail = status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
    except (AiExtractionError, FileStorageError) as exc:
        status_code, detail = status.HTTP_502_BAD_GATEWAY, str(exc)
    except PdfRenderError as exc:
        status_code, detail = status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)
    except SQLAlchemyError:
        # hide_parameters on the engine keeps the name out of this log.
        logger.exception("Could not store the candidate: database error")
        status_code, detail = status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE
    else:
        yield CandidateEvent(stage="done", new_resume_url=new_resume_url)
        return
    save_ai_calls_without_intake(db, ai_calls)
    yield CandidateEvent(stage="error", status=status_code, detail=detail)


@router.get("", response_model=CandidateList)
def list_candidates_page(
    db: Annotated[Session, Depends(get_db)],
    before_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE,
) -> CandidateList:
    """Candidates, newest first: `limit` of them, only those older than `before_id` when given.

    Each has a presigned link to the original (1 hour) and the branded PDF's public URL.
    503 if the database is unavailable.
    """
    try:
        rows, has_more = list_candidates(db, before_id, limit)
    except SQLAlchemyError:
        logger.exception("Could not list candidates: database error")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE) from None
    items = [
        CandidateSummary(
            id=row.id,
            name=row.name,
            created_at=row.created_at,
            source=row.source,
            ai_cost_usd=row.ai_cost_usd,
            original_resume_url=original_url(row.original_key),
            new_resume_url=public_url(row.resume_key),
        )
        for row in rows
    ]
    return CandidateList(items=items, has_more=has_more)


@router.delete("/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_candidate(
    candidate_id: Annotated[int, Path(ge=1, le=MAX_ID)], db: Annotated[Session, Depends(get_db)]
) -> Response:
    """Delete the candidate's two files, then its row (204).

    404 no such candidate; 502 a file couldn't be deleted (the row is kept); 503 the database
    is unavailable (if the files were already deleted, the row stays and a retry finishes).
    """
    try:
        deleted = delete_candidate(db, candidate_id)
    except FileStorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    except SQLAlchemyError:
        logger.exception("Could not delete a candidate: database error")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE) from None
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
