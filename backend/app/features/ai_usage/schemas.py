"""Response models for GET /api/ai_usage: totals, groups, days and recent calls.

Costs are float, not Decimal: Pydantic would send a Decimal as a JSON string. The sums are
exact in SQL; only the response rounds them to a float.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

CallType = Literal["structuring", "ocr", "ocr_retry"]


class UsageTotals(BaseModel):
    """Calls, tokens (input + output) and cost over a set of calls.

    cost_usd sums the priced calls only (0 when there are none); unpriced_calls counts the calls
    whose model has no price, so the panel can warn that the cost leaves them out.
    """

    model_config = ConfigDict(from_attributes=True)

    calls: int
    tokens: int
    cost_usd: float
    unpriced_calls: int


class UsageGroup(UsageTotals):
    """The totals of one model or one call type; `key` is the model name or the call type."""

    key: str


class DayUsage(UsageTotals):
    """The totals of one day (in the browser's time zone) that had calls."""

    day: date


class RecentAiCall(BaseModel):
    """One recent call. candidate_name is filled from the linked intake, null when none is."""

    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    model: str
    call_type: CallType
    tokens: int
    cost_usd: float | None
    resume_intake_id: int | None
    candidate_name: str | None = None


class AiUsageReport(BaseModel):
    """The AI usage panel: this month and all time, by model and call type (all time), cost
    per day (the last 30 days, days with calls only, newest first) and the newest calls.
    """

    this_month: UsageTotals
    all_time: UsageTotals
    by_model: list[UsageGroup]
    by_call_type: list[UsageGroup]
    by_day: list[DayUsage]
    recent_calls: list[RecentAiCall]
