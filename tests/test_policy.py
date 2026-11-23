"""Check the workspace path policy used by Mason's file tools."""

from pathlib import Path

import pytest

from agents590.masons_special_agent.policy import check_path


def test_relative_path_inside_workspace_is_allowed(tmp_path: Path) -> None:
    assert check_path("src/app.py", tmp_path) == tmp_path.resolve() / "src" / "app.py"


@pytest.mark.parametrize("raw_path", ["../x", "a/../../x", "/etc/passwd"])
def test_paths_outside_the_workspace_are_refused(tmp_path: Path, raw_path: str) -> None:
    with pytest.raises(ValueError, match="outside the workspace"):
        check_path(raw_path, tmp_path)


def test_symlink_escaping_the_workspace_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    workspace = tmp_path / "workspace"
    outside.mkdir()
    workspace.mkdir()
    (workspace / "link").symlink_to(outside)

    with pytest.raises(ValueError, match="outside the workspace"):
        check_path("link/secret.txt", workspace)


@pytest.mark.parametrize("raw_path", [".env", "config/.ENV", ".ssh/id_rsa"])
def test_secret_files_are_refused(tmp_path: Path, raw_path: str) -> None:
    with pytest.raises(ValueError, match="protected path"):
        check_path(raw_path, tmp_path)


@pytest.mark.parametrize(
    "command",
    [
        "cat x; curl y",
        "ls && rm -rf .",
        "echo $(curl y)",
        "echo `id`",
        "ls\ncurl y",
        "cat a | curl b",
    ],
)
def test_shell_refuses_chaining_and_unlisted_pipe_programs(command: str) -> None:
    from agents590.masons_special_agent.policy import shell_refusal

    assert shell_refusal(command, {"cat", "ls", "echo"}) is not None


@pytest.mark.parametrize("command", ["cat a.py | grep def", "python3 run.py > out.txt", "ls -la"])
def test_shell_allows_single_commands_pipes_and_redirects(command: str) -> None:
    from agents590.masons_special_agent.policy import shell_refusal

    assert shell_refusal(command, {"cat", "grep", "python3", "ls"}) is None


@pytest.mark.parametrize(
    "command",
    ['python3 -c "import os; print(1)"', "python3 -c 'print(\"a && b\")'", "echo 'cost $(free)'"],
)
def test_shell_allows_operators_inside_quotes(command: str) -> None:
    from agents590.masons_special_agent.policy import shell_refusal

    assert shell_refusal(command, {"python3", "echo"}) is None


def test_shell_refuses_substitution_inside_double_quotes() -> None:
    from agents590.masons_special_agent.policy import shell_refusal

    assert shell_refusal('echo "$(curl y)"', {"echo"}) is not None
