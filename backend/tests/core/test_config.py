"""Tests for app.core.config.Settings: which settings are required to start the app, and the
defaults of the optional ones.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings

DATABASE_URL = "postgresql+psycopg://user:password@127.0.0.1:5432/example"
# The storage settings (candidates) are required too; fake values, never used to connect.
STORAGE = {
    "aws_endpoint_url_s3": "https://storage.example.com",
    "aws_access_key_id": "test-key-id",
    "aws_secret_access_key": "test-secret",
    "aws_region": "us-east-2",
    "resume_bucket": "originals",
    "public_resume_bucket": "resumes",
}


def test_openai_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var and no .env file: the key is reported missing, so the app can't start."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url=DATABASE_URL, **STORAGE)

    assert [error["loc"] for error in caught.value.errors()] == [("openai_api_key",)]
    assert caught.value.errors()[0]["type"] == "missing"


def test_openai_api_key_is_read_from_the_environment_and_kept_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    settings = Settings(_env_file=None, database_url=DATABASE_URL, **STORAGE)

    assert settings.openai_api_key.get_secret_value() == "sk-test-not-real"
    assert "sk-test-not-real" not in repr(settings)


def test_ai_spend_cap_defaults_to_5_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_SPEND_CAP_USD", raising=False)

    settings = Settings(
        _env_file=None, database_url=DATABASE_URL, openai_api_key="sk-test", **STORAGE
    )

    assert settings.ai_spend_cap_usd == Decimal("5")


def test_ai_spend_cap_is_read_from_the_environment_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_SPEND_CAP_USD", "0.10")

    settings = Settings(
        _env_file=None, database_url=DATABASE_URL, openai_api_key="sk-test", **STORAGE
    )

    assert settings.ai_spend_cap_usd == Decimal("0.10")


def test_every_storage_setting_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var and no .env file: each storage setting is reported missing."""
    for name in STORAGE:
        monkeypatch.delenv(name.upper(), raising=False)

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url=DATABASE_URL, openai_api_key="sk-test")

    assert {error["loc"][0] for error in caught.value.errors()} == set(STORAGE)
    assert {error["type"] for error in caught.value.errors()} == {"missing"}
