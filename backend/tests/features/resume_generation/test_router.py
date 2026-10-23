"""Tests for GET /api/resume_generation/{intake_id}. render_pdf is faked in every test, so no
test here starts Chromium; rows go through db_session and are rolled back.
"""

import logging
from typing import Any

import pytest
import sqlalchemy
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.resume_generation import router
from app.features.resume_generation.service import PDF_FAILED_MESSAGE, PdfRenderError
from app.features.resume_intake.models import ResumeIntake
from app.features.resume_intake.schemas import InterviewAnswers
from app.features.resume_intake.service import save_resume_intake
from app.main import app
from tests.conftest import FAKE_RESUME_DATA

FAKE_PDF = b"%PDF-fake"
ANSWERS = InterviewAnswers(
    notice_period="2 weeks",
    salary_expectation="$60k/year",
    work_mode="hybrid",
    rating=4,
    strengths="Clear communicator",
    concerns="Little cloud experience",
    overall_notes="Move to the next round",
)


class FakeRenderer:
    """Stand-in for render_pdf: records each HTML page and returns FAKE_PDF, or raises `error`."""

    def __init__(self) -> None:
        self.pages: list[str] = []
        self.error: Exception | None = None

    def __call__(self, html: str) -> bytes:
        self.pages.append(html)
        if self.error is not None:
            raise self.error
        return FAKE_PDF


class UnreachableDatabaseSession:
    """Stand-in session whose every read fails the way it does when Postgres is down."""

    def get(self, *args: Any, **kwargs: Any) -> None:
        raise OperationalError("SELECT", {}, ConnectionRefusedError("connection refused"))


@pytest.fixture(autouse=True)
def fake_renderer(monkeypatch: pytest.MonkeyPatch) -> FakeRenderer:
    """Replace render_pdf on the router module in every test, so none starts Chromium."""
    fake = FakeRenderer()
    monkeypatch.setattr(router, "render_pdf", fake)
    return fake


def store_intake(db_session: Session, structured_data: Any) -> int:
    """Insert a row directly, for stored data the service would never write; returns its id."""
    intake = ResumeIntake(
        original_filename="cv.txt",
        file_type="txt",
        extracted_text="Jane Doe",
        structured_data=structured_data,
        **ANSWERS.model_dump(),
    )
    db_session.add(intake)
    db_session.commit()
    return intake.id


def test_pdf_is_returned_inline_and_not_cached(
    client: TestClient, db_session: Session, fake_renderer: FakeRenderer
) -> None:
    intake = save_resume_intake(
        db_session, "cv.txt", "txt", "Jane Doe", FAKE_RESUME_DATA, ANSWERS, []
    )

    response = client.get(f"/api/resume_generation/{intake.id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == FAKE_PDF
    assert (
        response.headers["content-disposition"]
        == "inline; filename*=UTF-8''MinistryHub%20-%20Jane%20Doe.pdf"
    )
    assert response.headers["cache-control"] == "no-store"
    assert "Jane Doe" in fake_renderer.pages[0]


def test_filename_is_percent_encoded(client: TestClient, db_session: Session) -> None:
    """The name is AI output: a slash, a non-ASCII letter or CR/LF must not reach the header."""
    data = FAKE_RESUME_DATA.model_copy(update={"name": "Ana/Ünal\r\nX"})
    intake = save_resume_intake(db_session, "cv.txt", "txt", "Ana", data, ANSWERS, [])

    response = client.get(f"/api/resume_generation/{intake.id}")

    disposition = response.headers["content-disposition"]
    assert "%2F" in disposition
    assert "%C3%9C" in disposition
    assert "%0D%0A" in disposition
    assert "\r" not in disposition
    assert "\n" not in disposition


def test_filename_cannot_add_content_disposition_parameters(
    client: TestClient, db_session: Session
) -> None:
    """A quote and a semicolon in the AI name must stay inside the percent-encoded filename."""
    data = FAKE_RESUME_DATA.model_copy(update={"name": 'Jane "The Coder"; filename=evil.exe'})
    intake = save_resume_intake(db_session, "cv.txt", "txt", "Jane", data, ANSWERS, [])

    response = client.get(f"/api/resume_generation/{intake.id}")

    assert response.headers["content-disposition"] == (
        "inline; filename*=UTF-8''MinistryHub%20-%20Jane%20%22The%20Coder%22%3B%20filename%3D"
        "evil.exe.pdf"
    )


def test_unknown_intake_is_404(client: TestClient, db_session: Session) -> None:
    response = client.get("/api/resume_generation/-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Intake not found."}


@pytest.mark.parametrize(
    ("intake_id", "status_code"),
    [("2147483647", 404), ("2147483648", 422), ("99999999999", 422)],
)
def test_id_beyond_the_database_integer_range_is_not_reported_as_database_down(
    client: TestClient, db_session: Session, intake_id: str, status_code: int
) -> None:
    """The id column is a 32-bit integer: Postgres rejects 99999999999 with 'integer out of
    range', which the router must not turn into "The database is unavailable" (503). A bigger
    id is a 422, like a non-integer one (decided 2026-10-22).
    """
    response = client.get(f"/api/resume_generation/{intake_id}")

    assert response.status_code == status_code, response.text


def test_intake_without_structured_data_is_404(
    client: TestClient, db_session: Session, fake_renderer: FakeRenderer
) -> None:
    """sqlalchemy.null() stores a SQL NULL, like rows from before AI extraction."""
    intake_id = store_intake(db_session, sqlalchemy.null())

    response = client.get(f"/api/resume_generation/{intake_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "This intake has no extracted details."}
    assert fake_renderer.pages == []


def test_intake_with_json_null_structured_data_is_404(
    client: TestClient, db_session: Session, fake_renderer: FakeRenderer
) -> None:
    """A JSON null (a Python None written through SQLAlchemy) loads as None, like a SQL NULL."""
    intake_id = store_intake(db_session, None)
    stored_type = db_session.execute(
        sqlalchemy.text("SELECT jsonb_typeof(structured_data) FROM resume_intakes WHERE id = :id"),
        {"id": intake_id},
    ).scalar()
    assert stored_type == "null"

    response = client.get(f"/api/resume_generation/{intake_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "This intake has no extracted details."}
    assert fake_renderer.pages == []


def test_non_integer_id_is_422(client: TestClient) -> None:
    response = client.get("/api/resume_generation/abc")

    assert response.status_code == 422


def test_database_down_is_503(client: TestClient) -> None:
    app.dependency_overrides[get_db] = UnreachableDatabaseSession

    response = client.get("/api/resume_generation/1")

    assert response.status_code == 503
    assert response.json() == {"detail": "The database is unavailable. Try again later."}


def test_stored_data_that_does_not_match_the_schema_is_500_without_leaking_it(
    client: TestClient,
    db_session: Session,
    fake_renderer: FakeRenderer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """E.g. a row the location migration didn't reach. The ValidationError quotes CV data."""
    stored = FAKE_RESUME_DATA.model_dump()
    del stored["location"]
    intake_id = store_intake(db_session, stored)

    with caplog.at_level(logging.INFO):
        response = client.get(f"/api/resume_generation/{intake_id}")

    assert response.status_code == 500
    assert response.json() == {"detail": "The stored CV details can't be read."}
    assert f"intake {intake_id}" in caplog.text
    assert "Jane Doe" not in caplog.text
    assert "jane.doe" not in caplog.text
    assert fake_renderer.pages == []


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param([FAKE_RESUME_DATA.model_dump()], id="json-array"),
        pytest.param("Jane Doe, jane.doe@example.com", id="json-string"),
        pytest.param({}, id="empty-object"),
        pytest.param({**FAKE_RESUME_DATA.model_dump(), "name": None}, id="name-is-null"),
        pytest.param({**FAKE_RESUME_DATA.model_dump(), "skills": "Python"}, id="skills-not-a-list"),
    ],
)
def test_stored_data_of_the_wrong_shape_is_500(
    client: TestClient,
    db_session: Session,
    fake_renderer: FakeRenderer,
    caplog: pytest.LogCaptureFixture,
    stored: Any,
) -> None:
    intake_id = store_intake(db_session, stored)

    with caplog.at_level(logging.INFO):
        response = client.get(f"/api/resume_generation/{intake_id}")

    assert response.status_code == 500
    assert response.json() == {"detail": "The stored CV details can't be read."}
    assert "Jane Doe" not in caplog.text
    assert "jane.doe" not in caplog.text
    assert fake_renderer.pages == []


def test_render_failure_is_500(
    client: TestClient, db_session: Session, fake_renderer: FakeRenderer
) -> None:
    fake_renderer.error = PdfRenderError(PDF_FAILED_MESSAGE)
    intake = save_resume_intake(
        db_session, "cv.txt", "txt", "Jane Doe", FAKE_RESUME_DATA, ANSWERS, []
    )

    response = client.get(f"/api/resume_generation/{intake.id}")

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not create the PDF. Try again."}
