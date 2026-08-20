from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(digest, _unb64(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class AuthSettings:
    secret: bytes
    issuer: str
    access_seconds: int
    refresh_seconds: int
    secure_cookie: bool


def load_auth_settings(runtime_dir: Path, production: bool) -> AuthSettings:
    configured = os.getenv("MEALPILOT_AUTH_SECRET", "").strip()
    if production and len(configured) < 32:
        raise RuntimeError("MEALPILOT_AUTH_SECRET must contain at least 32 characters in production")
    if configured:
        secret = configured.encode()
    else:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        secret_path = runtime_dir / "auth-secret"
        if not secret_path.exists():
            secret_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        secret = secret_path.read_text(encoding="utf-8").strip().encode()
    return AuthSettings(
        secret=secret,
        issuer="mealpilot",
        access_seconds=900,
        refresh_seconds=30 * 24 * 60 * 60,
        secure_cookie=os.getenv("MEALPILOT_SECURE_COOKIES", "true" if production else "false").casefold() == "true",
    )


def issue_access_token(user_id: str, settings: AuthSettings) -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"sub": user_id, "iss": settings.issuer, "iat": now, "exp": now + settings.access_seconds, "jti": secrets.token_hex(12)}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(settings.secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def verify_access_token(token: str, settings: AuthSettings) -> str:
    try:
        header, payload, signature = token.split(".")
        metadata = json.loads(_unb64(header))
        if metadata != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("unsupported token header")
        expected = _b64(hmac.new(settings.secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        claims = json.loads(_unb64(payload))
        if claims.get("iss") != settings.issuer or int(claims.get("exp", 0)) <= int(time.time()):
            raise ValueError("expired token")
        return str(claims["sub"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid access token") from error
