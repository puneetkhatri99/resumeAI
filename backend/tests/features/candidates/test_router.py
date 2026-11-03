"""Tests for the candidates endpoints (/api/candidates). OpenAI is replaced by the autouse
fake_openai fixture, storage by fake_s3 and render_pdf by FakeRenderer, so no test calls OpenAI,
Neon or Chromium. Every request that can store something goes through db_session and is
rolled back.
"""

import json
import re
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.features.ai_usage.models import AiCall
from app.features.ai_usage.service import AI_SPEND_CAP_MESSAGE, DATABASE_DOWN_MESSAGE
from app.features.candidates import router
from app.features.candidates.models import Candidate
from app.features.candidates.service import (
    DELETE_FAILED_MESSAGE,
    ORIGINAL_URL_EXPIRY_SECONDS,
    STORE_FAILED_MESSAGE,
)
from app.features.resume_generation.service import PDF_FAILED_MESSAGE, PdfRenderError
from app.features.resume_intake.service import EMPTY_MESSAGE, UNSUPPORTED_TYPE_MESSAGE
from app.features.resume_structuring.service import AI_FAILED_MESSAGE, MISSING_CONTACT_MESSAGE
from app.main import MAX_REQUEST_BYTES, app
from tests.conftest import FAKE_CALL_COST, FAKE_OCR_TEXT, FAKE_RESUME_DATA, FakeOpenAI, FakeS3
from tests.features.resume_intake.conftest import build_pdf

URL = "/api/candidates"
TXT_CV = ("cv.txt", b"Jane Doe\r\njane.doe@example.com", "text/plain")
FAKE_PDF = b"%PDF-fake"
KEY_PATTERN = r"candidates/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}"


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


class FailingCommitSession:
    """Stand-in session whose commit fails the way it does when Postgres is down.

    Its one read, the spending-cap check's sum, answers $0, so the upload gets to the commit.
    """

    def scalar(self, statement: object) -> Decimal:
        return Decimal(0)

    def add(self, instance: object) -> None:
        pass

    def add_all(self, instances: object) -> None:
        pass

    def rollback(self) -> None:
        pass

    def commit(self) -> None:
        raise OperationalError("INSERT", {}, ConnectionRefusedError("connection refused"))


class FailingQuerySession:
    """Stand-in session whose reads fail the way they do when Postgres is down."""

    def execute(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT", {}, ConnectionRefusedError("connection refused"))

    get = execute
    scalar = execute
    scalars = execute


@pytest.fixture(autouse=True)
def fake_renderer(monkeypatch: pytest.MonkeyPatch) -> FakeRenderer:
    """Replace render_pdf on the router module in every test, so none starts Chromium."""
    fake = FakeRenderer()
    monkeypatch.setattr(router, "render_pdf", fake)
    return fake


def read_events(response: httpx2.Response) -> list[dict[str, Any]]:
    """Check the response is a 200 event stream and return each event's JSON data, in order."""
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def post_cv(client: TestClient, cv: tuple[str, bytes, str] = TXT_CV) -> list[dict[str, Any]]:
    """Upload a CV and return the stream's events."""
    return read_events(client.post(URL, files={"file": cv}))


def max_id(db_session: Session, column: Any) -> int:
    """The newest id in a table, so a test can find the rows its request added."""
    return db_session.scalar(select(func.coalesce(func.max(column), 0)))


def candidates_after(db_session: Session, last_id: int) -> list[Candidate]:
    """The candidates stored after `last_id`."""
    return list(db_session.scalars(select(Candidate).where(Candidate.id > last_id)))


def ai_calls_after(db_session: Session, last_id: int) -> list[tuple[str, int | None]]:
    """(call_type, resume_intake_id) of the AI calls stored after `last_id`, oldest first."""
    query = (
        select(AiCall.call_type, AiCall.resume_intake_id)
        .where(AiCall.id > last_id)
        .order_by(AiCall.id)
    )
    return [tuple(row) for row in db_session.execute(query)]


def public_url_of(key: str) -> str:
    """The public URL the API should give for a key in the public bucket."""
    return f"{settings.aws_endpoint_url_s3.rstrip('/')}/{settings.public_resume_bucket}/{key}"


# --- POST /api/candidates: success ---


def test_a_txt_cv_streams_every_stage_and_done_holds_only_the_public_url(
    client: TestClient, db_session: Session, fake_s3: FakeS3, fake_renderer: FakeRenderer
) -> None:
    last_candidate = max_id(db_session, Candidate.id)
    last_call = max_id(db_session, AiCall.id)

    events = post_cv(client)

    stages = ["reading", "extracting", "generating", "uploading", "done"]
    assert [event["stage"] for event in events] == stages
    [candidate] = candidates_after(db_session, last_candidate)
    assert events[-1] == {
        "stage": "done",
        "new_resume_url": public_url_of(candidate.resume_key),
        "status": None,
        "detail": None,
    }
    assert candidate.name == "Jane Doe"
    assert candidate.source == "manual"
    assert candidate.file_type == "txt"
    assert candidate.ai_cost_usd == FAKE_CALL_COST
    assert re.fullmatch(KEY_PATTERN + "/original.txt", candidate.original_key)
    assert (
        candidate.resume_key == candidate.original_key.removesuffix("original.txt") + "resume.pdf"
    )
    assert ai_calls_after(db_session, last_call) == [("structuring", None)]
    assert "Jane Doe" in fake_renderer.pages[0]

    original = fake_s3.objects[(settings.resume_bucket, candidate.original_key)]
    assert original == {
        "Body": TXT_CV[1],
        "ContentType": "text/plain",
        "ContentDisposition": "attachment",
        "CacheControl": "no-store",
    }
    resume = fake_s3.objects[(settings.public_resume_bucket, candidate.resume_key)]
    assert resume == {
        "Body": FAKE_PDF,
        "ContentType": "application/pdf",
        "ContentDisposition": "inline; filename*=UTF-8''MinistryHub%20-%20Jane%20Doe.pdf",
    }
    assert len(fake_s3.objects) == 2


def test_every_ai_call_is_unpriced_so_the_cost_is_null(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.features.ai_usage.service.PRICES", {})
    last_candidate = max_id(db_session, Candidate.id)

    assert post_cv(client)[-1]["stage"] == "done"

    [candidate] = candidates_after(db_session, last_candidate)
    assert candidate.ai_cost_usd is None


def test_a_scanned_pdf_is_read_by_ocr_and_its_cost_covers_both_calls(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI, fake_s3: FakeS3
) -> None:
    last_candidate = max_id(db_session, Candidate.id)
    last_call = max_id(db_session, AiCall.id)

    events = post_cv(client, ("scan.pdf", build_pdf([""]), "application/pdf"))

    assert events[-1]["stage"] == "done"
    assert fake_openai.calls[0]["input"] == FAKE_OCR_TEXT
    [candidate] = candidates_after(db_session, last_candidate)
    assert candidate.file_type == "pdf"
    assert candidate.ai_cost_usd == 2 * FAKE_CALL_COST
    assert ai_calls_after(db_session, last_call) == [("ocr", None), ("structuring", None)]
    original = fake_s3.objects[(settings.resume_bucket, candidate.original_key)]
    assert original["ContentType"] == "application/pdf"
    assert candidate.original_key.endswith("/original.pdf")


def test_a_pdf_without_an_email_is_ocrd_once_more(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    fake_openai.results = [FAKE_RESUME_DATA.model_copy(update={"email": ""})]
    last_call = max_id(db_session, AiCall.id)

    events = post_cv(client, ("cv.pdf", build_pdf(["Jane Doe"]), "application/pdf"))

    assert events[-1]["stage"] == "done"
    assert len(fake_openai.ocr_calls) == 1
    assert ai_calls_after(db_session, last_call) == [
        ("structuring", None),
        ("ocr_retry", None),
        ("structuring", None),
    ]


# --- POST /api/candidates: error events ---


def assert_nothing_stored(db_session: Session, fake_s3: FakeS3, last_candidate: int) -> None:
    """No candidate row and no object in either bucket."""
    assert candidates_after(db_session, last_candidate) == []
    assert fake_s3.objects == {}


def test_an_unsupported_file_is_a_415_event_and_stores_nothing(
    client: TestClient, db_session: Session, fake_s3: FakeS3
) -> None:
    last_candidate = max_id(db_session, Candidate.id)
    png = ("cv.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")

    events = post_cv(client, png)

    assert [event["stage"] for event in events] == ["reading", "error"]
    assert events[-1] == {
        "stage": "error",
        "new_resume_url": None,
        "status": 415,
        "detail": UNSUPPORTED_TYPE_MESSAGE,
    }
    assert_nothing_stored(db_session, fake_s3, last_candidate)


def test_an_empty_cv_is_a_422_event_and_stores_nothing(
    client: TestClient, db_session: Session, fake_s3: FakeS3, fake_openai: FakeOpenAI
) -> None:
    last_candidate = max_id(db_session, Candidate.id)

    events = post_cv(client, ("cv.txt", b"   ", "text/plain"))

    assert events[-1]["status"] == 422
    assert events[-1]["detail"] == EMPTY_MESSAGE
    assert fake_openai.calls == []
    assert_nothing_stored(db_session, fake_s3, last_candidate)


def test_a_cv_without_an_email_is_a_422_event_and_its_call_is_stored_unlinked(
    client: TestClient, db_session: Session, fake_s3: FakeS3, fake_openai: FakeOpenAI
) -> None:
    fake_openai.result = FAKE_RESUME_DATA.model_copy(update={"email": ""})
    last_candidate = max_id(db_session, Candidate.id)
    last_call = max_id(db_session, AiCall.id)

    events = post_cv(client)

    assert [event["stage"] for event in events] == ["reading", "extracting", "error"]
    assert events[-1]["status"] == 422
    assert events[-1]["detail"] == MISSING_CONTACT_MESSAGE
    assert_nothing_stored(db_session, fake_s3, last_candidate)
    assert ai_calls_after(db_session, last_call) == [("structuring", None)]


def test_an_ai_failure_is_a_502_event_and_stores_nothing(
    client: TestClient, db_session: Session, fake_s3: FakeS3, fake_openai: FakeOpenAI
) -> None:
    fake_openai.error = openai.APITimeoutError(request=httpx2.Request("POST", "https://x"))
    last_candidate = max_id(db_session, Candidate.id)

    events = post_cv(client)

    assert events[-1]["status"] == 502
    assert events[-1]["detail"] == AI_FAILED_MESSAGE
    assert_nothing_stored(db_session, fake_s3, last_candidate)


def test_a_render_failure_is_a_500_event_uploads_nothing_and_keeps_the_ai_cost(
    client: TestClient,
    db_session: Session,
    fake_s3: FakeS3,
    fake_renderer: FakeRenderer,
) -> None:
    fake_renderer.error = PdfRenderError(PDF_FAILED_MESSAGE)
    last_candidate = max_id(db_session, Candidate.id)
    last_call = max_id(db_session, AiCall.id)

    events = post_cv(client)

    assert [event["stage"] for event in events] == ["reading", "extracting", "generating", "error"]
    assert events[-1]["status"] == 500
    assert events[-1]["detail"] == "Could not create the PDF. Try again."
    assert_nothing_stored(db_session, fake_s3, last_candidate)
    assert ai_calls_after(db_session, last_call) == [("structuring", None)]


@pytest.mark.parametrize("failing_bucket", ["resume_bucket", "public_resume_bucket"])
def test_a_failed_put_is_a_502_event_and_leaves_no_object(
    client: TestClient,
    db_session: Session,
    fake_s3: FakeS3,
    caplog: pytest.LogCaptureFixture,
    failing_bucket: str,
) -> None:
    fake_s3.fail_put_buckets.add(getattr(settings, failing_bucket))
    last_candidate = max_id(db_session, Candidate.id)
    last_call = max_id(db_session, AiCall.id)

    events = post_cv(client)

    assert [event["stage"] for event in events][-2:] == ["uploading", "error"]
    assert events[-1]["status"] == 502
    assert events[-1]["detail"] == STORE_FAILED_MESSAGE
    assert_nothing_stored(db_session, fake_s3, last_candidate)
    assert ai_calls_after(db_session, last_call) == [("structuring", None)]
    assert "Could not store a candidate file: InternalError" in caplog.text
    assert "Jane Doe" not in caplog.text


def test_a_failed_commit_is_a_503_event_and_deletes_both_objects(
    client: TestClient, fake_s3: FakeS3, caplog: pytest.LogCaptureFixture
) -> None:
    app.dependency_overrides[get_db] = FailingCommitSession

    events = post_cv(client)

    assert events[-1]["status"] == 503
    assert events[-1]["detail"] == DATABASE_DOWN_MESSAGE
    assert fake_s3.objects == {}
    assert len(fake_s3.deleted) == 2
    assert "Jane Doe" not in caplog.text


def test_a_failed_cleanup_logs_the_orphans_key_only(
    client: TestClient, fake_s3: FakeS3, caplog: pytest.LogCaptureFixture
) -> None:
    app.dependency_overrides[get_db] = FailingCommitSession
    fake_s3.fail_delete = True

    events = post_cv(client)

    assert events[-1]["status"] == 503
    assert len(fake_s3.objects) == 2
    for _, key in fake_s3.objects:
        assert f"Orphaned object {key} left in storage: InternalError" in caplog.text
    assert "Jane Doe" not in caplog.text


# --- POST /api/candidates: refused before the stream ---


def test_the_spending_cap_is_402_before_any_ai_call(
    client: TestClient,
    db_session: Session,
    fake_openai: FakeOpenAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A $0 cap is always reached."""
    monkeypatch.setattr(settings, "ai_spend_cap_usd", Decimal(0))

    response = client.post(URL, files={"file": TXT_CV})

    assert response.status_code == 402
    assert response.json() == {"detail": AI_SPEND_CAP_MESSAGE}
    assert fake_openai.calls == []


def test_the_database_down_during_the_cap_check_is_503(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    app.dependency_overrides[get_db] = FailingQuerySession

    response = client.post(URL, files={"file": TXT_CV})

    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_DOWN_MESSAGE}
    assert fake_openai.calls == []


def test_a_request_over_10_mb_is_413(client: TestClient, fake_openai: FakeOpenAI) -> None:
    big = ("cv.txt", b"a" * (MAX_REQUEST_BYTES + 1), "text/plain")

    response = client.post(URL, files={"file": big})

    assert response.status_code == 413
    assert fake_openai.calls == []


def test_a_missing_file_is_422(client: TestClient, db_session: Session) -> None:
    assert client.post(URL).status_code == 422


# --- GET /api/candidates ---


def add_candidates(db_session: Session, count: int) -> list[Candidate]:
    """Store `count` synthetic candidates directly, oldest first."""
    rows = [
        Candidate(
            name=f"Test Candidate {number}",
            source="manual" if number % 2 else "api",
            file_type="pdf",
            original_key=f"candidates/test-{number}/original.pdf",
            resume_key=f"candidates/test-{number}/resume.pdf",
            ai_cost_usd=Decimal("0.0012") if number % 2 else None,
        )
        for number in range(count)
    ]
    db_session.add_all(rows)
    db_session.flush()
    return rows


def test_the_list_is_newest_first_and_pages_by_id(client: TestClient, db_session: Session) -> None:
    rows = add_candidates(db_session, 3)
    newest_first = [row.id for row in reversed(rows)]

    page = client.get(URL, params={"before_id": rows[-1].id + 1, "limit": 2}).json()

    assert [item["id"] for item in page["items"]] == newest_first[:2]
    assert page["has_more"] is True
    rest = client.get(URL, params={"before_id": newest_first[1], "limit": 2}).json()
    assert rest["items"][0]["id"] == newest_first[2]


def test_each_row_has_its_fields_and_both_links(client: TestClient, db_session: Session) -> None:
    unpriced, priced = add_candidates(db_session, 2)

    items = client.get(URL, params={"before_id": priced.id + 1, "limit": 2}).json()["items"]

    assert items[0]["name"] == "Test Candidate 1"
    assert items[0]["source"] == "manual"
    assert items[0]["ai_cost_usd"] == 0.0012
    assert items[0]["created_at"]
    assert items[0]["new_resume_url"] == public_url_of(priced.resume_key)
    assert items[1]["source"] == "api"
    assert items[1]["ai_cost_usd"] is None

    original = urlsplit(items[0]["original_resume_url"])
    endpoint = urlsplit(settings.aws_endpoint_url_s3)
    assert (original.scheme, original.netloc) == (endpoint.scheme, endpoint.netloc)
    assert original.path.endswith(f"/{settings.resume_bucket}/{priced.original_key}")
    query = parse_qs(original.query)
    assert query["X-Amz-Expires"] == [str(ORIGINAL_URL_EXPIRY_SECONDS)]
    assert "X-Amz-Signature" in query


@pytest.mark.parametrize(
    "params",
    [{"before_id": 0}, {"before_id": 2_147_483_648}, {"limit": 0}, {"limit": 101}],
)
def test_bad_paging_is_422(client: TestClient, params: dict[str, int]) -> None:
    assert client.get(URL, params=params).status_code == 422


def test_listing_with_the_database_down_is_503(client: TestClient) -> None:
    app.dependency_overrides[get_db] = FailingQuerySession

    response = client.get(URL)

    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_DOWN_MESSAGE}


# --- DELETE /api/candidates/{id} ---


def test_delete_removes_both_objects_and_the_row(
    client: TestClient, db_session: Session, fake_s3: FakeS3
) -> None:
    post_cv(client)
    candidate = db_session.scalars(select(Candidate).order_by(Candidate.id.desc())).first()
    assert len(fake_s3.objects) == 2

    response = client.delete(f"{URL}/{candidate.id}")

    assert response.status_code == 204
    assert response.content == b""
    assert fake_s3.objects == {}
    assert db_session.get(Candidate, candidate.id) is None


def test_deleting_an_unknown_candidate_is_404(client: TestClient, db_session: Session) -> None:
    response = client.delete(f"{URL}/2147483647")

    assert response.status_code == 404
    assert response.json() == {"detail": "Candidate not found."}


@pytest.mark.parametrize("candidate_id", ["0", "2147483648", "abc"])
def test_deleting_an_out_of_range_id_is_422(client: TestClient, candidate_id: str) -> None:
    assert client.delete(f"{URL}/{candidate_id}").status_code == 422


def test_a_failed_object_delete_is_502_and_keeps_the_row(
    client: TestClient, db_session: Session, fake_s3: FakeS3
) -> None:
    [row] = add_candidates(db_session, 1)
    fake_s3.fail_delete = True

    response = client.delete(f"{URL}/{row.id}")

    assert response.status_code == 502
    assert response.json() == {"detail": DELETE_FAILED_MESSAGE}
    assert db_session.get(Candidate, row.id) is not None


def test_a_database_failure_after_the_objects_are_deleted_is_503_and_a_retry_finishes(
    client: TestClient, db_session: Session, fake_s3: FakeS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    [row] = add_candidates(db_session, 1)
    real_commit = db_session.commit

    def failing_commit() -> None:
        raise OperationalError("DELETE", {}, ConnectionRefusedError("connection refused"))

    monkeypatch.setattr(db_session, "commit", failing_commit)
    response = client.delete(f"{URL}/{row.id}")

    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_DOWN_MESSAGE}
    assert (settings.resume_bucket, row.original_key) in fake_s3.deleted
    # A new request gets a fresh session: forget the unflushed delete, as that would.
    db_session.expunge_all()
    assert db_session.get(Candidate, row.id) is not None

    monkeypatch.setattr(db_session, "commit", real_commit)
    assert client.delete(f"{URL}/{row.id}").status_code == 204
    assert db_session.get(Candidate, row.id) is None
