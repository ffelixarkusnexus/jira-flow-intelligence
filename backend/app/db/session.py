from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _enable_sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
    """SQLite ignores FK constraints unless PRAGMA foreign_keys=ON. CASCADE deletes
    rely on this — without it, deleting a tenant orphans its issues.
    Postgres enforces FKs by default; this hook is a no-op there.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


def _build_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        db_path = database_url.replace("sqlite:///", "")
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
        event.listen(eng, "connect", _enable_sqlite_foreign_keys)
        return eng
    return create_engine(database_url, future=True, pool_pre_ping=True)


settings = get_settings()
engine = _build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
