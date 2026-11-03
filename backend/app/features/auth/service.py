"""Login business logic: password hashing (stdlib scrypt), server-side sessions, changing your own
password, the admin's recruiter management, and the bootstrap admin from ADMIN_EMAIL and
ADMIN_PASSWORD. No HTTP here.

Never logged: passwords, tokens, hashes or emails. Only a session token's SHA-256 is stored.
"""

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.features.auth.models import User, UserSession
from app.features.auth.schemas import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, is_storable

logger = logging.getLogger(__name__)

SESSION_HOURS = 12
# scrypt parameters (OWASP-equivalent; 32 MiB per hash). maxmem leaves room above that.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 3
SCRYPT_MAXMEM = 64 * 1024 * 1024
SCRYPT_DKLEN = 32
SALT_BYTES = 16


class WrongPasswordError(Exception):
    """The current password given to change_password is wrong."""


class EmailTakenError(Exception):
    """An account with this email already exists."""


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    """scrypt of `password`. surrogatepass: a lone surrogate from JSON still hashes, not 500s."""
    return hashlib.scrypt(
        password.encode("utf-8", "surrogatepass"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=SCRYPT_MAXMEM,
        dklen=dklen,
    )


def hash_password(password: str) -> str:
    """Hash with scrypt and a random 16-byte salt, as "scrypt$<n>$<r>$<p>$<salt>$<hash>"
    (base64), so verifying uses the parameters the hash was made with.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN)
    salt_b64 = base64.b64encode(salt).decode()
    digest_b64 = base64.b64encode(digest).decode()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${digest_b64}"


def verify_password(password: str, stored: str) -> bool:
    """True when `password` matches the stored hash. A malformed stored string is False, never
    an exception. Compared in constant time.
    """
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64, validate=True)
        salt = base64.b64decode(salt_b64, validate=True)
        actual = _scrypt(password, salt, int(n), int(r), int(p), len(expected))
    except (ValueError, OverflowError, TypeError):
        # Bad base64, a wrong field count, or scrypt rejecting the parameters (out-of-range
        # numbers raise OverflowError or TypeError).
        return False
    return hmac.compare_digest(actual, expected)


# Verified against when the email is unknown, so a login takes the same time either way.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def normalize_email(email: str) -> str:
    """Emails are stored, looked up and compared trimmed and lower-cased."""
    return email.strip().lower()


def hash_token(token: str) -> str:
    """The SHA-256 hex of a session token: the only form of it stored in the database."""
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(db: Session, email: str, password: str) -> User | None:
    """The user with this email and password, or None. An unknown email still runs one scrypt
    verify (against DUMMY_HASH), so it can't be told apart from a wrong password by timing.
    """
    # An email the database can't hold matches no account; skip the query, which would fail.
    user = (
        db.scalar(select(User).where(User.email == normalize_email(email)))
        if is_storable(email)
        else None
    )
    if user is None:
        verify_password(password, DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def start_session(db: Session, user: User) -> str:
    """Delete the user's expired sessions, store a new one (only the token's hash) that ends
    SESSION_HOURS from now, commit, and return the raw token for the cookie.
    """
    db.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id, UserSession.expires_at <= func.now()
        )
    )
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=func.now() + timedelta(hours=SESSION_HOURS),
        )
    )
    db.commit()
    return token


def find_session_user(db: Session, token: str) -> User | None:
    """The user of the unexpired session with this token, or None."""
    return db.scalar(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == hash_token(token), UserSession.expires_at > func.now())
    )


def end_session(db: Session, token: str) -> None:
    """Delete the session with this token (logout). An unknown token is fine."""
    db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
    db.commit()


def change_password(db: Session, user: User, current: str, new: str, keep_token: str) -> None:
    """Set the user's new password and end their other sessions; the session with `keep_token`
    stays. Raises WrongPasswordError when `current` is wrong.
    """
    if not verify_password(current, user.password_hash):
        raise WrongPasswordError
    user.password_hash = hash_password(new)
    db.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id, UserSession.token_hash != hash_token(keep_token)
        )
    )
    db.commit()


def list_recruiters(db: Session) -> list[User]:
    """Every recruiter account, sorted by email. The admin isn't listed."""
    return list(db.scalars(select(User).where(User.role == "recruiter").order_by(User.email)))


def add_recruiter(db: Session, email: str, password: str) -> User:
    """Create a recruiter account. Raises EmailTakenError when the email exists (any role),
    including when two adds race (the unique constraint decides).
    """
    user = User(
        email=normalize_email(email), password_hash=hash_password(password), role="recruiter"
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise EmailTakenError from None
    return user


def _find_recruiter(db: Session, recruiter_id: int) -> User | None:
    """The recruiter with this id; None for an unknown id or the admin's own."""
    return db.scalar(select(User).where(User.id == recruiter_id, User.role == "recruiter"))


def reset_recruiter_password(db: Session, recruiter_id: int, password: str) -> bool:
    """Set a recruiter's password and end all their sessions. False when the id isn't a
    recruiter's (the admin can't be reset through the admin page).
    """
    user = _find_recruiter(db, recruiter_id)
    if user is None:
        return False
    user.password_hash = hash_password(password)
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    return True


def remove_recruiter(db: Session, recruiter_id: int) -> bool:
    """Delete a recruiter; their sessions go by ON DELETE CASCADE. False when the id isn't a
    recruiter's (the admin can't be removed).
    """
    removed = db.execute(
        delete(User).where(User.id == recruiter_id, User.role == "recruiter").returning(User.id)
    ).first()
    db.commit()
    return removed is not None


def ensure_admin(db: Session) -> None:
    """Create the bootstrap admin from ADMIN_EMAIL and ADMIN_PASSWORD if it doesn't exist yet.

    Both unset: nothing happens. Only one set: a warning, nothing created. A password outside
    12–1024 characters: raises RuntimeError, so startup fails instead of using a weak password.
    An existing account with that email is never changed (a changed ADMIN_PASSWORD has no
    effect; a recruiter isn't promoted, with a warning). A database error is logged and startup
    continues. Two processes starting at once are safe (INSERT ... ON CONFLICT DO NOTHING).
    """
    email = normalize_email(settings.admin_email or "")
    password = settings.admin_password.get_secret_value() if settings.admin_password else ""
    if not email and not password:
        return
    if not email or not password:
        logger.warning("ADMIN_EMAIL and ADMIN_PASSWORD must both be set; no admin was created.")
        return
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise RuntimeError(
            f"ADMIN_PASSWORD must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters."
        )
    try:
        role = db.scalar(select(User.role).where(User.email == email))
        if role is None:
            db.execute(
                insert(User)
                .values(email=email, password_hash=hash_password(password), role="admin")
                .on_conflict_do_nothing(index_elements=[User.email])
            )
            db.commit()
        elif role != "admin":
            logger.warning("ADMIN_EMAIL belongs to a recruiter account; it was not made an admin.")
    except SQLAlchemyError:
        logger.exception("Could not create the admin: database error")
