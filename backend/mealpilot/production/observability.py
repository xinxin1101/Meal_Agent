from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Bounded request rate with Redis coordination in production and local fallback."""

    def __init__(self, app, *, limit_per_minute: int, redis_url: str | None, trust_proxy_headers: bool) -> None:
        super().__init__(app)
        self.limit = limit_per_minute
        self.trust_proxy_headers = trust_proxy_headers
        self._redis = None
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        if redis_url:
            try:
                import redis
                self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
            except ImportError:  # startup remains explicit through readiness/deployment checks
                self._redis = None

    async def dispatch(self, request: Request, call_next):
        if self.limit <= 0 or request.url.path in {"/health", "/ready", "/metrics"}:
            return await call_next(request)
        identity = self._identity(request)
        if not self._allow(identity):
            return JSONResponse(status_code=429, content={"detail": "request rate exceeded"}, headers={"Retry-After": "60"})
        return await call_next(request)

    def _identity(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip() if self.trust_proxy_headers else ""
        raw = forwarded or (request.client.host if request.client else "unknown")
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _allow(self, identity: str) -> bool:
        window = int(time.time() // 60)
        if self._redis is not None:
            try:
                key = f"mealpilot:rate:{identity}:{window}"
                count = self._redis.incr(key)
                if count == 1:
                    self._redis.expire(key, 75)
                return int(count) <= self.limit
            except Exception:
                pass
        now = time.monotonic()
        with self._lock:
            bucket = self._windows[identity]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        try:
            from prometheus_client import Counter, Histogram
        except ImportError:
            self.requests = self.latency = None
        else:
            self.requests = Counter("mealpilot_http_requests_total", "HTTP requests", ["method", "route", "status"])
            self.latency = Histogram("mealpilot_http_request_seconds", "HTTP request latency", ["method", "route"])

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", request.url.path)
        if self.requests is not None and self.latency is not None:
            self.requests.labels(request.method, route, str(response.status_code)).inc()
            self.latency.labels(request.method, route).observe(time.perf_counter() - started)
        return response
