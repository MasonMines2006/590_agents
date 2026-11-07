.PHONY: setup check test smoke sandbox clean

setup:
	uv sync --locked

check:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked pyright

test:
	uv run --locked python -m pytest

# Makes one billable UNC Azure Responses API request.
smoke:
	uv run --locked --env-file .env openai-smoke

# Open a shell in a throwaway Docker container for local agent sessions.
#  - Only this repo is visible to the agent (mounted at /work). Your home
#    folder, SSH keys and other projects are not.
#  - .env is passed as environment variables with --env-file. The file itself
#    is hidden: /dev/null is mounted over /work/.env, so `cat .env` is empty.
#  - The container's virtualenv lives in /opt/venv, so it never touches the
#    macOS .venv in this folder.
#  - --rm deletes the container when you exit. Changes under /work are real
#    changes to this repo, so commit or branch first.
# Inside, run e.g.: uv run agents590-chat --session demo -i "..."
SANDBOX_IMAGE ?= ghcr.io/astral-sh/uv:python3.12-bookworm-slim
sandbox:
	docker run --rm -it \
		--env-file .env \
		-v "$(CURDIR)":/work \
		-v /dev/null:/work/.env:ro \
		-w /work \
		-e UV_PROJECT_ENVIRONMENT=/opt/venv \
		$(SANDBOX_IMAGE) \
		bash -c 'uv sync --locked && exec bash'

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov build dist
	rm -f .coverage .coverage.*
	find src tests -type d \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} +
