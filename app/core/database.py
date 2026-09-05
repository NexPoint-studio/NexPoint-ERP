from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
import unicodedata
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str) -> Engine:
    if not database_url.lower().startswith("sqlite"):
        raise ValueError("A carcaça local permite somente banco SQLite.")
    connect_args = {"check_same_thread": False}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.create_function("casefold", 1, lambda value: unicodedata.normalize("NFKC", value or "").casefold(), deterministic=True)
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def session_dependency(factory: sessionmaker[Session]):
    def _session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session
    return _session
