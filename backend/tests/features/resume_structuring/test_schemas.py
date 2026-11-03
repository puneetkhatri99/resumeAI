"""Tests for the resume_structuring schemas, which are also OpenAI's strict output schema."""

from typing import Any

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from app.features.resume_structuring.schemas import ResumeData


def object_schemas(schema: Any) -> list[dict[str, Any]]:
    """Every JSON-schema object (a dict with "properties") anywhere inside `schema`."""
    if isinstance(schema, list):
        return [found for item in schema for found in object_schemas(item)]
    if not isinstance(schema, dict):
        return []
    found = [schema] if "properties" in schema else []
    return found + [found for value in schema.values() for found in object_schemas(value)]


def keys_anywhere(schema: Any) -> set[str]:
    """Every dict key anywhere inside `schema` (not the text of descriptions)."""
    if isinstance(schema, list):
        return {key for item in schema for key in keys_anywhere(item)}
    if not isinstance(schema, dict):
        return set()
    return set(schema) | {key for value in schema.values() for key in keys_anywhere(value)}


def test_the_strict_schema_sent_to_openai_has_no_default_and_requires_every_property() -> None:
    """Strict mode needs every property required. summary and inferred_skills default only for
    old stored rows; the SDK must keep them required and drop the default, or OpenAI rejects it.
    """
    schema = to_strict_json_schema(ResumeData)
    objects = object_schemas(schema)

    assert "default" not in keys_anywhere(schema)
    # ResumeData, InferredSkill, Experience, Education, Project and Certificate.
    assert len(objects) == 6
    for found in objects:
        assert sorted(found["required"]) == sorted(found["properties"])
        assert found["additionalProperties"] is False


def test_an_old_row_without_the_new_fields_still_validates() -> None:
    data = ResumeData.model_validate(
        {
            "name": "Jane Doe",
            "email": "jane.doe@example.com",
            "phone": None,
            "location": None,
            "links": [],
            "skills": [],
            "experience": [],
            "education": [],
            "projects": [],
            "certificates": [],
        }
    )

    assert data.summary is None
    assert data.inferred_skills == []


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
