from sqlalchemy.orm import Session

from .db import session_scope
from .models import DEFAULT_SETTINGS, Setting


def ensure_defaults() -> None:
    with session_scope() as s:
        existing = {row.key for row in s.query(Setting).all()}
        for k, v in DEFAULT_SETTINGS.items():
            if k not in existing:
                s.add(Setting(key=k, value=v))


def get_all(s: Session | None = None) -> dict[str, str]:
    if s is not None:
        return {row.key: row.value for row in s.query(Setting).all()}
    with session_scope() as s2:
        return {row.key: row.value for row in s2.query(Setting).all()}


def get(key: str, default: str = "") -> str:
    return get_all().get(key, default)


def set_many(values: dict[str, str]) -> dict[str, str]:
    with session_scope() as s:
        for k, v in values.items():
            row = s.get(Setting, k)
            if row is None:
                s.add(Setting(key=k, value=str(v)))
            else:
                row.value = str(v)
        s.flush()
        return get_all(s)


def as_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}
