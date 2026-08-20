"""Apply the approved M23 retention policy; schedule this command daily in production."""

from mealpilot.main import account_store, durable_runs


def main() -> int:
    refresh_count = account_store.purge_revoked_sessions(retention_days=30)
    event_count = durable_runs.store.purge_operational_events(retention_days=90)
    print(f"purged revoked_refresh_sessions={refresh_count} operational_events={event_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
