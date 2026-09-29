#!/usr/bin/env python3
"""판독과 반론을 칸마다 상태 하나로 만나게 하고, 사람이 답할 화면 한 장을 만든다.

## 상태는 규칙이 정한다 — 에이전트가 정하지 않는다

| GT | 판독(증거만 본 눈) | 반론(GT를 지키는 눈) | 상태 | 사람에게 |
|---|---|---|---|---|
| 있음 | GT와 같다 | — | `GT_HOLDS` | 한 번에 «모두 유지»할 수 있게 모아 둔다 |
| 있음 | 다르다 | 판독이 맞다 | `FIX_PROPOSED` | 고치자는 제안 |
| 있음 | 다르다 | GT가 맞다 · 못 가른다 | `CONTESTED` | 두 눈이 갈렸다. 증거(사진·글)를 직접 본다 |
| 없음 | 값을 냈다 | 판독이 맞다 | `FILL_PROPOSED` | 채우자는 제안 |
| 없음 | 값을 냈다 | 반대 · 못 가른다 | `CONTESTED` | |
| — | 못 읽었다 · 허용값 밖 | — | `NEEDS_HUMAN_LOOK` | 증거로도 못 가른다 |
| (증거 없음) | — | — | `NO_EVIDENCE` | 판독 없이 GT와 이유만 보고 가른다 |

판독이 GT와 같으면 반론을 부르지 않는다. 한 눈이 GT와 같은 답을 독립적으로 냈다는 것 자체가
GT를 지지하는 근거이고, 그 위에 반론을 붙이면 비용만 든다.

에이전트가 «고쳐야 한다»고 한 문장을 그대로 상태로 쓰지 않는 이유 — 판독자는 GT를 모르므로
고칠지 말지를 말할 수 없고, 반론자는 GT를 지키는 역할이라 한쪽으로 기운다. 상태는 둘이
**만난 자리**에서만 정해진다.

## 화면의 규칙

- 읽는 사람은 데이터 운영팀이다. 필드 ID 대신 프로필이 붙인 이름을, 신호 코드 대신 문장을 쓴다.
- 판정 버튼은 제안 옆에 있다. 읽던 자리에서 답한다. 두 눈이 갈린 칸은 양쪽 버튼의 무게가 같다.
- 화면은 열릴 때마다 원장을 읽어 «이미 답함»을 다시 그린다. 파일로 굳힌 화면이 원장과 어긋나면
  사람은 어디까지 했는지 모른다.
- 모든 판정은 화면이 보여 준 GT 값을 함께 보낸다. 그사이 원본이 바뀌었으면 기록기가 거절한다.
- 숫자는 이 렌더러가 방금 센 것만 쓴다. 문서에 옮겨 적지 않는다.
"""

from __future__ import annotations

import sys

import html
import urllib.parse
import json
import os
import re
from pathlib import Path
from typing import Any

from catalog_profile import PROJECT_ROOT
from gt_decisions import _write_atomic, answered_on_page, locked, reasked
from gt_task import (MANY_SEPARATOR, PURPOSE_PARTS, TaskError, authority, definition_policy, definition_texts, definition_value_notes, gt_source, gt_value,
                     in_range, load_gt, load_task, resolve)
from policy_prompt import decision_rules
from page_style import head

REVIEW_SCHEMA = "gt-review-v2"
STATUS = {
    "FIX_PROPOSED": {"order": 1, "name": "고치자는 제안",
                     "hint": "정답을 모른 채 증거만 본 AI가 GT와 다른 값을 냈고, GT 편에서 반론한 AI도 그 값이 맞다고 봤습니다."},
    "FILL_PROPOSED": {"order": 2, "name": "채우자는 제안",
                      "hint": "GT가 비어 있는 칸입니다. 증거만 본 AI가 값을 냈고 반론한 AI도 동의했습니다."},
    "CONTESTED": {"order": 3, "name": "의견이 갈림",
                  "hint": "두 AI가 서로 다른 답을 냈습니다. 증거를 직접 보고 골라 주세요."},
    "NEEDS_HUMAN_LOOK": {"order": 4, "name": "직접 봐야 함",
                         "hint": "AI가 확신하지 못했습니다. 증거가 부족하거나 경계에 걸린 사례입니다."},
    "NO_EVIDENCE": {"order": 5, "name": "증거 없음",
                    "hint": "이 건에는 사진도 글도 없어 AI가 보지 못했습니다. GT 값과 이유만 보고 판단해 주세요."},
    "NOT_READ": {"order": 6, "name": "AI가 못 읽음",
                 "hint": "이 건은 AI 판독이 돌아오지 않았습니다. Claude에게 «못 읽은 거 다시 봐줘»라고 말해 주세요."},
    "GT_HOLDS": {"order": 7, "name": "기본값 유지",
                 "hint": "정답을 모르는 AI가 증거만 보고 같은 값을 냈습니다."},
}
# 값이 이만큼 이하인 칸은 값마다 버튼을 둔다(넘으면 «다른 값…» 목록).
CHIP_LIMIT = 6
# 한 페이지에 보이는 건 수. 배치(프로필 `limit`)가 이보다 크면 화면이 페이지로 나눠 보인다 — AI는 배치를 한 번에 미리 읽는다.
PAGE_SIZE = 20
ACTIONABLE = ("FIX_PROPOSED", "FILL_PROPOSED", "CONTESTED", "NEEDS_HUMAN_LOOK", "NO_EVIDENCE")
CONTRADICTED_HINT = "AI는 GT와 같은 값을 냈지만, 같은 줄의 다른 칸과 말이 안 맞습니다. 두 칸을 함께 보고 골라 주세요."
# 기록용 정보의 GT 출처 — 운영팀에게는 등급으로 말하고, 원래 출처 이름은 마우스를 올리면 보인다.
AUTHORITY_SHORT = {"TRUSTED": "사람이 확정한 라벨", "REFERENCE": "사람 확인 전 라벨", "UNKNOWN": "출처를 모르는 라벨"}


def call_name(profile: dict[str, Any]) -> str:
    """과제 이름 — 메뉴·정책·골든셋·증분 화면이 모두 이 이름으로 과제를 부른다. 과제는 정책·골든셋·증분을 함께 가지므로
    이름에 «골든셋»을 넣지 않는다(옛 프로필이 «… 골든셋»으로 적었으면 꼬리를 뗀다 — 메뉴마다 이름이 달라지지 않게)."""
    name = str(profile.get("displayName") or profile.get("id") or "")
    return re.sub(r"\s*골든셋$", "", name) or name


def golden_call(profile: dict[str, Any]) -> str:
    """골든셋 검수를 Claude에게 부르는 말 — «<과제> 골든셋 반영해줘»·«… 다음 거». 증분 검수(«<과제> 증분 …»)와 섞이지 않게 «골든셋»을 붙인다."""
    return f"{call_name(profile)} 골든셋"


def flow_steps(profile: dict[str, Any]) -> list[dict[str, str]]:
    """버튼에서 GT가 실제로 바뀌기까지. 원본이 어디 있느냐(프로필 `gtTask.gt`)로 갈린다 — 화면과 첫 화면이 같은 문장을 쓴다.
    원본이 이 레포·이 컴퓨터의 파일이면 «반영하기» 버튼이 곧바로 넣는다. 상류(시트)가 원본이면 목록까지만 — 붙여 넣기는 사람이 한다."""
    spec = (profile.get("gtTask") or {}).get("gt") or {}
    upstream = spec.get("upstream") or {}
    name = golden_call(profile)
    if upstream:
        where = upstream.get("note") or "원천"
        return [{"say": "칸마다 버튼", "does": "판정만 기록 — GT는 그대로"},
                {"say": f"Claude에게 «{name} 반영해줘»", "does": "붙여 넣을 Excel 목록을 받음"},
                {"say": "직접: 목록 붙여 넣기", "does": f"{where}에 옮긴 뒤 개발자에게 알림"},
                {"say": "개발자가 새로 고침", "does": "이때 GT가 바뀜"}]
    steps = [{"say": "칸마다 버튼", "does": "판정만 기록 — GT는 그대로"},
             {"say": "«반영하기» 버튼", "does": "이 컴퓨터의 GT 파일에 바로 들어감(팀 GT는 그대로)"}]
    path = str(spec.get("path") or "")
    if spec.get("root") == "project" or path.startswith(".claude/"):
        steps.append({"say": "«올려줘»", "does": "팀 검토 요청 — 검토자가 승인(머지)하면 팀 GT가 바뀜"})
    return steps


def change_moment(profile: dict[str, Any]) -> str:
    """GT가 실제로 바뀌는 순간 한 문장 — 기록 뒤 안내·첫 화면·스킬이 같은 말을 쓴다."""
    spec = (profile.get("gtTask") or {}).get("gt") or {}
    upstream = spec.get("upstream") or {}
    if upstream:
        return f"목록을 {upstream.get('note') or '원천'}에 붙여 넣고 개발자가 새로 고치면 바뀝니다"
    path = str(spec.get("path") or "")
    if spec.get("root") == "project" or path.startswith(".claude/"):
        return "«반영하기»를 누르면 이 컴퓨터의 GT에 들어가고, «올려줘» 뒤 검토자가 승인하면 팀 GT가 바뀝니다"
    return "«반영하기»를 누르면 바뀝니다"


def _names_for_text(fields: dict[str, dict[str, Any]], field: dict[str, Any], column_names: dict[str, str]) -> dict[str, str]:
    """기준 문서의 코드(값 코드·필드 ID·`context.<열>`)를 화면 이름으로 — 운영팀은 코드를 모른다."""
    names: dict[str, str] = {}
    for other in fields.values():
        names.setdefault(str(other.get("id")), str(other.get("name") or other.get("id")))
        for code, label in (other.get("labelNames") or {}).items():
            names.setdefault(str(code), str(label))
    names.update({str(code): str(label) for code, label in (field.get("labelNames") or {}).items()})
    for column, label in column_names.items():
        names[f"context.{column}"] = str(label)
        names.setdefault(str(column), str(label))
    return names


# 코드 뒤의 조사는 코드의 소리에 맞춰 적혀 있다(`CODE`다). 이름으로 바꾸면 이름의 받침에 맞춰 다시 고른다(받침이 있으면 «이다»).
JOSA = {"이다": ("이다", "다"), "다": ("이다", "다"), "이나": ("이나", "나"), "나": ("이나", "나"), "이": ("이", "가"), "가": ("이", "가"),
        "은": ("은", "는"), "는": ("은", "는"), "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와"),
        "으로": ("으로", "로"), "로": ("으로", "로"), "이면": ("이면", "면"), "면": ("이면", "면")}


def josa(word: str, particle: str) -> str:
    pair = JOSA.get(particle)
    last = word[-1:] if word else ""
    if not pair or not ("가" <= last <= "힣"):
        return particle
    final = (ord(last) - 0xAC00) % 28
    if pair[0] == "으로":
        return "로" if final in (0, 8) else "으로"
    return pair[0] if final else pair[1]


def _ai(text: Any) -> str:
    """AI 문장의 영문 확신 코드(LOW·HIGH)를 사람 말로 — 판독기가 제 출력 형식의 낱말을 문장에 흘리곤 한다."""
    # 뒤에 붙은 조사는 바꾼 낱말에 맞춘다 — «LOW로»를 «낮음로»로 두지 않게.
    words = {"LOW": "낮음", "MEDIUM": "중간", "HIGH": "높음"}

    def swap(match: re.Match) -> str:
        word = words[match.group(1)]
        return word + (josa(word, match.group(2)) if match.group(2) else "")

    text = re.sub(r"(?<![A-Za-z])(LOW|MEDIUM|HIGH)(?![A-Za-z])(으로|로|이다|다|이나|나|이면|면|이|가|은|는|을|를|과|와)?", swap, str(text or ""))
    # 하네스 안쪽 낱말을 운영팀의 말로 — AI가 제 지시문의 낱말(정의 문서·판독자·검수 문답)을 문장에 흘리곤 한다. 조사는 바꾼 낱말에 맞춘다.
    for inner, plain in INNER_WORDS:
        text = re.sub(re.escape(inner) + r"(으로|로|이다|다|이나|나|이면|면|이|가|은|는|을|를|과|와)?",
                      lambda m, plain=plain: plain + (josa(plain, m.group(1)) if m.group(1) else ""), text)
    return text


# 긴 낱말부터 — «판독자»를 «판독»보다 먼저 바꾼다
INNER_WORDS = (("판독 값", "사진을 본 AI의 값"), ("판독값", "사진을 본 AI의 값"), ("정의 문서", "정책"), ("검수 문답", "사람이 답한 사례"), ("반론 AI", "GT가 맞는지 다시 본 AI"),
               ("판독자", "사진을 본 AI"), ("판독기", "사진을 본 AI"), ("이 문서", "정책"))


def _inline(text: str, names: dict[str, str]) -> str:
    # «열 이름(`context.x`)»처럼 이름 뒤에 코드를 괄호로 단 곳 — 코드를 이름으로 바꾸면 같은 말이 두 번 나온다.
    text = re.sub(r"([^\s`(]+)\(`([^`]+)`\)",
                  lambda m: m.group(1) if names.get(m.group(2)) == m.group(1) else m.group(0), text)
    text = re.sub(r"`([^`]+)`\(([^)]+)\)",
                  lambda m: f"`{m.group(1)}`" if names.get(m.group(1)) == m.group(2) else m.group(0), text)
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)

    def code(match: re.Match[str]) -> str:
        word = html.unescape(match.group(1))
        after = match.group(2) or ""
        if word not in names:
            return f"<code>{match.group(1)}</code>{after}"
        return f"<b>{html.escape(names[word])}</b>{josa(names[word], after) if after else ''}"
    particles = "|".join(sorted(JOSA, key=len, reverse=True))
    # 조사는 그 뒤가 한글이 아닐 때만 조사다(«은행»의 «은»이 아니게). 뒤에 무엇이 오든 백틱은 짝끼리만 묶는다 — 조사 목록에 없는 말(«의»)이
    # 뒤에 오면 짝이 어긋나 다음 백틱과 묶이던 일을 막는다.
    return re.sub(r"`([^`]+)`((?:" + particles + r")(?![가-힣]))?", code, out)


def _md(text: str, names: dict[str, str]) -> str:
    """정의 문서 한 절을 읽히는 모양으로 — 제목·목록·굵게·코드만. 원문(`**`·`###`)을 그대로 보이지 않는다."""
    parts: list[str] = []
    items: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            parts.append(f"<p>{_inline(' '.join(para), names)}</p>")
            para.clear()
        if items:
            parts.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()
    for raw in text.split("\n"):
        line = raw.rstrip()
        value = re.match(r"^- `([^`\s]+)`(?: ([^`—]*?))? — (.+)$", line)
        if line.startswith(">"):
            continue  # «> »로 시작하는 줄은 개발 메모 — 판독 AI는 읽고, 사람 화면에는 싣지 않는다
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip()
            parts.append(f'<p class="md-h">{_inline("들어올 수 있는 값" if heading == "허용값" else heading, names)}</p>')
        elif value:
            if para:
                flush()
            label = names.get(value.group(1)) or value.group(2) or value.group(1)
            items.append(f"<b>{html.escape(label)}</b> — {_inline(value.group(3), names)}")
        elif re.match(r"^\s*[-*]\s", line):
            if para:
                flush()
            items.append(_inline(re.sub(r"^\s*[-*]\s", "", line), names))
        elif line.startswith((" ", "\t")) and items:
            items[-1] += " " + _inline(line.strip(), names)
        elif not line.strip():
            flush()
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return "".join(parts)
NO_DEFENSE_HINT = "증거만 본 AI가 GT와 다른 값을 냈는데, 반론할 AI가 답을 돌려주지 않았습니다. 증거를 직접 보고 골라 주세요."


def cell_status(current: str | None, reading: dict[str, Any] | None, rebuttal: dict[str, Any] | None,
                labels: list[str], many: bool = False, alternatives: list[str] | None = None,
                contradicted: bool = False) -> str:
    if reading is None:
        return "NOT_READ"
    value = reading.get("value")
    # 값 여럿은 빈 조각을 빼고 본다 — 워크플로우의 canonical·agreed.jsonl과 같은 규칙(«WARM|»는 «WARM»).
    parts = [part for part in str(value).split(MANY_SEPARATOR) if part] if (value and many) else [value]
    if many and value and not parts:
        value = None
    if not value or any(part not in labels for part in parts) or reading.get("confidence") == "LOW":
        return "NEEDS_HUMAN_LOOK"
    if many:
        value = MANY_SEPARATOR.join(sorted(set(parts)))
    if current is not None and (value == current or value in (alternatives or [])):
        # 모순이 걸린 칸은 «GT가 맞다»로 묶지 않는다 — 같은 줄의 다른 칸과 말이 안 맞으니 사람이 봐야 한다.
        return "NEEDS_HUMAN_LOOK" if contradicted else "GT_HOLDS"
    if (rebuttal or {}).get("verdict") == "READER_RIGHT":
        return "FIX_PROPOSED" if current is not None else "FILL_PROPOSED"
    return "CONTESTED"


def ask_of(reading: dict[str, Any] | None, rebuttal: dict[str, Any] | None, labels: list[str], many: bool = False) -> dict[str, Any] | None:
    """판독(없으면 반론)이 이 칸에 남긴 `askHuman`을 화면·원장이 쓰는 모양으로 다듬는다.

    `question`은 이 상품을 떠나서도 통하는 경계 물음이다 — 사람의 답이 정책의 문답으로 옮겨 갈 수 있어야 한다.
    `here`는 이 사진에서 그 물음이 걸린 자리, `options`는 답마다 어느 허용값이 되는지. 허용값 밖의 선택지는 버린다 —
    화면이 그 답을 버튼으로 이을 수 없고, 정책에 옮기면 없는 값을 가르친다. 물음이 없으면 없는 것으로 본다."""
    for source in (reading, rebuttal):
        ask = (source or {}).get("askHuman")
        if not isinstance(ask, dict) or not str(ask.get("question") or "").strip():
            continue
        options = []
        for option in ask.get("options") or []:
            if not isinstance(option, dict) or not str(option.get("answer") or "").strip():
                continue
            value = str(option.get("value") or "")
            parts = [part for part in value.split(MANY_SEPARATOR) if part] if many else [value]
            if parts and all(part in labels for part in parts):
                options.append({"answer": str(option["answer"]).strip(), "value": value})
        shaped = {"question": str(ask["question"]).strip(), "here": str(ask.get("here") or "").strip(),
                  "imageIds": [str(i) for i in ask.get("imageIds") or []], "options": options}
        # 관찰 칸의 물음 — 어느 관찰 항목을 가르는 물음인지. 사람의 답(값)을 «그 항목 예/아니오»로 되돌려 다음 판독자의 사례로 줄 때 쓴다.
        if isinstance(ask.get("observe"), str) and ask["observe"].strip():
            shaped["observe"] = ask["observe"].strip()
        return shaped
    return None


def legacy_ask(item: dict[str, Any], fields: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """옛 배치의 메모에서 사람에게 묻는 문장(최대 둘)을 고르고, 그 물음이 걸린 칸을 찾는다. 칸마다 표준 물음이 있으면 쓰지 않는다.
    화면(칸에 붙이기)과 `qa`(그 판정이 어느 물음에 대한 답이었나)가 같은 함수를 지난다 — 둘이 다른 칸을 가리키면 안 된다."""
    if any(cell.get("ask") and not cell["ask"].get("legacy") for cell in item["cells"]):
        return None, ""
    asks = [part.strip() for part in re.split(r"(?<=[.?!])\s+", _ai(item.get("note") or ""))
            if part.strip() and ("?" in part or re.search(r"확인하면|확인해|확인되|확인이 필요|직접 보고|판단해|봐야|갈리는|같은지|맞는지|정하지 않았", part))
            and not re.search(r"(없습니다|없다|없었습니다)\.?$", part.strip()) and len(part.strip()) >= 15
            and not re.search(r"점이 (하나|둘|두 가지|몇 가지)|점은 (하나|둘|두 가지)", part)][:2]
    if not asks:
        return None, ""
    text = " ".join(asks)
    return _ask_target(text, item["cells"], fields), text


def _ask_target(text: str, cells: list[dict[str, Any]], fields: dict[str, Any]) -> dict[str, Any] | None:
    """옛 배치(표준 물음이 없던 판독)의 물음이 어느 칸 이야기인지 고른다. 칸 이름이 물음에 있으면 그 칸, 없으면 그 칸 판독 문장과
    겹치는 말이 가장 많은 칸(«오른팔이 옆선을…»은 그 말을 쓴 칸의 판독에 있다), 그래도 없으면 사람이 직접 봐야 할 칸이 하나일 때 그 칸.
    못 고르면 None — 맨 위에 둔다. 새 배치는 칸마다 `askHuman`으로 받아 이 추측이 없다."""
    words = lambda value: {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", value or "")}
    asked = words(text)
    best, best_score = None, 0
    for cell in cells:
        field = fields.get(cell["field"]) or {}
        name = str(field.get("name") or "")
        score = 100 if name and name in text else 0
        score += len(asked & words((cell.get("reading") or {}).get("observation")))
        if score > best_score:
            best, best_score = cell, score
    if best is not None and best_score >= 2:
        return best
    looking = [cell for cell in cells if cell["status"] in ("CONTESTED", "NEEDS_HUMAN_LOOK", "NO_EVIDENCE")]
    return looking[0] if len(looking) == 1 else None


def merge(worklist: dict[str, Any], sweep: dict[str, Any] | None, ledger: list[dict[str, Any]]) -> dict[str, Any]:
    fields = {field["id"]: field for field in worklist.get("fields") or []}
    returned = {item.get("id"): item for item in (sweep or {}).get("items") or []}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in ledger:
        latest[(str(entry["key"]), str(entry["field"]))] = entry
    items: list[dict[str, Any]] = []
    for item in worklist.get("items") or []:
        result = returned.get(item["id"]) or {}
        readings: dict[str, Any] = {}
        for r in (result.get("reading") or {}).get("readings") or []:
            earlier = readings.get(r.get("field"))
            if earlier is not None and str(earlier.get("value")) != str(r.get("value")):
                # 한 칸에 두 값을 냈다 — 판독이 스스로 갈렸다. 한쪽을 조용히 고르지 않고 확신 낮음으로 사람에게 넘긴다.
                r = {**r, "confidence": "LOW", "observation": f"판독이 이 칸에 두 값을 냈습니다({earlier.get('value')} · {r.get('value')}). "
                     + str(r.get("observation") or "")}
            readings[r.get("field")] = r
        rebuttals = {r.get("field"): r for r in (result.get("defense") or {}).get("rebuttals") or []}
        # 후보가 아닌 칸은 판독이 확신 있게(HIGH) 허용값 안의 다른 값을 냈을 때만 올린다 — 워크플로우가 반론을 부르는 조건과 같다.
        promoted = []
        for cell in item.get("quiet") or []:
            field = fields.get(cell["field"]) or {"labels": []}
            reading = readings.get(cell["field"])
            if not reading or reading.get("confidence") != "HIGH" or not reading.get("value"):
                continue
            many = field.get("cardinality") == "many"
            status = cell_status(cell.get("current"), reading, rebuttals.get(cell["field"]),
                                 [str(label) for label in field["labels"]], many, cell.get("alternatives"))
            if status in ("FIX_PROPOSED", "CONTESTED"):
                promoted.append(cell)
        cells = []
        for cell in [*item["cells"], *promoted]:
            field = fields.get(cell["field"]) or {"labels": []}
            many = field.get("cardinality") == "many"
            reading = readings.get(cell["field"])
            rebuttal = rebuttals.get(cell["field"])
            status = ("NO_EVIDENCE" if item.get("noEvidence") else
                      cell_status(cell.get("current"), reading, rebuttal, [str(label) for label in field["labels"]], many,
                                  cell.get("alternatives"), "GT_SELF_CONTRADICTION" in cell.get("signals", [])))
            value = reading.get("value") if reading else None
            if value and many:
                # 빈 조각을 빼고 정렬 — cell_status·워크플로우·agreed.jsonl과 같은 규칙.
                value = MANY_SEPARATOR.join(sorted({part for part in str(value).split(MANY_SEPARATOR) if part})) or None
            # 판독이 GT가 아니라 «같이 맞다고 적어 둔 값»을 냈다 — GT와 같은 값을 냈다고 말하면 거짓이다.
            alternative = status == "GT_HOLDS" and value is not None and value != cell.get("current")
            cells.append({
                **cell,
                # AI가 사람에게 묻는 말 — 칸에 묶인 표준 모양(`askHuman`)으로만 받는다. 메모에서 문장을 골라내지 않는다.
                "ask": ask_of(reading, rebuttal, [str(label) for label in field["labels"]], many),
                "status": status,
                "proposal": value if status in ("FIX_PROPOSED", "FILL_PROPOSED", "CONTESTED") else None,
                # 사진 이름은 AI가 본 이름(P01) 그대로 둔다. 화면의 사진 설명에도 같은 이름이 있다 —
                # 바꿔 적으면 AI 문장 속 «P01에서…»를 화면에서 찾을 수 없다.
                "reading": reading,
                "rebuttal": rebuttal,
                "alternative": alternative,
                # 판독은 GT와 달랐는데 반론이 돌아오지 않은 칸 — «의견이 갈림»이 아니라 «한 눈이 못 봄»이다. 다시 읽기 대상.
                "noDefense": status == "CONTESTED" and rebuttal is None and bool(reading),
                "hint": (f"GT와 같은 값이 아니라, 같이 맞다고 적어 둔 다른 값({value})을 AI가 냈습니다. 지금 GT가 맞는지 직접 봐 주세요."
                         if alternative else
                         NO_DEFENSE_HINT if status == "CONTESTED" and rebuttal is None else
                         CONTRADICTED_HINT if status == "NEEDS_HUMAN_LOOK" and "GT_SELF_CONTRADICTION" in cell.get("signals", [])
                         and reading and reading.get("confidence") == "HIGH" else STATUS[status]["hint"]),
                "decision": latest.get((item["key"], cell["field"])),
            })
        cells.sort(key=lambda cell: STATUS[cell["status"]]["order"])
        items.append({
            "id": item["id"], "key": item["key"], "group": item.get("group"), "title": item.get("title"),
            "images": item.get("images") or [], "omitted": item.get("omitted") or [],
            "missing": item.get("missing") or [], "text": item.get("text") or {},
            # 판독자가 받은 맥락(카테고리 같은). AI 문장이 «하의로 파는 상품이라…»처럼 기대면 사람도 봐야 되짚는다.
            "context": item.get("context") or {},
            "noEvidence": bool(item.get("noEvidence")),
            "note": (result.get("reading") or {}).get("note"),
            "cells": cells,
            "order": min(STATUS[cell["status"]]["order"] for cell in cells) if cells else 99,
        })
    items.sort(key=lambda item: (item["order"], item["id"]))
    counts: dict[str, int] = {name: 0 for name in STATUS}
    for item in items:
        for cell in item["cells"]:
            counts[cell["status"]] += 1
    return {
        "schemaVersion": REVIEW_SCHEMA,
        "profileId": worklist.get("profileId"),
        "batchId": worklist.get("batchId"),
        "basedOn": {"worklist": worklist.get("generatedAt"), "sweep": bool(sweep)},
        "fields": list(fields.values()),
        "counts": {"byStatus": counts, "worklist": worklist.get("counts")},
        "warnings": (sweep or {}).get("warnings") or {},
        "sources": worklist.get("sources"),
        "items": items,
        "excluded": worklist.get("excluded") or [],
    }


SCRIPT = r"""
// 글쇠 J — 다음 안 한 칸으로(증분 검수 화면과 같은 글쇠). 이름 칸·입력 중에는 듣지 않고, ⌘·Ctrl과 함께 누른 것은 무시한다.
document.addEventListener('keydown', (event) => {
  if (event.code !== 'KeyJ' || event.metaKey || event.ctrlKey || event.altKey || event.repeat || event.isComposing) return;
  const tag = (event.target && event.target.tagName) || '';
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag) || (event.target && event.target.isContentEditable)) return;
  const viewer = document.getElementById('viewer'); if (viewer && !viewer.hidden) return;
  const button = document.getElementById('next-open'); if (button && !button.hidden) button.click();
});
const data = JSON.parse(document.getElementById('gt-review-data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const NAMES = {CORRECT: '고침', CONFIRM: 'GT로 유지', LEAVE_EMPTY: '빈칸 유지', CLEAR: '비움', HOLD: '보류'};
const CHANGES = ['CORRECT', 'CLEAR'];
const labelOf = (field, value) => value == null ? '(빈칸)' : String(value).split('|').map(part => (data.labelNames[field] || {})[part] || part).join(' + ');
let pendingBox = null;
const nameBox = document.getElementById('reviewer');
// 이름은 이 탭에서만 기억한다(증분 검수와 같다) — 컴퓨터를 같이 쓰면 앞사람 이름으로 기록되지 않게. 지난 이름은 고르기 목록으로만.
try {
  nameBox.value = sessionStorage.getItem('gt-review-reviewer') || '';
  const list = document.createElement('datalist'); list.id = 'reviewer-recent';
  JSON.parse(localStorage.getItem('gt-review-reviewer-recent') || '[]').forEach(name => { const o = document.createElement('option'); o.value = name; list.appendChild(o); });
  document.body.appendChild(list); nameBox.setAttribute('list', 'reviewer-recent');
} catch (e) {}
nameBox.addEventListener('focus', () => nameBox.select());
nameBox.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === 'Escape') nameBox.blur(); });
nameBox.addEventListener('change', () => {
  try {
    sessionStorage.setItem('gt-review-reviewer', nameBox.value.trim());
    const recent = JSON.parse(localStorage.getItem('gt-review-reviewer-recent') || '[]').filter(name => name !== nameBox.value.trim());
    if (nameBox.value.trim()) localStorage.setItem('gt-review-reviewer-recent', JSON.stringify([nameBox.value.trim(), ...recent].slice(0, 8)));
  } catch (e) {}
  // 이름을 적으면 그 칸으로 돌아와, 아직 기록되지 않았다고 말한다 — 조용히 지우면 기록된 줄 안다.
  if (nameBox.value.trim()) { const hint = document.getElementById('name-hint'); if (hint) hint.hidden = true; }
  if (pendingBox && nameBox.value.trim()) {
    pendingBox.scrollIntoView({block: 'center'});
    const out = pendingBox.querySelector('.result');
    if (out) out.textContent = '이름을 적었습니다. 이제 버튼을 한 번 더 눌러 주세요 — 아직 기록되지 않았습니다.';
    pendingBox = null;
  }
});
// 한국어 입력기에서는 첫 Enter가 글자를 맺기만 하고 change가 나지 않을 수 있다 — 맺음이 끝난 Enter를 직접 받아 같은 일을 한다.
nameBox.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.isComposing) nameBox.dispatchEvent(new Event('change'));
});
const served = location.protocol.startsWith('http');
if (!served) document.getElementById('offline').hidden = false;

function human(error, response) {
  // 사람이 다음에 무엇을 할지 말하는 문장만 낸다. 영문 오류를 그대로 보이지 않는다.
  if (!response) return '서버에 닿지 못했습니다 — Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요.';
  if (response.status === 404) return '서버가 오래된 판입니다 — Claude에게 «서버 다시 켜줘»라고 말해 주세요.';
  if (response.status >= 500) return '저장하지 못했습니다 — 잠시 뒤 다시 눌러 보고, 또 안 되면 Claude에게 «서버 다시 켜줘»라고 말해 주세요.';
  return error;
}

// 기록된 값의 버튼을 눌린 모양으로 — 다시 고르면 표시도 그 버튼으로 옮겨 간다.
function markPicked(box, entry) {
  const value = !entry || entry.decision === 'HOLD' ? undefined
    : ['CORRECT', 'CLEAR'].includes(entry.decision) ? entry.after : entry.before;
  box.querySelectorAll('button.chip').forEach(chip => {
    const on = value != null && chip.dataset.label === String(value);
    chip.classList.toggle('picked', on);
    chip.classList.remove('pending');
    if (entry) chip.classList.remove('chosen');
    chip.setAttribute('aria-pressed', String(on || chip.classList.contains('chosen')));
  });
  box.querySelectorAll('.act button[data-decision="HOLD"]').forEach(b => b.classList.toggle('picked', !!entry && entry.decision === 'HOLD'));
}

function paint(box, entry) {
  const out = box.querySelector('.result');
  box.classList.remove('done', 'held');
  markPicked(box, entry);
  if (!entry) { out.textContent = ''; return; }
  if (entry.decision === 'HOLD') {
    box.classList.add('held');
    out.innerHTML = `<b>보류</b> (${esc(entry.reviewer)}, ${esc((entry.decidedAt||'').slice(0,10))}) — 보류한 칸은 새 후보를 다 본 뒤 다시 나옵니다.`;
    return;
  }
  box.classList.add('done');
  const change = ['CORRECT', 'CLEAR'].includes(entry.decision) ? ` · ${esc(labelOf(box.dataset.field, entry.before))} → ${esc(labelOf(box.dataset.field, entry.after))}` : '';
  const warn = (entry.constraintWarnings || []).length
    ? `<br><span style="color:var(--accent)">주의 — 이 칸만 고친 지금은 «${esc(entry.constraintWarnings.join(' / '))}»가 어긋납니다. 같은 건의 다른 칸도 봐 주세요.</span>` : '';
  // 칸에는 «무엇으로 기록했나 · 누가»만 — GT가 언제 바뀌는지는 맨 위에 한 번, 바꾸는 법은 곁의 «바꾸기» 버튼이 말한다.
  const to = labelOf(box.dataset.field, entry.after);
  const last = to.slice(-1), code = last.charCodeAt(0) - 0xAC00;
  const ro = code >= 0 && code < 11172 && code % 28 !== 0 && code % 28 !== 8 ? '으로' : '로';
  const from = esc(labelOf(box.dataset.field, entry.before));
  const what = entry.decision === 'CORRECT' ? `${from} → <b>${esc(to)}</b>`
    : entry.decision === 'CLEAR' ? `${from} → <b>비움</b>`
    : entry.decision === 'LEAVE_EMPTY' ? '<b>빈칸 유지</b>'
    : `<b>${from}</b> 유지`;
  void ro;
  out.innerHTML = `${what} · ${esc(entry.reviewer)}${warn}`;
}

// 이 화면의 답이 아닌 지난 판정(지난 배치의 보류, 모순을 풀지 못한 지난 답). 무엇을 적었는지만 보여 주고 답으로 세지 않는다.
function paintEarlier(box, entry) {
  const out = box.querySelector('.result');
  box.classList.remove('done', 'held');
  const when = `${esc(entry.reviewer)}, ${esc((entry.decidedAt||'').slice(0,10))}${entry.reason ? ' — ' + esc(entry.reason) : ''}`;
  out.innerHTML = box.dataset.contradicted && entry.decision !== 'HOLD'
    ? `지난번 <b>${esc(NAMES[entry.decision])}</b> (${when}) — 그런데 같은 줄의 다른 칸과 여전히 안 맞습니다. 둘 중 하나를 고쳐 주세요.`
    : `지난번 <b>${esc(NAMES[entry.decision])}</b> (${when}). 이번에 다시 골라 주세요.`;
}

function progress() {
  // 볼 칸 — 사람이 가를 칸과, AI가 못 읽었지만 사람이 직접 답할 수 있는 칸. 못 읽은 칸의 답도 «답한 칸»으로 센다.
  const boxes = [...document.querySelectorAll('.cell[data-actionable="1"], .cell.NOT_READ')].filter(b => !b.classList.contains('stale'));
  // 보류도 이 화면에서는 답이다. GT는 정하지 않았으니 다음 배치에서 다시 나온다.
  const held = boxes.filter(b => b.classList.contains('held')).length;
  const done = boxes.filter(b => b.classList.contains('done')).length + held;
  // 못 읽은 칸(판독이나 반론이 돌아오지 않음) 가운데 사람이 아직 답하지 않은 칸만 센다.
  // 원본이 바뀐 칸(stale)은 다시 읽어도 이 화면에서 답할 수 없다 — status.notRead와 같게 뺀다.
  const unread = [...document.querySelectorAll('.cell.NOT_READ, .cell.nodefense')]
    .filter(b => !b.classList.contains('done') && !b.classList.contains('held') && !b.classList.contains('stale')).length;
  // «AI가 GT와 같게 봄» 칸은 볼 칸에 넣지 않지만, 유지를 누르기 전에는 «다 했습니다»라고 하지 않는다.
  const open = b => !b.classList.contains('done') && !b.classList.contains('stale') && !b.classList.contains('held');
  const holds = 0;  // AI가 GT와 같게 본 칸은 기본값(지금 GT)으로 둔다 — 누를 것이 없어 남은 일로 세지 않는다
  // 대체 정답을 낸 칸은 묶음 유지에서 빠진다 — 칸마다 «지금 GT가 맞다»를 눌러야 한다.
  const alts = [...document.querySelectorAll('.cell.GT_HOLDS.alt')].filter(open).length;
  // AI가 못 읽은 칸이 남았으면 «다 했습니다»라고 하지 않는다 — «다음 거»가 그 칸을 뒤로 밀어낸다.
  const finished = done === boxes.length && !holds && !alts && !unread;
  let text = boxes.length
    ? `이 화면: 판정할 칸 ${boxes.length} 중 ${done} 답함` + (held ? ` (그중 보류 ${held} — 다음에 다시 나옵니다)` : '')
    : '이번 화면에는 고칠지 가를 칸이 없습니다.';
  if (boxes.length && boxes.every(b => b.classList.contains('NOT_READ')))
    text = `AI가 이번에 읽지 못했습니다 — 칸 ${boxes.length}개를 직접 보고 고르셔도 되고, 다음 거로 넘어가기 전에 Claude에게 «못 읽은 거 다시 봐줘»라고 하셔도 됩니다. 답한 칸 ${done}`;
  const holdItems = [...document.querySelectorAll('.item')].filter(item => [...item.querySelectorAll('.cell.GT_HOLDS:not(.alt)')].some(open)).length;
  if (holds) text += ` · 유지 확인만 남은 칸 ${holds} — 상품 ${holdItems}개의 맨 아래 «GT로 유지» 버튼을 눌러 주세요.`;
  if (alts) text += ` · 같이 맞다고 적어 둔 값을 AI가 낸 칸 ${alts} — 그 칸의 «지금 GT» 값 버튼을 눌러 주세요.`;
  if (finished) text += data.upstreamNote ? ` — 다 했습니다. Claude에게 «${data.callName} 반영해줘», 더 보려면 «${data.callName} 다음 거»라고 말해 주세요.`
    : ` — 다 했습니다. 위의 «반영하기»를 누르면 GT에 들어갑니다. 더 보려면 Claude에게 «${data.callName} 다음 거»라고 말해 주세요.`;
  const allUnread = boxes.length && boxes.every(b => b.classList.contains('NOT_READ'));
  if (unread && !allUnread) text += ` · AI가 못 읽은 칸 ${unread} — 다음으로 넘어가기 전에 Claude에게 «못 읽은 거 다시 봐줘»라고 말해 주세요.`;
  const stale = document.querySelectorAll('.cell.stale').length;
  if (stale) text += ` · 원본이 바뀐 칸 ${stale} — «다음 거»에서 새 값으로 다시 나옵니다.`;
  document.getElementById('progress').textContent = text;
  // 휴대폰에서는 마지막 칸이 화면 맨 아래다 — 진행률을 아래에도 보여 «다 했습니다»를 놓치지 않게.
  // 아래 막대는 짧은 요약만 — 긴 문장이 버튼 줄을 가리지 않게. 긴 문장은 위에 있다.
  const bottom = document.getElementById('progress-bottom-text');
  // 다 했으면 짧게 — 무엇을 할지는 위의 «반영해줘» 버튼과 «다음 후보 받기» 버튼이 이미 말한다. 같은 말을 여러 자리에 두지 않는다.
  const short = finished ? '다 답했습니다'
    : `판정할 칸 ${done} / ${boxes.length} 답함` + (holds ? ` · «GT로 유지» ${holdItems}번 남음 (${holds}칸)` : '') + (alts ? ` · 같이 맞다고 적은 값 확인 ${alts}` : '') + (unread ? ` · AI가 못 읽은 칸 ${unread}` : '');
  if (bottom) bottom.textContent = short;
  // 아래 떠 있는 막대는 아직 할 일이 있을 때만 — 다 했으면 위의 버튼 둘(반영해줘 · 다음 후보 받기)로 충분하다.
  const dock = document.getElementById('progress-bottom');
  if (dock) dock.hidden = finished;
  const top = document.getElementById('progress-short');
  if (top) top.textContent = short;
  const nextButton = document.getElementById('next-open');
  if (nextButton) nextButton.hidden = finished;
  // 상품마다 남은 일 — 긴 화면에서 어디가 끝났는지 머리에서 본다.
  document.querySelectorAll('.item').forEach(item => {
    const mine = [...item.querySelectorAll('.cell[data-actionable="1"], .cell.NOT_READ')].filter(open).length;
    const alts = [...item.querySelectorAll('.cell.GT_HOLDS.alt')].filter(open).length;
    const state = item.querySelector('.item-state');
    const over = !mine && !alts;
    item.classList.toggle('finished', over);
    if (state) state.textContent = over ? '다 답했습니다' : `수정 제안 ${mine + alts}개`;
  });
}

async function send(box, decision, value, bulk = false) {
  const out = box.querySelector('.result');
  const reviewer = nameBox.value.trim();
  if (!reviewer) {
    out.textContent = '맨 위에 이름을 먼저 적어 주세요. 적고 나면 이 칸으로 돌아옵니다.';
    pendingBox = box;
    const hint = document.getElementById('name-hint');
    if (hint) { hint.textContent = '이름을 적고 Enter를 누르거나 이름 칸 밖을 누르면 방금 누른 칸으로 돌아갑니다.'; hint.hidden = false; }
    nameBox.focus(); return false;
  }
  if (!served) { out.textContent = '파일로 열면 기록할 수 없습니다. 서버 주소로 열어 주세요.'; return false; }
  const body = {
    task: data.profileId, key: box.dataset.key, field: box.dataset.field, decision, value,
    reviewer, reason: (box.querySelector('input.reason') || {}).value || '',
    batch: data.batchId, gap: !!(box.querySelector('input.gap') || {}).checked,
    expectedBefore: box.dataset.current === '' ? null : box.dataset.current,
    proposal: JSON.parse(box.dataset.proposal || 'null'), bulk,
    // 화면이 본 그 칸의 마지막 판정 — 그사이 다른 사람이 답했으면 서버가 거절하고, 이 화면이 그 답을 들인다
    expectedLatest: box.dataset.latest || null,
  };
  box.querySelectorAll('button').forEach(b => b.disabled = true);
  // 이미 답한 칸을 다시 누르다 실패하면 전의 판정이 사라진 것처럼 보인다 — 전의 문장을 들고 있다가 실패 문장 뒤에 붙인다.
  const earlier = (box.classList.contains('done') || box.classList.contains('held')) ? out.textContent.trim() : '';
  out.textContent = '기록하는 중…';
  let response = null;
  try {
    response = await fetch('/gt-decide', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    let answer = null;
    try { answer = await response.json(); } catch (e) { throw new Error(human('', {status: 404})); }
    if (!answer.ok) {
      if (/먼저 이 칸을 답했습니다/.test(answer.error || '')) load();  // 다른 사람의 답을 곧바로 들인다
      throw new Error(human(answer.error, response));
    }
    box.dataset.latest = answer.decision.decisionId || '';
    paint(box, answer.decision);
    return true;
  } catch (error) {
    out.textContent = '기록하지 못했습니다 — ' + (response ? error.message : human('', null))
      + (earlier ? ` — 전의 판정은 그대로 남아 있습니다: ${earlier}` : '');
    return false;
  } finally {
    box.querySelectorAll('button').forEach(b => b.disabled = false);
    progress();
  }
}

// «다른 값…»을 고르면 그 옆 버튼이 «○○(으)로 고치기»가 된다. 고르기 전에는 누를 수 없다(제안대로와 같은 일을 하지 않게).
document.querySelectorAll('.cell select').forEach(picker => picker.addEventListener('change', () => {
  const box = picker.closest('.cell');
  const button = box.querySelector('button[data-pick]');
  if (!button) return;
  const chosen = picker.value;
  button.disabled = !chosen;
  button.classList.toggle('primary', !!chosen);
  button.textContent = chosen ? `${labelOf(box.dataset.field, chosen)}(으)로 고치기` : '다른 값을 고르면 여기서 고칩니다';
}));

// 이유·경계 표시는 버튼을 누를 때 함께 간다. 누른 뒤에 바꾸면 조용히 버려지므로, 다시 누르라고 말한다.
document.querySelectorAll('.cell input.reason, .cell input.gap').forEach(input => input.addEventListener('change', () => {
  const box = input.closest('.cell');
  if (!box.classList.contains('done') && !box.classList.contains('held')) return;
  box.querySelector('.result').insertAdjacentHTML('beforeend',
    '<br><b>아직 저장되지 않았습니다</b> — 바꾼 이유·경계 표시를 남기려면 판정 버튼을 한 번 더 눌러 주세요.');
}));

document.querySelectorAll('.cell button[data-decision]').forEach(button => button.addEventListener('click', () => {
  // 누른 순간 반응 — 기록이 끝나면 paint가 눌린 모양으로 바꾼다.
  button.closest('.cell').querySelectorAll('button.chip.pending').forEach(b => b.classList.remove('pending'));
  if (button.classList.contains('chip')) button.classList.add('pending');
  // AI가 «기준 문서가 다루지 않는 경우»라고 짚은 칸 — 표시하지 않고 고르면 한 번만 묻는다(칸마다 한 번).
  const gapBox = button.closest('.cell');
  const gap = gapBox.querySelector('input.gap');
  if (gapBox.dataset.gap && gap && !gap.checked && !gapBox.dataset.gapAsked && button.dataset.decision !== 'HOLD') {
    gapBox.dataset.gapAsked = '1';
    if (!confirm('AI가 정책이 다루지 않는 경우라고 짚은 칸입니다.\n«정책이 다루지 않는 경우»에 표시하지 않고 기록할까요?\n(취소를 누르면 표시할 수 있게 그 칸을 펼칩니다)')) {
      const more = gapBox.querySelector('details.memo-add'); if (more) more.open = true; gap.focus(); return;
    }
  }
  const box = button.closest('.cell');
  let value = button.dataset.value || null;
  if (button.dataset.pick) {
    const picker = box.querySelector('select');
    const chosen = picker ? [...picker.selectedOptions].map(o => o.value).filter(Boolean)
      : [...box.querySelectorAll('input.pick:checked')].map(o => o.value);
    if (!chosen.length) { box.querySelector('.result').textContent = '고칠 값을 골라 주세요.'; return; }
    value = picker ? chosen[0] : chosen.sort().join('|');
  }
  send(box, button.dataset.decision, value);
}));

// 크게 보기 — 같은 상품의 사진을 ← → 로 넘기고, 사진을 누르면 원래 크기(글자 조각을 읽게), Esc나 바깥을 누르면 닫는다.
const viewer = document.getElementById('viewer');
let gallery = [], at = 0;
function show(index) {
  at = (index + gallery.length) % gallery.length;
  const img = gallery[at];
  const big = viewer.querySelector('img');
  big.src = img.src; big.classList.remove('full');
  viewer.querySelector('p').textContent = `${img.alt} — ${at + 1} / ${gallery.length} · ← → 넘기기 · 사진을 누르면 원래 크기 · Esc 닫기`;
  viewer.hidden = false;
}
function openFrom(img) {
  gallery = [...img.closest('.item').querySelectorAll('.rail img')];
  show(gallery.indexOf(img));
}
document.querySelectorAll('.rail img').forEach(img => img.addEventListener('click', () => openFrom(img)));
viewer.addEventListener('click', event => {
  if (event.target.tagName === 'IMG') { event.target.classList.toggle('full'); return; }
  if (event.target.dataset.go) { show(at + Number(event.target.dataset.go)); return; }
  viewer.hidden = true;
});
document.addEventListener('keydown', event => {
  if (viewer.hidden) return;
  if (event.key === 'Escape') viewer.hidden = true;
  if (event.key === 'ArrowRight') show(at + 1);
  if (event.key === 'ArrowLeft') show(at - 1);
});
document.querySelectorAll('a.pic').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  const img = [...link.closest('.item').querySelectorAll('.rail img')].find(i => i.dataset.view === link.dataset.view);
  if (img) { const more = img.closest('details'); if (more) more.open = true; openFrom(img); }
}));
// 다음 안 한 칸으로 — 긴 화면에서 남은 칸을 찾으려고 스크롤을 되풀이하지 않게.
// «다음 안 한 칸»(J) — 지금 보고 있는 자리 **다음의** 안 한 칸으로. 뒤에 없으면 처음부터(앞 페이지 포함) 찾는다.
// 늘 맨 위의 칸으로 가면 어려운 칸을 건너뛸 수 없다(증분 검수의 J와 같다).
function nextOpenTarget() {
  const open = b => !b.classList.contains('done') && !b.classList.contains('stale') && !b.classList.contains('held');
  const cells = [...document.querySelectorAll('.cell[data-actionable="1"], .cell.NOT_READ')];
  let here = -1;
  cells.forEach((c, i) => { if (c.offsetParent && c.getBoundingClientRect().top < window.innerHeight / 2) here = i; });
  return cells.slice(here + 1).find(open) || cells.find(open) || null;
}
document.getElementById('next-open').addEventListener('click', () => {
  // 페이지 넘김(아래 잡는 단계)이 먼저 고른 칸이 있으면 그것 — 페이지를 바꾼 뒤 다시 고르면 다른 칸이 된다
  const target = window.NEXT_TARGET || nextOpenTarget(); window.NEXT_TARGET = null;
  if (!target) return;
  const item = target.closest('.item');
  if (item) { item.classList.remove('folded'); const again = item.querySelector('.reopen'); if (again) again.hidden = true; }
  target.scrollIntoView({block: 'center', behavior: 'smooth'});
});
// 열 때 이미 다 답한 상품은 접는다(머리와 «다시 펼치기»만). 답하는 도중에는 접지 않는다 — 누른 칸이 눈앞에서 사라지지 않게.
function foldFinished() {
  document.querySelectorAll('.item.finished').forEach(item => {
    item.classList.add('folded');
    const again = item.querySelector('.reopen');
    if (again) again.hidden = false;
  });
}
document.querySelectorAll('.reopen').forEach(button => button.addEventListener('click', () => {
  button.closest('.item').classList.remove('folded'); button.hidden = true;
}));
document.querySelectorAll('.fold-thumb').forEach(button => button.addEventListener('click', () => {
  const again = button.closest('.item').querySelector('.reopen'); if (again) again.click();
}));
// 다른 과제와 남은 건 — 첫 화면과 같은 목록(/gt-tasks)에서 읽는다. 화면을 그린 때가 아니라 지금의 수다.
async function others() {
  if (!served) return;
  try {
    const listing = await (await fetch('/gt-tasks', {cache: 'no-store'})).json();
    const tasks = listing.tasks || [];
    const me = tasks.find(task => task.task === data.profileId);
    if (me && typeof me.remainingItems === 'number')
      document.getElementById('remaining').textContent = me.remainingItems ? `이 화면 뒤에 ${me.remainingItems}건이 더 있습니다.` : '남은 후보가 없습니다.';
    const next = tasks.find(task => task.task !== data.profileId && task.prepared && !task.preparing
      && ((task.waitingForHuman || 0) + (task.holdsUnconfirmed || 0) + (task.notRead || 0)) > 0);
    const link = document.getElementById('next-task');
    if (next && link) {
      link.href = next.page; link.hidden = false;
      link.textContent = `다음 골든셋 검수: ${next.name} (판정할 칸 ${next.waitingForHuman || 0}${next.holdsUnconfirmed ? ` · «GT로 유지» ${next.holdsUnconfirmed}칸` : ''}) →`;
    }
  } catch (e) {}
}
document.getElementById('show-holds').addEventListener('change', e => document.body.classList.toggle('show-holds', e.target.checked));

// 만든 시각은 사람의 시계로 보인다.
(() => { const made = document.getElementById('made'); const at = made && made.dataset.at;
  if (at) made.textContent = '만든 시각 ' + new Date(at).toLocaleString('ko-KR');
  document.querySelectorAll('.made-at').forEach(span => { if (span.dataset.at) span.textContent = new Date(span.dataset.at).toLocaleString('ko-KR'); }); })();
// 답한 칸은 한 줄로 접힌다(제목·값·기록 문장만). «바꾸기»를 누르면 펼쳐 다른 버튼을 누를 수 있다.
document.querySelectorAll('.cell .change').forEach(button => button.addEventListener('click', () => {
  // 같은 버튼이 펴고 접는다 — 편 뒤에 접을 길이 없으면 답한 칸이 계속 펼쳐진 채 남는다.
  const cell = button.closest('.cell');
  const open = cell.classList.toggle('open');
  button.textContent = open ? '접기' : '바꾸기';
  button.setAttribute('aria-expanded', String(open));
}));
// 열릴 때마다 원장을 다시 읽는다. 파일로 굳힌 화면만 믿으면 새로고침한 뒤 답이 사라진 것처럼 보인다.
async function load() {
  if (served) {
    try {
      const response = await fetch('/gt-decided?task=' + encodeURIComponent(data.profileId));
      const answer = await response.json();
      const latest = answer.latest || {};
      // 지난번 불러오기가 실패해 잠갔던 화면이면 푼다(배치가 달라 잠근 것은 풀지 않는다). 칸별 잠금은 아래 칠하기가 다시 건다.
      if (document.body.dataset.locked === 'offline') {
        delete document.body.dataset.locked;
        document.getElementById('offline').hidden = true;
        document.querySelectorAll('.cell button').forEach(b => b.disabled = false);
      }
      const now = answer.current || {};
      document.querySelectorAll('.cell').forEach(box => {
        const cell = box.dataset.key + '\u0000' + box.dataset.field;
        const entry = latest[cell];
        box.dataset.latest = entry ? entry.decisionId || '' : '';
        const current = box.dataset.current === '' ? null : box.dataset.current;
        // 사람이 고른 값이 원본에 들어갔다(«반영하기» 뒤). 바뀐 것이 아니라 반영된 것이다.
        if (entry && CHANGES.includes(entry.decision) && cell in now && now[cell] === entry.after && now[cell] !== current) {
          box.classList.add('done');
          box.querySelectorAll('button').forEach(b => b.disabled = true);
          box.querySelector('.result').innerHTML = `<b>${esc(labelOf(box.dataset.field, entry.after))}</b> ${esc(data.appliedNote)} · ${esc(entry.reviewer)}`;
          return;
        }
        // 화면을 그린 뒤 원본 GT가 바뀐 칸. 이 화면에서는 답할 수 없다 — 새로고침해도 화면의 값은 그대로다.
        if (cell in now && now[cell] !== current) {
          box.classList.add('stale');
          box.querySelectorAll('button').forEach(b => b.disabled = true);
          box.querySelector('.result').innerHTML = `<b>원본 GT가 바뀌었습니다</b> (지금 ${esc(labelOf(box.dataset.field, now[cell]))}). 이 화면에서는 답할 수 없고, «다음 거»에서 새 값으로 다시 나옵니다.`;
          return;
        }
        // 사람이 본 값이 지금 GT와 같을 때만 «답함»이다(status의 answered_on_page와 같은 규칙).
        // «이 화면의 답인가»는 서버가 status와 같은 함수로 정해 보낸다. 여기서 다시 셈하지 않는다.
        if (!entry) { paint(box, null); return; }
        if ((answer.answered || {})[cell]) { paint(box, entry); return; }
        paintEarlier(box, entry);
      });
      // 열어 둔 이 탭보다 새 배치가 있으면 옛 화면에서 답하지 않게 막는다.
      if (answer.batchId && answer.batchId !== data.batchId) {
        const notice = document.getElementById('offline');
        notice.textContent = answer.preparing
          ? 'AI가 새 후보를 보는 중입니다 — 이 탭은 지난 화면입니다. Claude가 새 화면을 열어 드릴 때까지 기다려 주세요.'
          : '새 화면이 준비됐습니다 — 이 탭은 지난 화면입니다. 새로고침해 주세요.';
        notice.hidden = false;
        document.body.dataset.locked = 'batch';
        document.querySelectorAll('.cell button').forEach(b => b.disabled = true);
      }
    } catch (e) {
      // 지난 답을 모르는 채로 누르게 두면, 이미 답한 칸을 다시 누르고 진행률도 0으로 읽힌다. 버튼을 막는다.
      document.body.dataset.locked = document.body.dataset.locked || 'offline';
      const notice = document.getElementById('offline');
      notice.textContent = '서버에 닿지 못해 지난 답을 불러오지 못했습니다 — 잠시 뒤 새로고침하고, 그래도 안 되면 Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요.';
      notice.hidden = false;
      document.querySelectorAll('.cell button').forEach(b => b.disabled = true);
      document.getElementById('progress').textContent = '지난 답을 불러오지 못해 진행률을 알 수 없습니다.';
      return;
    }
  }
  progress();
}
load().then(foldFinished);
others();
// 다른 탭에서 돌아오면 다시 읽는다 — 그사이 «다음 거»로 새 배치가 생겼으면 이 탭을 막는다.
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
// 같은 화면을 다른 사람이 열어 두었을 수 있다 — 30초마다 다른 사람의 답을 들인다(증분 검수와 같다)
if (served) setInterval(() => { if (!document.hidden) load(); }, 30000);
"""

EXTRA_STYLE = """
body{font-family:"IBM Plex Sans KR","Apple SD Gothic Neo",var(--sans)}
/* 미니멀 — 상자 안의 상자를 없앤다. 구분은 가는 줄 하나와 여백으로, 강조는 굵기와 빨강 밑줄 하나로. */
.wrap{max-width:1180px}
.masthead{padding:28px 0 0}
.masthead h1{font-size:clamp(1.7rem,3.2vw,2.5rem);font-weight:700;letter-spacing:-.03em;line-height:1.1;padding:10px 0 6px;border-top:0}
.lead{color:var(--muted);font-size:13.5px;margin-top:6px;max-width:70ch}
.bar{display:flex;flex-wrap:wrap;gap:22px;align-items:center;margin-top:22px;padding:0;border:0}
.bar label{font-size:13px;color:var(--muted)}
.bar input#reviewer{font:inherit;font-size:14px;padding:4px 0;border:0;border-bottom:1px solid var(--ink);border-radius:0;background:transparent;color:var(--ink);min-width:140px}
.bar input#reviewer:focus{outline:0;border-bottom-color:var(--accent)}
.statusline{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px 24px;margin-top:18px;padding-top:12px;border-top:1px solid var(--ink);font-size:13px;color:var(--muted)}
.tally b,#progress-short{color:var(--ink);font-weight:600}
.hidden-count{color:var(--faint)}
#progress{margin-top:6px;font-size:12.5px;color:var(--muted)}
.notice{margin-top:14px;padding:8px 0;border:0;border-top:1px solid var(--accent);color:var(--accent);font-size:13px}
#offline{position:sticky;top:0;z-index:5;background:var(--paper)}
#progress-bottom{position:sticky;bottom:0;z-index:5;background:var(--paper);border-top:1px solid var(--rule);padding:6px 0;font-size:13px;margin-top:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* 건 */
.item{margin-top:56px;padding-top:22px;border-top:1px solid var(--ink)}
.item:last-of-type{padding-bottom:48px}
.item .kicker{color:var(--faint)}
.item h3{font-size:1.05rem;font-weight:600;margin-top:2px;letter-spacing:-.01em}
.item h3 small{font-weight:400;color:var(--muted);margin-left:6px}
.item-body{display:grid;grid-template-columns:minmax(0,1fr);gap:22px;margin-top:16px}
.side-kicker{display:none}
.shots{display:flex;gap:8px;overflow-x:auto;padding:0}
.shots figure{flex:0 0 auto;margin:0}
.shots img{height:180px;width:auto;display:block;cursor:zoom-in;border:0}
.shots figcaption{font-family:var(--mono);font-size:10px;color:var(--faint);margin-top:3px}
.textbox{margin-top:12px;font-size:13px;color:var(--muted);white-space:pre-wrap;padding:0;background:transparent}
.textbox b{color:var(--ink);font-weight:600}
.why{color:var(--muted);font-size:13px;margin-top:5px;line-height:1.55}

/* 칸 — 상자 없이, 위에 가는 줄 하나 */
.gt-cell-grid{display:block;margin-top:0}
.cell{padding:20px 0 22px;border:0;border-top:1px solid var(--rule)}
.decide .cell:first-child{border-top:0;padding-top:0}
.cell.done{opacity:.5}
.cell.held{opacity:.75}
.cell.stale{opacity:.5}
.cell.GT_HOLDS{display:none}
body.show-holds .cell.GT_HOLDS{display:block}
.cell.GT_HOLDS.alt{display:block}
.cell h4{font-size:1rem;font-weight:600;margin:0}

/* 지금 GT → 제안. 글자로만 — 지금 값은 줄을 긋고, 제안은 굵게 빨강 밑줄 */
.diff{display:flex;flex-wrap:wrap;gap:6px 36px;align-items:flex-end;margin:10px 0 4px;border:0}
.diff > div{padding:0;min-width:0}
.diff .tag{display:block;font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin-bottom:2px}
.diff .v{display:inline-block;font-size:1.4rem;font-weight:600;line-height:1.25;word-break:keep-all;overflow-wrap:anywhere}
.diff .arrow{align-self:end;color:var(--faint);font-family:var(--mono);font-size:1.15rem;padding:0 0 4px;border:0}
/* 누르기 전에는 지금 GT에 줄을 긋지 않는다 — 이미 고쳐진 것처럼 읽힌다. 지금 값은 회색, 제안은 굵게 빨강 밑줄. */
.diff.proposed .now .v{color:var(--muted);font-weight:400}
.diff .code{display:block;font-family:var(--mono);font-size:10px;color:var(--faint);margin-top:1px}
.diff.fill .now .v{text-decoration:none;color:var(--faint);font-style:italic;font-weight:400}
.diff.proposed .to .v{border-bottom:2px solid var(--accent);padding-bottom:1px}
.diff.proposed .to .tag{color:var(--accent)}
.diff.contested .to .v{border-bottom:2px dashed var(--ink);padding-bottom:1px}
.diff.weak .to .v,.diff.none .to .v,.diff.same .to .v{color:var(--muted);font-weight:400}
/* 판단 근거 — 결정 구역(검정·빨강) 아래, 회색 들여쓰기 한 구역. 읽을 것이지 누를 것이 아니다. */
.grounds{margin-top:18px;padding:12px 0 0 18px;border-left:2px solid var(--rule)}
.grounds-kicker{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;color:var(--faint);margin-bottom:8px}
.grounds dl{display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px 16px;margin:0;font-size:12.5px;line-height:1.55;color:var(--muted)}
.grounds dt{color:var(--ink);font-weight:500}
.grounds dd{margin:0}
.grounds dd b{color:var(--ink);font-weight:500}

/* 판정 */
.act{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}
.act button{font:inherit;font-size:13px;padding:8px 14px;border:1px solid var(--rule);background:transparent;color:var(--ink);border-radius:2px;cursor:pointer}
.act button:hover{border-color:var(--ink)}
.act button.primary{background:var(--ink);border-color:var(--ink);color:var(--paper)}
.act select{font:inherit;font-size:13px;padding:5px 4px;border:0;border-bottom:1px solid var(--rule);border-radius:0;background:transparent;color:var(--ink);max-width:260px}
.act.notes{margin-top:14px;gap:14px}
.act.notes input.reason{font:inherit;font-size:13px;padding:4px 0;border:0;border-bottom:1px solid var(--rule);border-radius:0;background:transparent;color:var(--ink);flex:1;min-width:180px}
.act.notes input.reason:focus{outline:0;border-bottom-color:var(--ink)}
.act.notes label{font-size:12.5px;color:var(--muted)}
.picks{display:inline-flex;flex-wrap:wrap;gap:4px 10px;font-size:13px}
.result{font-size:12.5px;color:var(--muted);margin-top:8px}
details.def,details.meta{margin-top:8px;font-size:12px;color:var(--faint)}
details.def summary,details.meta summary{cursor:pointer}
details.def .deftext{white-space:pre-wrap;margin-top:6px;padding-left:10px;border-left:1px solid var(--rule);color:var(--muted);font-size:12.5px}
#viewer{position:fixed;inset:0;background:rgba(0,0,0,.86);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:10;cursor:zoom-out}
#viewer[hidden]{display:none}
#viewer img{max-width:94vw;max-height:88vh}
#viewer p{color:#fff;font-family:var(--mono);font-size:12px;margin-top:8px}
footer{color:var(--muted)}
/* 버튼 → GT까지의 순서. 화면 머리와 끝에 같은 줄을 둔다. */
.back{margin-bottom:10px;font-size:13.5px}
.back a{border-bottom:1px solid var(--ink);color:var(--ink)}
.flow{list-style:none;padding:0;margin:12px 0 0;display:flex;flex-wrap:wrap;gap:6px 0;font-size:12.5px;color:var(--muted);counter-reset:step}
.flow li{counter-increment:step;display:flex;gap:6px;align-items:baseline;padding-right:14px;margin-right:10px;border-right:1px solid var(--rule)}
.flow li:last-child{border-right:0}
.flow li::before{content:counter(step);font-family:var(--mono);font-size:10px;color:var(--faint)}
.flow b{color:var(--ink);font-weight:600}
.tally-kicker{color:var(--faint)}
.item-top{display:flex;gap:14px;align-items:baseline;justify-content:space-between}
.item-state{font-size:12.5px;color:var(--accent);font-weight:500}
.item.finished .item-state{color:var(--muted);font-weight:400}
.item h3{font-size:1.2rem;margin-top:4px}
.item.folded .item-body{display:none}
.reopen{font:inherit;font-size:12.5px;margin-top:8px;padding:4px 10px;border:1px solid var(--rule);background:transparent;cursor:pointer}
.shot{position:relative}
.shot.cited img{outline:2px solid var(--accent);outline-offset:-2px}
.shot figcaption em{font-style:normal;color:var(--accent);margin-left:6px;font-family:var(--sans);font-weight:600}
.more-shots{margin-top:10px;font-size:12.5px;color:var(--muted)}
.more-shots summary{cursor:pointer}
.more-shots .shots{margin-top:8px}
.zoom-hint{font-size:11.5px;color:var(--faint);margin-top:8px}
.value-notes{list-style:none;padding:0;margin:2px 0 0;font-size:12.5px;color:var(--muted)}
.value-notes b{color:var(--ink);font-weight:500}
.deftext .md-h{font-weight:600;color:var(--ink);margin-top:8px}
.deftext ul{padding-left:18px;margin:4px 0}
.deftext p{margin:4px 0}
.deftext code{font-family:var(--mono);font-size:11px}
details.def .deftext{white-space:normal}
.gap-strong{color:var(--accent)!important;font-weight:600}
.gap-note{color:var(--ink)}
.obs{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0 2px}
.obs span{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);background:var(--paper)}
.obs .yes{border-color:transparent;background:color-mix(in srgb,var(--accent,#0E6B5F) 14%,transparent)}
.obs .no{color:var(--muted);text-decoration:line-through}
.obs .split{border-color:var(--warn,#b8860b);outline:1px dashed var(--warn,#b8860b)}
.obs .unsure::after{content:" ?";color:var(--muted)}
.act button:disabled{color:var(--faint);border-style:dashed;cursor:default}
#progress-bottom{display:flex;gap:16px;justify-content:space-between;align-items:center}
#progress-bottom[hidden]{display:none!important}
#export-out.flash{animation:flash 1.2s ease}
@keyframes flash{0%{box-shadow:0 0 0 3px var(--accent)}100%{box-shadow:var(--shadow)}}
#export-out code.phrase{padding:2px 8px;border-radius:6px;background:#EFF6FF;color:#1D4ED8;font-weight:600}
#progress-bottom-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bottom-actions{display:flex;gap:14px;align-items:center;flex:0 0 auto}
.bottom-actions button{font:inherit;font-size:12.5px;padding:4px 10px;border:1px solid var(--ink);background:var(--ink);color:var(--paper);cursor:pointer}
.bottom-actions a{font-size:12.5px}
/* 증거 사진은 왼쪽 단 폭만큼 — 구도·얼굴 가장자리를 보려면 썸네일로는 모자라다. */
.rail .shots figure{width:100%;min-width:0}
.rail .shots.one img{width:100%;max-width:460px;height:auto}
/* 답한 칸 — 기록 문장은 흐리지 않고, 나머지는 접는다. «바꾸기»로 다시 편다. */
.decide .cell.done{opacity:1}
.decide .cell.done:not(.open) > :not(h4):not(.c-head):not(.diff):not(.result):not(.change){display:none}
.decide .cell.done:not(.open) .diff{opacity:.55}
.cell .change{display:none;font:inherit;font-size:12.5px;margin-top:6px;padding:4px 12px;border:1px solid var(--rule);background:transparent;cursor:pointer}
.decide .cell.done:not(.open) .change{display:inline-block}
.memo{margin:0 0 14px;padding:12px 14px;border-left:3px solid var(--rule);font-size:13px;color:var(--muted)}
.memo.caution{border-left-color:var(--accent)}
.memo-title{font-weight:600;color:var(--ink);margin-bottom:4px}
footer.end{margin:60px 0 40px;font-size:13.5px;padding-top:18px;border-top:2px solid var(--ink)}
.end-title{font-weight:700;color:var(--ink);font-size:1rem;margin-bottom:4px}
.end-links{margin-top:16px;display:flex;flex-wrap:wrap;gap:10px 24px}
.end-links a{font-weight:500;color:var(--ink)}
#viewer .go{position:absolute;top:50%;translate:0 -50%;font-size:40px;line-height:1;color:#fff;background:transparent;border:0;cursor:pointer;padding:12px}
#viewer .prev{left:10px}
#viewer .next{right:10px}
#viewer img{cursor:zoom-in}
#viewer img.full{max-width:none;max-height:none;cursor:zoom-out}
#viewer{overflow:auto}

@media (min-width:900px){
  .item-body{grid-template-columns:minmax(240px,30%) minmax(0,1fr);gap:40px;align-items:start}
  .item-body.one{grid-template-columns:minmax(300px,42%) minmax(0,1fr)}
  .rail{position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto;padding-right:4px}
  .rail .shots{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;overflow:visible}
  .rail .shots.one{grid-template-columns:1fr}
  .rail .shots img{height:auto;width:100%}
}
@media (max-width:640px){.grounds dl{grid-template-columns:1fr;gap:2px 0}.grounds dd{margin-bottom:8px}.statusline{flex-direction:column}
  .wrap{width:calc(100% - 32px)}.shots img{height:130px}.diff .v{font-size:1rem}.act.notes input.reason{min-width:100%}}
"""


# 어드민 대시보드 시안의 모양 — 옅은 회색 바탕(slate-50) 위에 흰 카드, 선 대신 여백과 옅은 그림자, 강조는 차분한 파랑 하나.
# 상태는 초록·노랑·빨강 뱃지로만 말한다. 이 화면에만 건다(page_style의 토큰을 여기서 다시 정한다) — 감사 보고서는 그대로다.
THEME_STYLE = """
:root{--paper:#F8FAFC;--inset:#FFFFFF;--ink:#0F172A;--muted:#475569;--faint:#64748B;--rule:#E2E8F0;--rule-soft:#F1F5F9;
  --accent:#2563EB;--accent-soft:#EFF6FF;--accent-ink:#1D4ED8;
  --ok:#15803D;--ok-soft:#F0FDF4;--warn:#854D0E;--warn-soft:#FEFCE8;--bad:#B91C1C;--bad-soft:#FEF2F2;
  --shadow:0 1px 2px rgba(15,23,42,.04),0 8px 24px -12px rgba(15,23,42,.10);
  --sans:"IBM Plex Sans KR","Apple SD Gothic Neo",system-ui,sans-serif;--display:var(--sans)}
html,body{background:var(--paper)}
body{font-family:var(--sans);font-size:14.5px;color:var(--ink);font-variant-numeric:tabular-nums}
a{border-bottom:0;color:var(--accent)}a:hover{color:var(--accent-ink)}
button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{max-width:1240px}
.kicker{font-family:var(--sans);font-size:12px;font-weight:600;letter-spacing:.02em;text-transform:none;color:var(--faint)}
@keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

/* 머리 — 제목·안내·이름 입력·집계를 한 흰 카드 위에 */
.masthead{padding:28px 0 4px}
.masthead-top{font-family:var(--sans);font-size:12.5px;color:var(--faint)}
.masthead-top .kicker a{color:var(--accent);font-weight:600}
.masthead h1{font-family:var(--sans);font-weight:700;font-size:clamp(1.5rem,2.6vw,1.75rem);letter-spacing:-.02em;line-height:1.25;padding:10px 0 4px}
.lead{font-size:14px;line-height:1.6;color:var(--faint);max-width:72ch}
.bar{margin-top:20px;gap:12px 24px}
.bar label{font-size:13.5px;font-weight:500;color:var(--muted);display:flex;align-items:center;gap:10px}
.bar input#reviewer{font-size:14px;padding:0 14px;min-height:42px;box-sizing:border-box;border:0;border-radius:10px;background:#F1F5F9;min-width:200px}
.bar input#reviewer:focus{background:#fff;outline:2px solid var(--accent)}
.bar input[type=checkbox]{width:18px;height:18px;accent-color:var(--accent)}
.statusline{margin-top:20px;padding:16px 20px;border:0;border-radius:14px;background:#fff;box-shadow:var(--shadow);color:var(--faint);font-size:13.5px;align-items:center}
.tally b{color:var(--ink);font-weight:600}
.tally b:first-child{color:var(--accent)}
#progress-short{color:var(--accent);font-weight:600}
.hidden-count{color:var(--faint)}
#progress{font-size:13px;color:var(--faint);padding:8px 4px 0}
.notice{margin-top:14px;padding:12px 16px;border:0;border-radius:12px;background:var(--warn-soft);color:var(--warn);font-weight:500}

/* 건 — 흰 카드. 선 없이 그림자로 띄운다 */
.item{margin-top:24px;padding:24px 28px 28px;border:0;border-radius:16px;background:#fff;box-shadow:var(--shadow);animation:fade .45s ease both}
.item:last-of-type{padding-bottom:28px}
.item .kicker{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:12px}
.item h3{font-family:var(--sans);font-weight:700;font-size:1.15rem;letter-spacing:-.01em;margin-top:8px}
.item h3 small{font-size:13.5px;font-weight:400;color:var(--faint)}
.item-body{gap:28px}
.shots img{border-radius:12px}
.shots figcaption{font-family:var(--sans);font-size:12px;color:var(--faint);margin-top:6px}
.textbox,.why{font-size:13.5px;color:var(--muted)}

/* 칸 — 칸 사이는 아주 옅은 줄 하나 */
.cell{padding:22px 0 24px;border-top:1px solid var(--rule-soft)}
.cell h4{font-family:var(--sans);font-weight:600;font-size:15.5px}
.cell.done{opacity:.5}

/* 지금 GT → 제안 — 제안은 파랑 뱃지, 갈린 칸은 노랑, 지금 값은 회색으로 긋는다 */
.diff{gap:8px 20px;margin:12px 0 4px;align-items:center}
.diff .tag{font-family:var(--sans);font-size:11.5px;font-weight:600;letter-spacing:0;text-transform:none;color:var(--faint);margin-bottom:6px}
.diff .v{font-family:var(--sans);font-size:1.2rem;font-weight:600;line-height:1.3}
.diff .arrow{font-family:var(--sans);color:#94A3B8;font-size:1.1rem;padding:0;align-self:center}
.diff.proposed .now .v{color:#94A3B8;font-weight:500;text-decoration-color:#94A3B8;text-decoration-thickness:1.5px}
.diff.proposed .to .v{border-bottom:0;padding:4px 12px;border-radius:10px;background:var(--accent-soft);color:var(--accent-ink)}
.diff.proposed .to .tag{color:var(--accent)}
.diff.contested .to .v{border-bottom:0;padding:4px 12px;border-radius:10px;background:var(--warn-soft);color:var(--warn)}
.diff.contested .to .tag{color:var(--warn)}
.diff.weak .to .v,.diff.none .to .v,.diff.same .to .v{color:var(--muted);font-weight:500}

/* 판단 근거 — 회색 판 */
.grounds{margin-top:18px;padding:16px 18px;border:0;border-radius:12px;background:var(--paper)}
.grounds-kicker{font-family:var(--sans);font-size:12px;font-weight:600;letter-spacing:0;color:var(--faint)}
.grounds dl{font-size:13.5px;color:var(--muted)}
.grounds dt{color:var(--ink);font-weight:600}

/* 판정 — 주 버튼은 파랑 채움, 나머지는 흰 바탕에 옅은 테두리 */
.act{gap:8px;margin-top:14px;border:0}
.act button{font:inherit;font-size:13.5px;font-weight:600;min-height:40px;padding:0 16px;border:1px solid var(--rule);border-radius:10px;
  background:#fff;color:var(--ink);transition:background .15s,border-color .15s}
.act button:hover{background:#F8FAFC;border-color:#CBD5E1}
.act button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.act button.primary:hover{background:var(--accent-ink);border-color:var(--accent-ink)}
.act select{font-size:13.5px;min-height:40px;padding:0 12px;border:1px solid var(--rule);border-radius:10px;background:#fff}
.act.notes input.reason{font-size:13.5px;padding:0 14px;min-height:40px;box-sizing:border-box;border:0;border-radius:10px;background:#F1F5F9}
.act.notes input.reason:focus{background:#fff;outline:2px solid var(--accent)}
.act.notes label{font-size:13px;color:var(--muted)}
.result{font-size:13px;color:var(--ok)}
details.def,details.meta{font-size:12.5px;color:var(--faint)}
details.def .deftext{border-left:0;padding:12px 14px;border-radius:10px;background:var(--paper);font-size:13px}
#progress-bottom{bottom:12px;margin:16px auto 0;width:fit-content;max-width:100%;box-sizing:border-box;padding:10px 18px;border:0;border-radius:999px;
  background:#fff;color:var(--ink);font-size:13.5px;font-weight:500;box-shadow:0 8px 24px -8px rgba(15,23,42,.25)}
#offline{background:var(--bad-soft);color:var(--bad);border-radius:0 0 12px 12px}
footer{font-size:13.5px}
#viewer{background:rgba(15,23,42,.88)}
#viewer img{border-radius:12px}

@media (max-width:640px){
  .item{padding:18px 16px 20px;border-radius:14px}
  .statusline{border-radius:14px}
  .diff .v{font-size:1.05rem}
  .act button{min-height:44px}
}

/* 개편 — 시안(검수 화면 개편안)의 구조 */
.masthead{padding:24px 0 4px}
.crumbs a{color:var(--faint);font-weight:500}
.title-row{display:flex;flex-wrap:wrap;align-items:end;justify-content:space-between;gap:12px 24px;margin-top:6px}
.title-row h1{padding:0}
.who{display:flex;align-items:center;gap:10px;font-size:13.5px;font-weight:500;color:var(--muted)}
.who input{font:inherit;font-size:14px;padding:0 14px;min-height:42px;box-sizing:border-box;border:0;border-radius:10px;background:#F1F5F9;color:var(--ink);width:200px}
.who input:focus{background:#fff;outline:2px solid var(--accent)}
.statusline{display:flex;flex-wrap:wrap;gap:14px 24px;justify-content:space-between;align-items:center}
.progress-block{flex:1 1 380px;display:flex;flex-direction:column;gap:8px;min-width:0}
#progress-short{font-size:16px;font-weight:700;color:var(--ink)}
.pbar{height:8px;border-radius:999px;background:#F1F5F9;overflow:hidden}
.pbar i{display:block;height:100%;width:0;background:var(--accent);border-radius:999px;transition:width .3s}
.statusline .tally{font-size:12.5px}
.views{display:flex;gap:2px;padding:4px;border-radius:12px;background:#F1F5F9}
.views button{font:inherit;font-size:13.5px;font-weight:600;min-height:38px;padding:0 14px;border:0;border-radius:9px;background:transparent;color:var(--faint);cursor:pointer;display:flex;align-items:center;gap:8px}
.views button[aria-pressed="true"]{background:#fff;color:var(--ink);box-shadow:0 1px 2px rgba(15,23,42,.12)}
.views .count{font-size:12px;padding:1px 7px;border-radius:999px;background:#E2E8F0;color:var(--muted)}
.views button[aria-pressed="true"] .count{background:var(--accent-soft);color:var(--accent-ink)}
.views .count:empty{display:none}
.masthead .bar{margin-top:10px}
#progress{font-size:12.5px;color:var(--faint);padding:8px 2px 0;margin:0}
body.only-ask .cell.GT_HOLDS{display:none}
body.only-ask .item.finished{display:none}

/* 칸 머리 — 이름과 상태 뱃지 */
.c-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.badge{flex-shrink:0;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;background:#F1F5F9;color:var(--muted)}
.badge.s-FIX_PROPOSED,.badge.s-FILL_PROPOSED{background:var(--accent-soft);color:var(--accent-ink)}
.badge.s-CONTESTED,.badge.s-NEEDS_HUMAN_LOOK,.badge.s-NO_EVIDENCE{background:var(--warn-soft);color:var(--warn)}
.badge.s-NOT_READ{background:var(--bad-soft);color:var(--bad)}
.cell.done .badge{background:var(--ok-soft);color:var(--ok)}
.cell.done .badge{font-size:0}.cell.done .badge::before{content:"답함";font-size:12px}
.cell.held .badge{font-size:0;background:#F1F5F9;color:var(--muted)}.cell.held .badge::before{content:"보류";font-size:12px}
/* 값 버튼 — 값마다 하나. AI가 낸 값에 표시, 칠할 수 있는 제안만 칠한다 */
.act.chips{margin-top:12px}
.act button.chip{display:inline-flex;align-items:center;gap:8px;min-height:42px;padding:0 16px;border:1.5px solid var(--rule);border-radius:10px;background:#fff;font-size:14px;font-weight:600}
.act button.chip:hover{border-color:#94A3B8;background:#fff}
.act button.chip .tag{font-size:11.5px;font-weight:600;padding:1px 7px;border-radius:999px;background:var(--warn-soft);color:var(--warn)}
.act button.chip.is-now .tag{background:#F1F5F9;color:var(--muted)}
.act button.chip.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.act button.chip.primary .tag{background:rgba(255,255,255,.2);color:#fff}
.act button.chip.primary:hover{background:var(--accent-ink);border-color:var(--accent-ink)}
.act:not(.chips):not(.notes){margin-top:8px}
/* 접는 구역 — 이유·정책 밖 표시, 판단 근거 */
details.memo-add{margin-top:10px}
details.memo-add > summary,details.grounds > summary{display:inline-flex;align-items:center;min-height:36px;cursor:pointer;font-size:13.5px;font-weight:600;list-style:none}
details.memo-add > summary{color:var(--muted)}
details.memo-add > summary::-webkit-details-marker,details.grounds > summary::-webkit-details-marker{display:none}
details.memo-add > summary::before,details.grounds > summary::before{content:"+";display:inline-block;width:16px;color:var(--faint)}
details.memo-add[open] > summary::before,details.grounds[open] > summary::before{content:"–"}
details.grounds{margin-top:12px;padding:6px 16px 14px}
details.grounds > summary{color:var(--accent);letter-spacing:0;margin-bottom:0}
details.grounds[open] > summary{margin-bottom:8px}
/* 건 메모 — 노란 줄 하나, 누르면 펼친다 */
.memo{margin-bottom:14px;border-radius:12px;background:var(--paper);padding:10px 14px;border:0}
.memo.caution{background:var(--warn-soft)}
.memo details > summary{cursor:pointer;font-size:13.5px;font-weight:600;color:var(--ink);list-style:none;padding:4px 0}
.memo.caution details > summary{color:var(--warn)}
.memo details > summary::-webkit-details-marker{display:none}
.memo details > summary::after{content:" 읽기";font-weight:500;color:var(--faint)}
.memo details[open] > summary::after{content:" 접기"}
.memo p{font-size:13.5px;line-height:1.6;color:var(--muted);margin:6px 0 2px}
/* 유지 확인 — 값 칩 한 줄과 버튼 하나 */
.item-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0}
.item-state{font-size:12.5px;font-weight:600;color:var(--accent)}

/* 시안 맞춤 — 한 줄 diff, 한 줄 버튼, 접기 둘은 나란히 */
.diff{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;margin:12px 0 2px}
.diff > div{display:flex;align-items:center;gap:10px}
.diff .tag{display:inline;margin:0;font-size:13px;font-weight:500;color:var(--faint)}
.diff .v{font-size:1.05rem}
.diff .now .v{color:#94A3B8;font-style:italic;font-weight:500}
.diff.proposed .to .v,.diff.contested .to .v,.diff.weak .to .v{padding:3px 12px;border-radius:9px}
.diff.weak .to .v{background:var(--warn-soft);color:var(--warn);font-weight:600}
.diff .arrow{font-size:1rem}
.act.chips + .act{margin-top:10px}
.more{display:flex;flex-wrap:wrap;align-items:flex-start;gap:0 18px;margin-top:6px}
.more > details{margin-top:0}
.more > details[open]{flex-basis:100%}
.more > details.grounds{padding:0;background:transparent}
.more > details.grounds[open]{padding:6px 16px 14px;background:var(--paper);border-radius:12px;margin-top:6px}
.grounds .value-notes{list-style:none;padding:0;margin:0 0 10px;font-size:13px;color:var(--muted)}
.cell{padding:20px 0 22px}
/* 사진 — 틀 없이, 캡션은 작게 */
.shots figure,.rail figure{border:0!important;padding:0!important;background:transparent!important}
.shots figcaption{border:0!important;padding:4px 2px 0!important;background:transparent!important}
.zoom-hint{font-size:12px;color:var(--faint)}
.rail .memo{margin-top:12px}
.rail .why{font-size:13px;line-height:1.55;color:var(--muted)}
/* 유지 확인 — 제목과 버튼 한 줄, 값은 칩 */
.ask{margin:0 0 12px;padding:12px 14px;border-radius:12px;background:var(--warn-soft);color:var(--warn);font-size:13.5px;line-height:1.55}
.ask b{margin-right:6px}
.gist{margin:8px 0 0;font-size:13.5px;line-height:1.55;color:var(--muted)}
.gist b{color:var(--ink);font-weight:600;margin-right:6px}
figcaption em.rep{background:#F1F5F9;color:var(--muted)}
/* 버튼 줄 — 시안: 값 버튼 아래 한 줄에 «빈칸이 맞다 · 보류 … 근거 보기 · 메모 추가» */
.act:not(.chips):not(.notes){display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.act:not(.chips):not(.notes) button:not(.chip):not(.toggle):not(.primary){min-height:38px;padding:0 14px;font-weight:500;color:#334155;border:1px solid var(--rule);background:#fff}
.act .grow{flex:1 1 auto}
.act button.toggle{min-height:38px;padding:0 12px;border:0;background:transparent;font-weight:600;color:var(--accent)}
.act button.toggle.t-memo{color:var(--muted)}
.act button.toggle:hover{background:#F1F5F9}
.more > details > summary{display:none}
.more{gap:0;margin-top:0}
.more > details[open]{margin-top:10px}
.more > details.grounds[open]{padding:14px 16px}
/* 답한 칸 — 초록 «기록됨» 줄 */
.cell.done .result{margin-top:10px;padding:10px 14px;border-radius:10px;background:var(--ok-soft);color:var(--ok);font-size:13.5px}
.cell.done .result b{color:var(--ok)}
.cell.done .change{margin-top:6px;border:0;background:transparent;color:var(--ok);font-weight:600;font-size:13px;padding:4px 2px;cursor:pointer}
.once{margin:8px 0 0;padding:10px 14px;border-radius:10px;background:#fff;box-shadow:var(--shadow);font-size:13.5px;color:var(--muted)}
/* 칸 안의 간격 — 시안처럼 한 규칙: 요소 사이 12px, 값 버튼 줄과 그 아래 줄 사이 10px. 여러 겹의 margin을 쓰지 않는다 */
.decide .cell{display:flex;flex-direction:column;gap:12px;padding:22px 0 24px}
/* 간격 규칙이 «같게 본 칸은 묶음에만» 규칙을 덮지 않게 — 켜 둔 때(칸마다 펼치기)와 대체 정답 칸만 따로 보인다 */
.decide .cell.GT_HOLDS{display:flex}
body.only-ask .decide .cell.GT_HOLDS{display:none}
.decide .cell > *{margin:0!important}
.decide .cell > :empty{display:none}
.decide .cell > .act.chips + .act{margin-top:-2px!important}
.decide .cell .act{gap:8px;padding:0!important;border:0!important}
.decide .cell .diff{gap:6px 12px}
.decide .cell .c-head h4{margin:0}
.decide .cell .why,.decide .cell .gist{margin:0}
.decide .cell .more:not(:has(details[open])){display:none}
.decide .cell .change{align-self:flex-start}
.decide .gt-cell-grid > .cell:first-child{padding-top:4px}
.actions{margin-top:10px;display:flex;flex-direction:column;gap:10px}
.actions .once{margin:0}
.action-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px}
/* 제목 줄 오른쪽 — 페이지 넘김과 판정하는 사람. 반영 버튼은 진행률 카드 오른쪽(.status-side). 버튼 하나만 있는 줄을 두지 않는다. */
.title-side{display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px}
.status-side{display:flex;flex-wrap:wrap;align-items:center;gap:12px}
.actions:has(#export-out[hidden]){display:none}
#do-export{font:inherit;font-size:14px;font-weight:600;min-height:40px;padding:0 16px;border:0;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer}
#do-export:hover{background:var(--accent-ink)}#do-export:disabled{opacity:.6}
.pager{display:flex;align-items:center;gap:8px;font-size:13.5px;color:var(--muted)}
.pager button,#progress-bottom .page-next{font:inherit;font-size:13.5px;font-weight:600;min-height:36px;padding:0 12px;border:1px solid var(--rule);border-radius:10px;background:#fff;color:var(--ink);cursor:pointer}
.pager button:disabled{opacity:.4;cursor:default}
#export-out{padding:14px 16px;border-radius:12px;background:#fff;box-shadow:var(--shadow);font-size:13.5px;color:var(--muted)}
#export-out p{margin:0 0 8px}#export-out b{color:var(--ink)}
#export-out table{width:100%;border-collapse:collapse;margin-top:6px;font-size:13px}
#export-out th{text-align:left;font-size:12px;font-weight:600;color:var(--faint);padding:6px 10px;background:var(--paper)}
#export-out td{padding:6px 10px;box-shadow:inset 0 1px 0 #F1F5F9}
.item[hidden]{display:none!important}
/* 기본값 — AI도 지금 GT와 같은 칸. 지금 값 버튼이 눌린 모양으로 시작한다 */
.act button.chip.chosen{background:var(--ok-soft);border-color:#86EFAC;color:var(--ok)}
.act button.chip.chosen .tick{font-weight:700}
.act button.chip.chosen .tag{background:#fff;color:var(--ok)}
.badge.s-GT_HOLDS{background:var(--ok-soft);color:var(--ok)}
.cell.GT_HOLDS .diff{display:none}
/* 사진 — 칸(상자) 크기 안에 통째로. 세로로 긴 사진이 화면을 넘지 않게 */
.rail .shots img,.evidence .shots img{width:100%;height:auto;max-height:560px;object-fit:contain;background:#F1F5F9;border-radius:12px}
/* 고른 값 — 기록된 값의 버튼은 눌린 모양, 누르는 동안은 테두리로 반응 */
.act button.chip.picked{background:var(--ok-soft);border-color:#22C55E;color:var(--ok);box-shadow:inset 0 0 0 1px #22C55E}
.act button.chip.picked::before{content:"✓";font-weight:700;margin-right:2px}
.act button.chip.picked .tag{background:#fff;color:var(--ok)}
.act button.chip.pending{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.act button.chip:active{transform:scale(.97)}
.act button[data-decision="HOLD"].picked{background:#F1F5F9;border-color:#94A3B8}
.act button.chip{transition:background .15s,border-color .15s,box-shadow .15s,transform .08s}
/* ===== 칸 개편안 — 선택기 하나 · 점 두 개 · 글자 링크 한 줄 · 답한 칸 한 줄 ===== */
.decide .cell .c-head .badge{display:none}
.decide .cell:has(.act.chips) .diff{display:none}
.decide .cell.CONTESTED .c-head h4::after,.decide .cell.NEEDS_HUMAN_LOOK .c-head h4::after,.decide .cell.NO_EVIDENCE .c-head h4::after,.decide .cell.asking .c-head h4::after{content:"직접 봐 주세요";
  margin-left:8px;font-size:12px;font-weight:600;padding:2px 8px;border-radius:6px;background:#FEF3C7;color:#92400E;vertical-align:middle}
.decide .cell.done:not(.open) .c-head h4::after{display:none}
.ask{margin:0 0 10px;padding:10px 12px;border-radius:10px;background:#FEF3C7;color:#92400E;font-style:normal;font-size:13.5px;line-height:1.55}
.decide .cell .ask{margin:10px 0 0}
.ask .ask-here,.ask .ask-options{display:inline-block;margin-top:4px;font-size:13px}
.ask .ask-options b{font-weight:700}
.decide .cell.done:not(.open) .ask{display:none}
.decide .cell.done.open .change{display:inline-block;justify-self:end;margin-top:8px!important}
/* 선택기 — 이어 붙은 칸들 */
.decide .cell .act.chips{display:flex;gap:0;border:1px solid var(--rule);border-radius:12px;overflow:hidden;background:#fff;flex-wrap:nowrap}
.decide .cell .act.chips button.chip{flex:1 1 0;min-height:46px;margin:0;border:0;border-left:1px solid var(--rule);border-radius:0;background:#fff;color:var(--ink);
  font-size:14px;font-weight:500;justify-content:center;gap:8px;box-shadow:none;transform:none}
.decide .cell .act.chips button.chip:first-child{border-left:0}
/* 값 이름은 낱말 가운데서 끊지 않는다(«전\n신»). 좁으면 «AI 제안» 표시가 아래 줄로 내려간다. */
.decide .cell .act.chips button.chip{word-break:keep-all;flex-wrap:wrap;column-gap:6px;row-gap:0}
.decide .cell .act.chips button.chip:hover{background:#F8FAFC}
.decide .cell .act.chips button.chip.primary{background:#fff;color:var(--ink);font-weight:500}  /* 추천은 파란 점만 — 파란 채움은 사람이 고른 값 하나에만 */
.decide .cell .act.chips button.chip.picked,.decide .cell .act.chips button.chip.chosen{background:var(--accent);color:#fff;font-weight:600;box-shadow:none}
.decide .cell .act.chips button.chip.picked::before,.decide .cell .act.chips button.chip.chosen::before{content:"✓";margin-right:2px}
.decide .cell .act.chips button.chip.pending{box-shadow:inset 0 0 0 2px var(--accent)}
.decide .cell .act.chips .tick{display:none}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dot.ai{background:var(--accent)}
.dot.now{box-shadow:inset 0 0 0 1.5px #94A3B8}
.chip.picked .dot.ai,.chip.chosen .dot.ai{background:#FFFFFF}
.chip.picked .dot.now,.chip.chosen .dot.now{box-shadow:inset 0 0 0 1.5px #DBEAFE}
/* 아래 한 줄 — 범례 왼쪽, 글자 링크 오른쪽 */
.decide .cell .act.chips + .act{margin-top:0!important;gap:4px 14px;font-size:12.5px}
.decide .cell .act:not(.chips):not(.notes) button:not(.toggle){min-height:32px;padding:0;border:0;background:transparent;color:var(--faint);font-size:13px;font-weight:500}
.decide .cell .act:not(.chips):not(.notes) button:not(.toggle):hover{color:var(--ink);background:transparent}
.decide .cell .act:not(.chips):not(.notes) button.picked{color:var(--ink);font-weight:600;text-decoration:underline}
.decide .cell .act button.toggle{min-height:32px;padding:0;font-size:13px}
.legend{display:inline-flex;gap:14px;color:var(--faint);font-size:12.5px;margin-right:6px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend .dot{margin:0}
.decide .cell .gist{color:var(--faint);font-size:13px}
.decide .cell .gist b{color:var(--muted);font-weight:500}
/* 답한 칸 — 한 줄: 항목 이름 · 옆 → 앞 · 이민준 · 바꾸기 */
.decide .cell.done:not(.open){display:grid!important;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:0 14px;padding:14px 0!important}
.decide .cell.done:not(.open) > :not(.c-head):not(.result):not(.change){display:none!important}
.decide .cell.done .result{text-align:right;margin:0!important;padding:0!important;background:transparent!important;color:var(--muted)!important;font-size:13.5px}
.decide .cell.done .result b{color:var(--ink)!important}
.decide .cell.done .change{margin:0!important;padding:0!important;color:var(--accent)!important;font-size:13px}
.decide .cell.done.open .result{display:none}
/* 값 옆 글자 표시 — «AI 제안»·«지금 GT»를 그 값 바로 옆에 */
.vmark{font-size:11.5px;font-weight:600;white-space:nowrap}
.vmark.ai{color:var(--accent)}
.vmark.now{color:var(--faint);font-weight:500}
.chip.picked .vmark,.chip.chosen .vmark{color:#DBEAFE}
/* 접힌 상품 — 왼쪽에 대표 사진 작게, 오른쪽에 번호·키·다시 펼치기 */
.fold-thumb{display:none}
.item.folded{display:grid;grid-template-columns:72px minmax(0,1fr);column-gap:16px;align-items:start}
.item.folded > :not(.fold-thumb){grid-column:2}
.item.folded .reopen{justify-self:start}
.item.folded .fold-thumb{display:block;grid-column:1;grid-row:1 / span 3;width:72px;height:90px;padding:0;border:0;border-radius:10px;overflow:hidden;background:#F1F5F9;cursor:pointer}
.item.folded .fold-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.item.folded .fold-thumb:hover{box-shadow:0 0 0 2px var(--accent)}
/* «다음 후보 받기»가 도는 동안 — 돌고 있다는 것이 눈에 보이게 */
.next-live{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.next-live .muted{color:var(--faint);font-weight:400}
.spin{width:14px;height:14px;border-radius:50%;border:2px solid var(--accent-soft);border-top-color:var(--accent);animation:spin .8s linear infinite;flex-shrink:0}
@keyframes spin{to{transform:rotate(360deg)}}
.reload-now{font:inherit;font-size:13.5px;font-weight:600;min-height:36px;padding:0 14px;border:0;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer}
/* 좁은 화면 — 맨 끝에 둔다(위의 기본 규칙이 뒤에 오면 같은 무게라 이긴다). 골든셋 검수·증분 검수가 함께 쓴다.
   진행률 줄은 세로로 쌓이는데(EXTRA_STYLE), 진행률 칸의 기준 크기(380px)가 세로에서는 높이가 되어 빈 칸이 생겼다. */
@media (max-width:640px){
  .progress-block{flex:0 0 auto;width:100%}
  .title-side,.status-side{width:100%}
  .who{flex:1 1 auto;white-space:nowrap}.who input{width:100%;min-width:0}
  .masthead-top{flex-wrap:wrap;gap:4px 12px}
  /* 값이 많은 칸의 선택기는 줄을 바꾼다 — 한 줄로 이으면 화면 오른쪽 밖으로 밀린다 */
  .decide .cell .act.chips{flex-wrap:wrap}
  .decide .cell .act.chips button.chip{flex:1 1 30%;min-width:0;border-top:1px solid var(--rule);margin-top:-1px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


# 사이드바 — 홈 · 정책 · 골든셋, 그 아래 과제 목록. 과제 목록은 첫 화면과 같은 /gt-tasks(JSON)에서 읽는다(화면이 수를 세지 않는다).
# 정책·골든셋은 이 과제의 화면 폴더에 함께 만든 읽기 전용 두 장이다. 파일을 직접 열었으면(서버 없음) 목록만 비어 있다.
SIDEBAR_ICONS = {
    "review": "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9",
    "home": "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
    "policy": "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5",
    "golden": "M3 5h18M3 12h18M3 19h18M8 5v14",
    "incr": "M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z",
}


def sidebar_html(active: str, task_id: str | None, base: str = "", *, has_gt: bool = True) -> str:
    """`active`는 home·review·incr·policy·golden. `base`는 이 과제 화면 폴더로 가는 앞머리(같은 폴더면 빈 문자열).
    과제가 없는 화면(증분 검수)에서는 골든셋 검수·정책·골든셋이 첫 화면으로 가고, 과제는 펼쳐지는 목록에서 고른다."""
    e = html.escape
    if task_id is None:
        base = None

    def link(key: str, label: str, href: str | None) -> str:
        current = ' aria-current="page"' if active == key or (key == "home" and active == "home") else ""
        # 마우스를 올리거나 키보드로 오면 오른쪽에 과제 목록이 펼쳐진다 — 어느 과제의 골든셋 검수·증분 검수·정책·골든셋으로 갈지
        # 고른다(목록은 SIDEBAR_SCRIPT가 채운다 — 골든셋 쪽은 /gt-tasks, 증분 검수는 /incr-tasks).
        menu = key != "home"  # 홈은 첫 화면 하나 — 고를 것이 없다
        if href is None:
            return (f'<div class="side-item"><span class="side-link side-disabled" aria-disabled="true" title="이 작업은 GT 검수 기능을 제공하지 않습니다">'
                    f'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '
                    f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{SIDEBAR_ICONS[key]}"></path></svg>{e(label)} '
                    '<small>이 작업은 제공하지 않음</small></span></div>')
        return (f'<div class="side-item"{f' data-menu="{key}"' if menu else ""}><a class="side-link{" on" if current else ""}" href="{e(href)}"{current}'
                f'{' aria-haspopup="true"' if menu else ""}>'
                f'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '
                f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{SIDEBAR_ICONS[key]}"></path></svg>{e(label)}</a>'
                + (f'<div class="flyout" role="menu" aria-label="{e(label)} — 고르기"></div>' if menu else "") + '</div>')

    return (f'<aside class="side" data-task="{e(task_id or "")}" data-base="{e(base or "")}">'
            '<a class="side-brand" href="/"><span class="side-logo" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
            'stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"></path></svg></span>데이터 운영</a>'
            '<nav class="side-nav" aria-label="주 메뉴">'
            + link("home", "홈", "/") + link("review", "골든셋 검수", ("/gt/" + urllib.parse.quote(task_id) if task_id and has_gt else "/" if has_gt else None))
            + link("incr", "증분 검수", "/incr") + link("policy", "정책", "/" if base is None else base + "policy.html")
            + link("golden", "골든셋", ("/" if base is None else base + "golden.html") if has_gt else None)
            + '</nav>'
            + SIDEBAR_HELP
            + '</aside>')


# 사이드바 맨 아래 도움말 — «Claude에게 말해 주세요»가 어디인지와 화면의 말뜻. 코딩을 모르는 운영팀이 막히는 첫 자리다.
SIDEBAR_HELP = '<details class="side-help"><summary>도움말 · 용어</summary><p><b>Claude는 어디에?</b> 이 도구가 설치된 컴퓨터의 Claude 앱(Claude Code) 채팅 창입니다. 화면이 «Claude에게 ○○라고 말해 주세요»라고 하면 그 문장을 그 창에 적으세요. 앱이 없거나 답이 없으면 그 문장을 담당 개발자에게 보내 주세요.</p><dl><dt>골든셋 · GT</dt><dd>정답으로 쓰는 라벨 모음. AI를 채점하는 기준이라 틀리면 고칩니다.</dd><dt>골든셋 검수</dt><dd>있는 정답을 AI 둘(정답을 모르는 AI · 기존 정답과 판독이 다를 때 정책·근거로 다시 확인하는 AI)이 검토하고, 고칠 만한 칸만 올립니다.</dd><dt>증분 검수 · 묶음</dt><dd>새로 들어온 상품에 AI가 먼저 값을 붙이면 사람이 확정합니다. 한 번에 넣은 상품들이 묶음 하나.</dd><dt>AI 제안 · AI 의견</dt><dd>제안은 AI가 확신한 값, 의견은 확신하지 못한 값 — 의견 칸(«직접 봐 주세요»)은 사진을 보고 직접 고릅니다.</dd><dt>관찰 · 값 규칙</dt><dd>어떤 칸은 AI가 값을 고르지 않고 사진의 사실(예/아니오)에만 답합니다 — 화면의 «AI가 본 것». 값은 정책의 값 규칙 표가 정합니다. 두 번 본 AI의 답이 갈린 항목은 «갈림»으로 표시되고 사람이 봅니다.</dd><dt>보류</dt><dd>지금 못 정한 칸. 답으로 세지만 나중에 값을 누르면 바뀝니다.</dd><dt>빈칸이 맞다 · 비워야 한다</dt><dd>앞은 비어 있던 칸이 비어 있는 게 맞다, 뒤는 값이 있지만 지워야 한다.</dd><dt>반영하기</dt><dd>답한 값을 결과(GT 파일 또는 증분 결과 파일)에 넣습니다. 잘못 넣었으면 Claude에게 «되돌려줘».</dd></dl></details>'

SIDEBAR_SCRIPT = r"""
(() => {
  const side = document.querySelector('aside.side');
  if (!side || location.protocol === 'file:') return;
  const me = side.dataset.task;
  // 증분 화면은 과제를 주소(?task=)로 안다 — 그 과제를 목록에서 «지금»으로 칠한다.
  const incrMe = location.pathname === '/incr' ? new URLSearchParams(location.search).get('task') : null;
  const currentTask = me || incrMe;
  const make = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  // 메뉴 하나를 채운다 — 네 메뉴(골든셋 검수·증분 검수·정책·골든셋)가 같은 함수를 쓴다. 과제 이름은 두 목록이 같은 이름(name)이고,
  // 수는 서버가 센 그대로다. 부모 줄의 수는 자식 수의 합(수가 있는 메뉴만). 과제 없는 화면(증분)에서 부모 링크가 첫 화면(«/»)이면
  // 첫 과제로 보낸다 — 첫 화면의 사이드바와 같은 동작.
  const fill = (key, rows) => {
    const item = side.querySelector('.side-item[data-menu="' + key + '"]');
    if (!item) return;
    const menu = item.querySelector('.flyout'); menu.textContent = '';
    rows.forEach(row => {
      const a = make('a', 'flyout-link' + (row.on ? ' on' : '') + (row.empty ? ' empty' : ''));
      a.href = row.href; a.setAttribute('role', 'menuitem');
      a.appendChild(make('span', null, row.name));
      if (row.count) a.appendChild(make('span', 'side-count', String(row.count)));
      else if (row.empty) a.appendChild(make('span', 'side-empty', row.empty));
      menu.appendChild(a);
    });
    item.classList.toggle('has-menu', menu.children.length > 0);
    const link = item.querySelector('a.side-link');
    if (!link) return;
    link.setAttribute('aria-haspopup', 'true');
    if (link.getAttribute('href') === '/' && rows.length) link.href = (rows.find(row => row.on) || rows.find(row => !row.empty) || rows[0]).href;
    const total = rows.reduce((sum, row) => sum + (row.count || 0), 0);
    let badge = link.querySelector('.side-count');
    if (key === 'review' || key === 'incr') {
      if (!badge) { badge = make('span', 'side-count'); link.appendChild(badge); }
      badge.textContent = String(total); badge.hidden = !total;
    }
  };
  fetch('/gt-tasks', {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(listing => {
    if (!listing) return;
    const pages = {review: task => task.page || ('/gt/' + encodeURIComponent(task.task)),
                   policy: task => task.policyPage || ('/f/' + encodeURIComponent(task.task) + '/gt-review/policy.html'),
                   golden: task => '/f/' + encodeURIComponent(task.task) + '/gt-review/golden.html'};
    ['review', 'policy', 'golden'].forEach(key => {
      const tasks = key === 'policy' ? (listing.policies || listing.tasks || []) : (listing.tasks || []);
      fill(key, tasks.filter(task => key === 'review' || task.pages || task.prepared).map(task => ({
        href: pages[key](task), name: task.name, on: task.task === currentTask,
        count: key === 'review' ? (task.waitingForHuman || 0) + (task.notRead || 0) : 0})));
    });
  }).catch(() => {});
  fetch('/incr-tasks', {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(incr => {
    if (!incr) return;
    fill('incr', (incr.tasks || []).map(task => ({href: '/incr?task=' + encodeURIComponent(task.task), name: task.menuName || task.name,
                                                on: task.task === incrMe, count: task.itemsLeft || 0,
                                                empty: (task.batches || []).length ? '' : '증분 없음'})));
  }).catch(() => {});

  // 메뉴 글쇠 — ↓ 목록으로 들어가 다음, ↑ 이전(맨 위에서 부모로), Esc 닫고 부모로. 골든셋·증분·정책·골든셋 메뉴가 같다.
  side.addEventListener('keydown', (event) => {
    const item = event.target.closest('.side-item.has-menu');
    if (!item || !['ArrowDown', 'ArrowUp', 'Escape'].includes(event.key)) return;
    item.classList.remove('closed');
    const parent = item.querySelector('a.side-link');
    const links = [...item.querySelectorAll('.flyout a')];
    const now = links.indexOf(event.target);
    event.preventDefault();
    if (event.key === 'Escape') { item.classList.add('closed'); parent.focus(); return; }  // 포커스는 부모에 남긴다 — 다음 Tab이 제자리에서 이어진다
    if (event.key === 'ArrowDown') (links[now + 1] || links[0]).focus();
    else if (now <= 0) parent.focus(); else links[now - 1].focus();
  });
  side.addEventListener('focusout', (event) => { const item = event.target.closest('.side-item'); if (item && !item.contains(event.relatedTarget)) item.classList.remove('closed'); });
  side.addEventListener('mouseleave', () => side.querySelectorAll('.side-item.closed').forEach((item) => item.classList.remove('closed')));
  // 감사 사이클(과제 목록 밖의 GT)로 가는 문은 사이드바에 두지 않는다 — 운영 화면은 골든셋 검수 과제만 다룬다. 감사 화면은 /audit 주소로 연다.
})();
"""

SIDEBAR_STYLE = """
.side-help{margin-top:auto;padding:12px 14px;border-radius:12px;background:var(--paper);font-size:12.5px;color:var(--muted);line-height:1.55}
.side-help > summary{cursor:pointer;font-weight:600;color:var(--ink);list-style:none}
.side-help > summary::-webkit-details-marker{display:none}
.side-help > summary::before{content:"? ";color:var(--accent);font-weight:700}
.side-help p{margin:8px 0}
.side-help dl{margin:0}
.side-help dt{margin-top:6px;font-weight:600;color:var(--ink)}
.side-help dd{margin:0}
.side-help[open]{max-height:60vh;overflow:auto}
@media (max-width:900px){.side-help{display:none}}

/* 밀도 — 화면 전체를 한 비율로 줄인다(사용자가 고른 촘촘한 크기). 바꿀 때는 이 한 줄만. */
:root{--zoom:.75}
html{zoom:var(--zoom)}
.shell{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:calc(100vh / var(--zoom))}
@media (max-width:900px){.shell{grid-template-columns:minmax(0,1fr)}}
.shell > .wrap{width:min(1180px,calc(100% - 80px));margin:0 auto}
.side{position:sticky;top:0;align-self:start;height:calc(100vh / var(--zoom));box-sizing:border-box;padding:24px 14px;background:#fff;box-shadow:1px 0 0 #F1F5F9;
  display:flex;flex-direction:column;gap:26px;overflow:auto}
.side a{border-bottom:0;text-decoration:none}
.side-brand{display:flex;align-items:center;gap:10px;padding:0 10px;color:var(--ink);font-size:16px;font-weight:700}
.side-logo{width:30px;height:30px;border-radius:9px;background:var(--accent);display:flex;align-items:center;justify-content:center}
.side-nav{display:flex;flex-direction:column;gap:2px}
.side-link,.side-task{display:flex;align-items:center;gap:12px;min-height:42px;padding:0 12px;border-radius:10px;color:var(--muted);font-size:14.5px;font-weight:500}
.side-link:hover,.side-task:hover{background:#F1F5F9;color:var(--ink)}
.side-disabled{opacity:.68;cursor:not-allowed}
.side-disabled small{margin-left:auto;font-size:10.5px;font-weight:500;line-height:1.2;text-align:right}
.side{overflow:visible}
.side-item{position:relative}
.flyout{display:none;position:absolute;left:calc(100% + 6px);top:0;z-index:20;min-width:220px;padding:6px;border-radius:12px;background:#fff;
  box-shadow:0 12px 32px -8px rgba(15,23,42,.25),0 0 0 1px var(--rule)}
.flyout::before{content:"";position:absolute;left:-8px;top:0;width:8px;height:100%}
.side-item.has-menu:hover .flyout,.side-item.has-menu:focus-within .flyout{display:flex;flex-direction:column;gap:2px}
.side-item.closed .flyout{display:none!important}  /* Esc로 닫은 메뉴 — 포커스는 부모에 남는다 */
.flyout-link{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:38px;padding:0 12px;border-radius:8px;font-size:14px;font-weight:500;color:var(--muted);white-space:nowrap}
.flyout-link:hover,.flyout-link:focus-visible{background:#F1F5F9;color:var(--ink)}
.flyout-link.on{background:var(--accent-soft);color:var(--accent-ink);font-weight:600}
.side-link.on,.side-task.on{background:var(--accent-soft);color:var(--accent-ink);font-weight:600}
.side-task{justify-content:space-between;font-size:14px}
.side-count{font-size:12px;font-weight:600;padding:1px 8px;border-radius:999px;background:#F1F5F9;color:var(--muted)}
.side-link .side-count{margin-left:auto}
.side-count[hidden]{display:none}
.side-empty{font-size:12px;color:#94A3B8}
.flyout-link.empty{color:var(--faint)}
.side-task.on .side-count{background:#fff;color:var(--accent-ink)}
.side-kicker{padding:0 12px 6px;font-size:12px;font-weight:600;color:var(--faint);margin:0}
.side-others{margin-top:auto;padding:14px;border-radius:12px;background:var(--paper)}
.side-others .side-kicker{padding:0 0 4px}
.side-door{display:block;font-size:13.5px;font-weight:500;color:var(--accent)}
@media (max-width:900px){
  .shell{grid-template-columns:minmax(0,1fr)}  /* 1fr(=minmax(auto,1fr))이면 한 줄 메뉴의 폭만큼 화면 전체가 넓어진다 */
  .side{min-width:0;position:static;height:auto;flex-direction:row;flex-wrap:wrap;align-items:center;gap:8px 16px;padding:12px 16px}
  /* 좁은 화면 — 메뉴는 줄을 바꿔 두 줄이 된다. 가로 스크롤(overflow)은 펼침 목록까지 잘라 내므로 쓰지 않는다 */
  .side-nav{flex-direction:row;flex-wrap:wrap;max-width:100%}.side-group,.side-others{display:none}
  .side-link{white-space:nowrap}  /* «골든셋 검/수»처럼 낱말 가운데서 끊지 않는다 */
  .shell > .wrap{width:calc(100% - 32px)}
  .flyout{left:0;top:calc(100% + 4px)}.flyout::before{display:none}
}
"""

VIEW_SCRIPT = r"""
// 보기 — 전체 · 답할 칸 · 유지 확인. 진행률을 셀 때마다 칩의 수와 막대도 같이 맞춘다(같은 DOM을 같은 규칙으로 본다).
(() => {
  const buttons = [...document.querySelectorAll('.views button')];
  const setView = view => {
    document.body.classList.toggle('only-ask', view === 'ask');
    document.body.classList.toggle('only-holds', view === 'holds');
    buttons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
  };
  buttons.forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
  const open = b => !b.classList.contains('done') && !b.classList.contains('stale') && !b.classList.contains('held');
  const decorate = () => {
    const boxes = [...document.querySelectorAll('.cell[data-actionable="1"], .cell.NOT_READ')].filter(b => !b.classList.contains('stale'));
    const holds = [...document.querySelectorAll('.cell.GT_HOLDS')].filter(b => !b.classList.contains('stale'));
    const left = boxes.filter(open).length, holdLeft = holds.filter(open).length;
    const holdItems = [...document.querySelectorAll('.item')].filter(item => [...item.querySelectorAll('.cell.GT_HOLDS')].some(open)).length;
    const all = boxes.length + holds.length, done = all - left - holdLeft;
    const ask = document.getElementById('count-ask'), keep = document.getElementById('count-holds'), bar = document.getElementById('pbar');
    if (ask) ask.textContent = left;
    if (keep) keep.textContent = holdItems;
    if (bar) bar.style.width = (all ? Math.round(done * 100 / all) : 100) + '%';
    const long = document.getElementById('progress');
    if (long) long.hidden = !/읽지 못했|못 읽은|원본이 바뀐|같이 맞다고|보류/.test(long.textContent);
  };
  // 근거·메모는 버튼 줄 오른쪽의 글자 버튼으로 연다(시안) — 접는 구역은 그대로 칸 아래에 있다.
  document.querySelectorAll('.cell button.toggle').forEach(button => {
    const why = button.classList.contains('t-why');
    const box = button.closest('.cell').querySelector(why ? 'details.grounds' : 'details.memo-add');
    if (!box) { button.hidden = true; return; }
    const sync = () => {
      button.setAttribute('aria-expanded', String(box.open));
      button.textContent = why ? (box.open ? '근거 접기' : '근거 보기') : (box.open ? '메모 접기' : '메모 추가');
    };
    sync();
    button.addEventListener('click', () => { box.open = !box.open; sync(); });
    box.addEventListener('toggle', sync);
  });
  const base = progress;
  progress = function () { base(); decorate(); };
  decorate();
})();
"""


PAGE_SCRIPT = r"""
// 페이지 — 배치를 PAGE_SIZE건씩 나눠 보인다. 마지막 페이지의 «다음»은 새 배치라 Claude에게 부탁한다(AI가 새로 읽어야 한다).
(() => {
  const items = [...document.querySelectorAll('.item[data-page]')];
  const pages = Math.max(1, ...items.map(item => Number(item.dataset.page)));
  let page = Math.min(pages, Math.max(1, Number((location.hash.match(/^#p(\d+)$/) || [])[1]) || 1));
  const nextButtons = [...document.querySelectorAll('.page-next')];
  const NEXT_LABEL = '다음 후보 받기 →';
  const show = (to, scroll = true) => {
    page = Math.min(pages, Math.max(1, to));
    items.forEach(item => { item.hidden = Number(item.dataset.page) !== page; });
    const now = document.getElementById('page-now'); if (now) now.textContent = `${page} / ${pages} 페이지`;
    const prev = document.getElementById('page-prev'); if (prev) prev.disabled = page === 1;
    nextButtons.forEach(b => { b.textContent = page < pages ? '다음 페이지 →' : NEXT_LABEL; });
    if (history.replaceState) history.replaceState(null, '', page > 1 ? `#p${page}` : location.pathname);
    if (scroll) window.scrollTo({top: 0, behavior: 'smooth'});
  };
  show(page, false);
  const prev = document.getElementById('page-prev'); if (prev) prev.addEventListener('click', () => show(page - 1));
  const nextOut = () => { const out = document.getElementById('export-out'); out.hidden = false; return out; };
  // 서버가 도는 준비를 알려 준다 — 한 번에 하나만 돈다(gt_next.py). 이 과제의 준비가 끝나면 새 화면으로 다시 읽는다.
  // started — 지켜보는 러너의 pid(서버가 POST 답에 실어 준다). 그 pid의 상태만 이번 결과로 읽는다: 러너가 막 떠서 아직 제 상태를
  // 쓰기 전이면 남아 있는 것은 지난 실행의 상태이고, 그것을 이번 결과로 읽으면 옛 실패 문장이 뜨거나 옛 화면으로 새로고침한다.
  const watchNext = async (started, tries = 0) => {
    let answer;
    try { answer = await (await fetch('/gt-next?task=' + encodeURIComponent(data.profileId), {cache: 'no-store'})).json(); } catch (e) { answer = null; }
    if (!answer) { nextOut().textContent = '서버에 닿지 못했습니다 — Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요.'; return; }
    const mine = answer.task === data.profileId;
    if (answer.running) {
      nextButtons.forEach(b => { b.disabled = true; b.textContent = mine ? 'AI가 보는 중…' : '다른 과제 준비 중…'; });
      const since = answer.startedAt ? Math.max(0, Math.round((Date.now() - Date.parse(answer.startedAt)) / 60000)) : null;
      const box = nextOut();
      const first = !box.querySelector('.next-live');
      box.innerHTML = `<p class="next-live"><span class="spin" aria-hidden="true"></span><b>${esc(mine ? (answer.message || 'AI가 보는 중입니다.') : '이미 AI가 검수중입니다.')}</b>`
        + (since !== null ? ` <span class="muted">(${since}분째)</span>` : '') + '</p>'
        + (mine ? '<p>이 탭을 닫아도 계속 돕니다. 끝나면 이 화면이 새 후보로 바뀝니다.</p>' : '');
      if (started && first) box.scrollIntoView({block: 'center', behavior: 'smooth'});
      clearTimeout(watchNext.timer);
      watchNext.timer = setTimeout(() => watchNext(started || (mine && answer.pid)), 4000);
      return;
    }
    if (started && answer.pid !== started && tries < 15) {
      // 아직 그 러너의 상태가 아니다(지난 실행의 것) — 조금 뒤 다시 본다.
      clearTimeout(watchNext.timer);
      watchNext.timer = setTimeout(() => watchNext(started, tries + 1), 1000);
      return;
    }
    nextButtons.forEach(b => { b.disabled = false; b.textContent = page < pages ? '다음 페이지 →' : NEXT_LABEL; });
    if (!started || !mine || answer.pid !== started) return;
    if (answer.phase === 'done' && answer.newScreen) { location.href = location.pathname; return; }
    if (answer.phase === 'done') {
      // 볼 칸이 없다 — 버튼을 다시 켜 두면 «눌러도 아무 일이 없다»로 보인다. 끝났다는 것을 분명히 말하고 버튼을 잠근다.
      nextButtons.forEach(b => { b.disabled = true; b.textContent = '남은 후보 없음'; });
      const done = nextOut();
      done.innerHTML = `<p><b>이 GT의 검수 후보를 모두 봤습니다.</b> ${esc(answer.message || '')}</p>`
        + '<p>GT나 정책(정의 문서)이 바뀌면 그 칸이 다시 후보가 됩니다.</p>';
      done.scrollIntoView({block: 'center', behavior: 'smooth'});
      return;
    }
    // 실패는 문장을 남긴다 — 바로 새로고침하면 문장이 사라져 «아무 반응 없음»으로 보인다. 새 화면(«못 읽음»)은 사람이 누를 때 연다.
    const box = nextOut();
    box.innerHTML = `<p><b>${esc(answer.phase === 'failed' ? '다음 후보를 받지 못했습니다.' : '준비가 끝났습니다.')}</b> ${esc(answer.message || '')}</p>`
      + (answer.phase === 'failed' ? '<p class="then"><button type="button" class="reload-now">화면 새로 보기</button></p>' : '');
    const again = box.querySelector('.reload-now'); if (again) again.addEventListener('click', () => { location.href = location.pathname; });
    box.scrollIntoView({block: 'center', behavior: 'smooth'});
  };
  const goNext = async () => {
    if (page < pages) return show(page + 1);
    if (served) {
      // 답하지 않은 칸은 다음에 다시 나오지만, 모르고 넘어가지 않게 한 번 묻는다(스킬의 «다음 거»와 같은 물음).
      const left = [...document.querySelectorAll('.cell[data-actionable="1"], .cell.NOT_READ')]
        .filter(b => !['done', 'held', 'stale'].some(name => b.classList.contains(name))).length;
      if (left && !confirm(`이 화면에 답하지 않은 칸이 ${left}개 있습니다. 새 후보로 넘어갈까요?\n(답하지 않은 칸은 다음에 다시 나옵니다)`)) return;
      nextButtons.forEach(b => { b.disabled = true; b.textContent = '시작하는 중…'; });
      let result;
      try {
        result = await (await fetch('/gt-next', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                 body: JSON.stringify({task: data.profileId})})).json();
      } catch (e) { result = {ok: false, error: '서버에 닿지 못했습니다 — Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요.'}; }
      if (!result.ok) {
        nextButtons.forEach(b => { b.disabled = false; b.textContent = NEXT_LABEL; });
        nextOut().innerHTML = `<p>${esc(result.error || '시작하지 못했습니다.')}</p>`;
        return;
      }
      return watchNext(result.pid);
    }
    // 파일을 직접 열었다(서버 없음) — 요청 문장을 복사해 Claude에게 건넨다.
    const phrase = `${data.callName} 다음 거`;
    let copied = true;
    try { await navigator.clipboard.writeText(phrase); } catch (e) {
      const area = document.createElement('textarea');
      area.value = phrase; area.style.position = 'fixed'; area.style.opacity = '0';
      document.body.appendChild(area); area.select();
      try { copied = document.execCommand('copy'); } catch (err) { copied = false; }
      area.remove();
    }
    const out = nextOut();
    out.innerHTML = `<p>${copied ? '<b>복사했습니다.</b> ' : ''}서버 없이 연 화면이라 Claude가 준비합니다 — Claude 창에 `
      + `<code class="phrase">${esc(phrase)}</code>${copied ? '를 붙여 넣어 주세요.' : '를 적어 주세요(복사하지 못했습니다).'}</p>`;
    out.scrollIntoView({block: 'center', behavior: 'smooth'});
  };
  if (served) watchNext(false);  // 다시 연 탭 — 도는 준비가 있으면 버튼을 잠그고 끝나기를 기다린다
  nextButtons.forEach(b => b.addEventListener('click', goNext));
  // «다음 안 한 칸»이 다른 페이지의 칸을 가리키면 그 페이지로 먼저 간다.
  document.addEventListener('click', event => {
    if (!event.target.closest || !event.target.closest('#next-open')) return;
    const target = nextOpenTarget(); window.NEXT_TARGET = target;
    const item = target && target.closest('.item[data-page]');
    if (item && Number(item.dataset.page) !== page) show(Number(item.dataset.page), false);
  }, true);

  // «반영하기» — 원장에서 고칠 목록을 만들고 곧바로 원본 GT에 넣는다. 상류(시트)가 원본인 과제만 목록까지.
  const button = document.getElementById('do-export');
  if (!button) return;
  button.addEventListener('click', async () => {
    const out = document.getElementById('export-out');
    if (!served) { out.hidden = false; out.textContent = '이 파일을 직접 열었습니다 — Claude에게 «GT 화면 열어줘»라고 말해 주세요.'; return; }
    button.disabled = true; out.hidden = false; out.textContent = data.upstreamNote ? '목록을 만드는 중…' : 'GT에 넣는 중…';
    try {
      const response = await fetch('/gt-export', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                   body: JSON.stringify({task: data.profileId})});
      const result = await response.json();
      if (!result.ok) { out.textContent = result.error || '목록을 만들지 못했습니다.'; return; }
      const sum = result.summary;
      // 원본이 이 레포의 파일이면 누른 순간 들어갔다. 상류(시트)가 원본이면 목록까지만 — 붙여 넣기는 사람이 한다.
      // 결과는 한 줄로만 — 표도, 다음으로 가는 버튼도 붙이지 않는다. 누른 뒤 무엇이 새로 뜨면 «뭐지?» 하고 멈춘다.
      // 페이지 이동은 화면 위아래의 제자리 버튼이 한다. 무엇이 바뀌었는지는 «올려줘»의 PR에서 본다.
      // 넣을 것이 없을 때는 수를 쓰지 않는다 — «고친 칸 0»은 «고친 게 없다»로 읽힌다(실은 이미 다 들어갔다).
      const line = data.upstreamNote
        ? `<b>고칠 칸 ${sum.corrections} · 유지 확인 ${sum.confirmations}</b> — 목록을 만들었습니다. <b>원본 GT는 아직 그대로입니다.</b> Claude에게 «${esc(data.callName)} 반영해줘»라고 하시면 ${esc(data.upstreamNote)}에 붙여 넣을 Excel 목록을 열어 드립니다.`
        : sum.applied
          ? `<b>GT에 넣었습니다</b> — 고친 칸 ${sum.corrections} · 유지 확인 ${sum.confirmations} (바뀐 줄 ${sum.linesChanged}). 넣기 전 원본은 옆에 사본으로 남겼습니다. 팀에 올리려면 Claude에게 «올려줘»라고 말해 주세요.`
          : '<b>새로 넣을 판정이 없습니다</b> — 지금까지 기록된 판정은 모두 GT에 들어가 있습니다.';
      out.innerHTML = `<p>${line}</p>`
        + ((sum.outOfPolicy || []).length ? `<p>정책에서 뺀 값으로 고친 판정 ${sum.outOfPolicy.length}개는 넣지 않았습니다 — 다음 화면에 다시 나옵니다.</p>` : '');
    } catch (e) {
      out.textContent = '서버에 닿지 못했습니다 — Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요.';
    } finally {
      button.disabled = false;
    }
  });
})();
"""


def _label(field: dict[str, Any], value: Any) -> str:
    """사람에게 보이는 값 이름. 코드는 이름이 없을 때만 — 코드는 값 옆에 작은 글씨(`_code`)로 한 번만 둔다."""
    if value is None or value == "":
        return "(빈칸)"
    names = field.get("labelNames") or {}
    parts = str(value).split(MANY_SEPARATOR) if field.get("cardinality") == "many" else [str(value)]
    return " + ".join(names.get(part, part) for part in parts)


def _code(field: dict[str, Any], value: Any) -> str:
    if value is None or value == "":
        return ""
    names = field.get("labelNames") or {}
    parts = str(value).split(MANY_SEPARATOR) if field.get("cardinality") == "many" else [str(value)]
    shown = [part for part in parts if part in names]
    # 코드는 운영팀에게 잡음이다 — 화면에는 싣지 않고 마우스를 올렸을 때만(기록용 정보에도 있다).
    return f'<span class="code" hidden>{html.escape(" + ".join(shown))}</span>' if shown else ""


def _cell_html(item: dict[str, Any], cell: dict[str, Any], field: dict[str, Any], signals: dict[str, Any],
               clear_ok: bool = True, batch: str | None = None, caution: bool = False) -> str:
    e = html.escape
    status = STATUS[cell["status"]]
    reading = cell.get("reading") or {}
    rebuttal = cell.get("rebuttal") or {}
    reasons = " · ".join(signals.get(signal, {}).get("label", signal) for signal in cell["signals"])
    contradiction = "".join(
        f'<p class="why">모순: {e(c["text"] or c["constraint"] or "")} — 지금 {e(_label(field, c["actual"]))}</p>'
        for c in cell.get("contradictions") or []
    )
    proposal = cell.get("proposal")
    many = field.get("cardinality") == "many"
    labels = [str(label) for label in field.get("labels") or []]
    current = cell.get("current")
    # 미리 골라 두지 않는다 — 제안값이 골라져 있으면 «이 값으로 고치기»가 «제안대로»와 같은 일을 하는 둘째 버튼이 된다.
    options = "".join(f'<option value="{e(label)}">{e(_label(field, label))}</option>' for label in labels)
    buttons = []
    # 판독자가 «정의에 없는 경계»라고 짚은 칸은 한쪽을 칠하지 않는다 — 사람이 경계를 정할 자리다.
    # 사람이 확정한 GT(TRUSTED)를 뒤집자는 제안도 칠하지 않는다 — 계약이 «뒤집으려면 기준이 높다»고 한 값을 칠한 버튼 한 번에 덮지 않게.
    # 같은 건의 다른 칸에서 AI가 기준 밖 경우를 짚었으면(파는 상품이 사진에 없는 것 같은) 이 건의 어느 제안도 칠하지 않는다.
    contested = (cell["status"] == "CONTESTED" or bool(reading.get("definitionGap")) or caution
                 or (cell.get("authority") == "TRUSTED" and cell["status"] == "FIX_PROPOSED"))
    if proposal and cell["status"] in ("FIX_PROPOSED", "FILL_PROPOSED", "CONTESTED"):
        # 두 눈이 갈린 칸은 한쪽을 칠해 유도하지 않는다. 판독 값과 GT 유지를 같은 무게로 둔다.
        label = f"AI 제안대로 → {_label(field, proposal)}"
        buttons.append(f'<button class="{"" if contested else "primary"}" data-decision="CORRECT" data-value="{e(proposal)}">{e(label)}</button>')
    # 확신 낮은 판독도 허용값 안의 값이면 한 번에 고를 수 있게 — 칠하지 않은(무게 없는) 버튼으로 둔다.
    low_value = reading.get("value") if cell["status"] == "NEEDS_HUMAN_LOOK" else None
    if low_value and many:
        # 값 여럿은 화면·기록과 같은 모양으로(빈·겹친 조각 빼고 정렬) — 순서만 다른 같은 집합에 «고치기» 버튼이 생기지 않게.
        low_value = MANY_SEPARATOR.join(sorted({part for part in str(low_value).split(MANY_SEPARATOR) if part})) or None
    unknown = field.get("unknownLabel")
    # 확신 낮은 판독이 곧 «가를 수 없음» 값이면 버튼을 하나만 둔다 — 이름이 다른 두 버튼이 같은 판정을 남기면 사람은 둘이 다른 뜻인 줄 안다.
    if (low_value and low_value != current and low_value != unknown
            and all(part in labels for part in str(low_value).split(MANY_SEPARATOR))):
        buttons.append(f'<button data-decision="CORRECT" data-value="{e(low_value)}">AI 제안대로 → {e(_label(field, low_value))}</button>')
    in_range = current is not None and all(part in labels for part in str(current).split(MANY_SEPARATOR))
    if current is not None and in_range:
        buttons.append('<button data-decision="CONFIRM">지금 GT가 맞다</button>')
    if unknown and unknown != current and unknown != proposal:
        unknown_name = _label(field, unknown)
        buttons.append(f'<button data-decision="CORRECT" data-value="{e(unknown)}">{e(unknown_name)}{josa(unknown_name, "으로")} 고치기</button>')
    if current is None:
        buttons.append('<button data-decision="LEAVE_EMPTY">빈칸이 맞다</button>')
    elif clear_ok:
        buttons.append('<button data-decision="CLEAR">비워야 한다</button>')
    # 값이 몇 개 안 되는 칸은 값마다 버튼 하나 — «제안대로» 버튼과 «다른 값…» 목록이 같은 일을 두 번 하지 않게.
    # AI가 낸 값에는 표시를 붙이고, 칠할 수 있는 제안(갈리지 않은 제안)만 칠한다. 지금 GT 값의 버튼이 곧 «지금 GT가 맞다»다.
    chips = ""
    if not many and 1 < len(labels) <= CHIP_LIMIT:
        ai_value = proposal if cell["status"] in ("FIX_PROPOSED", "FILL_PROPOSED", "CONTESTED") else low_value
        ai_tag = "AI 제안" if cell["status"] in ("FIX_PROPOSED", "FILL_PROPOSED") else "AI 의견"
        parts = []
        for label in labels:
            name_html = e(_label(field, label))
            if label == current and cell["status"] == "GT_HOLDS" and not cell.get("alternative"):
                # AI도 같은 값 — 처음부터 골라 둔 모양으로 보인다. 누르지 않으면 기록하지 않고 GT는 그대로다(원장은 사람이 누른 것만).
                parts.append(f'<button class="chip chosen" data-decision="CONFIRM" data-label="{e(label)}" aria-pressed="true">'
                             f'{name_html}<span class="vmark ai">AI 제안</span><span class="vmark now">지금 GT</span></button>')
                continue
            if label == current:
                ai_here = f'<span class="vmark ai">{ai_tag}</span>' if label == ai_value else ""
                parts.append(f'<button class="chip is-now" data-decision="CONFIRM" data-label="{e(label)}">{name_html}{ai_here}'
                             f'<span class="vmark now">지금 GT</span></button>')
                continue
            strong = label == ai_value and cell["status"] in ("FIX_PROPOSED", "FILL_PROPOSED") and not contested
            tag = f'<span class="vmark ai">{ai_tag}</span>' if label == ai_value else ""
            parts.append(f'<button class="chip{" primary" if strong else ""}{" is-ai" if label == ai_value else ""}" '
                         f'data-decision="CORRECT" data-value="{e(label)}" data-label="{e(label)}">{name_html}{tag}</button>')
        chips = '<div class="act chips" role="group" aria-label="값 고르기">' + "".join(parts) + "</div>"
        rest = []
        if current is None:
            rest.append('<button data-decision="LEAVE_EMPTY">빈칸이 맞다</button>')
        elif clear_ok:
            rest.append('<button data-decision="CLEAR">비워야 한다</button>')
        buttons = rest
    if many:
        # 값 여럿은 체크 상자로 — 여럿 고르기 목록(select multiple)은 그냥 누르면 다른 선택이 조용히 지워진다.
        chosen = set(str(proposal or current or "").split(MANY_SEPARATOR))
        picker = '<span class="picks">' + "".join(
            f'<label><input type="checkbox" class="pick" value="{e(label)}"{" checked" if label in chosen else ""}> '
            f'{e(_label(field, label))}</label>' for label in labels) + '</span>'
    elif chips:
        picker = ""
    else:
        picker = f'<select aria-label="다른 값으로 고치기"><option value="">다른 값…</option>{options}</select>'

    decided = cell.get("decision")
    # 근거 사진 이름은 그 사진을 여는 고리로 — 휴대폰에서 칸과 사진 사이를 몇 화면씩 오르내리지 않게.
    def pics(ids: list[Any]) -> str:
        return " ".join(f'<a href="#" class="pic" data-view="{e(str(i))}">{e(str(i))}</a>' for i in ids or [])
    ask = cell.get("ask")
    # 사람이 답한 물음을 판정과 함께 남긴다 — 그 문답이 나중에 정책으로 옮겨 갈 때 어느 판정·골든셋에서 나왔는지 되짚는다.
    proposal_json = json.dumps({"status": cell["status"], "proposal": proposal,
                                "reader": reading.get("value"), "defense": rebuttal.get("verdict"),
                                **({"ask": {"question": ask["question"], "here": ask["here"]}} if ask else {})}, ensure_ascii=False)
    ask_html = ""
    if ask:
        answers = " · ".join(f'{e(option["answer"])} → <b>{e(_label(field, option["value"]))}</b>' for option in ask["options"])
        ask_html = (f'<p class="ask"><b>AI가 묻는 것</b> — {e(_ai(ask["question"]))}'
                    + (f'<br><span class="ask-here">{pics(ask["imageIds"])} {e(_ai(ask["here"]))}</span>' if ask["here"] else "")
                    + (f'<br><span class="ask-options">{answers}</span>' if answers else "") + "</p>")

    evidence = pics(reading.get("evidenceImageIds"))
    defense_ids = pics(rebuttal.get("evidenceImageIds"))
    verdict_name = {"READER_RIGHT": "제안에 동의 — 지금 GT를 지킬 근거를 못 찾음", "GT_STANDS": "지금 GT가 맞다고 봄",
                    "CANT_TELL": "못 정함"}
    # 반론 AI를 부르지 않은 까닭을 칸마다 말한다 — 이유가 없으면 사람은 AI가 실패한 줄 알고 «다시 봐줘»를 되풀이한다.
    not_called = {"GT_HOLDS": "(두 값이 같아 묻지 않았음)", "NEEDS_HUMAN_LOOK": "사진을 본 AI가 확신하지 못해 GT가 맞는지 다시 보는 AI는 부르지 않았습니다 — 사진을 직접 보고 값 버튼을 눌러 주세요",
                  "NOT_READ": "(사진을 본 AI의 답이 없어 묻지 않았음)", "NO_EVIDENCE": "(증거가 없어 묻지 않았음)"}
    defense_text = verdict_name.get(rebuttal.get("verdict"), not_called.get(cell["status"], "(답이 돌아오지 않음)"))
    actionable = "1" if cell["status"] in ACTIONABLE else "0"
    # 제안 칸은 «지금 GT → AI 제안»이 곧 설명이다 — 같은 말을 한 줄 더 쓰지 않는다. 특별한 안내(대체 정답·반론 없음 같은)만 남긴다.
    hint = cell.get("hint") or status["hint"]
    hint_html = ("" if cell["status"] in ("FIX_PROPOSED", "FILL_PROPOSED", "GT_HOLDS", "NEEDS_HUMAN_LOOK") and hint == status["hint"]
                 else f'<p class="why">{e(hint)}</p>')
    # 근거 구역 — 결정할 것(위)과 읽을 것(아래)을 무게로 가른다. 값은 진하게, 근거 문장은 회색.
    if reading:
        low = " (확신 낮음)" if reading.get("confidence") == "LOW" else ""
        # 값을 내지 못한 판독은 «(빈칸)»이 아니라 말로 — «(빈칸)»은 GT가 빈칸이라는 뜻과 헷갈린다
        reader_html = ((f'<b>{e(_label(field, reading.get("value")))}</b>{low}' if reading.get("value") not in (None, "", [])
                        else "<b>값을 고르지 못했습니다</b>")
                       + (f' — [{evidence}] {e(_ai(reading.get("observation")))}' if reading.get("observation") else ""))
        reader_html += _observations_html(reading, field) + _citations_html(reading, field, item)
    else:
        reader_html = "(답이 돌아오지 않음)"
    defense_html = (f'<b>{e(defense_text)}</b>' + (f' — [{defense_ids}] {e(_ai(rebuttal.get("why")))}' if rebuttal else "")
                    + (_citations_html(rebuttal, field, item) if rebuttal else ""))
    contradicted = "GT_SELF_CONTRADICTION" in (cell.get("signals") or [])
    # 고치자는 제안은 근거가 접혀 있어도 한 줄은 보이게 — 무엇을 보고 그 값을 냈는지 모른 채 누르지 않게.
    first = re.split(r"(?<=[.?!다])\s+", _ai(reading.get("observation") or "").strip())[0] if reading.get("observation") else ""
    gist = (f'<p class="gist"><b>AI가 사진에서 본 것</b> — {e(first)}</p>' if first and cell["status"] in ("FIX_PROPOSED", "CONTESTED") else "")
    if caution and not reading.get("definitionGap") and cell["status"] in ("FIX_PROPOSED", "FILL_PROPOSED"):
        gist += '<p class="why">AI가 이 상품은 정책이 다루지 않는 경우라고 봐서 제안 값을 미리 골라 두지 않았습니다 — 사진을 보고 골라 주세요.</p>'
    # 근거는 접어 둔다 — 사진을 보고 사람이 직접 가를 칸(갈림·확신 낮음·반론 없음·사람 확정 GT를 뒤집는 제안)만 펼쳐 둔다.
    open_grounds = (cell["status"] in ("CONTESTED", "NEEDS_HUMAN_LOOK", "NO_EVIDENCE") or bool(cell.get("noDefense"))
                    or bool(reading.get("definitionGap")) or (cell.get("authority") == "TRUSTED" and cell["status"] == "FIX_PROPOSED"))
    # status·화면 load()와 같은 규칙. 보류는 칠하지 않는다(화면이 load()에서 따로 칠한다).
    settled = decided and decided.get("decision") != "HOLD" and answered_on_page(decided, current, batch, reasked(cell))
    done = " done" if settled else ""
    return f"""
<div class="cell {e(cell['status'])}{" asking" if ask else ""}{done}{" alt" if cell.get("alternative") else ""}{" nodefense" if cell.get("noDefense") else ""}" data-key="{e(item['key'])}" data-field="{e(cell['field'])}" data-current="{e(current or '')}" data-actionable="{actionable}" data-contradicted="{"1" if contradicted else ""}" data-gap="{"1" if reading.get("definitionGap") else ""}" data-proposal="{e(proposal_json)}">
  <div class="c-head"><h4>{e(field.get('name') or cell['field'])}</h4><span class="badge s-{e(cell['status'])}">{e(status["name"])}</span></div>
  {ask_html}{hint_html}{contradiction}
  {f'<p class="why gap-note"><b>AI가 짚은 점</b> — 정책이 이 경우를 다루지 않는다고 봤습니다. 고르기 <b>전에</b> 아래 «정책이 다루지 않는 경우»에 표시해 주세요.</p>' if reading.get("definitionGap") else ''}
  {f'<p class="why">{e(decided.get("reviewer") or "")}님이 이 값을 «유지»로 기록했습니다.</p>' if decided and decided.get("decision") == "CONFIRM" else ''}
  {''}
  {_diff_html(field, cell, reading)}
  {chips}
  <div class="act">
    {''.join(buttons)}
    {picker}
    {'' if many or chips else '<button data-decision="CORRECT" data-pick="1" disabled>다른 값을 고르면 여기서 고칩니다</button>'}
    {'<button data-decision="CORRECT" data-pick="1">고른 값으로 고치기</button>' if many else ''}
    <button data-decision="HOLD">보류</button>
    <span class="grow"></span>
    <button type="button" class="toggle t-why" aria-expanded="false">근거 보기</button>
    <button type="button" class="toggle t-memo" aria-expanded="false">메모 추가</button>
  </div>
  {gist}
  <div class="more">
  <details class="memo-add"{" open" if reading.get("definitionGap") else ""}><summary>이유 · 정책 밖 표시</summary>
  <div class="act notes">
    <input class="reason" placeholder="이유 (선택) — 누르기 전에 적으면 다음 사람이 읽습니다">
    <label class="{"gap-strong" if reading.get("definitionGap") else ""}"><input type="checkbox" class="gap"> 정책이 다루지 않는 경우 — 정책을 고칠 목록에 오릅니다</label>
  </div></details>
  <details class="grounds"{" open" if open_grounds else ""}>
    <summary class="grounds-kicker">판단 근거 · 두 AI</summary>
    {_value_notes(field, cell, reading)}
    <dl>
      <dt>AI가 사진에서 본 것</dt>
      <dd>{reader_html}</dd>
      <dt>지금 GT가 맞는지 다시 본 AI</dt>
      <dd>{defense_html}</dd>
    </dl>
    {f'<details class="def"><summary>이 항목의 정책 보기</summary><div class="deftext">{field["definitionHtml"]}</div></details>' if field.get("definitionHtml") else ''}
    <details class="meta"><summary>기록용 정보</summary>왜 올라왔나 {e(reasons)} · GT 출처 <span>{e(AUTHORITY_SHORT.get(cell.get("authority"), "없음") if cell.get("currentSource") else "없음")}</span>{f" · 지난 판정 {e(cell['previousDecision'])}" if cell.get('previousDecision') else ''}</details>
  </details>
  </div>
  <p class="result"></p>
  <button type="button" class="change">바꾸기</button>
</div>"""


def verdict(reading: dict[str, Any], table: dict[str, Any]) -> dict[str, Any] | None:
    """관찰 칸의 값이 **왜** 그 값인지 — 값 규칙 표에 관찰 답을 대 보아(`gt_derive.derive`, 판독과 같은 계산) 걸린 줄과,
    그 줄을 참으로 만든 관찰(이름·근거 문장·근거 사진 번호), 그리고 앞 순위 줄이 왜 안 걸렸는지(그 줄들이 묻는 관찰의 답)를 낸다.
    사람은 «어느 사진으로 어느 정책을 골랐나»를 이 한 덩이로 읽는다. 관찰이 없거나 표가 없으면 None."""
    from gt_derive import derive, describe, names

    observations = reading.get("observations") or []
    if not observations or not table:
        return None
    label = {item["id"]: item["name"] for item in table.get("items") or []}
    answers = {str(obs.get("id")): bool(obs.get("v")) for obs in observations}
    by_id = {str(obs.get("id")): obs for obs in observations}
    value, rule_id = derive(table, answers)
    rule = next((rule for rule in table["rules"] if rule["id"] == rule_id), None)

    def fact(name: str) -> dict[str, Any]:
        obs = by_id.get(name) or {}
        why = _ai(str(obs.get("why") or ""))
        return {"id": name, "name": label.get(name, name), "yes": bool(obs.get("v")), "sure": obs.get("sure") is not False,
                "why": why, "photos": list(dict.fromkeys(re.findall(r"P\d{2}", why)))}

    deciding = [fact(name) for name in sorted(names(rule["ast"]), key=list(label).index) if name in label] if rule else []
    before = []
    for earlier in table["rules"]:  # 걸린 줄이 없으면(«그 밖») 모든 줄이 «앞 순위»다 — 무엇이 부족했는지 보인다
        if rule and earlier["id"] == rule["id"]:
            break
        before += [name for name in sorted(names(earlier["ast"]), key=list(label).index) if name not in before and name in label]
    decided = {item["id"] for item in deciding}
    return {"value": value, "ruleId": rule_id or "그 밖",
            "ruleText": describe(rule["ast"], label) if rule else "어느 값 규칙에도 걸리지 않음",
            "ruleWhy": (rule or {}).get("근거") or (None if rule else "위 규칙들의 근거가 모두 없을 때의 값"),
            "deciding": deciding,
            # 앞 순위 줄이 묻는 관찰 가운데 이번 값을 정한 관찰이 아닌 것 — «그 근거는 없었다»의 목록(예로 답한 것이 있으면 조합이 안 맞은 것)
            "earlier": [fact(name) for name in before if name not in decided],
            # 뒷순위라 값을 정하지는 않았지만 «예»로 답한 관찰 — 같은 쪽을 가리키는 추가 근거일 수도, 부딪치는 근거일 수도 있다
            "alsoTrue": [fact(name) for name in label if answers.get(name) and name not in decided and name not in before],
            "matchesReading": value == reading.get("value")}


def _observations_html(reading: dict[str, Any], field: dict[str, Any]) -> str:
    """관찰 칸 — AI가 값을 고르지 않고 답한 관찰(예/아니오)을 칩으로. 칩 하나가 사실 하나라 사람이 1초에 맞는지 가른다.
    다시 본 눈과 갈린 항목은 테두리로 표시하고, 애매하다고 한 항목에는 «?»를 붙인다. 마우스를 올리면 근거 구절."""
    e = html.escape
    observations = reading.get("observations") or []
    if not observations or not field.get("observe"):
        return ""
    names = {item["id"]: item["name"] for item in field["observe"]}
    split = set(reading.get("split") or [])
    chips = []
    for obs in observations:
        classes = ["yes" if obs.get("v") else "no"]
        if obs.get("id") in split:
            classes.append("split")
        elif obs.get("sure") is False:
            classes.append("unsure")
        why = str(obs.get("why") or "")
        second = obs.get("second")
        if second:
            why += f" / 다시 본 눈: {'있음' if second.get('v') else '없음'} — {second.get('why') or ''}"
        chips.append(f'<span class="{" ".join(classes)}" title="{e(_ai(why))}">{e(names.get(obs.get("id"), obs.get("id")))}</span>')
    note = (f'<br><span class="sub">다시 본 눈과 갈린 항목: {e(", ".join(names.get(i, i) for i in sorted(split)))} — 사진을 직접 봐 주세요.</span>'
            if split else "")
    return f'<div class="obs" aria-label="AI가 본 것">{"".join(chips)}</div>{note}'


def _citations_html(answer: dict[str, Any], field: dict[str, Any], item: dict[str, Any]) -> str:
    """AI가 근거로 댄 규칙과 판례를 사람이 읽는 문장으로. 규칙은 지금 정책의 문장, 판례는 그 배치에서 이 건에 준 사례(번호 → 물음·답).
    번호만 보이면 사람이 정책 페이지와 사례 파일을 열어 찾아야 한다. 받지 않은 것을 댔으면 그렇다고 적는다."""
    e = html.escape
    rules = [str(rule) for rule in answer.get("rulesApplied") or []]
    cases = [str(case) for case in answer.get("casesApplied") or []]
    parts = []
    if rules:
        known = field.get("ruleTexts") or {}
        parts.append('<br><span class="sub">따른 정책 규칙 — ' + " · ".join(
            f'{e(rule)} {e(known[rule])}' if rule in known else f'{e(rule)} (정책에 없는 규칙입니다 — 근거를 직접 봐 주세요)'
            for rule in rules) + "</span>")
    if cases:
        given = item.get("_cases") or {}
        parts.append('<br><span class="sub">따른 판례 — ' + " · ".join(
            f'«{e(given[case]["question"])}» → {e(given[case]["answerName"])}' if case in given
            else f'{e(case)} (이 상품에 주지 않은 판례입니다 — 근거를 직접 봐 주세요)' for case in cases) + "</span>")
    return "".join(parts)


def _diff_html(field: dict[str, Any], cell: dict[str, Any], reading: dict[str, Any]) -> str:
    """칸 머리의 «지금 GT → 제안». 사람이 가장 먼저 가를 것은 «무엇을 무엇으로 바꾸자는가»다 — 네 값을 같은 무게로 늘어놓으면
    어느 것이 지금 GT이고 어느 것이 제안인지 읽어 내야 한다. 제안이 없는 칸은 오른쪽에 그 까닭을 적는다."""
    e = html.escape
    status = cell["status"]
    current = cell.get("current")
    proposal = cell.get("proposal")
    now = f'<div class="now"><span class="tag">지금 GT</span><span class="v">{e(_label(field, current))}</span>{_code(field, current)}</div>'
    if status in ("FIX_PROPOSED", "FILL_PROPOSED") and proposal is not None:
        kind, tag, raw = "proposed", "AI 제안", proposal
    elif status == "CONTESTED" and proposal is not None:
        kind, tag, raw = "contested", "AI 의견 · 두 AI가 갈림", proposal
    elif status == "NEEDS_HUMAN_LOOK" and reading.get("value"):
        kind, tag, raw = "weak", "AI 의견 · 확신 낮음", reading.get("value")
    elif status == "GT_HOLDS":
        return (f'<div class="diff same">{now}<div class="arrow" aria-hidden="true">=</div>'
                '<div class="to"><span class="tag">증거만 본 AI</span><span class="v">같은 값</span></div></div>')
    else:
        reason = {"NOT_READ": "AI가 아직 못 읽음", "NO_EVIDENCE": "증거 없음 — 제안 없음"}.get(status, "제안 없음")
        return (f'<div class="diff none">{now}<div class="arrow" aria-hidden="true">→</div>'
                f'<div class="to"><span class="tag">제안</span><span class="v">{e(reason)}</span></div></div>')
    kind += " fill" if current is None else ""
    return (f'<div class="diff {kind}">{now}<div class="arrow" aria-hidden="true">→</div>'
            f'<div class="to"><span class="tag">{e(tag)}</span><span class="v">{e(_label(field, raw))}</span>{_code(field, raw)}</div></div>')


def _value_notes(field: dict[str, Any], cell: dict[str, Any], reading: dict[str, Any]) -> str:
    """지금 값과 AI 값이 정책에서 무엇을 뜻하는지 한 줄씩 — 이름만 보고 고르면 정의와 다른 뜻으로 고친다."""
    notes = field.get("valueNotes") or {}
    names = field.get("textNames") or {}
    shown = []
    for value in (cell.get("current"), cell.get("proposal") or reading.get("value")):
        for part in (str(value).split(MANY_SEPARATOR) if value else []):
            if part in notes and part not in shown:
                shown.append(part)
    if not shown:
        return ""
    return '<ul class="value-notes">' + "".join(
        f"<li><b>{html.escape(_label(field, part))}</b> — {_inline(notes[part], names)}</li>" for part in shown) + "</ul>"


def output_path(root: Path, project_relative: str) -> Path:
    """사진 경로는 프로젝트 루트 기준으로 적혀 있다. 화면은 gt-review 폴더에 있다."""
    return PROJECT_ROOT / project_relative


def render_html(profile: dict[str, Any], review: dict[str, Any], root: Path) -> str:
    e = html.escape
    unit = (profile.get("gtTask") or {}).get("unit")
    task = profile.get("gtTask") or {}
    upstream = (task.get("gt") or {}).get("upstream")
    clear_ok = not upstream or bool(upstream.get("acceptsEmpty"))
    # 무엇을 누르면 무엇이 되는가 — 버튼은 판정을 기록할 뿐이다. GT가 실제로 바뀌는 순서는 flow_steps가 말한다.
    name = golden_call(profile)
    where = (upstream or {}).get("note") or "원천"
    record_note = f"GT는 아직 그대로입니다 — {change_moment(profile)}."
    confirm_note = (f"이 확인은 {where}로 가지 않고 판정 기록에만 남습니다 — 다음에 같은 칸을 다시 묻지 않게." if upstream
                    else "GT 값은 그대로이고, 사람이 확인했다는 기록이 남습니다.")
    fields = {field["id"]: field for field in review["fields"]}
    # 사람도 판독자와 **같은 정의**를 본다 — AI 문장이 «정의의 기준»을 인용할 때 화면에서 되짚을 수 있게. 정의는 GT 값이 아니라
    # 눈가림과 무관하다.
    try:
        definitions_path = resolve(profile, {"path": task["definitions"], "root": task.get("definitionsRoot") or "project"})
        texts = definition_texts(definitions_path)
        value_notes = definition_value_notes(definitions_path)
    except (OSError, KeyError, TaskError):
        texts, value_notes = {}, {}
    # 판독이 따랐다고 적은 규칙(rulesApplied)을 문장으로 보이려고 — 규칙 ID만 보이면 사람이 정책 페이지를 열어 찾아야 한다.
    try:
        rule_texts: dict[str, dict[str, str]] = {}
        policy_now = definition_policy(definitions_path, {f["id"]: [str(c) for c in f.get("labels") or []] for f in fields.values()},
                                       {f["id"]: f.get("cardinality") == "many" for f in fields.values()})
        for field_id, rules in policy_now["rules"].items():
            rule_texts[field_id] = {rule["id"]: rule["text"] for rule in rules}
        for field_id, table in (policy_now.get("observed") or {}).items():
            # 값 규칙은 식으로 적혀 있다 — 관찰 이름으로 풀어 보인다(«어깨 아님 & 엉덩이 & 무릎»).
            names = {item["id"]: item["name"] for item in table["items"]}
            for rule in table["rules"]:
                from gt_derive import describe
                readable = describe(rule["ast"], names)
                label = (fields.get(field_id) or {}).get("labelNames", {}).get(rule["value"], rule["value"])
                rule_texts.setdefault(field_id, {})[rule["id"]] = f"{readable} → {label}"
    except (OSError, KeyError, TaskError, NameError):
        rule_texts = {}
    column_names = task.get("columnNames") or {}
    # 준비가 건마다 준 판례 표(번호 → 물음·답). 이 화면의 배치 것만 쓴다 — 번호는 배치마다 다시 매겨진다.
    try:
        given_policy = json.loads((root / "policy-given.json").read_text(encoding="utf-8"))
        if given_policy.get("batchId") != review.get("batchId"):
            given_policy = {}
    except (OSError, json.JSONDecodeError):
        given_policy = {}
    for field_id, field in fields.items():
        field["ruleTexts"] = rule_texts.get(field_id) or {}
        field["textNames"] = _names_for_text(fields, field, column_names)
        field["valueNotes"] = value_notes.get(field_id) or {}
        if texts.get(field_id):
            field["definition"] = texts[field_id]
            field["definitionHtml"] = _md(texts[field_id], field["textNames"])
    for spec in task.get("fields") or []:
        if spec.get("unknownLabel") and spec["id"] in fields:
            fields[spec["id"]]["unknownLabel"] = spec["unknownLabel"]
    signals: dict[str, Any] = {}
    try:
        signals = json.loads((root / "worklist.json").read_text(encoding="utf-8")).get("signals") or {}
    except (OSError, json.JSONDecodeError):
        pass
    counts = review["counts"]["byStatus"]
    tally = " · ".join((f'<b>{e(meta["name"])} {counts[name]}</b>' if name in ACTIONABLE else f'{e(meta["name"])} {counts[name]}')
                       for name, meta in STATUS.items() if counts[name])
    blocks = []
    total = len(review["items"])
    for number, item in enumerate(review["items"], 1):
        role_names = ((profile.get("gtTask") or {}).get("images") or {}).get("roleNames") or {}

        def caption(image: dict[str, Any]) -> str:
            # AI 문장은 P01처럼 부른다. 그 이름과 사진 종류(images.roleNames의 이름)만 보이고, 원래 이름(D04T02 같은 내부 코드)은
            # 마우스를 올리면 보인다 — 운영팀이 내부 코드를 사진 번호로 착각해 이유 칸에 적지 않게.
            rule = (image.get("tileRule") or {}).get("version")
            kind = role_names.get(str(image.get("role")))
            return (f'<b title="원래 이름 {e(image["imageId"])}">{e(image.get("viewId") or "")}</b>'
                    + (f' · {e(kind)}' if kind else "")
                    + (f'<span class="meta-rule" data-rule="{e(rule)}"></span>' if rule else ""))  # 자른 규칙은 기록용 — 마우스에 띄우지 않는다
        # AI가 근거로 든 사진을 앞에, «AI 근거» 표시와 함께. 사진이 많으면 나머지는 접는다 — 12장을 같은 무게로 늘어놓으면
        # 무엇을 봐야 할지 사람이 다시 찾아야 한다. 증거만 본 AI의 인용만 쓴다(반론 AI는 전부를 인용하곤 해 표시가 무의미해진다).
        cited = []
        for cell in item["cells"]:
            for image_id in (cell.get("reading") or {}).get("evidenceImageIds") or []:
                if str(image_id) not in cited:
                    cited.append(str(image_id))
        # 전부(또는 여섯 장 넘게) 인용했으면 짚은 것이 아니다 — 표시하면 모든 사진에 «AI 근거»가 붙어 아무것도 가리키지 않는다.
        # 그때는 두 AI가 설명 **문장 안에서** 이름을 부른 사진(«P07 모델이…»)을 근거로 본다.
        if len(cited) > 6 or len(cited) >= len(item["images"]):
            views = {str(image.get("viewId")) for image in item["images"]}
            # 먼저 건 메모가 부른 사진, 없으면 설명 문장에서 **홀로** 불린 사진(«P08·P11·P12»·«P01~P06»처럼 묶여 불린 것은
            # 훑어본 목록이지 짚은 사진이 아니다). 처음 부른 차례대로 넷까지.
            named = list(dict.fromkeys(view for view in re.findall(r"P\d{2}", str(item.get("note") or "")) if view in views))
            if not named or len(named) >= len(item["images"]):
                spoken = " ".join(str((cell.get(part) or {}).get(key) or "") for cell in item["cells"]
                                  for part, key in (("reading", "observation"), ("rebuttal", "why")))
                alone = re.compile(r"(?<![·~,\-])(?<![·~,\-]\s)P\d{2}(?!\s?[·~,\-]\s?P\d)")
                named = list(dict.fromkeys(view for view in alone.findall(spoken) if view in views))
            cited = named[:4] if 0 < len(named) < len(item["images"]) else []
        ordered = sorted(item["images"], key=lambda image: (str(image.get("viewId")) not in cited, item["images"].index(image)))

        def shot(image: dict[str, Any]) -> str:
            mark = " cited" if str(image.get("viewId")) in cited else ""
            kind = role_names.get(str(image.get("role"))) or ""
            return (f'<figure class="shot{mark}"><img loading="lazy" title="누르면 크게 봅니다 — ← → 넘기기 · Esc 닫기" data-view="{e(str(image.get("viewId") or ""))}" src="{e(os.path.relpath(output_path(root, image["path"]), root))}" '
                    f'alt="{e(image.get("viewId") or "")}{" · " + e(kind) if kind else ""}"><figcaption>{caption(image)}'
                    f'{"<em>AI 근거</em>" if mark else ""}{"<em class=\"rep\">대표 사진</em>" if cited and image is item["images"][0] and not mark else ""}</figcaption></figure>')
        keep = [image for image in ordered if str(image.get("viewId")) in cited] or ordered[:4]
        # 짚은 사진만 보이면 무엇과 견줄지 없다 — 대표 사진(첫 장)은 늘 곁에 둔다.
        if item["images"] and item["images"][0] not in keep:
            keep = [*keep, item["images"][0]]
        keep = keep if len(ordered) > 6 else ordered
        rest = [image for image in ordered if image not in keep]
        shots = "".join(shot(image) for image in keep)
        more = (f'<details class="more-shots"><summary>나머지 사진 {len(rest)}장 보기</summary>'
                f'<div class="shots">{"".join(shot(image) for image in rest)}</div></details>' if rest else "")
        if not item["images"] and not item.get("text"):
            shots = '<p class="why">사진도 글도 없는 건입니다.</p>'
        hint_zoom = ""  # 사진 위 커서(돋보기)와 마우스를 올리면 나오는 안내로 충분하다 — 문장으로 한 줄 더 두지 않는다
        # 열 이름 대신 사람 이름(gtTask.columnNames)으로 — 운영팀은 notice 같은 영문 열 이름을 모른다.
        column_names = (profile.get("gtTask") or {}).get("columnNames") or {}
        text = "".join(f'<div class="textbox"><b>{e(str(column_names.get(name, name)))}</b>\n{e(str(value))}</div>'
                       for name, value in (item.get("text") or {}).items())
        if item.get("context"):
            text += ('<p class="why">'
                     + " · ".join(f'<span title="{e(str(column_names.get(name, name)))}">{e(str(value))}</span>' for name, value in item["context"].items())
                     + "</p>")
        # 빠진 사진 — 운영팀에게는 장 수만, 사진 이름(D02T04 같은 내부 코드)은 «기록용 정보» 안에
        gaps = ""
        lost = [m["imageId"] for m in item.get("missing") or []]
        dropped = list(item.get("omitted") or [])
        if lost or dropped:
            said = " · ".join(part for part in (f"못 받은 사진 {len(lost)}장" if lost else "", f"너무 많아 고르게 뽑고 뺀 사진 {len(dropped)}장" if dropped else "") if part)
            names = " · ".join(part for part in (f"못 받음: {' · '.join(lost)}" if lost else "", f"뺌: {' · '.join(dropped)}" if dropped else "") if part)
            gaps += f'<details class="meta"><summary>{e(said)}</summary>{e(names)}</details>'
        caution = any((cell.get("reading") or {}).get("definitionGap") for cell in item["cells"])
        title = " · ".join(part for part in (item.get("title"), item.get("group")) if part)
        subtitle = f' <small style="color:var(--muted);font-weight:400">{e(title)}</small>' if title else ""
        # AI 메모(건 전체에 대한 자유 서술)는 사진 곁에 두지 않는다 — 물음은 칸의 «AI가 묻는 것»에, 정책 밖 경우는 칸의 «AI가 짚은 점»에,
        # 본 것은 칸의 «AI가 사진에서 본 것»에 이미 있다. 같은 말을 세 자리에 두면 사람은 서로 다른 이야기인 줄 안다.
        memo = ""
        # AI가 사람에게 묻는 말은 버튼 곁에 — 사진 아래 메모에 묻히면 판정 순간에 없다.
        # 옛 판독(표준 물음이 없던 배치)만 메모에서 물음 문장을 골라낸다. 칸마다 `askHuman`을 받은 판독은 그것만 쓴다.
        # 옛 배치(표준 물음이 없던 판독)는 메모에서 물음을 골라 그 칸에 표준 물음처럼 붙인다 — 그래야 사람이 누른 판정에
        # 그 물음이 함께 남고(`basedOn.ask`), 정책 규칙의 재료가 된다. 칸을 못 고르면 맨 위에 둔다(판정에는 안 남는다).
        target, legacy = legacy_ask(item, fields)
        if target is not None:
            target["ask"] = {"question": legacy, "here": "", "imageIds": [], "options": [], "legacy": True}
        ask = (f'<p class="ask"><b>AI가 묻는 것</b> — {e(legacy)}</p>' if legacy and target is None else "")
        item["_cases"] = ((given_policy.get("items") or {}).get(item["id"]) or {}).get("cases") or {}
        cell_parts = [_cell_html(item, cell, fields.get(cell["field"]) or {}, signals, clear_ok, review.get("batchId"), caution)
                      for cell in item["cells"]]
        cells = "".join(cell_parts)
        one = len(item["images"]) == 1
        # 접힌 상품은 머리만 남는다 — 키만으로는 어떤 상품인지 모르니 대표 사진(첫 장)을 작게 곁에 둔다. 누르면 펼친다.
        cover = item["images"][0] if item["images"] else None
        thumb = (f'<button class="fold-thumb" type="button" aria-label="{e(item["key"])} 펼치기"><img loading="lazy" alt="" '
                 f'src="{e(os.path.relpath(output_path(root, cover["path"]), root))}"></button>' if cover else "")
        blocks.append(
            f'<section class="item" id="{e(item["id"])}" data-page="{(number - 1) // PAGE_SIZE + 1}">{thumb}<p class="item-top"><span class="kicker">{number} / {total}</span>'
            f'<span class="item-state" aria-live="polite"></span></p>'
            f'<h3>{e(item["key"])}{subtitle}</h3>'
            f'<button class="reopen" type="button" hidden>이 상품 다시 펼치기</button>'
            f'<div class="item-body{" one" if one else ""}"><aside class="rail"><p class="side-kicker">증거 — AI가 본 그대로</p>'
            f'<div class="shots{" one" if one else ""}">{shots}</div>{more}{text}{gaps}{memo}{hint_zoom}</aside>'
            f'<div class="decide">{ask}<div class="gt-cell-grid">{cells}</div></div></div></section>'
        )
    if not blocks:
        blocks.append('<p style="margin-top:30px">이번에 볼 칸이 없습니다. 남은 후보가 없거나 모두 답했습니다.</p>')
    warn = ""
    if (review.get("warnings") or {}).get("notKorean"):
        warn = (f'<p class="notice">AI 설명 일부가 한국어가 아닙니다({e(", ".join(review["warnings"]["notKorean"]))}). '
                '판정에는 문제가 없지만, 읽기 어려우면 Claude에게 «이 건 한국어로 다시 봐줘»라고 말해 주세요.</p>')
    if (review.get("warnings") or {}).get("rulesUnknown"):
        odd = review["warnings"]["rulesUnknown"]
        warn += (f'<p class="notice">AI가 이 상품에 주지 않은 규칙·판례를 근거로 적은 칸이 있습니다'
                 f'({e(", ".join(sorted({str(row.get("item")) for row in odd})))}). 그 칸은 근거를 직접 보고 골라 주세요.</p>')
    body = f"""<div class="shell">{sidebar_html("review", review["profileId"])}<div class="wrap">
<header class="masthead">
  <div class="masthead-top"><p class="kicker crumbs"><a href="/">홈</a> / {e(str(profile.get('subjectName') or ''))}{f" · {e(str(unit))} 단위" if unit else ""}</p><p id="made" data-at="{e(str(review['basedOn']['worklist'] or ''))}"></p></div>
  <div class="title-row"><h1>{e(name)}</h1><div class="title-side"><nav class="pager" aria-label="페이지"><button type="button" id="page-prev" aria-label="이전 페이지"{' hidden' if total <= PAGE_SIZE else ''}>‹</button><span id="page-now"{' hidden' if total <= PAGE_SIZE else ''}></span><button type="button" class="page-next">다음 페이지 →</button></nav><label class="who">판정하는 사람 <input id="reviewer" placeholder="이름"></label></div></div>
  <div class="actions">
    <div id="export-out" hidden aria-live="polite"></div>
  </div>
  <p id="offline" class="notice" hidden>이 파일을 직접 열었습니다. 판정을 기록하려면 Claude에게 «GT 화면 열어줘»라고 말해 주세요.</p>
  {warn}
  <p id="name-hint" class="notice" hidden></p>
  <div class="statusline">
    <div class="progress-block">
      <div id="progress-short"></div>
      <div class="pbar" aria-hidden="true"><i id="pbar"></i></div>
      <div class="tally"><span class="tally-kicker">처음 올라온 칸 {sum(counts.values())} —</span> {tally}</div>
    </div>
    <div class="status-side">
      <div class="views" role="group" aria-label="보기">
        <button type="button" data-view="all" aria-pressed="true">전체</button>
        <button type="button" data-view="ask" aria-pressed="false">답할 칸 <span class="count" id="count-ask"></span></button>
        <button type="button" data-view="holds" aria-pressed="false" hidden>유지 확인 <span class="count" id="count-holds"></span></button>
      </div>
      <!-- 반영은 진행률 곁에 — 버튼 하나만 있는 줄은 빈 공간만 만든다. 다 답했는지 보면서 바로 누른다. -->
      <button type="button" id="do-export" title="{"고칠 목록을 만듭니다(원본은 시트라 목록만)" if upstream else "답한 값을 GT에 바로 넣습니다"}">반영하기</button>
    </div>
  </div>
  <div class="bar" hidden><label hidden><input type="checkbox" id="show-holds"> 유지 확인만 남은 칸도 칸마다 펼치기{f' <span class="hidden-count">({counts["GT_HOLDS"]}칸 — 상품마다 아래에 모아 두었습니다)</span>' if counts["GT_HOLDS"] else ''}</label></div>
  <p id="progress"></p>
</header>
{''.join(blocks)}
<div id="progress-bottom" aria-live="polite"><span id="progress-bottom-text"></span>
  <span class="bottom-actions"><button type="button" id="next-open">다음 안 한 칸 ↓</button><button type="button" class="page-next">다음 페이지 →</button></span></div>
<footer class="end" hidden><span id="remaining"></span><a id="next-task" hidden></a><span class="made-at" data-at="{e(str(review['basedOn']['worklist'] or ''))}"></span></footer>
<div id="viewer" hidden><button type="button" class="go prev" data-go="-1" aria-label="이전 사진">‹</button><img alt=""><button type="button" class="go next" data-go="1" aria-label="다음 사진">›</button><p></p></div>
</div></div>
<script id="gt-review-data" type="application/json">{json.dumps({"profileId": review["profileId"], "batchId": review.get("batchId"),
    "callName": name, "recordNote": record_note, "confirmNote": confirm_note, "pageSize": PAGE_SIZE,
    "fieldNames": {fid: spec.get("name") or fid for fid, spec in fields.items()},
    "upstreamNote": (upstream or {}).get("note"),
    # 답한 칸 한 줄(«<값> 유지 · <사람>»)과 같은 길이로 — 긴 안내 문장은 한 줄 격자에서 칸 이름을 밀어낸다.
    "appliedNote": ("원본에 들어감" if upstream else "GT에 넣음"),
    "labelNames": {fid: spec.get("labelNames") or {} for fid, spec in fields.items()}}, ensure_ascii=False)}</script>
<script>{SCRIPT}{VIEW_SCRIPT}{PAGE_SCRIPT}{SIDEBAR_SCRIPT}</script>
</body></html>
"""
    page = head(f"데이터 운영 · {profile.get('displayName') or review['profileId']}")
    page = page.replace("</head>", '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap" rel="stylesheet">\n</head>', 1)
    return page.replace("</style>", EXTRA_STYLE + THEME_STYLE + SIDEBAR_STYLE + "</style>", 1) + body


# 카드·표·알약 — 정책·골든셋 두 장과 증분 검수의 목록(incr_render)이 함께 쓴다. 한 벌이어야 두 화면이 같은 표로 보인다.
LIST_STYLE = """
.card{background:#fff;border-radius:16px;padding:22px 24px;margin-top:20px;box-shadow:var(--shadow)}
.card h2{font-size:17px;font-weight:700;margin:0 0 4px}
.card .sub{font-size:13px;color:var(--faint);margin:0 0 14px}
.table{margin-top:16px;background:#fff;border-radius:16px;box-shadow:var(--shadow);overflow:auto}
.table table{width:100%;border-collapse:collapse;font-size:13.5px}
.table th{position:sticky;top:0;background:var(--paper);text-align:left;font-size:12px;font-weight:600;color:var(--faint);padding:10px 14px;white-space:nowrap}
.table td{padding:10px 14px;box-shadow:inset 0 1px 0 #F1F5F9;white-space:nowrap}
.table tr:hover td{background:#FAFCFF}
.table td.key{font-weight:600}
.table .empty{color:#94A3B8;font-style:italic}
.table .pill{display:inline-block;margin-left:6px;font-size:11.5px;font-weight:600;padding:1px 8px;border-radius:999px}
.pill.out{background:var(--bad-soft);color:var(--bad)}
.pill.to{background:var(--accent-soft);color:var(--accent-ink)}
.pill.ok{background:var(--ok-soft);color:var(--ok)}
.table .src{color:var(--faint);font-size:12.5px}
"""

PAGE_STYLE = LIST_STYLE + """
.page-head{padding:28px 0 8px;display:flex;flex-direction:column;gap:6px}
.page-head h1{font-size:clamp(1.5rem,2.6vw,1.75rem);font-weight:700;letter-spacing:-.02em;margin:0}
.page-head .lead{margin:0}
.goal-row{display:grid;grid-template-columns:110px 1fr;gap:12px;padding:6px 0;font-size:14px}
.goal-row>span{color:var(--muted)}
.goal-row .deftext p{margin:0}
.policy-rules li{line-height:1.6}
.policy-rules .tag{display:inline-block;padding:1px 7px;border-radius:6px;font-size:12px;font-weight:600;background:#F1F5F9;color:#475569}
.policy-rules .tag.from-review{background:#FEF3C7;color:#92400E}
.policy-rules li.struck{color:var(--muted)}
.policy-rules li.struck .rule-text{text-decoration:line-through}
.values{list-style:none;padding:0;margin:0 0 14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.values li{padding:12px 14px;border-radius:12px;background:var(--paper);font-size:13.5px;line-height:1.55;color:var(--muted)}
.values strong.vn{display:block;color:var(--ink);font-size:14.5px;margin-bottom:2px}
.values b{color:var(--ink);font-weight:600}
.values .n{font-size:11.5px;font-weight:600;color:var(--faint);margin-left:6px}
.edit-lead{margin:0}
.edit-name{display:inline-flex;align-items:center;gap:8px;font-size:13.5px;color:var(--muted);margin-top:4px}
.edit-name input,.edit-row input,.edit-row select,.combo select{font:inherit;font-size:13.5px;padding:6px 10px;border:1px solid #E2E8F0;border-radius:8px;background:#fff;color:var(--ink)}
details.edit{margin-top:16px;border-top:1px solid #F1F5F9;padding-top:10px}
details.edit>summary{cursor:pointer;font-size:13.5px;font-weight:600;color:var(--accent)}
.edit-body{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px;margin-top:12px}
.edit-form{background:var(--paper);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.edit-title{margin:0;font-size:13.5px;font-weight:700;color:var(--ink)}
.edit-row{display:grid;grid-template-columns:78px 1fr;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.edit-row input,.edit-row select{min-width:0;width:100%}
.combo{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
.combo label{display:flex;flex-direction:column;gap:2px;font-size:12.5px;color:var(--muted)}
.edit-actions{display:flex;gap:8px}
.edit-form .btn{font:inherit;font-size:13px;font-weight:600;padding:6px 14px;border-radius:8px;border:0;background:var(--accent);color:#fff;cursor:pointer}
.edit-form .btn.ghost{background:#fff;color:var(--muted);border:1px solid #E2E8F0}
.edit-form .btn:disabled{opacity:.5;cursor:default}
.edit-result{font-size:13px;line-height:1.55;border-top:1px dashed #E2E8F0;padding-top:8px}
.edit-result p{margin:4px 0}
.edit-result .warn{color:var(--bad)}
.edit-result .ok{color:var(--ok)}
.edit-result .was{color:var(--muted);text-decoration:line-through}
.edit-result .will{color:var(--ink);font-weight:600}
.edit-result .changes,.edit-result .table-lines{margin:4px 0;padding-left:18px}
details.src > summary{cursor:pointer;font-size:13.5px;font-weight:600;color:var(--accent);min-height:36px;display:flex;align-items:center}
details.src .deftext{font-size:13.5px;line-height:1.65;color:var(--muted);padding:12px 16px;border-radius:12px;background:var(--paper)}
.rules{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:8px;font-size:14px}
.rules li{padding:10px 14px;border-radius:10px;background:var(--paper)}
.filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:18px}
.filters input[type=search],.filters select{font:inherit;font-size:14px;min-height:42px;box-sizing:border-box;padding:0 14px;border:0;border-radius:10px;background:#fff;box-shadow:var(--shadow);color:var(--ink)}
.filters input[type=search]{width:260px}
.filters input[type=checkbox]{width:18px;height:18px;accent-color:var(--accent)}
.filters label{display:flex;align-items:center;gap:8px;font-size:13.5px;color:var(--muted)}
.filters .shown{margin-left:auto;font-size:13px;color:var(--faint)}
/* 골든셋 카드 — 검수 화면의 접힌 상품처럼: 왼쪽 대표 사진, 오른쪽 키와 칸별 값 */
.gcards{display:flex;flex-direction:column;gap:12px;margin-top:16px}
.gcard{display:grid;grid-template-columns:96px minmax(0,1fr);gap:18px;padding:16px 20px;background:#fff;border-radius:16px;box-shadow:var(--shadow)}
.gcard[hidden]{display:none}
.gthumb{display:block;width:96px;height:120px;border-radius:10px;overflow:hidden;background:#F1F5F9;border:0}
.gthumb img{width:100%;height:100%;object-fit:cover;display:block}
a.gthumb:hover{box-shadow:0 0 0 2px var(--accent)}
.gthumb.none{display:flex;align-items:center;justify-content:center;font-size:11.5px;color:var(--faint)}
.ghead{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px}
.ghead .gkey{font-size:1.05rem;font-weight:700;color:var(--ink);border:0}
.ghead a.gkey:hover{color:var(--accent)}
.ghead small{font-size:13px;color:var(--faint)}
.ghead .src{margin-left:auto;font-size:12.5px;color:var(--faint)}
.gvals{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px 20px;margin:10px 0 0;padding:0}
.gvals > div{display:flex;flex-direction:column;gap:2px;min-width:0}
.gvals dt{font-size:12px;color:var(--faint)}
.gvals dd{margin:0;font-size:14px;font-weight:600;color:var(--ink)}
.gvals .empty{color:#94A3B8;font-style:italic;font-weight:400}
.gvals .pill{display:inline-block;margin-left:6px;font-size:11.5px;font-weight:600;padding:1px 8px;border-radius:999px}
"""


def _page(title: str, profile: dict[str, Any], active: str, inner: str, script: str = "") -> str:
    """정책·골든셋 두 장의 틀 — 검수 화면과 같은 사이드바·색."""
    page = head(title).replace("</head>", '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap" rel="stylesheet">\n</head>', 1)
    page = page.replace("</style>", THEME_STYLE + SIDEBAR_STYLE + PAGE_STYLE + "</style>", 1)
    return (page + f'<div class="shell">{sidebar_html(active, profile.get("id"), has_gt=bool(profile.get("gtTask")))}<div class="wrap">{inner}</div></div>'
            f'<script>{SIDEBAR_SCRIPT}{script}</script>\n</body></html>\n')


def _archived_readable(rule: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """보관된 값 규칙(식)을 관찰 이름으로 풀어 보인다 — 칸이 아직 관찰 칸이고 식을 읽을 수 있을 때만. 아니면 그대로."""
    text = str(rule.get("text") or "")
    table = (policy.get("observed") or {}).get(rule.get("field"))
    if not (table and len(text) > 2 and text.startswith("`") and text.endswith("`")):
        return rule
    from gt_derive import DeriveError, describe, parse

    try:
        return {**rule, "text": describe(parse(text[1:-1]), {item["id"]: item["name"] for item in table["items"]})}
    except DeriveError:
        return rule


def _rule_html(field: dict[str, Any], rule: dict[str, Any], archived: bool = False) -> str:
    """정책 규칙 한 건 — 문장과 될 값, 그 아래 출처·범위·근거(골든셋 링크)·물음. 근거 키는 골든셋의 그 줄로 잇는다."""
    e = html.escape
    keys = ", ".join(f'<a href="golden.html#q={urllib.parse.quote(key.strip())}">{e(key.strip())}</a>'
                     for key in (rule.get("근거") or "").split(",") if key.strip())
    source = rule.get("출처") or ""
    kind = "검수 문답" if source.startswith("검수 문답") else "직접 작성" if source.startswith("직접 작성") else "정책"
    meta = " · ".join(part for part in (
        f'<span class="tag {"from-review" if kind == "검수 문답" else ""}">{e(kind)}</span> {e(source.split("·", 1)[1].strip()) if "·" in source else ""}',
        f'범위 {e(rule["범위"])}' if rule.get("범위") else "",
        f'근거 골든셋 {keys}' if keys else "",
        f'대체: {e(rule["대체"])}' if archived and rule.get("대체") else "") if part)
    value = f' → <b>{e(_label(field, rule["value"]))}</b>' if rule.get("value") else ""
    asked = f'<br><span class="sub">물음 — {e(rule["물음"])}</span>' if rule.get("물음") else ""
    label = f'{rule["field"]}/{rule["id"]}' if archived else rule["id"]
    text = rule["text"]
    # 보관된 값 규칙(V)은 문장 대신 식이다 — 백틱째 보이지 않게 코드로 그린다.
    shown = f'<code>{e(text[1:-1])}</code>' if len(text) > 2 and text.startswith("`") and text.endswith("`") else e(text)
    return (f'<li{" class=\"struck\"" if archived else ""}><code>{e(label)}</code> <span class="rule-text">{shown}</span>{value}'
            f'<br><span class="sub">{meta}</span>{asked}</li>')


def _complete_policy_document(path: Path, field_ids: list[str]) -> str:
    """Show exact source and the same policy projection used by the SDK agents."""
    from policy_prompt import render_agent_policy
    e = html.escape
    raw = path.read_text(encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    from policy_document import load_document
    document = load_document(path)
    names = {field["id"]: field["name"] for field in (document or {}).get("fields", [])}
    panels = []
    for field_id in field_ids:
        try:
            rendered, _ = render_agent_policy(path, field_id)
        except ValueError as error:
            panels.append(f'<p role="status">{e(field_id)}: SDK 정책을 구성할 수 없습니다. {e(str(error))}</p>')
        else:
            title = "SDK 에이전트에 전달되는 정책"
            if len(field_ids) > 1:
                title += " · " + names.get(field_id, field_id)
            panels.append('<details class="policy-disclosure">'
                          f'<summary>{e(title)}</summary>'
                          f'<pre class="complete-policy-text" data-agent-policy="{e(field_id)}">{e(rendered)}</pre></details>')
    return ('<section class="card policy-transparency" id="policy-document">'
            '<details class="policy-disclosure"><summary>정책 문서 전체</summary>'
            '<p>아래는 definitions.md 전체입니다. 문서에 있는 모든 항목을 생략 없이 표시합니다.</p>'
            f'<p class="sub">원본: {e(str(path))}<br>문서 SHA-256: <code>{digest}</code></p>'
            f'<pre class="complete-policy-text" id="policy-document-source">{e(raw)}</pre></details>'
            '<details class="policy-disclosure"><summary>에이전트 전달 정책</summary>'
            '<p>SDK 판독·검수·판례 에이전트가 사용하는 정책입니다. 같은 변환 함수로 표시합니다. '
            '역할 지침과 상품 입력은 별도로 전달되며, 아래는 정책 부분입니다.</p>'
            + ''.join(panels) + '</details></section>'
            '<style>.complete-policy-text{white-space:pre-wrap;overflow-wrap:anywhere;'
            'font:13px/1.8 ui-monospace,monospace;background:#f5f6f8;padding:20px;'
            'border:1px solid #e1e4e8;border-radius:6px;max-height:none}'
            '.policy-transparency{min-width:0}.policy-transparency code{overflow-wrap:anywhere}'
            '.policy-disclosure>summary{cursor:pointer;font-weight:600;padding:12px 0}'
            '.policy-disclosure>summary:focus-visible{outline:2px solid #557da9;outline-offset:3px}'
            '.policy-transparency>.policy-disclosure+.policy-disclosure{border-top:1px solid #e1e4e8}'
            '.policy-disclosure .policy-disclosure{margin-left:16px}</style>')


def _standard_policy_html(profile: dict[str, Any], path: Path, document: dict[str, Any]) -> str:
    e = html.escape
    def rule_card(rule: dict[str, Any], prefix: str) -> str:
        rank = "공통" if rule["우선순위"] == "공통" else f"{rule['우선순위']}순위"
        return (f'<article class="policy-rule" id="rule-{e(prefix)}-{e(rule["id"])}">'
                f'<header class="policy-rule-head"><span class="policy-rank">{e(rank)}</span>'
                f'<h4>{e(rule["title"])}</h4><span class="policy-rule-id">{e(rule["id"])}</span></header>'
                f'<div class="deftext">{_md(rule["내용"], {})}</div>'
                f'<footer class="policy-case">판례 <span>{e(rule["판례"])}</span></footer></article>')

    fields = document.get("fields") or [{"id": document["field"], "name": document["name"],
                                          "values": document["values"], "rules": document["rules"]}]
    multi_field = len(fields) > 1
    vocabulary, groups = "", ""
    if document.get("commonRules"):
        groups += ('<div class="policy-rule-group"><h3>속성 간 공통 규칙</h3>'
                   + "".join(rule_card(rule, "common") for rule in document["commonRules"]) + '</div>')
    for field in fields:
        heading = f'<h3 class="policy-field-heading">{e(field["name"])}</h3>' if multi_field else ""
        values = "".join(f'<div class="policy-value-row"><dt>{e(v["name"])}</dt><dd>{e(v["description"])}</dd></div>'
                         for v in field["values"])
        field_purpose = f'<p class="policy-field-purpose">{e(field["purpose"])}</p>' if field.get("purpose") else ""
        vocabulary += heading + field_purpose + f'<dl class="policy-values-list">{values}</dl>'
        groups += heading
        for common, title, description in [(True, "공통 규칙", "모든 판정에 적용"),
                                           (False, "판정 규칙", "우선순위 순서로 적용")]:
            rules = [rule for rule in field["rules"] if (rule["우선순위"] == "공통") == common]
            if rules:
                groups += (f'<div class="policy-rule-group"><h3>{title} <span>{description}</span></h3>'
                           + "".join(rule_card(rule, field["id"]) for rule in rules) + '</div>')
    style = """<style>
.policy-jump{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 28px}
.policy-jump a{padding:7px 14px;border:1px solid #d7dfe8;border-radius:6px;color:#29435e;text-decoration:none;background:#fff;font-size:13px}
.policy-jump a:hover,.policy-jump a:focus-visible{background:#eaf1fa;border-color:#668bb9}
.policy-zone{margin:28px 0 38px;scroll-margin-top:24px}
.policy-zone-heading{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.policy-zone-heading .policy-step{font:600 12px ui-monospace,monospace;color:#5279a6}
.policy-zone-heading h2{margin:0;font-size:22px;color:#182c43}
.policy-purpose-body{border-left:4px solid #557da9;padding:14px 22px;background:#edf3f9;font-size:17px;line-height:1.8}
.policy-purpose-body p{margin:0}
.policy-values-list{margin:0;background:#fff;border-top:2px solid #354b63;border-bottom:1px solid #d8e0e8}
.policy-value-row{display:grid;grid-template-columns:130px 1fr;gap:22px;padding:15px 18px;border-bottom:1px solid #edf0f3;align-items:baseline}
.policy-value-row:last-child{border-bottom:0}.policy-value-row dt{font-weight:700;color:#213b58}.policy-value-row dd{margin:0;color:#485769;line-height:1.6}
.policy-field-heading{margin:28px 0 12px;padding-bottom:8px;border-bottom:2px solid #354b63;font-size:18px}.policy-rule-group{margin-top:24px}.policy-rule-group>h3{font-size:15px;margin:0 0 12px;display:flex;gap:12px;align-items:baseline}
.policy-rule-group>h3 span{font-size:12px;font-weight:400;color:#637387}
.policy-rule{background:#fff;border:1px solid #dce3eb;border-radius:8px;padding:20px 22px;margin:12px 0;scroll-margin-top:24px}
.policy-rule-head{display:flex;gap:12px;align-items:baseline;margin-bottom:14px}.policy-rule-head h4{font-size:17px;line-height:1.5;margin:0;flex:1}
.policy-rank{background:#edf2f8;color:#31567d;border-radius:4px;padding:4px 8px;font-size:12px;white-space:nowrap;font-weight:600}
.policy-rule-id{font:11px ui-monospace,monospace;color:#7c8997}.policy-case{border-top:1px solid #edf0f3;padding-top:12px;margin-top:16px;color:#7c8997;font-size:12px}.policy-case span{color:#485769;margin-left:10px}
@media(max-width:600px){.policy-value-row{grid-template-columns:90px 1fr;gap:12px;padding:12px}.policy-rule{padding:16px}.policy-rule-head{gap:8px}.policy-rule-head h4{font-size:16px}.policy-zone-heading h2{font-size:20px}.policy-purpose-body{padding:12px 16px;font-size:15px}}
</style>"""
    body = (f'<header class="page-head"><h1>{e(document["title"])}</h1></header>'
            '<nav class="policy-jump" aria-label="정책 구역"><a href="#policy-purpose">목적</a>'
            '<a href="#policy-values">허용값</a><a href="#policy-rules">규칙</a>'
            '<a href="#policy-document">문서 전체 · 전달 정책</a></nav>'
            '<section class="policy-zone" id="policy-purpose" aria-labelledby="purpose-title">'
            '<div class="policy-zone-heading"><span class="policy-step">01</span><h2 id="purpose-title">목적</h2></div>'
            f'<div class="policy-purpose-body">{_md(document["purpose"], {})}</div></section>'
            '<section class="policy-zone" id="policy-values" aria-labelledby="values-title">'
            '<div class="policy-zone-heading"><span class="policy-step">02</span><h2 id="values-title">허용값</h2></div>'
            f'{vocabulary}</section>'
            '<section class="policy-zone" id="policy-rules" aria-labelledby="rules-title">'
            '<div class="policy-zone-heading"><span class="policy-step">03</span><h2 id="rules-title">규칙</h2></div>'
            + groups + '</section>' + _complete_policy_document(path, [field["id"] for field in fields]) + style)
    if profile.get("gtTask"):
        body += ('<section class="policy-zone" id="registered-precedents" data-task="' + e(profile["id"]) + '">'
                 '<h2>등록한 판례</h2><p class="precedent-state">판례를 불러오는 중입니다.</p><div class="precedent-list"></div></section>')
        body += r"""<script>
(() => {
  const section = document.getElementById('registered-precedents');
  const state = section.querySelector('.precedent-state');
  fetch('/gt-qa?task=' + encodeURIComponent(section.dataset.task), {cache:'no-store'})
    .then(r => { if (!r.ok) throw new Error('판례 조회 실패'); return r.json(); })
    .then(data => {
      if (!data.ok) throw new Error(data.error || '판례 조회 실패');
      const rows = data.answered || [];
      state.textContent = rows.length ? rows.length + '개 · 사람이 확인한 판단 근거입니다.' : '등록된 판례가 없습니다. 증분 검수에서 값을 확정한 뒤 판례 등록을 누르세요.';
      for (const row of rows) {
        const card = document.createElement('details'); card.className = 'policy-rule'; card.id = 'precedent-' + row.decisionId;
        const title = document.createElement('summary'); title.textContent = row.fieldName + ' · ' + row.answerName + ' · ' + row.key;
        const reason = document.createElement('p'); reason.textContent = row.here || row.question;
        const source = document.createElement('small'); source.textContent = row.decisionId + ' · ' + row.reviewer + ' · ' + (row.ruleIds || []).join(', ');
        card.append(title, reason, source); section.querySelector('.precedent-list').appendChild(card);
        for (const rule of row.ruleIds || []) {
          const ruleCard = document.getElementById('rule-' + row.field + '-' + rule) || document.getElementById('rule-common-' + rule);
          if (!ruleCard) continue;
          const footer = ruleCard.querySelector('.policy-case span');
          if (footer.textContent.trim() === '없음' || footer.textContent.trim() === '없음.') footer.textContent = '';
          const link = document.createElement('a'); link.href = '#precedent-' + row.decisionId; link.textContent = row.decisionId;
          link.addEventListener('click', () => { card.open = true; }); footer.append(document.createTextNode(' '), link);
        }
      }
    }).catch(error => { state.textContent = error.message; });
})();
</script>"""
    return _page(document["title"], profile, "policy", body)


def render_policy_html(profile: dict[str, Any]) -> str:
    """정책 — 이 과제의 정의 문서를 필드마다 한 장씩, 읽기 전용으로. 값 목록과 뜻은 정의 문서(정본)에서 그대로 읽는다."""
    from policy_document import load_document
    config = profile.get("policyTask") or profile.get("gtTask") or {}
    source = resolve(profile, {"path": config["definitions"], "root": config.get("definitionsRoot") or "project"})
    document = load_document(source)
    if document is not None:
        return _standard_policy_html(profile, source, document)
    e = html.escape
    task = load_task(profile)
    spec = profile.get("policyTask") or profile.get("gtTask") or {}
    path = resolve(profile, {"path": spec["definitions"], "root": spec.get("definitionsRoot") or "project"})
    texts, notes = definition_texts(path), definition_value_notes(path)
    fields = {field["id"]: field for field in task["fields"]}
    policy = definition_policy(path, {f["id"]: [str(c) for c in f["labels"]] for f in task["fields"]},
                               {f["id"]: f.get("cardinality") == "many" for f in task["fields"]})
    column_names = (profile.get("gtTask") or {}).get("columnNames") or {}
    cards = []
    edit_fields: list[dict[str, Any]] = []
    for field in task["fields"]:
        names = _names_for_text(fields, field, column_names)
        values = "".join(
            f'<li><strong class="vn">{e((field.get("labelNames") or {}).get(label, label))}</strong>{_inline(notes.get(field["id"], {}).get(label, ""), names)}</li>'
            for label in field.get("labels") or [])
        # 허용값·관찰·값 규칙은 아래 구조화된 값/카드에 이미 표시된다. 원문 펼침에는 보충 설명만 남겨 같은 정책을 두 번 읽게 하지 않는다.
        supplemental = re.split(r"(?m)^### (?:허용값|판정 규칙|관찰|값 규칙|규칙)\s*$", texts.get(field["id"], ""), maxsplit=1)[0].strip()
        source = (f'<details class="src"><summary>추가 판정 기준 보기</summary><div class="deftext">{_md(supplemental, names)}</div></details>'
                  if supplemental else "")
        rules = "".join(_rule_html(field, rule) for rule in policy["rules"].get(field["id"], []))
        qa = (f'<p class="sub" style="margin-top:14px">규칙</p><ul class="rules policy-rules">{rules}</ul>' if rules else "")
        table = (policy.get("observed") or {}).get(field["id"])
        policy_cards_html = ""
        prompt_rules = decision_rules(path, field["id"])
        if table:
            # 관찰 칸 — AI는 값을 고르지 않고 아래 항목에 예/아니오로 답하고, 값은 값 규칙 표가 위에서부터 정한다.
            obs_names = {item["id"]: item["name"] for item in table["items"]}
            from gt_derive import describe
            configured_cards = (profile.get("policyTask") or spec).get("policyCards") or []
            cards_for_field = (configured_cards.get(field["id"], []) if isinstance(configured_cards, dict)
                               else configured_cards if isinstance(configured_cards, list) else [])
            if cards_for_field:
                item_by_id = {item["id"]: item for item in table["items"]}
                rule_by_id = {rule["id"]: rule for rule in table["rules"]}
                prompt_rule_by_id = {rule["id"]: rule for rule in prompt_rules}
                used_items: set[str] = set()
                used_rules: set[str] = set()
                used_decision_rules: set[str] = set()

                def policy_group_html(group: dict[str, Any]) -> str:
                    item_ids = [key for key in group.get("observations", []) if key in item_by_id]
                    rule_ids = [key for key in group.get("rules", []) if key in rule_by_id]
                    decision_ids = [key for key in group.get("decisionRules", []) if key in prompt_rule_by_id]
                    used_items.update(item_ids)
                    used_rules.update(rule_ids)
                    used_decision_rules.update(decision_ids)
                    group_observations = "".join(
                        f'<li><strong class="vn">{e(item_by_id[key]["name"])}</strong> {e(item_by_id[key]["desc"])}</li>'
                        for key in item_ids)
                    group_rules = "".join(
                        f'<li><code>{e(rule_by_id[key]["id"])}</code> {e(describe(rule_by_id[key]["ast"], obs_names))} → '
                        f'<b>{e(_label(field, rule_by_id[key]["value"]))}</b>'
                        + (f'<br><span class="sub">출처 {e(rule_by_id[key].get("출처") or "")}</span>'
                           if rule_by_id[key].get("출처") else "") + "</li>" for key in rule_ids)
                    contents = ""
                    if group_observations:
                        contents += '<p class="sub" style="margin-top:14px">확인할 관찰</p><ul class="values">' + group_observations + "</ul>"
                    if group_rules:
                        contents += '<details class="rules-audit"><summary>골든셋 검수용 계산 규칙</summary><ol class="rules policy-rules">' + group_rules + "</ol></details>"
                    if decision_ids:
                        decision_html = "".join(
                            f'<li><strong>{e(prompt_rule_by_id[key]["title"])}</strong>'
                            f'<p>{e(prompt_rule_by_id[key]["내용"])}</p>'
                            f'<span class="sub">우선순위 {e(prompt_rule_by_id[key]["우선순위"])}'
                            + (f' · 판례 {e(prompt_rule_by_id[key]["판례"])}' if prompt_rule_by_id[key].get("판례") else "")
                            + (f' · 출처 {e(prompt_rule_by_id[key]["출처"])}' if prompt_rule_by_id[key].get("출처") else "")
                            + "</span></li>" for key in decision_ids)
                        contents = ('<p class="sub" style="margin-top:14px">운영 추론에 전달되는 자연어 판정 규칙</p>'
                                    '<ol class="rules policy-rules">' + decision_html + "</ol>" + contents)
                    description = f'<p class="sub">{e(group["description"])}</p>' if group.get("description") else ""
                    return (f'<section class="card policy-card"><h2>{e(group.get("title") or "정책")}</h2>'
                            + description + contents + "</section>")

                policy_cards_html = ""
                policy_cards_html += "".join(policy_group_html(group) for group in cards_for_field)
                remaining_items = [item for item in table["items"] if item["id"] not in used_items]
                remaining_rules = [rule for rule in table["rules"] if rule["id"] not in used_rules]
                remaining_decision_rules = [rule for rule in prompt_rules if rule["id"] not in used_decision_rules]
                if remaining_items or remaining_rules or remaining_decision_rules:
                    policy_cards_html += policy_group_html({
                        "title": "기타 정책 근거",
                        "observations": [item["id"] for item in remaining_items],
                        "rules": [rule["id"] for rule in remaining_rules],
                        "decisionRules": [rule["id"] for rule in remaining_decision_rules],
                    })
                end_of_last_card = policy_cards_html.rfind("</section>")
                if end_of_last_card >= 0:
                    policy_cards_html = (policy_cards_html[:end_of_last_card]
                                         + f'<p class="sub">그 밖의 관찰 조합 → <b>{e(_label(field, table["else"]))}</b></p>'
                                         + policy_cards_html[end_of_last_card:])
            else:
                observe_html = "".join(f'<li><strong class="vn">{e(item["name"])}</strong> {e(item["desc"])}</li>' for item in table["items"])
                derive_html = "".join(
                    f'<li><code>{e(rule["id"])}</code> {e(describe(rule["ast"], obs_names))} → <b>{e(_label(field, rule["value"]))}</b>'
                    + (f'<br><span class="sub">출처 {e(rule.get("출처") or "")}</span>' if rule.get("출처") else "") + "</li>"
                    for rule in table["rules"]) + f'<li>그 밖 → <b>{e(_label(field, table["else"]))}</b></li>'
                qa = (f'<p class="sub" style="margin-top:14px">AI가 답하는 관찰 — 값을 고르지 않고 이 항목에만 예/아니오로 답합니다</p>'
                      f'<ul class="values">{observe_html}</ul>'
                      f'<p class="sub" style="margin-top:14px">값 규칙 — 위에서부터 처음 맞는 줄의 값</p><ol class="rules policy-rules">{derive_html}</ol>') + qa
        if prompt_rules and not table:
            decision_html = "".join(
                f'<li><code>{e(rule["id"])}</code> <strong>{e(rule["title"])}</strong>'
                f'<p>{e(rule["내용"])}</p><span class="sub">우선순위 {e(rule["우선순위"])}'
                + (f' · 판례 {e(rule["판례"])}' if rule.get("판례") else "")
                + "</span></li>" for rule in prompt_rules)
            policy_cards_html = ('<section class="card policy-card"><h2>운영 추론 규칙</h2>'
                                 '<p class="sub">정책 정의에서 읽어 프롬프트에 전달되는 문장입니다.</p>'
                                 f'<ol class="rules policy-rules">{decision_html}</ol></section>')
        # 검수 문답 — 사람이 AI의 물음에 답한 것. 원장이 정본이라 화면이 열릴 때 서버(/gt-qa)에서 읽어 채운다(답하면 곧 보인다).
        qa += (f'<div class="qa-live" data-field="{e(field["id"])}" hidden><p class="sub" style="margin-top:14px">검수 문답</p>'
               '<ul class="rules policy-rules"></ul></div>')
        many = " · 값을 여럿 고를 수 있습니다" if field.get("cardinality") == "many" else ""
        edit_box = (f'<details class="edit" data-field="{e(field["id"])}"><summary>이 칸 고치기</summary>'
                    '<div class="edit-body"><p class="sub">서버에서 열었을 때만 고칠 수 있습니다(./serve.sh start).</p></div></details>')
        cards.append(f'<section class="card"><h2>{e(field.get("name") or field["id"])}</h2>'
                     f'<p class="sub">들어올 수 있는 값 {len(field.get("labels") or [])}개{many}</p><ul class="values">{values}</ul>{qa}{source}{edit_box}</section>'
                     + policy_cards_html)
        edit_fields.append({
            "id": field["id"], "name": field.get("name") or field["id"], "many": field.get("cardinality") == "many",
            "values": [{"code": str(label), "name": (field.get("labelNames") or {}).get(label, ""), "desc": notes.get(field["id"], {}).get(label, "")}
                       for label in field.get("labels") or []],
            "observed": None if not table else {
                "items": [dict(item) for item in table["items"]],
                "rules": [{"id": rule["id"], "text": describe(rule["ast"], {i["id"]: i["name"] for i in table["items"]}), "value": rule["value"]}
                          for rule in table["rules"]],
                "else": table["else"]}})
    if policy["archive"]:
        cards.append('<section class="card"><h2>보관</h2><p class="sub">다른 규칙으로 대체된 규칙입니다. 판독 AI는 읽지 않습니다.</p>'
                     '<ul class="rules policy-rules archived">'
                     + "".join(_rule_html(fields.get(rule["field"]) or {}, _archived_readable(rule, policy), archived=True) for rule in policy["archive"])
                     + "</ul></section>")
    rules = [c.get("text") for c in task.get("constraints") or [] if c.get("text")]
    if rules:
        cards.append('<section class="card"><h2>칸끼리 맞아야 하는 것</h2><p class="sub">한 줄 안의 칸이 서로 이 규칙을 어기면 검수 화면에 «GT 안에서 서로 모순»으로 올라옵니다.</p>'
                     '<ul class="rules">' + "".join(f"<li>{e(rule)}</li>" for rule in rules) + "</ul></section>")
    # 목적은 정책의 맨 위 — 값의 경계가 헷갈릴 때 «이 값이 어디에 쓰이나»가 가른다. 정의 문서의 `## 목적` 절이 정본이다.
    # 목적은 정책의 맨 위 — 세 소제목(무엇을 가르나 · 어디에 쓰나 · 기대 효과)이 정본이다. 정책은 이 목적 아래에서 검수하며 구체화된다.
    goal = policy["purpose"]
    purpose = ('<section class="card purpose"><h2>목적</h2>'
               + ("".join(f'<div class="goal-row"><span>{e(part)}</span><div class="deftext">{_md(goal[part], {})}</div></div>' for part in PURPOSE_PARTS)
                  if goal else '<p class="sub">아직 적힌 목적이 없습니다 — 정의 문서에 `## 목적`과 세 소제목(무엇을 가르나 · 어디에 쓰나 · 기대 효과)을 더하면 여기에 나옵니다.</p>')
               + "</section>")
    inner = (f'<header class="page-head"><p class="kicker crumbs"><a href="/">홈</a> / {e(call_name(profile))}</p><h1>정책</h1>'
             '<label class="edit-name">고치는 사람 <input id="editor" placeholder="이름" autocomplete="off"></label>'
             '<p><a href="#policy-document">정책 문서 전체 · 에이전트 전달 정책</a></p>'
             '</header>' + purpose + "".join(cards)
             + _complete_policy_document(path, list(fields)))
    from gt_policy_edit import policy_stamp

    edit_data = {"task": profile["id"], "call": call_name(profile), "stamp": policy_stamp(path), "fields": edit_fields}
    inner += ('<script id="policy-edit-data" type="application/json">'
              + json.dumps(edit_data, ensure_ascii=False).replace("<", "\\u003c") + "</script>")
    script = """<script>
(async () => {
  if (location.protocol === 'file:') return;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);
  try {
    const answer = await (await fetch('/gt-qa?task=' + encodeURIComponent(TASK))).json();
    (answer.answered || []).forEach(q => {
      const box = document.querySelector(`.qa-live[data-field="${CSS.escape(q.field)}"]`);
      if (!box) return;
      box.hidden = false;
      box.querySelector('ul').insertAdjacentHTML('beforeend',
        `<li><span class="rule-text">${esc(q.question)}</span> → <b>${esc(q.answerName)}</b><br>`
        + `<span class="sub"><span class="tag from-review">검수 문답</span> ${esc((q.decidedAt || '').slice(0, 10))} · ${esc(q.reviewer)}`
        + ` · 근거 골든셋 <a href="golden.html#q=${encodeURIComponent(q.key)}">${esc(q.key)}</a></span></li>`);
    });
  } catch (e) {}
})();
</script>""".replace("TASK", json.dumps(profile["id"]))
    return _page(f"정책 · {call_name(profile)}", profile, "policy", inner + script + "<script>" + POLICY_EDIT_SCRIPT + "</script>")


def render_policy_only_html(profile: dict[str, Any]) -> str:
    """Read-only policy page for a policyTask that intentionally has no GT worklist."""
    from policy_document import load_document
    config = profile.get("policyTask") or profile.get("gtTask") or {}
    source = resolve(profile, {"path": config["definitions"], "root": config.get("definitionsRoot") or "project"})
    document = load_document(source)
    if document is not None:
        return _standard_policy_html(profile, source, document)
    e = html.escape
    spec = profile.get("policyTask") or {}
    path = resolve(profile, {"path": spec["definitions"], "root": spec.get("definitionsRoot") or "project"})
    from policy_prompt import allowed_values, render_inference_rules
    definition_by_field = definition_texts(path)
    cards: list[str] = []
    for field in spec.get("fields") or []:
        field_id = field["id"]
        values = allowed_values(path, field_id)
        rules = decision_rules(path, field_id)
        body = definition_by_field.get(field_id, "")
        supplemental = re.split(r"(?m)^### (?:허용값|판정 규칙|관찰|값 규칙|규칙)\s*$", body, maxsplit=1)[0].strip()
        supplemental_html = (f'<details><summary>추가 정책 설명</summary>{_md(supplemental, {})}</details>'
                             if supplemental else "")
        value_html = "".join(f'<li><code>{e(row["code"])}</code> {e(row["name"])} — {e(row["description"])}</li>'
                             for row in values)
        rule_html = "".join(f'<li><b>{e(rule["우선순위"])}. {e(rule["title"])}</b><p>{e(rule["내용"])}</p>'
                            f'<small>출처 {e(rule.get("출처") or "미기재")}</small></li>'
                            for rule in rules)
        rendered = render_inference_rules(path, field_id)
        cards.append(f'<section><h2>{e(field.get("name") or field_id)}</h2>'
                     f'{supplemental_html}<h3>허용값</h3><ul>{value_html}</ul>'
                     f'<h3>운영 추론에 전달되는 판정 규칙</h3><ol>{rule_html}</ol>'
                     f'<details><summary>운영 프롬프트에 전달하는 규칙 발췌</summary><pre>{e(rendered)}</pre></details></section>')
    title = profile.get("displayName") or profile.get("id") or "정책"
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>{e(title)} · 정책</title><style>body{{font:16px/1.65 system-ui;max-width:900px;margin:36px auto;padding:0 20px;color:#18202a}}'
            'section{border:1px solid #d8dee7;border-radius:12px;padding:20px;margin:18px 0}h1,h2{line-height:1.3}li{margin:10px 0}pre{white-space:pre-wrap;background:#f3f5f8;padding:16px;border-radius:8px}</style>'
            f'<h1>{e(title)}</h1><p>아래 규칙은 우선순위 순서로 운영 추론 프롬프트에 전달됩니다.</p>'
            + "".join(cards) + _complete_policy_document(path, [f["id"] for f in spec.get("fields") or []]) + "</html>")


# 정책 화면의 편집 — 서버(`POST /gt-policy` → gt_policy_edit.edit_policy)가 미리 보기와 넣기를 한다. 화면은 모양을 고르지 않는다:
# 무엇이 옳은 줄인지는 서버의 함수(로더·가림 검사)가 정하고, 화면은 그 답을 그대로 보인다.
POLICY_EDIT_SCRIPT = r"""
(() => {
  const dataNode = document.getElementById('policy-edit-data');
  if (!dataNode || location.protocol === 'file:') return;
  const DATA = JSON.parse(dataNode.textContent);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const editor = document.getElementById('editor');
  try { editor.value = sessionStorage.getItem('gt-review-reviewer') || ''; } catch (e) {}
  editor.addEventListener('change', () => { try { sessionStorage.setItem('gt-review-reviewer', editor.value.trim()); } catch (e) {} });
  const byField = Object.fromEntries(DATA.fields.map(f => [f.id, f]));
  const valueName = (f, code) => { const v = f.values.find(x => x.code === code); return v && v.name ? v.name : code; };
  const opt = (value, label, selected) => `<option value="${esc(value)}"${selected ? ' selected' : ''}>${esc(label)}</option>`;
  const valueSelect = f => f.values.map(v => opt(v.code, `${v.name || v.code} (${v.code})`)).join('');
  const combo = (items, extra) => '<div class="combo">' + [...items, ...(extra ? [extra] : [])].map(it =>
      `<label><span>${esc(it.name)}</span><select data-obs="${esc(it.id)}">${opt('', '상관없음')}${opt('yes', '예')}${opt('no', '아니오')}</select></label>`).join('') + '</div>';
  // 넣을 자리 — 표는 위에서부터 처음 맞는 줄이 이긴다. 기본은 «그 밖» 바로 앞(다른 줄을 덮지 않는다). 위로 올릴수록 먼저 적용된다.
  const positions = (f, obs) => `<select name="before">${opt('end', '맨 아래 — «그 밖» 바로 앞', true)}`
      + obs.rules.map(r => opt(r.id, `${r.id} 앞 — ${r.text} → ${valueName(f, r.value)}`)).join('') + `</select>`;
  const positionHelp = '<p class="sub">표는 위에서부터 처음 맞는 줄의 값을 씁니다 — 위에 넣을수록 먼저 적용되어 아래 줄을 덮습니다.</p>';
  const exprOf = (form, newId) => {
    const parts = [...form.querySelectorAll('select[data-obs]')].filter(s => s.value).map(s => {
      const id = s.dataset.obs === '__NEW__' ? newId : s.dataset.obs;
      return (s.value === 'no' ? '!' : '') + id;
    });
    return parts.join(' & ');
  };
  const section = (title, body, op) => `<form class="edit-form" data-op="${op}"><p class="edit-title">${esc(title)}</p>${body}`
      + `<div class="edit-actions"><button type="submit" class="btn">미리 보기</button></div><div class="edit-result" hidden></div></form>`;
  const input = (name, label, ph) => `<label class="edit-row"><span>${esc(label)}</span><input name="${name}" placeholder="${esc(ph || '')}" autocomplete="off"></label>`;
  const selectRow = (name, label, options) => `<label class="edit-row"><span>${esc(label)}</span><select name="${name}">${options}</select></label>`;

  document.querySelectorAll('details.edit').forEach(box => {
    const f = byField[box.dataset.field];
    if (!f) return;
    let html = section('값 더하기', input('code', '코드', '영문 대문자·숫자·밑줄 (예: NEW_VALUE)') + input('name', '이름', '짧게 — 화면에 보일 이름')
        + input('desc', '설명', '무엇이 이 값인가 — 한 문장'), 'value-add');
    html += section('값 이름·설명 고치기', selectRow('code', '값', valueSelect(f)) + input('name', '새 이름', '그대로면 비워 두기')
        + input('desc', '새 설명', '그대로면 비워 두기'), 'value-edit');
    html += section('값 빼기', selectRow('code', '값', valueSelect(f)), 'value-remove');
    if (f.observed) {
      const o = f.observed;
      html += section('값 규칙 더하기 — 관찰 조합이 이러면 이 값', combo(o.items) + selectRow('value', '값', valueSelect(f))
          + `<label class="edit-row"><span>넣을 자리</span>${positions(f, o)}</label>` + positionHelp, 'derive-add');
      html += section('값 규칙 빼기', selectRow('id', '줄', o.rules.map(r => opt(r.id, `${r.id} — ${r.text} → ${valueName(f, r.value)}`)).join(''))
          + input('reason', '이유', '보관에 남습니다'), 'derive-retire');
      html += section('관찰 항목 더하기 — 그 항목을 쓰는 값 규칙 한 줄과 함께',
          input('id', 'ID', '영문 대문자 (예: NEW_MARK)') + input('name', '이름', '짧게 — 화면에 보일 이름') + input('desc', '뜻', '무엇이 «예»인가 — 한 문장')
          + '<p class="sub">아래 조합에서 «새 항목»을 예나 아니오로 고르세요.</p>'
          + combo(o.items, {id: '__NEW__', name: '새 항목'}) + selectRow('value', '값', valueSelect(f))
          + `<label class="edit-row"><span>넣을 자리</span>${positions(f, o)}</label>` + positionHelp, 'observe-add');
      html += section('관찰 뜻 고치기', selectRow('id', '항목', o.items.map(i => opt(i.id, `${i.name} (${i.id})`)).join(''))
          + input('name', '이름', '') + input('desc', '뜻', ''), 'observe-edit');
    }
    const ask = `${DATA.call} ${f.name} 규칙 추가해줘`;
    html += `<div class="edit-form"><p class="edit-title">문장 규칙 더하기·고치기</p><p class="sub">Claude에게 이렇게 말해 주세요 — 겹침·충돌을 AI가 먼저 가린 뒤 넣습니다.</p>`
        + `<p><code>${esc(ask)}</code> <button type="button" class="btn ghost copy" data-copy="${esc(ask)}">복사</button></p></div>`;
    box.querySelector('.edit-body').innerHTML = html;
    // 관찰 뜻 고치기 — 고를 때 지금 이름·뜻을 채운다
    box.querySelectorAll('form[data-op="observe-edit"]').forEach(form => {
      const fill = () => { const it = f.observed.items.find(i => i.id === form.id.value); form.name.value = it ? it.name : ''; form.desc.value = it ? it.desc : ''; };
      form.id.addEventListener('change', fill); fill();
    });
  });

  document.addEventListener('click', event => {
    const copy = event.target.closest('button.copy');
    if (copy) { navigator.clipboard && navigator.clipboard.writeText(copy.dataset.copy); copy.textContent = '✓ 복사했습니다'; }
  });

  const paramsOf = form => {
    const op = form.dataset.op, get = n => (form.elements[n] ? form.elements[n].value.trim() : '');
    const p = {};
    ['code', 'name', 'desc', 'id', 'value', 'reason', 'before'].forEach(n => { if (form.elements[n]) p[n] = get(n); });
    if (op === 'derive-add') p.when = exprOf(form);
    if (op === 'observe-add') p.when = exprOf(form, get('id'));
    return p;
  };
  const describePlan = (f, plan) => {
    const rows = [];
    // 정책 문서의 줄(«- `CODE` 이름 — 설명»)을 사람 말로 — 글머리표·백틱을 떼고 코드는 괄호로.
    const clean = t => esc(String(t || '').replace(/^- /, '').replace(/^`([^`]+)`\s*/, '($1) ').replace(/`/g, ''));
    if (plan.before) rows.push(`<p class="was">${plan.after ? '지금' : '빼는 값'}: ${clean(plan.before)}</p>`);
    if (plan.impact) rows.push(`<p>이 값을 가진 GT 칸 ${plan.impact.gtCells || 0}개 · 이 값으로 고친 판정 ${plan.impact.correctedTo || 0}개 · 유지한 판정 ${plan.impact.keptAs || 0}개</p>`);
    if (plan.after) rows.push(`<p class="will">${plan.before ? '바뀜' : '더함'}: ${clean(plan.after)}</p>`);
    if (plan.rule && plan.rule.length) rows.push(`<p class="will">값 규칙: ${esc(plan.readable || clean(plan.rule[0]))}</p>`);
    if (plan.archive && plan.archive.length) rows.push(`<p class="was">보관으로: ${esc(!(plan.rule && plan.rule.length) && plan.readable ? plan.readable : clean(plan.archive[0]))}</p>`);
    if (plan.changes) {
      const names = Object.assign(Object.fromEntries(((f.observed || {}).items || []).map(i => [i.id, i.name])), plan.names || {});
      rows.push(`<p>관찰 조합 ${plan.changes.total}개 가운데 <b>${plan.changes.changed}개</b>의 값이 바뀝니다.</p>`);
      if (plan.changes.examples.length) rows.push('<ul class="changes">' + plan.changes.examples.map(x =>
        `<li>${(Object.entries(x.answers).filter(([, v]) => v).map(([k]) => esc(names[k] || k)).join(' · ') || '모두 아니오') + (Object.values(x.answers).some(v => v) ? ' (나머지 아니오)' : '')}: `
        + `${esc(valueName(f, x.before))} → <b>${esc(valueName(f, x.after))}</b></li>`).join('') + '</ul>');
    }
    if (plan.table) rows.push('<details><summary>바뀐 뒤 값 규칙 표</summary><ol class="table-lines">' + plan.table.map(t => `<li><code>${esc(t)}</code></li>`).join('') + '</ol></details>');
    (plan.warnings || []).forEach(w => rows.push(`<p class="warn">${esc(w)}</p>`));
    if (plan.history) rows.push(`<p class="sub">변경 이력에 남는 줄: ${esc(String(plan.history).replace(/^- /, '').replace(/`/g, ''))}</p>`);
    rows.push('<p class="sub">넣은 뒤 되돌리려면 같은 칸에서 반대로 고치거나(값 빼기·값 규칙 빼기), Claude에게 «정책 마지막 변경 되돌려줘»라고 말해 주세요.</p>');
    return rows.join('');
  };
  document.addEventListener('submit', async event => {
    const form = event.target.closest('form.edit-form');
    if (!form) return;
    event.preventDefault();
    const out = form.querySelector('.edit-result');
    const field = form.closest('details.edit').dataset.field, f = byField[field];
    // 넣기는 미리 본 그대로를 보낸다 — 미리 본 뒤 칸을 고쳤으면 그 고친 것은 미리 보지 않은 것이다.
    const previewed = {params: paramsOf(form), reviewer: editor.value.trim()};
    const send = async confirm => {
      const reviewer = previewed.reviewer;
      if (!reviewer) { out.hidden = false; out.innerHTML = '<p class="warn">맨 위 «고치는 사람»에 이름을 적어 주세요.</p>'; editor.focus(); return null; }
      const response = await fetch('/gt-policy', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task: DATA.task, field, op: form.dataset.op, reviewer, confirm, expectedStamp: DATA.stamp, params: previewed.params})});
      return response.json().catch(() => ({ok: false, error: '서버 답을 읽지 못했습니다.'}));
    };
    const lock = on => form.querySelectorAll('input, select').forEach(el => { el.disabled = on; });
    out.hidden = false; out.innerHTML = '<p class="sub">확인하는 중…</p>';
    const preview = await send(false);
    if (!preview) return;
    if (!preview.ok) { out.innerHTML = `<p class="warn">${esc(preview.error)}</p>`; return; }
    out.innerHTML = describePlan(f, preview.plan) + '<div class="edit-actions"><button type="button" class="btn apply">넣기</button>'
      + '<button type="button" class="btn ghost cancel">그만두기</button></div>';
    lock(true);  // 미리 본 동안은 칸을 잠근다 — 보이는 것과 넣는 것이 같게
    out.querySelector('.cancel').onclick = () => { out.hidden = true; out.innerHTML = ''; lock(false); };
    out.querySelector('.apply').onclick = async () => {
      out.querySelector('.apply').disabled = true;
      const done = await send(true);
      if (!done) return;
      if (!done.ok) { out.insertAdjacentHTML('beforeend', `<p class="warn">${esc(done.error)}</p>`); return; }
      out.innerHTML = '<p class="ok">넣었습니다 — 정책이 새 버전이 됐습니다. 새로 읽습니다…</p>'
        + (done.plan && done.plan.pagesError ? `<p class="warn">${esc(done.plan.pagesError)}</p>` : '');
      setTimeout(() => location.reload(), 700);
    };
  });
})();
"""


GOLDEN_SCRIPT = r"""
(() => {
  const rows = [...document.querySelectorAll('.gcard')];
  const q = document.getElementById('q'), field = document.getElementById('field'), value = document.getElementById('value'), only = document.getElementById('only');
  const shown = document.getElementById('shown');
  const values = {};
  rows.forEach(r => [...r.querySelectorAll('[data-f]')].forEach(td => { (values[td.dataset.f] = values[td.dataset.f] || new Set()).add(td.dataset.v); }));
  const fillValues = () => {
    value.textContent = '';
    const all = document.createElement('option'); all.value = ''; all.textContent = '모든 값'; value.appendChild(all);
    if (!field.value) { value.disabled = true; return; }
    value.disabled = false;
    [...(values[field.value] || [])].sort().forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = v || '(빈칸)'; value.appendChild(o); });
  };
  const apply = () => {
    const needle = q.value.trim().toLowerCase();
    let n = 0;
    rows.forEach(r => {
      let ok = !needle || r.dataset.key.toLowerCase().includes(needle);
      if (ok && field.value && value.value !== '') ok = (r.querySelector(`[data-f="${CSS.escape(field.value)}"]`) || {}).dataset?.v === value.value;
      if (ok && only.checked) ok = r.dataset.decided === '1' || r.dataset.out === '1';
      r.hidden = !ok; if (ok) n += 1;
    });
    shown.textContent = `${n} / ${rows.length}건`;
  };
  q.addEventListener('input', apply); field.addEventListener('change', () => { fillValues(); apply(); });
  value.addEventListener('change', apply); only.addEventListener('change', apply);
  // 정책의 변경 이력이 «golden.html#q=<키>»로 보낸다 — 그 줄만 보이게 연다.
  const wanted = decodeURIComponent((location.hash.match(/^#q=(.+)$/) || [])[1] || '');
  if (wanted) q.value = wanted;
  fillValues(); apply();
})();
"""


def item_links(profile: dict[str, Any], task: dict[str, Any], rows: dict[str, Any]) -> dict[str, str]:
    """키마다 상품 페이지(PDP) 주소. 프로필 `gtTask.linkField`가 칸 이름을 선언한다 — GT 행에서 먼저 찾고, 없으면 사진 원본 행
    (`gtTask.images`의 파일, 같은 키)에서 찾는다. 주소 모양(어느 쇼핑몰인지)은 엔진이 모른다. http(s) 주소만 링크로 쓴다."""
    name = task.get("linkField")
    if not name:
        return {}
    links = {key: str(row.get(name)) for key, row in rows.items() if row.get(name)}
    images = task.get("images") or {}
    if set(rows) - set(links) and images.get("path"):
        try:
            path = resolve(profile, {"path": images["path"], "root": images.get("root") or "project"})
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    key = str(row.get(images.get("keyField") or task.get("keyField")))
                    if key in rows and key not in links and row.get(name):
                        links[key] = str(row[name])
        except (OSError, ValueError, TaskError):
            pass  # 사진 원본을 못 읽으면 링크 없이 그린다 — 표가 안 뜨는 것보다 낫다
    return {key: url for key, url in links.items() if url.startswith(("https://", "http://"))}


GOLDEN_THUMB_EDGE = 240  # 카드의 사진 칸(96×120)을 두 배 밀도로 채우는 크기 — 더 크면 149장이 무겁다


def golden_thumbs(profile: dict[str, Any], task: dict[str, Any], rows: dict[str, Any], root: Path | None) -> dict[str, str]:
    """키마다 대표 사진 한 장을 작게 줄여 `golden-thumbs/`에 둔다(화면 폴더 기준 상대 경로를 돌려준다). 사진은 과제가 선언한
    사진 색인(`gtTask.images`)에서 고른다 — 역할이 선언돼 있으면 선언한 역할 순서대로 첫 장. 이미 만든 사진은 원본이 더 새롭지 않으면
    다시 만들지 않는다. 사진을 못 찾거나 못 열면 그 키는 빈 칸으로 둔다 — 골든셋을 읽는 화면이 사진 때문에 안 뜨면 안 된다.
    이 화면은 판독자에게 가지 않으므로 눈가림 규칙(키를 파일 이름에 쓰지 않기)과 무관하지만, 키에 든 글자를 피하려고 해시로 이름 짓는다."""
    if root is None or not (task.get("images") or {}).get("fileField"):
        return {}
    try:
        from gt_images import ImageIndex
        from PIL import Image
        import hashlib

        index = ImageIndex(profile, task)
    except (ImportError, TaskError, OSError, ValueError):
        return {}
    spec = index.spec
    roles = list(spec.get("roles") or [])
    role_field = spec.get("roleField") or "role"
    folder = root / "golden-thumbs"
    found: dict[str, str] = {}
    for key, row in rows.items():
        try:
            entries = index.entries(row)
            if roles:
                entries = sorted(entries, key=lambda entry: roles.index(entry.get(role_field)) if entry.get(role_field) in roles else len(roles))
            source = next((path for path in (index.file_of(entry) for entry in entries) if path and path.is_file()), None)
            if source is None:
                continue
            target = folder / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".jpg")
            if not target.is_file() or target.stat().st_mtime < source.stat().st_mtime:
                with Image.open(source) as image:
                    image = image.convert("RGB")
                    image.thumbnail((GOLDEN_THUMB_EDGE, GOLDEN_THUMB_EDGE * 5 // 4))
                    folder.mkdir(parents=True, exist_ok=True)
                    image.save(target, "JPEG", quality=82, optimize=True)
            found[key] = f"golden-thumbs/{target.name}"
        except Exception:  # noqa: BLE001 — 사진 한 장이 화면 전체를 멈추게 두지 않는다
            continue
    return found


def render_golden_html(profile: dict[str, Any], root: Path | None = None) -> str:
    """골든셋 — 이 과제의 GT 전체를 읽기 전용 카드 목록으로(검수 화면의 접힌 상품과 같은 모양: 대표 사진 · 키 · 칸별 값).
    값은 지금 규칙(legacy)으로 읽고, 사람이 고쳤는데 아직 원본에 안 들어간 칸과 정책의 값 목록에 없는 칸을 표시한다.
    여기서는 고치지 않는다 — 고치는 곳은 검수 화면 하나다. `root`(화면 폴더)가 있으면 대표 사진을 작게 만들어 곁에 둔다."""
    from gt_decisions import current_answers

    e = html.escape
    task = load_task(profile)
    rows = load_gt(profile, task)
    latest = current_answers(profile, task)
    links = item_links(profile, task, rows)
    thumbs = golden_thumbs(profile, task, rows, root)
    fields = task["fields"]
    group_field, title_field = task.get("groupField"), task.get("titleField")
    body = []
    for key, row in rows.items():
        cells, decided, out = [], False, False
        for field in fields:
            value = gt_value(task, field, row)
            name = _label(field, value)
            note = ""
            if value is not None and not in_range(field, value):
                note, out = '<span class="pill out">정책 밖</span>', True
            entry = latest.get((key, field["id"]))
            if entry and entry.get("decision") in ("CORRECT", "CLEAR") and entry.get("after") != value:
                note += f'<span class="pill to">→ {e(_label(field, entry.get("after")))} 판정</span>'
                decided = True
            elif entry and entry.get("decision") == "CONFIRM" and entry.get("before") == value:
                note += '<span class="pill ok">확인</span>'
            shown = f'<span class="empty">(빈칸)</span>' if value is None else e(name)
            label = field.get("name") or field["id"]
            cells.append(f'<div data-f="{e(label)}" data-v="{e("" if value is None else name)}"><dt>{e(label)}</dt><dd>{shown}{note}</dd></div>')
        source = gt_source(task, fields[0], row) if fields else ""
        grade = AUTHORITY_SHORT.get(authority(task, source), "") if source else ""
        link = links.get(key)
        img = (f'<img loading="lazy" alt="" src="{e(thumbs[key])}">' if key in thumbs else "")
        thumb = (f'<a class="gthumb" href="{e(link)}" target="_blank" rel="noopener" title="상품 페이지 열기">{img}</a>' if link and img
                 else f'<span class="gthumb">{img}</span>' if img else '<span class="gthumb none">사진 없음</span>')
        name_html = (f'<a class="gkey" href="{e(link)}" target="_blank" rel="noopener" title="상품 페이지 열기">{e(key)}</a>' if link
                     else f'<span class="gkey">{e(key)}</span>')
        sub = " · ".join(str(row.get(name)) for name in (title_field, group_field) if name and row.get(name) not in (None, ""))
        body.append(f'<section class="gcard" data-key="{e(key)}" data-decided="{"1" if decided else "0"}" data-out="{"1" if out else "0"}">'
                    f'{thumb}<div><p class="ghead">{name_html}{f"<small>{e(sub)}</small>" if sub else ""}'
                    f'<span class="src" title="{e(source)}">{e(grade)}</span></p>'
                    f'<dl class="gvals">{"".join(cells)}</dl></div></section>')
    options = "".join(f'<option value="{e(field.get("name") or field["id"])}">{e(field.get("name") or field["id"])}</option>' for field in fields)
    inner = (f'<header class="page-head"><p class="kicker crumbs"><a href="/">홈</a> / {e(call_name(profile))}</p><h1>골든셋</h1>'
             f'<p class="lead">{e(call_name(profile))}의 GT 전체입니다({len(rows)}건). 읽기 전용입니다 — 고치는 곳은 검수 화면 하나입니다. '
             '«→ … 판정»은 사람이 고쳤지만 아직 원본에 들어가지 않은 칸, «정책 밖»은 정책의 값 목록에 없는 값입니다.</p></header>'
             '<div class="filters"><input id="q" type="search" placeholder="키로 찾기" aria-label="키로 찾기">'
             f'<select id="field" aria-label="칸"><option value="">모든 칸</option>{options}</select>'
             '<select id="value" aria-label="값" disabled></select>'
             '<label><input type="checkbox" id="only"> 판정 대기·정책 밖만</label><span class="shown" id="shown"></span></div>'
             f'<div class="gcards">{"".join(body)}</div>')
    return _page(f"골든셋 · {call_name(profile)}", profile, "golden", inner, GOLDEN_SCRIPT)


def render(profile: dict[str, Any], worklist: dict[str, Any], sweep: dict[str, Any] | None,
           ledger: list[dict[str, Any]], root: Path) -> list[Path]:
    # 허용값·이름표는 그릴 때마다 정책(정의 문서)에서 다시 읽는다 — 작업 목록에 찍힌 옛 목록으로 그리면, 정책을 고쳐도
    # 열린 화면의 «다른 값…»이 새 배치 전까지 옛 목록을 보인다.
    # 정책을 읽지 못하면(방금 고친 줄이 틀렸다) 화면은 배치 때 목록으로 그린다 — 화면이 안 열리는 것보다 낫고, 틀린 줄은
    # 다음 준비(prepare)가 문장으로 멈춰 알린다.
    try:
        policy = {field["id"]: field for field in load_task(profile)["fields"]}
    except (TaskError, OSError, ValueError) as error:
        # 말없이 옛 목록으로 그리면 사람은 정책을 고쳤는데 화면이 안 바뀐 까닭을 모른다.
        print(f"경고: 정책을 읽지 못해 배치를 준비할 때의 값 목록으로 그렸습니다 — {error}", file=sys.stderr)
        policy = {}
    worklist = {**worklist, "fields": [
        {**field, **{key: policy[field["id"]][key] for key in ("labels", "labelNames") if key in policy.get(field["id"], {})}}
        for field in worklist.get("fields") or []]}
    review = merge(worklist, sweep, ledger)
    review_path = root / "review.json"
    html_path = root / "review.html"
    html = render_html(profile, review, root)
    # 서버가 경로를 관습으로 추측하지 않도록 화면의 자리를 선언한다.
    manifest = root / "manifest.json"
    # 화면·판정 요약·화면 자리를 판정 기록·준비와 같은 잠금 안에서, 한 번에 바꿔 끼운다. 그사이 새 배치가 준비됐으면
    # (manifest의 배치가 이 작업 목록과 다르면) 옛 화면으로 아무것도 덮지 않는다. 준비 시각은 잇는다.
    with locked(profile):
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
        except (OSError, json.JSONDecodeError):
            previous = {}
        if not previous.get("batchId") or previous.get("batchId") == worklist.get("batchId"):
            _write_atomic(review_path, json.dumps(review, ensure_ascii=False, indent=2) + "\n")
            _write_atomic(html_path, html)
            _write_atomic(manifest, json.dumps({"schemaVersion": "gt-review-manifest-v1", "profileId": profile.get("id"),
                                                "page": "review.html", "review": "review.json",
                                                "batchId": worklist.get("batchId"), "preparing": False,
                                                # 준비 시각이 없던 옛 화면이면 배치 이름 앞의 시각을 쓴다(«가장 최근 과제»를 가를 수 있게).
                                                "preparedAt": previous.get("preparedAt") or str(worklist.get("batchId") or "")[:25] or None},
                                               ensure_ascii=False, indent=2) + "\n")
            written = [review_path, html_path, manifest]
            # 사이드바의 정책·골든셋 두 장 — 읽기 전용 파생물이다. 못 만들어도 검수 화면은 그대로 둔다.
            for name, build in (("policy.html", render_policy_html), ("golden.html", lambda p: render_golden_html(p, root))):
                try:
                    _write_atomic(root / name, build(profile))
                    written.append(root / name)
                except (TaskError, OSError, ValueError, KeyError) as error:
                    print(f"경고: {name}을 만들지 못했습니다 — {error}", file=sys.stderr)
            return written
    return []  # 새 배치가 준비됐다 — 아무것도 쓰지 않았다
