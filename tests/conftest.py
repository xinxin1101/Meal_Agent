import os
from pathlib import Path


# The repository sample corpus is a test fixture, not a runtime database.
os.environ.setdefault("MEALPILOT_INCLUDE_SAMPLE_RECIPES", "true")
# Keep developer/runtime publications from changing deterministic test counts.
os.environ.setdefault(
    "MEALPILOT_PUBLISHED_RECIPES_PATH",
    str(Path(__file__).resolve().parent / "fixtures" / "no-runtime-recipes.json"),
)
