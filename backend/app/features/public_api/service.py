"""Public API business logic: store and read the AI-extracted details of a resume under the
caller's (candidate_id, resume_id) pair, and download a CV from a caller's public https URL.
"""

import ipaddress
import socket
from time import monotonic

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.features.ai_usage.service import AiCall, add_ai_calls
from app.features.public_api.models import PublicResume
from app.features.resume_structuring.service import ResumeData


def save_public_resume(
    db: Session, candidate_id: str, resume_id: str, data: ResumeData, ai_calls: list[AiCall]
) -> None:
    """Store the details under the pair, replacing what a repeat pair stored before, and the
    upload's AI calls (unlinked: ai_calls only links to resume_intakes), in one commit.
    """
    statement = insert(PublicResume).values(
        candidate_id=candidate_id, resume_id=resume_id, structured_data=data.model_dump()
    )
    statement = statement.on_conflict_do_update(
        index_elements=[PublicResume.candidate_id, PublicResume.resume_id],
        set_={"structured_data": statement.excluded.structured_data, "updated_at": func.now()},
    )
    db.execute(statement)
    add_ai_calls(db, ai_calls, None)
    db.commit()


def get_public_resume(db: Session, candidate_id: str, resume_id: str) -> PublicResume | None:
    """The stored row for the pair, or None."""
    return db.scalar(
        select(PublicResume).where(
            PublicResume.candidate_id == candidate_id, PublicResume.resume_id == resume_id
        )
    )


# The same 10 MB as an upload (MAX_REQUEST_BYTES in app/main.py, which can't be imported here).
MAX_CV_DOWNLOAD_BYTES = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 10  # per connect, read or write
DOWNLOAD_DEADLINE_SECONDS = 30  # for the whole body
BAD_URL_MESSAGE = "file_url must be a public https URL without a user name, password or port."
TOO_LARGE_MESSAGE = "The CV at file_url is larger than 10 MB."


class CvDownloadError(Exception):
    """The CV at file_url couldn't be downloaded. `status_code` is 413 when it is over 10 MB,
    else 422. The message never holds the URL (presigned links carry tokens), only its host.
    """

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_public_ip(host: str) -> str:
    """The host's first IPv4 address, after checking that every address it resolves to is public.

    Raises CvDownloadError (422) when it doesn't resolve or any address is internal (loopback,
    private, link-local such as cloud metadata, shared, unspecified or multicast).
    """
    try:
        addresses = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise CvDownloadError(f"Could not find the host {host}.") from None
    ips = [ipaddress.ip_address(sockaddr[0]) for *_, sockaddr in addresses]
    if not ips:
        raise CvDownloadError(f"Could not find the host {host}.")
    if any(not ip.is_global or ip.is_multicast for ip in ips):
        raise CvDownloadError(f"The host {host} is not a public address.")
    return str(ips[0])


def download_cv(url: str) -> bytes:
    """Download the CV at a public https URL (port 443, no redirects, at most 10 MB in 30 s).

    The host is resolved and checked once, and the request goes to that checked IP (with the real
    Host header and TLS server name), so a second DNS answer can't point it at an internal
    address. Raises CvDownloadError: 422 for any failure, 413 when the file is over 10 MB.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise CvDownloadError(BAD_URL_MESSAGE) from None
    if parsed.scheme != "https" or not parsed.raw_host or parsed.userinfo or parsed.port:
        # httpx drops the default port, so an explicit :443 reads as None here.
        raise CvDownloadError(BAD_URL_MESSAGE)
    host = parsed.raw_host.decode("ascii")  # the IDNA form, for DNS and TLS
    pinned = parsed.copy_with(host=resolve_public_ip(host))
    deadline = monotonic() + DOWNLOAD_DEADLINE_SECONDS
    try:
        # A new client per download: a pooled connection verified for another host is never reused.
        with (
            httpx.Client(
                trust_env=False, follow_redirects=False, timeout=DOWNLOAD_TIMEOUT_SECONDS
            ) as client,
            client.stream(
                "GET",
                pinned,
                headers={"Host": parsed.netloc.decode("ascii"), "Accept-Encoding": "identity"},
                extensions={"sni_hostname": host},
            ) as response,
        ):
            if 300 <= response.status_code < 400:
                raise CvDownloadError(
                    f"{host} answered {response.status_code} (a redirect). "
                    "Send the final URL of the CV."
                )
            if response.status_code != 200:
                raise CvDownloadError(f"{host} answered {response.status_code}.")
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > MAX_CV_DOWNLOAD_BYTES:
                raise CvDownloadError(TOO_LARGE_MESSAGE, 413)
            body = bytearray()
            # iter_raw: no decompression (Accept-Encoding: identity), so no decompression bomb.
            for chunk in response.iter_raw():
                body += chunk
                if len(body) > MAX_CV_DOWNLOAD_BYTES:
                    raise CvDownloadError(TOO_LARGE_MESSAGE, 413)
                if monotonic() > deadline:
                    raise CvDownloadError(f"The download from {host} took too long.")
    except httpx.HTTPError:
        # The error's message holds the URL: never chain or log it.
        raise CvDownloadError(f"Could not download the CV from {host}.") from None
    return bytes(body)
