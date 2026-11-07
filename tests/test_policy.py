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
