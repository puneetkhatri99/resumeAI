"""Candidates business logic: store a candidate's two files in Neon Object Storage (S3-compatible)
and its row in Postgres, list candidates with links to their files, and delete them.

The original CV goes to the private RESUME_BUCKET and is shown through a presigned link that
expires after 1 hour; the branded PDF goes to the public PUBLIC_RESUME_BUCKET and is shown
through its permanent public URL. Keys are random (`candidates/{uuid4}/...`), so they hold no
personal data and can't be guessed. Never logged here: CV content, names or presigned URLs;
storage errors log the error code only. public_api uses store_candidate for POST /api/public/pdf.
"""

import logging
import uuid
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.features.ai_usage.service import AiCall, add_ai_calls, total_cost
from app.features.candidates.models import Candidate
from app.features.candidates.schemas import Source

logger = logging.getLogger(__name__)

ORIGINAL_URL_EXPIRY_SECONDS = 3600
STORE_FAILED_MESSAGE = "Could not store the resume files. Try again."
DELETE_FAILED_MESSAGE = "Could not delete the resume files. Try again."
# The original's Content-Type by file type. It is always sent as an attachment: an HTML file
# saved as .doc counts as "txt" and must never render in a browser.
ORIGINAL_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "txt": "text/plain",
}

# Neon is path-style only. Bounded timeouts and 2 attempts, so a storage outage fails the
# upload in about a minute instead of hanging it. No ACL, encryption or policy parameters are
# ever sent: Neon answers 501 to them (access is set per bucket in the Neon Console).
s3 = boto3.client(
    "s3",
    endpoint_url=settings.aws_endpoint_url_s3,
    region_name=settings.aws_region,
    aws_access_key_id=settings.aws_access_key_id.get_secret_value(),
    aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
    config=Config(
        s3={"addressing_style": "path"},
        connect_timeout=5,
        read_timeout=30,
        retries={"mode": "standard", "max_attempts": 2},
    ),
)


class FileStorageError(Exception):
    """Object storage failed or timed out (HTTP 502)."""


def store_candidate(
    db: Session,
    original: bytes,
    file_type: str,
    pdf: bytes,
    name: str,
    source: Source,
    ai_calls: list[AiCall],
) -> Candidate:
    """Store the original CV (private bucket) and the branded PDF (public bucket), then the
    candidate row with its AI cost and the upload's AI calls (unlinked), in one commit.

    Storage is written before the row, so a row never points at a missing file. On a failed
    PUT or commit the objects already written are deleted (best effort), and FileStorageError
    or the SQLAlchemyError is raised. If that cleanup fails too, the orphan's key (no personal
    data) is logged.
    """
    prefix = f"candidates/{uuid.uuid4()}"
    original_key = f"{prefix}/original.{file_type}"
    resume_key = f"{prefix}/resume.pdf"
    written: list[tuple[str, str]] = []
    try:
        s3.put_object(
            Bucket=settings.resume_bucket,
            Key=original_key,
            Body=original,
            ContentType=ORIGINAL_CONTENT_TYPES[file_type],
            ContentDisposition="attachment",
            CacheControl="no-store",
        )
        written.append((settings.resume_bucket, original_key))
        s3.put_object(
            Bucket=settings.public_resume_bucket,
            Key=resume_key,
            Body=pdf,
            ContentType="application/pdf",
            ContentDisposition=pdf_content_disposition(name),
        )
        written.append((settings.public_resume_bucket, resume_key))
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Could not store a candidate file: %s", storage_error_code(exc))
        delete_objects_quietly(written)
        raise FileStorageError(STORE_FAILED_MESSAGE) from None

    candidate = Candidate(
        name=name,
        source=source,
        file_type=file_type,
        original_key=original_key,
        resume_key=resume_key,
        ai_cost_usd=total_cost(ai_calls),
    )
    try:
        db.add(candidate)
        add_ai_calls(db, ai_calls, None)
        db.commit()
    except SQLAlchemyError:
        delete_objects_quietly(written)
        raise
    return candidate


def list_candidates(db: Session, before_id: int | None, limit: int) -> tuple[list[Candidate], bool]:
    """Return up to `limit` candidates, newest first, and whether older ones exist.

    With `before_id`, only candidates with a smaller id are listed (paging by id, like the
    past-interviews list).
    """
    query = select(Candidate).order_by(Candidate.id.desc()).limit(limit + 1)
    if before_id is not None:
        query = query.where(Candidate.id < before_id)
    rows = list(db.scalars(query))
    return rows[:limit], len(rows) > limit


def delete_candidate(db: Session, candidate_id: int) -> bool:
    """Delete the candidate's two objects, then its row. False when there is no such candidate.

    Objects first: a failure leaves a visible row and a safe retry (deleting a missing key
    succeeds in S3), not hidden orphans. Raises FileStorageError when an object delete fails
    (the row is kept) and SQLAlchemyError when the database does. Neon's DeleteObject is a soft
    delete: the object stays recoverable through Neon's branch history until it expires.
    """
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        return False
    try:
        s3.delete_object(Bucket=settings.resume_bucket, Key=candidate.original_key)
        s3.delete_object(Bucket=settings.public_resume_bucket, Key=candidate.resume_key)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Could not delete a candidate file: %s", storage_error_code(exc))
        raise FileStorageError(DELETE_FAILED_MESSAGE) from None
    db.delete(candidate)
    db.commit()
    return True


def original_url(key: str) -> str:
    """A presigned GET for the original CV in the private bucket, valid for 1 hour.

    Signed locally (no request is made). Never log the result: it grants access.
    """
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.resume_bucket, "Key": key},
        ExpiresIn=ORIGINAL_URL_EXPIRY_SECONDS,
    )


def public_url(key: str) -> str:
    """The branded PDF's permanent public URL (path-style; the UUID key needs no encoding)."""
    return f"{settings.aws_endpoint_url_s3.rstrip('/')}/{settings.public_resume_bucket}/{key}"


def pdf_content_disposition(name: str) -> str:
    """`inline` with the file name "MinistryHub - <name>.pdf", percent-encoded.

    The name is AI output, so it is always encoded (also /, \\, quotes, CR and LF). public_api
    sends the same header with its PDFs.
    """
    return f"inline; filename*=UTF-8''{quote(f'MinistryHub - {name}.pdf', safe='')}"


def delete_objects_quietly(objects: list[tuple[str, str]]) -> None:
    """Best-effort cleanup of objects written for a failed save; logs any orphan's key."""
    for bucket, key in objects:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            logger.error("Orphaned object %s left in storage: %s", key, storage_error_code(exc))


def storage_error_code(exc: BotoCoreError | ClientError) -> str:
    """The S3 error code (e.g. "AccessDenied"), or the exception type for a network error."""
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "ClientError"))
    return type(exc).__name__
