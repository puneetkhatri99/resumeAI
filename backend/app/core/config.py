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


settings = Settings()
