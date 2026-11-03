"""Request and response models for login, the current user, password changes and the admin
page's recruiter management.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, StringConstraints

# Every password set anywhere (add, reset, change, ADMIN_PASSWORD) must fit these limits.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
MAX_EMAIL_LENGTH = 254


def is_storable(text: str) -> bool:
    """False for text Postgres can't hold (a NUL). A lone surrogate never gets this far:
    FastAPI's JSON decoding already rejects it.
    """
    return "\x00" not in text


def require_storable(text: str) -> str:
    """Validator: a 422 for text that is_storable rejects, instead of a false database error."""
    if not is_storable(text):
        raise ValueError("contains a character that can't be stored")
    return text


Role = Literal["admin", "recruiter"]
# A password being checked (login, current password): any non-empty value up to the cap.
GivenPassword = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_PASSWORD_LENGTH),
    AfterValidator(require_storable),
]
# A password being set: the 12-character minimum applies.
NewPassword = Annotated[
    str,
    StringConstraints(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH),
    AfterValidator(require_storable),
]
# A new account's email: trimmed and lower-cased first, then one "@" with no spaces around it.
# No email-validator package: a typo only hurts the admin, who can remove and re-add the account.
NewEmail = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=MAX_EMAIL_LENGTH,
        pattern=r"^[^@\s]+@[^@\s]+$",
    ),
    AfterValidator(require_storable),
]


class LoginRequest(BaseModel):
    """POST /api/auth/login. The email isn't format-checked: a bad one is just a wrong login."""

    email: Annotated[str, StringConstraints(min_length=1, max_length=MAX_EMAIL_LENGTH)]
    password: GivenPassword


class CurrentUser(BaseModel):
    """The logged-in user (login and GET /api/auth/me)."""

    email: str
    role: Role


class PasswordChange(BaseModel):
    """POST /api/auth/password: the user's current password and their new one."""

    current_password: GivenPassword
    new_password: NewPassword


class NewRecruiter(BaseModel):
    """POST /api/auth/recruiters: the new recruiter's email and the password the admin sets."""

    email: NewEmail
    password: NewPassword


class PasswordReset(BaseModel):
    """PUT /api/auth/recruiters/{id}/password: the recruiter's new password."""

    password: NewPassword


class Recruiter(BaseModel):
    """One recruiter account on the admin page."""

    id: int
    email: str
    created_at: datetime


class RecruiterList(BaseModel):
    """Every recruiter, sorted by email (no paging: there are only a handful)."""

    items: list[Recruiter]
