"""HTTP tests for the auth endpoints, the login guard on every /api route, and the admin routes.

Every test here uses real_auth (no fake recruiter), so requests need a real session cookie.
Synthetic accounts only, inserted under db_session (rolled back). Each scrypt hash costs about
0.4 s, so users are inserted with one precomputed hash.
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import main
from app.core.config import settings
from app.core.database import MAX_ID, get_db
from app.core.rate_limit import LOGIN_LIMIT
from app.features.auth.models import User, UserSession
from app.features.auth.router import SESSION_COOKIE
from app.features.auth.service import hash_password, hash_token
from app.features.public_api.router import INVALID_KEY_MESSAGE
from app.main import app
from tests.conftest import FakeOpenAI

pytestmark = pytest.mark.usefixtures("real_auth")

PASSWORD = "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)
NEW_PASSWORD = "a brand new password"
ADMIN_EMAIL = "admin@example.com"
RECRUITER_EMAIL = "jane@example.com"
NOT_LOGGED_IN = {"detail": "Not logged in."}
WRONG_LOGIN = {"detail": "Email or password is incorrect."}


def add_user(db: Session, email: str, role: str = "recruiter") -> User:
    """Insert a committed user with PASSWORD."""
    user = User(email=email, password_hash=PASSWORD_HASH, role=role)
    db.add(user)
    db.commit()
    return user


def log_in(client: TestClient, email: str, password: str = PASSWORD) -> httpx2.Response:
    """POST the login form; on success the client keeps the session cookie."""
    return client.post("/api/auth/login", json={"email": email, "password": password})


def token_of(response: httpx2.Response) -> str:
    """The session token a login response set."""
    return response.cookies[SESSION_COOKIE]


def use_token(client: TestClient, token: str | None) -> None:
    """Make the client send only this session cookie (or none)."""
    client.cookies.clear()
    if token is not None:
        client.cookies.set(SESSION_COOKIE, token)


# --- login ---


def test_login_sets_a_locked_down_session_cookie(client: TestClient, db_session: Session) -> None:
    add_user(db_session, RECRUITER_EMAIL)

    response = log_in(client, "  Jane@Example.com ")

    assert response.status_code == 200
    assert response.json() == {"email": RECRUITER_EMAIL, "role": "recruiter"}
    cookie = response.headers["set-cookie"]
    for attribute in ["HttpOnly", "Secure", "SameSite=lax", "Path=/api", "Max-Age=43200"]:
        assert attribute.lower() in cookie.lower()
    token = token_of(response)
    stored = set(db_session.scalars(select(UserSession.token_hash)))
    assert hash_token(token) in stored
    assert token not in stored


def test_an_unknown_email_and_a_wrong_password_get_the_same_401(
    client: TestClient, db_session: Session
) -> None:
    add_user(db_session, RECRUITER_EMAIL)

    unknown = log_in(client, "nobody@example.com")
    wrong = log_in(client, RECRUITER_EMAIL, "wrong password!")

    assert (unknown.status_code, unknown.json()) == (401, WRONG_LOGIN)
    assert (wrong.status_code, wrong.json()) == (401, WRONG_LOGIN)
    assert SESSION_COOKIE not in wrong.cookies


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "", "password": PASSWORD},
        {"email": RECRUITER_EMAIL, "password": ""},
        {"email": RECRUITER_EMAIL, "password": "x" * 1025},
        {"email": "a" * 255, "password": PASSWORD},
    ],
)
def test_login_rejects_bad_input_with_422(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/api/auth/login", json=body).status_code == 422


def test_the_11th_password_attempt_is_429_and_the_password_change_shares_the_bucket(
    client: TestClient,
) -> None:
    """Invalid bodies still count: the limit is checked before the request reaches the app."""
    for _ in range(LOGIN_LIMIT - 1):
        assert client.post("/api/auth/login", json={}).status_code == 422
    assert client.post("/api/auth/password", json={}).status_code == 401

    for path in ["/api/auth/login", "/api/auth/password"]:
        response = client.post(path, json={})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
    # Other routes still work.
    assert client.post("/api/auth/logout").status_code == 204


# --- /me and logout ---


def test_me_returns_the_logged_in_user(client: TestClient, db_session: Session) -> None:
    add_user(db_session, ADMIN_EMAIL, role="admin")
    log_in(client, ADMIN_EMAIL)

    response = client.get("/api/auth/me")

    assert (response.status_code, response.json()) == (200, {"email": ADMIN_EMAIL, "role": "admin"})


def test_me_without_a_valid_session_is_401(client: TestClient, db_session: Session) -> None:
    user = add_user(db_session, RECRUITER_EMAIL)
    expired = "expired-token"
    db_session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(expired),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    db_session.commit()

    for token in [None, "unknown-token", expired]:
        use_token(client, token)
        response = client.get("/api/auth/me")
        assert (response.status_code, response.json()) == (401, NOT_LOGGED_IN)


def test_logout_ends_the_session(client: TestClient, db_session: Session) -> None:
    add_user(db_session, RECRUITER_EMAIL)
    token = token_of(log_in(client, RECRUITER_EMAIL))

    response = client.post("/api/auth/logout")

    assert response.status_code == 204
    assert 'session=""' in response.headers["set-cookie"]
    use_token(client, token)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_without_a_cookie_is_still_204(client: TestClient) -> None:
    assert client.post("/api/auth/logout").status_code == 204


# --- changing your own password ---


def test_a_wrong_current_password_is_400_not_401(client: TestClient, db_session: Session) -> None:
    add_user(db_session, RECRUITER_EMAIL)
    log_in(client, RECRUITER_EMAIL)

    response = client.post(
        "/api/auth/password",
        json={"current_password": "wrong password!", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Current password is incorrect."}


def test_a_new_password_under_12_characters_is_422(client: TestClient, db_session: Session) -> None:
    add_user(db_session, RECRUITER_EMAIL)
    log_in(client, RECRUITER_EMAIL)

    response = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": "eleven char"}
    )

    assert response.status_code == 422


def test_a_password_change_ends_the_other_sessions_only(
    client: TestClient, db_session: Session
) -> None:
    add_user(db_session, RECRUITER_EMAIL)
    other = token_of(log_in(client, RECRUITER_EMAIL))
    current = token_of(log_in(client, RECRUITER_EMAIL))
    use_token(client, current)

    response = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 200
    use_token(client, other)
    assert client.get("/api/auth/me").status_code == 401
    assert log_in(client, RECRUITER_EMAIL).status_code == 401
    assert log_in(client, RECRUITER_EMAIL, NEW_PASSWORD).status_code == 200


# --- the admin page ---


@pytest.fixture
def admin_client(client: TestClient, db_session: Session) -> TestClient:
    """The client, logged in as a test admin."""
    add_user(db_session, ADMIN_EMAIL, role="admin")
    assert log_in(client, ADMIN_EMAIL).status_code == 200
    return client


def test_the_admin_adds_a_recruiter(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/auth/recruiters", json={"email": "  New@Example.com ", "password": PASSWORD}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert isinstance(body["id"], int)
    assert body["created_at"]
    assert log_in(admin_client, "new@example.com").json()["role"] == "recruiter"


@pytest.mark.parametrize("email", [RECRUITER_EMAIL, " JANE@example.com", " Admin@Example.COM "])
def test_adding_an_existing_email_is_409(
    admin_client: TestClient, db_session: Session, email: str
) -> None:
    add_user(db_session, RECRUITER_EMAIL)

    response = admin_client.post(
        "/api/auth/recruiters", json={"email": email, "password": PASSWORD}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "An account with this email already exists."}


@pytest.mark.parametrize(
    "body",
    [
        {"email": "no-at-sign", "password": PASSWORD},
        {"email": "two@@example.com", "password": PASSWORD},
        {"email": "a b@example.com", "password": PASSWORD},
        {"email": "a@" + "b" * 253, "password": PASSWORD},
        {"email": "new@example.com", "password": "eleven char"},
    ],
)
def test_adding_a_bad_email_or_short_password_is_422(
    admin_client: TestClient, body: dict[str, str]
) -> None:
    assert admin_client.post("/api/auth/recruiters", json=body).status_code == 422


def test_the_list_shows_recruiters_sorted_by_email_without_the_admin(
    admin_client: TestClient, db_session: Session
) -> None:
    add_user(db_session, "zed@example.com")
    add_user(db_session, "amy@example.com")

    response = admin_client.get("/api/auth/recruiters")

    assert response.status_code == 200
    assert [item["email"] for item in response.json()["items"]] == [
        "amy@example.com",
        "zed@example.com",
    ]


def test_a_reset_ends_the_recruiters_sessions_and_sets_the_new_password(
    admin_client: TestClient, db_session: Session
) -> None:
    recruiter = add_user(db_session, RECRUITER_EMAIL)
    admin_token = admin_client.cookies[SESSION_COOKIE]
    recruiter_token = token_of(log_in(admin_client, RECRUITER_EMAIL))
    use_token(admin_client, admin_token)

    response = admin_client.put(
        f"/api/auth/recruiters/{recruiter.id}/password", json={"password": NEW_PASSWORD}
    )

    assert response.status_code == 204
    use_token(admin_client, recruiter_token)
    assert admin_client.get("/api/auth/me").status_code == 401
    assert log_in(admin_client, RECRUITER_EMAIL, NEW_PASSWORD).status_code == 200


def test_removing_a_recruiter_ends_their_sessions_and_login(
    admin_client: TestClient, db_session: Session
) -> None:
    recruiter = add_user(db_session, RECRUITER_EMAIL)
    admin_token = admin_client.cookies[SESSION_COOKIE]
    recruiter_token = token_of(log_in(admin_client, RECRUITER_EMAIL))
    use_token(admin_client, admin_token)

    response = admin_client.delete(f"/api/auth/recruiters/{recruiter.id}")

    assert response.status_code == 204
    assert db_session.scalar(select(UserSession).where(UserSession.user_id == recruiter.id)) is None
    use_token(admin_client, recruiter_token)
    assert admin_client.get("/api/auth/me").status_code == 401
    assert log_in(admin_client, RECRUITER_EMAIL).status_code == 401


def test_the_admins_own_id_and_an_unknown_id_are_404(
    admin_client: TestClient, db_session: Session
) -> None:
    admin_id = db_session.scalar(select(User.id).where(User.email == ADMIN_EMAIL))

    for recruiter_id in [admin_id, MAX_ID]:
        reset = admin_client.put(
            f"/api/auth/recruiters/{recruiter_id}/password", json={"password": NEW_PASSWORD}
        )
        remove = admin_client.delete(f"/api/auth/recruiters/{recruiter_id}")
        for response in [reset, remove]:
            assert (response.status_code, response.json()) == (
                404,
                {"detail": "Recruiter not found."},
            )


def test_an_id_beyond_the_database_integer_range_is_422(admin_client: TestClient) -> None:
    too_big = MAX_ID + 1

    reset = admin_client.put(
        f"/api/auth/recruiters/{too_big}/password", json={"password": PASSWORD}
    )
    remove = admin_client.delete(f"/api/auth/recruiters/{too_big}")

    assert reset.status_code == 422
    assert remove.status_code == 422


# --- the login guard on every route ---

# Routes that answer without a login.
OPEN_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout"}


def api_operations() -> list[tuple[str, str, str]]:
    """Every (method, path template, URL with each path parameter set to 1) under /api, read
    from the OpenAPI schema, so a new route is covered without editing this test.
    """
    return [
        (method.upper(), path, re.sub(r"\{[^}]+\}", "1", path))
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api")
        for method in operations
    ]


def test_every_api_route_needs_a_login_unless_it_is_on_the_allow_list(client: TestClient) -> None:
    operations = api_operations()
    assert {"/api/candidates", "/api/auth/recruiters", "/api/ai_usage"} <= {
        p for _, p, _ in operations
    }

    for method, path, url in operations:
        response = client.request(method, url)
        where = f"{method} {path}"
        if path == "/api/health":
            assert response.status_code == 200, where
        elif path.startswith("/api/public/"):
            assert (response.status_code, response.json()) == (
                401,
                {"detail": INVALID_KEY_MESSAGE},
            ), where
        elif path in OPEN_PATHS:
            assert response.status_code != 401, where
        else:
            assert (response.status_code, response.json()) == (401, NOT_LOGGED_IN), where


def test_a_recruiter_gets_403_on_every_admin_route(client: TestClient, db_session: Session) -> None:
    add_user(db_session, RECRUITER_EMAIL)
    log_in(client, RECRUITER_EMAIL)
    admin_operations = [op for op in api_operations() if op[1].startswith("/api/auth/recruiters")]
    assert len(admin_operations) == 4

    for method, path, url in admin_operations:
        response = client.request(method, url)
        assert (response.status_code, response.json()) == (
            403,
            {"detail": "Admins only."},
        ), f"{method} {path}"


@pytest.mark.parametrize("path", ["/api/resume_intake", "/api/candidates"])
@pytest.mark.parametrize("cap", [Decimal(5), Decimal(0)])
def test_an_upload_without_login_is_401_before_the_spending_cap_and_any_ai_call(
    client: TestClient,
    fake_openai: FakeOpenAI,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    cap: Decimal,
) -> None:
    """A $0 cap is always reached; the login check still answers first."""
    monkeypatch.setattr(settings, "ai_spend_cap_usd", cap)

    response = client.post(
        path, files={"file": ("cv.txt", b"Jane Doe\njane@example.com", "text/plain")}
    )

    assert (response.status_code, response.json()) == (401, NOT_LOGGED_IN)
    assert fake_openai.calls == []
    assert fake_openai.ocr_calls == []


def test_startup_runs_the_admin_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(main, "ensure_admin", calls.append)

    with TestClient(app):
        pass

    assert len(calls) == 1


# --- QA pass: odd input, boundaries, database down ---


class DownSession:
    """A session whose every database call fails the way a dead connection does."""

    def _fail(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("select 1", {}, Exception("connection refused"))

    scalar = scalars = execute = commit = add = rollback = _fail


@pytest.fixture
def database_down() -> Iterator[None]:
    """Make get_db hand out a session that fails on every call."""
    app.dependency_overrides[get_db] = lambda: DownSession()
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("email", ["a\u0000b@example.com", "x" * 254])
def test_login_with_an_odd_email_is_a_plain_401(
    client: TestClient, db_session: Session, email: str
) -> None:
    # Postgres text can't hold a NUL; it must read as a wrong login, not "database down".
    response = log_in(client, email)

    assert (response.status_code, response.json()) == (401, WRONG_LOGIN)


def test_adding_a_recruiter_with_a_nul_in_the_email_is_a_422(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/auth/recruiters", json={"email": "a\u0000b@example.com", "password": PASSWORD}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(("length", "status"), [(11, 422), (12, 201), (1024, 201), (1025, 422)])
def test_a_new_recruiters_password_length_limits_are_inclusive(
    admin_client: TestClient, length: int, status: int
) -> None:
    response = admin_client.post(
        "/api/auth/recruiters", json={"email": f"p{length}@example.com", "password": "x" * length}
    )

    assert response.status_code == status


@pytest.mark.parametrize(("length", "status"), [(11, 422), (12, 204), (1025, 422)])
def test_a_reset_password_length_limits_are_inclusive(
    admin_client: TestClient, db_session: Session, length: int, status: int
) -> None:
    recruiter = add_user(db_session, RECRUITER_EMAIL)

    response = admin_client.put(
        f"/api/auth/recruiters/{recruiter.id}/password", json={"password": "y" * length}
    )

    assert response.status_code == status


def test_a_login_password_over_1024_characters_is_422(client: TestClient) -> None:
    assert log_in(client, RECRUITER_EMAIL, "x" * 1025).status_code == 422


def test_an_added_recruiter_can_log_in_with_any_email_case_and_spaces(
    admin_client: TestClient, client: TestClient
) -> None:
    created = admin_client.post(
        "/api/auth/recruiters", json={"email": "  New.Person@Example.COM ", "password": PASSWORD}
    )
    use_token(client, None)

    login = log_in(client, " NEW.person@example.com  ")

    assert created.json()["email"] == "new.person@example.com"
    assert login.status_code == 200


def test_the_admin_can_change_their_own_password_and_stays_logged_in(
    admin_client: TestClient,
) -> None:
    response = admin_client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 204
    assert admin_client.get("/api/auth/me").status_code == 200


def test_logging_out_one_session_leaves_the_users_other_session(
    client: TestClient, db_session: Session
) -> None:
    add_user(db_session, RECRUITER_EMAIL)
    first = token_of(log_in(client, RECRUITER_EMAIL))
    second = token_of(log_in(client, RECRUITER_EMAIL))
    use_token(client, first)
    client.post("/api/auth/logout")

    use_token(client, second)

    assert client.get("/api/auth/me").status_code == 200


def test_ten_failed_logins_with_different_emails_then_a_correct_one_is_429(
    client: TestClient, db_session: Session
) -> None:
    add_user(db_session, RECRUITER_EMAIL)
    for i in range(LOGIN_LIMIT):
        assert log_in(client, f"nobody{i}@example.com").status_code == 401

    response = log_in(client, RECRUITER_EMAIL)

    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_the_login_limit_is_not_used_up_by_me_or_logout(client: TestClient) -> None:
    for _ in range(LOGIN_LIMIT + 2):
        client.get("/api/auth/me")
        client.post("/api/auth/logout")

    assert log_in(client, RECRUITER_EMAIL).status_code == 401


def test_public_routes_ignore_a_session_cookie(client: TestClient, db_session: Session) -> None:
    add_user(db_session, RECRUITER_EMAIL)
    log_in(client, RECRUITER_EMAIL)

    response = client.get("/api/public/resumes/c1/r1/pdf")

    assert (response.status_code, response.json()) == (401, {"detail": INVALID_KEY_MESSAGE})


def test_a_protected_route_is_503_when_the_database_is_down(
    client: TestClient, database_down: None
) -> None:
    client.cookies.set(SESSION_COOKIE, "sometoken")

    for url in ["/api/auth/me", "/api/candidates", "/api/ai_usage"]:
        response = client.get(url)
        assert (response.status_code, response.json()) == (
            503,
            {"detail": "The database is unavailable. Try again later."},
        ), url


def test_login_is_503_when_the_database_is_down(client: TestClient, database_down: None) -> None:
    assert log_in(client, RECRUITER_EMAIL).status_code == 503


def test_logout_is_503_when_the_database_is_down_but_still_clears_the_cookie(
    client: TestClient, database_down: None
) -> None:
    client.cookies.set(SESSION_COOKIE, "sometoken")

    response = client.post("/api/auth/logout")

    assert response.status_code == 503
    assert "Max-Age=0" in response.headers["set-cookie"]
