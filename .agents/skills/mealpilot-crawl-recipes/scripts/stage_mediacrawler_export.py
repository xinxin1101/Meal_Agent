"""Convert MediaCrawler JSON/JSONL exports into MealPilot raw quarantine records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


PINNED_COMMIT = "071c8c0acaece3e82f2532cffb19faeddc9ec1c3"
SCHEMA_VERSION = "mealpilot.raw-crawl.v1"
MAX_FILE_BYTES = 25 * 1024 * 1024
SENSITIVE_KEY_PARTS = ("cookie", "token", "authorization", "password", "secret", "phone", "mobile")
SOURCE_ID_KEYS = ("note_id", "aweme_id", "video_id", "article_id", "id", "content_id")
SOURCE_URL_KEYS = ("note_url", "aweme_url", "video_url", "article_url", "url", "source_url")
TEXT_KEYS = ("title", "desc", "description", "content", "text", "note_text")
BATCH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sanitise(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            normalised_key = str(key).lower()
            if any(part in normalised_key for part in SENSITIVE_KEY_PARTS):
                clean[str(key)] = "[REDACTED]"
            else:
                clean[str(key)] = sanitise(item)
        return clean
    if isinstance(value, list):
        return [sanitise(item) for item in value]
    return value


def first_scalar(record: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None


def extraction_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in TEXT_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n\n".join(dict.fromkeys(parts))


def input_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        candidates = [input_path]
    elif input_path.is_dir():
        candidates = sorted(
            path
            for path in input_path.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
        )
    else:
        raise ValueError(f"Input does not exist: {input_path}")
    return candidates


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Input file exceeds 25 MiB limit: {path}")
    with path.open("r", encoding="utf-8-sig") as handle:
        if path.suffix.lower() == ".jsonl":
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"Expected object at {path}:{line_number}")
                yield value
            return

        value = json.load(handle)
        if isinstance(value, dict):
            yield value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, dict):
                    raise ValueError(f"Expected object at {path}[{index}]")
                yield item
        else:
            raise ValueError(f"Expected object or array in {path}")


def stage_exports(
    input_path: Path,
    staging_root: Path,
    platform: str,
    batch_id: str,
    crawler_commit: str = PINNED_COMMIT,
    max_records: int = 100,
) -> dict[str, Any]:
    if not BATCH_PATTERN.fullmatch(batch_id):
        raise ValueError("batch_id must match [a-z0-9][a-z0-9-]{2,79}")
    if not 1 <= max_records <= 100:
        raise ValueError("max_records must be between 1 and 100")

    files = input_files(input_path.resolve())
    batch_dir = staging_root.resolve() / batch_id
    if batch_dir.exists():
        raise FileExistsError(f"Batch already exists: {batch_dir}")

    captured_at = datetime.now(timezone.utc).isoformat()
    envelopes: list[dict[str, Any]] = []
    hashes: set[str] = set()
    duplicate_count = 0
    skipped_non_content_count = 0

    for source_file in files:
        lowered_name = source_file.name.lower()
        if "comment" in lowered_name or "creator" in lowered_name:
            skipped_non_content_count += 1
            continue
        for raw_record in iter_records(source_file):
            clean_record = sanitise(raw_record)
            raw_json = canonical_json(clean_record)
            content_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
            if content_hash in hashes:
                duplicate_count += 1
                continue
            if len(envelopes) >= max_records:
                raise ValueError(f"Export exceeds max_records={max_records}")
            hashes.add(content_hash)
            source_id = first_scalar(clean_record, SOURCE_ID_KEYS) or content_hash[:20]
            envelopes.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "staging_id": f"raw-{content_hash[:24]}",
                    "captured_at": captured_at,
                    "source_tool": "MediaCrawler",
                    "source_commit": crawler_commit,
                    "platform": platform,
                    "source_id": source_id,
                    "source_url": first_scalar(clean_record, SOURCE_URL_KEYS),
                    "upstream_file": source_file.name,
                    "raw_content_hash": content_hash,
                    "raw_payload": clean_record,
                    "extraction_text": extraction_text(clean_record),
                    "trust_status": "UNTRUSTED",
                    "license_status": "PENDING",
                    "review_status": "PENDING",
                    "extraction_status": "PENDING",
                }
            )

    batch_dir.mkdir(parents=True)
    records_path = batch_dir / "records.jsonl"
    with records_path.open("x", encoding="utf-8", newline="\n") as handle:
        for envelope in envelopes:
            handle.write(canonical_json(envelope) + "\n")

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": captured_at,
        "source_tool": "MediaCrawler",
        "source_commit": crawler_commit,
        "platform": platform,
        "input_path": str(input_path.resolve()),
        "records_file": "records.jsonl",
        "imported_count": len(envelopes),
        "duplicate_count": duplicate_count,
        "skipped_non_content_count": skipped_non_content_count,
        "publication_eligible": False,
        "required_next_steps": [
            "source_and_licence_review",
            "structured_recipe_extraction",
            "unit_and_ingredient_normalisation",
            "validation_and_deduplication",
            "human_or_automatic_acceptance",
        ],
    }
    manifest_path = batch_dir / "manifest.json"
    with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, default=Path("data/staging/raw"))
    parser.add_argument("--platform", required=True)
    parser.add_argument(
        "--batch-id",
        default=f"crawl-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:8]}",
    )
    parser.add_argument("--crawler-commit", default=PINNED_COMMIT)
    parser.add_argument("--max-records", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = stage_exports(
        input_path=args.input,
        staging_root=args.staging_root,
        platform=args.platform,
        batch_id=args.batch_id,
        crawler_commit=args.crawler_commit,
        max_records=args.max_records,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
