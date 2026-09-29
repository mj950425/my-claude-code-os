"""Human-readable attribute policies, bound to profile fields and value codes."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FORMAT = "policy-document-v2"
_SECTION_HEAD = re.compile(r"^## (.+)$", re.M)
_RULE_HEAD = re.compile(r"^#{3,4} ([A-Z][A-Z0-9_-]*) · ([^\n#].*?)\s*$", re.M)
_RULE_PROPERTY = re.compile(r"^- (내용|우선순위|판례):[ \t]*(.*)$", re.M)


def _profile_binding(path: Path) -> list[dict[str, Any]] | None:
    """Read and validate the v2 profile binding, or return None for legacy files."""
    config_path = path.parent / "profile.json"
    if not config_path.is_file():
        return None

    profile = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError(f"프로필은 JSON 객체여야 합니다: {config_path}")
    spec = profile.get("policyTask")
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ValueError("profile.policyTask는 객체여야 합니다")
    if spec.get("documentFormat") != FORMAT:
        return None

    definitions = spec.get("definitions")
    if not isinstance(definitions, str) or not definitions.strip():
        raise ValueError("policy-document-v2에는 policyTask.definitions 경로가 필요합니다")
    declared = Path(definitions)
    if not declared.is_absolute():
        project_root = _project_root(path)
        declared = project_root / declared
    if declared.resolve() != path.resolve():
        return None

    fields = spec.get("fields")
    if not isinstance(fields, list) or not fields or not all(isinstance(field, dict) for field in fields):
        raise ValueError("policy-document-v2에는 한 개 이상의 속성 객체가 필요합니다")
    for target in fields:
        for key, description in (("id", "id"), ("name", "표시 name")):
            value = target.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"policy-document-v2 속성에는 비어 있지 않은 {description}이 필요합니다")
        _value_codes(target)
    ids = [field["id"] for field in fields]
    names = [field["name"] for field in fields]
    if len(set(ids)) != len(ids) or len(set(names)) != len(names):
        raise ValueError("policy-document-v2 속성 ID와 표시 이름은 각각 고유해야 합니다")
    return fields


def _project_root(path: Path) -> Path:
    for parent in path.resolve().parents:
        if (parent / ".claude").is_dir():
            return parent
    raise ValueError(f"상대 정책 경로를 확인할 프로젝트 루트가 없습니다: {path}")


def _value_codes(target: dict[str, Any]) -> dict[str, str]:
    mapping = target.get("valueCodes")
    valid_pairs = isinstance(mapping, dict) and all(
        isinstance(name, str) and name.strip() and isinstance(code, str) and code.strip()
        for name, code in mapping.items()
    )
    if not valid_pairs or not mapping:
        raise ValueError("policyTask.fields[].valueCodes에 허용값 이름과 응답 코드 연결이 필요합니다")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("policyTask.fields[].valueCodes 응답 코드가 중복되었습니다")
    return mapping


def _parse_values(section: str, mapping: dict[str, str]) -> list[dict[str, str]]:
    values = []
    for line in section.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"- (.+?) — (.+)", line)
        if not match or match[1] not in mapping:
            raise ValueError(f"작업 설정에 연결되지 않은 허용값: {line}")
        values.append({"code": mapping[match[1]], "name": match[1], "description": match[2]})

    names = [value["name"] for value in values]
    codes = [value["code"] for value in values]
    if not values or len(set(names)) != len(names) or len(set(codes)) != len(codes):
        raise ValueError("허용값이 비어 있거나 중복되었습니다")
    if set(mapping) != set(names):
        raise ValueError("정책 허용값과 작업 설정의 값 연결이 다릅니다")
    return values


def _parse_rule_properties(rule_id: str, body: str) -> dict[str, str]:
    properties = list(_RULE_PROPERTY.finditer(body))
    expected = ["내용", "우선순위", "판례"]
    if ([match[1] for match in properties] != expected
            or body[:properties[0].start()].strip()):
        raise ValueError(f"{rule_id}: 내용·우선순위·판례를 순서대로 작성합니다")
    unknown_property = re.search(r"^- (?!내용:|우선순위:|판례:)[^\n:]+:", body, re.M)
    if unknown_property:
        raise ValueError(f"{rule_id}: 내용·우선순위·판례 외 속성은 정책 문서에 둘 수 없습니다")

    values: dict[str, str] = {}
    for index, prop in enumerate(properties):
        end = properties[index + 1].start() if index + 1 < len(properties) else len(body)
        continuation = body[prop.end():end].strip()
        value = prop[2] + ("\n" + continuation if continuation else "")
        values[prop[1]] = value.strip()

    priority = values["우선순위"]
    if (not values["내용"] or not values["판례"]
            or not re.fullmatch(r"공통|[1-9][0-9]*", priority)):
        raise ValueError(f"{rule_id}: 내용·판례 또는 우선순위가 잘못되었습니다")
    return values


def _parse_rules(section: str) -> list[dict[str, str]]:
    heads = list(_RULE_HEAD.finditer(section))
    all_headings = list(re.finditer(r"^#{3,4}\s+.*$", section, re.M))
    if (not heads or len(heads) != len(all_headings)
            or section[:heads[0].start()].strip()):
        raise ValueError("규칙은 '### ID · 제목'으로 작성합니다")

    rules = []
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(section)
        body = section[head.end():end].strip()
        rule = {"id": head[1], "title": head[2]}
        rule.update(_parse_rule_properties(head[1], body))
        rules.append(rule)

    ids = [rule["id"] for rule in rules]
    if len(set(ids)) != len(ids):
        raise ValueError("규칙 ID 중복")
    ranks = [rule["우선순위"] for rule in rules if rule["우선순위"] != "공통"]
    if len(set(ranks)) != len(ranks):
        raise ValueError("판정 우선순위 중복")
    return sorted(rules, key=lambda rule: 0 if rule["우선순위"] == "공통" else int(rule["우선순위"]))


def _parse_field_section(section: str, target: dict[str, Any]) -> dict[str, Any]:
    heads = list(re.finditer(r"^### (.+?)\s*$", section, re.M))
    if [head[1] for head in heads] != ["허용값", "규칙"]:
        raise ValueError(f"{target['name']}: 허용값·규칙 절을 순서대로 작성합니다")
    purpose = section[:heads[0].start()].strip()
    values_end = heads[1].start()
    values = _parse_values(section[heads[0].end():values_end].strip(), _value_codes(target))
    rules = _parse_rules(section[heads[1].end():].strip())
    return {
        "id": target["id"],
        "name": target["name"],
        "purpose": purpose,
        "values": values,
        "rules": rules,
    }


def _parse_common_rules(section: str | None) -> list[dict[str, str]]:
    if section is None:
        return []
    rules = _parse_rules(section)
    if any(rule["우선순위"] != "공통" for rule in rules):
        raise ValueError("공통 규칙 절의 우선순위는 공통이어야 합니다")
    return rules


def _field_projection(document: dict[str, Any], field: str) -> dict[str, Any]:
    matches = [item for item in document["fields"] if item["id"] == field]
    if not matches:
        raise ValueError(f"이 문서에 연결되지 않은 속성: {field}")
    selected = matches[0]
    return {
        **document,
        "field": selected["id"],
        "name": selected["name"],
        "fieldPurpose": selected["purpose"],
        "values": selected["values"],
        "fieldRules": selected["rules"],
        "rules": [*document["commonRules"], *selected["rules"]],
    }


def load_document(path: Path, field: str | None = None) -> dict[str, Any] | None:
    """Load v2 policy fields, or return None when the file uses a legacy format.

    Supplying ``field`` returns the field projection used by existing consumers.
    Without it, a multi-field document returns its shared and per-field sections.
    """
    configured_fields = _profile_binding(path)
    if configured_fields is None:
        return None

    raw = path.read_text(encoding="utf-8")
    title_match = re.match(r"^# ([^\n]+)\n", raw)
    top_heads = list(_SECTION_HEAD.finditer(raw))
    if not title_match or not top_heads or top_heads[0][1] != "목적":
        raise ValueError("정책 문서는 제목 뒤에 목적 절을 작성합니다")
    if raw[title_match.end():top_heads[0].start()].strip():
        raise ValueError("제목 아래 별도 설명은 목적 또는 속성 절에 작성합니다")
    section_names = [head[1] for head in top_heads]
    if len(set(section_names)) != len(section_names):
        raise ValueError("정책 절 제목 중복: 같은 이름의 절을 여러 번 작성할 수 없습니다")

    sections: dict[str, str] = {}
    for index, head in enumerate(top_heads):
        end = top_heads[index + 1].start() if index + 1 < len(top_heads) else len(raw)
        sections[head[1]] = raw[head.end():end].strip()
    purpose = sections.pop("목적")
    if not purpose:
        raise ValueError("정책 목적이 비어 있습니다")

    # The original single-field v2 layout remains readable during migration.
    if len(configured_fields) == 1 and set(sections) == {"허용값", "규칙"}:
        target = configured_fields[0]
        field_doc = {
            "id": target["id"], "name": target["name"], "purpose": "",
            "values": _parse_values(sections["허용값"], _value_codes(target)),
            "rules": _parse_rules(sections["규칙"]),
        }
        document = {
            "title": title_match[1], "purpose": purpose, "fields": [field_doc],
            "commonRules": [], "raw": raw,
        }
        return _field_projection(document, field) if field else {
            **document, "field": field_doc["id"], "name": field_doc["name"],
            "values": field_doc["values"], "rules": field_doc["rules"],
        }

    common_rules = _parse_common_rules(sections.pop("공통 규칙", None))
    by_name = {item["name"]: item for item in configured_fields}
    if set(sections) != set(by_name):
        missing = sorted(set(by_name) - set(sections))
        unknown = sorted(set(sections) - set(by_name))
        raise ValueError(f"정책의 속성 절과 프로필 fields가 다릅니다 (누락: {missing}, 미등록: {unknown})")

    fields = [_parse_field_section(sections[item["name"]], item) for item in configured_fields]
    rule_ids = [rule["id"] for rule in common_rules]
    rule_ids.extend(rule["id"] for item in fields for rule in item["rules"])
    if len(set(rule_ids)) != len(rule_ids):
        raise ValueError("규칙 ID는 공통 규칙과 모든 속성을 통틀어 고유해야 합니다")
    document = {
        "title": title_match[1],
        "purpose": purpose,
        "fields": fields,
        "commonRules": common_rules,
        "raw": raw,
    }
    if field is not None:
        return _field_projection(document, field)
    if len(fields) == 1:
        return _field_projection(document, fields[0]["id"])
    return document


def legacy_text(path: Path) -> str:
    """Read adapter for older field-based consumers; no extra decision instructions."""
    document = load_document(path)
    if document is None:
        return path.read_text(encoding="utf-8")

    fields = document["fields"] if "fields" in document else [{
        "id": document["field"], "name": document["name"],
        "purpose": document.get("fieldPurpose", ""),
        "values": document["values"], "rules": document.get("fieldRules", document["rules"]),
    }]
    blocks = []
    for field_doc in fields:
        values = "\n".join(
            f"- `{value['code']}` {value['name']} — {value['description']}"
            for value in field_doc["values"]
        )
        rules = [*document.get("commonRules", []), *field_doc["rules"]]
        rendered_rules = "\n".join(
            f"- `{rule['id']}` {rule['title']}\n"
            f"  - 내용: {rule['내용'].replace(chr(10), ' ')}\n"
            f"  - 우선순위: {rule['우선순위']}\n"
            f"  - 판례: {rule['판례']}"
            for rule in rules
        )
        intro = f"{field_doc['purpose']}\n\n" if field_doc.get("purpose") else ""
        blocks.append(
            f"## {field_doc['id']}\n\n{intro}### 허용값\n{values}\n\n"
            f"### 판정 규칙\n{rendered_rules}"
        )
    return f"# {document['title']}\n\n## 목적\n{document['purpose']}\n\n" + "\n\n".join(blocks) + "\n"
