"""Tests for ai_usage pricing and recording (no database needed), and the spending cap check
(Postgres, through db_session).
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.features.ai_usage.models import AiCall
from app.features.ai_usage.service import (
    AI_SPEND_CAP_MESSAGE,
    DATABASE_DOWN_MESSAGE,
    PRICES,
    ai_spend_cap_reached,
    call_cost,
    record_ai_call,
    require_ai_budget,
    total_cost,
)
from app.features.resume_structuring.service import OPENAI_MODEL
from tests.conftest import FAKE_CALL_COST, FAKE_USAGE


def test_call_cost_charges_each_kind_of_token_at_its_own_rate() -> None:
    """gpt-6-luna has four different rates: 700k plain input x 0.10 + 200k cached x 0.01
    + 100k cache writes x 0.125 + 50k output x 0.50 = $0.1095.
    """
    assert call_cost("gpt-6-luna", 1_000_000, 200_000, 100_000, 50_000) == Decimal("0.1095")


def test_a_cache_write_on_gpt_4_1_mini_costs_the_input_rate() -> None:
    with_write = call_cost("gpt-4.1-mini", 1_000, 0, 1_000, 0)
    without_write = call_cost("gpt-4.1-mini", 1_000, 0, 0, 0)

    assert with_write == without_write == Decimal("0.0004")


def test_a_model_with_no_price_costs_none() -> None:
    assert call_cost("gpt-unknown", 1_000, 0, 0, 300) is None


def test_the_model_in_use_has_a_price() -> None:
    """Fails the day OPENAI_MODEL is switched to a model PRICES doesn't know."""
    assert OPENAI_MODEL in PRICES


def test_record_ai_call_appends_the_call_with_its_tokens_and_cost() -> None:
    ai_calls: list[AiCall] = []

    record_ai_call(ai_calls, OPENAI_MODEL, "structuring", FAKE_USAGE)

    [call] = ai_calls
    assert (call.model, call.call_type, call.resume_intake_id) == (
        OPENAI_MODEL,
        "structuring",
        None,
    )
    assert (call.input_tokens, call.cached_tokens, call.cache_write_tokens) == (1000, 200, 0)
    assert call.output_tokens == 300
    assert call.cost_usd == FAKE_CALL_COST


def test_a_response_without_usage_is_skipped() -> None:
    ai_calls: list[AiCall] = []

    record_ai_call(ai_calls, OPENAI_MODEL, "ocr", None)

    assert ai_calls == []


def test_a_detail_count_the_api_left_out_counts_as_zero() -> None:
    """The SDK reads a count missing from the API's answer as None."""
    details = SimpleNamespace(cached_tokens=None, cache_write_tokens=None)
    usage = SimpleNamespace(input_tokens=1_000, input_tokens_details=details, output_tokens=0)
    ai_calls: list[AiCall] = []

    record_ai_call(ai_calls, "gpt-4.1-mini", "ocr", usage)

    [call] = ai_calls
    assert (call.cached_tokens, call.cache_write_tokens) == (0, 0)
    assert call.cost_usd == Decimal("0.0004")


def test_a_usage_without_input_details_counts_no_cached_tokens() -> None:
    """QA found this raised AttributeError outside the upload's try, cutting the event stream."""
    usage = SimpleNamespace(input_tokens=1_000, input_tokens_details=None, output_tokens=200)
    ai_calls: list[AiCall] = []

    record_ai_call(ai_calls, "gpt-4.1-mini", "structuring", usage)

    [call] = ai_calls
    assert (call.input_tokens, call.cached_tokens, call.cache_write_tokens) == (1000, 0, 0)
    # 1,000 x 0.40 + 200 x 1.60 = 720 per 1M tokens
    assert call.cost_usd == Decimal("0.00072")


# --- The all-time spending cap (Postgres; the dev database already holds some real calls) ---


def add_call(db_session: Session, model: str, cost_usd: Decimal | None) -> None:
    """Store one AI call with the given cost, inside the rolled-back test transaction."""
    db_session.add(
        AiCall(
            model=model,
            call_type="structuring",
            input_tokens=1000,
            cached_tokens=0,
            cache_write_tokens=0,
            output_tokens=300,
            cost_usd=cost_usd,
        )
    )
    db_session.flush()


@pytest.fixture
def total_before(db_session: Session) -> Decimal:
    """The all-time cost already stored, so each test can set the cap relative to it."""
    return db_session.scalar(select(func.coalesce(func.sum(AiCall.cost_usd), 0)))


def test_the_cap_is_not_reached_below_it(
    db_session: Session, total_before: Decimal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ai_spend_cap_usd", total_before + Decimal("0.01"))
    add_call(db_session, OPENAI_MODEL, Decimal("0.0099"))

    assert ai_spend_cap_reached(db_session) is False


def test_the_cap_is_reached_at_and_above_it(
    db_session: Session, total_before: Decimal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ai_spend_cap_usd", total_before + Decimal("0.01"))
    add_call(db_session, OPENAI_MODEL, Decimal("0.01"))

    assert ai_spend_cap_reached(db_session) is True

    add_call(db_session, OPENAI_MODEL, Decimal("0.01"))

    assert ai_spend_cap_reached(db_session) is True


def test_an_unpriced_call_counts_as_zero_towards_the_cap(
    db_session: Session, total_before: Decimal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ai_spend_cap_usd", total_before + Decimal("0.01"))
    add_call(db_session, "gpt-unknown", None)

    assert ai_spend_cap_reached(db_session) is False


def make_call(cost_usd: Decimal | None) -> AiCall:
    """An unsaved AI call with the given cost."""
    return AiCall(model=OPENAI_MODEL, call_type="structuring", cost_usd=cost_usd)


def test_total_cost_sums_the_priced_calls_only() -> None:
    calls = [make_call(Decimal("0.001")), make_call(None), make_call(Decimal("0.0005"))]

    assert total_cost(calls) == Decimal("0.0015")


@pytest.mark.parametrize("calls", [[], [None, None]])
def test_total_cost_is_none_when_no_call_is_priced(calls: list[None]) -> None:
    assert total_cost([make_call(cost) for cost in calls]) is None


def test_require_ai_budget_passes_below_the_cap(
    db_session: Session, total_before: Decimal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ai_spend_cap_usd", total_before + Decimal("0.01"))

    assert require_ai_budget(db_session) is None


def test_require_ai_budget_returns_the_connection_to_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upload's long AI and PDF work must not hold a pooled connection (16+ at once would
    wait 30 s and get a false 503). A plain session: it only reads, so nothing is written.
    """
    monkeypatch.setattr(settings, "ai_spend_cap_usd", Decimal("1000000000"))
    checked_out_before = engine.pool.checkedout()
    with SessionLocal() as session:
        require_ai_budget(session)

        assert not session.in_transaction()
        assert engine.pool.checkedout() == checked_out_before


def test_require_ai_budget_is_402_at_the_cap(
    db_session: Session, total_before: Decimal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ai_spend_cap_usd", total_before)

    with pytest.raises(HTTPException) as caught:
        require_ai_budget(db_session)

    assert (caught.value.status_code, caught.value.detail) == (402, AI_SPEND_CAP_MESSAGE)


def test_require_ai_budget_is_503_when_the_database_is_down() -> None:
    class FailingSession:
        def scalar(self, statement: object) -> None:
            raise OperationalError("SELECT", {}, ConnectionRefusedError("connection refused"))

    with pytest.raises(HTTPException) as caught:
        require_ai_budget(FailingSession())  # type: ignore[arg-type]

    assert (caught.value.status_code, caught.value.detail) == (503, DATABASE_DOWN_MESSAGE)
