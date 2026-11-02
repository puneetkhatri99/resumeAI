"""Tests for public_api.service: storing, replacing and reading a pair's details (needs Postgres;
db_session rolls every write back).
"""

from sqlalchemy.orm import Session

from app.features.ai_usage.service import AiCall, record_ai_call
from app.features.public_api.service import get_public_resume, save_public_resume
from app.features.resume_structuring.service import OPENAI_MODEL
from tests.conftest import FAKE_RESUME_DATA, FAKE_USAGE


def test_a_saved_pair_is_read_back_with_its_ai_calls_unlinked(db_session: Session) -> None:
    ai_calls: list[AiCall] = []
    record_ai_call(ai_calls, OPENAI_MODEL, "structuring", FAKE_USAGE)

    save_public_resume(db_session, "cand-1", "res-1", FAKE_RESUME_DATA, ai_calls)

    row = get_public_resume(db_session, "cand-1", "res-1")
    assert row is not None
    assert row.structured_data == FAKE_RESUME_DATA.model_dump()
    assert ai_calls[0].id is not None
    assert ai_calls[0].resume_intake_id is None


def test_a_repeat_pair_replaces_and_other_pairs_are_kept(db_session: Session) -> None:
    renamed = FAKE_RESUME_DATA.model_copy(update={"name": "Jane Q. Doe"})
    save_public_resume(db_session, "cand-1", "res-1", FAKE_RESUME_DATA, [])
    save_public_resume(db_session, "cand-1", "res-2", FAKE_RESUME_DATA, [])

    save_public_resume(db_session, "cand-1", "res-1", renamed, [])

    db_session.expire_all()
    assert get_public_resume(db_session, "cand-1", "res-1").structured_data["name"] == "Jane Q. Doe"
    assert get_public_resume(db_session, "cand-1", "res-2").structured_data["name"] == "Jane Doe"


def test_an_unknown_pair_is_none(db_session: Session) -> None:
    assert get_public_resume(db_session, "nobody", "none") is None
