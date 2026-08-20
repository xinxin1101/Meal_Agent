"""Rate-limited HTTP acquisition with robots and access-challenge hard stops."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .parser import ALLOWED_HOSTS, AccessChallengeError, parse_tree


USER_AGENT = "MealPilotRecipeImporter/0.1 (personal-study; no-images)"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class SourceAccessError(RuntimeError):
    pass


class RobotsDeniedError(SourceAccessError):
    pass


class RateLimitError(SourceAccessError):
    pass


@dataclass(frozen=True)
class FetchPolicy:
    delay_seconds: float = 2.0
    timeout_seconds: float = 15.0
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.delay_seconds < 2.0:
            raise ValueError("delay_seconds must be at least 2.0")
        if not 1 <= self.max_attempts <= 2:
            raise ValueError("max_attempts must be between 1 and 2")
        if not 1 <= self.timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")


class MeishiChinaHttpClient:
    def __init__(
        self,
        policy: FetchPolicy | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy or FetchPolicy()
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            follow_redirects=True,
            timeout=self.policy.timeout_seconds,
        )
        self.sleep = sleep
        self._last_request_at: float | None = None
        self._robots: dict[str, RobotFileParser] = {}

    def __enter__(self) -> "MeishiChinaHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise SourceAccessError("SOURCE_URL_NOT_ALLOWED")
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise SourceAccessError("SOURCE_URL_AUTHORITY_INVALID")

    def _wait(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self.policy.delay_seconds - elapsed
            if remaining > 0:
                self.sleep(remaining)

    def _request(self, url: str) -> str:
        self._validate_url(url)
        last_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            self._wait()
            try:
                response = self.client.get(url)
                self._last_request_at = time.monotonic()
            except httpx.HTTPError as error:
                self._last_request_at = time.monotonic()
                last_error = error
                if attempt == self.policy.max_attempts:
                    raise SourceAccessError("SOURCE_NETWORK_ERROR") from error
                continue
            self._validate_url(str(response.url))
            if response.status_code in {401, 403, 429}:
                raise RateLimitError(f"SOURCE_ACCESS_STOP_{response.status_code}")
            if response.status_code >= 500:
                last_error = SourceAccessError(f"SOURCE_HTTP_{response.status_code}")
                if attempt == self.policy.max_attempts:
                    raise last_error
                continue
            if response.status_code != 200:
                raise SourceAccessError(f"SOURCE_HTTP_{response.status_code}")
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text/plain" not in content_type:
                raise SourceAccessError("SOURCE_CONTENT_TYPE_INVALID")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise SourceAccessError("SOURCE_RESPONSE_TOO_LARGE")
            charset_match = None
            content_type_lower = content_type.lower()
            if "charset=" in content_type_lower:
                charset_match = content_type_lower.split("charset=", 1)[1].split(";", 1)[0].strip()
            encoding = charset_match or "utf-8"
            try:
                text = response.content.decode(encoding)
            except (LookupError, UnicodeDecodeError) as error:
                raise SourceAccessError("SOURCE_ENCODING_INVALID") from error
            try:
                parse_tree(text)
            except AccessChallengeError as error:
                raise SourceAccessError("SOURCE_ACCESS_CHALLENGE") from error
            return text
        raise SourceAccessError("SOURCE_NETWORK_ERROR") from last_error

    def _robots_for(self, url: str) -> RobotFileParser:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host in self._robots:
            return self._robots[host]
        robots_url = f"https://{host}/robots.txt"
        text = self._request(robots_url)
        parser = RobotFileParser(robots_url)
        parser.parse(text.splitlines())
        self._robots[host] = parser
        return parser

    def get_html(self, url: str) -> str:
        self._validate_url(url)
        robots = self._robots_for(url)
        if not robots.can_fetch(USER_AGENT, url):
            raise RobotsDeniedError("ROBOTS_DENIED")
        return self._request(url)
