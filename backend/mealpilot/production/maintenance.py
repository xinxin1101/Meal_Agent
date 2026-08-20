"""Scheduled privacy retention and backup maintenance for production Compose."""

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def _backup() -> None:
    backup_dir = Path(os.getenv("MEALPILOT_BACKUP_DIR", "/backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"mealpilot-{stamp}.dump"
    subprocess.run(["pg_dump", os.environ["DATABASE_URL"], "-Fc", "-f", str(target)], check=True)
    keep_days = max(1, int(os.getenv("MEALPILOT_BACKUP_RETENTION_DAYS", "14")))
    cutoff = time.time() - keep_days * 86400
    for item in backup_dir.glob("mealpilot-*.dump"):
        if item.stat().st_mtime < cutoff:
            item.unlink()


def _purge() -> None:
    from mealpilot.main import account_store, durable_runs
    account_store.purge_revoked_sessions(retention_days=30)
    durable_runs.store.purge_operational_events(retention_days=90)


def main() -> int:
    interval = max(3600, int(os.getenv("MEALPILOT_MAINTENANCE_INTERVAL_SECONDS", "86400")))
    while True:
        _purge()
        _backup()
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
