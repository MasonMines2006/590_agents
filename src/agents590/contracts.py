"""The small interface shared by standalone agent implementations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RunContext:
    """Explicit working directory and optional per-run execution controls."""

    cwd: Path
    model: str | None = None
    plan_only: bool = False
    auto_approve: bool = False
    session: str | None = None
    run_id: str | None = None
    max_turns: int | None = None
    max_tool_calls: int | None = None
    max_seconds: int | None = None
    state_dir: Path | None = None
    vault: Path | None = None


@runtime_checkable
class Agent(Protocol):
    """Implementations return their final response and may act within `context.cwd`."""

    async def run(self, instruction: str, context: RunContext) -> str: ...
