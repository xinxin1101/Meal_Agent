# Recipe automation operations

The automation path is incremental but not autonomous by default. It follows the same trust boundary as manual acquisition:

```text
scheduled policy run -> robots/rate-limit bounded fetch -> hash deduplication
-> raw quarantine -> Pydantic extraction -> mc-r3-v3 quality report
-> operator review -> conditional publication
```

## Safe defaults

`config/recipe_sources/meishichina.personal-study.json` keeps `auto_publish=false`, a ten-record per-run limit, at most three category pages, a two-second minimum delay, no images, and no comments. Network execution still requires the administrator's explicit personal-study acknowledgement for every queued job.

Preview without network access:

```powershell
.\scripts\run_recipe_automation.ps1
```

Execute a personal-study batch only after accepting the source terms:

```powershell
.\scripts\run_recipe_automation.ps1 -Execute
```

The state file records only `source_id -> content hash`, never credentials. Unchanged pages are not staged again. The crawler skips known IDs on the current page and scans up to the configured three category pages for at most the requested number of unseen recipes; the per-run limit is not a database capacity limit. New or changed pages live below `MEALPILOT_RECIPE_DATA_ROOT` (default `.runtime/recipe-data`): `raw/`, `reviews/`, `state/`, `jobs/`, and `published/` form one persistent contract. Records are reported as `BLOCKED`, `PUBLICATION_READY`, or `SOLVER_READY`.

## Review workbench

Use the ADMIN-only workbench to compare source data with the LLM-generated page structure, correct an automatically assigned ingredient identity only when it is wrong, edit steps, rerun final validation, and batch-publish `PUBLICATION_READY` or `SOLVER_READY` records. Publication-only recipes remain excluded from planning until their authoritative nutrition coverage is complete. These operations are never exposed to ordinary users.

## Automatic publication hard stop

Acquisition never publishes automatically. The Worker stops after LLM structuring and deterministic final validation. Only an administrator-selected `PUBLICATION_READY` or `SOLVER_READY` record can be approved and published, individually or in a batch. Only `SOLVER_READY` recipes can participate in meal planning.

Formal catalog publication in this workflow means availability inside the user's private personal-study MealPilot instance. It is not permission to publicly reproduce or redistribute source content.

## Scheduling

The admin UI performs a no-network preview and may enqueue an explicitly acknowledged personal-study job. FastAPI never crawls: `python -m mealpilot.ingestion.worker` claims the leased job and writes only to quarantine/review storage. Local `start.ps1` and Docker Compose start this Worker idle. The reviewed policy file is the only place where source execution can be enabled.

Preview legacy-data migration with `.\.venv\Scripts\python.exe scripts\migrate_recipe_data_root.py`. Add `--execute` only after reviewing the manifest; existing destinations are never overwritten.
