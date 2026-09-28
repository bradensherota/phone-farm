from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DB_PATH


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    _migrate()


# Tiny additive migrations for columns added after a table already existed.
_ADDED_COLUMNS = [
    ("media", "probed", "BOOLEAN NOT NULL DEFAULT 0"),
    ("media", "added_at", "DATETIME"),
    ("media", "albums", "VARCHAR NOT NULL DEFAULT ''"),
    ("devices", "story_album", "VARCHAR NOT NULL DEFAULT 'Stories'"),
    ("devices", "queue_album", "VARCHAR NOT NULL DEFAULT 'Farm Queue'"),
    ("devices", "prepare_lead_min", "INTEGER NOT NULL DEFAULT 60"),
]


def _migrate() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table in insp.get_table_names():
                cols = {c["name"] for c in insp.get_columns(table)}
                if column not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
