"""Check skill discovery, the skills prompt section and the load_skill tool."""

import asyncio
import json
from pathlib import Path

import pytest
from fake_model import final_response, use_fake_model
from openai.types.responses import ResponseFunctionToolCall

from agents590.contracts import RunContext
from agents590.masons_special_agent import MasonsSpecialAgent
from agents590.masons_special_agent.skills import (
    SKILLS_DIR,
    available_skills,
    load_skill,
    skills_prompt,
)


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "skills"
    directory.mkdir()
    (directory / "b-skill.md").write_text("# Second skill.\n\nFull body of B.\n")
    (directory / "a-skill.md").write_text("First skill.\n\nFull body of A.\n")
    return directory


def test_prompt_lists_only_names_and_descriptions(skills_dir: Path) -> None:
    prompt = skills_prompt(skills_dir)

    assert "- a-skill: First skill." in prompt
    assert "- b-skill: Second skill." in prompt
    assert prompt.index("a-skill") < prompt.index("b-skill")
    assert "Full body" not in prompt


def test_load_skill_returns_the_full_text(skills_dir: Path) -> None:
    assert "Full body of A." in load_skill("a-skill", skills_dir)


@pytest.mark.parametrize("name", ["missing", "../skills/a-skill", "a-skill.md", ""])
def test_unknown_or_path_like_names_are_errors(skills_dir: Path, name: str) -> None:
    output = load_skill(name, skills_dir)

    assert output.startswith("[error] Unknown skill")
    assert "a-skill, b-skill" in output


def test_empty_skills_folder_adds_nothing(tmp_path: Path) -> None:
    assert skills_prompt(tmp_path) == ""


def test_shipped_skills_each_have_a_one_line_description() -> None:
    skills = available_skills(SKILLS_DIR)

    assert {"editing-large-files", "fixing-failing-tests"} <= set(skills)
    for path in skills.values():
        first_line = path.read_text().splitlines()[0]
        assert first_line.strip(), f"{path.name} needs a description on line 1"


def test_agent_offers_skills_and_loads_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_fake_model(monkeypatch, [final_response("done")])
    asyncio.run(MasonsSpecialAgent().run("task", RunContext(cwd=tmp_path)))
    action = ResponseFunctionToolCall(
        type="function_call",
        call_id="call_1",
        name="load_skill",
        arguments=json.dumps({"name": "editing-large-files"}),
    )

    output = asyncio.run(MasonsSpecialAgent().handle_tool_call(action, RunContext(cwd=tmp_path)))

    assert "- editing-large-files:" in str(fake.requests[0]["instructions"])
    assert "sed -n" in output
