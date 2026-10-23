"""Tests for GET /api/ai_usage, against Postgres through db_session (rolled back afterwards).

The usage_db fixture first deletes every stored AI call inside that rolled-back transaction, so
rows already in the dev database never change the totals. Some tests came from the QA pass
(2026-10-23): the month boundary per time zone, mixed priced and unpriced groups, ordering, the
50-call cap and the intake foreign key.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.ai_usage.models import AiCall
from app.features.ai_usage.service import RECENT_CALLS_LIMIT, record_ai_call
from app.features.resume_intake.models import ResumeIntake
from app.features.resume_intake.schemas import InterviewAnswers
from app.features.resume_intake.service import save_resume_intake
from app.main import app
from tests.conftest import FAKE_RESUME_DATA, FAKE_USAGE

URL = "/api/ai_usage"
ANSWERS = InterviewAnswers(
    notice_period="2 weeks",
    salary_expectation="$60k/year",
    work_mode="hybrid",
    rating=4,
    strengths="Clear communicator",
    concerns="Little cloud experience",
    overall_notes="Move to the next round",
)


class FailingQuerySession:
    """Stand-in session whose reads fail the way they do when Postgres is down."""

    def execute(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT", {}, ConnectionRefusedError("connection refused"))


@pytest.fixture
def usage_db(db_session: Session) -> Session:
    """db_session with no AI calls in it (they come back when the test's transaction rolls back)."""
    db_session.execute(delete(AiCall))
    return db_session


def add_call(
    db_session: Session,
    *,
    created_at: datetime | None = None,
    model: str = "gpt-4.1-mini",
    call_type: str = "structuring",
    cost: str | None = "0.0010",
) -> None:
    """Store an AI call of 1,000 input and 300 output tokens, linked to no intake.

    created_at defaults to the database's now() (the column's server default).
    """
    call = AiCall(
        model=model,
        call_type=call_type,
        input_tokens=1000,
        cached_tokens=0,
        cache_write_tokens=0,
        output_tokens=300,
        cost_usd=None if cost is None else Decimal(cost),
    )
    if created_at is not None:
        call.created_at = created_at
    db_session.add(call)
    db_session.flush()


def totals(calls: int, cost: float, unpriced_calls: int = 0) -> dict[str, Any]:
    """The expected totals of `calls` calls stored by add_call (1,300 tokens each)."""
    return {
        "calls": calls,
        "tokens": 1300 * calls,
        "cost_usd": cost,
        "unpriced_calls": unpriced_calls,
    }


def utc_today(db_session: Session) -> date:
    """Today in UTC by the database clock: the date the endpoint's current_date gives for UTC."""
    return db_session.scalar(text("SELECT (now() AT TIME ZONE 'UTC')::date"))


def month_start_in(db_session: Session, tz: str) -> datetime:
    """The first instant of the current month in `tz`, by the database clock."""
    return db_session.scalar(
        text("SELECT date_trunc('month', now() AT TIME ZONE :tz) AT TIME ZONE :tz"), {"tz": tz}
    )


def test_with_no_calls_the_totals_are_zero_and_the_lists_empty(
    client: TestClient, usage_db: Session
) -> None:
    """A period with no calls costs $0 (user decision, 2026-10-23), not null."""
    response = client.get(URL, params={"tz": "Asia/Kolkata"})

    assert response.status_code == 200
    assert response.json() == {
        "this_month": totals(0, 0),
        "all_time": totals(0, 0),
        "by_model": [],
        "by_call_type": [],
        "by_day": [],
        "recent_calls": [],
    }


def test_this_month_leaves_out_a_call_from_40_days_ago(
    client: TestClient, usage_db: Session
) -> None:
    add_call(usage_db, cost="0.0010")
    add_call(usage_db, created_at=datetime.now(UTC) - timedelta(days=40), cost="0.0030")

    body = client.get(URL, params={"tz": "UTC"}).json()

    assert body["this_month"] == totals(1, 0.001)
    assert body["all_time"] == totals(2, 0.004)
    assert [day["calls"] for day in body["by_day"]] == [1]


def test_this_month_starts_at_the_start_of_the_month_in_the_browsers_time_zone(
    client: TestClient, usage_db: Session
) -> None:
    month_start = month_start_in(usage_db, "Asia/Tokyo")
    add_call(usage_db, created_at=month_start - timedelta(minutes=1), cost="0.0030")
    add_call(usage_db, created_at=month_start + timedelta(minutes=1), cost="0.0010")

    body = client.get(URL, params={"tz": "Asia/Tokyo"}).json()

    assert body["this_month"] == totals(1, 0.001)
    assert body["all_time"] == totals(2, 0.004)


def test_cost_per_day_covers_the_last_30_days(client: TestClient, usage_db: Session) -> None:
    today = utc_today(usage_db)
    for days_ago in (29, 30):
        noon = datetime.combine(today - timedelta(days=days_ago), time(12), tzinfo=UTC)
        add_call(usage_db, created_at=noon)

    by_day = client.get(URL, params={"tz": "UTC"}).json()["by_day"]

    assert by_day == [{"day": (today - timedelta(days=29)).isoformat(), **totals(1, 0.001)}]


def test_days_are_listed_newest_first(client: TestClient, usage_db: Session) -> None:
    today = utc_today(usage_db)
    for days_ago in (3, 1, 2):
        noon = datetime.combine(today - timedelta(days=days_ago), time(12), UTC)
        add_call(usage_db, created_at=noon)

    by_day = client.get(URL, params={"tz": "UTC"}).json()["by_day"]

    assert [day["day"] for day in by_day] == [
        (today - timedelta(days=days_ago)).isoformat() for days_ago in (1, 2, 3)
    ]


def test_a_model_with_no_price_costs_nothing_and_is_counted_as_unpriced(
    client: TestClient, usage_db: Session
) -> None:
    add_call(usage_db, model="gpt-unknown", cost=None)
    add_call(usage_db, model="gpt-4.1-mini", cost="0.0020")

    body = client.get(URL, params={"tz": "UTC"}).json()

    assert body["by_model"] == [
        {"key": "gpt-4.1-mini", **totals(1, 0.002)},
        {"key": "gpt-unknown", **totals(1, 0, unpriced_calls=1)},
    ]
    # A single call keeps its null cost: the panel shows "no price" for it.
    recent_costs = {call["model"]: call["cost_usd"] for call in body["recent_calls"]}
    assert recent_costs == {"gpt-4.1-mini": 0.002, "gpt-unknown": None}


def test_a_group_of_priced_and_unpriced_calls_sums_the_priced_ones_and_counts_the_rest(
    client: TestClient, usage_db: Session
) -> None:
    """User decision (2026-10-23): the priced sum, plus a warning about the unpriced calls."""
    add_call(usage_db, model="gpt-4.1-mini", cost="0.0040")
    add_call(usage_db, model="gpt-unknown", cost=None)

    body = client.get(URL, params={"tz": "UTC"}).json()

    mixed = totals(2, 0.004, unpriced_calls=1)
    assert body["this_month"] == mixed
    assert body["all_time"] == mixed
    assert body["by_call_type"] == [{"key": "structuring", **mixed}]
    assert [{k: v for k, v in day.items() if k != "day"} for day in body["by_day"]] == [mixed]


def test_a_day_with_only_unpriced_calls_costs_nothing_and_counts_them(
    client: TestClient, usage_db: Session
) -> None:
    add_call(usage_db, model="gpt-unknown", cost=None)

    by_day = client.get(URL, params={"tz": "UTC"}).json()["by_day"]

    assert [(day["calls"], day["cost_usd"], day["unpriced_calls"]) for day in by_day] == [(1, 0, 1)]


def test_calls_are_grouped_by_call_type(client: TestClient, usage_db: Session) -> None:
    for call_type, cost in [
        ("structuring", "0.0010"),
        ("structuring", "0.0020"),
        ("ocr", "0.0040"),
        ("ocr_retry", "0.0050"),
    ]:
        add_call(usage_db, call_type=call_type, cost=cost)

    by_call_type = client.get(URL, params={"tz": "UTC"}).json()["by_call_type"]

    assert by_call_type == [
        {"key": "ocr", **totals(1, 0.004)},
        {"key": "ocr_retry", **totals(1, 0.005)},
        {"key": "structuring", **totals(2, 0.003)},
    ]


def test_days_follow_the_browsers_time_zone(client: TestClient, usage_db: Session) -> None:
    """20:00 UTC yesterday is 05:00 the next day in Tokyo (UTC+9, no daylight saving)."""
    yesterday = utc_today(usage_db) - timedelta(days=1)
    add_call(usage_db, created_at=datetime.combine(yesterday, time(20), tzinfo=UTC))

    in_utc = client.get(URL, params={"tz": "UTC"}).json()["by_day"]
    in_tokyo = client.get(URL, params={"tz": "Asia/Tokyo"}).json()["by_day"]

    assert [day["day"] for day in in_utc] == [yesterday.isoformat()]
    assert [day["day"] for day in in_tokyo] == [(yesterday + timedelta(days=1)).isoformat()]


def test_recent_calls_carry_the_candidates_name_or_null(
    client: TestClient, usage_db: Session
) -> None:
    ai_calls: list[AiCall] = []
    record_ai_call(ai_calls, "gpt-4.1-mini", "structuring", FAKE_USAGE)
    intake = save_resume_intake(
        usage_db, "cv.txt", "txt", "Jane Doe", FAKE_RESUME_DATA, ANSWERS, ai_calls
    )
    # A failed upload's call, an hour older: linked to no intake.
    add_call(usage_db, call_type="ocr", created_at=datetime.now(UTC) - timedelta(hours=1))

    recent = client.get(URL, params={"tz": "UTC"}).json()["recent_calls"]

    assert [(c["call_type"], c["resume_intake_id"], c["candidate_name"]) for c in recent] == [
        ("structuring", intake.id, "Jane Doe"),
        ("ocr", None, None),
    ]
    assert set(recent[0]) == {
        "created_at",
        "model",
        "call_type",
        "tokens",
        "cost_usd",
        "resume_intake_id",
        "candidate_name",
    }
    assert recent[0]["tokens"] == 1300


def test_recent_calls_are_the_newest_50(client: TestClient, usage_db: Session) -> None:
    now = datetime.now(UTC)
    # The call `age` minutes old costs 0.(age + 1), so the cost says which call it is.
    for age in range(RECENT_CALLS_LIMIT + 1):
        add_call(usage_db, created_at=now - timedelta(minutes=age), cost=f"0.{age + 1:04d}")

    body = client.get(URL, params={"tz": "UTC"}).json()

    assert body["all_time"]["calls"] == RECENT_CALLS_LIMIT + 1
    assert [call["cost_usd"] for call in body["recent_calls"]] == [
        float(Decimal(f"0.{age + 1:04d}")) for age in range(RECENT_CALLS_LIMIT)
    ]


def test_calls_stored_in_one_transaction_list_the_last_stored_first(
    client: TestClient, usage_db: Session
) -> None:
    """One upload's calls share created_at (now() is the transaction start); id breaks the tie."""
    same_time = datetime.now(UTC)
    add_call(usage_db, created_at=same_time, call_type="ocr")
    add_call(usage_db, created_at=same_time, call_type="structuring")

    recent = client.get(URL, params={"tz": "UTC"}).json()["recent_calls"]

    assert [call["call_type"] for call in recent] == ["structuring", "ocr"]


def test_deleting_an_intake_keeps_its_ai_calls_unlinked(
    client: TestClient, usage_db: Session
) -> None:
    ai_calls: list[AiCall] = []
    record_ai_call(ai_calls, "gpt-4.1-mini", "structuring", FAKE_USAGE)
    intake = save_resume_intake(
        usage_db, "cv.txt", "txt", "Jane Doe", FAKE_RESUME_DATA, ANSWERS, ai_calls
    )

    usage_db.execute(delete(ResumeIntake).where(ResumeIntake.id == intake.id))
    usage_db.expire_all()

    assert usage_db.scalars(select(AiCall.resume_intake_id)).all() == [None]
    body = client.get(URL, params={"tz": "UTC"}).json()
    assert body["all_time"]["calls"] == 1
    assert [(c["resume_intake_id"], c["candidate_name"]) for c in body["recent_calls"]] == [
        (None, None)
    ]


def test_an_unknown_time_zone_is_422(client: TestClient, usage_db: Session) -> None:
    response = client.get(URL, params={"tz": "Not/AZone"})

    assert response.status_code == 422
    assert response.json() == {"detail": "Unknown time zone."}


@pytest.mark.parametrize("params", [{}, {"tz": ""}, {"tz": "A" * 65}])
def test_a_missing_or_badly_sized_time_zone_is_422(
    client: TestClient, params: dict[str, str]
) -> None:
    assert client.get(URL, params=params).status_code == 422


def test_database_down_is_503(client: TestClient) -> None:
    app.dependency_overrides[get_db] = FailingQuerySession

    response = client.get(URL, params={"tz": "UTC"})

    assert response.status_code == 503
    assert response.json() == {"detail": "The database is unavailable. Try again later."}
