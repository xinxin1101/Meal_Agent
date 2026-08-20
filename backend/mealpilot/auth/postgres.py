from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from psycopg import connect
from psycopg.rows import dict_row

from mealpilot.auth.security import hash_password, token_hash, verify_password
from mealpilot.auth.store import AccountConflict, AuthenticationFailed
from mealpilot.domain.models import AccountProfile, AccountSummary, SaveAccountProfileCommand, UserProfile
from mealpilot.domain.contract_migration import migrate_persisted_document


class PostgresAccountStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def register(self, email: str, password: str, display_name: str) -> AccountSummary:
        now = datetime.now(timezone.utc)
        account = AccountSummary(user_id=f"user-{uuid4().hex}", email=email, display_name=display_name.strip(), created_at=now, role="USER")
        try:
            with connect(self.database_url) as connection:
                connection.execute(
                    "INSERT INTO accounts(user_id,email,display_name,password_hash,created_at) VALUES(%s,%s,%s,%s,%s)",
                    (account.user_id, account.email, account.display_name, hash_password(password), now),
                )
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise AccountConflict("email is already registered") from error
            raise
        return account

    def ensure_admin(self, username: str, password: str, display_name: str) -> AccountSummary:
        normalized = username.strip().casefold()
        now = datetime.now(timezone.utc)
        with connect(self.database_url, row_factory=dict_row) as connection:
            connection.execute(
                "INSERT INTO accounts(user_id,email,display_name,password_hash,created_at,role) VALUES(%s,%s,%s,%s,%s,'ADMIN') "
                "ON CONFLICT(email) DO UPDATE SET role='ADMIN',deleted_at=NULL",
                ("admin-root", normalized, display_name.strip(), hash_password(password), now),
            )
            row = connection.execute("SELECT * FROM accounts WHERE email=%s", (normalized,)).fetchone()
        return self._account(row)

    def authenticate(self, email: str, password: str) -> AccountSummary:
        with connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT * FROM accounts WHERE email=%s AND deleted_at IS NULL", (email,)).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise AuthenticationFailed("invalid credentials")
        return self._account(row)

    def get(self, user_id: str) -> AccountSummary:
        with connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT * FROM accounts WHERE user_id=%s AND deleted_at IS NULL", (user_id,)).fetchone()
        if row is None:
            raise KeyError(user_id)
        return self._account(row)

    def verify_account_password(self, user_id: str, password: str) -> bool:
        with connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT password_hash FROM accounts WHERE user_id=%s AND deleted_at IS NULL", (user_id,)).fetchone()
        return bool(row and verify_password(password, row["password_hash"]))

    def create_refresh_session(self, user_id: str, lifetime_seconds: int) -> str:
        raw = secrets.token_urlsafe(48)
        now = datetime.now(timezone.utc)
        with connect(self.database_url) as connection:
            connection.execute(
                "INSERT INTO refresh_sessions(session_id,user_id,token_hash,expires_at,created_at) VALUES(%s,%s,%s,%s,%s)",
                (f"session-{uuid4().hex}", user_id, token_hash(raw), now + timedelta(seconds=lifetime_seconds), now),
            )
        return raw

    def rotate_refresh_session(self, raw: str, lifetime_seconds: int) -> tuple[AccountSummary, str]:
        now = datetime.now(timezone.utc)
        replacement = secrets.token_urlsafe(48)
        replacement_id = f"session-{uuid4().hex}"
        with connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT * FROM refresh_sessions WHERE token_hash=%s FOR UPDATE", (token_hash(raw),)).fetchone()
            if row is None or row["revoked_at"] is not None or row["expires_at"] <= now:
                raise AuthenticationFailed("invalid refresh session")
            updated = connection.execute(
                "UPDATE refresh_sessions SET revoked_at=%s,replaced_by=%s WHERE session_id=%s AND revoked_at IS NULL",
                (now, replacement_id, row["session_id"]),
            )
            if updated.rowcount != 1:
                raise AuthenticationFailed("refresh session already used")
            connection.execute(
                "INSERT INTO refresh_sessions(session_id,user_id,token_hash,expires_at,created_at) VALUES(%s,%s,%s,%s,%s)",
                (replacement_id, row["user_id"], token_hash(replacement), now + timedelta(seconds=lifetime_seconds), now),
            )
            account_row = connection.execute("SELECT * FROM accounts WHERE user_id=%s AND deleted_at IS NULL", (row["user_id"],)).fetchone()
            if account_row is None:
                raise AuthenticationFailed("account unavailable")
        return self._account(account_row), replacement

    def revoke_refresh_session(self, raw: str | None) -> None:
        if not raw:
            return
        with connect(self.database_url) as connection:
            connection.execute("UPDATE refresh_sessions SET revoked_at=%s WHERE token_hash=%s AND revoked_at IS NULL", (datetime.now(timezone.utc), token_hash(raw)))

    def revoke_all(self, user_id: str) -> None:
        with connect(self.database_url) as connection:
            connection.execute("UPDATE refresh_sessions SET revoked_at=%s WHERE user_id=%s AND revoked_at IS NULL", (datetime.now(timezone.utc), user_id))

    def profile(self, user_id: str) -> AccountProfile:
        self.get(user_id)
        with connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT * FROM account_profiles WHERE user_id=%s", (user_id,)).fetchone()
        if not row:
            return AccountProfile(profile_version=0)
        raw = row["profile_json"]
        return AccountProfile(profile_version=row["profile_version"], profile=UserProfile.model_validate(migrate_persisted_document(raw if isinstance(raw, dict) else json.loads(raw))), updated_at=row["updated_at"])

    def save_profile(self, user_id: str, command: SaveAccountProfileCommand) -> AccountProfile:
        now = datetime.now(timezone.utc)
        with connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT profile_version FROM account_profiles WHERE user_id=%s FOR UPDATE", (user_id,)).fetchone()
            version = int(row["profile_version"]) if row else 0
            if version != command.expected_profile_version:
                raise AccountConflict("stale profile version")
            next_version = version + 1
            connection.execute(
                "INSERT INTO account_profiles(user_id,profile_version,profile_json,updated_at) VALUES(%s,%s,%s::jsonb,%s) ON CONFLICT(user_id) DO UPDATE SET profile_version=EXCLUDED.profile_version,profile_json=EXCLUDED.profile_json,updated_at=EXCLUDED.updated_at",
                (user_id, next_version, command.profile.model_dump_json(), now),
            )
        return AccountProfile(profile_version=next_version, profile=command.profile, updated_at=now)

    def delete_account(self, user_id: str) -> None:
        with connect(self.database_url) as connection:
            connection.execute("DELETE FROM accounts WHERE user_id=%s", (user_id,))

    def purge_revoked_sessions(self, retention_days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with connect(self.database_url) as connection:
            cursor = connection.execute("DELETE FROM refresh_sessions WHERE revoked_at IS NOT NULL AND revoked_at<%s", (cutoff,))
            return cursor.rowcount

    @staticmethod
    def _account(row: dict) -> AccountSummary:
        return AccountSummary(user_id=row["user_id"], email=row["email"], display_name=row["display_name"], created_at=row["created_at"], role=row.get("role", "USER"))
