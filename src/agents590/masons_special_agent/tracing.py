"""Record what the agent does: an ATIF trajectory for Harbor plus stderr log lines.

Harbor reads the ATIF file (via the shared `agents590.atif` recorder) to show each
step and the token totals in `harbor view`. The log lines go to stderr so they never
mix into the final answer, which the CLI prints on stdout.
"""

import json
import logging
import sys
from typing import cast

from openai.types.responses import Response, ResponseFunctionToolCall

from agents590.atif import ATIFFinalMetrics, ATIFMetrics, ATIFRecorder, ATIFToolCall, JSONValue
from agents590.masons_special_agent.redaction import redact

logger = logging.getLogger("agents590.masons_special_agent")


class RedactSecrets(logging.Filter):
    """Blank out API keys in every log line before it is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        # getMessage() fills in the %s arguments; store the cleaned result so the
        # handler doesn't format the original arguments again.
        record.msg = redact(record.getMessage())
        record.args = None
        return True  # keep the line (just cleaned)


def configure_logging() -> None:
    """Send this agent's log lines to stderr, unless the caller set up logging already."""
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactSecrets())
    handler.setFormatter(logging.Formatter("[masons_special_agent] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def usage_metrics(response: Response) -> ATIFMetrics | None:
    """Pull the token counts the API reports for one response."""
    usage = response.usage
    if usage is None:
        return None
    # Cached tokens are part of input_tokens, not extra, so they are not added on top.
    return {
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "cached_tokens": usage.input_tokens_details.cached_tokens,
    }


class RunTracer:
    """Collect one run's steps and token totals."""

    def __init__(self, model: str, *, write_trajectory: bool = True) -> None:
        self.model = model
        if write_trajectory:
            self.recorder = ATIFRecorder.from_environment(agent="masons_special_agent", model=model)
        else:
            # A subagent must not write the trajectory file: it would overwrite
            # the parent's. A path of None keeps the steps in memory only.
            self.recorder = ATIFRecorder(None, agent="masons_special_agent", model=model)
        self.model_calls = 0
        self.totals: ATIFFinalMetrics = {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_cached_tokens": 0,
            "total_steps": 0,
        }

    def record_instruction(self, instruction: str) -> None:
        self.recorder.add("user", instruction)

    def record_response(self, response: Response) -> None:
        """Add one model response as an ATIF step and log its token use."""
        self.model_calls += 1
        tool_calls: list[ATIFToolCall] = []
        for item in response.output:
            if isinstance(item, ResponseFunctionToolCall):
                try:
                    arguments: JSONValue = json.loads(item.arguments)
                except json.JSONDecodeError:
                    arguments = {"raw_arguments": item.arguments}
                tool_calls.append(
                    {
                        "tool_call_id": item.call_id,
                        "function_name": item.name,
                        "arguments": arguments,
                    }
                )

        fields: dict[str, JSONValue] = {"model_name": self.model}
        if tool_calls:
            fields["tool_calls"] = cast(JSONValue, tool_calls)
        metrics = usage_metrics(response)
        if metrics is not None:
            fields["metrics"] = cast(JSONValue, metrics)
            self.totals["total_prompt_tokens"] += metrics["prompt_tokens"]
            self.totals["total_completion_tokens"] += metrics["completion_tokens"]
            self.totals["total_cached_tokens"] += metrics["cached_tokens"]
        self.recorder.add("agent", response.output_text, **fields)

        logger.info(
            "model call %d: input=%s cached=%s output=%s tools=%s",
            self.model_calls,
            metrics["prompt_tokens"] if metrics else "?",
            metrics["cached_tokens"] if metrics else "?",
            metrics["completion_tokens"] if metrics else "?",
            [call["function_name"] for call in tool_calls] or "none",
        )

    def record_tool_result(self, call_id: str, output: str) -> None:
        """Attach a tool's output to the model step that asked for it."""
        steps = self.recorder.document["steps"]
        if not steps:
            return
        observation = steps[-1].setdefault("observation", {"results": []})
        observation["results"].append({"source_call_id": call_id, "content": output})
        self.recorder.write()

    def add_subagent_usage(self, subagent: "RunTracer") -> None:
        """Add a finished subagent's token totals to this run's totals.

        Without this, Harbor would only count the parent's tokens and
        under-report what a run with subagents really used.
        """
        self.model_calls += subagent.model_calls
        self.totals["total_prompt_tokens"] += subagent.totals["total_prompt_tokens"]
        self.totals["total_completion_tokens"] += subagent.totals["total_completion_tokens"]
        self.totals["total_cached_tokens"] += subagent.totals["total_cached_tokens"]
        logger.info(
            "subagent used %d model calls: input=%d cached=%d output=%d",
            subagent.model_calls,
            subagent.totals["total_prompt_tokens"],
            subagent.totals["total_cached_tokens"],
            subagent.totals["total_completion_tokens"],
        )

    def finish(self) -> None:
        """Write the run's totals. Called from `finally`, so a crash still reports them."""
        self.totals["total_steps"] = len(self.recorder.document["steps"])
        self.recorder.document["final_metrics"] = self.totals
        self.recorder.write()
        logger.info(
            "total after %d model calls: input=%d cached=%d output=%d",
            self.model_calls,
            self.totals["total_prompt_tokens"],
            self.totals["total_cached_tokens"],
            self.totals["total_completion_tokens"],
        )
