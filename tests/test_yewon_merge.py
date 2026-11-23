"""Tests for the pieces merged in from Yewon's `yewons_stale_path` agent.

- The shell keeps the start AND the end of long output (errors are usually at the end).
- The whole run has a time limit, and each model call has an output-token cap.
- A model reply that did not complete, or is empty, stops the run with a clear message.
- API keys are blanked out of tool output before the model or the logs see them.
"""

import asyncio
import json
from pathlib import Path

import pytest
from fake_model import final_response, tool_response, use_fake_model
from openai.types.responses import Response

from agents590.contracts import RunContext
from agents590.masons_special_agent import MasonsSpecialAgent
from agents590.masons_special_agent import agent as agent_module
from agents590.masons_special_agent.redaction import REDACTED, redact
from agents590.masons_special_agent.shell_output import read_bounded

SECRET = "sk-test-secret-value-12345"


def run(context: RunContext) -> str:
    return asyncio.run(MasonsSpecialAgent().run("do the task", context))


def read_from_bytes(data: bytes, limit: int) -> str:
    """Feed *data* through a real StreamReader, the same type a subprocess pipe uses."""

    async def go() -> str:
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        return await read_bounded(reader, limit)

    return asyncio.run(go())


# --- shell output: keep the beginning and the end -------------------------------


def test_short_output_is_returned_unchanged() -> None:
    assert read_from_bytes(b"hello\n", 100) == "hello\n"


def test_long_output_keeps_head_and_tail_and_marks_the_gap() -> None:
    data = b"START" + b"x" * 10_000 + b"END"

    text = read_from_bytes(data, 100)

    assert text.startswith("START")
    assert text.endswith("END")
    assert "[output truncated: 9908 bytes omitted]" in text


def test_shell_keeps_the_end_of_a_long_traceback(tmp_path: Path) -> None:
    # 50 KB of noise, then the line that actually explains the failure.
    command = "python3 -c \"print('noise ' * 10000); print('THE REAL ERROR')\""
    context = RunContext(cwd=tmp_path)
    action = tool_response(arguments={"command": command}).output[0]

    output = asyncio.run(MasonsSpecialAgent().handle_tool_call(action, context))  # type: ignore[arg-type]

    assert "THE REAL ERROR" in output
    assert "bytes omitted" in output


# --- run limits ---------------------------------------------------------------------


def test_every_model_call_has_an_output_token_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = use_fake_model(monkeypatch, [final_response("done")])

    run(RunContext(cwd=tmp_path))

    assert fake.requests[0]["max_output_tokens"] == agent_module.MAX_OUTPUT_TOKENS
    assert fake.requests[0]["parallel_tool_calls"] is False


def test_whole_run_time_limit_stops_a_slow_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_module, "RUN_TIMEOUT_SECONDS", 1)
    slow = {"command": "python3 -c 'import time; time.sleep(5)'"}
    use_fake_model(monkeypatch, [tool_response(arguments=slow), final_response("never")])

    answer = run(RunContext(cwd=tmp_path))

    assert answer == "Stopped: the run went over its 1-second time limit."


# --- model replies that did not finish -------------------------------------------


def test_incomplete_model_reply_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cut_off = Response.model_construct(
        output=[],
        status="incomplete",
        incomplete_details={"reason": "max_output_tokens"},
        usage=final_response("x").usage,
    )
    use_fake_model(monkeypatch, [cut_off])

    answer = run(RunContext(cwd=tmp_path))

    assert answer == "Stopped: the model reply did not complete (status=incomplete)."


def test_empty_model_reply_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_model(monkeypatch, [final_response("   ")])

    answer = run(RunContext(cwd=tmp_path))

    assert answer == "Stopped: the model returned neither a tool call nor an answer."


# --- secret redaction --------------------------------------------------------------


def test_redact_replaces_known_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)

    assert redact(f"key={SECRET}!") == f"key={REDACTED}!"


def test_redact_ignores_empty_or_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "")

    assert redact("nothing secret here") == "nothing secret here"


def test_key_in_a_file_never_reaches_the_model_or_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    (tmp_path / "notes.txt").write_text(f"my key is {SECRET}\n")
    fake = use_fake_model(
        monkeypatch,
        [tool_response(arguments={"command": "cat notes.txt"}), final_response("done")],
    )

    run(RunContext(cwd=tmp_path))

    second_request = json.dumps(fake.requests[1]["input"], default=str)
    assert SECRET not in second_request
    assert REDACTED in second_request
    assert SECRET not in capsys.readouterr().err
