"""The course starter agent, independent of Harbor."""

import asyncio
import json
from pathlib import Path
from typing import Any

from openai.types.responses import (
    FunctionToolParam,
    Response,
    ResponseFunctionToolCall,
)

from agents590.contracts import RunContext
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
    "git",
    "python3",
    "pytest",
    "uv",
    "make",
]


class MasonsSpecialAgent:
    """Scaffold for a model/tool loop using the shared standalone interface."""

    tools: list[FunctionToolParam] = [
        {
            "type": "function",
            "name": "shell",
            "description": "Execute a shell command.",
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

        if cmd.split()[0] not in whitelist:
            return f"[stderr]\nCommand not allowed: {cmd.split()[0]}"
        shell_timeout = 10  # seconds

        # i got this from https://docs.python.org/3/library/asyncio-subprocess.html#asyncio.create_subprocess_shell

        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd
        )
        try:
            async with asyncio.timeout(shell_timeout):
                stdout, stderr = await proc.communicate()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"[stderr]\nthe command timed out after {shell_timeout} seconds: {cmd}"

        print(f"[{cmd!r} exited with {proc.returncode}]")
        if stdout:
            return f"[stdout]\n{stdout.decode()}"
        if stderr:
            return f"[stderr]\n{stderr.decode()}"
        if not stdout and not stderr:
            return "[stderr]\nNo output from command."
        else:
            return "this part should never be reached lol its for the type checker"

    async def handle_tool_call(self, action: ResponseFunctionToolCall, cwd: Path) -> str:
        """Run one tool call and always return text for the model, even on errors."""
        if action.name != "shell":
            return f"[stderr]\nUnknown tool: {action.name}"

        # action.arguments is a JSON string like '{"command": "ls"}', not a dict.
        try:
            cmd = json.loads(action.arguments)["command"]
        except (json.JSONDecodeError, KeyError, TypeError):
            cmd = None
        if not isinstance(cmd, str) or not cmd:
            return "[stderr]\nNo command provided to shell tool."

        return await self.tool_shell(cmd, cwd=cwd)

    async def run(self, instruction: str, context: RunContext) -> str:
        # Start with settings = self.provider_settings(context), then open
        # settings.create_async_client() and use model=settings.model.
        # Send Responses requests with store=False and background=False.
        # Run shell tools with context.cwd as their explicit working directory.

        UNC_provider = self.provider_settings(context)
        max_loops = 20

        async with UNC_provider.create_async_client() as UNC_client:
            items: list[Any] = [{"role": "user", "content": instruction}]

            for _loop in range(max_loops):
                UNC_client_response: Response = await UNC_client.responses.create(
                    model=UNC_provider.model,
                    input=items,
                    store=False,
                    tools=self.tools,
                    background=False,
                    instructions="You are a very limited agent that is currently being used in "
                    "development"
                    ". You are only able to execute a limited set of commands. "
                    "If you try to execute a command that is not allowed, you will receive "
                    "an error "
                    "message. Check the list of whielisted commands to use, and avoid workarounds"
                    " like appending multiple commands together. They are not whitelisted"
                    " for a reason."
                    "To check this part is working, please start each sentence with "
                    "'Mason's Special "
                    "Agent:'.",
                )
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
                    items += [
                        {
                            "type": "function_call_output",
                            "call_id": action.call_id,
                            "output": output,
                        }
                    ]

        # output = UNC_client_response.output

        return f"Could not think of an answer in {max_loops}"
