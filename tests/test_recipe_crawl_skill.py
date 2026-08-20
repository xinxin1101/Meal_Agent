from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / ".agents" / "skills" / "mealpilot-crawl-recipes" / "scripts"


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mediacrawler_command_is_bounded_and_does_not_inject_credentials(tmp_path: Path) -> None:
    module = load_script("run_mediacrawler")
    command = module.build_command(
        uv_executable="uv",
        platform="xhs",
        keywords="低脂 高蛋白 菜谱",
        output_dir=tmp_path,
        max_notes=10,
        concurrency=1,
    )

    assert command[:4] == ["uv", "run", "python", "main.py"]
    assert command[command.index("--type") + 1] == "search"
    assert command[command.index("--get_comment") + 1] == "false"
    assert command[command.index("--save_data_option") + 1] == "jsonl"
    assert not any("cookie" in argument.lower() for argument in command)

    with pytest.raises(ValueError, match="between 1 and 20"):
        module.build_command("uv", "xhs", "菜谱", tmp_path, 21, 1)


def test_export_is_deduplicated_redacted_and_staged_as_untrusted(tmp_path: Path) -> None:
    module = load_script("stage_mediacrawler_export")
    content = {
        "note_id": "note-1",
        "note_url": "https://example.invalid/note-1",
        "title": "番茄鸡蛋的做法",
        "desc": "网络正文只作为不可信数据",
        "cookie": "must-not-leak",
    }
    contents_file = tmp_path / "xhs_contents.jsonl"
    contents_file.write_text(
        json.dumps(content, ensure_ascii=False) + "\n" + json.dumps(content, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "xhs_comments.jsonl").write_text(
        json.dumps({"content": "not recipe source"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    manifest = module.stage_exports(
        input_path=tmp_path,
        staging_root=tmp_path / "staging",
        platform="xhs",
        batch_id="crawl-test-batch",
    )

    assert manifest["imported_count"] == 1
    assert manifest["duplicate_count"] == 1
    assert manifest["skipped_non_content_count"] == 1
    assert manifest["publication_eligible"] is False

    records_path = tmp_path / "staging" / "crawl-test-batch" / "records.jsonl"
    envelope = json.loads(records_path.read_text(encoding="utf-8"))
    assert envelope["raw_payload"]["cookie"] == "[REDACTED]"
    assert envelope["trust_status"] == "UNTRUSTED"
    assert envelope["license_status"] == "PENDING"
    assert envelope["review_status"] == "PENDING"
    assert "recipe" not in envelope
