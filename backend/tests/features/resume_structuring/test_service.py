"""Tests for extract_resume_data and transcribe_pdf. OpenAI is replaced by the autouse
fake_openai fixture.
"""

import base64

import httpx2
import openai
import pydantic
import pytest

from app.features.ai_usage.models import AiCall
from app.features.resume_structuring import service as resume_structuring_service
from app.features.resume_structuring.schemas import ResumeData
from app.features.resume_structuring.service import (
    AI_FAILED_MESSAGE,
    INSTRUCTIONS,
    MAX_CV_TEXT_CHARS,
    MISSING_CONTACT_MESSAGE,
    OCR_INSTRUCTIONS,
    OPENAI_MODEL,
    AiExtractionError,
    MissingContactError,
    ResumeStructuringError,
    extract_resume_data,
    transcribe_pdf,
)
from tests.conftest import FAKE_CALL_COST, FAKE_OCR_TEXT, FakeOpenAI, make_openai_status_error

CV_TEXT = "Jane Doe\njane.doe@example.com\nSoftware engineer at Acme Corp"


def test_returns_the_parsed_result_and_sends_the_text_with_store_off(
    fake_openai: FakeOpenAI,
) -> None:
    assert extract_resume_data(CV_TEXT, []) == fake_openai.result
    assert fake_openai.calls == [
        {
            "model": OPENAI_MODEL,
            "instructions": INSTRUCTIONS,
            "input": CV_TEXT,
            "text_format": ResumeData,
            "temperature": 0,
            "store": False,
        }
    ]


@pytest.mark.parametrize("field", ["name", "email"])
@pytest.mark.parametrize("blank", ["", "  "], ids=["empty", "spaces"])
def test_blank_name_or_email_is_rejected(fake_openai: FakeOpenAI, field: str, blank: str) -> None:
    fake_openai.result = fake_openai.result.model_copy(update={field: blank})

    with pytest.raises(MissingContactError, match=MISSING_CONTACT_MESSAGE):
        extract_resume_data(CV_TEXT, [])


def test_text_at_the_limit_is_sent(fake_openai: FakeOpenAI) -> None:
    extract_resume_data("a" * MAX_CV_TEXT_CHARS, [])

    assert len(fake_openai.calls) == 1


def test_text_over_the_limit_is_rejected_before_any_ai_call(fake_openai: FakeOpenAI) -> None:
    with pytest.raises(ResumeStructuringError) as caught:
        extract_resume_data("a" * (MAX_CV_TEXT_CHARS + 1), [])

    assert str(caught.value) == "The CV text is too long (over 50,000 characters)."
    # resume_intake retries with OCR only on MissingContactError; a too-long CV must not.
    assert not isinstance(caught.value, MissingContactError)
    assert fake_openai.calls == []


def test_timeout_becomes_ai_extraction_error(fake_openai: FakeOpenAI) -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    fake_openai.error = openai.APITimeoutError(request=request)

    with pytest.raises(AiExtractionError, match=AI_FAILED_MESSAGE):
        extract_resume_data(CV_TEXT, [])


def test_cut_off_output_becomes_ai_extraction_error_without_logging_cv_text(
    fake_openai: FakeOpenAI, caplog: pytest.LogCaptureFixture
) -> None:
    """Truncated output is invalid JSON; the SDK's ValidationError message contains CV text."""
    with pytest.raises(pydantic.ValidationError) as invalid_json:
        ResumeData.model_validate_json('{"name": "Jane Doe", "email": "jane.doe@exa')
    fake_openai.error = invalid_json.value

    with pytest.raises(AiExtractionError, match=AI_FAILED_MESSAGE) as caught:
        extract_resume_data(CV_TEXT, [])

    assert caught.value.__suppress_context__  # `from None`: tracebacks don't show the CV text
    assert "ValidationError" in caplog.text
    assert "jane.doe" not in caplog.text


def test_refusal_without_parsed_output_becomes_ai_extraction_error(
    fake_openai: FakeOpenAI,
) -> None:
    fake_openai.result = None

    with pytest.raises(AiExtractionError, match=AI_FAILED_MESSAGE):
        extract_resume_data(CV_TEXT, [])


@pytest.mark.parametrize(
    ("error_class", "status_code"),
    [
        (openai.AuthenticationError, 401),
        (openai.PermissionDeniedError, 403),
        (openai.NotFoundError, 404),
        (openai.RateLimitError, 429),
        (openai.InternalServerError, 500),
        (openai.InternalServerError, 503),
    ],
    ids=["bad-key", "forbidden", "model-gone", "rate-limited", "outage-500", "outage-503"],
)
def test_http_failures_from_openai_become_ai_extraction_error(
    fake_openai: FakeOpenAI, error_class: type[openai.APIStatusError], status_code: int
) -> None:
    fake_openai.error = make_openai_status_error(error_class, status_code)

    with pytest.raises(AiExtractionError, match=AI_FAILED_MESSAGE):
        extract_resume_data(CV_TEXT, [])


@pytest.mark.parametrize(
    "error",
    [
        openai.APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com")),
        openai.ContentFilterFinishReasonError(),
    ],
    ids=["connection-error", "content-filter"],
)
def test_other_openai_errors_become_ai_extraction_error(
    fake_openai: FakeOpenAI, error: Exception
) -> None:
    fake_openai.error = error

    with pytest.raises(AiExtractionError, match=AI_FAILED_MESSAGE):
        extract_resume_data(CV_TEXT, [])


def test_ai_extraction_error_hides_the_sdk_error_and_logs_only_its_type(
    fake_openai: FakeOpenAI, caplog: pytest.LogCaptureFixture
) -> None:
    fake_openai.error = make_openai_status_error(openai.AuthenticationError, 401)

    with pytest.raises(AiExtractionError) as caught:
        extract_resume_data(CV_TEXT, [])

    assert str(caught.value) == AI_FAILED_MESSAGE
    assert caught.value.__suppress_context__
    assert "AuthenticationError" in caplog.text
    assert "jane.doe" not in caplog.text


def test_transcribe_pdf_sends_the_file_as_base64_under_a_fixed_name_with_store_off(
    fake_openai: FakeOpenAI,
) -> None:
    pdf = b"%PDF-1.4 synthetic scan"

    assert transcribe_pdf(pdf, [], "ocr") == FAKE_OCR_TEXT
    assert fake_openai.ocr_calls == [
        {
            "model": OPENAI_MODEL,
            "instructions": OCR_INSTRUCTIONS,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": "cv.pdf",
                            "file_data": "data:application/pdf;base64,"
                            + base64.b64encode(pdf).decode(),
                        }
                    ],
                }
            ],
            "temperature": 0,
            "store": False,
        }
    ]
    assert fake_openai.calls == []


@pytest.mark.parametrize(
    "error",
    [
        openai.APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com")),
        make_openai_status_error(openai.AuthenticationError, 401),
        make_openai_status_error(openai.BadRequestError, 400),
    ],
    ids=["timeout", "bad-key", "bad-request"],
)
def test_transcribe_pdf_failure_becomes_ai_extraction_error_and_logs_only_its_type(
    fake_openai: FakeOpenAI, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    fake_openai.ocr_error = error

    with pytest.raises(AiExtractionError, match=AI_FAILED_MESSAGE) as caught:
        transcribe_pdf(b"%PDF-1.4 synthetic scan", [], "ocr")

    assert caught.value.__suppress_context__
    assert caplog.messages == [f"OCR failed: {type(error).__name__}"]


def test_transcribe_pdf_cut_short_becomes_ai_extraction_error(
    fake_openai: FakeOpenAI, caplog: pytest.LogCaptureFixture
) -> None:
    fake_openai.ocr_text = "Jane Doe\njane.doe@exa"
    fake_openai.ocr_status = "incomplete"

    with pytest.raises(AiExtractionError, match=AI_FAILED_MESSAGE):
        transcribe_pdf(b"%PDF-1.4 synthetic scan", [], "ocr")

    assert caplog.messages == ["OCR failed: response status incomplete"]


def test_an_answered_extraction_is_recorded_even_when_it_is_a_refusal(
    fake_openai: FakeOpenAI,
) -> None:
    """OpenAI bills a refusal, so it is recorded before the answer is checked."""
    fake_openai.result = None
    ai_calls: list[AiCall] = []

    with pytest.raises(AiExtractionError):
        extract_resume_data(CV_TEXT, ai_calls)

    recorded = [(call.model, call.call_type, call.cost_usd) for call in ai_calls]
    assert recorded == [(OPENAI_MODEL, "structuring", FAKE_CALL_COST)]


def test_a_cut_short_ocr_answer_is_recorded_because_openai_bills_it(
    fake_openai: FakeOpenAI,
) -> None:
    fake_openai.ocr_status = "incomplete"
    ai_calls: list[AiCall] = []

    with pytest.raises(AiExtractionError):
        transcribe_pdf(b"%PDF-1.4 synthetic scan", ai_calls, "ocr_retry")

    assert [(call.call_type, call.cost_usd) for call in ai_calls] == [("ocr_retry", FAKE_CALL_COST)]


def test_a_call_that_gets_no_answer_is_not_recorded(fake_openai: FakeOpenAI) -> None:
    """No response, no usage: the known undercount (OpenAI's billing is the source of truth)."""
    fake_openai.error = openai.APITimeoutError(request=httpx2.Request("POST", "https://x"))
    ai_calls: list[AiCall] = []

    with pytest.raises(AiExtractionError):
        extract_resume_data(CV_TEXT, ai_calls)

    assert ai_calls == []


def test_the_real_client_never_retries_and_times_out_after_120_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry would spend a paid request; the SDK default timeout is 600 s (design: 120 s).

    Undoes the autouse fake only to read the real client's settings; no request is made.
    """
    monkeypatch.undo()

    assert resume_structuring_service.client.max_retries == 0
    assert resume_structuring_service.client.timeout == 120
