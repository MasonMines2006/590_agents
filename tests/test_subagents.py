"""Check the delegate tool: fresh context, depth guard and token accounting."""

import asyncio
import json
from pathlib import Path

import pytest
from fake_model import final_response, tool_response, use_fake_model
from openai.types.responses import ResponseFunctionToolCall

from agents590.contracts import RunContext
from agents590.masons_special_agent import MasonsSpecialAgent


def tool_names(agent: MasonsSpecialAgent) -> set[str]:
    return {tool["name"] for tool in agent.tool_definitions()}


def test_only_the_main_agent_is_offered_delegate() -> None:
    assert "delegate" in tool_names(MasonsSpecialAgent())
    assert "delegate" not in tool_names(MasonsSpecialAgent(depth=1))


def test_subagent_cannot_delegate_even_if_it_tries(tmp_path: Path) -> None:
    action = ResponseFunctionToolCall(
        type="function_call",
        call_id="call_1",
        name="delegate",
        arguments=json.dumps({"task": "go deeper"}),
    )
    subagent = MasonsSpecialAgent(depth=1)

    output = asyncio.run(subagent.handle_tool_call(action, RunContext(cwd=tmp_path)))

    assert output.startswith("[error] Subagents cannot delegate further")


def test_delegate_runs_a_fresh_subagent_and_adds_its_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trajectory = tmp_path / "logs" / "trajectory.json"
    monkeypatch.setenv("AGENTS590_ATIF_PATH", str(trajectory))
    # One fake model serves both agents, in this order:
    fake = use_fake_model(
        monkeypatch,
        [
            # 1. parent asks for a subagent
            tool_response("delegate", {"task": "count the files"}, tokens=(100, 0, 10)),
            # 2. the subagent answers right away
            final_response("there are 3 files", tokens=(50, 0, 5)),
            # 3. parent finishes
            final_response("done", tokens=(200, 0, 20)),
        ],
    )

    answer = asyncio.run(MasonsSpecialAgent().run("parent task", RunContext(cwd=tmp_path)))

    saved = json.loads(trajectory.read_text())
    tool_result = saved["steps"][1]["observation"]["results"][0]["content"]
    assert answer == "done"
    # The subagent saw only its own task, not the parent's conversation.
    assert fake.requests[1]["input"] == [{"role": "user", "content": "count the files"}]
    assert tool_result == "there are 3 files"
    # The parent's trajectory was not overwritten by the subagent's...
    assert [step["source"] for step in saved["steps"]] == ["user", "agent", "agent"]
    # ...and its totals include the subagent's tokens (100 + 50 + 200, 10 + 5 + 20).
    assert saved["final_metrics"]["total_prompt_tokens"] == 350
    assert saved["final_metrics"]["total_completion_tokens"] == 35
