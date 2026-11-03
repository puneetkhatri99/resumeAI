"""Application settings, read from environment variables and backend/.env."""

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
