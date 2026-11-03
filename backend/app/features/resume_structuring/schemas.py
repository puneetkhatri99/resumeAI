"""Structured resume data: the details AI extracts from a CV's text.

These models are also sent to OpenAI as the strict structured-output schema. Strict mode needs
every field to be required with no default, so "missing" is null or an empty list. Dates are
kept as strings, exactly as written in the CV (e.g. "Jan 2020", "Present").

Everything is copied as written, with two exceptions: `summary` is written by the AI when the
CV has none, and an ongoing role's end_date is "Present".

ResumeData's `summary` and `inferred_skills` have defaults only so that rows stored before they
existed still validate. The OpenAI SDK's strict schema (to_strict_json_schema) still marks them
required and drops the None default; tests/features/resume_structuring/test_schemas.py checks it.
"""

from pydantic import BaseModel, Field


class Experience(BaseModel):
    """One job."""

    company: str | None
    title: str | None
    start_date: str | None
    end_date: str | None
    location: str | None
    description: str | None


class Education(BaseModel):
    """One degree, diploma or course of study."""

    institution: str | None
    degree: str | None
    field_of_study: str | None
    start_date: str | None
    end_date: str | None


class Project(BaseModel):
    """One project."""

    name: str | None
    description: str | None
    link: str | None
    technologies: list[str]


class Certificate(BaseModel):
    """One certificate or certification."""

    name: str | None
    issuer: str | None
    date: str | None


class InferredSkill(BaseModel):
    """A skill the CV shows the candidate used but doesn't list, with a quote that proves it.

    `evidence` is a short phrase copied word for word from the CV; the service drops the skill
    when the phrase isn't in the CV text (keep_proven_skills).
    """

    name: str
    evidence: str


class ResumeData(BaseModel):
    """Everything extracted from one CV. Name and email are required by the service.

    `summary` and `inferred_skills` default only for old stored rows (see the module docstring).
    """

    name: str
    email: str
    phone: str | None
    # The candidate's current location, as written in the CV.
    location: str | None
    # The CV's own summary as written, or 2-3 sentences the AI wrote from the CV's facts.
    summary: str | None = None
    links: list[str]
    # The skills the CV lists.
    skills: list[str]
    inferred_skills: list[InferredSkill] = Field(default_factory=list)
    experience: list[Experience]
    education: list[Education]
    projects: list[Project]
    certificates: list[Certificate]
