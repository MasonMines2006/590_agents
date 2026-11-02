"""Check Mason's agent tools without contacting model services."""

import asyncio
import json
import time
from pathlib import Path

import pytest
from openai.types.responses import ResponseFunctionToolCall

from agents590.masons_special_agent import MasonsSpecialAgent
from agents590.masons_special_agent import agent as agent_module
from agents590.masons_special_agent.agent import MAX_OUTPUT_CHARS, truncate


def call(name: str, arguments: object) -> ResponseFunctionToolCall:
    """Build a fake tool call like the ones the model sends."""
    return ResponseFunctionToolCall(
        type="function_call",
        call_id="call_1",
        name=name,
        arguments=json.dumps(arguments),
    )


def run_tool(action: ResponseFunctionToolCall, cwd: Path) -> str:
    return asyncio.run(MasonsSpecialAgent().handle_tool_call(action, cwd))


def test_shell_reports_exit_code_stdout_and_stderr(tmp_path: Path) -> None:
    output = run_tool(call("shell", {"command": "ls missing-file ."}), tmp_path)

    assert output.startswith("[exit code ")
    assert "[exit code 0]" not in output
    assert "[stdout]" in output
    assert "missing-file" in output.split("[stderr]")[1]


def test_shell_runs_in_the_given_directory(tmp_path: Path) -> None:
    output = run_tool(call("shell", {"command": "pwd"}), tmp_path)

    assert "[exit code 0]" in output
    assert str(tmp_path.resolve()) in output


@pytest.mark.parametrize("command", ["curl example.com", "   ", ""])
def test_shell_rejects_disallowed_or_empty_commands(tmp_path: Path, command: str) -> None:
    output = run_tool(call("shell", {"command": command}), tmp_path)

    assert output.startswith("[error]")


def test_shell_disables_interactive_git_editor(tmp_path: Path) -> None:
    output = run_tool(
        call("shell", {"command": "python3 -c 'import os; print(os.environ[\"GIT_EDITOR\"])'"}),
        tmp_path,
    )

    assert "[stdout]\ntrue" in output


def test_write_then_read_file_round_trips_and_creates_parents(tmp_path: Path) -> None:
    written = run_tool(call("write_file", {"path": "a/b/new.py", "content": "x = 1\n"}), tmp_path)
    read = run_tool(call("read_file", {"path": str(tmp_path / "a/b/new.py")}), tmp_path)

    assert written.startswith("Wrote 6 characters")
    assert read == "x = 1\n"


def test_read_missing_file_returns_error_text(tmp_path: Path) -> None:
    output = run_tool(call("read_file", {"path": "nope.txt"}), tmp_path)

    assert output.startswith("[error] Could not read")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("shell", {"cmd": "ls"}),
        ("write_file", {"path": "x.txt"}),
        ("read_file", {}),
        ("delete_everything", {"path": "x"}),
        ("shell", ["ls"]),
    ],
)
def test_bad_tool_calls_return_error_text(tmp_path: Path, name: str, arguments: object) -> None:
    output = run_tool(call(name, arguments), tmp_path)

    assert output.startswith("[error]")


def test_truncate_keeps_short_text_and_marks_long_text() -> None:
    assert truncate("short") == "short"

    long_text = "x" * (MAX_OUTPUT_CHARS + 5)
    cut = truncate(long_text)

    assert cut.startswith("x" * MAX_OUTPUT_CHARS)
    assert cut.endswith("[truncated 5 characters]")


def test_huge_stdout_does_not_hide_stderr(tmp_path: Path) -> None:
    command = "python3 -c \"import sys; print('x' * 50000); print('boom', file=sys.stderr)\""
    output = run_tool(call("shell", {"command": command}), tmp_path)

    assert len(output) < MAX_OUTPUT_CHARS + 500
    assert "boom" in output.split("[stderr]")[1]


def test_timeout_stops_child_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_module, "SHELL_TIMEOUT_SECONDS", 1)
    marker = tmp_path / "still_running.txt"
    # The child sleeps past the timeout and then writes a file, unless it was killed.
    command = f"python3 -c \"import time; time.sleep(2); open('{marker}', 'w').write('x')\"; true"

    output = run_tool(call("shell", {"command": command}), tmp_path)
    time.sleep(2)

    assert output.startswith("[error] The command timed out")
    assert not marker.exists()


def test_reading_a_long_file_warns_against_rewriting_it(tmp_path: Path) -> None:
    (tmp_path / "big.py").write_text("y = 2\n" * 5000)

    output = run_tool(call("read_file", {"path": "big.py"}), tmp_path)

    assert "[truncated" in output
    assert "Do not rewrite it with write_file" in output
