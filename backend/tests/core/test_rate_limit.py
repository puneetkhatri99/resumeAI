"""Tests for app.core.rate_limit: the per-IP sliding-window limits on /api (no database needed).

A fake clock makes the windows deterministic. The autouse reset_rate_limits fixture (in
tests/conftest.py) starts each test with no requests counted.
"""

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.rate_limit import (
    API_LIMIT,
    API_WINDOW_SECONDS,
    UPLOAD_LIMIT,
    UPLOAD_PATH,
    UPLOAD_WINDOW_SECONDS,
    RateLimiter,
)
from app.features.resume_intake.router import require_ai_budget
from app.main import app

UNKNOWN_API_PATH = "/api/no_such_endpoint"  # a 404 that needs no database, but is still /api


class FakeClock:
    """Stands in for time.monotonic; tests move `now` by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Replace the limiter's clock with a FakeClock."""
    fake = FakeClock()
    monkeypatch.setattr(rate_limit, "monotonic", fake)
    return fake


def test_the_121st_api_request_in_a_minute_is_429_with_retry_after(
    client: TestClient, clock: FakeClock
) -> None:
    for _ in range(API_LIMIT):
        assert client.get(UNKNOWN_API_PATH).status_code == 404

    response = client.get(UNKNOWN_API_PATH)

    assert response.status_code == 429
    assert response.headers["retry-after"] == str(API_WINDOW_SECONDS)
    assert response.json() == {"detail": "Too many requests. Try again in 60 seconds."}


def test_the_31st_upload_in_an_hour_is_429_but_other_requests_still_pass(
    client: TestClient, clock: FakeClock
) -> None:
    """Empty upload bodies get a quick 422; the cap check is skipped, so no database is needed."""
    app.dependency_overrides[require_ai_budget] = lambda: None
    for _ in range(UPLOAD_LIMIT):
        assert client.post(UPLOAD_PATH).status_code == 422

    response = client.post(UPLOAD_PATH)

    assert response.status_code == 429
    assert response.headers["retry-after"] == str(UPLOAD_WINDOW_SECONDS)
    assert response.json() == {"detail": "Too many requests. Try again in 3600 seconds."}
    assert client.get(UNKNOWN_API_PATH).status_code == 404


def test_requests_outside_api_are_not_counted(client: TestClient, clock: FakeClock) -> None:
    """`/apiary` only starts with the letters "api"; it isn't under /api."""
    for _ in range(API_LIMIT + 5):
        assert client.get("/apiary").status_code == 404

    for _ in range(API_LIMIT):
        assert client.get(UNKNOWN_API_PATH).status_code == 404
    assert client.get(UNKNOWN_API_PATH).status_code == 429


def test_two_ips_are_counted_separately(clock: FakeClock) -> None:
    limiter = RateLimiter()
    for _ in range(API_LIMIT):
        assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) is None

    assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) == API_WINDOW_SECONDS
    assert limiter.check("203.0.113.2", "GET", UNKNOWN_API_PATH) is None


def test_the_window_slides(clock: FakeClock) -> None:
    """Each request leaves the window 60 s after it was made, not at a fixed minute boundary."""
    limiter = RateLimiter()
    for second in range(API_LIMIT):
        clock.now = 1000.0 + second * 0.25  # 120 requests spread over 30 s
        assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) is None

    clock.now = 1059.5
    assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) == 1  # 0.5 s, rounded up
    clock.now = 1060.0  # the first request has left the window; the second hasn't
    assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) is None
    assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) == 1


def test_a_refused_request_is_not_counted(clock: FakeClock) -> None:
    """A client that waits for Retry-After gets in, however often it was refused before."""
    limiter = RateLimiter()
    for _ in range(API_LIMIT):
        limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH)
    for _ in range(50):
        assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) is not None

    clock.now += API_WINDOW_SECONDS
    assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) is None


def test_a_refused_upload_does_not_use_up_the_overall_limit(clock: FakeClock) -> None:
    limiter = RateLimiter()
    for _ in range(UPLOAD_LIMIT):
        assert limiter.check("203.0.113.1", "POST", UPLOAD_PATH) is None
    for _ in range(API_LIMIT):
        assert limiter.check("203.0.113.1", "POST", UPLOAD_PATH) is not None

    for _ in range(API_LIMIT - UPLOAD_LIMIT):
        assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) is None
    assert limiter.check("203.0.113.1", "GET", UNKNOWN_API_PATH) is not None


def test_idle_ips_are_dropped_from_memory(clock: FakeClock) -> None:
    """The periodic sweep prunes every key, so IPs that stop sending don't pile up."""
    limiter = RateLimiter()
    limiter.check("203.0.113.1", "POST", UPLOAD_PATH)
    limiter.check("203.0.113.2", "GET", UNKNOWN_API_PATH)

    clock.now += API_WINDOW_SECONDS
    limiter.check("203.0.113.3", "GET", UNKNOWN_API_PATH)
    # Only the upload (still inside its hour) and the new request are left.
    assert set(limiter._hits) == {("203.0.113.1", "upload"), ("203.0.113.3", "api")}

    clock.now += UPLOAD_WINDOW_SECONDS
    limiter.check("203.0.113.3", "GET", UNKNOWN_API_PATH)
    assert set(limiter._hits) == {("203.0.113.3", "api")}
