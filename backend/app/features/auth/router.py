"""Login endpoints and the guards every protected route uses.

require_user (any logged-in user) is attached by app/main.py to every data router; require_admin
guards the admin page's recruiter routes here. The session token travels in an HttpOnly, Secure,
SameSite=Lax cookie on /api; only its SHA-256 is stored. 401 always means "not logged in" (the
browser then shows the login page), so a wrong current password on the change form is a 400.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import MAX_ID, get_db
from app.features.auth.models import User
from app.features.auth.schemas import (
    CurrentUser,
    LoginRequest,
    NewRecruiter,
    PasswordChange,
    PasswordReset,
    Recruiter,
    RecruiterList,
)
from app.features.auth.service import (
    SESSION_HOURS,
    EmailTakenError,
    WrongPasswordError,
    add_recruiter,
    authenticate,
    change_password,
    end_session,
    find_session_user,
    list_recruiters,
    remove_recruiter,
    reset_recruiter_password,
    start_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE = "session"
COOKIE_PATH = "/api"
DATABASE_DOWN_MESSAGE = "The database is unavailable. Try again later."
NOT_LOGGED_IN_MESSAGE = "Not logged in."
WRONG_LOGIN_MESSAGE = "Email or password is incorrect."
RECRUITER_NOT_FOUND_MESSAGE = "Recruiter not found."

# auto_error=False: a missing cookie is our own 401 "Not logged in.", not FastAPI's 403.
session_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)
SessionToken = Annotated[str | None, Security(session_cookie)]
Db = Annotated[Session, Depends(get_db)]


def database_down() -> HTTPException:
    """The 503 every endpoint here answers when the database fails."""
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_DOWN_MESSAGE)


def require_user(token: SessionToken, db: Db) -> User:
    """FastAPI dependency: the logged-in user, from the session cookie.

    401 "Not logged in." with no cookie, or an unknown or expired session; 503 if the database
    fails. Attached to routers before their own dependencies, so it runs before the spending
    cap and any event stream.
    """
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN_MESSAGE)
    try:
        user = find_session_user(db, token)
    except SQLAlchemyError:
        logger.exception("Could not check the session: database error")
        raise database_down() from None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN_MESSAGE)
    return user


def require_admin(user: Annotated[User, Depends(require_user)]) -> User:
    """FastAPI dependency for the admin routes: require_user, then 403 unless an admin."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only.")
    return user


def clear_session_cookie(response: Response) -> None:
    """Tell the browser to drop the session cookie (same attributes it was set with)."""
    response.delete_cookie(
        SESSION_COOKIE, path=COOKIE_PATH, secure=True, httponly=True, samesite="lax"
    )


@router.post("/login", response_model=CurrentUser)
def login(body: LoginRequest, response: Response, db: Db) -> CurrentUser:
    """Check the email and password and start a 12-hour session in the cookie.

    401 "Email or password is incorrect." for an unknown email or a wrong password alike;
    503 if the database fails. Rate-limited with the password change (10 per 15 minutes).
    """
    try:
        user = authenticate(db, body.email, body.password)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, WRONG_LOGIN_MESSAGE)
        token = start_session(db, user)
        current = CurrentUser(email=user.email, role=user.role)
    except SQLAlchemyError:
        logger.exception("Could not log in: database error")
        raise database_down() from None
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_HOURS * 3600,
        path=COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return current


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: SessionToken, db: Db) -> Response:
    """End the session, if there is one, and clear the cookie (204). On a database error the
    answer is 503, but the cookie is still cleared.
    """
    response: Response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if token:
        try:
            end_session(db, token)
        except SQLAlchemyError:
            logger.exception("Could not log out: database error")
            response = JSONResponse(
                {"detail": DATABASE_DOWN_MESSAGE}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )
    clear_session_cookie(response)
    return response


@router.get("/me", response_model=CurrentUser)
def me(user: Annotated[User, Depends(require_user)]) -> CurrentUser:
    """The logged-in user's email and role (401 when not logged in)."""
    return CurrentUser(email=user.email, role=user.role)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_own_password(
    body: PasswordChange,
    user: Annotated[User, Depends(require_user)],
    token: SessionToken,
    db: Db,
) -> Response:
    """Change the logged-in user's password and end their other sessions (204).

    400 when the current password is wrong (not 401: that would log the user out); 503 if the
    database fails. Shares the login rate-limit bucket, so a stolen session can't guess faster.
    """
    try:
        change_password(db, user, body.current_password, body.new_password, token or "")
    except WrongPasswordError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.") from None
    except SQLAlchemyError:
        logger.exception("Could not change a password: database error")
        raise database_down() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/recruiters", response_model=RecruiterList, dependencies=[Depends(require_admin)])
def get_recruiters(db: Db) -> RecruiterList:
    """Every recruiter account, sorted by email (admins only)."""
    try:
        users = list_recruiters(db)
    except SQLAlchemyError:
        logger.exception("Could not list recruiters: database error")
        raise database_down() from None
    return RecruiterList(items=[Recruiter.model_validate(u, from_attributes=True) for u in users])


@router.post(
    "/recruiters",
    response_model=Recruiter,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_recruiter(body: NewRecruiter, db: Db) -> Recruiter:
    """Add a recruiter with the password the admin sets (201; admins only).

    409 when an account with this email exists (the admin's included); 503 if the database fails.
    """
    try:
        user = add_recruiter(db, body.email, body.password)
        # Read inside the try: after the commit, reading the row reloads it.
        return Recruiter.model_validate(user, from_attributes=True)
    except EmailTakenError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists."
        ) from None
    except SQLAlchemyError:
        logger.exception("Could not add a recruiter: database error")
        raise database_down() from None


@router.put(
    "/recruiters/{recruiter_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def reset_password(
    recruiter_id: Annotated[int, Path(ge=1, le=MAX_ID)], body: PasswordReset, db: Db
) -> Response:
    """Set a recruiter's password and end all their sessions (204; admins only).

    404 when the id isn't a recruiter's (also the admin's own); 503 if the database fails.
    """
    try:
        found = reset_recruiter_password(db, recruiter_id, body.password)
    except SQLAlchemyError:
        logger.exception("Could not reset a password: database error")
        raise database_down() from None
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, RECRUITER_NOT_FOUND_MESSAGE)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/recruiters/{recruiter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_recruiter(recruiter_id: Annotated[int, Path(ge=1, le=MAX_ID)], db: Db) -> Response:
    """Remove a recruiter and their sessions (204; admins only).

    404 when the id isn't a recruiter's (also the admin's own); 503 if the database fails.
    """
    try:
        removed = remove_recruiter(db, recruiter_id)
    except SQLAlchemyError:
        logger.exception("Could not remove a recruiter: database error")
        raise database_down() from None
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, RECRUITER_NOT_FOUND_MESSAGE)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
