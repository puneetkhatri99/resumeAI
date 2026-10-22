"""Tests for app.core.config.Settings: which settings are required to start the app."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

DATABASE_URL = "postgresql+psycopg://user:password@127.0.0.1:5432/example"


def test_openai_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var and no .env file: the key is reported missing, so the app can't start."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url=DATABASE_URL)

    assert [error["loc"] for error in caught.value.errors()] == [("openai_api_key",)]
    assert caught.value.errors()[0]["type"] == "missing"


def test_openai_api_key_is_read_from_the_environment_and_kept_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    settings = Settings(_env_file=None, database_url=DATABASE_URL)

    assert settings.openai_api_key.get_secret_value() == "sk-test-not-real"
    assert "sk-test-not-real" not in repr(settings)
