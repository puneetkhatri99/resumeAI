"""Request and response models for the public API (outside systems such as Loxo)."""

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.features.resume_structuring.service import ResumeData

MAX_ID_CHARS = 100

# The caller's own candidate or resume id: 1-100 characters with no control characters
# (Postgres rejects NUL in text columns) and no "/" (the PDF URL couldn't reach the pair).
PublicId = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_ID_CHARS, pattern=r"^[^\x00-\x1f\x7f/]+$")
]


class PublicResumeResponse(BaseModel):
    """The answer to POST /api/public/resumes: the caller's ids and the AI-extracted details."""

    candidate_id: str
    resume_id: str
    data: ResumeData


class PublicPdfResponse(BaseModel):
    """The answer to POST /api/public/pdf: the branded PDF's permanent public URL and the
    AI-extracted details it was made from."""

    url: str
    data: ResumeData
