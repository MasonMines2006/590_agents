"""Durable, local records for agents590 runs.

The store is intentionally filesystem-only and has no dependency on an agent or
model provider. Run records are useful after a process is interrupted, so the
manifest is replaced atomically while events remain append-only.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

_SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,}]+")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: object) -> object:
    """Redact obvious inline credentials before they reach the run log."""
    if isinstance(value, str):
        return _SECRET.sub(r"\1=[redacted]", value)
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _clean(item) for key, item in mapping.items()}
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_clean(item) for item in items]
    return value


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


class RunStore:
    """Persist one run as a manifest, plan, event log, and final summary."""

    def __init__(self, root: Path, run_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError("invalid run id")
        self.directory = root / "runs" / run_id
        self.manifest_path = self.directory / "manifest.json"
        self.events_path = self.directory / "events.jsonl"
        self.plan_path = self.directory / "plan.md"
        self.summary_path = self.directory / "summary.md"
        self.manifest: dict[str, object] = {}

    @classmethod
    def start(cls, root: Path, run_id: str, metadata: dict[str, object]) -> RunStore:
        store = cls(root, run_id)
        store.manifest = {
            "run_id": run_id,
            "created_at": _now(),
            "updated_at": _now(),
            "status": "in_progress",
            "policy_version": "workspace-v1",
            **cast(dict[str, object], _clean(metadata)),
        }
        _atomic_json(store.manifest_path, store.manifest)
        store.plan_path.write_text(
            "# Run plan\n\n"
            f"- Run: `{run_id}`\n"
            "- Status: `in_progress`\n\n"
            "## Instruction\n\n"
            f"{metadata.get('instruction', '')}\n"
        )
        store.event("run_started", {"instruction": metadata.get("instruction", "")})
        return store

    @classmethod
    def open(cls, root: Path, run_id: str) -> RunStore:
        store = cls(root, run_id)
        try:
            value = json.loads(store.manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read run {run_id!r}: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"run {run_id!r} has an invalid manifest")
        store.manifest = cast(dict[str, object], value)
        return store

    def event(self, kind: str, data: dict[str, object] | None = None) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": _now(), "event": kind, "data": _clean(data or {})}
        with self.events_path.open("a") as events:
            events.write(json.dumps(entry, sort_keys=True) + "\n")

    def finish(self, status: str, output: str = "", error: str | None = None) -> None:
        self.manifest.update({"status": status, "updated_at": _now()})
        if error is not None:
            self.manifest["error"] = _clean(error)
        _atomic_json(self.manifest_path, self.manifest)
        self.event("run_finished", {"status": status, "error": error})
        result = _clean(output) if output else (error or "No result was produced.")
        self.summary_path.write_text(
            "# Run summary\n\n"
            f"- Status: `{status}`\n"
            f"- Run: `{self.manifest['run_id']}`\n\n"
            "## Result\n\n"
            f"{result}\n"
        )

    def instruction(self) -> str:
        value = self.manifest.get("instruction")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"run {self.manifest.get('run_id', '')!r} has no instruction")
        return value
