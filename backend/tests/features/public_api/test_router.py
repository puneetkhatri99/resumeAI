"""Tests for the public API (/api/public). OpenAI is replaced by the autouse fake_openai fixture
and render_pdf by FakeRenderer, so no test calls OpenAI or starts Chromium. Every request that
can store something goes through db_session and is rolled back.
"""

from decimal import Decimal

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.features.ai_usage.models import AiCall
from app.features.public_api import router
from app.features.public_api.models import PublicResume
from app.features.public_api.router import AI_SPEND_CAP_MESSAGE, INVALID_KEY_MESSAGE
from app.features.resume_intake.service import UNSUPPORTED_TYPE_MESSAGE
from app.features.resume_structuring.service import AI_FAILED_MESSAGE, MISSING_CONTACT_MESSAGE
from tests.conftest import FAKE_RESUME_DATA, FakeOpenAI

URL = "/api/public/resumes"
API_KEY = "test-public-key"
HEADERS = {"X-API-Key": API_KEY}
IDS = {"candidate_id": "cand-1", "resume_id": "res-1"}
PDF_URL = "/api/public/resumes/cand-1/res-1/pdf"
TXT_CV = ("cv.txt", b"Jane Doe\r\njane.doe@example.com", "text/plain")
FAKE_PDF = b"%PDF-fake"


class FakeRenderer:
    """Stand-in for render_pdf: records each HTML page and returns FAKE_PDF."""

    def __init__(self) -> None:
        self.pages: list[str] = []

    def __call__(self, html: str) -> bytes:
        self.pages.append(html)
        return FAKE_PDF


@pytest.fixture(autouse=True)
def public_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure PUBLIC_API_KEY for every test (tests that need it unset override it)."""
    monkeypatch.setattr(settings, "public_api_key", SecretStr(API_KEY))


@pytest.fixture(autouse=True)
def fake_renderer(monkeypatch: pytest.MonkeyPatch) -> FakeRenderer:
    """Replace render_pdf on the router module in every test, so none starts Chromium."""
    fake = FakeRenderer()
    monkeypatch.setattr(router, "render_pdf", fake)
    return fake


def stored_rows(db_session: Session) -> list[PublicResume]:
    """Every stored public resume for the test's pair."""
    query = select(PublicResume).where(
        PublicResume.candidate_id == IDS["candidate_id"], PublicResume.resume_id == IDS["resume_id"]
    )
    return list(db_session.scalars(query))


def max_ai_call_id(db_session: Session) -> int:
    """The newest ai_calls id, so a test can find the rows its request added."""
    return db_session.scalar(select(func.coalesce(func.max(AiCall.id), 0)))


# --- X-API-Key ---


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
@pytest.mark.parametrize("method, path", [("POST", URL), ("GET", PDF_URL)])
def test_a_missing_or_wrong_key_is_401(
    client: TestClient, fake_openai: FakeOpenAI, headers: dict[str, str], method: str, path: str
) -> None:
    response = client.request(method, path, headers=headers, data=IDS, files={"file": TXT_CV})

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_KEY_MESSAGE}
    assert fake_openai.calls == []


def test_every_request_is_401_while_no_key_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "public_api_key", None)

    assert client.get(PDF_URL, headers=HEADERS).status_code == 401
    assert client.get(PDF_URL, headers={"X-API-Key": ""}).status_code == 401


# --- POST /api/public/resumes ---


def test_a_resume_is_extracted_stored_and_returned(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    last_call_id = max_ai_call_id(db_session)

    response = client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    assert response.status_code == 200
    assert response.json() == {**IDS, "data": FAKE_RESUME_DATA.model_dump()}
    [row] = stored_rows(db_session)
    assert row.structured_data == FAKE_RESUME_DATA.model_dump()
    # The billed call counts toward the spending cap, linked to no intake.
    calls = db_session.execute(
        select(AiCall.call_type, AiCall.resume_intake_id).where(AiCall.id > last_call_id)
    ).all()
    assert [tuple(call) for call in calls] == [("structuring", None)]


def test_a_repeat_pair_replaces_the_stored_details(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})
    fake_openai.result = FAKE_RESUME_DATA.model_copy(update={"name": "Jane Q. Doe"})

    response = client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Jane Q. Doe"
    [row] = stored_rows(db_session)
    assert row.structured_data["name"] == "Jane Q. Doe"


def test_an_unsupported_file_is_415_and_stores_nothing(
    client: TestClient, db_session: Session
) -> None:
    png = ("cv.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")

    response = client.post(URL, headers=HEADERS, data=IDS, files={"file": png})

    assert response.status_code == 415
    assert response.json() == {"detail": UNSUPPORTED_TYPE_MESSAGE}
    assert stored_rows(db_session) == []


def test_a_cv_without_an_email_is_422_and_stores_nothing(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    fake_openai.result = FAKE_RESUME_DATA.model_copy(update={"email": ""})

    response = client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    assert response.status_code == 422
    assert response.json() == {"detail": MISSING_CONTACT_MESSAGE}
    assert stored_rows(db_session) == []


def test_an_ai_failure_is_502_and_stores_nothing(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    fake_openai.error = openai.APITimeoutError(request=request)

    response = client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    assert response.status_code == 502
    assert response.json() == {"detail": AI_FAILED_MESSAGE}
    assert stored_rows(db_session) == []


def test_a_billed_ai_answer_that_is_rejected_is_502_and_its_call_is_stored_unlinked(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    """A refusal (no parsed output) is billed, so it still counts toward the spending cap."""
    fake_openai.result = None
    last_call_id = max_ai_call_id(db_session)

    response = client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    assert response.status_code == 502
    assert stored_rows(db_session) == []
    calls = db_session.execute(
        select(AiCall.call_type, AiCall.resume_intake_id).where(AiCall.id > last_call_id)
    ).all()
    assert [tuple(call) for call in calls] == [("structuring", None)]


@pytest.mark.parametrize(
    "ids",
    [
        {"candidate_id": "", "resume_id": "res-1"},
        {"candidate_id": "cand-1", "resume_id": "x" * 101},
        {"candidate_id": "cand\x001", "resume_id": "res-1"},
        {"candidate_id": "cand-1", "resume_id": "res/1"},
        {"candidate_id": "cand-1"},
    ],
)
def test_invalid_ids_are_422_before_any_ai_call(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI, ids: dict[str, str]
) -> None:
    response = client.post(URL, headers=HEADERS, data=ids, files={"file": TXT_CV})

    assert response.status_code == 422
    assert fake_openai.calls == []


def test_ids_of_100_characters_are_accepted(client: TestClient, db_session: Session) -> None:
    ids = {"candidate_id": "c" * 100, "resume_id": "r" * 100}

    response = client.post(URL, headers=HEADERS, data=ids, files={"file": TXT_CV})

    assert response.status_code == 200


def test_the_spending_cap_is_402_before_any_ai_call(
    client: TestClient,
    db_session: Session,
    fake_openai: FakeOpenAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A $0 cap is always reached."""
    monkeypatch.setattr(settings, "ai_spend_cap_usd", Decimal(0))

    response = client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    assert response.status_code == 402
    assert response.json() == {"detail": AI_SPEND_CAP_MESSAGE}
    assert fake_openai.calls == []


# --- GET /api/public/resumes/{candidate_id}/{resume_id}/pdf ---


def test_the_pdf_of_a_stored_pair_is_returned_inline_and_not_cached(
    client: TestClient, db_session: Session, fake_renderer: FakeRenderer
) -> None:
    client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    response = client.get(PDF_URL, headers=HEADERS, params={"template_id": 1})

    assert response.status_code == 200
    assert response.content == FAKE_PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        "inline; filename*=UTF-8''MinistryHub%20-%20Jane%20Doe.pdf"
    )
    assert response.headers["cache-control"] == "no-store"
    assert "Jane Doe" in fake_renderer.pages[0]


def test_template_id_defaults_to_1(client: TestClient, db_session: Session) -> None:
    client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    assert client.get(PDF_URL, headers=HEADERS).status_code == 200


def test_an_unknown_pair_is_404(client: TestClient, db_session: Session) -> None:
    response = client.get("/api/public/resumes/nobody/none/pdf", headers=HEADERS)

    assert response.status_code == 404
    assert response.json() == {"detail": "Resume not found."}


def test_an_unknown_template_is_404(
    client: TestClient, db_session: Session, fake_renderer: FakeRenderer
) -> None:
    client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    response = client.get(PDF_URL, headers=HEADERS, params={"template_id": 2})

    assert response.status_code == 404
    assert response.json() == {"detail": "Template not found."}
    assert fake_renderer.pages == []
