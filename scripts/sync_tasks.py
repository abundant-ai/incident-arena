#!/usr/bin/env python3
"""Download current Oddish task versions into a Harbor benchmark repository."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import httpx


API = "https://abundant-ai--api.modal.run"
DEFAULT_EXPERIMENT = "348dc527"


def safe_relative_path(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe task file path: {value!r}")
    return Path(*path.parts)


def read_key(path: Path | None) -> str:
    key = os.environ.get("ODDISH_API_KEY", "").strip()
    if not key and path is not None:
        key = path.read_text().strip()
    if not key:
        raise SystemExit("Set ODDISH_API_KEY or pass --key-file")
    return key


async def get_json(client: httpx.AsyncClient, path: str, **kwargs) -> dict | list:
    response = await client.get(path, **kwargs)
    response.raise_for_status()
    return response.json()


async def task_inventory(client: httpx.AsyncClient, task_id: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        task = await get_json(client, f"/tasks/{task_id}")
        version_id = task["current_version_id"]
        version = int(version_id.rsplit("-v", 1)[1])
        files: list[dict] = []
        cursor = None
        source_hash = None
        while True:
            params = {"version": version, "presign": "true", "inline": "false", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            page = await get_json(client, f"/tasks/{task_id}/files", params=params)
            if source_hash is None:
                source_hash = page.get("source_hash")
            elif source_hash != page.get("source_hash"):
                raise RuntimeError(f"Source changed during listing: {task_id}")
            files.extend(page["files"])
            cursor = page.get("next_cursor") or page.get("cursor")
            if not cursor:
                break
        if not files or len({file["path"] for file in files}) != len(files):
            raise RuntimeError(f"Empty or duplicate file listing: {task_id}")
        if "task.toml" not in {file["path"] for file in files}:
            raise RuntimeError(f"Missing task.toml: {task_id}")
        return {"task_id": task_id, "version_id": version_id, "version": version, "source_hash": source_hash, "files": files}


async def download_file(client: httpx.AsyncClient, item: dict, task_dir: Path, sem: asyncio.Semaphore) -> dict:
    relative = safe_relative_path(item["path"])
    target = task_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    async with sem:
        for attempt in range(3):
            try:
                response = await client.get(item["url"])
                response.raise_for_status()
                data = response.content
                size = int(item["size"])
                if len(data) != size:
                    raise RuntimeError(f"Size mismatch: {relative}: {len(data)} != {size}")
                target.write_bytes(data)
                return {"path": relative.as_posix(), "bytes": size, "sha256": hashlib.sha256(data).hexdigest()}
            except (httpx.HTTPError, RuntimeError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")


async def sync(args: argparse.Namespace) -> None:
    key = read_key(args.key_file)
    headers = {"Authorization": f"Bearer {key}"}
    limits = httpx.Limits(max_connections=args.concurrency + 10)
    async with (
        httpx.AsyncClient(base_url=args.api, headers=headers, timeout=120, limits=limits) as client,
        httpx.AsyncClient(timeout=120, limits=limits) as download_client,
    ):
        trials = await get_json(client, f"/experiments/{args.experiment}/trials")
        task_ids = sorted({row["task_id"] for row in trials})
        if args.expect_tasks and len(task_ids) != args.expect_tasks:
            raise RuntimeError(f"Expected {args.expect_tasks} tasks, found {len(task_ids)}")
        listing_sem = asyncio.Semaphore(5)
        inventories = await asyncio.gather(
            *(task_inventory(client, task_id, listing_sem) for task_id in task_ids)
        )
        # Listing is cheap; downloads go into staging so a partial transfer cannot
        # masquerade as a usable task collection.
        args.out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="incident-arena-sync-") as tmp:
            staged = Path(tmp)
            file_sem = asyncio.Semaphore(args.concurrency)
            async def one_task(inventory: dict) -> dict:
                task_dir = staged / inventory["task_id"]
                downloaded = await asyncio.gather(
                    *(download_file(download_client, item, task_dir, file_sem) for item in inventory["files"])
                )
                downloaded.sort(key=lambda item: item["path"])
                return {
                    "task_id": inventory["task_id"],
                    "version_id": inventory["version_id"],
                    "version": inventory["version"],
                    "source_hash": inventory["source_hash"],
                    "file_count": len(downloaded),
                    "total_bytes": sum(item["bytes"] for item in downloaded),
                    "files": downloaded,
                }
            tasks = await asyncio.gather(*(one_task(item) for item in inventories))
            for inventory in inventories:
                # Task version URLs can be overwritten; verify the source hash
                # after the transfer before replacing any existing local task.
                fresh = await get_json(
                    client,
                    f"/tasks/{inventory['task_id']}/files",
                    params={"version": inventory["version"], "presign": "false", "inline": "false", "limit": 1},
                )
                if fresh.get("source_hash") != inventory["source_hash"]:
                    raise RuntimeError(f"Source changed during download: {inventory['task_id']}")
                current = await get_json(client, f"/tasks/{inventory['task_id']}")
                if current["current_version_id"] != inventory["version_id"]:
                    raise RuntimeError(f"Current task version changed during download: {inventory['task_id']}")
            for task in tasks:
                destination = args.out / task["task_id"]
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.move(str(staged / task["task_id"]), destination)

    manifest = {
        "source": "Oddish current task versions",
        "experiment_id": args.experiment,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "task_count": len(tasks),
        "file_count": sum(item["file_count"] for item in tasks),
        "total_bytes": sum(item["total_bytes"] for item in tasks),
        "tasks": tasks,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Downloaded {manifest['task_count']} tasks, {manifest['file_count']} files, {manifest['total_bytes']} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--api", default=API)
    parser.add_argument("--out", type=Path, default=Path("tasks"))
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--expect-tasks", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=24)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    asyncio.run(sync(args))


if __name__ == "__main__":
    main()
