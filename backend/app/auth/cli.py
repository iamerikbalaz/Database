from __future__ import annotations

import argparse
import getpass
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select, text

from app.auth.security import (
    PasswordPolicyError,
    PasswordService,
    normalize_email,
    validate_new_password,
    validate_password_input,
)
from app.core.config import Settings, get_settings
from app.db.models import InternalUser, InternalUserRole, UserCredential
from app.db.session import Database
from app.schemas import InternalUserCreate
from app.account_security_history import append_security_event


class AdminDatabase(Protocol):
    def session(self): ...


class AdminProvisioningError(RuntimeError):
    pass


def provision_first_admin(
    database: AdminDatabase,
    settings: Settings,
    *,
    email: str,
    display_name: str,
    password: str,
) -> UUID:
    validated = InternalUserCreate(
        email=normalize_email(email),
        display_name=display_name,
        role=InternalUserRole.ADMIN,
        is_active=True,
    )
    normalized_password = validate_new_password(
        password,
        email=validated.email,
        display_name=validated.display_name,
    )
    password_service = PasswordService(settings)

    with database.session() as db_session:
        if db_session.get_bind().dialect.name == "postgresql":
            db_session.execute(text("SELECT pg_advisory_xact_lock(737824901)"))
        if db_session.scalar(select(func.count()).select_from(UserCredential)) != 0:
            raise AdminProvisioningError(
                "Credentials already exist; first-admin provisioning is disabled."
            )
        user = db_session.scalar(
            select(InternalUser).where(InternalUser.email == validated.email).with_for_update()
        )
        if user is None:
            user = InternalUser(
                email=validated.email,
                display_name=validated.display_name,
                role=InternalUserRole.ADMIN.value,
                is_active=True,
            )
            db_session.add(user)
            db_session.flush()
        else:
            user.display_name = validated.display_name
            user.role = InternalUserRole.ADMIN.value
            user.is_active = True
        db_session.add(
            UserCredential(
                user_id=user.id,
                password_hash=password_service.hash_password(normalized_password),
                must_change_password=True,
            )
        )
        append_security_event(db_session, user.id, "FIRST_ADMIN_PROVISIONED")
        db_session.commit()
        return user.id


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision the first REAWOTE administrator.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    arguments = parser.parse_args()
    password = getpass.getpass("Initial password: ")
    confirmation = getpass.getpass("Confirm initial password: ")
    try:
        password = validate_password_input(password)
        confirmation = validate_password_input(confirmation)
    except PasswordPolicyError as exc:
        parser.error(str(exc))
    if password != confirmation:
        parser.error("Passwords do not match.")

    settings = get_settings()
    database = Database(settings.resolved_database_url)
    try:
        user_id = provision_first_admin(
            database,
            settings,
            email=arguments.email,
            display_name=arguments.display_name,
            password=password,
        )
    finally:
        database.dispose()
    print(f"First administrator provisioned: {user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
