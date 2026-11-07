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
from agents590.masons_special_agent.policy import (
    READ_ONLY_COMMANDS,
    check_path,
    plan_mode_refusal,
)
from agents590.masons_special_agent.skills import load_skill, skills_prompt
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

# Default number of model calls per run; `--max-turns` overrides it.
MAX_LOOPS = 30
SHELL_TIMEOUT_SECONDS = 60
# Long outputs (a huge file, a noisy test run) would flood the model's context,
# so every tool result is cut to this many characters.
MAX_OUTPUT_CHARS = 10_000
# The main agent (depth 0) may start subagents with the delegate tool, but a
# subagent (depth 1) may not start its own. This stops runaway recursion.
MAX_DELEGATION_DEPTH = 1
# A repo's AGENTS.md goes into the system prompt on every model call, so cap it.
MAX_AGENTS_MD_CHARS = 4_000

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

# The agent itself needs the API key, but the commands it runs never do. Removing
# these from the shell's environment means `python3 -c "import os; ..."` (or a
# test that prints its environment) cannot leak the key into the transcript.
SECRET_ENV_VARS = {"OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"}

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

PLAN_MODE_PROMPT = """
PLAN MODE (--plan): this run is read-only. Do not change anything.
- write_file is disabled.
- The shell only runs these read-only commands: {commands}, plus
  `git status|log|diff|show`. Redirects (>), `;`, `&&` and `$(...)` are refused.
- Inspect the code, then reply with a step-by-step plan: the files to change,
  what to change in each, and how to verify it."""


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cut long text and say how much was dropped, so the model knows it is partial."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} characters]"


def project_conventions(cwd: Path) -> str:
    """Return the repo's AGENTS.md as a system-prompt section, or "" if there is none.

    AGENTS.md is where a repository writes down its rules for coding agents
    ("run make test", "add a docstring to every function", ...). Putting it in
    the system prompt means the model sees it before its first tool call.
    """
    path = cwd / "AGENTS.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text().strip()
    except (OSError, UnicodeDecodeError) as error:
        logger.info("could not read AGENTS.md: %s", error)
        return ""
    if not text:
        return ""
    logger.info("loaded AGENTS.md (%d characters)", len(text))
    return "\n\nProject conventions (from AGENTS.md):\n" + truncate(text, MAX_AGENTS_MD_CHARS)


def shell_environment() -> dict[str, str]:
    """The environment for shell commands: ours, minus secrets, plus non-interactive settings."""
    inherited = {key: value for key, value in os.environ.items() if key not in SECRET_ENV_VARS}
    return {**inherited, **NON_INTERACTIVE_ENV}


def resolve_path(raw_path: str, cwd: Path) -> Path:
    """Treat relative paths as relative to the agent's working directory."""
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else cwd / path


# Offered only to agents that are allowed to delegate (see tool_definitions).
DELEGATE_TOOL: FunctionToolParam = {
    "type": "function",
    "name": "delegate",
    "description": (
        "Give a self-contained subtask to a fresh subagent and return its final answer. "
        "The subagent has the same tools and working directory but none of your "
        "conversation, so describe the task fully. Use it for big, separate "
        "investigations whose tool output you do not need to see yourself."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The complete subtask description"},
        },
        "required": ["task"],
        "additionalProperties": False,
    },
    "strict": True,
}


class MasonsSpecialAgent:
    """A model/tool loop with a whitelisted shell, file tools, skills and subagents."""

    def __init__(self, depth: int = 0) -> None:
        # 0 for the agent the CLI starts; each delegate() call adds 1.
        self.depth = depth

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
        {
            "type": "function",
            "name": "load_skill",
            "description": "Return the full text of one of the skills listed in your instructions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill name, e.g. 'editing-large-files'",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]

    def tool_definitions(self) -> list[FunctionToolParam]:
        """The tools this agent offers the model; subagents get no delegate tool."""
        if self.depth < MAX_DELEGATION_DEPTH:
            return [*self.tools, DELEGATE_TOOL]
        return self.tools

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
            env=shell_environment(),
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

    async def tool_delegate(self, task: str, context: RunContext, tracer: RunTracer | None) -> str:
        """Run *task* in a fresh subagent and return its final answer."""
        # The model only sees this tool when allowed, but check anyway: the
        # model can still send a tool name it was not offered.
        if self.depth >= MAX_DELEGATION_DEPTH:
            return "[error] Subagents cannot delegate further; do the task yourself."
        logger.info("delegate (depth %d): %s", self.depth + 1, truncate(task, 200))
        subagent = MasonsSpecialAgent(depth=self.depth + 1)
        sub_provider = subagent.provider_settings(context)
        sub_tracer = RunTracer(model=sub_provider.model, write_trajectory=False)
        try:
            # Same cwd and limits (including --plan), but a brand-new conversation.
            answer = await subagent.run_traced(sub_provider, sub_tracer, task, context)
        finally:
            # Count the subagent's tokens even if it crashed part-way.
            if tracer is not None:
                tracer.add_subagent_usage(sub_tracer)
        return truncate(answer)

    async def handle_tool_call(
        self,
        action: ResponseFunctionToolCall,
        context: RunContext,
        tracer: RunTracer | None = None,
    ) -> str:
        """Run one tool call and always return text for the model, even on errors.

        *tracer* is the caller's tracer; delegate() adds the subagent's tokens to it.
        """
        cwd = context.cwd
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
        name = args.get("name")
        task = args.get("task")

        if action.name == "shell":
            if not isinstance(command, str):
                return "[error] shell needs a 'command' string."
            if context.plan_only:
                refusal = plan_mode_refusal(command)
                if refusal is not None:
                    logger.info("shell %r refused: %s", command, refusal)
                    return f"[error] {refusal}"
            return await self.tool_shell(command, cwd=cwd)
        if action.name == "read_file":
            if not isinstance(path, str):
                return "[error] read_file needs a 'path' string."
            logger.info("read_file %s", path)
            return self.tool_read_file(path, cwd)
        if action.name == "write_file":
            if not isinstance(path, str) or not isinstance(content, str):
                return "[error] write_file needs 'path' and 'content' strings."
            if context.plan_only:
                logger.info("write_file %s refused: plan mode", path)
                return "[error] write_file is disabled in --plan mode (read-only)."
            logger.info("write_file %s (%d characters)", path, len(content))
            return self.tool_write_file(path, content, cwd)
        if action.name == "load_skill":
            if not isinstance(name, str):
                return "[error] load_skill needs a 'name' string."
            logger.info("load_skill %s", name)
            return load_skill(name)
        if action.name == "delegate":
            if not isinstance(task, str) or not task.strip():
                return "[error] delegate needs a non-empty 'task' string."
            return await self.tool_delegate(task, context, tracer)
        logger.info("unknown tool %s", action.name)
        return f"[error] Unknown tool: {action.name}"

    async def run(self, instruction: str, context: RunContext) -> str:
        configure_logging()
        UNC_provider = self.provider_settings(context)
        tracer = RunTracer(model=UNC_provider.model)
        return await self.run_traced(UNC_provider, tracer, instruction, context)

    async def run_traced(
        self,
        UNC_provider: UNCOpenAISettings,
        tracer: RunTracer,
        instruction: str,
        context: RunContext,
    ) -> str:
        """Run the task with a tracer chosen by the caller (run() or tool_delegate())."""
        instructions = SYSTEM_PROMPT.format(commands=", ".join(ALLOWED_COMMANDS))
        instructions += skills_prompt()
        instructions += project_conventions(context.cwd)
        if context.plan_only:
            instructions += PLAN_MODE_PROMPT.format(commands=", ".join(sorted(READ_ONLY_COMMANDS)))
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
            # `or` falls back to the default when the flag was not given (None).
            max_turns = context.max_turns or MAX_LOOPS
            tool_calls_made = 0

            for _turn in range(max_turns):
                UNC_client_response: Response = await UNC_client.responses.create(
                    model=UNC_provider.model,
                    input=items,
                    store=False,
                    tools=self.tool_definitions(),
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
                    limit = context.max_tool_calls
                    if limit is not None and tool_calls_made >= limit:
                        logger.info("stopping: reached --max-tool-calls %d", limit)
                        return f"Stopped: reached the limit of {limit} tool calls."
                    tool_calls_made += 1
                    output = await self.handle_tool_call(action, context, tracer)
                    tracer.record_tool_result(action.call_id, output)
                    items += [
                        {
                            "type": "function_call_output",
                            "call_id": action.call_id,
                            "output": output,
                        }
                    ]

        return f"Could not finish the task in {max_turns} model calls."
