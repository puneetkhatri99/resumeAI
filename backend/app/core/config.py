"""Application settings, read from environment variables and backend/.env."""

import secrets
from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env from this file, so the app finds it no matter which directory it is started from.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Typed settings. An environment variable with the same name overrides the .env value."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")

    database_url: str
    # Used by resume_structuring. SecretStr keeps it out of reprs and logs.
    openai_api_key: SecretStr
    # All-time AI spending cap in USD (ai_usage). Once the recorded cost reaches it, uploads
    # get 402 until it is raised.
    ai_spend_cap_usd: Decimal = Decimal("5")
    # The X-API-Key that callers of /api/public must send (public_api). Unset: every public
    # route answers 401, and the rest of the app still starts.
    public_api_key: SecretStr | None = None
    # The bootstrap admin (auth), created at startup only if missing and never overwritten.
    # Both optional: unset, no admin is created. ADMIN_PASSWORD needs 12 to 1024 characters.
    admin_email: str | None = None
    admin_password: SecretStr | None = None
    # Neon Object Storage (S3-compatible) for the candidates feature. All required: the app
    # won't start without them. RESUME_BUCKET is private (the original CVs);
    # PUBLIC_RESUME_BUCKET is public-read (the branded PDFs).
    aws_endpoint_url_s3: str
    aws_access_key_id: SecretStr
    aws_secret_access_key: SecretStr
    aws_region: str
    resume_bucket: str
    public_resume_bucket: str


settings = Settings()


def is_public_api_key(api_key: str | None) -> bool:
    """True when `api_key` matches PUBLIC_API_KEY, compared in constant time.

    Always False while PUBLIC_API_KEY is unset or empty. Shared by public_api (the 401 check)
    and the rate limiter (which counts a key holder by key, not by IP).
    """
    expected = settings.public_api_key.get_secret_value() if settings.public_api_key else ""
    # Bytes: compare_digest raises TypeError on a non-ASCII str (a header like "X-API-Key: é").
    return bool(
        expected and api_key and secrets.compare_digest(api_key.encode(), expected.encode())
    )
