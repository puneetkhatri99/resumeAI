"""Tests for GET /api/health."""

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.database import get_db
from app.main import app


class UnreachableDatabaseSession:
    """Stand-in session whose every query fails the way it does when Postgres is down."""

    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))


def test_health_is_ok_when_database_answers(client: TestClient) -> None:
    """Needs a running Postgres that matches DATABASE_URL."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_503_when_database_is_unreachable(client: TestClient) -> None:
    app.dependency_overrides[get_db] = UnreachableDatabaseSession

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
