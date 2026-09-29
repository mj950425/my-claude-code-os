"""Read confirmed human precedents for a single policy/field; never write ledgers."""
from pathlib import Path
from typing import Any
import re
import jsonschema

from catalog_profile import load_profile
from gt_task import load_task
from gt_review import answered_questions
from policy_prompt import decision_rules

RETRIEVAL_SCHEMA = {
    "type": "object", "properties": {
        "matches": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "properties": {
                "id": {"type": "string"},
                "similarity": {"type": "string"},
                "difference": {"type": "string"},
                "ruleIds": {"type": "array", "items": {"type": "string"}},
            }, "required": ["id", "similarity", "difference", "ruleIds"],
            "additionalProperties": False}},
    }, "required": ["matches"], "additionalProperties": False,
}


def load_candidates(profile_path: Path, definitions: Path, field: str,
                    subject_key: str | None) -> list[dict[str, Any]]:
    profile = load_profile(profile_path)
    spec = profile.get("policyTask") or profile.get("gtTask") or {}
    if not isinstance(spec, dict) or not isinstance(spec.get("definitions"), str):
        raise ValueError("판례 프로필에 정책 문서 경로가 없습니다")
    from gt_task import resolve
    configured = resolve(profile, {"path": spec["definitions"],
                                   "root": spec.get("definitionsRoot") or "project"})
    if configured.resolve() != definitions.resolve():
        raise ValueError("판례 프로필과 선택한 정책 문서가 다릅니다")
    if not profile.get("gtTask"):
        return []
    task = load_task(profile)
    if field not in {item["id"] for item in task["fields"]}:
        raise ValueError("판례 프로필에 요청한 필드가 없습니다")
    # `answered_questions` is the source of confirmed human answers. Restrict by
    # its owning profile and field, and never make a precedent out of this case.
    rows = [r for r in answered_questions(profile, task) if r.get("field") == field]
    if rows and not subject_key:
        raise ValueError("자기 정답을 판례로 조회하지 않도록 --subject-key가 필요합니다")
    rules = decision_rules(definitions, field)
    result = []
    for row in rows:
        key = row.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        if key == subject_key:
            continue
        linked = [r["id"] for r in rules if row["decisionId"] in
                  re.findall(r"GTD-\d+(?:-[0-9a-f]{4})?", str(r.get("출처", "")) + " " + str(r.get("판례", "")))]
        linked = sorted(set(linked) | (set(row.get("ruleIds", [])) & {r["id"] for r in rules}))
        result.append({"id": row["decisionId"], "policy": profile["id"], "field": field,
                       "question": row["question"], "context": row.get("here", ""),
                       "answer": row["answer"], "answerName": row["answerName"],
                       "ruleIds": linked, "decidedAt": row.get("decidedAt"),
                       "source": "incr-human-review" if row.get("source") == "incr" else "gt-human-review"})
    return result


def selected_precedents(reply: dict, candidates: list[dict], rule_ids: set[str]) -> list[dict]:
    if not isinstance(reply, dict) or reply.get("is_error") or not isinstance(reply.get("structured_output"), dict):
        raise ValueError("판례 검색 에이전트가 유효한 결과를 반환하지 않았습니다")
    structured = reply["structured_output"]
    try:
        jsonschema.validate(structured, RETRIEVAL_SCHEMA)
    except jsonschema.ValidationError as error:
        raise ValueError(f"판례 검색 결과가 계약과 다릅니다: {error.message}") from error
    matches = structured["matches"]
    known: dict[str, dict] = {}
    for row in candidates:
        identifier = row.get("id") if isinstance(row, dict) else None
        if not isinstance(identifier, str) or identifier in known:
            raise ValueError("확정 판례 후보의 식별자가 없거나 중복되었습니다")
        known[identifier] = row
    found = []
    seen = set()
    for match in matches:
        if not isinstance(match, dict):
            raise ValueError("판례 검색 결과 항목은 객체여야 합니다")
        identifier = match.get("id")
        if not isinstance(identifier, str) or identifier not in known or identifier in seen:
            raise ValueError("판례 검색 결과에 제공하지 않은 ID 또는 중복 ID가 있습니다")
        referenced_rules = match.get("ruleIds")
        if (not isinstance(referenced_rules, list)
                or any(not isinstance(rule_id, str) for rule_id in referenced_rules)
                or not set(referenced_rules) <= rule_ids):
            raise ValueError("판례 검색 결과의 규칙 ID가 현재 정책에 없습니다")
        if not all(isinstance(match.get(k), str) for k in ("similarity", "difference")):
            raise ValueError("판례의 유사점과 차이점이 필요합니다")
        seen.add(identifier)
        found.append({"precedent": known[identifier], "relevance": match})
    return found
