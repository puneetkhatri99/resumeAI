"""Request and response models for resume intake: the answers, the stored record and the
progress events streamed by POST /api/resume_intake.
"""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.features.resume_structuring.service import ResumeData

MAX_SHORT_ANSWER_CHARS = 200
MAX_LONG_ANSWER_CHARS = 5000

# Every C0 control character except \t and \n (\r is turned into \n before this runs).
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f]")


def clean_text(text: str) -> str:
    """Normalise line endings to \\n, drop C0 control characters except \\t and \\n, and strip.

    Postgres rejects NUL bytes in text columns, so everything stored goes through this:
    the answers (via the validators below) and the CV text and filename (in the service).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return CONTROL_CHARS.sub("", text).strip()


# clean_text runs before the length checks, so an answer made only of control characters is
# rejected as empty instead of being stored as "".
ShortAnswer = Annotated[
    str,
    AfterValidator(clean_text),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_SHORT_ANSWER_CHARS),
]
LongAnswer = Annotated[
    str,
    AfterValidator(clean_text),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_LONG_ANSWER_CHARS),
]
WorkMode = Literal["remote", "hybrid", "onsite"]
Rating = Annotated[int, Field(ge=1, le=5)]
FileType = Literal["pdf", "docx", "doc", "txt"]


class InterviewAnswers(BaseModel):
    """The interviewer's answers. All are required; text answers are cleaned and length-checked."""

    notice_period: ShortAnswer
    salary_expectation: ShortAnswer
    work_mode: WorkMode
    rating: Rating
    strengths: LongAnswer
    concerns: LongAnswer
    overall_notes: LongAnswer


class ResumeIntakeResponse(InterviewAnswers):
    """A stored intake: the answers, the CV's text and AI-extracted details, and the record fields.

    structured_data is null only on rows stored before AI extraction existed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    file_type: FileType
    extracted_text: str
    structured_data: ResumeData | None
    created_at: datetime


class IntakeSummary(BaseModel):
    """One row of the past-interviews list (GET /api/resume_intake), without the CV text.

    candidate_name and candidate_email come from the AI-extracted details, so they are null on
    rows stored before AI extraction existed; the list then shows original_filename.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    original_filename: str
    candidate_name: str | None
    candidate_email: str | None
    rating: Rating
    work_mode: WorkMode


class IntakeList(BaseModel):
    """A page of past interviews, newest first; has_more says whether older ones exist."""

    items: list[IntakeSummary]
    has_more: bool


class IntakeEvent(BaseModel):
    """One Server-Sent Event of POST /api/resume_intake (sent as the event's JSON `data`).

    "reading" and "extracting" report progress; the stream ends with "done" (with `result`) or
    "error" (with the HTTP `status` the failure maps to and its `detail`). Unused fields are null.
    """

    stage: Literal["reading", "extracting", "done", "error"]
    result: ResumeIntakeResponse | None = None
    status: int | None = None
    detail: str | None = None
