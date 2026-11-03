"""SQLAlchemy model for candidates: one row per candidate added through the web app or the
public PDF endpoint, pointing at its two stored files.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Identity, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Candidate(Base):
    """A candidate: the AI-extracted name, where it came from, and its two stored files.

    original_key is the original CV's key in the private RESUME_BUCKET; resume_key is the
    branded PDF's key in the public PUBLIC_RESUME_BUCKET. ai_cost_usd is the upload's AI cost
    at save time (null when every call was unpriced); the calls themselves are stored unlinked
    in ai_calls. The CV text and the structured data aren't kept.
    """

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    name: Mapped[str] = mapped_column(Text)
    # "manual" (the web app) or "api" (POST /api/public/pdf), validated in code.
    source: Mapped[str] = mapped_column(String(6))
    # "pdf", "docx", "doc" or "txt": the original's extension and Content-Type.
    file_type: Mapped[str] = mapped_column(String(4))
    original_key: Mapped[str] = mapped_column(Text)
    resume_key: Mapped[str] = mapped_column(Text)
    ai_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric)
