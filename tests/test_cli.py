"""Check the CLI contract and plugin loading without contacting model services."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agents590 import cli
from agents590.contracts import RunContext


def test_module_and_console_entrypoints_work_outside_project(tmp_path: Path) -> None:
    commands = [
        [sys.executable, "-m", "agents590"],
        [str(Path(sys.executable).parent / "agents590")],
    ]
    for command in commands:
        result = subprocess.run(
            [*command, "krisj_echo", "-i", "a one-line instruction"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "a one-line instruction\n"


def test_list_uses_registered_agent_entrypoints(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--list"]) == 0
    names = capsys.readouterr().out.splitlines()
    assert {"krisj_starter", "krisj_echo"}.issubset(names)
    assert names == sorted(names)


@pytest.mark.parametrize("arguments", [[], ["krisj_echo"], ["krisj_echo", "-i", "  "]])
def test_instruction_is_required(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


def test_unknown_agent_has_a_useful_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["missing", "-i", "test"]) == 1
    assert "Unknown agent" in capsys.readouterr().err


def test_cli_passes_instruction_workdir_and_model_to_the_selected_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, RunContext]] = []

    class RecordingAgent:
        async def run(self, instruction: str, context: RunContext) -> str:
            seen.append((instruction, context))
            return "completed"

    def factory(name: str) -> RecordingAgent:
        assert name == "custom-agent"
        return RecordingAgent()

    monkeypatch.setattr(cli, "create_agent", factory)
    instruction = "A quoted ' instruction; $(still literal)"
    assert (
        cli.main(
            [
                "custom-agent",
                "-i",
                instruction,
                "--cwd",
                str(tmp_path),
                "--model",
                "openai/selected-deployment",
            ]
        )
        == 0
    )
    assert seen == [(instruction, RunContext(cwd=tmp_path, model="openai/selected-deployment"))]
    assert capsys.readouterr().out == "completed\n"


def test_starter_scaffold_fails_explicitly(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["krisj_starter", "-i", "test"]) == 1
    assert "Implement the UNC Azure model/tool loop" in capsys.readouterr().err


def test_explicit_env_file_preserves_exported_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AGENTS590_TEST_VALUE=from-file\nAGENTS590_NEW_VALUE=new\n")
    monkeypatch.setenv("AGENTS590_TEST_VALUE", "from-shell")
    monkeypatch.delenv("AGENTS590_NEW_VALUE", raising=False)
    assert cli.main(["krisj_echo", "-i", "test", "--env-file", str(env_file)]) == 0
    assert os.environ["AGENTS590_TEST_VALUE"] == "from-shell"
    assert os.environ["AGENTS590_NEW_VALUE"] == "new"


@pytest.mark.parametrize("option", ["--cwd", "--env-file"])
def test_missing_paths_fail_before_loading_an_agent(tmp_path: Path, option: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["krisj_echo", "-i", "test", option, str(tmp_path / "missing")])
    assert error.value.code == 2


def test_plain_run_writes_no_state_folder(tmp_path: Path) -> None:
    # Harbor runs the CLI exactly like this, with cwd set to the task repo.
    assert cli.main(["krisj_echo", "-i", "hello", "--cwd", str(tmp_path)]) == 0

    assert not (tmp_path / ".agents590").exists()
    assert list(tmp_path.iterdir()) == []


def test_session_run_writes_records_in_the_state_dir(tmp_path: Path) -> None:
    state = tmp_path / "state"

    exit_code = cli.main(
        ["krisj_echo", "-i", "hello", "--cwd", str(tmp_path), "--session", "x"]
        + ["--state-dir", str(state)]
    )

    assert exit_code == 0
    assert (state / "session-x.json").is_file()
    manifests = list(state.glob("runs/*/manifest.json"))
    assert len(manifests) == 1
    assert '"status": "completed"' in manifests[0].read_text()


def test_session_without_state_dir_uses_cwd_agents590(tmp_path: Path) -> None:
    assert cli.main(["krisj_echo", "-i", "hi", "--cwd", str(tmp_path), "--session", "s"]) == 0

    assert (tmp_path / ".agents590" / "session-s.json").is_file()


def test_unwritable_state_dir_fails_with_a_friendly_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A regular file where the state directory should be makes mkdir fail.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")

    exit_code = cli.main(["krisj_echo", "-i", "hi", "--state-dir", str(blocker / "state")])

    assert exit_code == 1
    assert "could not save run records" in capsys.readouterr().err


def test_unexpected_agent_errors_print_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class CrashingAgent:
        async def run(self, instruction: str, context: RunContext) -> str:
            raise RuntimeError("model service down")

    monkeypatch.setattr(cli, "create_agent", lambda _name: CrashingAgent())

    assert cli.main(["crashing", "-i", "test"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "agents590: model service down" in err
