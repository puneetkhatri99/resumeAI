"""Public API business logic: store and read the AI-extracted details of a resume under the
caller's (candidate_id, resume_id) pair.
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.features.ai_usage.service import AiCall, add_ai_calls
from app.features.public_api.models import PublicResume
from app.features.resume_structuring.service import ResumeData


def save_public_resume(
    db: Session, candidate_id: str, resume_id: str, data: ResumeData, ai_calls: list[AiCall]
) -> None:
    """Store the details under the pair, replacing what a repeat pair stored before, and the
    upload's AI calls (unlinked: ai_calls only links to resume_intakes), in one commit.
    """
    statement = insert(PublicResume).values(
        candidate_id=candidate_id, resume_id=resume_id, structured_data=data.model_dump()
    )
    statement = statement.on_conflict_do_update(
        index_elements=[PublicResume.candidate_id, PublicResume.resume_id],
        set_={"structured_data": statement.excluded.structured_data, "updated_at": func.now()},
    )
    db.execute(statement)
    add_ai_calls(db, ai_calls, None)
    db.commit()


def get_public_resume(db: Session, candidate_id: str, resume_id: str) -> PublicResume | None:
    """The stored row for the pair, or None."""
    return db.scalar(
        select(PublicResume).where(
            PublicResume.candidate_id == candidate_id, PublicResume.resume_id == resume_id
        )
    )
