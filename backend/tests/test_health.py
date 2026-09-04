from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import Database
from app.main import create_app


class StubDatabase:
    def __init__(self, connected: bool) -> None:
        self.connected = connected
        self.disposed = False

    def ping(self) -> bool:
        return self.connected

    def dispose(self) -> None:
        self.disposed = True


def test_health_reports_connected_database() -> None:
    database = StubDatabase(connected=True)
    application = create_app(Settings(), database)

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "connected",
        "service": "backend",
        "version": "0.1.0",
    }
    assert database.disposed is True


def test_root_describes_the_api() -> None:
    application = create_app(Settings(), StubDatabase(connected=True))

    with TestClient(application) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "service": "backend",
        "status": "ok",
        "health": "/health",
        "docs": "/docs",
    }


def test_health_reports_unavailable_database() -> None:
    application = create_app(Settings(), StubDatabase(connected=False))

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"


def test_health_checks_a_real_sqlalchemy_connection() -> None:
    application = create_app(Settings(), Database("sqlite+pysqlite:///:memory:"))

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["database"] == "connected"
