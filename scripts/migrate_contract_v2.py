"""Preview or apply the no-price/no-equipment persistence migration.

The default mode is read-only.  ``--apply`` first copies every affected SQLite
database into a timestamped backup directory and then updates only JSON columns.
Free-text conversation content is retained as historical user-authored text.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from mealpilot.domain.contract_migration import (  # noqa: E402
    migrate_persisted_document,
    migrate_persisted_proposal,
    migrate_preference_items,
)


JsonTransform = Callable[[object], object]

MIGRATIONS: dict[str, list[tuple[str, str, JsonTransform]]] = {
    "accounts.sqlite3": [("account_profiles", "profile_json", migrate_persisted_document)],
    "meal-plan-history.sqlite3": [
        ("adopted_plans", "snapshot_json", migrate_persisted_document),
        ("history_idempotency", "response_json", migrate_persisted_document),
        ("feedback_idempotency", "request_json", migrate_persisted_document),
        ("feedback_idempotency", "response_json", migrate_persisted_document),
    ],
    "mealpilot.sqlite3": [
        ("runs", "command_json", migrate_persisted_document),
        ("runs", "result_json", migrate_persisted_document),
        ("runs", "proposal_json", migrate_persisted_proposal),
        ("decisions", "response_json", migrate_persisted_document),
        ("run_events", "payload_json", migrate_persisted_document),
    ],
    "conversations.sqlite3": [
        ("conversation_messages", "snapshot_json", migrate_persisted_document),
        ("conversation_idempotency", "response_json", migrate_persisted_document),
    ],
    "preference-memory.sqlite3": [
        ("preference_memory", "items_json", lambda value: migrate_preference_items(value if isinstance(value, list) else [])),
    ],
}


def migrate_database(path: Path, operations: list[tuple[str, str, JsonTransform]], apply: bool) -> int:
    changed = 0
    with sqlite3.connect(path) as connection:
        for table, column, transform in operations:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            for rowid, raw in connection.execute(f'SELECT rowid,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL').fetchall():
                original = json.loads(raw)
                migrated = transform(original)
                if migrated == original:
                    continue
                changed += 1
                if apply:
                    connection.execute(
                        f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',
                        (json.dumps(migrated, ensure_ascii=False, separators=(",", ":")), rowid),
                    )
        if not apply:
            connection.rollback()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--apply", action="store_true", help="backup and rewrite the affected JSON columns")
    args = parser.parse_args()
    runtime = args.runtime_dir.resolve()
    existing = [(runtime / name, operations) for name, operations in MIGRATIONS.items() if (runtime / name).exists()]
    if not existing:
        print(f"No supported SQLite databases found in {runtime}")
        return 0

    backup: Path | None = None
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = runtime / "backups" / f"contract-v2-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        for path, _ in existing:
            shutil.copy2(path, backup / path.name)

    total = 0
    for path, operations in existing:
        changed = migrate_database(path, operations, args.apply)
        total += changed
        print(f"{path.name}: {changed} JSON document(s) {'migrated' if args.apply else 'would change'}")
    print(f"total: {total}")
    if backup:
        print(f"backup: {backup}")
    else:
        print("dry run only; pass --apply to create a backup and migrate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
