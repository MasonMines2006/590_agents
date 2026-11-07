"""Mason's coding agent: a Responses API loop with shell and file tools."""

import asyncio
import contextlib
import json
import os
import signal
from pathlib import Path
from typing import Any, cast

from openai.types.responses import (
    FunctionToolParam,
    Response,
    ResponseFunctionToolCall,
)

from agents590.contracts import RunContext
from agents590.masons_special_agent.policy import check_path
from agents590.masons_special_agent.tracing import RunTracer, configure_logging, logger
from agents590.provider import UNCOpenAISettings

ALLOWED_COMMANDS = [
    "pwd",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "sed",
    "sort",
    "diff",
    "wc",
    "echo",
    "touch",
    "mkdir",
    "cp",
    "mv",
    "rm",
    "git",
    "python3",
    "pytest",
    "uv",
    "make",
]

MAX_LOOPS = 30
SHELL_TIMEOUT_SECONDS = 60
# Long outputs (a huge file, a noisy test run) would flood the model's context,
# so every tool result is cut to this many characters.
MAX_OUTPUT_CHARS = 10_000

# Git opens an editor for commit messages and `rebase --continue`, and a pager
# for `log`/`diff`. Nobody can type into those here, so the command would hang
# until the timeout. These settings make git skip both. PYTHONDONTWRITEBYTECODE
# stops test runs from leaving untracked __pycache__/ folders in the repo.
NON_INTERACTIVE_ENV = {
    "GIT_EDITOR": "true",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "PYTHONDONTWRITEBYTECODE": "1",
}

SYSTEM_PROMPT = """You are a careful coding agent working in a repository.

Work in this order:
1. Inspect: read the relevant files and search for every place the task touches.
2. Edit: make the smallest change that fully does the task. Use write_file to
   create or rewrite files; it is safer than editing code with sed.
3. Verify: run the code or tests to check your change, and fix any problems.

Rules:
- The shell tool only runs commands whose first word is one of: {commands}.
- Every shell result starts with the exit code. A non-zero exit code means the
  command failed; read stderr and fix the problem before continuing.
- Do not edit, create, or delete files the task does not ask about.
- When you are done, reply with a short summary of what you changed."""


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cut long text and say how much was dropped, so the model knows it is partial."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} characters]"


def resolve_path(raw_path: str, cwd: Path) -> Path:
    """Treat relative paths as relative to the agent's working directory."""
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else cwd / path


class MasonsSpecialAgent:
    """A model/tool loop with a whitelisted shell plus read_file and write_file."""

    tools: list[FunctionToolParam] = [
        {
            "type": "function",
            "name": "shell",
            "description": "Run one shell command and return its exit code, stdout, and stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "read_file",
            "description": "Return the full text of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "write_file",
            "description": (
                "Create or overwrite a file with the given content. "
                "Missing parent directories are created."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write"},
                    "content": {"type": "string", "description": "The complete new file text"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]

    def provider_settings(self, context: RunContext) -> UNCOpenAISettings:
        """Prefer the per-run model to the environment's configured deployment."""
        return UNCOpenAISettings.from_env(model=context.model)

    async def tool_shell(
        self,
        cmd: str,
        cwd: Path,
        whitelist: list[str] = ALLOWED_COMMANDS,
    ) -> str:
        words = cmd.split()
        if not words:
            return "[error] Empty command."
        if words[0] not in whitelist:
            return f"[error] Command not allowed: {words[0]}"

        # i got this from https://docs.python.org/3/library/asyncio-subprocess.html#asyncio.create_subprocess_shell
        # start_new_session puts the shell and everything it starts (pytest, etc.)
        # in their own process group, so a timeout can stop all of them at once.
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, **NON_INTERACTIVE_ENV},
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(SHELL_TIMEOUT_SECONDS):
                stdout, stderr = await proc.communicate()
        except TimeoutError:
            # proc.kill() alone would only stop the shell and leave its children running.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            logger.info("shell %r timed out", cmd)
            return f"[error] The command timed out after {SHELL_TIMEOUT_SECONDS} seconds: {cmd}"

        logger.info("shell %r exited with %s", cmd, proc.returncode)
        # Always send the exit code and both streams. Each stream gets half the
        # budget, so a huge stdout can never push the error message in stderr out.
        half = MAX_OUTPUT_CHARS // 2
        return (
            f"[exit code {proc.returncode}]\n"
            f"[stdout]\n{truncate(stdout.decode(errors='replace'), half)}\n"
            f"[stderr]\n{truncate(stderr.decode(errors='replace'), half)}"
        )

    def tool_read_file(self, raw_path: str, cwd: Path) -> str:
        try:
            path = check_path(raw_path, cwd)
            text = path.read_text()
        except (OSError, UnicodeDecodeError, ValueError) as error:
            return f"[error] Could not read {raw_path}: {error}"
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        # Rewriting a file from a cut-off copy would silently delete its end, so say so.
        return (
            truncate(text) + "\n[warning] This file is longer than shown. Do not rewrite it with "
            "write_file from this partial copy; read the rest with `sed -n` first."
        )

    def tool_write_file(self, raw_path: str, content: str, cwd: Path) -> str:
        try:
            path = check_path(raw_path, cwd, write=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        except (OSError, ValueError) as error:
            return f"[error] Could not write {raw_path}: {error}"
        return f"Wrote {len(content)} characters to {path}"

    async def handle_tool_call(self, action: ResponseFunctionToolCall, cwd: Path) -> str:
        """Run one tool call and always return text for the model, even on errors."""
        # action.arguments is a JSON string like '{"command": "ls"}', not a dict.
        try:
            parsed: object = json.loads(action.arguments)
        except json.JSONDecodeError:
            return "[error] Tool arguments were not valid JSON."
        if not isinstance(parsed, dict):
            return "[error] Tool arguments must be a JSON object."
        # JSON object keys are always strings; the values are checked one by one below.
        args = cast(dict[str, object], parsed)

        command = args.get("command")
        path = args.get("path")
        content = args.get("content")

        if action.name == "shell":
            if not isinstance(command, str):
                return "[error] shell needs a 'command' string."
            return await self.tool_shell(command, cwd=cwd)
        if action.name == "read_file":
            if not isinstance(path, str):
                return "[error] read_file needs a 'path' string."
            logger.info("read_file %s", path)
            return self.tool_read_file(path, cwd)
        if action.name == "write_file":
            if not isinstance(path, str) or not isinstance(content, str):
                return "[error] write_file needs 'path' and 'content' strings."
            logger.info("write_file %s (%d characters)", path, len(content))
            return self.tool_write_file(path, content, cwd)
        logger.info("unknown tool %s", action.name)
        return f"[error] Unknown tool: {action.name}"

    async def run(self, instruction: str, context: RunContext) -> str:
        configure_logging()
        UNC_provider = self.provider_settings(context)
        instructions = SYSTEM_PROMPT.format(commands=", ".join(ALLOWED_COMMANDS))
        tracer = RunTracer(model=UNC_provider.model)
        tracer.record_instruction(instruction)

        # `finally` runs even if the API call or a tool crashes, so the token
        # totals still reach Harbor for failed runs.
        try:
            return await self.run_loop(UNC_provider, instructions, instruction, context, tracer)
        finally:
            tracer.finish()

    async def run_loop(
        self,
        UNC_provider: UNCOpenAISettings,
        instructions: str,
        instruction: str,
        context: RunContext,
        tracer: RunTracer,
    ) -> str:
        async with UNC_provider.create_async_client() as UNC_client:
            items: list[Any] = [{"role": "user", "content": instruction}]

            for _loop in range(MAX_LOOPS):
                UNC_client_response: Response = await UNC_client.responses.create(
                    model=UNC_provider.model,
                    input=items,
                    store=False,
                    tools=self.tools,
                    background=False,
                    instructions=instructions,
                )
                tracer.record_response(UNC_client_response)
                actions: list[ResponseFunctionToolCall] = []
                for action in UNC_client_response.output:
                    if isinstance(action, ResponseFunctionToolCall):
                        actions.append(action)

                # no pending actions, so were done!
                if not actions:
                    return UNC_client_response.output_text

                items += UNC_client_response.output

                # Every function_call needs a function_call_output with the same call_id.
                for action in actions:
                    output = await self.handle_tool_call(action, context.cwd)
                    tracer.record_tool_result(action.call_id, output)
                    items += [
                        {
                            "type": "function_call_output",
                            "call_id": action.call_id,
                            "output": output,
                        }
                    ]

        return f"Could not finish the task in {MAX_LOOPS} model calls."
