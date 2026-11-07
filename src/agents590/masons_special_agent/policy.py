"""Local capability checks used by the coding-agent tools.

The model is not a security boundary. These checks are deliberately small and
independent of prompt text so every file operation gets the same treatment.
"""

import shlex
from pathlib import Path

# Names commonly containing credentials. Matching is case-insensitive and only
# applies to the path relative to the workspace.
_PROTECTED_NAMES = {".env", ".env.local", "id_rsa", "id_ed25519", "credentials"}


def workspace_path(raw_path: str, workspace: Path) -> Path:
    """Return a canonical path and reject traversal or symlink escapes."""
    root = workspace.expanduser().resolve()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    # Resolve existing components (including symlinks), while permitting a new
    # leaf for writes. Resolving the parent also catches a symlinked directory.
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"could not resolve path: {raw_path}") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"path is outside the workspace: {raw_path}") from error
    return resolved


def protected_path(path: Path, workspace: Path) -> bool:
    """Whether *path* has a conventional secret filename."""
    relative = path.relative_to(workspace.resolve())
    return any(part.lower() in _PROTECTED_NAMES for part in relative.parts)


def check_path(raw_path: str, workspace: Path, *, write: bool = False) -> Path:
    """Validate a tool path, including workspace and secret boundaries."""
    path = workspace_path(raw_path, workspace)
    if protected_path(path, workspace):
        operation = "write" if write else "read"
        raise ValueError(f"refusing to {operation} protected path: {path}")
    return path


# --plan mode is read-only. The shell is only allowed to run these programs,
# which only read. (Things like `sed`, `find`, `sort` and `rg` are left out on
# purpose: each has an option that writes files or runs other programs, e.g.
# `sed -i`, `find -delete`, `sort -o`, `rg --pre`.)
READ_ONLY_COMMANDS = {"pwd", "ls", "cat", "head", "tail", "grep", "wc", "diff"}
READ_ONLY_GIT_SUBCOMMANDS = {"status", "log", "diff", "show"}
# Characters that let one command line write files or start extra commands:
# `>` / `<` redirect, `;` and `&` chain commands, backticks and `$(` run a
# command inside another. Pipes (`|`) are checked segment by segment instead.
_SHELL_OPERATORS = (";", "&", ">", "<", "`", "$(", "\n")


def plan_mode_refusal(command: str) -> str | None:
    """Return why *command* is refused in --plan mode, or None if it is read-only.

    This is a best-effort allowlist, not a sandbox: it stops a well-meaning model
    from changing files by accident. A container is the real security boundary.
    """
    for operator in _SHELL_OPERATORS:
        if operator in command:
            return f"--plan mode is read-only; {operator!r} is not allowed in shell commands."
    # `cat a.py | grep def` is fine as long as every piece is read-only.
    for segment in command.split("|"):
        try:
            words = shlex.split(segment)
        except ValueError as error:
            return f"could not parse the command: {error}"
        if not words:
            return "--plan mode is read-only; empty pipeline segment."
        program = words[0]
        if program == "git":
            subcommand = words[1] if len(words) > 1 else ""
            if subcommand not in READ_ONLY_GIT_SUBCOMMANDS:
                return f"--plan mode is read-only; `git {subcommand}` is not allowed."
            if any(word.startswith("--output") for word in words):
                return "--plan mode is read-only; git --output writes a file."
        elif program not in READ_ONLY_COMMANDS:
            allowed = ", ".join(sorted(READ_ONLY_COMMANDS | {"git"}))
            return f"--plan mode is read-only; `{program}` is not allowed (allowed: {allowed})."
    return None
