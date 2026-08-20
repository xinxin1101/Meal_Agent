"""Single source of truth for persistent recipe and nutrition data paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RecipeDataPaths:
    root: Path
    raw: Path
    reviews: Path
    published: Path
    nutrition: Path
    state: Path
    jobs: Path

    def ensure(self) -> "RecipeDataPaths":
        for path in (
            self.root,
            self.raw,
            self.reviews,
            self.published.parent,
            self.nutrition.parent,
            self.state.parent,
            self.jobs.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self


def load_recipe_data_paths(project_root: Path) -> RecipeDataPaths:
    configured = Path(os.getenv("MEALPILOT_RECIPE_DATA_ROOT", ".runtime/recipe-data"))
    root = (configured if configured.is_absolute() else project_root / configured).resolve()
    return RecipeDataPaths(
        root=root,
        raw=root / "raw",
        reviews=root / "reviews",
        published=root / "published" / "recipes.json",
        nutrition=root / "nutrition" / "foods.json",
        state=root / "state" / "meishichina.json",
        jobs=root / "jobs" / "acquisition.sqlite3",
    ).ensure()
