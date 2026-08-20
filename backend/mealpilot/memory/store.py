import json
import sqlite3
from pathlib import Path

from mealpilot.domain.models import PreferenceMemory, PreferenceMemoryItem
from mealpilot.domain.contract_migration import migrate_preference_items


class PreferenceMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS preference_memory (user_id TEXT PRIMARY KEY, items_json TEXT NOT NULL)")

    def get(self, user_id: str) -> PreferenceMemory:
        with self._connection() as connection:
            row = connection.execute("SELECT items_json FROM preference_memory WHERE user_id=?", (user_id,)).fetchone()
        items = migrate_preference_items(json.loads(row[0])) if row else []
        return PreferenceMemory(user_id=user_id, items=[PreferenceMemoryItem.model_validate(item) for item in items])

    def replace(self, memory: PreferenceMemory) -> PreferenceMemory:
        serialized = json.dumps([item.model_dump() for item in memory.items], ensure_ascii=False)
        with self._connection() as connection:
            connection.execute("INSERT INTO preference_memory(user_id, items_json) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET items_json=excluded.items_json", (memory.user_id, serialized))
        return memory

    def delete(self, user_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM preference_memory WHERE user_id=?", (user_id,))

    def _connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
