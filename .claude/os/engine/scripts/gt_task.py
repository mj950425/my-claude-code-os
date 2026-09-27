#!/usr/bin/env python3
"""GT 개선 과제 하나를 읽는다 — 프로필의 `gtTask` 블록이 유일한 입력이다.

## 왜 따로 있는가

감사 사이클(`run_catalog_cycle.py`)은 **상품 하나에 라벨 하나**를 전제로 정책·판례·심판까지
엮는다. 그런데 GT를 고쳐야 하는 자리는 그 모양만이 아니다. 이미지 한 장에 관찰 칸이 여럿 붙는
메타데이터 GT가 있고, 그런 GT는 정책 문서도 심판도 아직 없다.

그 둘을 한 문으로 받으려면 단위를 한 칸 내려야 한다 — **(키, 필드) 하나가 판정 단위다.**
상품마다 라벨 하나인 GT는 필드가 하나인 과제이고, 이미지 관찰 GT는 필드가 여럿인 과제다.

## 무엇을 찾는가

| 신호 | 뜻 | 실행이 필요한가 |
|---|---|---|
| `GT_SELF_CONTRADICTION` | GT 한 줄 안에서 필드끼리 모순된다(선언된 제약) | 아니다 |
| `GT_OUT_OF_RANGE` | GT 값이 허용값 밖이다(대응 없는 옛 값, 오타) | 아니다 |
| `POLICY_CHANGED_SINCE_DECISION` | 사람이 고른(유지한) 값이 지금 정책의 허용값에 없다 | 아니다 |
| `GT_CHANGED_SINCE_DECISION` | 사람이 답한 뒤 원본 GT가 바뀌었다 — 사람이 본 값이 아니다 | 아니다 |
| `GT_MISSING` | 채워야 하는 필드가 GT에 비어 있다 | 아니다 |
| `GT_REFERENCE_ONLY` | GT 출처가 참고 등급이고, 실행 값이 없거나 굵은 범주뿐이라 교차 확인이 안 됐다 | 아니다 |
| `BLIND_DISAGREES` | (판독 뒤에만) 고른 건의 후보가 아닌 칸에서 GT를 모르는 판독이 확신 있게 다른 값을 냈다 | 아니다 |

실행과 비교하는 신호 하나만 두지 않는 이유는 사각 때문이다. 실행과 GT가 같이 틀리면
비교할 것이 없어 조용하다. GT 안의 모순·범위 밖·빈칸은 실행 없이도 보인다.

## 값의 모양

필드는 값 하나(`cardinality: one`, 기본)이거나 값 여럿(`many` — 색상 여러 개 같은)이다. 안에서는
값 여럿을 **정렬해 `|`로 이은 문자열** 하나로 다룬다. 그래야 비교·원장·화면이 한 모양이다.
내보낼 때는 원본 칸의 모양대로 되돌린다 — 목록이던 칸은 목록, «A|B» 글자이던 칸은 글자.

## 이 모듈이 하지 않는 것

판정하지 않는다. 사진을 보지 않는다. 무엇을 볼지만 고르고, 판단은 워크플로우의
에이전트가, 확정은 사람이 한다. 속성 이름을 모른다 — 필드 이름과 허용값은 전부 프로필에서 온다.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from catalog_profile import PROJECT_ROOT, output_root, project_path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))
import tile_rule  # noqa: E402

TASK_SCHEMA = "gt-task-v1"
MANY_SEPARATOR = "|"
KEY_SEPARATOR = "|"
RUNS_ROOT = (PROJECT_ROOT / ".claude" / "os" / "runs").resolve()
SIGNALS = {
    "GT_SELF_CONTRADICTION": {"rank": 1, "label": "GT 안에서 서로 모순",
                              "plain": "같은 줄의 다른 칸과 말이 안 맞습니다. 둘 중 하나는 틀렸습니다."},
    "GT_OUT_OF_RANGE": {"rank": 1, "label": "허용값 밖의 GT",
                        "plain": "GT 값이 정책의 허용값 목록에 없습니다 — 옛 이름·오타이거나, 정책에서 뺀 값입니다."},
    "GT_CHANGED_SINCE_DECISION": {"rank": 2, "label": "답한 뒤 GT가 바뀜",
                                  "plain": "사람이 답한 뒤 원본 GT가 바뀌었습니다. 지금 값을 다시 봐 주세요."},
    "POLICY_CHANGED_SINCE_DECISION": {"rank": 2, "label": "답한 뒤 정책이 바뀜",
                                      "plain": "사람이 고른 값이 지금 정책의 허용값에 없습니다(정책에서 뺀 값). 다시 골라 주세요."},
    "GT_MISSING": {"rank": 4, "label": "GT가 비어 있음",
                   "plain": "이 칸에 정답이 없습니다. 증거(사진·글)를 보고 채울 값을 제안합니다."},
    "GT_REFERENCE_ONLY": {"rank": 5, "label": "사람 확인 전 라벨",
                          "plain": "사람이 확인하지 않은 라벨입니다. 증거와 맞는지 한 번 봅니다."},
    # 고르기 단계의 신호가 아니다 — 판독 뒤에만 붙는다. 건을 고르게 한 칸 말고 같은 건의 다른 칸에서, GT를 모르는 눈이
    # 확신 있게 다른 값을 낸 경우. 실행과 GT가 함께 틀린 칸은 비교할 것이 없어 조용한데, 판독이 그 사각을 본다.
    "BLIND_DISAGREES": {"rank": 6, "label": "증거만 본 AI와 다름",
                        "plain": "이 칸 때문에 고른 건은 아니지만, 정답을 모르는 AI가 증거만 보고 확신 있게 다른 값을 냈습니다."},
}
# 같은 신호 안에서는 사람 확인 전 라벨을 먼저 본다. 틀렸을 가능성이 더 높고, 고쳐도 사람의
# 확정을 뒤집는 일이 아니다. 사람이 확정한 라벨은 뒤로 — 기준이 더 높아야 하기 때문이다.
AUTHORITY_ORDER = {"REFERENCE": 0, "UNKNOWN": 1, "NONE": 1, "TRUSTED": 2}


class TaskError(ValueError):
    """과제 선언이나 데이터가 규격을 벗어났다. 사람이 읽을 문장으로 말한다."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # 줄은 «\n»으로만 나눈다. splitlines는 U+2028 같은 글자에서도 끊는데, JSON은 그 글자를 문자열 안에 그대로 쓴다.
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise TaskError(f"{path}:{number} JSON을 읽지 못했습니다: {error}") from error
        if not isinstance(value, dict):
            # 객체가 아닌 줄을 건너뛰면, 내보낸 GT가 그 줄을 조용히 잃는다.
            raise TaskError(f"{path}:{number} 객체가 아닌 줄입니다. GT 한 줄은 JSON 객체여야 합니다.")
        rows.append(value)
    return rows


def resolve(profile: dict[str, Any], spec: dict[str, Any], key: str = "path") -> Path:
    """경로의 기준을 선언으로 고른다. `root`가 `source`면 외부 레포, `output`이면 run 폴더."""
    raw = spec.get(key)
    if not raw:
        raise TaskError(f"{profile.get('id')}: {key}가 비어 있습니다.")
    root = spec.get("root") or "project"
    if root == "source":
        repository = (profile.get("source") or {}).get("repository")
        if not repository:
            raise TaskError(f"{profile.get('id')}: root=source인데 source.repository가 없습니다.")
        return (project_path(str(repository)) / str(raw)).resolve()
    if root == "output":
        return (output_root(profile) / str(raw)).resolve()
    if root == "project":
        return project_path(str(raw))
    raise TaskError(f"{profile.get('id')}: root는 project·source·output 중 하나여야 합니다: {root}")


def missing_input(task: dict[str, Any], what: str, path: Path) -> TaskError:
    """입력 파일이 없을 때의 문장. 그 파일을 다시 만드는 가져오기가 선언돼 있으면 그것을 가리킨다 —
    runs/ 아래의 파생 입력은 지워질 수 있고, 지워졌을 때 할 일은 «개발자에게»가 아니라 «다시 가져오기»다."""
    prerequisite = task.get("prerequisite") or {}
    if prerequisite.get("adapter"):
        return TaskError(f"{what}이 없습니다: {path}. 가져오기를 다시 돌리면 생깁니다 — "
                         f"python3 {prerequisite['adapter']} (그다음 다시 준비)")
    return TaskError(f"{what}이 없습니다: {path}")


def key_fields(spec: Any) -> list[str]:
    """키는 열 하나이거나 여럿(복합 키)이다. 이미지·옵션 단위 GT는 흔히 두 열이 키다."""
    return [str(item) for item in spec] if isinstance(spec, list) else [str(spec)]


def key_of(row: dict[str, Any], fields: list[str]) -> str | None:
    parts = [row.get(field) for field in fields]
    if any(part in (None, "") for part in parts):
        return None
    if len(parts) > 1 and any(KEY_SEPARATOR in str(part) for part in parts):
        # 복합 키를 `|`로 잇는다. 조각 안에 `|`가 있으면 다른 두 줄이 같은 키가 될 수 있다.
        raise TaskError(f"복합 키 조각에 «{KEY_SEPARATOR}»가 들어 있습니다: {parts}. 키 열을 바꿔 선언하세요.")
    return KEY_SEPARATOR.join(str(part) for part in parts)


def key_parts(key: str, fields: list[str], row: dict[str, Any] | None = None) -> dict[str, Any]:
    """이은 키를 원래 열로 되돌린다. 원본 줄이 있으면 그 줄의 값(원래 형)을 쓴다 — 정수 키가 문자열로 바뀌지 않게."""
    if row is not None:
        return {field: row.get(field) for field in fields}
    values = key.split(KEY_SEPARATOR) if len(fields) > 1 else [key]
    return dict(zip(fields, values))


def superseded_by(path: Path, spec: dict[str, Any] | None) -> Path | None:
    """이 GT를 바탕으로 삼은 새 GT가 있으면 그 파일. 옛 GT를 고치면 새 GT에 이미 들어간 사람의 검수를 덮는다 —
    실제로 한 번 그렇게 옛 판을 가리킨 채 돌았다.

    «새 판이 옛 판을 어떻게 밝히는가»는 데이터 출처마다 다르므로 프로필이 선언한다(`gt.supersededBy`):
    `{glob, baseField, markerSuffix, newerSuffix}` — 같은 폴더에서 glob에 맞는 표지 파일 가운데 baseField가 이 GT를
    가리키는 것. 새 판의 이름은 표지 이름에서 markerSuffix(기본: 표지의 확장자)를 떼고 newerSuffix(기본 .jsonl)를 붙인다."""
    if not spec:
        return None
    for marker in sorted(path.parent.glob(str(spec.get("glob") or ""))):
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        base = str(value.get(str(spec.get("baseField") or "")) or "")
        if base and Path(base).name == path.name:
            suffix = str(spec.get("markerSuffix") or marker.suffix)
            return marker.with_name(marker.name[: -len(suffix)] + str(spec.get("newerSuffix") or ".jsonl"))
    return None


# 절 머리는 «## 아무 제목» 전부다 — 낱말 하나짜리만 경계로 보면 «## 필드끼리의 제약» 같은 절이 앞 필드의 본문에 붙는다.
_SECTION_HEAD = re.compile(r"^##\s+(.+?)\s*$", re.M)


_VALUES_HEAD = re.compile(r"^###\s+허용값\s*$", re.M)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_INDENT = (" ", "\t")
_ODD_VALUES_HEAD = re.compile(r"^#{1,6}\s*.*허용\s*값.*$", re.M)
_VALUE_LINE = re.compile(r"^- `([^`\s]+)`(?: ([^`—]*?))? — \S")
VALUE_LINE_SHAPE = "- `코드` 이름 — 설명"


def definition_values(path: Path) -> dict[str, list[tuple[str, str]]]:
    """정책(정의 문서)이 정하는 허용값. 필드 절(`## <필드ID>`) 안 `### 허용값` 바로 아래의 **한 덩어리 목록**을 차례대로 읽는다.

    한 줄 모양은 `` - `코드` 이름 — 설명 `` 하나뿐이다 — 코드는 백틱 안(공백 없이), 이름은 코드 뒤부터 « — » 앞까지(없어도 된다),
    « — » 뒤가 설명이다. 들여쓴 줄은 앞 줄 설명의 이어짐이다. 목록은 빈 줄에서 끝난다. 목록 순서가 화면의 고르기 순서다.

    조용히 넘어가는 줄이 없게 멈춘다 — 모양이 다른 목록 줄(`*` 글머리표, 백틱 없음, « — » 없음), 목록이 끝난 뒤 같은 절에 다시
    나오는 목록 줄(설명 글머리표가 값이 되는 일), `### 허용값`이 둘. 정본이 한 문서라서, 그 문서를 잘못 읽으면 값이 말없이 늘거나 준다."""
    values: dict[str, list[tuple[str, str]]] = {}
    for field_id, body in definition_texts(path).items():
        heads = list(_VALUES_HEAD.finditer(body))
        odd = [match.group(0).strip() for match in _ODD_VALUES_HEAD.finditer(body) if not _VALUES_HEAD.fullmatch(match.group(0))]
        if odd:
            raise TaskError(f"정의 문서 `## {field_id}`에 «{odd[0]}» 제목이 있습니다 — 허용값 제목은 `### 허용값` 한 가지 모양만 읽습니다"
                            "(다른 모양이면 목록을 못 보고 지나칩니다).")
        if not heads:
            continue
        if len(heads) > 1:
            raise TaskError(f"정의 문서 `## {field_id}` 절에 `### 허용값`이 둘 있습니다 — 하나로 합쳐 주세요.")
        rest = body[heads[0].end():]
        cut = re.search(r"^###\s", rest, re.M)
        lines = (rest[:cut.start()] if cut else rest).split("\n")
        rows: list[tuple[str, str]] = []
        state = "before"  # before → list → after
        for number, raw in enumerate(lines, 1):
            line = raw.rstrip("\r")
            if not line.strip():
                state = "after" if state == "list" else state
                continue
            if _LIST_ITEM.match(line) and line.startswith(_INDENT):
                # 들여쓴 글머리표는 목록 앞이든 안이든 뒤든 값으로 읽지 않는다 — 조용히 건너뛰지 않고 멈춘다.
                raise TaskError(f"정의 문서 `## {field_id}`의 `### 허용값`에 들여쓴 글머리표가 있습니다: «{line.strip()}» — "
                                "값은 들여쓰지 않은 한 줄씩 적고, 설명 글머리표는 `### 허용값` 밖(다른 `###` 절)에 둡니다.")
            if _LIST_ITEM.match(line):
                if state == "after":
                    raise TaskError(f"정의 문서 `## {field_id}`의 `### 허용값` 목록이 빈 줄 뒤에 다시 이어집니다: «{line.strip()}» — "
                                    "값은 빈 줄 없이 한 덩어리로 적고, 설명 글머리표는 `### 허용값` 밖(다른 `###` 절)에 둡니다.")
                match = _VALUE_LINE.match(line)
                if not match:
                    raise TaskError(f"정의 문서 `## {field_id}`의 `### 허용값` 줄이 `{VALUE_LINE_SHAPE}` 모양이 아닙니다: «{line.strip()}»")
                name = (match.group(2) or "").strip()
                if len(name) > 30 or re.search(r"[:：*_\[\]`]|--|–| - ", name):
                    raise TaskError(f"정의 문서 `## {field_id}`의 `{match.group(1)}` 줄 이름이 이상합니다(«{name}») — "
                                    f"이름과 설명은 « — »(긴 줄표)로 가릅니다: `{VALUE_LINE_SHAPE}`")
                rows.append((match.group(1), name))
                state = "list"
            elif state == "before":
                continue  # 목록 앞의 안내 문장
            elif state == "list" and line.startswith(_INDENT):
                continue  # 앞 값 설명의 이어짐
            elif state == "list":
                raise TaskError(f"정의 문서 `## {field_id}`의 `### 허용값` 목록 바로 뒤에 들여쓰지 않은 줄이 있습니다: «{line.strip()}» — "
                                "앞 값 설명의 이어짐이면 들여쓰고, 새 문단이면 목록과 사이에 빈 줄을 둡니다.")
            # after: 목록 뒤 문단. 목록 줄은 위에서 멈췄다.
        values[field_id] = rows
    return values


def definition_value_notes(path: Path) -> dict[str, dict[str, str]]:
    """값마다 정책이 붙인 설명(« — » 뒤 첫 줄). 화면이 값 이름 옆에 한 줄 기준으로 보인다 — 이름만 보고 고르면
    정의와 다른 뜻으로 고친다(이름은 짧고, 경계는 설명에 있다). 목록의 모양 검사는 `definition_values`가 한다."""
    notes: dict[str, dict[str, str]] = {}
    for field_id, body in definition_texts(path).items():
        head = _VALUES_HEAD.search(body)
        if not head:
            continue
        for line in body[head.end():].split("\n"):
            match = re.match(r"^- `([^`\s]+)`(?: [^`—]*?)? — (.+)$", line.rstrip())
            if match:
                notes.setdefault(field_id, {})[match.group(1)] = match.group(2).strip()
                last = match.group(1)
            elif line.startswith(_INDENT) and line.strip() and notes.get(field_id):
                # 들여쓴 줄은 앞 값 설명의 이어짐 — 첫 줄만 보이면 조건이 문장 중간에서 잘린다.
                notes[field_id][last] += " " + line.strip()
            elif line.strip() and notes.get(field_id):
                break
    return notes


def definition_sections(path: Path) -> set[str]:
    return {match.group(1).strip() for match in _SECTION_HEAD.finditer(path.read_text(encoding="utf-8"))}


def definition_texts(path: Path) -> dict[str, str]:
    """필드마다 정의 문서의 그 절(`## <필드ID>`부터 다음 `## `까지) 글. 사람 화면이 판독자와 같은 기준을 보이게 한다."""
    text = path.read_text(encoding="utf-8")
    heads = list(_SECTION_HEAD.finditer(text))
    names = [head.group(1).strip() for head in heads]
    twice = sorted({name for name in names if names.count(name) > 1})
    if twice:
        # 같은 절이 둘이면 뒤 절이 앞 절을 말없이 덮는다 — 사람은 앞 절을 고치고 화면은 뒤 절을 그린다.
        raise TaskError(f"정의 문서 {path.name}에 같은 절이 둘 있습니다: {', '.join(f'`## {name}`' for name in twice)} — 하나로 합쳐 주세요.")
    return {head.group(1).strip(): text[head.end():(heads[n + 1].start() if n + 1 < len(heads) else len(text))].strip()
            for n, head in enumerate(heads)}


# 정책의 규칙 — 검수에서 자란다. 칸 절의 `### 규칙`에는 적용 중인 규칙만, 대체된 규칙은 `## 보관`에 둔다.
# 판독자는 칸 절을 통째로 읽으니, 걸러내는 코드 없이 «지금 정책 전체»만 읽는다.
#   - `R1` <규칙 문장> → `<허용값 코드>`        (→ 뒤는 없어도 된다 — 값으로 이어지지 않는 안내 규칙)
#     - 물음: <이 규칙을 낳은 AI의 물음>          (검수 문답일 때)
#     - 출처: <검수 문답 · 날짜 · 사람 · 판정 ID | 직접 작성 · 날짜 · 사람>   (반드시)
#     - 범위: <상품 카테고리 이름 한 마디, 쉼표로 여럿>   (없으면 모든 상품)
#     - 근거: <골든셋 키, 쉼표로 여럿>
# `## 보관`의 줄은 ID 앞에 칸을 붙이고(`<칸ID>/R0`), `대체:` 한 줄을 더한다.
RULES_HEAD = "### 규칙"
ARCHIVE_SECTION = "보관"
PURPOSE_SECTION = "목적"
PURPOSE_PARTS = ("무엇을 가르나", "어디에 쓰나", "기대 효과")
RULE_KEYS = ("물음", "출처", "범위", "근거")
RULE_LINE_SHAPE = "- `R1` 규칙 문장 → `코드`"
_RULE_LINE = re.compile(r"^- `(?:([A-Za-z][\w-]*)/)?(R\d+)` (.+?)(?: → `([^`\s]+)`)?$")
_RULE_SUB = re.compile(r"^\s{2,}- ([^:：]+): (.+)$")


def _rule_block(where: str, lines: list[str], labels: dict[str, list[str]], many: dict[str, bool],
                field_id: str | None, archive: bool) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    keys = RULE_KEYS + (("대체",) if archive else ())
    for raw in lines:
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        sub = _RULE_SUB.match(line)
        if sub:
            if not rules:
                raise TaskError(f"정의 문서 {where}의 첫 줄이 규칙이 아니라 딸린 줄입니다: «{line.strip()}»")
            name, value = sub.group(1).strip(), sub.group(2).strip()
            if name not in keys:
                raise TaskError(f"정의 문서 {where}의 규칙 {rules[-1]['id']}에 모르는 줄이 있습니다: «{name}» — "
                                f"{' · '.join(keys)} 중 하나로 적어 주세요.")
            if name in rules[-1]:
                raise TaskError(f"정의 문서 {where}의 규칙 {rules[-1]['id']}에 «{name}» 줄이 둘 있습니다.")
            rules[-1][name] = value
            continue
        match = _RULE_LINE.match(line)
        if not match:
            raise TaskError(f"정의 문서 {where}의 줄이 `{RULE_LINE_SHAPE}` 모양이 아닙니다: «{line.strip()}»")
        owner = match.group(1) if archive else field_id
        if archive and not owner:
            raise TaskError(f"정의 문서 {where}의 규칙은 칸을 붙여 적습니다(`칸ID/R1`): «{line.strip()}»")
        if not archive and match.group(1):
            raise TaskError(f"정의 문서 {where}의 규칙 ID에 칸을 붙이지 않습니다(칸 절 안이라 이미 압니다): «{line.strip()}»")
        if owner not in labels:
            raise TaskError(f"정의 문서 {where}의 규칙이 없는 칸을 가리킵니다: {owner}")
        value = match.group(4)
        if value is not None:
            parts = [part for part in value.split(MANY_SEPARATOR) if part] if many.get(owner) else [value]
            if not parts or any(part not in labels[owner] for part in parts):
                raise TaskError(f"정의 문서 {where}의 규칙 {match.group(2)}이 허용값이 아닌 값을 가리킵니다: {value}")
        rules.append({"field": owner, "id": match.group(2), "text": match.group(3).strip(), "value": value})
    for rule in rules:
        if not rule.get("출처"):
            raise TaskError(f"정의 문서 {where}의 규칙 {rule['field']}/{rule['id']}에 «출처» 줄이 없습니다 — 어디서 온 규칙인지 모르면 되짚을 수 없습니다.")
        if archive and not rule.get("대체"):
            raise TaskError(f"정의 문서 {where}의 규칙 {rule['field']}/{rule['id']}에 «대체» 줄이 없습니다 — 무엇으로 바뀌었는지 적어 주세요.")
    return rules


def definition_policy(path: Path, labels: dict[str, list[str]], many: dict[str, bool] | None = None) -> dict[str, Any]:
    """정책의 목적·규칙·보관을 읽는다. 허용값처럼 엄격하다 — 모양이 다른 줄, 없는 값, 같은 ID가 둘이면 멈춘다.
    조용히 빠진 규칙은 판독자에게도 화면에도 가지 않는데, 사람은 넣은 줄 안다."""
    many = many or {}
    texts = definition_texts(path)
    purpose = None
    if PURPOSE_SECTION in texts:
        parts = {m.group(1).strip(): m for m in re.finditer(r"^###\s+(.+?)\s*$", texts[PURPOSE_SECTION], re.M)}
        odd = sorted(set(parts) - set(PURPOSE_PARTS))
        lacking = [name for name in PURPOSE_PARTS if name not in parts]
        if odd or lacking:
            raise TaskError(f"정의 문서 `## {PURPOSE_SECTION}`은 `### {'`·`### '.join(PURPOSE_PARTS)}` 세 소제목으로 적습니다"
                            + (f" — 없는 것: {', '.join(lacking)}" if lacking else "") + (f" — 모르는 것: {', '.join(odd)}" if odd else ""))
        body = texts[PURPOSE_SECTION]
        heads = sorted(parts.values(), key=lambda m: m.start())
        purpose = {"note": body[:heads[0].start()].strip()}
        for n, head in enumerate(heads):
            text = body[head.end():(heads[n + 1].start() if n + 1 < len(heads) else len(body))].strip()
            if not text:
                raise TaskError(f"정의 문서 `## {PURPOSE_SECTION}`의 `### {head.group(1).strip()}`이 비어 있습니다.")
            purpose[head.group(1).strip()] = text
    rules: dict[str, list[dict[str, Any]]] = {}
    for field_id in labels:
        body = texts.get(field_id) or ""
        heads = list(re.finditer(r"^###\s+규칙\s*$", body, re.M))
        if len(heads) > 1:
            raise TaskError(f"정의 문서 `## {field_id}` 절에 `{RULES_HEAD}`이 둘 있습니다 — 하나로 합쳐 주세요.")
        if not heads:
            continue
        rest = body[heads[0].end():]
        cut = re.search(r"^###\s", rest, re.M)
        rules[field_id] = _rule_block(f"`## {field_id}`의 `{RULES_HEAD}`", (rest[:cut.start()] if cut else rest).split("\n"),
                                      labels, many, field_id, archive=False)
    archive = _rule_block(f"`## {ARCHIVE_SECTION}`", (texts.get(ARCHIVE_SECTION) or "").split("\n"), labels, many, None, archive=True)
    for field_id in labels:
        ids = [rule["id"] for rule in rules.get(field_id, [])] + [rule["id"] for rule in archive if rule["field"] == field_id]
        twice = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if twice:
            raise TaskError(f"정의 문서 `## {field_id}`의 규칙 ID가 겹칩니다(보관 포함): {', '.join(twice)} — ID는 한 번만 씁니다.")
    return {"purpose": purpose, "rules": rules, "archive": archive}


def next_rule_id(policy: dict[str, Any], field_id: str) -> str:
    used = [int(rule["id"][1:]) for rule in policy["rules"].get(field_id, [])] + \
           [int(rule["id"][1:]) for rule in policy["archive"] if rule["field"] == field_id]
    return f"R{max(used, default=0) + 1}"


def gt_path(profile: dict[str, Any]) -> Path | None:
    """이 프로필이 판정 원장으로 고치는 GT 파일. 과제 없는 프로필은 사이클의 GT를 쓴다."""
    task = profile.get("gtTask")
    if isinstance(task, dict) and task.get("gt"):
        return resolve(profile, task["gt"])
    cycle = profile.get("gt")
    if isinstance(cycle, dict) and cycle.get("path"):
        return project_path(str(cycle["path"]))
    return None


class LineageUnknown(TaskError):
    """사이클의 계보 폴더가 이 컴퓨터에 없다. 그 폴더가 어디였을지는 안다(`folder`)."""

    def __init__(self, message: str, folder: Path | None) -> None:
        super().__init__(message)
        self.folder = folder


def ledger_targets(profile: dict[str, Any]) -> set[Path]:
    """이 프로필의 판정이 결국 들어가는 파일 전부. 사이클 프로필은 합친 GT만이 아니라 그 계보 원본까지다.

    사이클의 GT(`.claude/gt/<id>/gt.jsonl`)는 외부 레포의 계보 파일들을 순위로 합친 결과다. 과제가 그 계보
    파일 하나를 직접 고치면, 결과 파일은 달라도 같은 정답을 두 원장이 고치는 셈이다.
    """
    targets: set[Path] = set()
    main = gt_path(profile)
    if main is not None:
        targets.add(main)
    cycle = profile.get("gt")
    if isinstance(cycle, dict) and cycle.get("sourceDir"):
        repository = (profile.get("source") or {}).get("repository")
        folder = (project_path(str(repository)) / str(cycle["sourceDir"])).resolve() if repository else None
        patterns = [str(lineage.get("pattern")) for lineage in cycle.get("lineages") or [] if lineage.get("pattern")]
        if (cycle.get("corrections") or {}).get("pattern"):
            patterns.append(str(cycle["corrections"]["pattern"]))
        if folder is None or not folder.is_dir():
            # 폴더가 없으면 그 안의 파일을 알 수 없다. 모르면 열린 채 통과하지 않는다 — 다만 부르는 쪽(check_one_ledger)은
            # 비교할 GT가 이 폴더 안에 있을 수 없을 때(다른 뿌리에 있고 그 파일이 실제로 있을 때) 이 오류를 넘긴다.
            raise LineageUnknown(f"{profile.get('id')}: 사이클 GT의 계보 폴더를 찾지 못했습니다({folder}). "
                                 "계보 원본을 모르면 «한 GT에 원장 하나»를 확인할 수 없습니다.", folder)
        if folder.is_dir():
            for path in folder.iterdir():
                if any(re.match(pattern, path.name) for pattern in patterns):
                    targets.add(path.resolve())
    return targets


def load_task(profile: dict[str, Any]) -> dict[str, Any]:
    """`gtTask` 블록을 검사해 돌려준다. 빠진 것이 있으면 무엇이 빠졌는지 말하고 멈춘다."""
    pid = profile.get("id")
    task = profile.get("gtTask")
    if not isinstance(task, dict):
        raise TaskError(f"{pid}: gtTask 블록이 없습니다. GT 개선 과제가 아닙니다.")
    # 사본에 적는다 — 정책에서 읽은 허용값을 프로필 딕셔너리에 얹으면, 그 딕셔너리를 다시 저장하는 쪽이 목록을 프로필에 되써 버린다.
    task = copy.deepcopy(task)
    task["_profileId"] = pid
    if task.get("schemaVersion") != TASK_SCHEMA:
        raise TaskError(f"{pid}: gtTask.schemaVersion은 {TASK_SCHEMA}여야 합니다.")
    # 정답이 두 원장에 있으면 화면마다 다른 답을 그린다. 감사 사이클의 원장(`gt` 블록)을 쓰는
    # 속성은 그 사이클의 판정 원장으로 고친다. 두 문을 한 GT에 달지 않는다.
    # 예외는 하나 — 사이클이 GT를 **만들기만** 하고(계보 합치기) 판정 원장은 이 과제에 넘긴 경우(`gt.ledger: "gtTask"`).
    # 그때도 두 블록은 같은 GT 파일을 가리켜야 한다. 사이클 쪽 판정 문(record_review_decision)은 이 표시를 보고 닫힌다.
    cycle = profile.get("gt")
    if cycle:
        if not (isinstance(cycle, dict) and cycle.get("ledger") == "gtTask"):
            raise TaskError(f"{pid}: gt 블록(감사 사이클 원장)과 gtTask를 함께 선언할 수 없습니다. "
                            "한 GT에 판정 원장은 하나여야 합니다 — 원장을 이 과제로 옮기려면 gt 블록에 \"ledger\": \"gtTask\"를 적습니다.")
        if project_path(str(cycle.get("path") or "")).resolve() != resolve(profile, task.get("gt") or {}).resolve():
            raise TaskError(f"{pid}: gt 블록이 원장을 gtTask에 넘겼다면 두 블록이 같은 GT 파일을 가리켜야 합니다 "
                            f"({cycle.get('path')} ≠ {(task.get('gt') or {}).get('path')}).")
    for key in ("keyField", "gt", "fields", "definitions"):
        if not task.get(key):
            raise TaskError(f"{pid}: gtTask.{key}가 필요합니다.")
    # 정답은 지워도 되는 자리에 두지 않는다. runs/는 다시 만들 수 있는 산출물이다.
    source = resolve(profile, task["gt"])
    # 이 과제의 GT 파일부터 본다 — 없으면(데이터 레포를 안 받은 컴퓨터) 다른 프로필의 원장 검사보다 먼저, 그 사실로 멈춘다.
    # 뒤에서 멈추면 «다른 속성의 계보 폴더»라는, 운영팀이 할 일과 무관한 문장이 나간다.
    if not source.is_file():
        raise TaskError(f"{pid}: GT 파일이 없습니다: {source}")
    newer = superseded_by(source, task["gt"].get("supersededBy"))
    if newer is not None:
        raise TaskError(f"{pid}: 이 GT({source.name})는 더 새 GT({newer.name})의 바탕입니다. 옛 GT를 고치면 새 GT의 "
                        "사람 검수를 덮습니다. 프로필의 gtTask.gt를 새 파일로 바꾸세요.")
    if RUNS_ROOT in source.parents:
        raise TaskError(f"{pid}: GT가 runs/ 아래에 있습니다({source}). runs/는 지워도 되는 산출물이라 "
                        "정답의 원본이 될 수 없습니다. 원본 GT(외부 레포나 .claude/gt/)를 가리키세요.")
    fields = task["fields"]
    if not isinstance(fields, list) or not all(isinstance(f, dict) and f.get("id") for f in fields):
        raise TaskError(f"{pid}: gtTask.fields는 id를 가진 객체 목록이어야 합니다.")
    for name in ("groupField", "titleField", "linkField"):
        if task.get(name) is not None and not isinstance(task[name], str):
            raise TaskError(f"{pid}: gtTask.{name}은 열 하나(문자열)만 적습니다 — 복합 키는 keyField에만 씁니다.")
    # 허용값·이름표의 정본은 정책(정의 문서)이다 — 필드 절의 `### 허용값`. 프로필에는 두지 않는다(둘이면 어긋난다).
    definitions = resolve(profile, {"path": task["definitions"], "root": task.get("definitionsRoot") or "project"})
    if not definitions.is_file():
        raise TaskError(f"{pid}: 정의 문서가 없습니다: {definitions}")
    # 필드 절이 통째로 없는 것을 먼저 모두 말한다 — 허용값 목록은 그다음이다.
    absent = [str(field.get("id")) for field in fields if isinstance(field, dict) and field.get("id")
              and field["id"] not in definition_sections(definitions)]
    if absent:
        raise TaskError(f"{pid}: 정의 문서에 필드 절(`## <필드ID>`)이 없습니다: {', '.join(absent)}")
    try:
        policy_values = definition_values(definitions)
    except TaskError as error:
        raise TaskError(f"{pid}: {error}") from error
    for field in fields:
        private = sorted(key for key in field if str(key).startswith("_"))
        if private:
            raise TaskError(f"{pid}: 필드 {field['id']}에 적을 수 없는 키가 있습니다: {', '.join(private)} — `_`로 시작하는 키는 로더가 쓰는 자리입니다.")
        if "labels" in field or "labelNames" in field:
            raise TaskError(f"{pid}: 필드 {field['id']}의 허용값은 정책(정의 문서)이 정본입니다 — 프로필의 labels·labelNames를 지우고 "
                            f"정의 문서 `## {field['id']}` 절의 `### 허용값`에 `` - `코드` 이름 — 설명 `` 줄로 적어 주세요.")
        rows = policy_values.get(field["id"])
        if not rows:
            raise TaskError(f"{pid}: 정의 문서 `## {field['id']}` 절에 `### 허용값` 목록이 없습니다 — 고를 수 있는 값은 정책이 정합니다.")
        codes = [code for code, _ in rows]
        twice = sorted({code for code in codes if codes.count(code) > 1})
        if twice:
            raise TaskError(f"{pid}: 정의 문서 `## {field['id']}`의 허용값에 같은 코드가 두 번 있습니다: {', '.join(twice)}")
        field["labels"] = codes
        field["labelNames"] = {code: name for code, name in rows if name}
    # 목적·규칙·보관도 로드할 때 읽어 본다 — 모양이 틀린 정책으로 판독을 돌리면 규칙이 말없이 빠진다.
    try:
        definition_policy(definitions, {field["id"]: [str(c) for c in field["labels"]] for field in fields},
                          {field["id"]: field.get("cardinality") == "many" for field in fields})
    except TaskError as error:
        raise TaskError(f"{pid}: {error}") from error
    seen: set[str] = set()
    names_seen: set[str] = set()
    for field in fields:
        if field["id"] in seen:
            raise TaskError(f"{pid}: 필드 {field['id']}가 두 번 선언됐습니다.")
        seen.add(field["id"])
        # 이름은 판독자가 쓰는 말이고 화면의 칸 이름이다 — 둘이 같으면 판독이 어느 칸인지 가를 수 없다.
        if field.get("name") in names_seen:
            raise TaskError(f"{pid}: 필드 이름 «{field['name']}»이 두 필드에 쓰였습니다.")
        if field.get("name"):
            names_seen.add(field["name"])
        labels = field.get("labels")
        if not isinstance(labels, list) or len(labels) < 2:
            raise TaskError(f"{pid}: 정의 문서 `## {field['id']}`의 `### 허용값`에 값이 둘 이상 있어야 합니다.")
        kind = field.get("valueType", "string")
        if kind == "boolean" and set(map(str, labels)) - {"true", "false"}:
            raise TaskError(f"{pid}: 필드 {field['id']}는 참·거짓 필드라 라벨이 \"true\"·\"false\"여야 합니다.")
        if kind == "integer" and not all(str(label).lstrip("-").isdigit() for label in labels):
            raise TaskError(f"{pid}: 필드 {field['id']}는 정수 필드라 라벨이 정수 모양이어야 합니다.")
        if not all(isinstance(label, str) for label in labels):
            # 참·거짓·정수 필드도 라벨은 문자열("true"·"2")이다. 원본 값의 형은 valueType이 말한다.
            raise TaskError(f"{pid}: 필드 {field['id']}의 labels는 문자열이어야 합니다(참·거짓은 \"true\"·\"false\").")
        if field.get("cardinality", "one") not in ("one", "many"):
            raise TaskError(f"{pid}: 필드 {field['id']}의 cardinality는 one·many 중 하나여야 합니다.")
        if any(MANY_SEPARATOR in str(label) for label in labels):
            raise TaskError(f"{pid}: 필드 {field['id']}의 허용값에 «{MANY_SEPARATOR}»를 쓸 수 없습니다.")
        # 옛 이름·«가를 수 없음»·이름표가 허용값 밖을 가리키면 조용히 틀린다(범위 밖 칸이 쏟아지거나,
        # 누르면 거절되는 버튼이 생기거나, 화면에 코드가 그대로 나온다). 선언에서 멈춘다.
        label_set = {str(label) for label in labels}
        for old_name, new_name in (field.get("legacy") or {}).items():
            if str(new_name) not in label_set:
                raise TaskError(f"{pid}: 필드 {field['id']}의 legacy {old_name} → {new_name}: 옮길 값이 허용값이 아닙니다.")
            if str(old_name) in label_set:
                raise TaskError(f"{pid}: 필드 {field['id']}의 legacy {old_name}는 이미 허용값입니다 — 옛 이름이 아닙니다.")
        slashed = [label for label in label_set if "/" in label]
        if slashed:
            # 굵은 범주(실행이 «이 가운데 하나»라고만 말함)를 «A/B»로 잇는다 — 코드에 «/»가 있으면 화면이 그것을 범주로 잘못 가른다.
            raise TaskError(f"{pid}: 필드 {field['id']}의 허용값에 «/»가 있습니다({', '.join(slashed)}) — «|»처럼 쓸 수 없습니다.")
        if field.get("unknownLabel") is not None and str(field["unknownLabel"]) not in label_set:
            raise TaskError(f"{pid}: 필드 {field['id']}의 unknownLabel {field['unknownLabel']}가 허용값이 아닙니다.")
        # 운영팀이 읽는 이름은 선택이 아니다 — 없으면 화면 버튼과 AI 문장이 영문 코드로 나가고, 코드 경고도 이름 없는 코드를 놓친다.
        # 참·거짓·정수 필드는 값 자체가 읽히므로 이름표를 요구하지 않는다.
        if not field.get("name"):
            raise TaskError(f"{pid}: 필드 {field['id']}에 운영팀이 읽을 이름(name)이 없습니다.")
        unnamed = sorted(label_set - set(map(str, field.get("labelNames") or {})))
        # 참·거짓은 뜻이 필드마다 달라(«true»가 «유광 있음»인지 운영팀은 모른다) 이름표가 필요하다. 정수만 면제한다.
        if unnamed and field.get("valueType", "string") in ("string", "boolean"):
            raise TaskError(f"{pid}: 정의 문서 `## {field['id']}`의 `### 허용값`에서 이름이 빠진 값이 있습니다: {', '.join(unnamed)} — "
                           f"`{VALUE_LINE_SHAPE}`처럼 코드 뒤에 사람이 읽는 이름을 적어 주세요.")
        stray = sorted(set(map(str, field.get("labelNames") or {})) - label_set)
        if stray:
            raise TaskError(f"{pid}: 필드 {field['id']}의 labelNames에 허용값이 아닌 이름표가 있습니다: {', '.join(stray)}")
        # 이름표가 겹치면 화면에 같은 이름의 버튼이 둘 생기고, AI 문장의 이름이 어느 값인지 가릴 수 없다.
        by_name: dict[str, list[str]] = {}
        for code, shown_name in (field.get("labelNames") or {}).items():
            by_name.setdefault(str(shown_name), []).append(str(code))
        twins = [f"{shown_name}({', '.join(codes)})" for shown_name, codes in by_name.items() if len(codes) > 1]
        if twins:
            raise TaskError(f"{pid}: 정의 문서 `## {field['id']}`의 `### 허용값`에 같은 이름이 둘 이상입니다: {'; '.join(twins)}")
        # 이름이 같은 필드의 다른 코드와 글자가 같으면, 말로 받은 «LOW»가 코드 LOW인지 이름 LOW(다른 코드)인지 가릴 수 없다.
        crossed = sorted(f"{code}→{shown_name}" for code, shown_name in (field.get("labelNames") or {}).items()
                         if str(shown_name) in label_set and str(shown_name) != str(code))
        if crossed:
            raise TaskError(f"{pid}: 필드 {field['id']}의 이름표가 다른 허용값 코드와 같습니다: {', '.join(crossed)}")
    by_id = {field["id"]: field for field in fields}
    for field in fields:
        # 이름이 다른 필드의 id와 같으면 «sheen»이 id sheen인지 이름 sheen(다른 필드)인지 가릴 수 없다 — 답이 다른 칸에 적힌다.
        if field.get("name") in by_id and field.get("name") != field["id"]:
            raise TaskError(f"{pid}: 필드 {field['id']}의 이름 «{field['name']}»이 다른 필드의 id와 같습니다.")
    for constraint in task.get("constraints") or []:
        if not (constraint.get("require") or constraint.get("forbid")):
            raise TaskError(f"{pid}: 제약 {constraint.get('id')}에는 require(이 값들 중 하나)나 forbid(이 값은 안 됨)가 있어야 합니다.")
        for part in ("when", "require", "forbid"):
            for name in (constraint.get(part) or {}):
                if name not in seen:
                    raise TaskError(f"{pid}: 제약 {constraint.get('id')}가 선언되지 않은 필드 {name}를 씁니다.")
        # 제약의 값도 필드의 규칙으로 옮긴다. 옮기지 않고 문자열로만 비교하면 참·거짓·순서만 다른 집합은
        # 영영 걸리지 않고, 오타 라벨은 그 조건의 모든 줄을 모순으로 올린다.
        normalized_when: dict[str, str] = {}
        for name, value in (constraint.get("when") or {}).items():
            if isinstance(value, list) and not is_many(by_id[name]):
                raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 when.{name}은 값 하나여야 합니다.")
            try:
                canonical = normalize(by_id[name], value)
            except TaskError as error:
                raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 when.{name} — {error}") from error
            if not in_range(by_id[name], canonical):
                raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 when.{name}={value!r}가 허용값이 아닙니다.")
            normalized_when[name] = canonical  # type: ignore[assignment]
        normalized_require: dict[str, list[str]] = {}
        for name, allowed in (constraint.get("require") or {}).items():
            if not isinstance(allowed, list) or not allowed:
                raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 require.{name}은 허용 값 목록이어야 합니다.")
            values = []
            for value in allowed:
                try:
                    canonical = normalize(by_id[name], value)
                except TaskError as error:
                    raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 require.{name} — {error}") from error
                if not in_range(by_id[name], canonical):
                    raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 require.{name}에 허용값이 아닌 {value!r}가 있습니다.")
                values.append(canonical)
            normalized_require[name] = values
        # forbid는 «이 값만은 안 된다» — 나머지 전부를 require에 나열하면 정책에 값을 더할 때 그 목록이 따라가지 못해 새 값이 모두 모순이 된다.
        normalized_forbid: dict[str, list[str]] = {}
        for name, banned in (constraint.get("forbid") or {}).items():
            if not isinstance(banned, list) or not banned:
                raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 forbid.{name}은 값 목록이어야 합니다.")
            values = []
            for value in banned:
                try:
                    canonical = normalize(by_id[name], value)
                except TaskError as error:
                    raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 forbid.{name} — {error}") from error
                if not in_range(by_id[name], canonical):
                    raise TaskError(f"{pid}: 제약 {constraint.get('id')}의 forbid.{name}에 허용값이 아닌 {value!r}가 있습니다.")
                values.append(canonical)
            normalized_forbid[name] = values
        constraint["_when"] = normalized_when
        constraint["_require"] = normalized_require
        constraint["_forbid"] = normalized_forbid
    # 판독자와 반론자가 읽는 유일한 기준. 필드마다 절이 없으면 판독자가 상식으로 채운다.
    missing = sorted(seen - definition_sections(definitions))
    if missing:
        raise TaskError(f"{pid}: 정의 문서에 필드 절(`## <필드ID>`)이 없습니다: {', '.join(missing)}")
    images = task.get("images") or {}
    if images.get("tileRoles") and images.get("preTiledRoles"):
        overlap = set(images["tileRoles"]) & set(images["preTiledRoles"])
        if overlap:
            raise TaskError(f"{pid}: 같은 역할을 «잘라야 할 원본»과 «이미 잘린 조각»으로 함께 선언했습니다: {overlap}")
    declared_roles = {str(role) for key in ("roles", "tileRoles", "preTiledRoles") for role in images.get(key) or []}
    # 역할 거르기(roles)를 선언했을 때만 본다 — 선언이 없으면 어떤 종류든 올 수 있다.
    stray_roles = sorted(set(map(str, images.get("roleNames") or {})) - declared_roles) if images.get("roles") else []
    if stray_roles:
        raise TaskError(f"{pid}: images.roleNames에 선언되지 않은 사진 종류가 있습니다: {', '.join(stray_roles)}")
    if images.get("preTiledRoles") and not images.get("preTiledRule"):
        raise TaskError(f"{pid}: 이미 잘린 조각(preTiledRoles)은 어느 규칙으로 잘렸는지(preTiledRule)를 밝혀야 합니다.")
    if images.get("preTiledRule"):
        try:
            tile_rule.rule_named(images["preTiledRule"])
            tile_rule.decoder_named(images["preTiledRule"])
        except ValueError as error:
            raise TaskError(f"{pid}: preTiledRule이 알려진 타일 규칙 판이 아닙니다 — {error}") from error
    if images.get("matchField") and not images.get("entryField"):
        raise TaskError(f"{pid}: images.matchField를 쓰려면 목록 안의 대조 열(entryField)도 선언해야 합니다.")
    if images.get("matchField") and images.get("tileRoles") and not images.get("tileRule"):
        # GT가 가리키는 조각 이름(D02T03)은 그 GT를 만든 실행이 매겼다. 그 판·디코더로 잘라야 같은 조각이다.
        raise TaskError(f"{pid}: 조각 단위 GT(matchField + tileRoles)는 그 조각 이름을 매긴 규칙(images.tileRule)을 밝혀야 합니다.")
    if images.get("tileRule"):
        # 선언했으면 늘 검사한다 — 준비 중(화면을 지운 뒤)에 처음 읽으면 파이썬 오류로 멈춘다.
        try:
            tile_rule.rule_named(images["tileRule"])
            tile_rule.decoder_named(images["tileRule"])
        except ValueError as error:
            raise TaskError(f"{pid}: images.tileRule이 알려진 타일 규칙이 아닙니다 — {error}") from error
    if (task.get("authority") or {}).get("default") not in (None, "trusted", "reference", "unknown"):
        raise TaskError(f"{pid}: authority.default는 trusted·reference·unknown 가운데 하나여야 합니다.")
    for grade in ("trusted", "reference"):
        for pattern in (task.get("authority") or {}).get(grade) or []:
            try:
                re.compile(str(pattern))
            except re.error as error:
                raise TaskError(f"{pid}: authority.{grade}의 패턴 {pattern!r}가 정규식이 아닙니다 — {error}") from error
    if "agents" in task:
        # 과제는 판독자·반론자를 고르지 않는다 — 역할 프롬프트는 모든 과제에 하나다. 과제마다 프롬프트를 고치기 시작하면
        # 과제가 늘수록 프롬프트가 늘고, 판단 기준이 정의 문서와 프롬프트 두 곳에 갈린다.
        raise TaskError(f"{pid}: gtTask.agents는 받지 않습니다 — 판독 프롬프트는 모든 과제에 공통입니다. "
                        f"판단을 바꾸려면 정의 문서({task.get('definitions')})의 그 칸 절에 적으세요.")
    for name in AGENTS.values():
        check_read_only_agent(pid, name)
    upstream = task["gt"].get("upstream")
    if upstream is not None and (not isinstance(upstream, dict) or not upstream.get("kind")):
        raise TaskError(f"{pid}: gt.upstream은 {{kind, note, mirrorFields}} 객체여야 합니다.")
    row_check = (upstream or {}).get("rowCheck")
    if row_check is not None:
        locator_names = set(map(str, (upstream or {}).get("locatorFields") or []))
        if not isinstance(row_check, dict) or not {str(row_check.get("row")), str(row_check.get("cell"))} <= locator_names:
            raise TaskError(f"{pid}: gt.upstream.rowCheck는 locatorFields 안의 두 열 {{row, cell}}이어야 합니다.")
        if bool(row_check.get("sheet")) != bool(row_check.get("rowSheet")) or (
                row_check.get("sheet") and str(row_check["sheet"]) not in locator_names):
            raise TaskError(f"{pid}: gt.upstream.rowCheck의 sheet(탭 열, locatorFields 안)와 rowSheet(행 번호가 속한 탭)는 함께 적습니다.")
    if (upstream or {}).get("mirrorFields") and len(fields) > 1:
        # 거울 열은 과제 전체에 하나다. 필드가 여럿이면 한 필드를 고칠 때 다른 필드의 답이 그 열을 덮는다.
        raise TaskError(f"{pid}: gt.upstream.mirrorFields는 필드가 하나인 과제에서만 쓸 수 있습니다.")
    # 답이 들어 있거나 답을 가리키는 열. 판독자에게 줄 맥락·글로 선언하면 눈가림이 풀리므로 선언에서 막는다.
    answer_columns = {str(field.get("gtField") or field["id"]) for field in fields}
    answer_columns |= {str(field["alternativesField"]) for field in fields if field.get("alternativesField")}
    answer_columns |= {str(task["gt"].get("sourceField") or "source"), str(task["gt"].get("fieldSourcesField") or "fieldSources")}
    answer_columns |= {str(name) for name in (upstream or {}).get("mirrorFields") or []}
    answer_columns |= {str(name) for name in (upstream or {}).get("locatorFields") or []}
    # 키 열도 막는다 — 판독자가 키를 알면 GT 파일을 Grep으로 찾아 답에 닿는다. 묶음(groupField)은 여러 줄이 나누는
    # 값이라 키를 가리키지 않으므로 막지 않는다. 제목(titleField — 판매 이름 같은)은 «직접 문구»의 근거일 수 있지만 한 줄을
    # 거의 가리키므로, 증거로 쓰려면 `titleAsEvidence: true`로 밝힌다(그 교환은 계약 «배치와 눈가림»에 적었다).
    key_columns = set(key_fields(task["keyField"]))
    for name in ("keyField", "joinField"):
        key_columns |= set(key_fields(images[name])) if images.get(name) else set()
    if task.get("titleField") and not task.get("titleAsEvidence"):
        key_columns |= set(key_fields(task["titleField"]))
    offered = set(images.get("contextFields") or []) | set((task.get("evidence") or {}).get("textFields") or [])
    leaked = sorted(offered & answer_columns)
    if leaked:
        raise TaskError(f"{pid}: 판독자에게 줄 맥락·글에 답 열(또는 답의 출처·거울·위치 열)이 있습니다: {', '.join(leaked)}")
    keyed = sorted(offered & key_columns)
    if keyed:
        raise TaskError(f"{pid}: 판독자에게 줄 맥락·글에 키 열이 있습니다: {', '.join(keyed)} — 키를 알면 GT에서 답을 찾을 수 있습니다"
                        + (" (제목 열을 증거로 쓰려면 gtTask.titleAsEvidence: true)" if task.get("titleField") in keyed else ""))
    if task.get("predictions") is not None:
        # 모델 실행 값은 검수자가 알 수도 다시 낼 수도 없는 값이다(추론은 개발자만 돌린다) — 화면에도, 후보를 고르는 데도 쓰지 않는다.
        raise TaskError(f"{pid}: gtTask.predictions는 더 쓰지 않습니다 — 검수자는 GT와 증거(사진·글)만 봅니다. 블록을 지워 주세요.")
    if images.get("matchField") and images.get("tileRoles") and not (
            images.get("sourceIndexField") or images.get("sourceListComplete")):
        # 운영의 D번호는 그 실행이 쓴 상세 원본 목록의 자리다. 색인이 한 장이라도 빠뜨리면 D가 조용히 밀린다.
        raise TaskError(f"{pid}: 조각 단위 GT는 원본의 D번호 열(images.sourceIndexField)을 밝히거나, 색인이 그 실행의 "
                        "상세 원본 전체를 같은 순서로 가진다고 선언(images.sourceListComplete: true)해야 합니다.")
    for field in fields:
        if field.get("alternativesField") and is_many(field):
            raise TaskError(f"{pid}: 필드 {field['id']}는 값 여럿이라 대체 정답 열을 둘 수 없습니다.")
    # 필드가 여럿이면 칸별 출처가 없을 때 줄 출처 하나로는 «어느 칸을 누가 고쳤나»를 적을 수 없다.
    if len(fields) > 1 and not (task["gt"].get("fieldSourcesField")):
        raise TaskError(f"{pid}: 필드가 여럿인 과제는 칸별 출처 열(gt.fieldSourcesField)을 선언해야 합니다 "
                        "— GT에 그 열이 아직 없어도 됩니다(없는 줄은 줄 출처를 따르고, 반영할 때 열이 생깁니다).")
    for field in fields:
        if "." in str(field.get("gtField") or field["id"]):
            raise TaskError(f"{pid}: 필드 {field['id']}의 GT 열 이름에 점이 있습니다. 중첩 열은 읽지 않습니다 — 평평한 열만.")
        if field.get("valueType", "string") not in ("string", "boolean", "integer"):
            raise TaskError(f"{pid}: 필드 {field['id']}의 valueType은 string·boolean·integer 중 하나여야 합니다.")
    # 답 열은 필드마다 따로이고, 키·출처·대체 정답·거울·위치 열과 겹치지 않는다. 겹치면 한 칸을 고칠 때 다른 뜻의 열(키면 원장이
    # 고아가 된다)을 조용히 덮는다.
    columns: dict[str, str] = {}
    for field in fields:
        column = str(field.get("gtField") or field["id"])
        if column in columns:
            raise TaskError(f"{pid}: 필드 {columns[column]}와 {field['id']}가 같은 GT 열({column})을 가리킵니다.")
        columns[column] = field["id"]
    reserved = {name: "키 열" for name in key_fields(task["keyField"])}
    reserved[str(task["gt"].get("sourceField") or "source")] = "출처 열(gt.sourceField)"
    reserved[str(task["gt"].get("fieldSourcesField") or "fieldSources")] = "칸별 출처 열(gt.fieldSourcesField)"
    for field in fields:
        if field.get("alternativesField"):
            reserved[str(field["alternativesField"])] = f"{field['id']}의 대체 정답 열"
    for part in ("mirrorFields", "locatorFields"):
        for name in (upstream or {}).get(part) or []:
            reserved[str(name)] = f"상류 {part} 열"
    for column, field_id in columns.items():
        if column in reserved:
            raise TaskError(f"{pid}: 필드 {field_id}의 GT 열({column})이 {reserved[column]}과 같습니다.")
    return task


def agent_tools(head: str) -> set[str]:
    """에이전트 머리말의 tools. 한 줄(`tools: Read, Grep`)과 YAML 목록(`tools:` 아래 `- Read`) 둘 다 읽는다."""
    lines = head.splitlines()
    for index, row in enumerate(lines):
        if not row.startswith("tools:"):
            continue
        inline = row.removeprefix("tools:").strip().strip("[]")
        if inline:
            return {tool.strip().strip("'\"") for tool in inline.split(",") if tool.strip()}
        tools = set()
        for item in lines[index + 1:]:
            if not item.strip().startswith("- "):
                break
            tools.add(item.strip()[2:].strip().strip("'\""))
        return tools
    return set()


# 판독자와 반론자 — 모든 과제가 같은 둘을 쓴다. 과제마다 다른 것은 정의 문서(무엇이 답인가)와 프로필(무엇을 읽는가)뿐이다.
AGENTS = {"reader": "gt-blind-reader", "defender": "gt-defender"}


def check_read_only_agent(pid: str, name: str) -> None:
    """판독자·반론자가 읽기 도구만 가진 등록된 에이전트인지 본다(규칙 6).
    쓰기 도구를 가진 에이전트가 판독하면 «읽기만 한다»가 지시로만 남는다."""
    for path in (PROJECT_ROOT / ".claude" / "agents").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        head = text.split("---")[1] if text.startswith("---") else ""
        if re.search(rf"^name:\s*{re.escape(name)}\s*$", head, re.M):
            tools = agent_tools(head)
            if tools and tools <= {"Read", "Grep", "Glob"}:
                return
            raise TaskError(f"{pid}: 에이전트 {name}는 읽기 도구(Read·Grep·Glob)만 가져야 합니다: {sorted(tools)}")
    raise TaskError(f"{pid}: 에이전트 {name}를 .claude/agents/에서 찾지 못했습니다.")


def check_one_ledger(profile: dict[str, Any]) -> None:
    """한 GT를 두 프로필이 고치면 원장이 둘이 된다. 다른 프로필의 판정이 들어가는 파일(사이클이면 계보 원본까지)과
    이 과제의 GT가 겹치면 멈춘다. 읽지 못한 프로필이 있으면 확인하지 못한 것이므로 멈춘다(열린 채 통과하지 않는다).
    준비만이 아니라 기록·내보내기·넣기 앞에서도 부른다 — 그사이 다른 프로필이 같은 GT를 가리키게 될 수 있다."""
    from catalog_profile import discover_profiles, load_profile

    mine = ledger_targets(profile)
    for path in discover_profiles():
        try:
            other = load_profile(path)
        except (ValueError, OSError) as error:
            raise TaskError(f"프로필 {path}을 읽지 못해 «한 GT에 원장 하나»를 확인할 수 없습니다: {error}") from error
        if other["id"] == profile["id"]:
            continue
        try:
            theirs = ledger_targets(other)
        except LineageUnknown as error:
            # 다른 속성의 외부 데이터가 이 컴퓨터에 없다고 모든 과제의 버튼을 막지 않는다. 이 과제의 GT가 실제로 있고
            # 그 없는 폴더 밖에 있으면 겹칠 수 없다. 그 폴더 안을 가리키면(파일이 없으니) 확인하지 못한 것이라 멈춘다.
            if error.folder is not None and all(path.exists() and error.folder not in path.parents for path in mine):
                continue
            raise
        overlap = mine & theirs
        if overlap:
            raise TaskError(f"GT 파일 {sorted(overlap)[0]}을 프로필 {other['id']}도 고칩니다. "
                            "한 GT에 판정 원장은 하나여야 합니다 — 그 GT는 그 프로필의 문으로 고칩니다.")


def clear_allowed(task: dict[str, Any]) -> bool:
    """«비워야 한다»를 받을 수 있는가. 원본이 상류에서 다시 만들어지는데 상류가 빈칸을 «이전 값 유지»로 읽으면,
    비운 정정은 원본에 닿지 못하고 영영 다시 나온다. 상류가 빈칸을 받는다고 밝힐 때만 연다."""
    upstream = task["gt"].get("upstream")
    return not upstream or bool(upstream.get("acceptsEmpty"))


def field_map(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["id"]: field for field in task["fields"]}


def is_many(field: dict[str, Any]) -> bool:
    return field.get("cardinality") == "many"


def _scalar(field: dict[str, Any], value: Any) -> str:
    """값 하나를 라벨 문자열로. 참·거짓·정수 열은 선언(valueType)이 있어야 받는다 — 조용히 문자열로 바꾸면
    내보낼 때 원본의 모양이 바뀐다."""
    kind = field.get("valueType", "string")
    if isinstance(value, bool):
        if kind != "boolean":
            raise TaskError(f"필드 {field['id']}에 참·거짓 값이 있습니다. valueType: boolean을 선언하세요.")
        return "true" if value else "false"
    if isinstance(value, int):
        if kind != "integer":
            raise TaskError(f"필드 {field['id']}에 정수 값이 있습니다. valueType: integer를 선언하세요.")
        return str(value)
    if not isinstance(value, str):
        raise TaskError(f"필드 {field['id']}에 읽을 수 없는 값이 있습니다: {value!r}")
    return value


def _odd(value: Any) -> str:
    """형이 틀린 GT 값 한 칸을 허용값이 될 수 없는 문자열로. 그 칸은 «범위 밖»으로 사람 앞에 온다 —
    한 줄의 오타가 과제 전체를 멈추면 운영팀은 어느 줄인지도 모른 채 개발자를 기다린다."""
    kind = "참·거짓" if isinstance(value, bool) else "정수" if isinstance(value, int) else "형이 틀린 값"
    return f"<{kind} {json.dumps(value, ensure_ascii=False)}>"


def normalize(field: dict[str, Any], value: Any, legacy: bool = True, lenient: bool = False) -> str | None:
    """옛 값을 지금 값으로 옮기고 한 모양으로 만든다. 비교는 늘 옮긴 뒤에 한다.

    `legacy=False`는 GT 밖에서 온 값(사람이 말로 준 값 같은)에 쓴다 — GT의 옛 이름표는 **GT의** 옛 어휘다.
    `lenient=True`는 GT를 읽을 때 — 형이 틀린 칸 하나는 멈추지 않고 «범위 밖»으로 올린다(`_odd`)."""
    table = (field.get("legacy") or {}) if legacy else {}
    if value is None or value == "" or value == []:
        return None

    def scalar(part: Any) -> str:
        if not lenient:
            return _scalar(field, part)
        try:
            return _scalar(field, part)
        except TaskError:
            return _odd(part)

    if is_many(field):
        parts = [scalar(part) for part in value] if isinstance(value, list) else scalar(value).split(MANY_SEPARATOR)
        cleaned = sorted({str(table.get(part, part)) for part in parts if part})
        return MANY_SEPARATOR.join(cleaned) or None
    if isinstance(value, list):
        # 값 하나 필드에 목록이 왔다. 모양이 선언과 다르다 — 그대로 문자열로 두면 범위 밖으로 잡힌다.
        return json.dumps(value, ensure_ascii=False)
    text = scalar(value)
    return str(table.get(text, text))


def in_range(field: dict[str, Any], value: str | None) -> bool:
    """허용값 안인가. 값 여럿 필드는 조각마다 본다."""
    if value is None:
        return False
    labels = {str(label) for label in field["labels"]}
    parts = value.split(MANY_SEPARATOR) if is_many(field) else [value]
    return all(part in labels for part in parts)


def export_value(field: dict[str, Any], value: str | None, original: Any = None) -> Any:
    """원장 모양 → 원본 GT 모양. 값 여럿은 목록으로, 참·거짓·정수는 원래 형으로 되돌린다.
    빈 값은 원본 칸의 모양을 따른다 — 목록이던 칸을 비우면 null이 아니라 빈 목록이다."""
    if value is None:
        if is_many(field) and isinstance(original, list):
            return []
        return "" if (is_many(field) and isinstance(original, str)) else None
    kind = field.get("valueType", "string")
    # 원본 칸이 참·거짓·정수를 글자("2"·"true")로 적어 두었으면 글자 그대로 되돌린다 — 형을 바꿔 쓰면
    # 사람이 «유지»만 눌러도 그 줄의 형이 바뀌어, 원본을 읽는 쪽이 섞인 형을 받는다.
    # 단, 원본 글자가 허용값이 아니었으면(«yes» 같은 형 오류) 선언된 형으로 고친다 — 오류를 고치는 정정이 오류를 다시 쓰지 않게.
    parts = original if isinstance(original, list) else [original]
    if (original not in (None, [], "") and all(isinstance(part, str) for part in parts)
            and in_range(field, normalize(field, original, lenient=True))):
        kind = "string"

    def typed(part: str) -> Any:
        if kind == "boolean":
            return part == "true"
        if kind == "integer":
            return int(part)
        return part

    if is_many(field) and isinstance(original, str) and original:
        # 값 여럿을 «A|B» 글자로 적는 GT도 있다. 목록으로 바꿔 쓰면 한 번 고친 줄만 목록이 되어 형이 섞인다.
        return value
    return [typed(part) for part in value.split(MANY_SEPARATOR)] if is_many(field) else typed(value)


def raw_differs(field: dict[str, Any], row: dict[str, Any]) -> bool:
    """원본 열의 날 값이 정규화한 값과 다른가(옛 이름·순서만 다른 집합 등). 유지 판정도 열을 다시 써야 하는 자리."""
    raw = row.get(field.get("gtField") or field["id"])
    return raw not in (None, "", []) and raw != export_value(field, normalize(field, raw, lenient=True), raw)


def gt_value(task: dict[str, Any], field: dict[str, Any], row: dict[str, Any]) -> str | None:
    return normalize(field, row.get(field.get("gtField") or field["id"]), lenient=True)


def gt_source(task: dict[str, Any], field: dict[str, Any], row: dict[str, Any]) -> str:
    """필드별 출처가 있으면 그것, 없으면 줄의 출처."""
    spec = task["gt"]
    per_field = row.get(spec.get("fieldSourcesField") or "fieldSources")
    if isinstance(per_field, dict) and per_field.get(field["id"]):
        return str(per_field[field["id"]])
    return str(row.get(spec.get("sourceField") or "source") or "")


def correction_prefix(task: dict[str, Any]) -> str:
    """이 하네스가 내보낸 정정의 출처 앞머리. 선언이 없으면 과제 ID로 만든다 — 상류 하네스의 이름 규약을 엔진이 알지 않게."""
    return str(task.get("correctionSourcePrefix") or f"GT_REVIEW_{str(task.get('_profileId') or 'TASK').upper().replace('-', '_')}")


def authority(task: dict[str, Any], source: str) -> str:
    """출처가 사람 확정인가, 참고 등급인가. 선언된 패턴으로 가른다 — 모르면 `UNKNOWN`.
    단 이 하네스가 내보낸 정정의 출처(사람 판정)는 선언과 무관하게 사람 확정이다 — 등급 패턴에 그 앞머리를 빠뜨리면
    사람이 고친 칸이 다음 준비부터 «등급 모름»으로 떨어진다."""
    if source and str(source).startswith(correction_prefix(task) + "_"):
        return "TRUSTED"
    spec = task.get("authority") or {}
    for grade in ("trusted", "reference"):
        for pattern in spec.get(grade) or []:
            if re.search(pattern, source or ""):
                return grade.upper()
    # 출처가 없거나 어느 패턴에도 안 맞는 칸의 등급 — 출처 열이 없는 손 라벨 GT는 «reference»로 두면
    # 채워진 칸도 GT를 모르는 눈이 다시 본다(GT_REFERENCE_ONLY). 선언이 없으면 모름.
    return str(spec.get("default") or "unknown").upper()


def load_gt_rows(profile: dict[str, Any], task: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """원본 순서 그대로 (키, 줄). 키가 비었거나 겹치면 멈춘다 — 원장의 한 칸이 어느 줄인지 모호해진다."""
    path = resolve(profile, task["gt"])
    if not path.is_file():
        raise TaskError(f"GT 파일이 없습니다: {path}")
    fields = key_fields(task["keyField"])
    rows: list[tuple[str, dict[str, Any]]] = []
    seen: dict[str, int] = {}
    empty: list[int] = []
    for number, row in enumerate(read_jsonl(path), 1):
        key = key_of(row, fields)
        if key is None:
            empty.append(number)
            continue
        if key in seen:
            raise TaskError(f"GT 키가 겹칩니다: {key} ({path} {seen[key]}번째 줄과 {number}번째 줄). "
                            "키 열을 늘려 복합 키로 선언하세요(keyField에 목록).")
        seen[key] = number
        rows.append((key, row))
    if empty:
        raise TaskError(f"GT에 키({' + '.join(fields)})가 빈 줄이 있습니다: {path} {empty[:5]}번째 줄")
    return rows


def load_gt(profile: dict[str, Any], task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(load_gt_rows(profile, task))


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def row_alternatives(field: dict[str, Any], row: dict[str, Any]) -> set[str | None]:
    if not field.get("alternativesField"):
        return set()
    raw = row.get(field["alternativesField"]) or []
    return {normalize(field, item, lenient=True) for item in (raw if isinstance(raw, list) else [raw])}


def effective_row(task: dict[str, Any], row: dict[str, Any], ledger_for_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """원본 줄에 사람이 이미 고친 칸을 얹은 줄. 제약은 이 줄로 따진다 — 한쪽만 고쳐도 모순이 풀렸는지,
    고친 값이 새 모순을 만들지 않았는지를 보려면 원본만으로는 안 된다."""
    fields = field_map(task)
    patched = dict(row)
    for field_id, entry in ledger_for_key.items():
        spec = fields.get(field_id)
        if not spec or entry.get("decision") not in ("CORRECT", "CLEAR"):
            continue
        if gt_value(task, spec, row) != entry.get("before"):
            continue  # 그 뒤 원본이 바뀌었다. 옛 판정을 얹지 않는다
        patched[spec.get("gtField") or field_id] = export_value(spec, entry.get("after"))
    return patched


def ledger_value(field: dict[str, Any], value: Any) -> Any:
    """원장에 적힌 값을 지금 규칙으로 읽는다 — 옛 코드는 `legacy`로 새 코드가 된다. 정책에서 코드를 바꾸고 legacy에 옛 코드를
    적었을 때, 지난 판정이 «바뀐 값»·«정책에서 빠진 값»으로 다시 올라오지 않게."""
    if value is None:
        return None
    try:
        return normalize(field, value, legacy=True, lenient=True)
    except TaskError:
        return value


def read_through_legacy(task: dict[str, Any], latest: dict[tuple[str, str], dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """칸마다 마지막 판정의 before·after를 `ledger_value`로 읽은 사본. 원장 파일은 그대로다."""
    fields = field_map(task)
    return {cell: ({**entry, "before": ledger_value(fields[cell[1]], entry.get("before")),
                    "after": ledger_value(fields[cell[1]], entry.get("after"))} if cell[1] in fields else entry)
            for cell, entry in latest.items()}


def violations(task: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    """선언된 제약을 GT 한 줄에 대 본다. 제약은 «이 칸이 이 값이면 저 칸은 이 값들 중 하나»다.

    값 여럿 필드는 **정렬해 이은 값 전체**로 대 본다(부분집합이 아니라 정확히 일치). 계약에 적힌 뜻이다."""
    fields = field_map(task)
    found: list[dict[str, Any]] = []
    for constraint in task.get("constraints") or []:
        # load_task가 필드 규칙으로 옮겨 둔 값. 옮기기 전 값(when·require)은 사람이 읽는 문장용이다.
        when = constraint.get("_when", constraint.get("when") or {})
        require = constraint.get("_require", constraint.get("require") or {})
        if not all(gt_value(task, fields[name], row) == value for name, value in when.items()):
            continue
        for name, allowed in require.items():
            actual = gt_value(task, fields[name], row)
            if actual is not None and actual not in allowed:
                found.append({"constraint": constraint.get("id"), "text": constraint.get("text") or "",
                              "field": name, "when": when, "allowed": list(allowed), "actual": actual})
        for name, banned in constraint.get("_forbid", constraint.get("forbid") or {}).items():
            actual = gt_value(task, fields[name], row)
            if actual is not None and actual in banned:
                allowed = [label for label in fields[name].get("labels") or [] if label not in banned]
                found.append({"constraint": constraint.get("id"), "text": constraint.get("text") or "",
                              "field": name, "when": when, "allowed": allowed, "actual": actual})
    return found


def quiet_cells(task: dict[str, Any], key: str, row: dict[str, Any],
                ledger: dict[tuple[str, str], dict[str, Any]], taken: set[str],
                settled_decisions: tuple[str, ...] = ("CORRECT", "CONFIRM", "LEAVE_EMPTY", "CLEAR")) -> list[dict[str, Any]]:
    """고른 건의 **후보가 아닌** 칸. 판독자는 필드를 전부 읽으므로(목록 모양으로 다투는 칸이 새지 않게), 그 판독을 버리지 않고
    GT와 대 본다 — 확신 있게 다르면 화면에 `BLIND_DISAGREES`로 올린다. 빈 칸(채우라는 선언 없음)과 사람이 이미 답한 칸은 뺀다."""
    cells = []
    for field_id, field in field_map(task).items():
        if field_id in taken:
            continue
        current = gt_value(task, field, row)
        decided = ledger.get((key, field_id))
        if current is None or (decided and decided.get("decision") in settled_decisions
                               and current in (decided.get("before"), decided.get("after"))):
            continue
        source = gt_source(task, field, row)
        cells.append({"key": key, "field": field_id, "current": current, "currentSource": source,
                      "authority": authority(task, source),
                      "signals": ["BLIND_DISAGREES"], "alternatives": sorted(a for a in row_alternatives(field, row) if a),
                      "contradictions": [], "previousDecision": decided["decisionId"] if decided else None,
                      "held": bool(decided and decided.get("decision") == "HOLD")})
    return cells


def candidates(
    task: dict[str, Any],
    gt_rows: dict[str, dict[str, Any]],
    ledger: dict[tuple[str, str], dict[str, Any]],
    settled_decisions: tuple[str, ...] = ("CORRECT", "CONFIRM", "LEAVE_EMPTY", "CLEAR"),
    blind: set[tuple[str, str, str | None]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """(키, 필드)마다 걸린 신호를 모은다. `blind`는 지난 화면에 «증거만 본 AI와 다름»으로 올라갔던 칸 — 같은 GT 값이면
    다시 후보다(보류·넘김 뒤에도 돌아오게).

    사람이 답한 칸은 다시 묻지 않는다 — **단, 사람이 본 값이 지금 GT와 같을 때만.** 답한 뒤 원본이
    바뀌었으면 그 답은 지금 값에 대한 답이 아니므로 다시 올린다(`GT_CHANGED_SINCE_DECISION`).
    """
    fields = field_map(task)
    found: list[dict[str, Any]] = []
    counts = {"alreadyDecided": 0, "outOfRange": 0}
    by_key: dict[str, dict[str, dict[str, Any]]] = {}
    for (cell_key, cell_field), entry in ledger.items():
        by_key.setdefault(cell_key, {})[cell_field] = entry
    for key, row in gt_rows.items():
        contradictions: dict[str, list[dict[str, Any]]] = {}
        # 사람이 고친 값을 얹은 줄로 모순을 본다. 모순인 칸은 «유지»로 답했어도 다시 올라온다 —
        # 둘 중 하나는 반드시 틀렸다는 뜻이라, 둘 다 맞다는 답은 답이 될 수 없다.
        for item in violations(task, effective_row(task, row, by_key.get(key, {}))):
            contradictions.setdefault(item["field"], []).append(item)
            for name in item["when"]:
                contradictions.setdefault(name, []).append(item)
        for field_id, field in fields.items():
            current = gt_value(task, field, row)
            source = gt_source(task, field, row) if current is not None else ""
            grade = authority(task, source) if current is not None else "NONE"
            decided = ledger.get((key, field_id))
            # 답한 뒤 원본이 «사람이 본 값»도 «사람이 고른 값»도 아니게 됐으면 다시 묻는다.
            # 고른 값과 같으면 정정이 원본에 반영된 것이다 — 그건 바뀐 것이 아니다.
            changed = bool(decided and decided.get("decision") in settled_decisions
                           and current not in (decided.get("before"), decided.get("after")))
            # 사람이 고른(유지한) 값이 지금 정책에 없다 — 정책에서 값을 뺐다. 그 답은 더 답이 아니다.
            chosen = (decided or {}).get("after") if (decided or {}).get("decision") in ("CORRECT", "CLEAR") else (decided or {}).get("before")
            policy_moved = bool(decided and decided.get("decision") in settled_decisions and chosen is not None
                                and not in_range(field, chosen))
            signals: list[str] = []
            if contradictions.get(field_id):
                signals.append("GT_SELF_CONTRADICTION")
            if current is not None and not in_range(field, current):
                signals.append("GT_OUT_OF_RANGE")
                counts["outOfRange"] += 1
            if changed:
                signals.append("GT_CHANGED_SINCE_DECISION")
            if policy_moved:
                signals.append("POLICY_CHANGED_SINCE_DECISION")
            alternatives = row_alternatives(field, row)
            if current is None and field.get("fillMissing"):
                signals.append("GT_MISSING")
            # 사람이 확인하지 않은 라벨은 GT를 모르는 눈이 한 번 본다 — 그 눈이 같은 값을 내면 agreed.jsonl이 다음 순서를 뒤로 민다.
            if current is not None and grade == "REFERENCE":
                signals.append("GT_REFERENCE_ONLY")
            if not signals and (key, field_id, current) in (blind or set()):
                signals.append("BLIND_DISAGREES")
            if not signals:
                continue
            if (decided and decided.get("decision") in settled_decisions and not changed and not policy_moved
                    and not contradictions.get(field_id) and "GT_OUT_OF_RANGE" not in signals):
                counts["alreadyDecided"] += 1
                continue
            found.append({"key": key, "field": field_id, "current": current, "currentSource": source,
                          "authority": grade, "signals": signals,
                          "alternatives": sorted(a for a in alternatives if a),
                          "contradictions": contradictions.get(field_id, []),
                          "previousDecision": decided["decisionId"] if decided else None,
                          "held": bool(decided and decided.get("decision") == "HOLD")})
    found.sort(key=lambda item: (min(SIGNALS[s]["rank"] for s in item["signals"]),
                                 AUTHORITY_ORDER[item["authority"]], item["key"], item["field"]))
    return found, counts


def group_items(task: dict[str, Any], found: list[dict[str, Any]], gt_rows: dict[str, dict[str, Any]],
                agreed: set[tuple[str, str, str | None]] | None = None,
                shown: dict[tuple[str, str, str | None], int] | None = None) -> list[dict[str, Any]]:
    """같은 키의 칸들을 한 건으로 묶는다. 사진 한 장을 필드마다 따로 읽히면 같은 눈이 여러 번 다른 결론을 낸다.

    `agreed`는 지난 판독에서 GT를 모르는 눈이 **지금과 같은 GT 값**을 독립적으로 낸 칸이다. 사람의
    판정이 아니므로 원장에 넣지 않지만, 같은 칸을 매번 맨 앞에 다시 올리면 «다음 거»가 앞으로 가지
    않는다. 그래서 그런 칸만 남은 건은 **뒤로 미룬다** — 버리지 않는다. 사람이 **보류**한 칸도 같다.
    보류한 건이 다음 배치의 맨 앞에 다시 오면 «다음 거»가 같은 화면을 되풀이한다.
    """
    agreed = agreed or set()
    # `shown`은 사람 앞에 놓였지만 답 없이 넘어간 칸 → 마지막으로 넘어간 차례다(«다음 거» → «네»). 같은 신호 등급 안에서
    # 한 번도 안 본 건 → 가장 오래전에 본 건 순으로 돈다. 등급을 넘지는 않는다 — 넘긴 모순이 뒤 등급 밑에 묻히지 않게.
    shown = shown or {}
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in found:
        by_key.setdefault(item["key"], []).append(item)
    group_field = task.get("groupField")
    items: list[dict[str, Any]] = []
    for key, cells in by_key.items():
        row = gt_rows.get(key) or {}
        fresh = [cell for cell in cells
                 if (cell["key"], cell["field"], cell["current"]) not in agreed and not cell.get("held")]
        items.append({
            "key": key,
            "group": str(row.get(group_field)) if group_field and row.get(group_field) is not None else None,
            "title": str(row.get(task.get("titleField") or "")) if task.get("titleField") else None,
            "rank": min(min(SIGNALS[s]["rank"] for s in cell["signals"]) for cell in cells),
            "authorityRank": min(AUTHORITY_ORDER[cell["authority"]] for cell in cells),
            "agreedOnly": not fresh,  # 이미 AI가 같다고 봤거나 사람이 보류한 칸만 남았다
            "lastShown": max((shown.get((cell["key"], cell["field"], cell["current"]), 0) for cell in cells), default=0),
            "cells": cells,
        })
    items.sort(key=lambda item: (item["agreedOnly"], item["rank"], item["lastShown"], item["authorityRank"],
                                 -len(item["cells"]), item["key"]))
    return items
