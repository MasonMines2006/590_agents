"""Local capability checks used by the coding-agent tools.

The model is not a security boundary. These checks are deliberately small and
independent of prompt text so every file operation gets the same treatment.
"""

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
