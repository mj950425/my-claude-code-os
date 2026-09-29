#!/usr/bin/env python3
"""Isolated policy evaluation with SDK subagents and script-owned validation/review gates."""
from __future__ import annotations
import argparse
import dataclasses
import json
from pathlib import Path
import shutil
import sys
import tempfile
import anyio
import jsonschema
from claude_agent_sdk import (AgentDefinition, AssistantMessage,
                             HookMatcher, ResultMessage, SystemMessage, query)
from policy_prompt import decision_rules, render_agent_policy as render_policy
from policy_precedents import load_candidates
from policy_sdk_runtime import (DEFAULT_MODEL, force_subscription_authentication,
                                sdk_options, subscription_auth_source,
                                AuthenticationError, OUTPUT_SCHEMA, input_data,
                                sdk_readable_case, validate_result)  # noqa: E402

MAX_SUBAGENT_CALLS = 6
REVIEW_SCHEMA = {
    "type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["EXTRACT_ERROR", "GT_SUSPECT", "POLICY_GAP"]},
        "reason": {"type": "string"}, "feedback": {"type": "string"},
        "evidenceImageIds": {"type": "array", "items": {"type": "string"}},
    }, "required": ["verdict", "reason", "feedback", "evidenceImageIds"],
    "additionalProperties": False,
}

def output_schema(allowed):
    schema = json.loads(json.dumps(OUTPUT_SCHEMA))
    schema["properties"]["value"]["enum"] = allowed
    schema["properties"]["evidenceImageIds"] = {
        "type": "array", "items": {"type": "string"},
        "description": "결론을 뒷받침하는 input.json images 배열의 id (image-01 등). 이미지가 없으면 빈 배열"}
    schema["required"].append("evidenceImageIds")
    return schema


def agent_definitions(root: Path, model: str):
    common = (f"정책은 {root / 'policy.txt'}, 입력은 {root / 'input.json'}에 있다. "
              "필요한 원본은 Read로 확인한다. 작업에 필요한 정책 전체의 우선순위를 적용한다. "
              "관찰마다 입력 images 배열의 id, 적용 규칙 ID, 불확실한 점을 보고한다. ")
    return {
        "reader": AgentDefinition(description="판독 에이전트: 정책과 입력으로 속성 판독; 이미지 묶음 관찰도 수행",
            prompt="너는 판독 에이전트다. " + common + "위임받은 범위가 부분 관찰이면 결론을 확정하지 않는다.",
            tools=["Read"], model=model, maxTurns=18),
        "reviewer": AgentDefinition(description="검수 에이전트: 판단 충돌과 근거 누락을 독립 검토",
            prompt="너는 검수 에이전트다. " + common + "다른 판단을 그대로 따르지 않고 원본과 정책으로 검토한다.",
            tools=["Read"], model=model, maxTurns=18),
        "precedent": AgentDefinition(description="판례 에이전트: 같은 정책의 확정 판례에서 관련 조건 검색",
            prompt="너는 판례 에이전트다. " + common +
                   f"{root / 'precedents.json'}의 후보에서 유사점, 차이점, 적용 가능한 규칙과 판례 ID를 보고한다. 관련 판례가 없으면 없다고 보고한다.",
            tools=["Read"], model=model, maxTurns=12),
    }


async def invoke(root, prompt, model, schema, orchestrate=True):
    force_subscription_authentication()
    trace = []
    delegation_count = 0
    print(f"[{'orchestrator' if orchestrate else 'reviewer'}] start model={model}", file=sys.stderr, flush=True)
    async def guard(event, tool_id, context):
        nonlocal delegation_count
        name = event.get("tool_name", "")
        args = event.get("tool_input", {})
        trace.append({"event": "tool_request", "tool": name, "id": tool_id, "input": args})
        if name in ("Agent", "Task"):
            delegation_count += 1
            print(f"[delegate {delegation_count}/{MAX_SUBAGENT_CALLS}] {args.get('subagent_type', 'unknown')}", file=sys.stderr, flush=True)
            if delegation_count > MAX_SUBAGENT_CALLS:
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                        "permissionDecisionReason": "Six subagent calls is the stage limit."}}
        if name == "Read":
            path = Path(args.get("file_path", ""))
            if not path.is_absolute():
                path = root / path
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                        "permissionDecisionReason": "Only the isolated case bundle is readable."}}
        return {}
    async def observed(event, tool_id, context):
        trace.append({"event": "tool_completed", "tool": event.get("tool_name"),
                      "id": tool_id, "input": event.get("tool_input", {}), "completed": True})
        return {}
    async def failed(event, tool_id, context):
        trace.append({"event": "tool_failed", "tool": event.get("tool_name"),
                      "id": tool_id, "input": event.get("tool_input", {}),
                      "error": event.get("error")})
        return {}
    tools = ["Read", "Agent"] if orchestrate else ["Read"]
    options = sdk_options(
        model=model, cwd=str(root), tools=tools, schema=schema, max_turns=32,
        agents=agent_definitions(root, model) if orchestrate else None,
        hooks={"PreToolUse": [HookMatcher(hooks=[guard])],
               "PostToolUse": [HookMatcher(hooks=[observed])],
               "PostToolUseFailure": [HookMatcher(hooks=[failed])]},
        system_prompt=("너는 오케스트레이터 에이전트다. 판독 에이전트에게 판독을 위임하고, "
                       "입력의 양과 판단의 복잡도에 따라 이미지 묶음 분업, 판례 검색, 독립 검수를 선택한다. "
                       "최대 6회까지 하위 에이전트를 호출한다. 최종 결과는 전체 정책의 우선순위에 따라 종합한다. "
                       "이미지는 하위 에이전트가 확인하며 너는 전달받은 증거와 파일 경로로 조율한다."
                       if orchestrate else "너는 검수 에이전트다. 정책과 원본 증거로 독립 검토한다. 기존 정답도 틀릴 수 있다."),
    )
    result = {"is_error": True, "structured_output": None}
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            source = subscription_auth_source(message)
            trace.append({"event": "init", "model": message.data.get("model"), "tools": message.data.get("tools"),
                          "apiKeySource": source})
        elif isinstance(message, AssistantMessage):
            trace.append({"event": "assistant", "model": message.model,
                          "parentToolUseId": message.parent_tool_use_id,
                          "tools": [{"name": b.name, "id": b.id, "input": b.input}
                                    for b in message.content if hasattr(b, "name") and hasattr(b, "input")]})
        elif isinstance(message, ResultMessage):
            result.update(is_error=message.is_error, structured_output=message.structured_output,
                          result=message.result, num_turns=message.num_turns,
                          duration_ms=message.duration_ms, usage=message.usage,
                          modelUsage={k: dataclasses.asdict(v) if dataclasses.is_dataclass(v) else v
                                      for k, v in (message.model_usage or {}).items()},
                          permissionDenials=message.permission_denials)
    print(f"[stage complete] is_error={result['is_error']}", file=sys.stderr, flush=True)
    result["trace"] = trace
    result["delegationObserved"] = any(
        t.get("event") == "tool_completed" and t.get("tool") in ("Agent", "Task")
        or t.get("event") == "assistant" and t.get("parentToolUseId") for t in trace)
    return result


async def run(case, definitions, field, model=DEFAULT_MODEL, profile=None, subject_key=None):
    policy, allowed = render_policy(definitions, field)
    rules = {r["id"] for r in decision_rules(definitions, field)}
    candidates = load_candidates(profile, definitions, field, subject_key) if profile and profile.is_file() else []
    case_ids = {c["id"] for c in candidates}
    schema = output_schema(allowed)
    expected = case.get("expected")
    if expected is not None and expected not in allowed:
        raise ValueError("expected is not an allowed value")
    record = {"field": field, "modelRequested": model, "case": case, "policy": policy,
              "precedents": {"candidateCount": len(candidates),
                             "status": "AVAILABLE" if candidates else "NO_CONFIRMED_CANDIDATES"},
              "steps": [], "scored": expected is not None}
    with tempfile.TemporaryDirectory(prefix="policy-multi-agent-") as temp:
        root = Path(temp)
        adapted, _ = sdk_readable_case(case, root)
        images = []
        for i, raw in enumerate(adapted.get("images", []), 1):
            source = Path(raw)
            target = root / f"evidence-{i:02d}{source.suffix}"
            shutil.copyfile(source, target)
            images.append({"id": f"image-{i:02d}", "path": str(target)})
        image_ids = {i["id"] for i in images}
        path_mapping = {str(old): new["path"] for old, new in zip(case.get("images", []), images)}
        def remap(value):
            if isinstance(value, dict):
                return {k: remap(v) for k, v in value.items()}
            if isinstance(value, list):
                return [remap(v) for v in value]
            return path_mapping.get(value, value) if isinstance(value, str) else value
        (root / "input.json").write_text(json.dumps({"data": remap(input_data(case)), "images": images}, ensure_ascii=False))
        (root / "policy.txt").write_text(policy)
        (root / "precedents.json").write_text(json.dumps(candidates, ensure_ascii=False))
        base = (f"{field}를 정책에 따라 판독한다. 정책: {root / 'policy.txt'}, 입력: {root / 'input.json'}. "
                f"이미지 {len(images)}개, 확정 판례 {len(candidates)}개. "
                "판독을 위임하고, 필요에 따라 멀티 에이전트로 분업한다. 제공 이미지 전체를 확인하게 한다. "
                "관련 판례가 있으면 판례 에이전트를 활용한다.")
        def checked(resp, contract=schema):
            if resp["is_error"]:
                raise RuntimeError(resp.get("result") or "SDK stage failed")
            return validate_result(resp["structured_output"], contract, rules, case_ids, image_ids)
        try:
            response = await invoke(root, base, model, schema)
            record["steps"].append({"stage": "READ", "response": response})
            output = checked(response)
            record["status"] = "NO_GOLD" if expected is None else "MATCH" if output["value"] == expected else "MISMATCH"
            if expected is not None and output["value"] != expected:
                comparison_label = case.get("comparisonLabel", "기존 정답")
                review_prompt = (f"정책 {root / 'policy.txt'}와 입력 {root / 'input.json'}의 원본 증거를 확인해 불일치를 검수한다.\n"
                                 f"판독 결과: {json.dumps(output, ensure_ascii=False)}\n{comparison_label}: {expected}\n"
                                 "판독 오류는 EXTRACT_ERROR, 비교값 오류 의심은 GT_SUSPECT, 정책의 모호함은 POLICY_GAP이다. "
                                 "feedback에는 재판독 시 확인할 증거와 규칙만 설명한다.")
                review = await invoke(root, review_prompt, model, REVIEW_SCHEMA, orchestrate=False)
                record["steps"].append({"stage": "REVIEW", "response": review})
                verdict = checked(review, REVIEW_SCHEMA)
                record["status"] = verdict["verdict"]
                if verdict["verdict"] == "EXTRACT_ERROR":
                    retry = await invoke(root, base + "\n[재판독 검토 지점]\n" + verdict["feedback"], model, schema)
                    record["steps"].append({"stage": "RETRY", "response": retry})
                    output = checked(retry)
                    record["status"] = "MATCH_AFTER_RETRY" if output["value"] == expected else "HUMAN_REVIEW"
            record["output"] = output
            opened = set()
            for step in record["steps"]:
                for event in step["response"].get("trace", []):
                    if event.get("event") != "tool_completed" or event.get("tool") != "Read":
                        continue
                    raw_path = event.get("input", {}).get("file_path")
                    if not isinstance(raw_path, str) or not raw_path:
                        continue
                    path = Path(raw_path)
                    if not path.is_absolute():
                        path = root / path
                    opened.add(str(path.resolve()))
            record["imageReadCoverage"] = {
                "total": len(images),
                "observedReadIds": [i["id"] for i in images if str(Path(i["path"]).resolve()) in opened],
                "unobservedReadIds": [i["id"] for i in images if str(Path(i["path"]).resolve()) not in opened],
                "note": "SDK PostToolUse trace; absence is not proof an image was inspected"}

            if expected is not None:
                record["correct"] = output["value"] == expected
        except AuthenticationError as exc:
            record.update(status="ERROR", errorCategory="AUTHENTICATION", error=str(exc))
        except RuntimeError as exc:
            record.update(status="ERROR", errorCategory="SDK_RUNTIME", error=str(exc))
        except (ValueError, jsonschema.ValidationError) as exc:
            record.update(status="INVALID_RESPONSE", error=str(exc))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("definitions", "case", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--subject-key")
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    for path in case.get("images", []):
        if not Path(path).is_absolute() or not Path(path).is_file():
            parser.error("images must contain existing absolute paths")
    force_subscription_authentication()
    profile = args.profile or args.definitions.parent / "profile.json"
    result = anyio.run(run, case, args.definitions, args.field, args.model, profile,
                       args.subject_key or case.get("subjectKey") or case.get("id"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "status": result["status"],
                      "correct": result.get("correct"), "steps": len(result["steps"])}, ensure_ascii=False))
    return 1 if result["status"] in ("INVALID_RESPONSE", "ERROR") else 0

if __name__ == "__main__":
    raise SystemExit(main())
