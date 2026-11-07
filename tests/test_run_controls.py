"""Check that masons_special_agent honors --plan, --max-turns and --max-tool-calls."""

import asyncio
from pathlib import Path

import pytest
from fake_model import final_response, tool_response, use_fake_model

from agents590.contracts import RunContext
from agents590.masons_special_agent import MasonsSpecialAgent


def run(context: RunContext) -> str:
    return asyncio.run(MasonsSpecialAgent().run("do the task", context))


def test_max_turns_limits_model_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake model would keep asking for tools forever.
    fake = use_fake_model(monkeypatch, [tool_response() for _ in range(10)])

    answer = run(RunContext(cwd=tmp_path, max_turns=2))

    assert answer == "Could not finish the task in 2 model calls."
    assert len(fake.requests) == 2


def test_max_tool_calls_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_fake_model(monkeypatch, [tool_response() for _ in range(10)])

    answer = run(RunContext(cwd=tmp_path, max_tool_calls=3))

    assert answer == "Stopped: reached the limit of 3 tool calls."
    # Three tool calls ran; the model asked for a fourth, which was refused.
    assert len(fake.requests) == 4


def test_plan_mode_adds_read_only_rules_to_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = use_fake_model(monkeypatch, [final_response("1. Edit app.py")])

    assert run(RunContext(cwd=tmp_path, plan_only=True)) == "1. Edit app.py"
    assert "PLAN MODE" in str(fake.requests[0]["instructions"])


def test_normal_mode_prompt_has_no_plan_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = use_fake_model(monkeypatch, [final_response("done")])

    run(RunContext(cwd=tmp_path))

    assert "PLAN MODE" not in str(fake.requests[0]["instructions"])
