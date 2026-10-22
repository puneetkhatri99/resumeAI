"""Database engine, session factory, and the declarative Base that every feature model uses."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# pool_pre_ping: check a pooled connection is still alive before use (e.g. after a DB restart).
# connect_timeout: fail fast when Postgres is down; without it each attempt hangs ~2 minutes.
# hide_parameters: keep bound values (CV text, answers) out of SQL error messages and logs.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5},
    hide_parameters=True,
)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    """Parent class for every SQLAlchemy model in app/features/<feature>/models.py."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency: opens one session per request and always closes it afterwards."""
    with SessionLocal() as session:
        yield session
