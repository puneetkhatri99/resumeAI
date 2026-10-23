"""Tests for ai_usage pricing and recording (no database needed)."""

from decimal import Decimal
from types import SimpleNamespace

from app.features.ai_usage.models import AiCall
from app.features.ai_usage.service import PRICES, call_cost, record_ai_call
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

    record_ai_call(ai_calls, "gpt-4.1-mini", "structuring", FAKE_USAGE)

    [call] = ai_calls
    assert (call.model, call.call_type, call.resume_intake_id) == (
        "gpt-4.1-mini",
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
