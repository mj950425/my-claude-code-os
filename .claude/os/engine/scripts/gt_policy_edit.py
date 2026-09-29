#!/usr/bin/env python3
"""정책 화면에서 고치는 문 — 허용값 · 관찰 항목 · 값 규칙. 과제를 모른다.

정책(정의 문서)은 모든 AI 판독의 기준이라, 쓰는 길은 하나로 모은다. 화면의 버튼(`POST /gt-policy`)과 CLI(`gt_review.py value|observe`,
값 규칙은 `rule --when`)가 모두 `edit_policy`를 지난다 — 문이 둘이면 검사가 둘이 되고 곧 어긋난다.

화면으로 받는 것은 **모양이 정해져 있고 코드가 옳고 그름을 확정할 수 있는 것**뿐이다.
- 허용값 더하기·이름과 설명 고치기·빼기 — 줄 모양은 로더가 확정한다. 빼기 전에는 그 값을 가진 GT 칸·판정 수를 미리 보기에 싣는다.
- 관찰 항목 더하기·뜻 고치기 — 새 항목은 그 항목을 쓰는 값 규칙 한 줄과 함께 들어간다(쓰이지 않는 관찰은 로더가 멈춘다).
- 값 규칙 더하기·빼기 — 가려지는 줄(중복·충돌)은 모든 관찰 조합을 대입하는 코드가 확정한다(`gt_review.rule_change`).
문장 규칙(`### 규칙`)은 받지 않는다 — 겹침·충돌은 에이전트(policy-auditor)가 가르고, 문답을 «다른 상품에도 통하는 한 문장»으로
다듬는 일은 Claude가 한다. 화면은 그 요청 문장을 복사하게 한다.

모든 변경은 미리 보기(`confirm=False`)와 확정(`confirm=True`) 두 번이다. 확정은 화면이 본 정책의 지문(`expected_stamp`)과 지금 정책이
같을 때만 쓴다 — 그사이 다른 사람이 고쳤으면 덮지 않는다. 쓰고 나면 정책 전체를 다시 읽어, 로더가 멈추는 모양이면 되돌린다(`_commit_policy`).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gt_task import TaskError, _VALUES_HEAD, definition_policy, field_map, load_task

EDIT_OPS = ("value-add", "value-edit", "value-remove", "observe-add", "observe-edit", "derive-add", "derive-retire")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_BAD_NAME = re.compile(r"[:：*_\[\]`]|--|–| - ")


class PolicyEditRejected(TaskError):
    """사람에게 그대로 보일 거절 사유. TaskError라서 CLI는 «멈춤:»으로, 서버는 400으로 보인다."""


def policy_stamp(path: Path) -> str:
    """정책 문서의 지문 — 화면이 본 정책과 지금 정책이 같은지 가른다. version 줄이 없는 문서도 가를 수 있게 내용으로 낸다."""
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def policy_version(path: Path) -> int | None:
    match = re.search(r"^version:\s*(\d+)\s*$", path.read_text(encoding="utf-8"), re.M)
    return int(match.group(1)) if match else None


def _clean(text: Any, what: str, required: bool = True, limit: int = 400) -> str:
    # 모양을 한 가지로 맞추고(전각 글자 → 보통 글자) 보이지 않는 글자(서식 문자: 너비 없는 공백·소프트 하이픈 등)는 뺀다 —
    # 같아 보이는 이름이 겹침 검사를, 전각으로 쓴 값 코드가 새는 검사를 지나지 않게.
    import unicodedata

    value = unicodedata.normalize("NFKC", str(text or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Cf")
    value = re.sub(r"\s+", " ", value).strip()
    if required and not value:
        raise PolicyEditRejected(f"«{what}» 칸을 채워 주세요.")
    if len(value) > limit:
        raise PolicyEditRejected(f"«{what}» 칸이 너무 깁니다({limit}자 이내).")
    if "`" in value:
        raise PolicyEditRejected(f"{what}에 백틱(`)은 쓰지 않습니다 — 정책 문서의 모양을 깨뜨립니다.")
    return value


def _name(text: Any, what: str) -> str:
    value = _clean(text, what, limit=30)
    if _BAD_NAME.search(value) or "—" in value:
        raise PolicyEditRejected(f"{what}에 쓸 수 없는 글자가 있습니다(콜론·밑줄·별표·대괄호·줄표) — 짧은 이름만 적어 주세요.")
    return value


def _field_span(text: str, field_id: str) -> tuple[int, int]:
    from gt_review import _section_span

    span = _section_span(text, field_id)
    if span is None:
        raise PolicyEditRejected(f"정의 문서에 `## {field_id}` 절이 없습니다.")
    return span


def _values_block(body: str) -> tuple[int, int]:
    """칸 절 안에서 허용값 목록 줄들이 차지하는 자리(시작, 끝). 들여쓴 이어짐 줄까지 포함한다."""
    head = _VALUES_HEAD.search(body)
    if not head:
        raise PolicyEditRejected("이 칸에는 `### 허용값` 목록이 없습니다.")
    lines = body[head.end():].split("\n")
    offset = head.end()
    start = end = None
    pos = offset
    for line in lines:
        if line.startswith("- `"):
            start = pos if start is None else start
            end = pos + len(line)
        elif start is not None and line.startswith((" ", "\t")) and line.strip():
            end = pos + len(line)
        elif start is not None:
            break
        pos += len(line) + 1
    if start is None:
        raise PolicyEditRejected("이 칸의 `### 허용값` 목록이 비어 있습니다.")
    return start, end


def _value_entries(block: str) -> list[tuple[str, str]]:
    """목록 덩어리를 값마다 (코드, 그 값의 줄들)로."""
    entries: list[tuple[str, str]] = []
    for line in block.split("\n"):
        match = re.match(r"^- `([^`\s]+)`", line)
        if match:
            entries.append((match.group(1), line))
        elif entries:
            entries[-1] = (entries[-1][0], entries[-1][1] + "\n" + line)
    return entries


def _value_line(code: str, name: str, desc: str) -> str:
    return f"- `{code}` {name} — {desc}" if name else f"- `{code}` — {desc}"


def _parse_value(entry: str) -> tuple[str, str]:
    first, *rest = entry.split("\n")
    match = re.match(r"^- `[^`]+`(?: ([^`—]*?))? — (.*)$", first)
    name, desc = ((match.group(1) or "").strip(), match.group(2).strip()) if match else ("", "")
    return name, " ".join([desc, *(line.strip() for line in rest)]).strip()


def _replace_observe_line(body: str, obs_id: str, line: str) -> str:
    """관찰 한 항목(이어짐 줄 포함)을 새 한 줄로."""
    from gt_task import OBSERVE_HEAD, _subsection

    _, start, end = _subsection(body, OBSERVE_HEAD)
    part = body[start:end].split("\n")
    out, skipping = [], False
    for raw in part:
        if raw.startswith(f"- `{obs_id}` "):
            out.append(line)
            skipping = True
            continue
        if skipping and raw.startswith((" ", "\t")) and raw.strip():
            continue
        skipping = False
        out.append(raw)
    return body[:start] + "\n".join(out) + body[end:]


def _append_observe(body: str, line: str) -> str:
    from gt_task import OBSERVE_HEAD, _subsection

    _, start, end = _subsection(body, OBSERVE_HEAD)
    part = body[start:end].rstrip("\n")
    return body[:start] + part + "\n" + line + "\n\n" + body[end:].lstrip("\n")


def _resolve_field(task: dict[str, Any], field: str) -> dict[str, Any]:
    fields = field_map(task)
    if field in fields:
        return fields[field]
    by_name = {(spec.get("name") or fid): fid for fid, spec in fields.items()}
    if field in by_name:
        return fields[by_name[field]]
    raise PolicyEditRejected(f"이 과제에 «{field}» 칸이 없습니다 — {', '.join(spec.get('name') or fid for fid, spec in fields.items())}")


def edit_policy(profile: dict[str, Any], op: str, field: str, reviewer: str, confirm: bool,
                expected_stamp: str | None = None, **params: Any) -> dict[str, Any]:
    """정책 한 곳을 고친다. `confirm=False`면 무엇이 바뀌는지만 돌려준다(아무것도 쓰지 않는다).

    넣기는 과제 잠금을 쥔 채 **잠금 안에서** 화면이 본 지문과 지금 정책을 대조하고, 같은 잠금 안에서 읽고 고치고 쓴다 —
    두 탭이 같은 지문으로 동시에 눌러도 하나만 들어가고, 다른 하나는 «그사이 바뀌었습니다»를 받는다."""
    from contextlib import nullcontext

    from gt_decisions import locked
    from gt_review import _definitions_path

    with locked(profile) if confirm else nullcontext():
        if confirm and expected_stamp is not None and expected_stamp != policy_stamp(_definitions_path(profile)):
            raise PolicyEditRejected("그사이 정책이 바뀌었습니다(다른 사람이나 Claude가 고쳤습니다) — 새로고침한 뒤 다시 해 주세요.")
        return _edit(profile, op, field, reviewer, confirm, params)


def _edit(profile: dict[str, Any], op: str, field: str, reviewer: str, confirm: bool, params: dict[str, Any]) -> dict[str, Any]:
    from gt_review import DecisionRejected, _commit_policy, _definitions_path, _insert_derive, require_legacy_policy_editor, rule_change, validate_policy_text

    if op not in EDIT_OPS:
        raise PolicyEditRejected(f"고칠 수 있는 것은 {' · '.join(EDIT_OPS)} 가운데 하나입니다.")
    reviewer = _clean(reviewer, "고치는 사람의 이름", limit=40)
    if ((profile.get("gtTask") or {}).get("definitionsRoot") or "project") != "project":
        raise PolicyEditRejected("정의 문서가 이 저장소 밖에 있습니다 — 그 저장소에서 고쳐 주세요.")
    task = load_task(profile)
    spec = _resolve_field(task, field)
    field_id, name = spec["id"], spec.get("name") or spec["id"]
    path = _definitions_path(profile)
    try:
        require_legacy_policy_editor(path)
    except DecisionRejected as rejected:
        raise PolicyEditRejected(str(rejected)) from rejected
    version = policy_version(path)
    labels = {f["id"]: [str(c) for c in f["labels"]] for f in task["fields"]}
    many = {f["id"]: f.get("cardinality") == "many" for f in task["fields"]}
    policy = definition_policy(path, labels, many)
    table = (policy.get("observed") or {}).get(field_id)

    if op in ("derive-add", "derive-retire"):
        if not table:
            raise PolicyEditRejected(f"{name}은 관찰 칸이 아닙니다 — 값 규칙은 `### 관찰`·`### 값 규칙`이 있는 칸에만 있습니다.")
        if op == "derive-add" and not str(params.get("when") or "").strip():
            raise PolicyEditRejected("관찰을 하나 이상 «예»나 «아니오»로 골라 주세요 — 그 조합이 이 줄의 조건입니다.")
        if op == "derive-add" and not str(params.get("value") or "").strip():
            raise PolicyEditRejected("이 조합일 때의 값을 골라 주세요.")
        try:
            if op == "derive-add":
                plan = rule_change(profile, "add", field_id, reviewer, confirm, value=str(params.get("value") or "") or None,
                                   when=str(params.get("when") or ""), before=str(params.get("before") or "") or None)
            else:
                plan = rule_change(profile, "retire", field_id, reviewer, confirm, rule_id=str(params.get("id") or ""),
                                   reason=_clean(params.get("reason"), "빼는 이유", limit=200))
        except (DecisionRejected, TaskError) as rejected:
            raise PolicyEditRejected(str(rejected)) from rejected
        return {**plan, "op": op, "version": policy_version(path), "stamp": policy_stamp(path),
                "readable": _readable(plan, {item["id"]: item["name"] for item in table["items"]}, spec)}

    original = path.read_text(encoding="utf-8")
    today = datetime.now(timezone.utc).date().isoformat()
    new_version = version + 1 if version is not None else None
    stamp = f"- {today} · " + (f"v{new_version} · " if new_version else "")
    start, end = _field_span(original, field_id)
    body = original[start:end]
    plan: dict[str, Any] = {"op": op, "field": field_id, "definitions": path.name, "applied": False,
                            "warnings": [], "version": version}
    text = original

    if op.startswith("value-"):
        v_start, v_end = _values_block(body)
        entries = _value_entries(body[v_start:v_end])
        codes = [code for code, _ in entries]
        code = str(params.get("code") or "").strip()
        if op == "value-add":
            if not _CODE.match(code):
                raise PolicyEditRejected("값 코드는 영문 대문자로 시작하고 대문자·숫자·밑줄만 씁니다(예: NEW_VALUE).")
            if code in codes:
                raise PolicyEditRejected(f"{name}에 값 {code}이(가) 이미 있습니다.")
            line = _value_line(code, _name(params.get("name"), "값 이름"), _clean(params.get("desc"), "값 설명"))
            new_body = body[:v_end] + "\n" + line + body[v_end:]
            plan.update(before=None, after=line)
            history = stamp + f"{name} 허용값 «{code}» 추가 — {reviewer}"
            if table:
                plan["warnings"].append("관찰 칸이라 이 값을 내는 값 규칙이 아직 없습니다 — 값 규칙 줄을 더해야 AI가 이 값을 냅니다. 사람은 곧바로 고를 수 있습니다.")
            if (profile.get("gtTask") or {}).get("gt", {}).get("upstream") or re.search(r"^seededFrom:", original, re.M):
                plan["warnings"].append("이 정책은 운영 코드·원본에서 옮겨 왔습니다 — 운영에도 이 값이 있는지 확인해 주세요. 운영이 낼 수 없는 값이 GT에 생깁니다.")
        else:
            if code not in codes:
                raise PolicyEditRejected(f"{name}에 값 {code}이(가) 없습니다 — {', '.join(codes)}")
            old = dict(entries)[code]
            if op == "value-edit":
                old_name, old_desc = _parse_value(old)
                new_name = _name(params.get("name"), "값 이름") if str(params.get("name") or "").strip() else old_name
                new_desc = _clean(params.get("desc"), "값 설명") if str(params.get("desc") or "").strip() else old_desc
                if (new_name, new_desc) == (old_name, old_desc):
                    raise PolicyEditRejected("바뀌는 것이 없습니다 — 이름이나 설명을 바꿔 주세요.")
                line = _value_line(code, new_name, new_desc)
                block = "\n".join(line if c == code else lines for c, lines in entries)
                plan.update(before=old, after=line)
                history = stamp + f"{name} 허용값 «{code}» 이름·설명 고침 — {reviewer}"
            else:
                if len(codes) <= 2:
                    raise PolicyEditRejected(f"{name}의 값이 둘뿐이라 뺄 수 없습니다 — 고를 것이 하나면 칸이 뜻이 없습니다.")
                if table and table["else"] == code:
                    raise PolicyEditRejected("이 값은 어느 값 규칙에도 안 걸린 사진의 값(«그 밖»)이라 뺄 수 없습니다 — "
                                             "«그 밖» 값을 바꾸는 일은 Claude에게 부탁해 주세요.")
                from gt_derive import describe

                obs_names = {item["id"]: item["name"] for item in (table or {}).get("items", [])}
                givers = [f"{rule['id']}({describe(rule['ast'], obs_names)})" for rule in (table or {}).get("rules", [])
                          if code in str(rule["value"]).split("|")]
                if givers:
                    raise PolicyEditRejected(f"값 규칙 {', '.join(givers)}이(가) 이 값을 냅니다 — 아래 «값 규칙 빼기»에서 그 줄을 먼저 빼 주세요.")
                texts = [rule["id"] for rule in policy["rules"].get(field_id, []) if code in str(rule.get("value") or "").split("|")]
                if texts:
                    raise PolicyEditRejected(f"문장 규칙 {', '.join(texts)}이(가) 이 값을 가리킵니다 — Claude에게 «{name} {texts[0]} 빼줘»라고 부탁한 뒤 빼 주세요.")
                from gt_review import values_of

                impact = next((row for row in values_of(profile, field_id)["fields"][0]["values"] if row["code"] == code), {})
                plan["impact"] = {k: impact.get(k) for k in ("gtCells", "correctedTo", "keptAs", "usedInProfile")}
                if impact.get("usedInProfile"):
                    raise PolicyEditRejected(f"프로필 설정이 이 값을 씁니다({', '.join(impact['usedInProfile'])}) — Claude에게 «이 값 빼줘»라고 말해 주세요.")
                if impact.get("gtCells") or impact.get("correctedTo") or impact.get("keptAs"):
                    plan["warnings"].append(f"이 값을 가진 GT 칸 {impact.get('gtCells', 0)}개와 이 값으로 고치거나 유지한 판정 "
                                            f"{(impact.get('correctedTo') or 0) + (impact.get('keptAs') or 0)}개가 «정책 밖»이 되어 다음 화면에 다시 올라옵니다.")
                block = "\n".join(lines for c, lines in entries if c != code)
                plan.update(before=old, after=None)
                history = stamp + f"{name} 허용값 «{code}» 뺌 — {reviewer}"
            new_body = body[:v_start] + block + body[v_end:] if op != "value-add" else new_body
        text = original[:start] + new_body + original[end:]

    else:  # observe-*
        if not table:
            raise PolicyEditRejected(f"{name}은 관찰 칸이 아닙니다 — 관찰 칸으로 바꾸는 일(두 절을 새로 쓰기)은 Claude에게 부탁해 주세요.")
        obs_id = str(params.get("id") or "").strip()
        if not _CODE.match(obs_id):
            raise PolicyEditRejected("관찰 ID는 영문 대문자로 시작하고 대문자·숫자·밑줄만 씁니다(예: NEW_MARK).")
        new_name = _name(params.get("name"), "관찰 이름")
        new_desc = _clean(params.get("desc"), "관찰 뜻(무엇이 «예»인가)")
        line = f"- `{obs_id}` {new_name} — {new_desc}"
        # 관찰의 이름·뜻은 판독자에게 간다 — 값 코드가 있으면 값을 알게 된다. 넣을 때 로더도 막지만, 미리 보기에서 먼저 말한다.
        leaked = [code for code in labels[field_id]
                  if re.search(rf"(?<![A-Za-z0-9_]){re.escape(code)}(?![A-Za-z0-9_])", f"{new_name} {new_desc}", re.I)]
        if leaked:
            raise PolicyEditRejected(f"관찰의 이름·뜻에 값 코드가 있습니다: {', '.join(leaked)} — AI는 값을 모른 채 사실에만 답합니다. 사진에 보이는 사실로 적어 주세요.")
        # 같은 ID를 쓰는 다른 칸 — 같은 사실이라 뜻이 같아야 한다(로더가 확인한다). 고치기는 모든 칸을 함께 고친다.
        sharing = [fid for fid, other in (policy.get("observed") or {}).items()
                   if any(item["id"] == obs_id for item in other["items"])]
        field_names = {f["id"]: f.get("name") or f["id"] for f in task["fields"]}
        if op == "observe-add":
            if obs_id in table["observe"]:
                raise PolicyEditRejected(f"{name}에 관찰 {obs_id}이(가) 이미 있습니다 — 뜻을 고치려면 «관찰 뜻 고치기»를 쓰세요.")
            if sharing:
                other = next(item for item in policy["observed"][sharing[0]]["items"] if item["id"] == obs_id)
                if (other["name"], other["desc"]) != (new_name, new_desc):
                    raise PolicyEditRejected(f"{obs_id}은(는) 다른 칸({field_names[sharing[0]]})에 이미 있는 관찰입니다 — 같은 사실이면 이름과 뜻을 그대로 "
                                             f"«{other['name']} — {other['desc']}»로, 다른 사실이면 ID를 달리해 주세요.")
            when = str(params.get("when") or "").strip()
            value = str(params.get("value") or "").strip()
            if not when or not value:
                raise PolicyEditRejected("새 관찰은 그 관찰을 쓰는 값 규칙 한 줄과 함께 넣습니다 — 조합(식)과 값을 골라 주세요"
                                         "(«새 항목»을 포함해 관찰을 예나 아니오로 고릅니다).")
            from gt_derive import DeriveError, parse, prefixes, shadowed
            from gt_derive import names as expr_names

            try:
                ast = parse(when)
            except DeriveError as error:
                raise PolicyEditRejected(f"조합을 읽지 못했습니다: {error}") from error
            if obs_id not in expr_names(ast):
                raise PolicyEditRejected(f"함께 넣는 값 규칙에서 «새 항목»을 예나 아니오로 골라 주세요 — 새 관찰을 쓰지 않는 줄은 넣을 까닭이 없습니다.")
            empty = sorted(prefix for prefix in prefixes(ast) if not any(item.startswith(prefix) for item in [*table["observe"], obs_id]))
            if empty:
                raise PolicyEditRejected(f"COUNT가 셀 관찰이 없습니다: {', '.join(p + '*' for p in empty)}")
            unknown = sorted(expr_names(ast) - set(table["observe"]) - {obs_id})
            if unknown:
                raise PolicyEditRejected(f"식에 {name}의 관찰에 없는 항목이 있습니다: {', '.join(unknown)}")
            if value not in labels[field_id]:
                raise PolicyEditRejected(f"«{value}»은 {name}의 허용값이 아닙니다 — {', '.join(labels[field_id])}")
            from gt_task import next_rule_id

            rule_id = next_rule_id(policy, field_id, "V")
            ids = [rule["id"] for rule in table["rules"]]
            before = str(params.get("before") or "").strip() or None
            if before and before != "end" and before not in ids:
                raise PolicyEditRejected(f"«{before}» 앞에 넣으려 했지만 그런 값 규칙이 없습니다 — {', '.join(ids)} 가운데 하나나 «end».")
            at = len(ids) if before == "end" else ids.index(before) if before else 0
            new_rules = [*table["rules"][:at], {"id": rule_id, "ast": ast, "value": value, "expr": when}, *table["rules"][at:]]
            trial = {"observe": [*table["observe"], obs_id], "else": table["else"], "rules": new_rules}
            obs_names = {**{item["id"]: item["name"] for item in table["items"]}, obs_id: new_name}
            hidden = shadowed(trial)
            if hidden:
                from gt_review import shadow_sentence

                raise PolicyEditRejected(shadow_sentence(trial, hidden, rule_id, obs_names, spec))
            rule_lines = [f"- `{rule_id}` `{when}` → `{value}`",
                          f"  - 출처: 직접 작성 · {today} · {reviewer} · 관찰 {obs_id} 추가"]
            text = original[:start] + _append_observe(body, line) + original[end:]
            try:
                text = _insert_derive(text, field_id, rule_lines, new_rules[at + 1]["id"] if at + 1 < len(new_rules) else None)
            except DecisionRejected as rejected:
                raise PolicyEditRejected(str(rejected)) from rejected
            from gt_derive import table_changes

            plan["readable"] = _readable({"rule": rule_lines}, obs_names, spec)
            plan["names"] = obs_names
            plan["warnings"].append("새 관찰은 다음 판독부터 AI가 답합니다 — 지난 화면의 AI 값은 그대로입니다.")
            from gt_review import change_warnings

            changes = table_changes({**table, "observe": trial["observe"]}, trial)
            from gt_review import _rule_words

            plan["warnings"].extend(change_warnings(changes, True, {r["id"]: _rule_words(r, obs_names, spec) for r in table["rules"]}))
            plan.update(after=line, rule=rule_lines, changes=changes,
                        table=[f"{r['id']} {r['expr']} → {r['value']}" for r in new_rules] + [f"그 밖 → {table['else']}"])
            history = stamp + f"{name} 관찰 «{obs_id}»와 값 규칙 {rule_id} 추가 — {reviewer}"
        else:
            if obs_id not in table["observe"]:
                raise PolicyEditRejected(f"{name}에 관찰 `{obs_id}`이 없습니다 — {', '.join(table['observe'])}")
            old = next(item for item in table["items"] if item["id"] == obs_id)
            if (old["name"], old["desc"]) == (new_name, new_desc):
                raise PolicyEditRejected("바뀌는 것이 없습니다 — 이름이나 뜻을 바꿔 주세요.")
            for fid in sharing:
                s, e = _field_span(text, fid)
                text = text[:s] + _replace_observe_line(text[s:e], obs_id, line) + text[e:]
            others = [field_names[f] for f in sharing if f != field_id]
            plan.update(before=f"- `{obs_id}` {old['name']} — {old['desc']}", after=line, sharedWith=others)
            if others:
                plan["warnings"].append(f"이 관찰은 {', '.join('«' + n + '»' for n in others)}에도 쓰여 그 칸들도 함께 바뀝니다.")
            history = stamp + f"관찰 «{obs_id}» 뜻 고침({', '.join(field_names[f] for f in sharing)}) — {reviewer}"
            plan["warnings"].append("관찰의 뜻이 바뀌면 이 관찰로 답한 지난 사례도 옛 뜻 위의 답입니다 — 다음 판독부터 새 뜻으로 읽습니다.")

    plan["history"] = history
    if not confirm:
        # 미리 보기도 넣을 때와 같은 검사(로더 전체)를 지난다 — «미리 보기는 됐는데 넣기가 막힘»이 없게.
        from gt_review import _append_to_block

        try:
            validate_policy_text(profile, path, _append_to_block(text, "변경 이력", None, [history]))
        except DecisionRejected as rejected:
            raise PolicyEditRejected(str(rejected)) from rejected
        return plan
    try:
        _commit_policy(profile, path, original, text, history, new_version, today)
    except TaskError as error:
        raise PolicyEditRejected(f"넣지 않았습니다 — {str(error).split(': ', 1)[-1]}") from error
    plan["applied"] = True
    plan["version"] = policy_version(path)
    plan["stamp"] = policy_stamp(path)
    return plan


def _readable(plan: dict[str, Any], names: dict[str, str], spec: dict[str, Any]) -> str | None:
    """미리 보기의 값 규칙 한 줄을 사람이 읽는 말로 — «광» · «넓은 반사» 아님 → 강함."""
    from gt_derive import describe, parse

    line = next(iter(plan.get("rule") or plan.get("archive") or []), "")
    match = re.match(r"^- `(?:[^`/]+/)?(V\d+)` `([^`]+)` → `([^`]+)`", line)
    if not match:
        return None
    label = (spec.get("labelNames") or {}).get(match.group(3), match.group(3))
    return f"{match.group(1)} {describe(parse(match.group(2)), names)} → {label}"


def refresh_pages(profile: dict[str, Any]) -> list[Path]:
    """정책을 고친 뒤 화면을 다시 쓴다 — 정책·골든셋 두 장(CLI `pages`와 같은 함수), 그리고 배치가 있으면 검수 화면
    (CLI `render`와 같은 함수). 값 목록이 바뀌면 검수 화면의 «다른 값…»도 바뀌어야 한다."""
    from gt_next import lock_dir, running
    from gt_review import render_current, write_pages

    written = write_pages(profile)
    if running(lock_dir(profile)) is not None:
        return written  # AI가 새 후보를 읽는 중이면 검수 화면은 러너가 새 정책으로 다시 그린다 — 여기서 겹쳐 그리지 않는다
    try:
        # 물음·눈가림 기록(GT 쪽 파일)은 남기지 않는다 — 서버는 판정 원장·반영·정책 말고는 쓰지 않는다.
        written += render_current(profile, record=False) or []
    except TaskError:
        pass  # 배치가 없거나 다른 배치를 준비 중이면 검수 화면은 그대로 둔다(다음 준비가 새 정책으로 그린다)
    return written
