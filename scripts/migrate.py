"""Apply ordered, transactional PostgreSQL SQL migrations."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    try:
        import psycopg
    except ImportError as error:
        raise SystemExit("Install production dependencies: pip install -e '.[production]'") from error
    migrations = sorted((ROOT / "migrations" / "versions").glob("*.sql"))
    with psycopg.connect(database_url) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()}
        for migration in migrations:
            version = migration.name.split("_", maxsplit=2)[:2]
            version_key = "_".join(version)
            if version_key in applied:
                print(f"skipped {migration.name}")
                continue
            connection.execute(migration.read_text(encoding="utf-8"))
            print(f"applied {migration.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
