"""Tests for app.features.auth.service: password hashing, sessions, own-password change, the
admin's recruiter rules and the bootstrap admin. Synthetic accounts only; database tests run
under db_session (rolled back). Each scrypt hash costs about 0.4 s, so the tests reuse one.
"""

import base64
import logging
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.features.auth import service
from app.features.auth.models import User, UserSession
from app.features.auth.service import (
    DUMMY_HASH,
    WrongPasswordError,
    authenticate,
    change_password,
    ensure_admin,
    hash_password,
    hash_token,
    remove_recruiter,
    reset_recruiter_password,
    start_session,
    verify_password,
)

PASSWORD = "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)
ADMIN_EMAIL = "admin@example.com"


def add_user(db: Session, email: str, role: str = "recruiter") -> User:
    """Insert a committed user with PASSWORD."""
    user = User(email=email, password_hash=PASSWORD_HASH, role=role)
    db.add(user)
    db.commit()
    return user


def session_hashes(db: Session, user: User) -> set[str]:
    """The token hashes of every session the user has."""
    return set(db.scalars(select(UserSession.token_hash).where(UserSession.user_id == user.id)))


class FailingDb:
    """A session whose every query fails as if Postgres were down."""

    def scalar(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT", {}, Exception("database down"))


# --- hashing ---


def test_a_hash_verifies_its_password_and_no_other() -> None:
    assert verify_password(PASSWORD, PASSWORD_HASH)
    assert not verify_password(PASSWORD + "!", PASSWORD_HASH)


def test_two_hashes_of_one_password_have_different_salts() -> None:
    assert hash_password(PASSWORD).split("$")[4] != PASSWORD_HASH.split("$")[4]


def test_the_stored_hash_carries_its_scrypt_parameters() -> None:
    scheme, n, r, p, salt, digest = PASSWORD_HASH.split("$")

    assert (scheme, n, r, p) == ("scrypt", "32768", "8", "3")
    assert len(base64.b64decode(salt)) == 16
    assert len(base64.b64decode(digest)) == 32


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not a hash",
        "bcrypt$32768$8$3$c2FsdA==$aGFzaA==",
        "scrypt$32768$8$3$not base64!$aGFzaA==",
        "scrypt$3$8$3$c2FsdA==$aGFzaA==",  # n isn't a power of 2
        "scrypt$1048576$8$3$c2FsdA==$aGFzaA==",  # needs more memory than allowed
        "scrypt$x$8$3$c2FsdA==$aGFzaA==",
        "scrypt$99999999999999999999999$8$3$c2FsdA==$aGFzaA==",
    ],
)
def test_a_malformed_stored_hash_is_false_not_an_error(stored: str) -> None:
    assert verify_password(PASSWORD, stored) is False


# --- login and sessions ---


def test_an_unknown_email_still_runs_one_verify(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same work as a wrong password, so the timing doesn't reveal which emails exist."""
    checked: list[str] = []
    real_verify = service.verify_password

    def recording_verify(password: str, stored: str) -> bool:
        checked.append(stored)
        return real_verify(password, stored)

    monkeypatch.setattr(service, "verify_password", recording_verify)

    assert authenticate(db_session, "nobody@example.com", PASSWORD) is None
    assert checked == [DUMMY_HASH]


def test_login_matches_the_email_trimmed_and_lower_cased(db_session: Session) -> None:
    user = add_user(db_session, "jane@example.com")

    assert authenticate(db_session, "  Jane@Example.COM ", PASSWORD) is user
    assert authenticate(db_session, "jane@example.com", "wrong password!") is None


def test_a_new_session_prunes_the_expired_ones_and_lasts_12_hours(db_session: Session) -> None:
    user = add_user(db_session, "jane@example.com")
    now = datetime.now(UTC)
    db_session.add_all(
        [
            UserSession(user_id=user.id, token_hash="a" * 64, expires_at=now - timedelta(hours=1)),
            UserSession(user_id=user.id, token_hash="b" * 64, expires_at=now + timedelta(hours=1)),
        ]
    )
    db_session.commit()

    token = start_session(db_session, user)

    assert session_hashes(db_session, user) == {"b" * 64, hash_token(token)}
    lifetime = db_session.scalar(
        select(UserSession.expires_at - UserSession.created_at).where(
            UserSession.token_hash == hash_token(token)
        )
    )
    assert lifetime == timedelta(hours=12)


def test_change_password_keeps_only_the_current_session(db_session: Session) -> None:
    user = add_user(db_session, "jane@example.com")
    current = start_session(db_session, user)
    start_session(db_session, user)

    change_password(db_session, user, PASSWORD, "a brand new password", keep_token=current)

    assert session_hashes(db_session, user) == {hash_token(current)}
    assert verify_password("a brand new password", user.password_hash)


def test_change_password_with_a_wrong_current_password_changes_nothing(
    db_session: Session,
) -> None:
    user = add_user(db_session, "jane@example.com")
    token = start_session(db_session, user)
    start_session(db_session, user)

    with pytest.raises(WrongPasswordError):
        change_password(db_session, user, "wrong password!", "a brand new password", token)

    assert user.password_hash == PASSWORD_HASH
    assert len(session_hashes(db_session, user)) == 2


def test_reset_and_remove_refuse_the_admin(db_session: Session) -> None:
    admin = add_user(db_session, ADMIN_EMAIL, role="admin")

    assert reset_recruiter_password(db_session, admin.id, "a brand new password") is False
    assert remove_recruiter(db_session, admin.id) is False
    db_session.refresh(admin)
    assert admin.password_hash == PASSWORD_HASH


# --- the bootstrap admin ---


def set_admin_env(monkeypatch: pytest.MonkeyPatch, email: str | None, password: str | None) -> None:
    """Set ADMIN_EMAIL and ADMIN_PASSWORD for this test (the autouse fixture unsets them)."""
    monkeypatch.setattr(settings, "admin_email", email)
    monkeypatch.setattr(settings, "admin_password", SecretStr(password) if password else None)


def test_ensure_admin_creates_the_admin_once(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_admin_env(monkeypatch, "  Admin@Example.COM ", PASSWORD)

    ensure_admin(db_session)
    ensure_admin(db_session)

    admins = db_session.scalars(select(User).where(User.email == ADMIN_EMAIL)).all()
    assert len(admins) == 1
    assert admins[0].role == "admin"
    assert verify_password(PASSWORD, admins[0].password_hash)


def test_ensure_admin_never_changes_an_existing_admin(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = add_user(db_session, ADMIN_EMAIL, role="admin")
    set_admin_env(monkeypatch, ADMIN_EMAIL, "a different password")

    ensure_admin(db_session)

    db_session.refresh(admin)
    assert admin.password_hash == PASSWORD_HASH


def test_ensure_admin_does_not_promote_a_recruiter_and_warns(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    recruiter = add_user(db_session, ADMIN_EMAIL)
    set_admin_env(monkeypatch, ADMIN_EMAIL, PASSWORD)

    with caplog.at_level(logging.WARNING):
        ensure_admin(db_session)

    db_session.refresh(recruiter)
    assert recruiter.role == "recruiter"
    assert "not made an admin" in caplog.text
    assert ADMIN_EMAIL not in caplog.text


def test_ensure_admin_does_nothing_when_both_are_unset() -> None:
    """No database work at all: the stand-in has no methods."""
    ensure_admin(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(("email", "password"), [(ADMIN_EMAIL, None), (None, PASSWORD)])
def test_ensure_admin_warns_and_creates_nothing_when_only_one_is_set(
    email: str | None,
    password: str | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    set_admin_env(monkeypatch, email, password)

    with caplog.at_level(logging.WARNING):
        ensure_admin(object())  # type: ignore[arg-type]

    assert "must both be set" in caplog.text


@pytest.mark.parametrize("password", ["eleven char", "x" * 1025])
def test_ensure_admin_fails_startup_on_a_password_outside_the_limits(
    password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_admin_env(monkeypatch, ADMIN_EMAIL, password)

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD must be 12 to 1024") as caught:
        ensure_admin(object())  # type: ignore[arg-type]

    assert password not in str(caught.value)


def test_ensure_admin_logs_a_database_error_and_carries_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    set_admin_env(monkeypatch, ADMIN_EMAIL, PASSWORD)

    ensure_admin(FailingDb())  # type: ignore[arg-type]

    assert "Could not create the admin" in caplog.text
    assert PASSWORD not in caplog.text
