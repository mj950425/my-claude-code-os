#!/usr/bin/env python3
"""Run one frozen image case through Claude Agent SDK with natural-language policy.

This is an evaluation adapter, not a product inference implementation. It reads
the field's allowed values and ordered decision rules from definitions.md, puts
them in the SDK prompt, and lets Claude inspect the supplied local images.
"""
from __future__ import annotations

import argparse
import anyio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import jsonschema
from claude_agent_sdk import HookMatcher, ResultMessage, SystemMessage, query

HERE = Path(__file__).resolve()
PROJECT_ROOT = next(parent for parent in HERE.parents if (parent / ".claude").is_dir())
sys.path.insert(0, str(HERE.parent))
from policy_prompt import decision_rules, render_agent_policy as render_policy  # noqa: E402
from policy_precedents import load_candidates, selected_precedents, RETRIEVAL_SCHEMA
from policy_sdk_runtime import (DEFAULT_MODEL, force_subscription_authentication,
                                sdk_options, subscription_auth_source, AuthenticationError,
                                OUTPUT_SCHEMA, input_data, sdk_readable_case,
                                validate_result)  # noqa: E402


PROMPT_TEMPLATE = """{purpose}

[수행 지침]
제공된 입력 정보와 첨부 이미지를 확인하고, 아래 정책의 규칙을 우선순위 순서로 적용해 {field}를 판정한다.
첨부 이미지가 있으면 Read 도구로 모두 연다.

[정책]{policy_body}

[입력 정보]
{data}

[첨부 이미지]
{images}
"""


def build_prompt(case: dict[str, Any], policy: str, field: str) -> str:
    purpose, policy_body = policy.split("\n\n[정책]", 1)
    return PROMPT_TEMPLATE.format(
        purpose=purpose, policy_body=policy_body, field=field,
        data=json.dumps(input_data(case), ensure_ascii=False),
        images="\n".join(f"- {image}" for image in case.get("images", [])) or "없음",
    )



async def query_agent(prompt: str, model: str, schema: dict,
                      system_prompt: str | None = None) -> dict[str, Any]:
    force_subscription_authentication()
    trace: list[dict[str, Any]] = []

    async def observed(event, tool_id, context):
        trace.append({"event": "tool_completed", "tool": event.get("tool_name"),
                      "id": tool_id, "input": event.get("tool_input", {})})
        return {}

    async def failed(event, tool_id, context):
        trace.append({"event": "tool_failed", "tool": event.get("tool_name"),
                      "id": tool_id, "input": event.get("tool_input", {}),
                      "error": event.get("error")})
        return {}

    options = sdk_options(model=model, cwd=str(PROJECT_ROOT), tools=["Read"],
                          schema=schema, max_turns=24, system_prompt=system_prompt,
                          hooks={"PostToolUse": [HookMatcher(hooks=[observed])],
                                 "PostToolUseFailure": [HookMatcher(hooks=[failed])]})
    reply: dict[str, Any] = {"is_error": True, "structured_output": None, "trace": trace}
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage):
            try:
                source = subscription_auth_source(message)
            except AuthenticationError as exc:
                reply.update(is_error=True, error_category="AUTHENTICATION", result=str(exc))
                return reply
            if message.subtype == "init":
                trace.append({"event": "init", "model": message.data.get("model"),
                              "tools": message.data.get("tools"), "apiKeySource": source})
            reply["apiKeySource"] = source
        elif isinstance(message, ResultMessage):
            reply.update(is_error=bool(message.is_error), result=message.result or "",
                         structured_output=message.structured_output,
                         num_turns=message.num_turns, duration_ms=message.duration_ms)
            if message.is_error:
                reply["error_category"] = "SDK_RUNTIME"
            trace.append({"event": "result", "isError": bool(message.is_error),
                          "numTurns": message.num_turns, "durationMs": message.duration_ms})
    return reply


def retrieval_prompt(case: dict, policy: str, field: str, candidates: list[dict]) -> str:
    return (build_prompt(case, policy, field).replace(
                f"아래 정책의 규칙을 우선순위 순서로 적용해 {field}를 판정한다.",
                "아래 정책에서 판단 조건이 비슷한 판례를 찾는다.")
            + "\n[이번 단계]\n최종 속성 추출 전의 판례 검색 단계다. 관련 판례를 찾아 반환한다.\n"
            + "[확정 판례 후보]\n" + json.dumps(candidates, ensure_ascii=False))


async def run(case: dict[str, Any], policy: str, field: str, model: str,
              allowed: list[str], candidates: list[dict] | None = None,
              rule_ids: set[str] | None = None) -> dict[str, Any]:
    candidates = candidates or []
    selected = []
    retrieval = {"status": "SKIPPED_NO_CASES", "candidateCount": 0}
    if candidates:
        role = (HERE.parent.parent / "agents/policy-precedent-retriever.md").read_text()
        role = role.split("---", 2)[-1].strip()
        search = await query_agent(retrieval_prompt(case, policy, field, candidates),
                                   model, RETRIEVAL_SCHEMA, role)
        if search.get("is_error"):
            return {"is_error": True, "error_category": search.get("error_category", "SDK_RUNTIME"),
                    "structured_output": None, "result": search.get("result") or "판례 검색 세션이 실패했습니다.",
                    "retrieval": {"status": "FAILED", "response": search}}
        try:
            selected = selected_precedents(search, candidates, rule_ids or set())
        except ValueError as exc:
            return {"is_error": True, "error_category": "INVALID_RESPONSE",
                    "structured_output": None, "result": str(exc),
                    "retrieval": {"status": "FAILED", "response": search}}
        retrieval = {"status": "COMPLETED", "candidateCount": len(candidates),
                     "selected": selected, "response": search}
    schema = json.loads(json.dumps(OUTPUT_SCHEMA))
    schema["properties"]["value"]["enum"] = allowed
    prompt = build_prompt(case, policy, field)
    if selected:
        prompt += ("\n[관련 판례]\n현재 정책을 우선하며 조건이 맞는 판례만 참고한다.\n"
                   + json.dumps(selected, ensure_ascii=False))
    result = await query_agent(prompt, model, schema)
    result["retrieval"] = retrieval
    output = result.get("structured_output")
    if not result.get("is_error"):
        selected_ids = {r["precedent"]["id"] for r in selected}
        try:
            validate_result(output, schema, rule_ids, selected_ids)
        except (ValueError, jsonschema.ValidationError) as exc:
            result.update(is_error=True, error_category="INVALID_RESPONSE", result=str(exc))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definitions", type=Path, required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--case", type=Path, required=True, help="data에 작업 입력을 담은 JSON. images(절대 경로)와 expected는 선택")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", type=Path, help="판례 소유 프로필. 생략 시 정책 문서 옆 profile.json")
    parser.add_argument("--subject-key", help="현재 상품/사례의 원장 키. 자기 판례 제외용")
    args = parser.parse_args()
    force_subscription_authentication()
    case = json.loads(args.case.read_text(encoding="utf-8"))
    images = case.get("images", [])
    if not isinstance(images, list) or any(
            not isinstance(item, str) or not Path(item).is_absolute() or not Path(item).is_file()
            for item in images):
        parser.error("case.images는 실제 이미지 파일의 절대 경로 목록이어야 합니다")
    try:
        data = input_data(case)
    except ValueError as exc:
        parser.error(str(exc))
    if not data and not images:
        parser.error("case.data 또는 case.images에 판정할 입력이 필요합니다")
    policy, allowed = render_policy(args.definitions, args.field)
    profile = args.profile or args.definitions.parent / "profile.json"
    if args.profile and not profile.is_file():
        parser.error("지정한 판례 프로필 파일이 없습니다")
    candidates = load_candidates(profile, args.definitions, args.field, args.subject_key) if profile.is_file() else []
    rule_ids = {r["id"] for r in decision_rules(args.definitions, args.field)}
    with tempfile.TemporaryDirectory(prefix="policy-sdk-eval-") as temp:
        sdk_case, sdk_images = sdk_readable_case(case, Path(temp))
        result = anyio.run(run, sdk_case, policy, args.field, args.model, allowed, candidates, rule_ids)
    record = {"case": case, "sdkImagePaths": sdk_images, "field": args.field,
              "policy": policy, "result": result}
    opened = {str(Path(event.get("input", {}).get("file_path", "")).resolve())
              for event in result.get("trace", [])
              if event.get("event") == "tool_completed" and event.get("tool") == "Read"}
    record["imageReadCoverage"] = {
        "total": len(sdk_images),
        "observedReadPaths": [path for path in sdk_images if str(Path(path).resolve()) in opened],
        "unobservedReadPaths": [path for path in sdk_images if str(Path(path).resolve()) not in opened],
        "note": "SDK PostToolUse 기록이며, 기록이 없다는 사실만으로 이미지 미확인을 단정하지 않음",
    }
    expected = case.get("expected")
    actual = (result.get("structured_output") or {}).get("value")
    if expected is not None:
        record["scored"] = True
        record["correct"] = actual == expected
    else:
        record["scored"] = False
        record["scoringNote"] = "사례에 사람 검수 정답(expected)이 없어 정확도 집계에서 제외"
    output = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if not result.get("is_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
