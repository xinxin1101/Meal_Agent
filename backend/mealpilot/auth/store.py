from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from mealpilot.auth.security import hash_password, token_hash, verify_password
from mealpilot.domain.contract_migration import migrate_persisted_document
from mealpilot.domain.models import AccountProfile, AccountSummary, SaveAccountProfileCommand, UserProfile


class AccountConflict(Exception):
    pass


class AuthenticationFailed(Exception):
    pass


class SqliteAccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    user_id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL, created_at TEXT NOT NULL, deleted_at TEXT,
                    role TEXT NOT NULL DEFAULT 'USER'
                );
                CREATE TABLE IF NOT EXISTS refresh_sessions (
                    session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT,
                    replaced_by TEXT, FOREIGN KEY(user_id) REFERENCES accounts(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_refresh_sessions_user ON refresh_sessions(user_id,expires_at);
                CREATE TABLE IF NOT EXISTS account_profiles (
                    user_id TEXT PRIMARY KEY, profile_version INTEGER NOT NULL,
                    profile_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES accounts(user_id) ON DELETE CASCADE
                );
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}
            if "role" not in columns:
                connection.execute("ALTER TABLE accounts ADD COLUMN role TEXT NOT NULL DEFAULT 'USER'")

    def ensure_admin(self, username: str, password: str, display_name: str) -> AccountSummary:
        """Create the development bootstrap administrator without storing plaintext credentials."""
        normalized = username.strip().casefold()
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE email=?", (normalized,)).fetchone()
            if row is None:
                now = datetime.now(timezone.utc)
                connection.execute(
                    "INSERT INTO accounts(user_id,email,display_name,password_hash,created_at,role) VALUES(?,?,?,?,?,?)",
                    ("admin-root", normalized, display_name.strip(), hash_password(password), now.isoformat(), "ADMIN"),
                )
            else:
                connection.execute("UPDATE accounts SET role='ADMIN',deleted_at=NULL WHERE email=?", (normalized,))
            result = connection.execute("SELECT * FROM accounts WHERE email=?", (normalized,)).fetchone()
        return self._account(result)

    def register(self, email: str, password: str, display_name: str) -> AccountSummary:
        now = datetime.now(timezone.utc)
        account = AccountSummary(user_id=f"user-{uuid4().hex}", email=email, display_name=display_name.strip(), created_at=now, role="USER")
        try:
            with self._lock, self._connection() as connection:
                connection.execute("INSERT INTO accounts(user_id,email,display_name,password_hash,created_at) VALUES(?,?,?,?,?)", (account.user_id, account.email, account.display_name, hash_password(password), now.isoformat()))
        except sqlite3.IntegrityError as error:
            raise AccountConflict("email is already registered") from error
        return account

    def authenticate(self, email: str, password: str) -> AccountSummary:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE email=? AND deleted_at IS NULL", (email,)).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise AuthenticationFailed("invalid credentials")
        return self._account(row)

    def get(self, user_id: str) -> AccountSummary:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE user_id=? AND deleted_at IS NULL", (user_id,)).fetchone()
        if row is None:
            raise KeyError(user_id)
        return self._account(row)

    def verify_account_password(self, user_id: str, password: str) -> bool:
        with self._connection() as connection:
            row = connection.execute("SELECT password_hash FROM accounts WHERE user_id=? AND deleted_at IS NULL", (user_id,)).fetchone()
        return bool(row and verify_password(password, row["password_hash"]))

    def create_refresh_session(self, user_id: str, lifetime_seconds: int) -> str:
        raw = secrets.token_urlsafe(48)
        now = datetime.now(timezone.utc)
        with self._lock, self._connection() as connection:
            connection.execute("INSERT INTO refresh_sessions(session_id,user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?,?)", (f"session-{uuid4().hex}", user_id, token_hash(raw), (now + timedelta(seconds=lifetime_seconds)).isoformat(), now.isoformat()))
        return raw

    def rotate_refresh_session(self, raw: str, lifetime_seconds: int) -> tuple[AccountSummary, str]:
        now = datetime.now(timezone.utc)
        replacement = secrets.token_urlsafe(48)
        replacement_id = f"session-{uuid4().hex}"
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM refresh_sessions WHERE token_hash=?", (token_hash(raw),)).fetchone()
            if row is None or row["revoked_at"] is not None or datetime.fromisoformat(row["expires_at"]) <= now:
                raise AuthenticationFailed("invalid refresh session")
            updated = connection.execute("UPDATE refresh_sessions SET revoked_at=?,replaced_by=? WHERE session_id=? AND revoked_at IS NULL", (now.isoformat(), replacement_id, row["session_id"]))
            if updated.rowcount != 1:
                raise AuthenticationFailed("refresh session already used")
            connection.execute("INSERT INTO refresh_sessions(session_id,user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?,?)", (replacement_id, row["user_id"], token_hash(replacement), (now + timedelta(seconds=lifetime_seconds)).isoformat(), now.isoformat()))
            account_row = connection.execute("SELECT * FROM accounts WHERE user_id=? AND deleted_at IS NULL", (row["user_id"],)).fetchone()
            if account_row is None:
                raise AuthenticationFailed("account unavailable")
            return self._account(account_row), replacement

    def revoke_refresh_session(self, raw: str | None) -> None:
        if not raw:
            return
        with self._lock, self._connection() as connection:
            connection.execute("UPDATE refresh_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (datetime.now(timezone.utc).isoformat(), token_hash(raw)))

    def revoke_all(self, user_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("UPDATE refresh_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (datetime.now(timezone.utc).isoformat(), user_id))

    def profile(self, user_id: str) -> AccountProfile:
        self.get(user_id)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM account_profiles WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return AccountProfile(profile_version=0)
        return AccountProfile(profile_version=row["profile_version"], profile=UserProfile.model_validate(migrate_persisted_document(json.loads(row["profile_json"]))), updated_at=row["updated_at"])

    def save_profile(self, user_id: str, command: SaveAccountProfileCommand) -> AccountProfile:
        now = datetime.now(timezone.utc)
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT profile_version FROM account_profiles WHERE user_id=?", (user_id,)).fetchone()
            version = int(row[0]) if row else 0
            if version != command.expected_profile_version:
                raise AccountConflict("stale profile version")
            next_version = version + 1
            connection.execute("INSERT INTO account_profiles(user_id,profile_version,profile_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET profile_version=excluded.profile_version,profile_json=excluded.profile_json,updated_at=excluded.updated_at", (user_id, next_version, command.profile.model_dump_json(), now.isoformat()))
        return AccountProfile(profile_version=next_version, profile=command.profile, updated_at=now)

    def delete_account(self, user_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM refresh_sessions WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM account_profiles WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM accounts WHERE user_id=?", (user_id,))

    def purge_revoked_sessions(self, retention_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM refresh_sessions WHERE revoked_at IS NOT NULL AND revoked_at<?", (cutoff,))
            return cursor.rowcount

    @staticmethod
    def _account(row: sqlite3.Row) -> AccountSummary:
        return AccountSummary(user_id=row["user_id"], email=row["email"], display_name=row["display_name"], created_at=row["created_at"], role=row["role"] if "role" in row.keys() else "USER")

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
