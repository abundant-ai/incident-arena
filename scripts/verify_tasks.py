#!/usr/bin/env python3
"""Verify every checked-in task against the Oddish download manifest."""

import hashlib
import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks"


def main() -> None:
    manifest = json.loads((TASKS / "manifest.json").read_text())
    assert manifest["task_count"] == len(manifest["tasks"]) == 20
    assert manifest["file_count"] == sum(task["file_count"] for task in manifest["tasks"])
    assert manifest["total_bytes"] == sum(task["total_bytes"] for task in manifest["tasks"])
    assert len({task["task_id"] for task in manifest["tasks"]}) == 20
    actual_dirs = {path.name for path in TASKS.iterdir() if path.is_dir()}
    assert actual_dirs == {task["task_id"] for task in manifest["tasks"]}

    for task in manifest["tasks"]:
        task_dir = TASKS / task["task_id"]
        assert re.fullmatch(r"[0-9a-f]{64}", task["source_hash"])
        assert task["version_id"].endswith(f"-v{task['version']}")
        recorded = {file["path"]: file for file in task["files"]}
        assert len(recorded) == task["file_count"]
        actual = {path.relative_to(task_dir).as_posix() for path in task_dir.rglob("*") if path.is_file()}
        assert actual == set(recorded), (task["task_id"], actual - set(recorded), set(recorded) - actual)
        assert {"task.toml", "instruction.md", "tests/test.sh"} <= actual
        assert "solution/solve.sh" in actual
        parsed = tomllib.loads((task_dir / "task.toml").read_text())
        assert parsed["schema_version"] == "1.3"
        assert parsed["task"]["name"].startswith("sre-world/")
        for relative, expected in recorded.items():
            path = task_dir / relative
            assert not path.is_symlink(), path
            data = path.read_bytes()
            assert len(data) == expected["bytes"], path
            assert hashlib.sha256(data).hexdigest() == expected["sha256"], path
        print(f"OK {task['task_id']} ({task['version_id']}, {task['file_count']} files)")

    print(f"Verified {manifest['task_count']} tasks and {manifest['file_count']} files")


if __name__ == "__main__":
    main()
