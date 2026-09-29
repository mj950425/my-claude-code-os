#!/usr/bin/env python3
"""Prompt-ready natural-language rules stored in an attribute definition.

The v2 `## 목적` / `## 허용값` / `## 규칙` document is the canonical policy
for inference consumers. Legacy definitions still use `### 판정 규칙`, kept
separate from `### 관찰` / `### 값 규칙` used by blind fact collection.
Audience adapters decide which sections they are allowed to receive.
"""
from __future__ import annotations

import re
import hashlib
from pathlib import Path
from typing import Any
from policy_document import load_document


class PolicyPromptError(ValueError):
    pass


_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)
_RULE_SECTION = re.compile(r"^###\s+판정 규칙\s*$", re.M)
_RULE = re.compile(r"^- `([A-Z][A-Z0-9_-]*)` (.+)$")
_SUB = re.compile(r"^\s{2,}- ([^:：]+):\s*(.*)$")
_VALUE = re.compile(r"^- `([^`\s]+)`\s+(.+?)\s+—\s+(.+)$")
_KEYS = {"내용", "우선순위", "판례", "출처", "검산"}


def _field_body(text: str, field: str) -> str:
    heads = list(_SECTION.finditer(text))
    matches = [(n, head) for n, head in enumerate(heads) if head.group(1).strip() == field]
    if len(matches) > 1:
        raise PolicyPromptError(f"정의 문서에 `## {field}` 절이 여러 개 있습니다")
    if matches:
        n, head = matches[0]
        return text[head.end():(heads[n + 1].start() if n + 1 < len(heads) else len(text))]
    raise PolicyPromptError(f"정의 문서에 `## {field}` 절이 없습니다")


def decision_rules(path: Path, field: str) -> list[dict[str, Any]]:
    """Load and validate ordered, prompt-ready decision rules for one field."""
    document = load_document(path, field)
    if document is not None:
        return document["rules"]
    body = _field_body(path.read_text(encoding="utf-8"), field)
    heads = list(_RULE_SECTION.finditer(body))
    if not heads:
        return []
    if len(heads) != 1:
        raise PolicyPromptError(f"`## {field}`에 `### 판정 규칙`이 여러 개 있습니다")
    start = heads[0].end()
    next_head = re.search(r"^###\s+", body[start:], re.M)
    block = body[start:start + next_head.start()] if next_head else body[start:]
    rules: list[dict[str, Any]] = []
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        sub = _SUB.match(line)
        if sub:
            if not rules:
                raise PolicyPromptError("첫 판정 규칙보다 앞에 속성 줄이 있습니다")
            key, value = sub.group(1).strip(), sub.group(2).strip()
            if key not in _KEYS:
                raise PolicyPromptError(f"알 수 없는 판정 규칙 속성: {key}")
            if key in rules[-1]:
                raise PolicyPromptError(f"{rules[-1]['id']}의 `{key}`가 중복됐습니다")
            rules[-1][key] = value
            continue
        continuation = re.match(r"^\s{4,}(?!- )(.+?)\s*$", line)
        if continuation and rules and "내용" in rules[-1]:
            # Markdown indented continuation keeps long natural rules readable at source.
            rules[-1]["내용"] += " " + continuation.group(1).strip()
            continue
        match = _RULE.match(line)
        if not match:
            raise PolicyPromptError(f"판정 규칙 줄 모양이 잘못됐습니다: {line.strip()}")
        rules.append({"id": match.group(1), "title": match.group(2).strip()})
    ids = [rule["id"] for rule in rules]
    if len(ids) != len(set(ids)):
        raise PolicyPromptError("판정 규칙 ID가 중복됐습니다")
    priorities: list[int] = []
    for rule in rules:
        for required in ("내용", "우선순위", "출처"):
            if not rule.get(required):
                raise PolicyPromptError(f"{rule['id']}에 `{required}`가 없습니다")
        if not re.fullmatch(r"[1-9][0-9]*", rule["우선순위"]):
            raise PolicyPromptError(f"{rule['id']}의 우선순위는 1 이상의 정수여야 합니다")
        priorities.append(int(rule["우선순위"]))
    if len(priorities) != len(set(priorities)):
        raise PolicyPromptError("판정 규칙 우선순위가 중복됐습니다")
    if any(rule.get("검산") for rule in rules):
        available = set(re.findall(r"^- `(V\d+)`", body, re.M))
        for rule in rules:
            refs = [item.strip() for item in (rule.get("검산") or "").split(",") if item.strip()]
            if len(refs) != len(set(refs)) or any(ref not in available for ref in refs):
                raise PolicyPromptError(f"{rule['id']}의 검산은 중복 없이 정의된 값 규칙 ID만 가리켜야 합니다")
    return sorted(rules, key=lambda rule: int(rule["우선순위"]))


def allowed_values(path: Path, field: str) -> list[dict[str, str]]:
    """Read the output vocabulary from the same policy document."""
    document = load_document(path, field)
    if document is not None:
        return document["values"]
    body = _field_body(path.read_text(encoding="utf-8"), field)
    heads = list(re.finditer(r"^###\s+허용값\s*$", body, re.M))
    if not heads:
        raise PolicyPromptError(f"`## {field}`에 `### 허용값`이 없습니다")
    if len(heads) != 1:
        raise PolicyPromptError(f"`## {field}`에 `### 허용값`이 여러 개 있습니다")
    head = heads[0]
    tail = body[head.end():]
    next_head = re.search(r"^###\s+", tail, re.M)
    section = tail[:next_head.start()] if next_head else tail
    values = []
    for line in section.splitlines():
        clean = line.strip()
        if not clean:
            continue
        match = _VALUE.match(clean)
        if match:
            values.append({"code": match.group(1), "name": match.group(2).strip(), "description": match.group(3).strip()})
        elif clean.startswith("- "):
            raise PolicyPromptError(f"`## {field}`의 허용값 줄 모양이 잘못됐습니다: {clean}")
    if not values:
        raise PolicyPromptError(f"`## {field}`의 `### 허용값`이 비어 있거나 모양이 잘못됐습니다")
    codes = [row["code"] for row in values]
    if len(codes) != len(set(codes)):
        raise PolicyPromptError(f"`## {field}`의 허용값 코드가 중복됐습니다")
    return values


def render_inference_rules(path: Path, field: str) -> str:
    """Render full rule text for the inference decision maker, in priority order."""
    rules = decision_rules(path, field)
    if not rules:
        raise PolicyPromptError(f"`## {field}`에 프롬프트로 보낼 판정 규칙이 없습니다")
    values = allowed_values(path, field)
    vocabulary = "\n".join(f"- `{row['code']}` {row['name']} — {row['description']}" for row in values)
    rendered = "\n".join(
        f"{rule['우선순위']}. {rule['title']}\n{rule['내용']}"
        for rule in rules
    )
    return f"[허용값]\n{vocabulary}\n\n[우선순위가 있는 판정 규칙]\n{rendered}\n"


def sync_declared_prompt(profile: dict[str, Any], definitions: Path, *, apply: bool = False) -> dict[str, Any]:
    """Preview or synchronize a policy fragment into its declared production prompt resource.

    The destination adapter pins the resource digest and a content-derived version, so a policy
    edit cannot silently leave inference on stale prompt text.
    """
    policy_task = profile.get("policyTask")
    if isinstance(policy_task, dict):
        if profile.get("promptDelivery") or (profile.get("gtTask") or {}).get("promptDelivery"):
            raise PolicyPromptError("policyTask-v1이 있는 프로필은 promptDelivery를 policyTask 안에만 선언해야 합니다")
        delivery = policy_task.get("promptDelivery")
    else:
        delivery = profile.get("promptDelivery") or (profile.get("gtTask") or {}).get("promptDelivery")
    if not isinstance(delivery, dict):
        raise PolicyPromptError(f"{profile.get('id')}: profile.promptDelivery가 없습니다")
    from gt_task import resolve

    resource = resolve(profile, {"path": delivery.get("resource"), "root": delivery.get("root") or "source"})
    adapter = resolve(profile, {"path": delivery.get("adapter"), "root": delivery.get("root") or "source"})
    field = str(delivery.get("field") or "")
    begin, end = str(delivery.get("beginMarker") or ""), str(delivery.get("endMarker") or "")
    if not field or not begin or not end or begin == end:
        raise PolicyPromptError("promptDelivery에 field와 서로 다른 beginMarker/endMarker가 필요합니다")
    if not resource.is_file() or not adapter.is_file():
        raise PolicyPromptError(f"선언한 운영 프롬프트 또는 어댑터가 없습니다: {resource} · {adapter}")
    old_resource = resource.read_text(encoding="utf-8")
    if old_resource.count(begin) != 1 or old_resource.count(end) != 1 or old_resource.index(begin) > old_resource.index(end):
        raise PolicyPromptError("운영 프롬프트에는 선언한 시작·끝 표식이 각각 한 번, 올바른 순서로 있어야 합니다")
    rendered = render_inference_rules(definitions, field).rstrip()
    start = old_resource.index(begin) + len(begin)
    finish = old_resource.index(end)
    new_resource = old_resource[:start] + "\n" + rendered + "\n" + old_resource[finish:]
    digest = hashlib.sha256(new_resource.encode("utf-8")).hexdigest()
    old_adapter = adapter.read_text(encoding="utf-8")
    new_adapter = old_adapter
    targets = delivery.get("adapterTargets") or []
    if not targets:
        raise PolicyPromptError("promptDelivery.adapterTargets에 해시·버전 선언의 패턴과 템플릿을 지정해야 합니다")
    for target in targets:
        pattern_text = str(target.get("pattern") or "")
        template = str(target.get("valueTemplate") or "")
        if not pattern_text or not template:
            raise PolicyPromptError("adapterTargets 각 항목에 pattern과 valueTemplate이 필요합니다")
        pattern = re.compile(pattern_text, re.S)
        matches = list(pattern.finditer(new_adapter))
        if len(matches) != 1 or pattern.groups != 1:
            raise PolicyPromptError(f"adapterTargets {target.get('name') or '(이름 없음)'} 패턴이 한 선언과 일치하고 캡처 그룹이 정확히 하나여야 합니다")
        value = template.format(sha256=digest, hash12=digest[:12])
        new_adapter = pattern.sub(lambda m: m.group(1) + f'"{value}";', new_adapter, count=1)
    changed = new_resource != old_resource or new_adapter != old_adapter
    if apply and changed:
        resource.write_text(new_resource, encoding="utf-8", newline="")
        adapter.write_text(new_adapter, encoding="utf-8", newline="")
    return {"applied": bool(apply and changed), "changed": changed,
            "resource": str(resource), "adapter": str(adapter), "sha256": digest,
            "version": digest[:12],
            "prompt": rendered}


def render_agent_policy(definitions: Path, field: str) -> tuple[str, list[str]]:
    """Policy-owned purpose, evidence criteria, ordered rules, and vocabulary."""
    standard = load_document(definitions, field)
    if standard is not None:
        values = standard["values"]
        rules = "\n\n".join(
            f"{rule['id']} · {rule['title']}\n우선순위: {rule['우선순위']}\n{rule['내용']}"
            + (f"\n판례: {rule['판례']}" if rule["판례"] != "없음" else "")
            for rule in standard["rules"]
        )
        vocabulary = "\n".join(f"- `{v['code']}` {v['name']} — {v['description']}" for v in values)
        field_purpose = standard.get("fieldPurpose")
        purpose = standard["purpose"]
        if field_purpose:
            purpose = f"{purpose}\n\n[{standard['name']}]\n{field_purpose}"
        return (
            f"[목적]\n{purpose}\n\n[정책]\n{rules}\n\n[허용값]\n{vocabulary}\n",
            [value["code"] for value in values],
        )
    document = definitions.read_text(encoding="utf-8")
    purpose_section = _field_body(document, "목적")
    purpose_match = re.search(r"^### 무엇을 가르나\s*\n(.*?)(?=^### |\Z)",
                              purpose_section, re.M | re.S)
    if not purpose_match or not purpose_match.group(1).strip():
        raise ValueError("정책 문서에 목적의 '무엇을 가르나'가 필요합니다")
    purpose = purpose_match.group(1).strip()
    body = _field_body(document, field)
    allowed_heading = re.search(r"^###\s+허용값\s*$", body, re.M)
    if not allowed_heading:
        raise ValueError(f"정의 문서에 {field}의 허용값 섹션이 없습니다")
    guidance = body[:allowed_heading.start()].strip()
    # Storage/legacy response mappings belong to adapters, not decision guidance.
    guidance = re.sub(r"^### 응답 계약\s*\n.*?(?=^### |\Z)", "", guidance,
                      flags=re.M | re.S).strip()
    values = allowed_values(definitions, field)
    rules = decision_rules(definitions, field)
    vocabulary = "\n".join(
        f"- `{row['code']}` {row['name']} — {row['description']}" for row in values
    )
    ordered = "\n\n".join(
        f"규칙 {rule['우선순위']}. {rule['title']} ({rule['id']})\n{rule['내용']}" for rule in rules
    )
    guidance_block = f"이 정책의 근거 기준\n{guidance}\n\n" if guidance else ""
    return (f"[목적]\n{purpose}\n\n[정책]\n{guidance_block}{ordered}"
            f"\n\n[허용값]\n{vocabulary}\n"), [row["code"] for row in values]
