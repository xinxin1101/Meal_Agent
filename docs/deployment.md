# Local and Docker delivery

## Local development

Start both services without activating the virtual environment:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Open `http://127.0.0.1:5173`. The Vite development proxy forwards `/v1` to the API on port 8000. Stop only the processes recorded by MealPilot with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1
```

The state file records the actual port-listener process ids, including when the Windows virtual-environment launcher creates a child Python process.

## Docker Compose

The API image is pinned to Python 3.11. The web image uses the frozen pnpm lockfile and serves the SPA through Nginx. Compose waits for the API health check before starting the web service and attaches both services to the private `mealpilot_internal` network.

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

Open `http://127.0.0.1:8080`. The browser uses the same origin for all requests. Nginx forwards `/v1/` and `/health` to `api:8000`; SSE buffering and caching are disabled, and the read timeout is 75 seconds. SPA refreshes fall back to `index.html` while hashed assets use immutable caching.

Stop the containers without deleting the durable SQLite volume:

```powershell
docker compose down
```

Use `docker compose down --volumes` only when intentionally deleting all container-side runs and preference memory.

## Configuration and troubleshooting

The `.env` file is optional for the deterministic product path. Add the SiliconFlow key and model only when model-backed planner/explainer/chat calls are wanted. Do not paste the output of `docker compose config` into logs because it expands `env_file` values; use `docker compose config --quiet` for validation.

If image metadata or pulls return HTTP 429 from a configured registry mirror, the Dockerfiles have not failed. Wait for that mirror's rate limit to reset or select a trusted registry source in Docker Desktop, then rerun `docker compose up --build -d`. Global Docker mirror changes are intentionally not made by project scripts.

Acceptance endpoints:

- Web health: `http://127.0.0.1:8080/healthz`
- Proxied API health: `http://127.0.0.1:8080/health`
- API documentation: `http://127.0.0.1:8000/docs`
- SSE: `http://127.0.0.1:8080/v1/runs/{run_id}/events`

## M27 production topology

The production topology is opt-in and separate from the local Compose file. It runs PostgreSQL migrations once, starts the API and leased worker against PostgreSQL, publishes committed run-event Outbox rows to Redis Streams, and exposes only Caddy on ports 80/443. PostgreSQL and Redis stay on an internal Docker network.

Create `.env` from `.env.example` and set strong `POSTGRES_PASSWORD`/`REDIS_PASSWORD`, a DNS name in `MEALPILOT_DOMAIN`, and `ACME_EMAIL`. Point that DNS name to the host and allow inbound TCP 80/443 (plus UDP 443 if HTTP/3 is desired), then run:

```powershell
docker compose -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.production.yml up --build -d
docker compose -f docker-compose.production.yml ps
```

Caddy obtains and renews HTTPS certificates only when the domain is publicly resolvable and ports 80/443 reach the host. View readiness at `https://<domain>/ready`. The public web tier deliberately returns 404 for `/metrics`; a monitoring collector must access the API service on the private application network.

The `maintenance` service runs privacy-retention cleanup and creates a PostgreSQL custom-format backup every 24 hours by default. Backups are retained for 14 days by default; configure `MEALPILOT_MAINTENANCE_INTERVAL_SECONDS` and `MEALPILOT_BACKUP_RETENTION_DAYS` explicitly. You can still create an on-demand backup with `powershell -ExecutionPolicy Bypass -File .\scripts\backup.ps1`. Restore only during a maintenance window with API/worker/maintenance stopped, using `scripts/restore.ps1`; the script requires an explicit `RESTORE` confirmation. Always rehearse restore against disposable storage before relying on a backup.

This topology is a production engineering foundation, not authorization for a real-user beta. Authentication/authorization and scheduled retention are implemented. Privacy/legal review, secrets management outside `.env`, a licensed recipe corpus, and qualified approval of the advisory nutrition-target policy are still mandatory gates.

### Local integration record

On 2026-08-13 the production data path was exercised locally with Docker Desktop: PostgreSQL 16 and Redis 7 health checks passed; migration `20260813_0001` applied; a queued Run was consumed once and completed by the Worker; queued/started/analyze/retrieve/solve/validate/finalize/completed Outbox events were published to Redis Streams; an expired Worker lease was reclaimed and completed; Prometheus metrics were exposed; and a PostgreSQL custom-format backup restored successfully into an isolated database. M23 subsequently added migration `20260813_0002` and automated cross-account authorization tests. The temporary integration containers and volumes were removed afterwards; this record does not cover public DNS/Caddy certificate issuance.

The M23 production image was also rebuilt in an isolated Compose project on 2026-08-13. Both migrations completed, PostgreSQL-backed registration returned an account, and the issued Bearer token resolved to the same identity through `GET /v1/account`. The isolated containers, networks and volumes were removed after verification.

On 2026-08-13 the hardening update added migration `20260813_0003`, run cancellation, Worker lease renewal, and the scheduled maintenance container. Production Compose validation passed; the production backend image (including `pg_dump`) and frontend Nginx image (`tsc -b`, 58 Vite modules) both built successfully.
