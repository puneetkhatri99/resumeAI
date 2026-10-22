"""Shared pytest fixtures. The autouse fake_openai fixture keeps every test away from OpenAI."""

from collections.abc import Iterator
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


def make_openai_status_error(
    error_class: type[openai.APIStatusError], status_code: int
) -> Exception:
    """Build an SDK error for an HTTP failure (e.g. AuthenticationError, 401) with no network."""
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(status_code, request=request)
    return error_class("fake OpenAI failure", response=response, body=None)


class FakeOpenAI:
    """Stand-in for the OpenAI client: `responses.parse` records its kwargs in `calls`.

    It returns `result` as `output_parsed`, or raises `error` when a test sets one.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: ResumeData | None = FAKE_RESUME_DATA
        self.error: Exception | None = None
        self.responses = SimpleNamespace(parse=self.parse)

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        """Record the call, then raise `error` or return `result` the way the SDK does."""
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_parsed=self.result)


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
