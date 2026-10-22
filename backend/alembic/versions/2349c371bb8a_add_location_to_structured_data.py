"""add location to structured_data

Data migration, no schema change: ResumeData gained a required `location` field, so every
stored structured_data object gets "location": null unless it already has one.

Revision ID: 2349c371bb8a
Revises: a8d5bd5c3c32
Create Date: 2026-10-22 19:09:31.504388

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '2349c371bb8a'
down_revision: Union[str, Sequence[str], None] = 'a8d5bd5c3c32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add "location": null to every stored object that lacks it.

    The default sits on the left of ||, so an existing location wins (idempotent).
    jsonb_typeof skips SQL NULL and JSON null rows: `object || 'null'` would make an array.
    """
    op.execute(
        "UPDATE resume_intakes"
        " SET structured_data = jsonb_build_object('location', NULL) || structured_data"
        " WHERE jsonb_typeof(structured_data) = 'object'"
    )


def downgrade() -> None:
    """Remove the location key again (real values too; the older code has no such field)."""
    op.execute(
        "UPDATE resume_intakes"
        " SET structured_data = structured_data - 'location'"
        " WHERE jsonb_typeof(structured_data) = 'object'"
    )
