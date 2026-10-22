"""Response models for the health feature."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Status of the API and of its connection to Postgres."""

    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
