"""Skills: short how-to guides the model can load when it needs them.

Each skill is a Markdown file in the `skills/` folder next to this module. The
file name (without `.md`) is the skill's name, and the FIRST LINE is a one-line
description. Only the names and descriptions go into the system prompt; the
model calls the `load_skill` tool to read a whole guide. That keeps every model
call small until a skill is actually useful (the same idea as Claude Code skills).
"""

from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"


def available_skills(directory: Path = SKILLS_DIR) -> dict[str, Path]:
    """Map each skill name to its file, sorted by name."""
    return {path.stem: path for path in sorted(directory.glob("*.md"))}


def skill_description(path: Path) -> str:
    """The first line of a skill file, without a leading Markdown '#'."""
    lines = path.read_text().splitlines()
    first_line = lines[0] if lines else ""
    return first_line.lstrip("#").strip()


def skills_prompt(directory: Path = SKILLS_DIR) -> str:
    """The system-prompt section listing every skill's name and description."""
    skills = available_skills(directory)
    if not skills:
        return ""
    lines = [f"- {name}: {skill_description(path)}" for name, path in skills.items()]
    return (
        "\n\nSkills (call load_skill with a name to read the full guide before you start):\n"
        + "\n".join(lines)
    )


def load_skill(name: str, directory: Path = SKILLS_DIR) -> str:
    """Return a skill's full text, or an error message listing the real names."""
    # Looking the name up in the dict (instead of building a path from it) means
    # a name like "../../.env" can never read a file outside the skills folder.
    skills = available_skills(directory)
    path = skills.get(name)
    if path is None:
        return f"[error] Unknown skill {name!r}. Available skills: {', '.join(skills)}"
    return path.read_text()
