#!/usr/bin/env python3
"""정책 관리 — 규칙과 사례(검수 문답)를 표준 모양으로 두고, 겹치거나 부딪히는 규칙을 **넣기 전에** 막는다.

GT 개선 하네스(`gt_review.py`)의 명령이 이 모듈을 부른다. 속성·과제를 모른다 — 규칙은 정의 문서에서, 사례는 판정 원장에서,
카테고리는 사진 색인의 맥락 열에서 읽는다.

## 표준은 두 겹이다 — 모양은 코드, 뜻은 에이전트

| 검사 | 누가 | 어디서 |
|---|---|---|
| 규칙 줄 모양·허용값·ID·출처·`조건:`·상품 이야기 금지 | 로더(`gt_task.definition_policy`) | 정책을 읽을 때마다 |
| `범위:`가 이 과제의 카테고리 이름인가 | 여기 `scope_problems` | 규칙을 쓸 때(`rule`·`qa --add`)와 `policy check` |
| 사례의 물음이 경계 물음 하나인가 | 여기 `question_problems` | `qa --lint`·`qa --rewrite`·`policy check` |
| 같은 경계에 다른 값(충돌)·같은 값(중복) | 에이전트 `policy-auditor` | 규칙을 쓸 때(문지기)와 정리 워크플로우 |

뜻의 검사를 에이전트에게 통째로 묻지 않는다. 코드가 **후보 쌍**을 좁히고(`candidate_pairs`), 에이전트는 쌍마다 답한다.
같은 칸이고, 범위가 겹치고(한 상품에 둘 다 걸릴 수 있고), 둘 다 값을 내는 쌍만 간다. 범위가 겹치지 않는 두 규칙은 문장이
반대여도 충돌이 아니다 — 같은 상품에 함께 걸리는 일이 없다. 이것을 거르지 않으면 정상 규칙이 경보로 올라와 사람이 경보를 무시하게 된다.

## 문지기 — 에이전트의 답 없이 규칙을 넣지 않는다

규칙을 쓰는 명령(`rule add|edit`·`qa --add`)은 미리 보기에서 후보 쌍을 뽑아 요청 파일(`runs/<id>/policy-audit/<지문>.request.json`)로
쓴다. 후보가 있으면 `--yes`는 같은 지문의 판정 파일(`<지문>.verdict.json` — 스킬이 `policy-auditor`의 답을 옮겨 쓴다)이 모든
쌍을 덮을 때만 쓴다. 중복·충돌이 하나라도 있으면 멈추고, 사람이 이유(`--accept-risk`)를 적어야만 넘는다 — 그 이유는 변경 이력에 남는다.
에이전트는 판정만 한다. 규칙을 합칠지 뺄지는 사람이 `rule edit|retire`로 정한다.

## 사례의 물음 다듬기 — 원장은 그대로, 다듬은 물음은 따로

표준 물음(`askHuman`)이 생기기 전의 문답은 메모에서 문장을 골라낸 것이라 물음이 둘씩 섞이고 사진 번호가 들어 있다. 조회층에서는
제목(물음)이 곧 찾기의 단서라 그대로 두면 찾지 못한다. 사람이 확인한 다듬은 물음을 `asked-rewrites.jsonl`(원장 옆, 덧붙이기만)에
남기고 사례를 읽을 때 겹쳐 쓴다. 원장의 판정은 바꾸지 않는다 — 사람이 누른 판정과 물음의 글은 다른 사실이다.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from catalog_profile import output_root, relative_or_absolute
from gt_task import CASE_TALK, TaskError, derive_condition, field_map, load_gt, rule_scope, _SCOPE_SPLIT

# 한 칸의 적용 중인 규칙이 이보다 많으면 정리할 때다 — 비슷한 규칙을 정의 문장으로 올리고 원래 규칙은 보관으로 보낸다.
RULES_PER_FIELD_LIMIT = 8
VERDICTS = ("DUPLICATE", "CONFLICT", "NARROWS", "UNRELATED")
# 넣기 전에 사람이 이유를 적어야 넘는 판정. 좁힘(NARROWS)은 예외 규칙이라 막지 않고 알린다.
BLOCKING = ("DUPLICATE", "CONFLICT")
REWRITES_LOG = "asked-rewrites.jsonl"
AUDIT_SCHEMA = "gt-policy-audit-v1"


class PolicyRejected(TaskError):
    pass


# ---------------------------------------------------------------- 카테고리 어휘

def category_map(profile: dict[str, Any], task: dict[str, Any]) -> dict[str, tuple[str, ...]] | None:
    """키마다 카테고리 경로(마디의 튜플). 맥락 열이 없거나 색인을 못 열면 None — 범위를 대조할 수 없다(모른다고 말한다)."""
    from gt_images import ImageIndex

    if not ((task.get("images") or {}).get("contextFields")):
        return None
    try:
        index = ImageIndex(profile, task)
        rows = load_gt(profile, task)
    except (TaskError, OSError):
        return None
    found: dict[str, tuple[str, ...]] = {}
    for key, row in rows.items():
        parts: list[str] = []
        for value in index.context(row).values():
            parts += [piece.strip() for piece in _SCOPE_SPLIT.split(str(value or "")) if piece.strip()]
        if parts:
            found[key] = tuple(parts)
    return found or None


def vocabulary(categories: dict[str, tuple[str, ...]] | None) -> set[str]:
    return {part for path in (categories or {}).values() for part in path}


def scope_problems(scope: list[str], categories: dict[str, tuple[str, ...]] | None) -> list[str]:
    """범위 이름 가운데 이 과제의 카테고리 마디가 아닌 것. 카테고리를 모르면 대조하지 않는다(빈 목록)."""
    if not scope or categories is None:
        return []
    known = vocabulary(categories)
    return [name for name in scope if name not in known]


def near_names(name: str, categories: dict[str, tuple[str, ...]] | None, limit: int = 5) -> list[str]:
    known = sorted(vocabulary(categories))
    return [word for word in known if name in word or word in name][:limit]


def scopes_overlap(a: list[str], b: list[str], categories: dict[str, tuple[str, ...]] | None) -> bool:
    """두 범위가 한 상품에 함께 걸릴 수 있는가. 범위가 없으면 모든 상품이다. 카테고리를 모르면 겹친다고 본다 —
    빼면 걸리는 쌍을 잃고, 에이전트는 범위를 보고 «무관»으로 가를 수 있다."""
    if not a or not b or set(a) & set(b):
        return True
    if categories is None:
        return True
    return any(any(x in path for x in a) and any(y in path for y in b) for path in set(categories.values()))


def key_in_scope(key: str, scope: list[str], categories: dict[str, tuple[str, ...]] | None) -> bool:
    if not scope or categories is None or key not in categories:
        return True
    return bool(set(scope) & set(categories[key]))


# ---------------------------------------------------------------- 사례의 물음

def question_problems(question: str) -> list[str]:
    """사례의 물음이 표준(경계 물음 하나, 상품·사진 이야기 없음)에서 벗어난 곳. 비었으면 표준이다."""
    text = (question or "").strip()
    problems = []
    if not text:
        return ["물음이 비어 있습니다"]
    if text.count("?") > 1 or re.search(r"\(\d\)|①|②", text):
        problems.append("물음이 둘 이상입니다 — 경계 하나에 물음 하나")
    talk = CASE_TALK.search(text)
    if talk:
        problems.append(f"«{talk.group(0)}» — 사진·상품 이야기는 물음이 아니라 «이 사진에서»(here)에 둡니다")
    if not re.search(r"(\?|가|까|는가|인가|나)\s*$", text):
        problems.append("물음으로 끝나지 않습니다(«~인가?»)")
    if len(text) > 160:
        problems.append("너무 깁니다 — 경계 하나만 남깁니다")
    return problems


def rewrites(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """판정 ID마다 사람이 확인한 마지막 다듬은 물음."""
    from gt_decisions import gt_dir, read_jsonl_file

    path = gt_dir(profile) / REWRITES_LOG
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl_file(path) if path.is_file() else []:
        latest[str(row.get("decisionId"))] = row
    return latest


def record_rewrite(profile: dict[str, Any], decision_id: str, question: str, here: str | None, reviewer: str,
                   confirm: bool) -> dict[str, Any]:
    from gt_decisions import gt_dir
    from gt_review import answered_questions

    reviewer, question = (reviewer or "").strip(), (question or "").strip()
    if not reviewer:
        raise PolicyRejected("누가 물음을 다듬었는지 이름을 적어 주세요(--reviewer).")
    rows = {row["decisionId"]: row for row in answered_questions(profile)}
    if decision_id not in rows:
        raise PolicyRejected(f"AI 물음에 답한 유효한 판정이 아닙니다: {decision_id} — `qa --task`로 목록을 보세요.")
    problems = question_problems(question)
    if problems:
        raise PolicyRejected("다듬은 물음도 표준이 아닙니다: " + " / ".join(problems))
    before = rows[decision_id]
    entry = {"decisionId": decision_id, "field": before["field"], "question": question,
             "here": (here or "").strip() or before.get("here") or "", "before": before["question"],
             "reviewer": reviewer, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not confirm:
        return {"applied": False, "rewrite": entry}
    path = gt_dir(profile) / REWRITES_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"applied": True, "rewrite": entry}


def lint_cases(profile: dict[str, Any]) -> list[dict[str, Any]]:
    from gt_review import answered_questions

    found = []
    for row in answered_questions(profile):
        problems = question_problems(row["question"])
        if problems:
            found.append({"decisionId": row["decisionId"], "field": row["field"], "fieldName": row["fieldName"],
                          "question": row["question"], "here": row.get("here") or "", "answerName": row["answerName"],
                          "problems": problems})
    return found


# ---------------------------------------------------------------- 후보 쌍

def _named(field: dict[str, Any], value: Any) -> str:
    names = field.get("labelNames") or {}
    return " + ".join(names.get(part, part) for part in str(value or "").split("|") if part) or "(값 없음)"


def rule_item(field: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "rule", "id": rule["id"], "text": rule["text"], "condition": rule.get("조건") or derive_condition(rule["text"]),
            "value": rule.get("value"), "valueName": _named(field, rule.get("value")) if rule.get("value") else None,
            "scope": rule_scope(rule)}


def case_item(case: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "case", "id": case["decisionId"], "question": case["question"], "here": case.get("here") or "",
            "value": case["answer"], "valueName": case["answerName"], "rule": case.get("rule")}


def candidate_pairs(field: dict[str, Any], rules: list[dict[str, Any]], cases: list[dict[str, Any]],
                    categories: dict[str, tuple[str, ...]] | None, subject: dict[str, Any] | None = None,
                    skip_cases: set[str] | None = None) -> list[dict[str, Any]]:
    """한 칸의 후보 쌍. `subject`(새로 넣을 규칙)가 있으면 그것과 나머지, 없으면 칸 전체(정리 워크플로우).

    - 규칙끼리: 둘 다 값을 내고 범위가 겹치는 쌍 — 값이 같으면 중복 후보, 다르면 충돌 후보.
    - 규칙과 사례: 사례의 답이 규칙의 값과 **다르고**, 그 사례의 상품이 규칙 범위 안인 쌍 — «규칙과 반대로 답한 사례» 후보.
      답이 같은 사례는 규칙을 뒷받침할 뿐이라 보내지 않는다. 이미 그 규칙이 된 사례(rule이 같은 것)도 보내지 않는다."""
    skip_cases = skip_cases or set()
    valued = [rule for rule in rules if rule.get("value")]
    subjects = [subject] if subject else valued
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for a in subjects:
        if not a.get("value"):
            continue
        for b in valued:
            if b is a or b["id"] == a["id"]:
                continue
            pair = tuple(sorted((a["id"], b["id"])))
            if pair in seen or not scopes_overlap(rule_scope(a), rule_scope(b), categories):
                continue
            seen.add(pair)
            pairs.append({"a": rule_item(field, a), "b": rule_item(field, b)})
        for case in cases:
            if case["decisionId"] in skip_cases or case.get("rule") == a["id"]:
                continue
            if str(case["answer"]) == str(a["value"]) or not key_in_scope(case["key"], rule_scope(a), categories):
                continue
            pairs.append({"a": rule_item(field, a), "b": case_item(case)})
    for number, pair in enumerate(pairs, 1):
        pair["pair"] = f"A{number:02d}"
    return pairs


def field_cases(profile: dict[str, Any], task: dict[str, Any], policy: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """칸마다 사례(사람이 답한 AI의 물음). 규칙이 된 사례에는 그 규칙 ID를 붙인다(물음이 같은 규칙)."""
    from gt_review import answered_questions

    links = rule_links(policy)
    found: dict[str, list[dict[str, Any]]] = {}
    for row in answered_questions(profile, task):
        rule = links.get((row["field"], row["decisionId"])) or links.get((row["field"], row["question"]))
        found.setdefault(row["field"], []).append({**row, "rule": rule["id"] if rule else None})
    return found


def rule_links(policy: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """사례 → 그 사례가 된 규칙. 규칙의 `출처:`에 적힌 판정 ID(문답 여럿을 한 규칙으로 올리면 모두 적힌다)와 `물음:`으로 잇는다.
    물음 글자로만 이으면 여러 사례를 한 규칙으로 올렸을 때 첫 사례 하나만 규칙이 되고 나머지는 «규칙 아닌 사례»로 남는다."""
    links: dict[tuple[str, str], dict[str, Any]] = {}
    for rules in policy["rules"].values():
        for rule in rules:
            for decision in re.findall(r"GTD-\d+(?:-[0-9a-f]{4})?", str(rule.get("출처") or "")):
                links[(rule["field"], decision)] = rule
            if rule.get("물음"):
                links.setdefault((rule["field"], rule["물음"]), rule)
    return links


# ---------------------------------------------------------------- 문지기

def audit_dir(profile: dict[str, Any]) -> Path:
    return output_root(profile) / "policy-audit"


def audit_request(profile: dict[str, Any], field: dict[str, Any], subject: dict[str, Any],
                  pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """문지기 요청 — 넣을 규칙과 후보 쌍. 지문은 넣을 규칙과 쌍의 내용으로 정한다(날짜·사람은 빼고) — 미리 보기와 `--yes`가
    같은 지문을 내야 판정 파일이 이어진다. 규칙이나 이웃이 바뀌면 지문이 바뀌어 옛 판정은 쓰이지 않는다."""
    body = {"task": profile["id"], "field": field["id"], "subject": rule_item(field, subject),
            "pairs": [{k: v for k, v in pair.items()} for pair in pairs]}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    folder = audit_dir(profile)
    request = folder / f"{digest}.request.json"
    verdict = folder / f"{digest}.verdict.json"
    info = {"hash": digest, "pairs": pairs, "request": relative_or_absolute(request), "verdictFile": relative_or_absolute(verdict)}
    if pairs:
        folder.mkdir(parents=True, exist_ok=True)
        request.write_text(json.dumps({"schemaVersion": AUDIT_SCHEMA, "hash": digest, "fieldName": field.get("name") or field["id"],
                                       "labelNames": field.get("labelNames") or {}, **body}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return info


def check_audit(audit: dict[str, Any], audit_file: str | None, accept_risk: str | None) -> list[dict[str, Any]]:
    """`--yes` 앞의 관문. 후보가 없으면 지난다. 있으면 같은 지문의 판정 파일이 모든 쌍을 덮어야 하고, 중복·충돌은 사람의 이유가
    있어야 넘는다. 돌려주는 것은 막는 판정(이유와 함께 넘은 것) — 변경 이력에 남긴다."""
    if not audit["pairs"]:
        return []
    from catalog_profile import PROJECT_ROOT

    path = Path(audit_file) if audit_file else Path(audit["verdictFile"])
    path = path if path.is_absolute() else PROJECT_ROOT / path
    if not path.is_file():
        raise PolicyRejected(f"이 규칙은 기존 규칙·사례 {len(audit['pairs'])}쌍과 겹칠 수 있습니다 — 넣기 전에 중복·충돌 검사가 필요합니다. "
                             f"요청 {audit['request']}를 policy-auditor에게 보이고, 답을 {audit['verdictFile']}에 둔 뒤 다시 부르세요.")
    try:
        verdict = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PolicyRejected(f"판정 파일을 읽지 못했습니다: {error}") from error
    if verdict.get("hash") != audit["hash"]:
        raise PolicyRejected("판정 파일이 이 규칙의 검사가 아닙니다(지문이 다릅니다) — 규칙이나 이웃 규칙이 바뀌었으면 다시 검사합니다.")
    by_pair = {str(row.get("pair")): row for row in verdict.get("verdicts") or []}
    missing = [pair["pair"] for pair in audit["pairs"] if pair["pair"] not in by_pair]
    if missing:
        raise PolicyRejected(f"판정 파일이 모든 쌍에 답하지 않았습니다: {', '.join(missing)}")
    odd = [f"{key}={row.get('verdict')}" for key, row in by_pair.items() if row.get("verdict") not in VERDICTS]
    if odd:
        raise PolicyRejected(f"판정 값이 {' · '.join(VERDICTS)} 가운데 하나가 아닙니다: {', '.join(odd)}")
    blocking = [{"pair": pair["pair"], "verdict": by_pair[pair["pair"]]["verdict"], "why": by_pair[pair["pair"]].get("why"),
                 "with": pair["b"]["id"]} for pair in audit["pairs"] if by_pair[pair["pair"]]["verdict"] in BLOCKING]
    if blocking and not (accept_risk or "").strip():
        lines = "; ".join(f"{row['with']}와 {'중복' if row['verdict'] == 'DUPLICATE' else '충돌'} — {row['why']}" for row in blocking)
        raise PolicyRejected(f"넣지 않았습니다 — {lines}. 겹치는 규칙을 고치거나 빼거나(rule edit|retire), 범위를 좁히세요. "
                             "그래도 넣어야 하면 이유를 --accept-risk로 적습니다(변경 이력에 남습니다).")
    return blocking


# ---------------------------------------------------------------- 건강 · 정리 워크플로우

def health(profile: dict[str, Any], task: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """«얼마나 남았어»에 곁들이는 정책의 건강 — 가볍게(사진 색인을 읽지 않는다)."""
    counts = {fid: len(policy["rules"].get(fid, [])) for fid in field_map(task)}
    return {"rulesByField": counts, "fieldsOverLimit": [fid for fid, n in counts.items() if n > RULES_PER_FIELD_LIMIT],
            "casesNotStandard": len(lint_cases(profile)), "ruleLimit": RULES_PER_FIELD_LIMIT}


def review_input(profile: dict[str, Any], task: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """정리 워크플로우의 입력 — 칸마다 규칙·사례·후보 쌍, 그리고 표준이 아닌 사례의 물음."""
    categories = category_map(profile, task)
    cases = field_cases(profile, task, policy)
    lint = {row["decisionId"]: row["problems"] for row in lint_cases(profile)}
    fields = []
    for fid, field in field_map(task).items():
        rules = policy["rules"].get(fid, [])
        mine = cases.get(fid, [])
        fields.append({
            "id": fid, "name": field.get("name") or fid, "labelNames": field.get("labelNames") or {},
            "rules": [rule_item(field, rule) for rule in rules],
            "cases": [{**case_item(case), "problems": lint.get(case["decisionId"], [])} for case in mine],
            "pairs": candidate_pairs(field, rules, mine, categories),
            "overLimit": len(rules) > RULES_PER_FIELD_LIMIT,
        })
    return {"schemaVersion": "gt-policy-review-input-v1", "task": profile["id"], "categoriesKnown": categories is not None,
            "ruleLimit": RULES_PER_FIELD_LIMIT, "fields": fields}


REVIEW_SCHEMA = "gt-policy-review-v1"


def _q(text: Any) -> str:
    return '"' + str(text or "").replace('"', "'") + '"'


def finish_review(profile: dict[str, Any], task: dict[str, Any], source: Path) -> dict[str, Any]:
    """정리 워크플로우(`policy-review.js`)의 반환값을 검사해 제안 목록으로 쓴다. 정책도 원장도 고치지 않는다 —
    제안마다 사람이 부를 명령을 붙여 둘 뿐이다. 입력에 없는 규칙·사례·쌍을 가리키는 제안은 버리고 그 사실을 적는다
    (에이전트가 없는 ID를 지어내면 사람이 없는 규칙을 고치려 들게 된다)."""
    raw = json.loads(source.read_text(encoding="utf-8"))
    value = raw.get("result") if isinstance(raw, dict) and isinstance(raw.get("result"), dict) else raw
    if not isinstance(value, dict) or value.get("schemaVersion") != REVIEW_SCHEMA:
        raise PolicyRejected(f"{source}: 워크플로우 policy-review의 반환값({REVIEW_SCHEMA})이 아닙니다.")
    if value.get("task") != profile["id"]:
        raise PolicyRejected(f"다른 과제의 정리 결과입니다: {value.get('task')} != {profile['id']}")
    root = output_root(profile) / "policy-review"
    given = json.loads((root / "input.json").read_text(encoding="utf-8"))
    fields = {f["id"]: f for f in given["fields"]}
    labels = {fid: [str(c) for c in spec["labels"]] for fid, spec in field_map(task).items()}
    name = profile["id"]
    dropped: list[str] = []
    drafts = []
    for draft in value.get("drafts") or []:
        case = next((c for f in given["fields"] for c in f["cases"] if c["id"] == draft.get("decisionId")), None)
        problems = question_problems(draft.get("question") or "")
        if case is None:
            dropped.append(f"물음 초안 {draft.get('decisionId')} — 없는 사례")
            continue
        drafts.append({**draft, "before": case["question"], "problems": problems,
                       "command": None if problems else
                       f"qa --task {name} --rewrite {draft['decisionId']} --question {_q(draft['question'])} "
                       f"--here {_q(draft.get('here') or case['here'])} --reviewer <이름> --yes"})
    audits = []
    for audit in value.get("audits") or []:
        field = fields.get(audit.get("field"))
        if field is None:
            dropped.append(f"칸 {audit.get('field')} — 입력에 없는 칸")
            continue
        rule_ids = {r["id"] for r in field["rules"]}
        case_ids = {c["id"] for c in field["cases"]}
        pair_ids = {p["pair"] for p in field["pairs"]}
        verdicts = []
        for row in audit.get("verdicts") or []:
            if row.get("pair") not in pair_ids or row.get("verdict") not in VERDICTS:
                dropped.append(f"{field['id']} 판정 {row.get('pair')}={row.get('verdict')} — 없는 쌍이거나 모르는 판정")
                continue
            verdicts.append(row)
        proposals = []
        for kind in ("merge", "promote", "retire", "definition"):
            for row in audit.get(kind) or []:
                rules = [str(r) for r in row.get("rules") or []]
                cases = [str(c) for c in row.get("cases") or []]
                value_code = row.get("value")
                if any(r not in rule_ids for r in rules) or any(c not in case_ids for c in cases):
                    dropped.append(f"{field['id']} {kind} — 없는 규칙·사례를 가리킴({', '.join(rules + cases)})")
                    continue
                if value_code and any(part not in labels[field["id"]] for part in str(value_code).split("|")):
                    dropped.append(f"{field['id']} {kind} — 허용값이 아닌 값 {value_code}")
                    continue
                proposals.append({"kind": kind, **row, "commands": _commands(name, field["id"], kind, row)})
        audits.append({"field": field["id"], "fieldName": field["name"], "verdicts": verdicts,
                       "blocking": [v for v in verdicts if v["verdict"] in BLOCKING], "proposals": proposals})
    out = {"schemaVersion": "gt-policy-proposals-v1", "task": profile["id"],
           "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "drafts": drafts, "audits": audits, "dropped": dropped,
           "counts": {"drafts": len(drafts), "blocking": sum(len(a["blocking"]) for a in audits),
                      "proposals": sum(len(a["proposals"]) for a in audits), "dropped": len(dropped)}}
    root.mkdir(parents=True, exist_ok=True)
    (root / "proposals.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    out["file"] = relative_or_absolute(root / "proposals.json")
    return out


def _commands(task_id: str, field_id: str, kind: str, row: dict[str, Any]) -> list[str]:
    """제안을 사람이 부를 명령으로. 에이전트는 기록하지 않는다 — 명령은 사람이 확인하고 `--yes`를 붙여 부른다."""
    scope = f" --scope {_q(row['scope'])}" if row.get("scope") else ""
    value = f" --value {row['value']}" if row.get("value") else ""
    cond = f" --condition {_q(row['condition'])}" if row.get("condition") else ""
    rules = [str(r) for r in row.get("rules") or []]
    if kind == "merge" and rules:
        keep, rest = rules[0], rules[1:]
        return ([f"rule edit --task {task_id} --field {field_id} --id {keep} --text {_q(row.get('text'))}{value}{cond}{scope} --reviewer <이름>"]
                + [f"rule retire --task {task_id} --field {field_id} --id {r} --reason {_q(f'{keep}(고친 뒤 새 ID)로 합침')} --reviewer <이름>"
                   for r in rest])
    if kind == "promote":
        adds = " ".join(f"--add {c}" for c in row.get("cases") or [])
        return [f"qa --task {task_id} {adds} --rule {_q(row.get('text'))}{cond}{scope} --reviewer <이름>"]
    if kind == "retire":
        return [f"rule retire --task {task_id} --field {field_id} --id {r} --reason {_q(row.get('why'))} --reviewer <이름>" for r in rules]
    return []  # definition — 정의 본문 문장은 사람이 정의 문서를 직접 고친다(값 목록 고치기와 같은 절차)
