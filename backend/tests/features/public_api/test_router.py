"""Tests for the public API (/api/public). OpenAI is replaced by the autouse fake_openai fixture,
storage by fake_s3 and render_pdf by FakeRenderer, so no test calls OpenAI, Neon or Chromium.
Every request that can store something goes through db_session and is rolled back.
"""

import json
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
from app.features.ai_usage.service import AI_SPEND_CAP_MESSAGE
from app.features.candidates.models import Candidate
from app.features.candidates.service import STORE_FAILED_MESSAGE, public_url
from app.features.public_api import router
from app.features.public_api.models import PublicResume
from app.features.public_api.router import INVALID_KEY_MESSAGE
from app.features.resume_generation.service import PdfRenderError
from app.features.resume_intake.service import UNSUPPORTED_TYPE_MESSAGE
from app.features.resume_structuring.schemas import InferredSkill
from app.features.resume_structuring.service import AI_FAILED_MESSAGE, MISSING_CONTACT_MESSAGE
from tests.conftest import FAKE_CALL_COST, FAKE_RESUME_DATA, FakeOpenAI, FakeS3

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
@pytest.mark.parametrize(
    "method, path", [("POST", URL), ("GET", PDF_URL), ("POST", "/api/public/pdf")]
)
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


def test_the_pdf_of_a_pair_stored_before_the_new_fields_is_returned(
    client: TestClient, db_session: Session
) -> None:
    """Old rows have no summary or inferred_skills keys; the model defaults fill them in."""
    stored = FAKE_RESUME_DATA.model_dump(exclude={"summary", "inferred_skills"})
    db_session.add(PublicResume(**IDS, structured_data=stored))
    db_session.flush()

    assert client.get(PDF_URL, headers=HEADERS).status_code == 200


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


# --- POST /api/public/pdf ---

NEW_PDF_URL = "/api/public/pdf"


def candidates_after(db_session: Session, last_id: int) -> list[Candidate]:
    """The candidates stored after `last_id`."""
    return list(db_session.scalars(select(Candidate).where(Candidate.id > last_id)))


def max_candidate_id(db_session: Session) -> int:
    """The newest candidates id, so a test can find the rows its request added."""
    return db_session.scalar(select(func.coalesce(func.max(Candidate.id), 0)))


def test_a_cv_comes_back_as_the_pdf_url_and_data_and_is_saved_as_an_api_candidate(
    client: TestClient, db_session: Session, fake_s3: FakeS3
) -> None:
    last_candidate = max_candidate_id(db_session)
    last_call_id = max_ai_call_id(db_session)

    response = client.post(NEW_PDF_URL, headers=HEADERS, files={"file": TXT_CV})

    assert response.status_code == 200
    [candidate] = candidates_after(db_session, last_candidate)
    assert response.json() == {
        "url": public_url(candidate.resume_key),
        "data": json.loads(FAKE_RESUME_DATA.model_dump_json()),
    }
    assert response.json()["url"].endswith(
        f"/{settings.public_resume_bucket}/{candidate.resume_key}"
    )
    assert (candidate.name, candidate.source, candidate.file_type) == ("Jane Doe", "api", "txt")
    assert candidate.ai_cost_usd == FAKE_CALL_COST
    assert (
        fake_s3.objects[(settings.public_resume_bucket, candidate.resume_key)]["Body"] == FAKE_PDF
    )
    assert (settings.resume_bucket, candidate.original_key) in fake_s3.objects
    calls = db_session.execute(
        select(AiCall.call_type, AiCall.resume_intake_id).where(AiCall.id > last_call_id)
    ).all()
    assert [tuple(call) for call in calls] == [("structuring", None)]


def test_template_id_1_is_accepted(client: TestClient, db_session: Session) -> None:
    response = client.post(
        NEW_PDF_URL, headers=HEADERS, data={"template_id": "1"}, files={"file": TXT_CV}
    )

    assert response.status_code == 200


def test_an_unknown_template_is_404_before_any_ai_call(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI, fake_s3: FakeS3
) -> None:
    response = client.post(
        NEW_PDF_URL, headers=HEADERS, data={"template_id": "2"}, files={"file": TXT_CV}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Template not found."}
    assert fake_openai.calls == []
    assert fake_s3.objects == {}


def test_a_non_integer_template_is_422(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    response = client.post(
        NEW_PDF_URL, headers=HEADERS, data={"template_id": "one"}, files={"file": TXT_CV}
    )

    assert response.status_code == 422
    assert fake_openai.calls == []


def test_a_storage_failure_is_502_without_the_pdf(
    client: TestClient, db_session: Session, fake_s3: FakeS3
) -> None:
    fake_s3.fail_put_buckets.add(settings.public_resume_bucket)
    last_candidate = max_candidate_id(db_session)

    response = client.post(NEW_PDF_URL, headers=HEADERS, files={"file": TXT_CV})

    assert response.status_code == 502
    assert response.json() == {"detail": STORE_FAILED_MESSAGE}
    assert fake_s3.objects == {}
    assert candidates_after(db_session, last_candidate) == []


def test_an_ai_failure_on_the_pdf_endpoint_is_502_and_stores_nothing(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI, fake_s3: FakeS3
) -> None:
    fake_openai.error = openai.APITimeoutError(request=httpx2.Request("POST", "https://x"))

    response = client.post(NEW_PDF_URL, headers=HEADERS, files={"file": TXT_CV})

    assert response.status_code == 502
    assert response.json() == {"detail": AI_FAILED_MESSAGE}
    assert fake_s3.objects == {}


def test_the_spending_cap_is_402_on_the_pdf_endpoint(
    client: TestClient,
    db_session: Session,
    fake_openai: FakeOpenAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_spend_cap_usd", Decimal(0))

    response = client.post(NEW_PDF_URL, headers=HEADERS, files={"file": TXT_CV})

    assert response.status_code == 402
    assert fake_openai.calls == []


# --- QA pass 2026-11-03: POST /api/public/pdf error paths ---


def test_an_unsupported_file_on_the_pdf_endpoint_is_415(
    client: TestClient, db_session: Session, fake_s3: FakeS3
) -> None:
    response = client.post(
        NEW_PDF_URL,
        headers=HEADERS,
        files={"file": ("cv.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")},
    )

    assert response.status_code == 415
    assert response.json() == {"detail": UNSUPPORTED_TYPE_MESSAGE}
    assert fake_s3.objects == {}


def test_a_missing_file_on_the_pdf_endpoint_is_422(client: TestClient) -> None:
    assert client.post(NEW_PDF_URL, headers=HEADERS).status_code == 422


def test_a_render_failure_on_the_pdf_endpoint_is_500_and_stores_nothing(
    client: TestClient,
    db_session: Session,
    fake_s3: FakeS3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_render(html: str) -> bytes:
        raise PdfRenderError("Could not create the PDF. Try again.")

    monkeypatch.setattr(router, "render_pdf", failing_render)
    last_candidate = max_candidate_id(db_session)

    response = client.post(NEW_PDF_URL, headers=HEADERS, files={"file": TXT_CV})

    assert response.status_code == 500
    assert fake_s3.objects == {}
    assert candidates_after(db_session, last_candidate) == []


def test_the_original_storage_failure_on_the_pdf_endpoint_leaves_no_object(
    client: TestClient, db_session: Session, fake_s3: FakeS3
) -> None:
    fake_s3.fail_put_buckets.add(settings.resume_bucket)

    response = client.post(NEW_PDF_URL, headers=HEADERS, files={"file": TXT_CV})

    assert response.status_code == 502
    assert fake_s3.objects == {}


def test_the_cap_is_checked_before_the_template_on_the_pdf_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    monkeypatch.setattr(settings, "ai_spend_cap_usd", Decimal(0))

    response = client.post(
        NEW_PDF_URL, headers=HEADERS, data={"template_id": "2"}, files={"file": TXT_CV}
    )

    assert response.status_code == 402


def test_a_request_over_10_mb_on_the_pdf_endpoint_is_413(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    from app.main import MAX_REQUEST_BYTES

    big = ("cv.txt", b"a" * (MAX_REQUEST_BYTES + 1), "text/plain")

    response = client.post(NEW_PDF_URL, headers=HEADERS, files={"file": big})

    assert response.status_code == 413
    assert fake_openai.calls == []


def test_the_pdf_of_a_pair_with_unknown_extra_keys_is_returned(
    client: TestClient, db_session: Session
) -> None:
    stored = {**FAKE_RESUME_DATA.model_dump(), "future_field": "x"}
    db_session.add(PublicResume(**IDS, structured_data=stored))
    db_session.flush()

    assert client.get(PDF_URL, headers=HEADERS).status_code == 200


def test_a_stored_hostile_summary_is_escaped_in_the_public_pdf_page(
    client: TestClient, db_session: Session, fake_renderer: FakeRenderer
) -> None:
    stored = {**FAKE_RESUME_DATA.model_dump(), "summary": "<img src=x onerror=alert(1)>"}
    db_session.add(PublicResume(**IDS, structured_data=stored))
    db_session.flush()

    assert client.get(PDF_URL, headers=HEADERS).status_code == 200
    assert "<img src=x" not in fake_renderer.pages[0]


def test_the_extraction_json_carries_the_summary_and_the_proven_inferred_skills(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    unproven = InferredSkill(name="Kubernetes", evidence="never written in this CV")
    fake_openai.result = FAKE_RESUME_DATA.model_copy(
        update={"inferred_skills": [*FAKE_RESUME_DATA.inferred_skills, unproven]}
    )

    response = client.post(URL, headers=HEADERS, data=IDS, files={"file": TXT_CV})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == FAKE_RESUME_DATA.summary
    assert [skill["name"] for skill in data["inferred_skills"]] == [
        skill.name for skill in FAKE_RESUME_DATA.inferred_skills
    ]
