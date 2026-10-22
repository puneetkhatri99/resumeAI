"""Tests for POST /api/resume_intake. OpenAI is replaced by the autouse fake_openai fixture."""

import json
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import fastapi.routing
import httpx2
import openai
import pydantic
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.resume_intake.models import ResumeIntake
from app.features.resume_intake.schemas import ResumeIntakeResponse
from app.features.resume_intake.service import SCANNED_PDF_MESSAGE, TOO_MANY_PAGES_MESSAGE
from app.features.resume_structuring.schemas import ResumeData
from app.features.resume_structuring.service import (
    AI_FAILED_MESSAGE,
    MAX_CV_TEXT_CHARS,
    MISSING_CONTACT_MESSAGE,
    TOO_LONG_MESSAGE,
)
from app.main import MAX_REQUEST_BYTES, app
from tests.conftest import FAKE_RESUME_DATA, FakeOpenAI, make_openai_status_error

URL = "/api/resume_intake"
ANSWERS = {
    "notice_period": "2 weeks",
    "salary_expectation": "$60k/year",
    "work_mode": "hybrid",
    "rating": "4",
    "strengths": "Clear communicator",
    "concerns": "Little cloud experience",
    "overall_notes": "Move to the next round",
}
TXT_CV = ("cv.txt", b"Jane Doe\r\nSoftware engineer", "text/plain")


class FailingCommitSession:
    """Stand-in session whose commit fails the way it does when Postgres is down."""

    def add(self, instance: object) -> None:
        pass

    def commit(self) -> None:
        raise OperationalError("INSERT", {}, ConnectionRefusedError("connection refused"))


def read_events(response: httpx2.Response) -> list[dict[str, Any]]:
    """Check the response is a 200 event stream and return each event's JSON data, in order."""
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def count_rows(db_session: Session) -> int:
    """The number of stored intakes, to check that a failed request stored nothing."""
    return db_session.scalar(select(func.count()).select_from(ResumeIntake))


def test_txt_cv_is_read_extracted_and_stored(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    """Needs a running Postgres that matches DATABASE_URL."""
    events = read_events(client.post(URL, data=ANSWERS, files={"file": TXT_CV}))

    assert [event["stage"] for event in events] == ["reading", "extracting", "done"]
    assert events[0] == {"stage": "reading", "result": None, "status": None, "detail": None}
    assert fake_openai.calls[0]["input"] == "Jane Doe\nSoftware engineer"
    body = events[-1]["result"]
    assert body["original_filename"] == "cv.txt"
    assert body["file_type"] == "txt"
    assert body["extracted_text"] == "Jane Doe\nSoftware engineer"
    assert body["structured_data"] == fake_openai.result.model_dump()
    assert body["rating"] == 4
    assert body["created_at"]
    row = db_session.get(ResumeIntake, body["id"])
    assert row is not None
    assert row.extracted_text == "Jane Doe\nSoftware engineer"
    assert row.structured_data == fake_openai.result.model_dump()
    assert row.work_mode == "hybrid"
    assert row.overall_notes == "Move to the next round"


def test_answer_with_nul_is_stored_clean(client: TestClient, db_session: Session) -> None:
    response = client.post(
        URL, data={**ANSWERS, "strengths": "Clear\x00 communicator"}, files={"file": TXT_CV}
    )

    intake_id = read_events(response)[-1]["result"]["id"]
    assert db_session.get(ResumeIntake, intake_id).strengths == "Clear communicator"


def test_long_answer_counts_crlf_as_one_character(client: TestClient, db_session: Session) -> None:
    """A browser counts a textarea line break as 1 character but sends it as \\r\\n."""
    strengths = "a" * 2499 + "\r\n" + "b" * 2500  # 5,000 characters as the browser counts them

    response = client.post(URL, data={**ANSWERS, "strengths": strengths}, files={"file": TXT_CV})

    stored = db_session.get(ResumeIntake, read_events(response)[-1]["result"]["id"]).strengths
    assert stored == "a" * 2499 + "\n" + "b" * 2500


def test_request_over_10_mb_is_413(client: TestClient) -> None:
    big_cv = ("cv.txt", b"a" * (MAX_REQUEST_BYTES + 1), "text/plain")

    response = client.post(URL, data=ANSWERS, files={"file": big_cv})

    assert response.status_code == 413


def test_png_is_a_415_event(client: TestClient) -> None:
    png = ("photo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png")

    events = read_events(client.post(URL, data=ANSWERS, files={"file": png}))

    assert events[-1]["stage"] == "error"
    assert events[-1]["status"] == 415
    assert "Unsupported file type" in events[-1]["detail"]


def test_blank_pdf_is_a_422_event(client: TestClient, make_pdf: Callable[..., bytes]) -> None:
    blank_pdf = ("cv.pdf", make_pdf([""]), "application/pdf")

    events = read_events(client.post(URL, data=ANSWERS, files={"file": blank_pdf}))

    assert events[-1] == {
        "stage": "error",
        "result": None,
        "status": 422,
        "detail": SCANNED_PDF_MESSAGE,
    }


def test_missing_answer_is_422(client: TestClient) -> None:
    answers = {name: value for name, value in ANSWERS.items() if name != "concerns"}

    response = client.post(URL, data=answers, files={"file": TXT_CV})

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["body", "concerns"]]


def test_strengths_over_5000_characters_is_422(client: TestClient) -> None:
    response = client.post(URL, data={**ANSWERS, "strengths": "a" * 5001}, files={"file": TXT_CV})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "strengths"]


def test_rating_of_6_is_422(client: TestClient) -> None:
    response = client.post(URL, data={**ANSWERS, "rating": "6"}, files={"file": TXT_CV})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "rating"]


def test_database_down_is_a_503_event(client: TestClient) -> None:
    app.dependency_overrides[get_db] = FailingCommitSession

    events = read_events(client.post(URL, data=ANSWERS, files={"file": TXT_CV}))

    assert events[-1]["stage"] == "error"
    assert events[-1]["status"] == 503
    assert events[-1]["detail"] == "The database is unavailable. Try again later."


def test_request_over_10_mb_without_content_length_is_413(client: TestClient) -> None:
    """A chunked upload has no Content-Length, so the limit applies while the body streams in."""

    def chunks() -> Iterator[bytes]:
        boundary_start = (
            b'--b\r\nContent-Disposition: form-data; name="file"; filename="cv.txt"\r\n\r\n'
        )
        yield boundary_start
        for _ in range(11):
            yield b"a" * (1024 * 1024)
        yield b"\r\n--b--\r\n"

    response = client.post(
        URL, content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=b"}
    )

    assert response.status_code == 413


def test_missing_file_is_422(client: TestClient) -> None:
    response = client.post(URL, data=ANSWERS)

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["body", "file"]]


def test_unknown_work_mode_is_422(client: TestClient) -> None:
    response = client.post(URL, data={**ANSWERS, "work_mode": "Remote"}, files={"file": TXT_CV})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "work_mode"]


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("notice_period", 200),
        ("salary_expectation", 200),
        ("strengths", 5000),
        ("concerns", 5000),
        ("overall_notes", 5000),
    ],
)
def test_answer_length_limit_is_inclusive(
    client: TestClient, db_session: Session, field: str, limit: int
) -> None:
    """db_session keeps the stored row out of the real database (it is rolled back)."""
    at_limit = client.post(URL, data={**ANSWERS, field: "a" * limit}, files={"file": TXT_CV})
    over_limit = client.post(
        URL, data={**ANSWERS, field: "a" * (limit + 1)}, files={"file": TXT_CV}
    )

    assert read_events(at_limit)[-1]["stage"] == "done"
    assert over_limit.status_code == 422
    assert over_limit.json()["detail"][0]["loc"] == ["body", field]


@pytest.mark.parametrize("blank", ["   \n\t ", "\x00\x01\x02"], ids=["whitespace", "control-chars"])
def test_answer_with_nothing_left_after_cleaning_is_422(client: TestClient, blank: str) -> None:
    response = client.post(URL, data={**ANSWERS, "strengths": blank}, files={"file": TXT_CV})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "strengths"]


@pytest.mark.parametrize(
    ("filename", "data", "status"),
    [
        ("photo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", 415),
        ("scan.pdf", None, 422),
        ("empty.txt", b"", 422),
    ],
    ids=["unsupported-type", "scanned-pdf", "empty-file"],
)
def test_unreadable_cv_stops_before_the_ai_and_stores_nothing(
    client: TestClient,
    db_session: Session,
    make_pdf: Callable[..., bytes],
    fake_openai: FakeOpenAI,
    filename: str,
    data: bytes | None,
    status: int,
) -> None:
    rows_before = count_rows(db_session)

    response = client.post(
        URL, data=ANSWERS, files={"file": (filename, make_pdf([""]) if data is None else data)}
    )

    events = read_events(response)
    assert [event["stage"] for event in events] == ["reading", "error"]
    assert events[-1]["status"] == status
    assert fake_openai.calls == []
    assert count_rows(db_session) == rows_before


def test_oversized_request_stores_nothing(client: TestClient, db_session: Session) -> None:
    rows_before = count_rows(db_session)
    big_cv = ("big.txt", b"a" * (MAX_REQUEST_BYTES + 1), "text/plain")

    response = client.post(URL, data=ANSWERS, files={"file": big_cv})

    assert response.status_code == 413
    assert count_rows(db_session) == rows_before


def test_cv_without_email_is_a_422_event_and_stores_nothing(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    fake_openai.result = fake_openai.result.model_copy(update={"email": ""})
    rows_before = count_rows(db_session)

    events = read_events(client.post(URL, data=ANSWERS, files={"file": TXT_CV}))

    assert [event["stage"] for event in events] == ["reading", "extracting", "error"]
    assert events[-1]["status"] == 422
    assert events[-1]["detail"] == MISSING_CONTACT_MESSAGE
    assert count_rows(db_session) == rows_before


def test_ai_timeout_is_a_502_event_and_stores_nothing(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    fake_openai.error = openai.APITimeoutError(request=request)
    rows_before = count_rows(db_session)

    events = read_events(client.post(URL, data=ANSWERS, files={"file": TXT_CV}))

    assert [event["stage"] for event in events] == ["reading", "extracting", "error"]
    assert events[-1]["status"] == 502
    assert events[-1]["detail"] == AI_FAILED_MESSAGE
    assert count_rows(db_session) == rows_before


def test_cv_over_the_ai_text_cap_is_a_422_event_and_stores_nothing(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    """The cap is checked inside extract_resume_data, so `extracting` is sent before the error."""
    rows_before = count_rows(db_session)
    long_cv = ("long.txt", b"a" * (MAX_CV_TEXT_CHARS + 1), "text/plain")

    events = read_events(client.post(URL, data=ANSWERS, files={"file": long_cv}))

    assert [event["stage"] for event in events] == ["reading", "extracting", "error"]
    assert events[-1]["status"] == 422
    assert events[-1]["detail"] == TOO_LONG_MESSAGE
    assert fake_openai.calls == []
    assert count_rows(db_session) == rows_before


def test_cv_at_the_ai_text_cap_is_sent_to_the_ai_and_stored(
    client: TestClient, fake_openai: FakeOpenAI, db_session: Session
) -> None:
    at_cap = ("cap.txt", b"a" * MAX_CV_TEXT_CHARS, "text/plain")

    events = read_events(client.post(URL, data=ANSWERS, files={"file": at_cap}))

    assert events[-1]["stage"] == "done"
    assert len(fake_openai.calls[0]["input"]) == MAX_CV_TEXT_CHARS


def test_cv_without_a_name_is_a_422_event_and_stores_nothing(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    fake_openai.result = fake_openai.result.model_copy(update={"name": "  "})
    rows_before = count_rows(db_session)

    events = read_events(client.post(URL, data=ANSWERS, files={"file": TXT_CV}))

    assert [event["stage"] for event in events] == ["reading", "extracting", "error"]
    assert events[-1]["status"] == 422
    assert events[-1]["detail"] == MISSING_CONTACT_MESSAGE
    assert count_rows(db_session) == rows_before


@pytest.mark.parametrize(
    ("error_class", "status_code"),
    [
        (openai.AuthenticationError, 401),
        (openai.RateLimitError, 429),
        (openai.InternalServerError, 503),
        (openai.NotFoundError, 404),
    ],
    ids=["bad-key", "rate-limited", "outage", "model-gone"],
)
def test_openai_http_failure_is_a_502_event_and_stores_nothing(
    client: TestClient,
    db_session: Session,
    fake_openai: FakeOpenAI,
    error_class: type[openai.APIStatusError],
    status_code: int,
) -> None:
    fake_openai.error = make_openai_status_error(error_class, status_code)
    rows_before = count_rows(db_session)

    events = read_events(client.post(URL, data=ANSWERS, files={"file": TXT_CV}))

    assert [event["stage"] for event in events] == ["reading", "extracting", "error"]
    assert events[-1] == {
        "stage": "error",
        "result": None,
        "status": 502,
        "detail": AI_FAILED_MESSAGE,
    }
    assert count_rows(db_session) == rows_before


def test_ai_refusal_is_a_502_event_and_stores_nothing(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    fake_openai.result = None
    rows_before = count_rows(db_session)

    events = read_events(client.post(URL, data=ANSWERS, files={"file": TXT_CV}))

    assert [event["stage"] for event in events] == ["reading", "extracting", "error"]
    assert events[-1]["status"] == 502
    assert count_rows(db_session) == rows_before


def test_cut_off_ai_output_is_a_502_event_that_does_not_leak_cv_text(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    """The SDK's ValidationError message quotes the (CV-derived) JSON; the event must not."""
    with pytest.raises(pydantic.ValidationError) as invalid_json:
        ResumeData.model_validate_json('{"name": "Canary Person", "email": "canary@exa')
    fake_openai.error = invalid_json.value
    canary_cv = ("cv.txt", b"Canary Person\ncanary@example.com", "text/plain")
    rows_before = count_rows(db_session)

    response = client.post(URL, data=ANSWERS, files={"file": canary_cv})

    events = read_events(response)
    assert [event["stage"] for event in events] == ["reading", "extracting", "error"]
    assert events[-1]["status"] == 502
    assert events[-1]["detail"] == AI_FAILED_MESSAGE
    assert "canary" not in response.text.lower()
    assert count_rows(db_session) == rows_before


def test_every_event_carries_all_four_fields(client: TestClient, db_session: Session) -> None:
    success = read_events(client.post(URL, data=ANSWERS, files={"file": TXT_CV}))
    failure = read_events(client.post(URL, data=ANSWERS, files={"file": ("e.txt", b"")}))

    for event in success + failure:
        assert set(event) == {"stage", "result", "status", "detail"}
    done = success[-1]
    assert done["status"] is None
    assert done["detail"] is None
    assert set(done["result"]) == {
        *ANSWERS,
        "id",
        "original_filename",
        "file_type",
        "extracted_text",
        "structured_data",
        "created_at",
    }


def test_only_the_cv_text_is_sent_to_the_ai(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    """The interviewer's answers are about the candidate and must never reach OpenAI."""
    client.post(URL, data=ANSWERS, files={"file": TXT_CV})

    sent = json.dumps(fake_openai.calls, default=str)
    assert len(fake_openai.calls) == 1
    text_answers = [value for name, value in ANSWERS.items() if name not in ("rating", "work_mode")]
    for answer in text_answers:
        assert answer not in sent


def test_over_four_page_pdf_is_a_422_event_that_skips_the_ai(
    client: TestClient,
    db_session: Session,
    make_pdf: Callable[..., bytes],
    fake_openai: FakeOpenAI,
) -> None:
    rows_before = count_rows(db_session)
    five_pages = ("cv.pdf", make_pdf(["Jane Doe"] * 5), "application/pdf")

    events = read_events(client.post(URL, data=ANSWERS, files={"file": five_pages}))

    assert [event["stage"] for event in events] == ["reading", "error"]
    assert events[-1]["status"] == 422
    assert events[-1]["detail"] == TOO_MANY_PAGES_MESSAGE
    assert fake_openai.calls == []
    assert count_rows(db_session) == rows_before


def test_form_validation_error_is_plain_json_before_any_stream(
    client: TestClient, db_session: Session, fake_openai: FakeOpenAI
) -> None:
    rows_before = count_rows(db_session)
    answers = {name: value for name, value in ANSWERS.items() if name != "concerns"}

    response = client.post(URL, data=answers, files={"file": TXT_CV})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert "data:" not in response.text
    assert fake_openai.calls == []
    assert count_rows(db_session) == rows_before


def test_oversized_request_never_reaches_the_ai(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    big_cv = ("big.txt", b"a" * (MAX_REQUEST_BYTES + 1), "text/plain")

    response = client.post(URL, data=ANSWERS, files={"file": big_cv})

    assert response.status_code == 413
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert fake_openai.calls == []


def test_keepalive_pings_during_a_slow_ai_call_do_not_break_the_stream(
    client: TestClient,
    db_session: Session,
    fake_openai: FakeOpenAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastAPI sends `: ping` comments while a step runs; events must still come through intact."""
    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.05)

    def slow_parse(**kwargs: Any) -> SimpleNamespace:
        time.sleep(0.4)
        return SimpleNamespace(output_parsed=fake_openai.result)

    monkeypatch.setattr(fake_openai.responses, "parse", slow_parse)

    response = client.post(URL, data=ANSWERS, files={"file": TXT_CV})

    assert ": ping" in response.text
    events = read_events(response)
    assert [event["stage"] for event in events] == ["reading", "extracting", "done"]
    assert events[-1]["result"]["structured_data"] == fake_openai.result.model_dump()


# --- GET: the past-interviews list and one stored intake ---


class FailingQuerySession:
    """Stand-in session whose reads fail the way they do when Postgres is down."""

    def execute(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT", {}, ConnectionRefusedError("connection refused"))

    get = execute


def add_intake(db_session: Session, name: str | None) -> ResumeIntake:
    """Store an intake directly. name=None stores no AI data, like rows from before AI."""
    structured_data = None
    if name is not None:
        email = f"{name.split()[0].lower()}@example.com"
        structured_data = FAKE_RESUME_DATA.model_copy(update={"name": name, "email": email})
    intake = ResumeIntake(
        original_filename=f"{name or 'old'}.txt",
        file_type="txt",
        extracted_text="CV text",
        structured_data=structured_data and structured_data.model_dump(),
        notice_period="2 weeks",
        salary_expectation="$60k/year",
        work_mode="onsite",
        rating=3,
        strengths="Clear communicator",
        concerns="Little cloud experience",
        overall_notes="Move to the next round",
    )
    db_session.add(intake)
    db_session.flush()
    return intake


def test_list_is_newest_first_and_pages_by_id(client: TestClient, db_session: Session) -> None:
    """Rows already in the dev database are older (smaller ids), so they never come first."""
    old = add_intake(db_session, None)
    ann = add_intake(db_session, "Ann Lee")
    bo = add_intake(db_session, "Bo Chan")

    first_page = client.get(URL, params={"limit": 2})
    assert first_page.status_code == 200
    body = first_page.json()
    assert [item["id"] for item in body["items"]] == [bo.id, ann.id]
    assert body["has_more"] is True
    newest = {key: value for key, value in body["items"][0].items() if key != "created_at"}
    assert newest == {
        "id": bo.id,
        "original_filename": "Bo Chan.txt",
        "candidate_name": "Bo Chan",
        "candidate_email": "bo@example.com",
        "rating": 3,
        "work_mode": "onsite",
    }

    next_page = client.get(URL, params={"limit": 2, "before_id": ann.id}).json()
    assert next_page["items"][0]["id"] == old.id
    assert next_page["items"][0]["candidate_name"] is None
    assert next_page["items"][0]["candidate_email"] is None


def test_list_before_the_first_id_is_empty(client: TestClient, db_session: Session) -> None:
    assert client.get(URL, params={"before_id": 1}).json() == {"items": [], "has_more": False}


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"before_id": 0}, {"limit": "many"}]
)
def test_list_rejects_bad_paging(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get(URL, params=params).status_code == 422


def test_one_intake_is_returned_like_the_upload_result(
    client: TestClient, db_session: Session
) -> None:
    intake = add_intake(db_session, "Ann Lee")

    response = client.get(f"{URL}/{intake.id}")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(ResumeIntakeResponse.model_fields)
    assert body["id"] == intake.id
    assert body["extracted_text"] == "CV text"
    assert body["structured_data"]["name"] == "Ann Lee"
    assert body["work_mode"] == "onsite"


def test_unknown_intake_is_404(client: TestClient, db_session: Session) -> None:
    response = client.get(f"{URL}/999999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Intake not found."}


@pytest.mark.parametrize("path", ["", "/1"])
def test_reads_with_the_database_down_are_503(client: TestClient, path: str) -> None:
    app.dependency_overrides[get_db] = FailingQuerySession

    response = client.get(URL + path)

    assert response.status_code == 503
    assert response.json() == {"detail": "The database is unavailable. Try again later."}
