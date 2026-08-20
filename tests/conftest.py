import os
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TEST_RUNTIME_DIR = _PROJECT_ROOT / ".runtime" / "pytest"
# Keep pytest bootstrap accounts, conversations, and runs isolated from developer data.
os.environ["MEALPILOT_RUNTIME_DIR"] = str(_TEST_RUNTIME_DIR)

# Administrator access is test-only and explicit. Runtime code has no built-in admin password.
if os.getenv("MEALPILOT_ENV", "development").casefold() != "production":
    os.environ["MEALPILOT_ADMIN_USERNAME"] = "root"
    os.environ["MEALPILOT_ADMIN_PASSWORD"] = "test-admin-password-only"
    os.environ["MEALPILOT_ADMIN_DISPLAY_NAME"] = "Test Administrator"

# Repository recipe and nutrition samples are explicit test fixtures, never runtime defaults.
os.environ.setdefault("MEALPILOT_INCLUDE_SAMPLE_RECIPES", "true")
os.environ.setdefault(
    "MEALPILOT_NUTRITION_DATA_PATH",
    str(_PROJECT_ROOT / "data" / "nutrition" / "foods.sample.json"),
)
# Keep developer/runtime publications from changing deterministic test counts.
os.environ.setdefault(
    "MEALPILOT_PUBLISHED_RECIPES_PATH",
    str(Path(__file__).resolve().parent / "fixtures" / "no-runtime-recipes.json"),
)
