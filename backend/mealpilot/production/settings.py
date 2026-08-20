from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProductionSettings:
    database_url: str | None
    redis_url: str | None
    rate_limit_per_minute: int
    trusted_proxy_headers: bool

    @property
    def postgres_enabled(self) -> bool:
        return bool(self.database_url and self.database_url.startswith(("postgresql://", "postgres://")))


def load_production_settings() -> ProductionSettings:
    raw_limit = os.getenv("MEALPILOT_RATE_LIMIT_PER_MINUTE", "0")
    try:
        rate_limit = max(0, int(raw_limit))
    except ValueError:
        rate_limit = 0
    return ProductionSettings(
        database_url=os.getenv("DATABASE_URL") or None,
        redis_url=os.getenv("REDIS_URL") or None,
        rate_limit_per_minute=rate_limit,
        trusted_proxy_headers=os.getenv("MEALPILOT_TRUST_PROXY_HEADERS", "false").casefold() == "true",
    )
