"""Tests for public_api.service: storing, replacing and reading a pair's details (needs Postgres;
db_session rolls every write back), and download_cv (fake DNS and web, no network).
"""

import socket

import httpx
import pytest
from sqlalchemy.orm import Session

from app.features.ai_usage.service import AiCall, record_ai_call
from app.features.public_api import service
from app.features.public_api.service import (
    BAD_URL_MESSAGE,
    DOWNLOAD_DEADLINE_SECONDS,
    DOWNLOAD_TIMEOUT_SECONDS,
    MAX_CV_DOWNLOAD_BYTES,
    TOO_LARGE_MESSAGE,
    CvDownloadError,
    download_cv,
    get_public_resume,
    save_public_resume,
)
from app.features.resume_structuring.service import OPENAI_MODEL
from tests.conftest import FAKE_RESUME_DATA, FAKE_USAGE
from tests.features.public_api.conftest import CV_BYTES, CV_HOST, CV_HOST_IP, CV_URL, FakeWeb


def test_a_saved_pair_is_read_back_with_its_ai_calls_unlinked(db_session: Session) -> None:
    ai_calls: list[AiCall] = []
    record_ai_call(ai_calls, OPENAI_MODEL, "structuring", FAKE_USAGE)

    save_public_resume(db_session, "cand-1", "res-1", FAKE_RESUME_DATA, ai_calls)

    row = get_public_resume(db_session, "cand-1", "res-1")
    assert row is not None
    assert row.structured_data == FAKE_RESUME_DATA.model_dump()
    assert ai_calls[0].id is not None
    assert ai_calls[0].resume_intake_id is None


def test_a_repeat_pair_replaces_and_other_pairs_are_kept(db_session: Session) -> None:
    renamed = FAKE_RESUME_DATA.model_copy(update={"name": "Jane Q. Doe"})
    save_public_resume(db_session, "cand-1", "res-1", FAKE_RESUME_DATA, [])
    save_public_resume(db_session, "cand-1", "res-2", FAKE_RESUME_DATA, [])

    save_public_resume(db_session, "cand-1", "res-1", renamed, [])

    db_session.expire_all()
    assert get_public_resume(db_session, "cand-1", "res-1").structured_data["name"] == "Jane Q. Doe"
    assert get_public_resume(db_session, "cand-1", "res-2").structured_data["name"] == "Jane Doe"


def test_an_unknown_pair_is_none(db_session: Session) -> None:
    assert get_public_resume(db_session, "nobody", "none") is None


# --- download_cv (fake DNS and web through the fake_web fixture; no network) ---


def download_error(url: str) -> CvDownloadError:
    """The CvDownloadError download_cv raises for `url`; its message must never hold the URL."""
    with pytest.raises(CvDownloadError) as caught:
        download_cv(url)
    assert "secret-token" not in str(caught.value)
    assert "/files/" not in str(caught.value)
    return caught.value


def test_a_cv_is_downloaded_from_the_checked_ip_with_the_real_host_name(fake_web: FakeWeb) -> None:
    assert download_cv(CV_URL) == CV_BYTES

    assert fake_web.lookups == [(CV_HOST, 443, socket.AF_INET, socket.SOCK_STREAM)]
    [request] = fake_web.requests
    assert str(request.url) == f"https://{CV_HOST_IP}/files/cv.txt?token=secret-token"
    assert request.headers["Host"] == CV_HOST
    assert request.headers["Accept-Encoding"] == "identity"
    assert request.extensions["sni_hostname"] == CV_HOST
    [kwargs] = fake_web.client_kwargs
    assert kwargs["trust_env"] is False
    assert kwargs["follow_redirects"] is False
    assert kwargs["timeout"] == DOWNLOAD_TIMEOUT_SECONDS


def test_an_explicit_port_443_is_accepted(fake_web: FakeWeb) -> None:
    assert download_cv(f"https://{CV_HOST}:443/cv.txt") == CV_BYTES


def test_a_presigned_s3_url_is_sent_byte_for_byte(fake_web: FakeWeb) -> None:
    # Loxo's CVs are presigned S3 links: any re-encoding of the path or query breaks the signature.
    host = "loxo-resumes.s3.amazonaws.com"
    fake_web.addresses[host] = [CV_HOST_IP]
    target = (
        "/agency/people/123/John%20O%27Neil%20R%C3%A9sum%C3%A9%2Bv2.pdf"
        "?response-content-disposition=attachment%3B%20filename%3D%22cv.pdf%22"
        "&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIAFAKE%2F20261104%2Fus-east-1%2Fs3"
        "%2Faws4_request&X-Amz-Date=20261104T120000Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host"
        "&X-Amz-Security-Token="
        + "IQoJb3JpZ2luX2VjE%2B%2F%3D" * 60
        + "&X-Amz-Signature="
        + "a" * 64
    )

    assert download_cv(f"https://{host}{target}") == CV_BYTES

    [request] = fake_web.requests
    assert request.url.raw_path.decode() == target
    assert request.headers["Host"] == host


def test_an_international_host_name_is_looked_up_and_sent_in_its_ascii_form(
    fake_web: FakeWeb,
) -> None:
    fake_web.addresses["xn--bcher-kva.example"] = [CV_HOST_IP]

    assert download_cv("https://bücher.example/cv.txt") == CV_BYTES

    [request] = fake_web.requests
    assert request.headers["Host"] == "xn--bcher-kva.example"
    assert request.extensions["sni_hostname"] == "xn--bcher-kva.example"


def test_a_new_client_is_built_for_every_download(fake_web: FakeWeb) -> None:
    download_cv(CV_URL)
    download_cv(CV_URL)

    assert len(fake_web.client_kwargs) == 2


@pytest.mark.parametrize(
    "url",
    [
        f"http://{CV_HOST}/files/cv.txt?token=secret-token",
        f"ftp://{CV_HOST}/files/cv.txt",
        "file:///etc/passwd",
        f"https://user:secret-token@{CV_HOST}/files/cv.txt",
        f"https://{CV_HOST}:8443/files/cv.txt",
        f"https://{CV_HOST}:80/files/cv.txt",
        "https:///files/cv.txt",
        "not a url",
        f"https://{CV_HOST}:abc/files/cv.txt?token=secret-token",  # httpx.InvalidURL
        "https://[::1/files/",  # httpx.InvalidURL
    ],
)
def test_a_url_that_is_not_plain_https_on_port_443_is_422_without_a_lookup(
    fake_web: FakeWeb, url: str
) -> None:
    error = download_error(url)

    assert (error.status_code, str(error)) == (422, BAD_URL_MESSAGE)
    assert fake_web.lookups == []
    assert fake_web.requests == []


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "::ffff:127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "10.0.0.1",
        "192.168.1.1",
        "224.0.0.1",
    ],
)
def test_a_host_on_an_internal_address_is_422_without_a_request(fake_web: FakeWeb, ip: str) -> None:
    fake_web.addresses[CV_HOST] = [ip]

    error = download_error(CV_URL)

    assert error.status_code == 422
    assert str(error) == f"The host {CV_HOST} is not a public address."
    assert fake_web.requests == []


def test_a_host_with_one_internal_address_among_public_ones_is_422(fake_web: FakeWeb) -> None:
    fake_web.addresses[CV_HOST] = [CV_HOST_IP, "10.0.0.1"]

    assert download_error(CV_URL).status_code == 422
    assert fake_web.requests == []


def test_localhost_is_422(fake_web: FakeWeb) -> None:
    fake_web.addresses["localhost"] = ["127.0.0.1"]

    assert download_error("https://localhost/cv.txt").status_code == 422
    assert fake_web.requests == []


def test_an_ip_address_url_is_checked_like_a_host_name(fake_web: FakeWeb) -> None:
    fake_web.addresses["127.0.0.1"] = ["127.0.0.1"]

    assert download_error("https://127.0.0.1/cv.txt").status_code == 422
    assert fake_web.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        None,  # gaierror
        UnicodeError("label too long"),
        [],  # resolves to nothing
    ],
)
def test_a_host_that_does_not_resolve_is_422(fake_web: FakeWeb, answer: object) -> None:
    if answer is None:
        del fake_web.addresses[CV_HOST]
    else:
        fake_web.addresses[CV_HOST] = answer

    error = download_error(CV_URL)

    assert (error.status_code, str(error)) == (422, f"Could not find the host {CV_HOST}.")
    assert fake_web.requests == []


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
def test_a_redirect_is_422_and_not_followed(fake_web: FakeWeb, status_code: int) -> None:
    fake_web.handler = lambda request: httpx.Response(
        status_code, headers={"Location": "https://10.0.0.1/"}
    )

    error = download_error(CV_URL)

    assert error.status_code == 422
    assert "Send the final URL of the CV." in str(error)
    assert len(fake_web.requests) == 1


@pytest.mark.parametrize("status_code", [204, 403, 404, 500, 503])
def test_any_status_other_than_200_is_422(fake_web: FakeWeb, status_code: int) -> None:
    fake_web.handler = lambda request: httpx.Response(status_code)

    error = download_error(CV_URL)

    assert (error.status_code, str(error)) == (422, f"{CV_HOST} answered {status_code}.")


def test_a_content_length_over_10_mb_is_413(fake_web: FakeWeb) -> None:
    fake_web.handler = lambda request: httpx.Response(
        200, headers={"Content-Length": str(MAX_CV_DOWNLOAD_BYTES + 1)}, content=iter([b"a"])
    )

    error = download_error(CV_URL)

    assert (error.status_code, str(error)) == (413, TOO_LARGE_MESSAGE)


def test_a_streamed_body_over_10_mb_is_413(fake_web: FakeWeb) -> None:
    chunk = b"a" * (1024 * 1024)
    # An iterator body has no Content-Length, so only the read loop can stop it.
    fake_web.handler = lambda request: httpx.Response(200, content=iter([chunk] * 11))

    error = download_error(CV_URL)

    assert (error.status_code, str(error)) == (413, TOO_LARGE_MESSAGE)


def test_a_body_of_exactly_10_mb_is_accepted(fake_web: FakeWeb) -> None:
    fake_web.handler = lambda request: httpx.Response(
        200, content=iter([b"a" * MAX_CV_DOWNLOAD_BYTES])
    )

    assert len(download_cv(CV_URL)) == MAX_CV_DOWNLOAD_BYTES


def test_a_download_past_the_30_second_deadline_is_422(
    fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter([0.0, 10.0, DOWNLOAD_DEADLINE_SECONDS + 1.0])
    monkeypatch.setattr(service, "monotonic", lambda: next(clock))
    fake_web.handler = lambda request: httpx.Response(200, content=iter([b"Jane ", b"Doe"]))

    error = download_error(CV_URL)

    assert (error.status_code, str(error)) == (422, f"The download from {CV_HOST} took too long.")


@pytest.mark.parametrize(
    "exception",
    [httpx.ConnectTimeout("timed out"), httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
)
def test_a_connection_failure_is_422(fake_web: FakeWeb, exception: Exception) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise exception

    fake_web.handler = fail

    error = download_error(CV_URL)

    assert (error.status_code, str(error)) == (422, f"Could not download the CV from {CV_HOST}.")
    assert error.__suppress_context__
