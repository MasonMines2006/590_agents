"""Blank out API keys before text reaches the model, the ATIF file, or the logs.

Idea from Yewon Song's `yewons_stale_path` agent (diagnostics.py, `RunDiagnostics.text`).

The shell already runs without the API keys in its environment (agent.py
removes SECRET_ENV_VARS, below, from it), but a key can still show up another way, for
example `cat .env` or a config file that has the key written in it. Redacting
every tool result is the second layer of protection.
"""

import os

REDACTED = "[REDACTED]"

# Environment variables whose values must never be shown.
SECRET_ENV_VARS = ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY")

# Very short values (or empty ones) would replace normal text all over the
# place, so they are skipped. Real API keys are much longer than this.
MIN_SECRET_LENGTH = 8


def secret_values() -> list[str]:
    """The current secret values, longest first.

    Longest first matters: if one key contains another, replacing the short
    one first would leave part of the long one visible.
    """
    values = {os.environ.get(name, "") for name in SECRET_ENV_VARS}
    real = [value for value in values if len(value) >= MIN_SECRET_LENGTH]
    return sorted(real, key=len, reverse=True)


def redact(text: str) -> str:
    """Return *text* with every known secret value replaced by [REDACTED]."""
    for secret in secret_values():
        text = text.replace(secret, REDACTED)
    return text
