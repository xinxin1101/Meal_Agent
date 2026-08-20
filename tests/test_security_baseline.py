from pathlib import Path


def test_runtime_has_no_built_in_admin_password() -> None:
    source = Path("backend/mealpilot/main.py").read_text(encoding="utf-8")
    legacy_password = "269" + "756"

    assert legacy_password not in source
    assert 'os.getenv("MEALPILOT_ADMIN_PASSWORD", "").strip()' in source
