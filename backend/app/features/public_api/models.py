"""SQLAlchemy model for public_api: one row per (candidate_id, resume_id) pair an outside system
sent through the public API.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Identity, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PublicResume(Base):
    """The details AI extracted from a resume an outside system sent, under that system's own
    candidate and resume ids.

    A repeat pair replaces structured_data (and moves updated_at); the CV file and text aren't
    kept. Kept apart from resume_intakes, whose rows need the interview answers.
    """

    __tablename__ = "public_resumes"
    __table_args__ = (UniqueConstraint("candidate_id", "resume_id"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(Text)
    resume_id: Mapped[str] = mapped_column(Text)
    # ResumeData as JSON.
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
