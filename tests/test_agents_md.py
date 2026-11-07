"""Check that masons_special_agent reads the working directory's AGENTS.md."""

import asyncio
from pathlib import Path

import pytest
from fake_model import final_response, use_fake_model

from agents590.contracts import RunContext
from agents590.masons_special_agent import MasonsSpecialAgent
from agents590.masons_special_agent.agent import MAX_AGENTS_MD_CHARS


def system_prompt_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run the agent once against a fake model and return the system prompt it sent."""
    fake = use_fake_model(monkeypatch, [final_response("done")])
    asyncio.run(MasonsSpecialAgent().run("do the task", RunContext(cwd=tmp_path)))
    return str(fake.requests[0]["instructions"])


def test_agents_md_is_added_to_the_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AGENTS.md").write_text("Every new function needs a docstring.\n")

    prompt = system_prompt_for(tmp_path, monkeypatch)

    assert "Project conventions (from AGENTS.md):" in prompt
    assert "Every new function needs a docstring." in prompt


def test_no_agents_md_means_no_conventions_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "Project conventions" not in system_prompt_for(tmp_path, monkeypatch)


def test_long_agents_md_is_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "AGENTS.md").write_text("rule\n" * MAX_AGENTS_MD_CHARS)

    prompt = system_prompt_for(tmp_path, monkeypatch)

    assert "[truncated" in prompt
    assert len(prompt) < 2 * MAX_AGENTS_MD_CHARS
