"""A fake UNC model client for tests, so no test ever makes a real (paid) API call.

Give `FakeSettings` the list of responses the "model" should send back, in order,
and patch it in with `use_fake_model`. Every request's keyword arguments are kept
in `settings.requests`, so tests can check the system prompt and tool list.
"""

import json

import pytest
from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agents590.masons_special_agent import MasonsSpecialAgent


def usage(input_tokens: int, cached: int, output_tokens: int) -> ResponseUsage:
    return ResponseUsage(
        input_tokens=input_tokens,
        input_tokens_details=InputTokensDetails(cached_tokens=cached, cache_write_tokens=0),
        output_tokens=output_tokens,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=input_tokens + output_tokens,
    )


def tool_response(
    name: str = "shell",
    arguments: object = None,
    tokens: tuple[int, int, int] = (100, 40, 10),
) -> Response:
    """A model reply that asks for one tool call (by default `shell pwd`)."""
    call = ResponseFunctionToolCall(
        type="function_call",
        call_id="call_1",
        name=name,
        arguments=json.dumps({"command": "pwd"} if arguments is None else arguments),
    )
    # model_construct skips validation, so the test only fills the fields the agent reads.
    return Response.model_construct(output=[call], usage=usage(*tokens))


def final_response(text: str, tokens: tuple[int, int, int] = (150, 100, 5)) -> Response:
    """A model reply with plain text and no tool calls, which ends the loop."""
    message = ResponseOutputMessage(
        id="msg_1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )
    return Response.model_construct(output=[message], usage=usage(*tokens))


class FakeResponses:
    def __init__(
        self, replies: list[Response | Exception], requests: list[dict[str, object]]
    ) -> None:
        self.replies = replies
        self.requests = requests

    async def create(self, **kwargs: object) -> Response:
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, settings: "FakeSettings") -> None:
        self.responses = FakeResponses(settings.replies, settings.requests)

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSettings:
    model = "fake-model"

    def __init__(self, replies: list[Response | Exception]) -> None:
        self.replies = replies
        self.requests: list[dict[str, object]] = []

    def create_async_client(self) -> FakeClient:
        return FakeClient(self)


def use_fake_model(
    monkeypatch: pytest.MonkeyPatch, replies: list[Response | Exception]
) -> FakeSettings:
    """Make every MasonsSpecialAgent (including subagents) talk to one fake model."""
    settings = FakeSettings(replies)
    monkeypatch.setattr(MasonsSpecialAgent, "provider_settings", lambda _self, _ctx: settings)
    return settings
