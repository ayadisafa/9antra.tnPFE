"""Moves the application's data from its SQLite file to PostgreSQL, and checks that every table arrived whole.

    python -m app.postgres_move <the SQLite file> <the PostgreSQL address>

The PostgreSQL database is emptied first, then its tables are made by the application itself (the same models). The
links between tables (foreign keys) are left out: the application has always run without them on SQLite, and some of
its deletes rely on that. Each value is turned from the way SQLite kept it (dates as text, yes/no as 0/1) into its real
type. The SQLite file is only read.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import date, datetime, time, timezone

logger = logging.getLogger("postgres_move")
BATCH = 500


def _when(value):
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        text = str(value).strip()
        if not text:
            return None
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is not None:          # kept as the application keeps its times: UTC, without the zone
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def convert(value, kind, problems: list, where: str):
    """One value from SQLite into the type of its PostgreSQL column."""
    from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, Integer, LargeBinary, Numeric, String, Text, Time

    if value is None:
        return None
    try:
        if isinstance(kind, Boolean):
            return value.strip().lower() in ("1", "true", "t", "yes", "y") if isinstance(value, str) else bool(value)
        if isinstance(kind, DateTime):
            return _when(value)
        if isinstance(kind, Date):
            moment = _when(value)
            return moment.date() if isinstance(moment, datetime) else moment
        if isinstance(kind, Time):
            return value if isinstance(value, time) else time.fromisoformat(str(value).strip())
        if isinstance(kind, Enum):
            names = list(kind.enums)
            if value in names:
                return value
            for member in (kind.enum_class or []):
                if str(member.value) == str(value) or member.name.lower() == str(value).lower():
                    return member.name
            problems.append(f"{where}: {value!r} is not one of {names}")
            return value
        if isinstance(kind, Integer):
            return value if isinstance(value, int) else (int(float(value)) if str(value).strip() else None)
        if isinstance(kind, (Float, Numeric)):
            return value if isinstance(value, (int, float)) else (float(value) if str(value).strip() else None)
        if isinstance(kind, JSON):
            return json.loads(value) if isinstance(value, (str, bytes)) else value
        if isinstance(kind, LargeBinary):
            return bytes(value) if not isinstance(value, str) else value.encode("utf-8")
        if isinstance(kind, (String, Text)):
            text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
            return text.replace("\x00", "")      # PostgreSQL refuses this character in text
    except (ValueError, TypeError) as exc:
        problems.append(f"{where}: {value!r} could not be read ({exc.__class__.__name__}); left empty")
        return None
    return value


def move(source: str) -> dict:
    """Copy every table of `source` (a SQLite file) into the database of DATABASE_URL (PostgreSQL)."""
    from sqlalchemy import Integer, inspect, text

    import app.database as database

    if database.IS_SQLITE:
        raise SystemExit("DATABASE_URL must name the PostgreSQL database to fill, not a SQLite file.")
    engine = database.engine
    with engine.begin() as connection:              # emptied: the move always starts from nothing
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    import app.main  # noqa: F401  (every model of the application, and its tables made)

    database.Base.metadata.create_all(bind=engine)
    quote = engine.dialect.identifier_preparer.quote
    with engine.begin() as connection:              # no links between tables, as on SQLite
        for table, name in connection.execute(text(
                "SELECT conrelid::regclass::text, conname FROM pg_constraint "
                "WHERE contype = 'f' AND connamespace = 'public'::regnamespace")).all():
            connection.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {quote(name)}"))

    reader = sqlite3.connect(f"file:{os.path.abspath(source)}?mode=ro", uri=True)
    present = {row[0] for row in reader.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    report = {"tables": {}, "left_behind": sorted(present - set(database.Base.metadata.tables) - {"sqlite_sequence"}), "problems": []}
    for table in database.Base.metadata.sorted_tables:
        if table.name not in present:
            report["tables"][table.name] = {"source": 0, "target": 0}
            continue
        have = [row[1] for row in reader.execute(f'PRAGMA table_info("{table.name}")')]
        columns = [column for column in table.columns if column.name in have]
        listed = ", ".join(f'"{column.name}"' for column in columns)
        cursor = reader.execute(f'SELECT {listed} FROM "{table.name}"')
        copied = 0
        with engine.begin() as connection:
            while True:
                rows = cursor.fetchmany(BATCH)
                if not rows:
                    break
                batch = [{column.name: convert(value, column.type, report["problems"], f"{table.name}.{column.name}")
                          for column, value in zip(columns, row)} for row in rows]
                connection.execute(table.insert(), batch)
                copied += len(batch)
        report["tables"][table.name] = {"source": reader.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0], "target": copied}
    reader.close()

    # The numbers PostgreSQL gives to new rows continue after the ones copied.
    with engine.begin() as connection:
        for table in database.Base.metadata.sorted_tables:
            keys = list(table.primary_key.columns)
            if len(keys) == 1 and isinstance(keys[0].type, Integer):
                connection.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', '{keys[0].name}'), "
                    f"COALESCE((SELECT MAX({quote(keys[0].name)}) FROM {quote(table.name)}), 0) + 1, false)"))
        for name, counts in report["tables"].items():
            counts["target"] = connection.execute(text(f"SELECT COUNT(*) FROM {quote(name)}")).scalar()
    report["whole"] = all(counts["source"] == counts["target"] for counts in report["tables"].values())
    report["rows"] = sum(counts["target"] for counts in report["tables"].values())
    inspector = inspect(engine)
    report["made"] = len(inspector.get_table_names())
    return report


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    source, target = sys.argv[1], sys.argv[2]
    os.environ["DATABASE_URL"] = target
    sys.stdout.reconfigure(encoding="utf-8")
    report = move(source)
    for problem in report["problems"][:40]:
        print("  value:", problem)
    short = [f"{name} {counts['source']}->{counts['target']}" for name, counts in report["tables"].items() if counts["source"] != counts["target"]]
    print(f"{report['rows']} rows in {len(report['tables'])} tables ({report['made']} made in PostgreSQL); "
          f"{'every table is whole' if report['whole'] else 'NOT WHOLE: ' + ', '.join(short)}; "
          f"{len(report['problems'])} value(s) needed a look; left behind (not used by the application): {', '.join(report['left_behind']) or 'none'}")
    raise SystemExit(0 if report["whole"] else 1)


if __name__ == "__main__":
    main()
