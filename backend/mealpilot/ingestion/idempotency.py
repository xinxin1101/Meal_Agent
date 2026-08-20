"""Small local idempotency registry for administrator review mutations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock
from typing import Callable

from mealpilot.ingestion.review import RecipeReviewItem, ReviewConflict


class ReviewIdempotencyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connection() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS operations(scope TEXT NOT NULL,idempotency_key TEXT NOT NULL,fingerprint TEXT NOT NULL,response_json TEXT NOT NULL,PRIMARY KEY(scope,idempotency_key))")

    def execute(self, scope: str, key: str, fingerprint: str, action: Callable[[], RecipeReviewItem]) -> RecipeReviewItem:
        if not key.strip() or len(key) > 200:
            raise ValueError("Idempotency-Key must contain 1-200 characters")
        with self._lock:
            with self._connection() as connection:
                existing = connection.execute("SELECT fingerprint,response_json FROM operations WHERE scope=? AND idempotency_key=?", (scope, key)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ReviewConflict("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST")
                return RecipeReviewItem.model_validate_json(existing["response_json"])
            result = action()
            with self._connection() as connection:
                connection.execute("INSERT INTO operations(scope,idempotency_key,fingerprint,response_json) VALUES(?,?,?,?)", (scope, key, fingerprint, result.model_dump_json()))
            return result

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection
