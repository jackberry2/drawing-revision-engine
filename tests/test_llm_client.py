import base64
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from dre.llm.client import call_structured, encode_image


def test_encode_image_sniffs_media_type_from_content_not_extension(tmp_path):
    """A file's extension must never decide media_type — a real production
    bug had a genuinely-PDF file pass straight through with
    media_type='application/pdf', which Claude's API rejects. Here a file
    named with a misleading .txt extension but containing real PNG bytes
    must still be correctly identified as image/png."""
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"rest of a fake but real-signature png"
    path = tmp_path / "sheet.txt"
    path.write_bytes(png_bytes)

    result = encode_image(path)

    assert result["source"]["media_type"] == "image/png"
    assert result["source"]["data"] == base64.standard_b64encode(png_bytes).decode("utf-8")


def test_encode_image_raises_on_pdf_instead_of_sending_bad_media_type(tmp_path):
    path = tmp_path / "sheet.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF")

    with pytest.raises(ValueError, match="not a recognized image format"):
        encode_image(path)


class _FakeResult(BaseModel):
    value: str


def _fake_response(*, tool_input: dict, input_tokens: int, output_tokens: int):
    tool_block = SimpleNamespace(type="tool_use", name="emit__fakeresult", input=tool_input)
    return SimpleNamespace(
        content=[tool_block],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def test_call_structured_records_usage_for_every_real_attempt_including_retries():
    """Retries are genuinely billed real API calls, not free re-parses — a
    usage_sink must get one entry per attempt, not just per successful
    parse. First attempt here returns a malformed payload (missing the
    required field) and gets retried; only the second attempt parses."""
    responses = [
        _fake_response(tool_input={}, input_tokens=100, output_tokens=10),
        _fake_response(tool_input={"value": "ok"}, input_tokens=120, output_tokens=15),
    ]
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: responses.pop(0)))
    usage_sink: list[dict] = []

    with patch("dre.llm.client.get_client", return_value=fake_client):
        result = call_structured(
            system="sys",
            user_content=[{"type": "text", "text": "hi"}],
            response_model=_FakeResult,
            model="claude-sonnet-5",
            usage_sink=usage_sink,
        )

    assert result.value == "ok"
    assert usage_sink == [
        {"model": "claude-sonnet-5", "input_tokens": 100, "output_tokens": 10},
        {"model": "claude-sonnet-5", "input_tokens": 120, "output_tokens": 15},
    ]


def test_call_structured_without_usage_sink_is_unaffected():
    response = _fake_response(tool_input={"value": "ok"}, input_tokens=50, output_tokens=5)
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))

    with patch("dre.llm.client.get_client", return_value=fake_client):
        result = call_structured(
            system="sys",
            user_content=[{"type": "text", "text": "hi"}],
            response_model=_FakeResult,
            model="claude-sonnet-5",
        )

    assert result.value == "ok"
