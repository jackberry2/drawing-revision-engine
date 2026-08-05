"""Thin wrapper around the Anthropic SDK.

Every pipeline stage that calls Claude goes through `call_structured`, which
forces a tool-use call shaped by the stage's pydantic output schema instead of
parsing free-form text. That keeps every stage's LLM output guaranteed-valid
against the same schemas the rest of the pipeline (and the DB logs) expect.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Type, TypeVar

import anthropic
from pydantic import BaseModel

from dre import config

T = TypeVar("T", bound=BaseModel)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_client: anthropic.Anthropic | None = None


def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text()


def dump_models(models: list[BaseModel]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models])


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def encode_image(path: Path) -> dict:
    media_type = mimetypes.guess_type(str(path))[0] or "image/png"
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def call_structured(
    *,
    system: str,
    user_content: list[dict],
    response_model: Type[T],
    model: str = config.REASONING_MODEL,
    max_tokens: int = 8192,
) -> T:
    """Call Claude with a forced tool-use call shaped by `response_model`,
    returning a validated instance of it."""
    tool_name = f"emit_{response_model.__name__.lower()}"
    tool = {
        "name": tool_name,
        "description": f"Emit the {response_model.__name__} result.",
        "input_schema": response_model.model_json_schema(),
    }

    response = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user_content}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return response_model.model_validate(block.input)

    raise RuntimeError(f"Claude did not return the expected tool call {tool_name!r}")
