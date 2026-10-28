"""Lightweight, idempotent schema upgrade.

`Base.metadata.create_all` creates missing tables.  For tables that already exist (for example a
database created by the first version of this project) we add any missing *columns* so no data
is lost.  Unique indexes that cannot be created because legacy data contains duplicates are
skipped with a warning instead of failing start-up.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

from app.database import Base, engine
from app.models import models  # noqa: F401  (register models)

log = logging.getLogger("migrations")


def _add_missing_columns() -> None:
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = str(CreateColumn(col).compile(dialect=engine.dialect))
                # SQLite cannot add NOT NULL / UNIQUE / PRIMARY KEY columns without a default.
                ddl = ddl.replace("NOT NULL", "").replace("UNIQUE", "").strip()
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))
                log.info("migrated: added %s.%s", table.name, col.name)


def _ensure_indexes() -> None:
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in insp.get_table_names():
            continue
        existing_idx = {i["name"] for i in insp.get_indexes(table.name)}
        existing_uq = {u["name"] for u in insp.get_unique_constraints(table.name)}
        for uc in table.constraints:
            name = getattr(uc, "name", None)
            if not name or not uc.__class__.__name__ == "UniqueConstraint":
                continue
            if name in existing_uq or name in existing_idx:
                continue
            cols = ", ".join(f'"{c.name}"' for c in uc.columns)
            try:
                with engine.begin() as conn:
                    conn.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS "{name}" ON "{table.name}" ({cols})'))
            except Exception as exc:  # legacy duplicates
                log.warning("could not create unique index %s: %s", name, exc)


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _ensure_indexes()
