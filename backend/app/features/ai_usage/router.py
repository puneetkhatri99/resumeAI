"""AI usage endpoint: the OpenAI calls the app made and what they cost, for the usage panel."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.ai_usage.schemas import AiUsageReport
from app.features.ai_usage.service import UnknownTimeZoneError, usage_report
from app.features.resume_intake.service import list_candidate_names

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai_usage", tags=["ai_usage"])


@router.get("", response_model=AiUsageReport)
def read_ai_usage(
    tz: Annotated[str, Query(min_length=1, max_length=64)],
    db: Annotated[Session, Depends(get_db)],
) -> AiUsageReport:
    """Usage and cost, with days and "this month" in the browser's IANA time zone `tz`.

    Each recent call carries the candidate's name when it is linked to a stored intake.
    422 for a time zone Postgres doesn't know; 503 if the database is unavailable.
    """
    try:
        report = usage_report(db, tz)
        intake_ids = {call.resume_intake_id for call in report.recent_calls}
        names = list_candidate_names(db, [i for i in intake_ids if i is not None])
    except UnknownTimeZoneError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except SQLAlchemyError:
        logger.exception("Could not read the AI usage: database error")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The database is unavailable. Try again later."
        ) from None
    for call in report.recent_calls:
        if call.resume_intake_id is not None:
            call.candidate_name = names.get(call.resume_intake_id)
    return report
