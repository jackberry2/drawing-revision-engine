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
from typing import Optional, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

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
    max_attempts: int = 3,
    temperature: Optional[float] = None,
) -> T:
    """Call Claude with a forced tool-use call shaped by `response_model`,
    returning a validated instance of it. Retries the call itself (not just
    the parse) on a malformed tool payload — an occasional model quirk on
    larger/nested schemas, not something a client-side fix can guarantee
    against, but re-sampling reliably clears it.

    `temperature` is omitted from the request unless explicitly passed —
    `claude-sonnet-5` (the current default model) rejects the parameter
    entirely with a 400 ("temperature is deprecated for this model"), so
    this can't be used as a consistency lever against that model. Left in
    place for models that do support it."""
    tool_name = f"emit_{response_model.__name__.lower()}"
    tool = {
        "name": tool_name,
        "description": f"Emit the {response_model.__name__} result.",
        "input_schema": response_model.model_json_schema(),
    }

    create_kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user_content}],
    )
    if temperature is not None:
        create_kwargs["temperature"] = temperature

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        response = get_client().messages.create(**create_kwargs)

        tool_block = next(
            (b for b in response.content if b.type == "tool_use" and b.name == tool_name), None
        )
        if tool_block is None:
            if response.stop_reason == "max_tokens":
                raise RuntimeError(
                    f"Claude hit max_tokens ({max_tokens}) before finishing the "
                    f"{tool_name!r} tool call — response was truncated, not just malformed."
                )
            raise RuntimeError(f"Claude did not return the expected tool call {tool_name!r}")

        try:
            return _parse_tool_input(tool_block.input, response_model)
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = exc

    raise RuntimeError(
        f"Claude returned a malformed {tool_name!r} payload {max_attempts} times in a row"
    ) from last_error


def _parse_tool_input(raw_input: dict, response_model: Type[T]) -> T:
    """Occasionally the model nests the whole JSON payload as a string under
    a single key instead of returning the structured object directly (a
    known tool-use quirk on larger/nested schemas). Fall back to parsing
    that string before giving up."""
    try:
        return response_model.model_validate(raw_input)
    except ValidationError:
        if isinstance(raw_input, dict) and len(raw_input) == 1:
            (only_value,) = raw_input.values()
            if isinstance(only_value, str):
                return response_model.model_validate(json.loads(only_value))
        raise
