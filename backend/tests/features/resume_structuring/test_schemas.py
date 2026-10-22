"""Tests for the resume_structuring schemas, which are also OpenAI's strict output schema."""

import pytest
from pydantic import BaseModel, ValidationError

from app.features.resume_structuring.schemas import (
    Certificate,
    Education,
    Experience,
    Project,
    ResumeData,
)


@pytest.mark.parametrize("model", [Experience, Education, Project, Certificate, ResumeData])
def test_every_field_is_required_with_no_default(model: type[BaseModel]) -> None:
    """OpenAI strict mode needs every property required; "missing" is null or an empty list."""
    optional = [name for name, field in model.model_fields.items() if not field.is_required()]

    assert optional == []


def test_a_cv_with_nothing_but_the_required_values_is_valid() -> None:
    data = ResumeData(
        name="Jane Doe",
        email="jane.doe@example.com",
        phone=None,
        location=None,
        links=[],
        skills=[],
        experience=[],
        education=[],
        projects=[],
        certificates=[],
    )

    assert data.phone is None
    assert data.model_dump()["experience"] == []


def test_name_and_email_cannot_be_null() -> None:
    """A CV without them is an empty string from the AI (then a 422), never null."""
    with pytest.raises(ValidationError):
        ResumeData.model_validate_json(
            '{"name": null, "email": null, "phone": null, "location": null, "links": [],'
            ' "skills": [],'
            ' "experience": [], "education": [], "projects": [], "certificates": []}'
        )
