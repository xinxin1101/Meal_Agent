"""Run a pinned MediaCrawler checkout in a deliberately bounded search mode."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


PINNED_COMMIT = "071c8c0acaece3e82f2532cffb19faeddc9ec1c3"
PLATFORMS = ("xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu")


def build_command(
    uv_executable: str,
    platform: str,
    keywords: str,
    output_dir: Path,
    max_notes: int,
    concurrency: int,
) -> list[str]:
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    clean_keywords = keywords.strip()
    if not clean_keywords or len(clean_keywords) > 200:
        raise ValueError("Keywords must contain 1 to 200 characters")
    if not 1 <= max_notes <= 20:
        raise ValueError("max_notes must be between 1 and 20")
    if not 1 <= concurrency <= 2:
        raise ValueError("concurrency must be between 1 and 2")

    return [
        uv_executable,
        "run",
        "python",
        "main.py",
        "--platform",
        platform,
        "--lt",
        "qrcode",
        "--type",
        "search",
        "--keywords",
        clean_keywords,
        "--get_comment",
        "false",
        "--get_sub_comment",
        "false",
        "--headless",
        "false",
        "--save_data_option",
        "jsonl",
        "--save_data_path",
        str(output_dir.resolve()),
        "--crawler_max_notes_count",
        str(max_notes),
        "--max_concurrency_num",
        str(concurrency),
    ]


def current_commit(crawler_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(crawler_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crawler-root", type=Path, required=True)
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--keywords", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-notes", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--expected-commit", default=PINNED_COMMIT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--acknowledge-noncommercial-license",
        action="store_true",
        help="Required acknowledgement of MediaCrawler's non-commercial licence.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.acknowledge_noncommercial_license:
        raise SystemExit(
            "Refusing to run: review the upstream licence and pass "
            "--acknowledge-noncommercial-license."
        )

    crawler_root = args.crawler_root.resolve()
    if not (crawler_root / "main.py").is_file():
        raise SystemExit(f"Not a MediaCrawler checkout: {crawler_root}")
    try:
        commit = current_commit(crawler_root)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise SystemExit("Could not verify the MediaCrawler git commit") from exc
    if commit != args.expected_commit:
        raise SystemExit(
            f"MediaCrawler commit mismatch: expected {args.expected_commit}, got {commit}"
        )

    uv_executable = shutil.which("uv")
    if uv_executable is None:
        raise SystemExit("uv is not available on PATH")
    command = build_command(
        uv_executable=uv_executable,
        platform=args.platform,
        keywords=args.keywords,
        output_dir=args.output_dir,
        max_notes=args.max_notes,
        concurrency=args.concurrency,
    )
    print(json.dumps(command, ensure_ascii=False))
    if args.dry_run:
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command, cwd=crawler_root, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
