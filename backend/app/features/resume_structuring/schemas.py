"""Structured resume data: the details AI extracts from a CV's text.

These models are also sent to OpenAI as the strict structured-output schema. Strict mode needs
every field to be required with no default, so "missing" is null or an empty list. Dates are
kept as strings, exactly as written in the CV (e.g. "Jan 2020", "Present").
"""

from pydantic import BaseModel


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


class ResumeData(BaseModel):
    """Everything extracted from one CV. Name and email are required by the service."""

    name: str
    email: str
    phone: str | None
    # The candidate's current location, as written in the CV.
    location: str | None
    links: list[str]
    skills: list[str]
    experience: list[Experience]
    education: list[Education]
    projects: list[Project]
    certificates: list[Certificate]
