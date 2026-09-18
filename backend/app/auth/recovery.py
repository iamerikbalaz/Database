"""Host administrator recovery. Never accepts a password in argv or environment."""
import argparse
import getpass

from sqlalchemy import select, text

from app.auth.access import ACCESS_LOCK
from app.auth.accounts import set_initial_access
from app.auth.security import PasswordService
from app.auth.service import audit
from app.core.config import get_settings
from app.db.models import InternalUser
from app.db.session import Database
from app.auth.security import normalize_email


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset access for an existing active REAWOTE account.")
    parser.add_argument("--email", required=True)
    arguments = parser.parse_args()
    password = getpass.getpass("Temporary password: ")
    confirmation = getpass.getpass("Confirm temporary password: ")
    if password != confirmation:
        parser.error("Passwords do not match.")
    settings = get_settings()
    database = Database(settings.resolved_database_url)
    try:
        with database.session() as session:
            if session.get_bind().dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ACCESS_LOCK})
            user = session.scalar(select(InternalUser).where(InternalUser.email == normalize_email(arguments.email)))
            if user is None:
                parser.error("Existing account not found.")
            set_initial_access(session, user, PasswordService(settings), password, source="HOST", actor_id=None)
            user_id = user.id
            session.commit()
        audit("host_access_recovery", user_id=user_id)
    finally:
        database.dispose()
    print(f"Access reset for account {user_id}; password change required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
