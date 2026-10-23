"""SQLAlchemy model for ai_usage: one row per OpenAI call that got an answer (and was billed)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Identity, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiCall(Base):
    """One OpenAI call: its model, type, token counts and cost. No CV text or personal data.

    cost_usd is computed when the call is recorded, so history keeps the price that applied
    then; it is null for a model with no price. resume_intake_id links the call to the stored
    intake, and is null for a failed upload (or once that intake is deleted).
    """

    __tablename__ = "ai_calls"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    model: Mapped[str] = mapped_column(Text)
    # "structuring", "ocr" or "ocr_retry"
    call_type: Mapped[str] = mapped_column(String(11))
    input_tokens: Mapped[int] = mapped_column(Integer)
    cached_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    resume_intake_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_intakes.id", ondelete="SET NULL"), index=True
    )
