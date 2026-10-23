"""Shared pytest fixtures. The autouse fake_openai fixture keeps every test away from OpenAI."""

from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.features.resume_structuring import service as resume_structuring_service
from app.features.resume_structuring.schemas import (
    Certificate,
    Education,
    Experience,
    Project,
    ResumeData,
)
from app.main import app

# Synthetic candidate only: no real person's data in tests.
FAKE_RESUME_DATA = ResumeData(
    name="Jane Doe",
    email="jane.doe@example.com",
    phone="+1 555 0100",
    location="Springfield, USA",
    links=["https://github.com/janedoe"],
    skills=["Python", "SQL"],
    experience=[
        Experience(
            company="Acme Corp",
            title="Software engineer",
            start_date="Jan 2020",
            end_date="Present",
            location="Remote",
            description="Builds APIs.",
        )
    ],
    education=[
        Education(
            institution="State University",
            degree="BSc",
            field_of_study="Computer Science",
            start_date="2015",
            end_date="2019",
        )
    ],
    projects=[Project(name="Resume parser", description=None, link=None, technologies=["FastAPI"])],
    certificates=[Certificate(name="AWS Cloud Practitioner", issuer="AWS", date="2022")],
)
FAKE_OCR_TEXT = "Jane Doe\njane.doe@example.com\nSoftware engineer, read by OCR"
# The usage every fake response reports, shaped like the SDK's ResponseUsage. At gpt-4.1-mini
# prices: 800 x 0.40 + 200 x 0.10 + 300 x 1.60 = 820 per 1M tokens, so $0.00082 per call.
FAKE_USAGE = SimpleNamespace(
    input_tokens=1000,
    input_tokens_details=SimpleNamespace(cached_tokens=200, cache_write_tokens=0),
    output_tokens=300,
    output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    total_tokens=1300,
)
FAKE_CALL_COST = Decimal("0.00082")


def make_openai_status_error(
    error_class: type[openai.APIStatusError], status_code: int
) -> Exception:
    """Build an SDK error for an HTTP failure (e.g. AuthenticationError, 401) with no network."""
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(status_code, request=request)
    return error_class("fake OpenAI failure", response=response, body=None)


class FakeOpenAI:
    """Stand-in for the OpenAI client.

    `responses.parse` (AI extraction) records its kwargs in `calls`. It returns the queued
    `results` first, one per call, then `result`, as `output_parsed`; or raises `error`.
    `responses.create` (OCR) records its kwargs in `ocr_calls`. It returns `ocr_text` as
    `output_text` with `ocr_status` as `status`, or raises `ocr_error`.
    Every response carries FAKE_USAGE as its `usage`.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.results: list[ResumeData | None] = []
        self.result: ResumeData | None = FAKE_RESUME_DATA
        self.error: Exception | None = None
        self.ocr_calls: list[dict[str, Any]] = []
        self.ocr_text = FAKE_OCR_TEXT
        self.ocr_status = "completed"
        self.ocr_error: Exception | None = None
        self.responses = SimpleNamespace(parse=self.parse, create=self.create)

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        """Record the call, then raise `error` or return the next result the way the SDK does."""
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        parsed = self.results.pop(0) if self.results else self.result
        return SimpleNamespace(output_parsed=parsed, usage=FAKE_USAGE)

    def create(self, **kwargs: Any) -> SimpleNamespace:
        """Record the OCR call, then raise `ocr_error` or return `ocr_text` the way the SDK does."""
        self.ocr_calls.append(kwargs)
        if self.ocr_error is not None:
            raise self.ocr_error
        return SimpleNamespace(output_text=self.ocr_text, status=self.ocr_status, usage=FAKE_USAGE)


@pytest.fixture(autouse=True)
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> FakeOpenAI:
    """Replace the OpenAI client in every test, so pytest can never call the live API.

    Not named `client`: that is the TestClient fixture.
    """
    fake = FakeOpenAI()
    monkeypatch.setattr(resume_structuring_service, "client", fake)
    return fake


@pytest.fixture
def client() -> Iterator[TestClient]:
    """HTTP client for the app. Clears dependency overrides afterwards to keep tests isolated."""
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def db_session() -> Iterator[Session]:
    """A real database session whose writes are rolled back after the test.

    The app's get_db is overridden to use it. Commits only release a SAVEPOINT inside the
    outer transaction, so nothing a test writes stays in the database. Needs Postgres.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        app.dependency_overrides[get_db] = lambda: session
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_db, None)
            session.close()
            transaction.rollback()
