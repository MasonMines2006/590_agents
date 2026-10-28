"""The course starter agent, independent of Harbor."""

import asyncio
from pathlib import Path

from openai.types.responses import (
    FunctionToolParam,
    Response,
    ResponseFunctionToolCall,
    ResponseInputItemParam,
)

from agents590.contracts import RunContext
from agents590.provider import UNCOpenAISettings

ALLOWED_COMMANDS = ["ls", "pwd", "echo", "cat", "head"]


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

    async def run(self, instruction: str, context: RunContext) -> str:
        # Start with settings = self.provider_settings(context), then open
        # settings.create_async_client() and use model=settings.model.
        # Send Responses requests with store=False and background=False.
        # Run shell tools with context.cwd as their explicit working directory.

        UNC_provider = self.provider_settings(context)
        max_loops = 20

        async with UNC_provider.create_async_client() as UNC_client:
            items: list[ResponseInputItemParam] = [{"role": "user", "content": instruction}]

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

                items.append({"role": "assistant", "content": UNC_client_response.output_text})

                for item in UNC_client_response.output:
                    if not isinstance(item, ResponseFunctionToolCall):
                        continue

        # output = UNC_client_response.output

        return f"Could not think of an answer in {max_loops}"
