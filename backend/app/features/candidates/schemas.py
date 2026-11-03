"""Request and response models for candidates: the add-candidate progress events and the
candidate list.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# Set by the system: "manual" for the web app upload, "api" for POST /api/public/pdf.
Source = Literal["manual", "api"]


class CandidateEvent(BaseModel):
    """One Server-Sent Event of POST /api/candidates (sent as the event's JSON `data`).

    "reading", "extracting", "generating" and "uploading" report progress; the stream ends with
    "done" (with `new_resume_url`, the branded PDF's permanent public URL) or "error" (with the
    HTTP `status` the failure maps to and its `detail`). Unused fields are null.
    """

    stage: Literal["reading", "extracting", "generating", "uploading", "done", "error"]
    new_resume_url: str | None = None
    status: int | None = None
    detail: str | None = None


class CandidateSummary(BaseModel):
    """One row of the candidate page (GET /api/candidates).

    original_resume_url is a presigned link to the private original that expires after 1 hour;
    new_resume_url is the branded PDF's permanent public URL. ai_cost_usd is null when every
    AI call of the upload was unpriced.
    """

    id: int
    name: str
    created_at: datetime
    source: Source
    ai_cost_usd: float | None
    original_resume_url: str
    new_resume_url: str


class CandidateList(BaseModel):
    """A page of candidates, newest first; has_more says whether older ones exist."""

    items: list[CandidateSummary]
    has_more: bool
