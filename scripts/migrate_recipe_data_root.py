"""Copy legacy recipe acquisition data into the unified persistent root.

Dry-run is the default. Existing destination files are never overwritten.
Use ``--execute`` only after reviewing the printed manifest.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from mealpilot.ingestion.settings import load_recipe_data_paths


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    target = load_recipe_data_paths(ROOT)
    mappings = [
        (ROOT / "data" / "staging" / "raw", target.raw),
        (ROOT / ".runtime" / "recipe-reviews", target.reviews),
        (ROOT / ".runtime" / "recipe-reviews-v2", target.reviews),
    ]
    actions: list[dict[str, str]] = []
    conflicts: list[str] = []
    for source, destination in mappings:
        if not source.exists():
            continue
        for item in sorted(path for path in source.rglob("*") if path.is_file()):
            relative = item.relative_to(source)
            output = destination / relative
            if output.exists():
                conflicts.append(str(output))
            else:
                actions.append({"source": str(item), "destination": str(output)})
    legacy_files = [
        (ROOT / "data" / "recipes.published.json", target.published),
        (ROOT / ".runtime" / "recipe-automation-state.json", target.state),
    ]
    for source, output in legacy_files:
        if not source.exists():
            continue
        if output.exists():
            conflicts.append(str(output))
        else:
            actions.append({"source": str(source), "destination": str(output)})
    print(json.dumps({"mode": "execute-copy" if args.execute else "dry-run", "root": str(target.root), "actions": actions, "conflicts": conflicts}, ensure_ascii=False, indent=2))
    if args.execute:
        if conflicts:
            raise SystemExit("destination conflicts exist; migration stopped without overwriting")
        for action in actions:
            output = Path(action["destination"])
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(action["source"], output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
