"""Check that Mason's agent records ATIF steps and token totals, using a fake model."""

import asyncio
import json
from pathlib import Path

import pytest
from fake_model import final_response, tool_response, use_fake_model
from openai.types.responses import Response

from agents590.contracts import RunContext
from agents590.masons_special_agent import MasonsSpecialAgent


def run_agent(
    replies: list[Response | Exception], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    use_fake_model(monkeypatch, replies)
    return asyncio.run(MasonsSpecialAgent().run("say where you are", RunContext(cwd=tmp_path)))


@pytest.fixture
def trajectory_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "logs" / "trajectory.json"
    monkeypatch.setenv("AGENTS590_ATIF_PATH", str(path))
    return path


def test_run_writes_steps_tool_results_and_token_totals(
    tmp_path: Path, trajectory_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = run_agent([tool_response(), final_response("done")], tmp_path, monkeypatch)

    trajectory = json.loads(trajectory_path.read_text())
    sources = [step["source"] for step in trajectory["steps"]]
    tool_step = trajectory["steps"][1]

    assert answer == "done"
    assert sources == ["user", "agent", "agent"]
    assert tool_step["tool_calls"][0]["function_name"] == "shell"
    assert tool_step["observation"]["results"][0]["content"].startswith("[exit code 0]")
    assert trajectory["final_metrics"] == {
        "total_prompt_tokens": 250,
        "total_completion_tokens": 15,
        "total_cached_tokens": 140,
        "total_steps": 3,
    }


def test_crashed_run_still_writes_totals(
    tmp_path: Path, trajectory_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RuntimeError, match="API down"):
        run_agent([tool_response(), RuntimeError("API down")], tmp_path, monkeypatch)

    trajectory = json.loads(trajectory_path.read_text())

    assert trajectory["final_metrics"]["total_prompt_tokens"] == 100
    assert trajectory["final_metrics"]["total_steps"] == 2


def test_logs_go_to_stderr_not_stdout(
    tmp_path: Path,
    trajectory_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with caplog.at_level("INFO", logger="agents590.masons_special_agent"):
        run_agent([tool_response(), final_response("done")], tmp_path, monkeypatch)

    messages = [record.getMessage() for record in caplog.records]

    assert any(message.startswith("model call 1: input=100 cached=40") for message in messages)
    assert any("'pwd' exited with 0" in message for message in messages)
    assert any(message.startswith("total after 2 model calls") for message in messages)
    assert capsys.readouterr().out == ""
