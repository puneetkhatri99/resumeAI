"""AI usage: price each OpenAI call, record it, and report usage and cost for the panel.

Other features record calls through this module (rule 7): they collect AiCall objects in a list
with record_ai_call while they work, then store them with add_ai_calls (with the intake) or
save_ai_calls_without_intake (a failed upload). This module never imports resume_intake, which
imports it. No CV text or personal data is stored or logged here.
"""

import logging
from decimal import Decimal
from typing import NamedTuple

from openai.types.responses import ResponseUsage
from sqlalchemy import ColumnElement, Date, ScalarSelect, cast, func, select, text
from sqlalchemy.exc import DataError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.features.ai_usage.models import AiCall
from app.features.ai_usage.schemas import (
    AiUsageReport,
    CallType,
    DayUsage,
    RecentAiCall,
    UsageGroup,
    UsageTotals,
)

logger = logging.getLogger(__name__)

RECENT_CALLS_LIMIT = 50
DAYS_SHOWN = 30
TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)
UNKNOWN_TIME_ZONE_MESSAGE = "Unknown time zone."


class ModelPrice(NamedTuple):
    """A model's prices in USD per 1M tokens."""

    input: Decimal
    cached_input: Decimal
    cache_write: Decimal
    output: Decimal


# USD per 1M tokens, from OpenAI's pricing and model pages on 2026-10-23. Edited by hand when
# OpenAI changes a price; an edit prices new calls only. Cache writes cost 1.25x input from
# GPT-5.6 on; for older models a cache write costs the input rate.
PRICES: dict[str, ModelPrice] = {
    "gpt-4.1-mini": ModelPrice(Decimal("0.40"), Decimal("0.10"), Decimal("0.40"), Decimal("1.60")),
    "gpt-5.4-mini": ModelPrice(Decimal("0.75"), Decimal("0.075"), Decimal("0.75"), Decimal("4.50")),
    "gpt-5.4-nano": ModelPrice(Decimal("0.20"), Decimal("0.02"), Decimal("0.20"), Decimal("1.25")),
    "gpt-5.6-luna": ModelPrice(Decimal("0.20"), Decimal("0.02"), Decimal("0.25"), Decimal("1.20")),
    "gpt-6-luna": ModelPrice(Decimal("0.10"), Decimal("0.01"), Decimal("0.125"), Decimal("0.50")),
}


class UnknownTimeZoneError(Exception):
    """Postgres doesn't know the time zone the browser sent (HTTP 422)."""


def call_cost(
    model: str, input_tokens: int, cached_tokens: int, cache_write_tokens: int, output_tokens: int
) -> Decimal | None:
    """Return one call's exact cost in USD, or None for a model with no price in PRICES.

    OpenAI's cached and cache-write counts are parts of input_tokens and don't overlap, so the
    rest of the input is charged at the plain input rate. Output includes reasoning tokens.
    """
    price = PRICES.get(model)
    if price is None:
        return None
    uncached_tokens = input_tokens - cached_tokens - cache_write_tokens
    cost = (
        uncached_tokens * price.input
        + cached_tokens * price.cached_input
        + cache_write_tokens * price.cache_write
        + output_tokens * price.output
    )
    return cost / TOKENS_PER_PRICE_UNIT


def record_ai_call(
    ai_calls: list[AiCall], model: str, call_type: CallType, usage: ResponseUsage | None
) -> None:
    """Append an AiCall for one answered OpenAI call to `ai_calls`, priced now. Doesn't store it.

    A response without usage is skipped. `model` is the model asked for (OPENAI_MODEL), not the
    response's, which can be a dated snapshot name that PRICES doesn't know.
    """
    if usage is None:
        return
    # The SDK reads anything the API left out as None. It counts as 0: this bookkeeping runs
    # outside the upload's error handling, so it must never raise.
    details = usage.input_tokens_details
    input_tokens = usage.input_tokens or 0
    cached_tokens = (details.cached_tokens or 0) if details else 0
    cache_write_tokens = (details.cache_write_tokens or 0) if details else 0
    output_tokens = usage.output_tokens or 0
    ai_calls.append(
        AiCall(
            model=model,
            call_type=call_type,
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            cost_usd=call_cost(
                model, input_tokens, cached_tokens, cache_write_tokens, output_tokens
            ),
        )
    )


def add_ai_calls(db: Session, ai_calls: list[AiCall], resume_intake_id: int | None) -> None:
    """Link the calls to the intake and add them to the session. The caller commits."""
    for ai_call in ai_calls:
        ai_call.resume_intake_id = resume_intake_id
    db.add_all(ai_calls)


def save_ai_calls_without_intake(db: Session, ai_calls: list[AiCall]) -> None:
    """Store a failed upload's calls, linked to no intake, so the ones OpenAI billed still count.

    Rolls back first, because the session may be dirty after a failed save. A database error is
    logged (type only) and swallowed, so it never hides the upload's own error.
    """
    if not ai_calls:
        return
    try:
        db.rollback()
        add_ai_calls(db, ai_calls, None)
        db.commit()
    except SQLAlchemyError as exc:
        logger.warning("Could not store the AI usage of a failed upload: %s", type(exc).__name__)


def intake_cost_column(intake_id: ColumnElement[int]) -> ScalarSelect[Decimal | None]:
    """A scalar subquery for the summed cost of an intake's calls (null when it has none)."""
    return (
        select(func.sum(AiCall.cost_usd))
        .where(AiCall.resume_intake_id == intake_id)
        .scalar_subquery()
    )


def usage_report(db: Session, tz: str) -> AiUsageReport:
    """Return the panel's usage and cost, with days and "this month" in the time zone `tz`.

    The zone is set for this transaction only, so date_trunc, the ::date cast and current_date
    all follow it. Raises UnknownTimeZoneError when Postgres doesn't know the zone.
    recent_calls come without candidate_name; the router fills it from resume_intake.
    """
    try:
        db.execute(text("SELECT set_config('TimeZone', :tz, true)"), {"tz": tz})
    except DataError:
        raise UnknownTimeZoneError(UNKNOWN_TIME_ZONE_MESSAGE) from None

    tokens = AiCall.input_tokens + AiCall.output_tokens
    totals = (
        func.count().label("calls"),
        func.coalesce(func.sum(tokens), 0).label("tokens"),
        # Unpriced calls add nothing to the cost; unpriced_calls says how many there were.
        func.coalesce(func.sum(AiCall.cost_usd), 0).label("cost_usd"),
        func.count().filter(AiCall.cost_usd.is_(None)).label("unpriced_calls"),
    )
    day = cast(AiCall.created_at, Date)

    this_month = db.execute(
        select(*totals).where(AiCall.created_at >= func.date_trunc("month", func.now()))
    ).one()
    all_time = db.execute(select(*totals)).one()
    by_model = db.execute(
        select(AiCall.model.label("key"), *totals).group_by(AiCall.model).order_by(AiCall.model)
    ).all()
    by_call_type = db.execute(
        select(AiCall.call_type.label("key"), *totals)
        .group_by(AiCall.call_type)
        .order_by(AiCall.call_type)
    ).all()
    by_day = db.execute(
        select(day.label("day"), *totals)
        .where(day > func.current_date() - DAYS_SHOWN)
        .group_by(day)
        .order_by(day.desc())
    ).all()
    recent_calls = db.execute(
        select(
            AiCall.created_at,
            AiCall.model,
            AiCall.call_type,
            tokens.label("tokens"),
            AiCall.cost_usd,
            AiCall.resume_intake_id,
        )
        .order_by(AiCall.created_at.desc(), AiCall.id.desc())
        .limit(RECENT_CALLS_LIMIT)
    ).all()

    return AiUsageReport(
        this_month=UsageTotals.model_validate(this_month),
        all_time=UsageTotals.model_validate(all_time),
        by_model=[UsageGroup.model_validate(row) for row in by_model],
        by_call_type=[UsageGroup.model_validate(row) for row in by_call_type],
        by_day=[DayUsage.model_validate(row) for row in by_day],
        recent_calls=[RecentAiCall.model_validate(row) for row in recent_calls],
    )
