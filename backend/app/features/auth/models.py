"""SQLAlchemy models for login: the user accounts and their server-side sessions."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """An account that can log in: the bootstrap admin or a recruiter.

    email is stored trimmed and lower-cased. password_hash is
    "scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>", so a hash can be verified with the parameters
    it was made with. The role CHECK lives in the database: a bad role is an authorisation bug.
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'recruiter')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(9))


class UserSession(Base):
    """One logged-in session. Only the SHA-256 hex of the cookie's token is stored, so a
    database read leak can't replay a session. Named UserSession, not Session, to avoid
    SQLAlchemy's Session class.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
