from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from app.auth.security import MAX_PASSWORD_LENGTH, normalize_email
from app.db.models import InternalUserRole
from app.schemas import ApiSchema, EmailAddress, Name


PasswordInput = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_PASSWORD_LENGTH),
]


class LoginRequest(ApiSchema):
    email: EmailAddress
    password: PasswordInput

    @field_validator("email", mode="before")
    @classmethod
    def normalize_unicode_email(cls, value: object) -> object:
        return normalize_email(value) if isinstance(value, str) else value


class PublicUser(ApiSchema):
    id: UUID
    display_name: Name
    email: EmailAddress
    role: InternalUserRole


class AuthSessionResponse(ApiSchema):
    user: PublicUser
    must_change_password: bool
    csrf_token: str


class ChangePasswordRequest(ApiSchema):
    current_password: PasswordInput
    new_password: PasswordInput


class LogoutResponse(ApiSchema):
    status: str = "logged_out"


class ChangePasswordResponse(ApiSchema):
    status: str = "password_changed"
    reauthentication_required: bool = True
    changed_at: datetime
