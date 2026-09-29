"""Shared, isolated Claude Agent SDK setup for policy evaluation adapters."""
from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Any

import jsonschema
from PIL import Image
from claude_agent_sdk import ClaudeAgentOptions

DEFAULT_MODEL = "claude-opus-5-5"

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "value": {"type": "string"},
        "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "rulesApplied": {"type": "array", "items": {"type": "string"},
                         "description": "판정에 실제 적용한 정책 규칙 ID"},
        "reason": {"type": "string", "description": "관찰 증거와 규칙 적용 이유를 간결한 한국어로 설명"},
        "casesApplied": {"type": "array", "items": {"type": "string"},
                         "description": "실제로 적용한 제공 판례 ID. 없으면 빈 배열"},
    },
    "required": ["value", "confidence", "rulesApplied", "reason", "casesApplied"],
    "additionalProperties": False,
}


class AuthenticationError(RuntimeError):
    """SDK selected an API-key credential when evaluation requires subscription auth."""


def input_data(case: dict[str, Any]) -> dict[str, Any]:
    """Return task input without evaluation bookkeeping such as the expected label."""
    if "data" in case:
        if not isinstance(case["data"], dict):
            raise ValueError("case.data는 작업별 입력 정보를 담은 객체여야 합니다")
        return case["data"]
    return {key: case[key] for key in
            ("title", "productName", "brand", "category", "context", "text", "options", "sizes")
            if key in case}


def sdk_readable_case(case: dict[str, Any], temp_root: Path) -> tuple[dict[str, Any], list[str]]:
    """Copy harness .img files to JPEG because Claude Read identifies images by extension."""
    adapted = dict(case)
    images: list[str] = []
    for index, raw_path in enumerate(case.get("images", []), start=1):
        source = Path(raw_path)
        if source.suffix.lower() == ".img":
            destination = temp_root / f"image-{index:02d}.jpg"
            with Image.open(source) as image:
                image.convert("RGB").save(destination, format="JPEG", quality=95)
            images.append(str(destination))
        else:
            images.append(str(source))
    adapted["images"] = images
    return adapted, images


def validate_result(value: Any, schema: dict[str, Any], rules: set[str] | None = None,
                    cases: set[str] | None = None,
                    image_ids: set[str] | None = None) -> dict[str, Any]:
    """Validate the JSON contract and ensure every reported reference was supplied."""
    jsonschema.validate(value, schema)
    for key, permitted in (("rulesApplied", rules), ("casesApplied", cases),
                           ("evidenceImageIds", image_ids)):
        if permitted is not None and not set(value.get(key, [])) <= set(permitted):
            raise ValueError(f"{key}: 제공되지 않은 참조입니다")
    return value


def sdk_options(*, model: str, cwd: str, tools: list[str], schema: dict[str, Any],
                max_turns: int, system_prompt: str | None = None,
                agents: dict[str, Any] | None = None,
                hooks: dict[str, Any] | None = None) -> ClaudeAgentOptions:
    """Build consistent no-write, no-network evaluation options."""
    return ClaudeAgentOptions(
        model=model, cwd=cwd, tools=tools, allowed_tools=tools,
        disallowed_tools=["Bash", "Write", "Edit", "WebFetch", "WebSearch"],
        permission_mode="dontAsk", strict_mcp_config=True, setting_sources=[],
        output_format={"type": "json_schema", "schema": schema},
        max_turns=max_turns,
        # Image Read events can exceed the SDK's default 1 MiB JSON message buffer.
        max_buffer_size=32 * 1024 * 1024,
        stderr=lambda line: print(line, file=sys.stderr, flush=True),
        **({"system_prompt": system_prompt} if system_prompt else {}),
        **({"agents": agents} if agents is not None else {}),
        **({"hooks": hooks} if hooks is not None else {}),
    )


def subscription_auth_source(message: Any) -> str | None:
    """Return an SDK init auth source when present; API-key sessions are rejected."""
    if getattr(message, "subtype", None) != "init":
        return None
    data = getattr(message, "data", {}) or {}
    source = data.get("apiKeySource")
    if source not in (None, "none"):
        raise AuthenticationError("Subscription authentication required; API key source detected")
    return source


def force_subscription_authentication() -> None:
    """Prevent inherited API credentials from turning evaluation into paid API usage."""
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
