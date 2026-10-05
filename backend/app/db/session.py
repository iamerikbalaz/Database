from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria
from sqlalchemy.pool import StaticPool


class ApplicationSession(Session):
    """Application reads omit deleted material records, including relationship loads."""


@event.listens_for(ApplicationSession, "do_orm_execute")
def exclude_deleted_materials(execution):
    if execution.is_select and not execution.execution_options.get("include_deleted_materials", False):
        from app.db.models import PBRMaterial
        execution.statement = execution.statement.options(with_loader_criteria(
            PBRMaterial, lambda material: material.deleted_at.is_(None), include_aliases=True))


class Database:
    def __init__(self, url: str) -> None:
        engine_options: dict[str, object] = {"pool_pre_ping": True, "hide_parameters": True}

        if url.startswith("sqlite"):
            engine_options["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                engine_options["poolclass"] = StaticPool

        self.engine: Engine = create_engine(url, **engine_options)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=ApplicationSession)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def dispose(self) -> None:
        self.engine.dispose()
