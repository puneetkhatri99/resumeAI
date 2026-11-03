"""Tests for the candidates service helpers that need no database or storage."""

import pytest

from app.core.config import settings
from app.features.candidates.service import pdf_content_disposition, public_url


def test_the_pdf_file_name_is_always_percent_encoded() -> None:
    """The name is AI output: slashes, quotes, CR and LF never reach the header raw."""
    header = pdf_content_disposition('Jane "/\\\r\nDoe')

    assert header == "inline; filename*=UTF-8''MinistryHub%20-%20Jane%20%22%2F%5C%0D%0ADoe.pdf"


def test_a_long_name_is_cut_in_the_pdf_file_name() -> None:
    header = pdf_content_disposition("a" * 2000)

    assert header == f"inline; filename*=UTF-8''MinistryHub%20-%20{'a' * 100}.pdf"


def test_the_public_url_is_path_style_without_a_double_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "aws_endpoint_url_s3", "https://storage.example.com/")
    monkeypatch.setattr(settings, "public_resume_bucket", "resumes")

    assert public_url("candidates/abc/resume.pdf") == (
        "https://storage.example.com/resumes/candidates/abc/resume.pdf"
    )
