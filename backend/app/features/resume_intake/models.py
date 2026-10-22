"""SQLAlchemy model for resume intake: one row per submitted interview."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Identity, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResumeIntake(Base):
    """A submitted interview: the CV's text, the details AI extracted from it, and the answers.

    The original file is not kept. Every column except structured_data is NOT NULL.
    """

    __tablename__ = "resume_intakes"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    original_filename: Mapped[str] = mapped_column(Text)
    file_type: Mapped[str] = mapped_column(String(4))
    extracted_text: Mapped[str] = mapped_column(Text)
    # ResumeData as JSON. Null only on rows stored before AI extraction existed.
    structured_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    notice_period: Mapped[str] = mapped_column(Text)
    salary_expectation: Mapped[str] = mapped_column(Text)
    work_mode: Mapped[str] = mapped_column(String(6))
    rating: Mapped[int] = mapped_column(SmallInteger)
    strengths: Mapped[str] = mapped_column(Text)
    concerns: Mapped[str] = mapped_column(Text)
    overall_notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
