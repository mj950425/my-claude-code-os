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

import html
import json
import os
from pathlib import Path
from typing import Any

from catalog_profile import PROJECT_ROOT
from gt_decisions import _write_atomic, answered_on_page, locked
from gt_task import MANY_SEPARATOR, definition_texts, resolve
from page_style import head

REVIEW_SCHEMA = "gt-review-v2"
STATUS = {
    "FIX_PROPOSED": {"order": 1, "name": "고치자는 제안",
                     "hint": "정답을 모른 채 증거만 본 AI가 GT와 다른 값을 냈고, GT 편에서 반론한 AI도 그 값이 맞다고 봤습니다."},
    "FILL_PROPOSED": {"order": 2, "name": "채우자는 제안",
                      "hint": "GT가 비어 있는 칸입니다. 증거만 본 AI가 값을 냈고 반론한 AI도 동의했습니다."},
    "CONTESTED": {"order": 3, "name": "의견이 갈림",
                  "hint": "두 AI가 서로 다른 답을 냈습니다. 증거를 직접 보고 골라 주세요."},
    "NEEDS_HUMAN_LOOK": {"order": 4, "name": "증거로 못 가름",
                         "hint": "AI가 확신하지 못했습니다. 증거가 부족하거나 경계에 걸린 사례입니다."},
    "NO_EVIDENCE": {"order": 5, "name": "증거 없음",
                    "hint": "이 건에는 사진도 글도 없어 AI가 보지 못했습니다. GT 값과 이유만 보고 판단해 주세요."},
    "NOT_READ": {"order": 6, "name": "AI가 못 읽음",
                 "hint": "이 건은 AI 판독이 돌아오지 않았습니다. Claude에게 «못 읽은 거 다시 봐줘»라고 말해 주세요."},
    "GT_HOLDS": {"order": 7, "name": "AI가 GT와 같게 봄",
                 "hint": "정답을 모르는 AI가 증거만 보고 같은 값을 냈습니다."},
}
ACTIONABLE = ("FIX_PROPOSED", "FILL_PROPOSED", "CONTESTED", "NEEDS_HUMAN_LOOK", "NO_EVIDENCE")
CONTRADICTED_HINT = "AI는 GT와 같은 값을 냈지만, 같은 줄의 다른 칸과 말이 안 맞습니다. 두 칸을 함께 보고 골라 주세요."
AUTHORITY_WORDS = {"TRUSTED": "사람이 확정한 값입니다 — 뒤집으려면 증거(사진·글)를 꼭 직접 보세요",
                   "REFERENCE": "사람이 아직 확인하지 않은 값입니다", "UNKNOWN": "누가 붙였는지 모르는 값입니다"}
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
const data = JSON.parse(document.getElementById('gt-review-data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const NAMES = {CORRECT: '고침', CONFIRM: 'GT 유지', LEAVE_EMPTY: '빈칸 유지', CLEAR: '비움', HOLD: '보류'};
const CHANGES = ['CORRECT', 'CLEAR'];
const labelOf = (field, value) => value == null ? '(빈칸)' : ((data.labelNames[field] || {})[value] ? `${data.labelNames[field][value]} (${value})` : value);
let pendingBox = null;
const nameBox = document.getElementById('reviewer');
try { nameBox.value = localStorage.getItem('gt-review-reviewer') || ''; } catch (e) {}
if (nameBox.value) {
  // 여럿이 쓰는 PC에서 앞사람 이름으로 판정이 쌓이지 않게 — 채워 둔 이름은 채워 두었다고 말한다.
  const hint = document.getElementById('name-hint');
  if (hint) { hint.textContent = `지난번 이름(${nameBox.value})을 넣어 두었습니다 — 본인이 아니면 맨 위 이름 칸을 바꿔 주세요.`; hint.hidden = false; }
}
nameBox.addEventListener('change', () => {
  try { localStorage.setItem('gt-review-reviewer', nameBox.value.trim()); } catch (e) {}
  // 이름을 적으면 그 칸으로 돌아와, 아직 기록되지 않았다고 말한다 — 조용히 지우면 기록된 줄 안다.
  if (nameBox.value.trim()) { const hint = document.getElementById('name-hint'); if (hint) hint.hidden = true; }
  if (pendingBox && nameBox.value.trim()) {
    pendingBox.scrollIntoView({block: 'center'});
    const out = pendingBox.querySelector('.result, .keep-note');
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

function paint(box, entry) {
  const out = box.querySelector('.result');
  box.classList.remove('done', 'held');
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
  out.innerHTML = `<b>${esc(NAMES[entry.decision])}</b> 기록됨 (${esc(entry.reviewer)})${change}. 다시 누르면 새 판정이 옛 판정을 대신합니다.${warn}`;
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
  const holds = [...document.querySelectorAll('.cell.GT_HOLDS:not(.alt)')].filter(open).length;
  // 대체 정답을 낸 칸은 묶음 유지에서 빠진다 — 칸마다 «지금 GT가 맞다»를 눌러야 한다.
  const alts = [...document.querySelectorAll('.cell.GT_HOLDS.alt')].filter(open).length;
  // AI가 못 읽은 칸이 남았으면 «다 했습니다»라고 하지 않는다 — «다음 거»가 그 칸을 뒤로 밀어낸다.
  const finished = done === boxes.length && !holds && !alts && !unread;
  let text = boxes.length
    ? `답한 칸 ${done} / 볼 칸 ${boxes.length}` + (held ? ` (그중 보류 ${held} — 다음에 다시 나옵니다)` : '')
    : '이번 화면에는 고칠지 가를 칸이 없습니다.';
  if (boxes.length && boxes.every(b => b.classList.contains('NOT_READ')))
    text = `AI가 이번에 읽지 못했습니다 — 칸 ${boxes.length}개를 직접 보고 고르셔도 되고, 다음 거로 넘어가기 전에 Claude에게 «못 읽은 거 다시 봐줘»라고 하셔도 됩니다. 답한 칸 ${done}`;
  if (holds) text += ` · AI가 GT와 같게 본 칸 ${holds} — 건마다 «이 값들을 GT로 유지»를 눌러 주세요.`;
  if (alts) text += ` · 같이 맞다고 적어 둔 값을 AI가 낸 칸 ${alts} — 그 칸의 «지금 GT가 맞다»를 눌러 주세요.`;
  if (finished) text += ' — 다 했습니다. Claude에게 «반영해줘» 또는 «다음 거»라고 말해 주세요.';
  const allUnread = boxes.length && boxes.every(b => b.classList.contains('NOT_READ'));
  if (unread && !allUnread) text += ` · AI가 못 읽은 칸 ${unread} — 다음으로 넘어가기 전에 Claude에게 «못 읽은 거 다시 봐줘»라고 말해 주세요.`;
  const stale = document.querySelectorAll('.cell.stale').length;
  if (stale) text += ` · 원본이 바뀐 칸 ${stale} — «다음 거»에서 새 값으로 다시 나옵니다.`;
  document.getElementById('progress').textContent = text;
  // 휴대폰에서는 마지막 칸이 화면 맨 아래다 — 진행률을 아래에도 보여 «다 했습니다»를 놓치지 않게.
  // 아래 막대는 짧은 요약만 — 긴 문장이 버튼 줄을 가리지 않게. 긴 문장은 위에 있다.
  const bottom = document.getElementById('progress-bottom');
  const short = finished ? '다 했습니다 — Claude에게 «반영해줘» 또는 «다음 거»'
    : `답한 칸 ${done} / ${boxes.length}` + (holds ? ` · 유지 대기 ${holds}` : '') + (alts ? ` · 대체 정답 ${alts}` : '') + (unread ? ` · 못 읽음 ${unread}` : '');
  if (bottom) bottom.textContent = short;
  const top = document.getElementById('progress-short');
  if (top) top.textContent = short;
  document.querySelectorAll('button.keep-all').forEach(button => {
    const left = [...button.closest('.item').querySelectorAll('.cell.GT_HOLDS')]
      .filter(b => !b.classList.contains('done') && !b.classList.contains('stale') && !b.classList.contains('held') && !b.classList.contains('alt')).length;
    if (!left && !button.dataset.busy) {
      button.disabled = true; button.textContent = 'AI와 같은 칸 모두 유지됨';
      const guide = button.parentElement.querySelector('.keep-guide');
      if (guide) guide.textContent = '유지로 기록했습니다 — 다시 나오지 않습니다.';
    }
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
    if (!answer.ok) throw new Error(human(answer.error, response));
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

// 이유·경계 표시는 버튼을 누를 때 함께 간다. 누른 뒤에 바꾸면 조용히 버려지므로, 다시 누르라고 말한다.
document.querySelectorAll('.cell input.reason, .cell input.gap').forEach(input => input.addEventListener('change', () => {
  const box = input.closest('.cell');
  if (!box.classList.contains('done') && !box.classList.contains('held')) return;
  box.querySelector('.result').insertAdjacentHTML('beforeend',
    '<br><b>아직 저장되지 않았습니다</b> — 바꾼 이유·경계 표시를 남기려면 판정 버튼을 한 번 더 눌러 주세요.');
}));

document.querySelectorAll('.cell button[data-decision]').forEach(button => button.addEventListener('click', () => {
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

// 건마다 «AI와 같은 칸 모두 GT 유지». 사람이 누르는 일괄 판정이라 원장에 들어간다.
document.querySelectorAll('button.keep-all').forEach(button => button.addEventListener('click', async () => {
  const note = button.parentElement.querySelector('.keep-note');
  if (!nameBox.value.trim()) {
    // 칸 버튼과 같게 — 이름을 적으면 이 버튼으로 돌아온다(휴대폰에서는 이름 칸이 몇 화면 위다).
    note.textContent = '맨 위에 이름을 먼저 적어 주세요. 적고 나면 이 자리로 돌아옵니다.';
    pendingBox = note.closest('p') || button;
    const hint = document.getElementById('name-hint');
    if (hint) { hint.textContent = '이름을 적고 Enter(또는 칸 밖)를 누르면 방금 누른 자리로 돌아갑니다.'; hint.hidden = false; }
    nameBox.focus(); return;
  }
  // 원본이 바뀐 칸·이미 보류한 칸은 묶어서 유지하지 않는다.
  const boxes = [...button.closest('.item').querySelectorAll('.cell.GT_HOLDS')]
    .filter(b => !b.classList.contains('done') && !b.classList.contains('stale') && !b.classList.contains('held') && !b.classList.contains('alt'));
  button.disabled = true; button.dataset.busy = '1'; note.textContent = '기록하는 중…';
  let saved = 0;
  for (const box of boxes) {
    if (await send(box, 'CONFIRM', null, true)) { saved += 1; continue; }
    // 실패한 칸을 숨겨 두면 무엇이 안 됐는지 모른다. «AI가 GT와 같게 봄» 칸을 펼쳐 그 칸을 보여 준다.
    document.body.classList.add('show-holds'); document.getElementById('show-holds').checked = true;
    box.scrollIntoView({block: 'center'});
    break;
  }
  delete button.dataset.busy;
  note.textContent = saved === boxes.length ? `${saved}칸 GT 유지 기록됨 (${nameBox.value.trim()})`
    : `${boxes.length}칸 중 ${saved}칸만 기록했습니다 — 펼쳐진 칸의 안내를 봐 주세요.`;
  button.disabled = saved === boxes.length;
  progress();
}));

document.querySelectorAll('.shots img').forEach(img => img.addEventListener('click', () => {
  const view = document.getElementById('viewer');
  view.querySelector('img').src = img.src;
  view.querySelector('p').textContent = img.alt;
  view.hidden = false;
}));
document.getElementById('viewer').addEventListener('click', e => { e.currentTarget.hidden = true; });
document.querySelectorAll('a.pic').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  const img = [...link.closest('.item').querySelectorAll('.shots img')].find(i => (i.alt || '').startsWith(link.dataset.view + ' '));
  if (!img) return;
  const view = document.getElementById('viewer');
  view.querySelector('img').src = img.src;
  view.querySelector('p').textContent = img.alt;
  view.hidden = false;
}));
document.getElementById('show-holds').addEventListener('change', e => document.body.classList.toggle('show-holds', e.target.checked));

// 만든 시각은 사람의 시계로 보인다.
(() => { const made = document.getElementById('made'); const at = made && made.dataset.at;
  if (at) made.textContent = '만든 시각 ' + new Date(at).toLocaleString('ko-KR'); })();
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
        document.querySelectorAll('.cell button, button.keep-all').forEach(b => b.disabled = false);
      }
      const now = answer.current || {};
      document.querySelectorAll('.cell').forEach(box => {
        const cell = box.dataset.key + '\u0000' + box.dataset.field;
        const entry = latest[cell];
        const current = box.dataset.current === '' ? null : box.dataset.current;
        // 사람이 고른 값이 원본에 들어갔다(«넣어줘» 뒤). 바뀐 것이 아니라 반영된 것이다.
        if (entry && CHANGES.includes(entry.decision) && cell in now && now[cell] === entry.after && now[cell] !== current) {
          box.classList.add('done');
          box.querySelectorAll('button').forEach(b => b.disabled = true);
          box.querySelector('.result').innerHTML = `<b>반영됨</b> — 원본 GT가 ${esc(labelOf(box.dataset.field, entry.after))}(으)로 바뀌었습니다 (${esc(entry.reviewer)}).`;
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
        document.querySelectorAll('.cell button, button.keep-all').forEach(b => b.disabled = true);
      }
    } catch (e) {
      // 지난 답을 모르는 채로 누르게 두면, 이미 답한 칸을 다시 누르고 진행률도 0으로 읽힌다. 버튼을 막는다.
      document.body.dataset.locked = document.body.dataset.locked || 'offline';
      const notice = document.getElementById('offline');
      notice.textContent = '서버에 닿지 못해 지난 답을 불러오지 못했습니다 — 잠시 뒤 새로고침하고, 그래도 안 되면 Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요.';
      notice.hidden = false;
      document.querySelectorAll('.cell button, button.keep-all').forEach(b => b.disabled = true);
      document.getElementById('progress').textContent = '지난 답을 불러오지 못해 진행률을 알 수 없습니다.';
      return;
    }
  }
  progress();
}
load();
// 다른 탭에서 돌아오면 다시 읽는다 — 그사이 «다음 거»로 새 배치가 생겼으면 이 탭을 막는다.
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
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
.keep-all{font:inherit;font-size:13px;padding:5px 12px;border:1px solid var(--ink);background:transparent;color:var(--ink);border-radius:2px;cursor:pointer}
.keep-all:disabled{border-color:var(--rule);color:var(--faint)}

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
.diff.proposed .now .v{color:var(--muted);font-weight:400;text-decoration:line-through;text-decoration-color:var(--accent);text-decoration-thickness:1.5px}
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
.keep-row{margin-top:4px;padding-top:18px;border-top:1px solid var(--rule);font-size:13px}

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

@media (min-width:900px){
  .item-body{grid-template-columns:minmax(240px,30%) minmax(0,1fr);gap:40px;align-items:start}
  .evidence{position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto;padding-right:4px}
  .evidence .shots{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;overflow:visible}
  .evidence .shots.one{grid-template-columns:1fr}
  .evidence .shots img{height:auto;width:100%}
}
@media (max-width:640px){.grounds dl{grid-template-columns:1fr;gap:2px 0}.grounds dd{margin-bottom:8px}.statusline{flex-direction:column}
  .wrap{width:calc(100% - 32px)}.shots img{height:130px}.diff .v{font-size:1rem}.act.notes input.reason{min-width:100%}}
"""


def _label(field: dict[str, Any], value: Any) -> str:
    if value is None or value == "":
        return "(빈칸)"
    names = field.get("labelNames") or {}
    parts = str(value).split(MANY_SEPARATOR) if field.get("cardinality") == "many" else [str(value)]
    return " + ".join(f"{names[part]} ({part})" if part in names else part for part in parts)


def _cell_html(item: dict[str, Any], cell: dict[str, Any], field: dict[str, Any], signals: dict[str, Any],
               clear_ok: bool = True, batch: str | None = None) -> str:
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
    options = "".join(
        f'<option value="{e(label)}"{" selected" if proposal and label in str(proposal).split(MANY_SEPARATOR) else ""}>'
        f'{e(_label(field, label))}</option>' for label in labels
    )
    buttons = []
    # 판독자가 «정의에 없는 경계»라고 짚은 칸은 한쪽을 칠하지 않는다 — 사람이 경계를 정할 자리다.
    # 사람이 확정한 GT(TRUSTED)를 뒤집자는 제안도 칠하지 않는다 — 계약이 «뒤집으려면 기준이 높다»고 한 값을 칠한 버튼 한 번에 덮지 않게.
    contested = (cell["status"] == "CONTESTED" or bool(reading.get("definitionGap"))
                 or (cell.get("authority") == "TRUSTED" and cell["status"] == "FIX_PROPOSED"))
    if proposal and cell["status"] in ("FIX_PROPOSED", "FILL_PROPOSED", "CONTESTED"):
        # 두 눈이 갈린 칸은 한쪽을 칠해 유도하지 않는다. 판독 값과 GT 유지를 같은 무게로 둔다.
        label = f"판독 값으로 → {_label(field, proposal)}" if contested else f"제안대로 → {_label(field, proposal)}"
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
        buttons.append(f'<button data-decision="CORRECT" data-value="{e(low_value)}">판독 값으로 → {e(_label(field, low_value))} (확신 낮음)</button>')
    in_range = current is not None and all(part in labels for part in str(current).split(MANY_SEPARATOR))
    if current is not None and in_range:
        buttons.append('<button data-decision="CONFIRM">지금 GT가 맞다</button>')
    if unknown and unknown != current and unknown != proposal:
        also = " (AI도 이 값을 냈습니다 · 확신 낮음)" if low_value == unknown else ""
        buttons.append(f'<button data-decision="CORRECT" data-value="{e(unknown)}">가를 수 없음 → {e(_label(field, unknown))}{also}</button>')
    if current is None:
        buttons.append('<button data-decision="LEAVE_EMPTY">빈칸이 맞다</button>')
    elif clear_ok:
        buttons.append('<button data-decision="CLEAR">비워야 한다</button>')
    if many:
        # 값 여럿은 체크 상자로 — 여럿 고르기 목록(select multiple)은 그냥 누르면 다른 선택이 조용히 지워진다.
        chosen = set(str(proposal or current or "").split(MANY_SEPARATOR))
        picker = '<span class="picks">' + "".join(
            f'<label><input type="checkbox" class="pick" value="{e(label)}"{" checked" if label in chosen else ""}> '
            f'{e(_label(field, label))}</label>' for label in labels) + '</span>'
    else:
        picker = f'<select aria-label="다른 값으로 고치기"><option value="">다른 값…</option>{options}</select>'

    decided = cell.get("decision")
    names = {"CORRECT": "고침", "CONFIRM": "유지", "LEAVE_EMPTY": "빈칸 유지", "CLEAR": "비움", "HOLD": "보류"}
    previous = (f'<p class="why">지난번: <b>{e(decided.get("reviewer") or "")}</b> · {e(names.get(decided.get("decision"), ""))}'
                f' · {e((decided.get("decidedAt") or "")[:10])}{" — " + e(decided["reason"]) if decided.get("reason") else ""}</p>'
                if decided else "")
    proposal_json = json.dumps({"status": cell["status"], "proposal": proposal,
                                "reader": reading.get("value"), "defense": rebuttal.get("verdict")}, ensure_ascii=False)
    # 근거 사진 이름은 그 사진을 여는 고리로 — 휴대폰에서 칸과 사진 사이를 몇 화면씩 오르내리지 않게.
    def pics(ids: list[Any]) -> str:
        return " ".join(f'<a href="#" class="pic" data-view="{e(str(i))}">{e(str(i))}</a>' for i in ids or [])

    evidence = pics(reading.get("evidenceImageIds"))
    defense_ids = pics(rebuttal.get("evidenceImageIds"))
    verdict_name = {"READER_RIGHT": "판독이 맞다", "GT_STANDS": "GT가 맞다", "CANT_TELL": "못 가른다"}
    # 반론 AI를 부르지 않은 까닭을 칸마다 말한다 — 이유가 없으면 사람은 AI가 실패한 줄 알고 «다시 봐줘»를 되풀이한다.
    not_called = {"GT_HOLDS": "(판독이 GT와 같아 부르지 않음)", "NEEDS_HUMAN_LOOK": "(판독이 확신하지 못해 부르지 않음)",
                  "NOT_READ": "(판독이 없어 부르지 않음)", "NO_EVIDENCE": "(증거가 없어 부르지 않음)"}
    defense_text = verdict_name.get(rebuttal.get("verdict"), not_called.get(cell["status"], "(답이 돌아오지 않음)"))
    actionable = "1" if cell["status"] in ACTIONABLE else "0"
    # 제안 칸은 «지금 GT → AI 제안»이 곧 설명이다 — 같은 말을 한 줄 더 쓰지 않는다. 특별한 안내(대체 정답·반론 없음 같은)만 남긴다.
    hint = cell.get("hint") or status["hint"]
    hint_html = ("" if cell["status"] in ("FIX_PROPOSED", "FILL_PROPOSED", "GT_HOLDS") and hint == status["hint"]
                 else f'<p class="why">{e(hint)}</p>')
    # 근거 구역 — 결정할 것(위)과 읽을 것(아래)을 무게로 가른다. 값은 진하게, 근거 문장은 회색.
    if reading:
        low = " (확신 낮음)" if reading.get("confidence") == "LOW" else ""
        reader_html = (f'<b>{e(_label(field, reading.get("value")))}</b>{low}'
                       + (f' — [{evidence}] {e(reading.get("observation") or "")}' if reading.get("observation") else ""))
    else:
        reader_html = "(판독 없음)"
    defense_html = (f'<b>{e(defense_text)}</b>' + (f' — [{defense_ids}] {e(rebuttal.get("why") or "")}' if rebuttal else ""))
    contradicted = "GT_SELF_CONTRADICTION" in (cell.get("signals") or [])
    # status·화면 load()와 같은 규칙. 보류는 칠하지 않는다(화면이 load()에서 따로 칠한다).
    settled = decided and decided.get("decision") != "HOLD" and answered_on_page(decided, current, batch, contradicted)
    done = " done" if settled else ""
    return f"""
<div class="cell {e(cell['status'])}{done}{" alt" if cell.get("alternative") else ""}{" nodefense" if cell.get("noDefense") else ""}" data-key="{e(item['key'])}" data-field="{e(cell['field'])}" data-current="{e(current or '')}" data-actionable="{actionable}" data-contradicted="{"1" if contradicted else ""}" data-proposal="{e(proposal_json)}">
  <h4>{e(field.get('name') or cell['field'])}</h4>
  {hint_html}{contradiction}
  {f'<p class="why"><b>AI가 짚은 점</b> — 정의 문서가 이 경우를 다루지 않는다고 봤습니다. 고르기 <b>전에</b> 아래 «정의에 없는 경계»에 표시해 주세요.</p>' if reading.get("definitionGap") else ''}
  {f'<p class="why">지금 GT는 <b>{e(AUTHORITY_WORDS.get(cell.get("authority"), ""))}</b>.</p>' if cell.get("current") is not None and AUTHORITY_WORDS.get(cell.get("authority")) else ''}
  {previous}
  {_diff_html(field, cell, reading)}
  <div class="act notes">
    <input class="reason" placeholder="이유 (선택) — 누르기 전에 적으면 다음 사람이 읽습니다">
    <label><input type="checkbox" class="gap"> 정의에 없는 경계</label>
  </div>
  <div class="act">
    {''.join(buttons)}
    {picker}
    <button data-decision="CORRECT" data-pick="1">이 값으로 고치기</button>
    <button data-decision="HOLD">보류</button>
  </div>
  <p class="result"></p>
  <div class="grounds">
    <p class="grounds-kicker">판단 근거 · 두 AI</p>
    <dl>
      <dt>증거만 본 AI</dt>
      <dd>{reader_html}</dd>
      <dt>GT 편 반론</dt>
      <dd>{defense_html}</dd>
    </dl>
    {f'<details class="def"><summary>이 칸의 기준 보기</summary><div class="deftext">{e(field["definition"])}</div></details>' if field.get("definition") else ''}
    <details class="meta"><summary>기록용 정보</summary>왜 올라왔나 {e(reasons)} · GT 출처 {e(cell.get('currentSource') or '없음')}{f" · 지난 판정 {e(cell['previousDecision'])}" if cell.get('previousDecision') else ''}</details>
  </div>
</div>"""


def _diff_html(field: dict[str, Any], cell: dict[str, Any], reading: dict[str, Any]) -> str:
    """칸 머리의 «지금 GT → 제안». 사람이 가장 먼저 가를 것은 «무엇을 무엇으로 바꾸자는가»다 — 네 값을 같은 무게로 늘어놓으면
    어느 것이 지금 GT이고 어느 것이 제안인지 읽어 내야 한다. 제안이 없는 칸은 오른쪽에 그 까닭을 적는다."""
    e = html.escape
    status = cell["status"]
    current = cell.get("current")
    proposal = cell.get("proposal")
    now = f'<div class="now"><span class="tag">지금 GT</span><span class="v">{e(_label(field, current))}</span></div>'
    if status in ("FIX_PROPOSED", "FILL_PROPOSED") and proposal is not None:
        kind, tag, value = "proposed", "AI 제안", _label(field, proposal)
    elif status == "CONTESTED" and proposal is not None:
        kind, tag, value = "contested", "판독 AI 값 · 의견 갈림", _label(field, proposal)
    elif status == "NEEDS_HUMAN_LOOK" and reading.get("value"):
        kind, tag, value = "weak", "판독 AI 값 · 확신 낮음", _label(field, reading.get("value"))
    elif status == "GT_HOLDS":
        return (f'<div class="diff same">{now}<div class="arrow" aria-hidden="true">=</div>'
                '<div class="to"><span class="tag">AI 판독</span><span class="v">같은 값</span></div></div>')
    else:
        reason = {"NOT_READ": "AI가 아직 못 읽음", "NO_EVIDENCE": "증거 없음 — 제안 없음"}.get(status, "제안 없음")
        return (f'<div class="diff none">{now}<div class="arrow" aria-hidden="true">→</div>'
                f'<div class="to"><span class="tag">제안</span><span class="v">{e(reason)}</span></div></div>')
    kind += " fill" if current is None else ""
    return (f'<div class="diff {kind}">{now}<div class="arrow" aria-hidden="true">→</div>'
            f'<div class="to"><span class="tag">{e(tag)}</span><span class="v">{e(value)}</span></div></div>')


def output_path(root: Path, project_relative: str) -> Path:
    """사진 경로는 프로젝트 루트 기준으로 적혀 있다. 화면은 gt-review 폴더에 있다."""
    return PROJECT_ROOT / project_relative


def render_html(profile: dict[str, Any], review: dict[str, Any], root: Path) -> str:
    e = html.escape
    unit = (profile.get("gtTask") or {}).get("unit")
    task = profile.get("gtTask") or {}
    upstream = (task.get("gt") or {}).get("upstream")
    clear_ok = not upstream or bool(upstream.get("acceptsEmpty"))
    # 무엇을 누르면 무엇이 되는가 — 버튼은 판정을 기록할 뿐이다. GT가 바뀌는 것은 «반영해줘» 뒤다.
    title_note = (f"칸마다 답하면 판정이 기록됩니다 — «반영해줘» 뒤에 {upstream.get('note') or upstream.get('kind')}에 붙여 넣을 목록이 만들어집니다"
                  if upstream else "칸마다 답하면 판정이 기록됩니다 — GT에는 «반영해줘» 뒤에 들어갑니다")
    fields = {field["id"]: field for field in review["fields"]}
    # 사람도 판독자와 **같은 정의**를 본다 — AI 문장이 «정의의 기준»을 인용할 때 화면에서 되짚을 수 있게. 정의는 GT 값이 아니라
    # 눈가림과 무관하다.
    try:
        texts = definition_texts(resolve(profile, {"path": task["definitions"], "root": task.get("definitionsRoot") or "project"}))
    except (OSError, KeyError):
        texts = {}
    for field_id, field in fields.items():
        if texts.get(field_id):
            field["definition"] = texts[field_id]
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
    for item in review["items"]:
        role_names = ((profile.get("gtTask") or {}).get("images") or {}).get("roleNames") or {}

        def caption(image: dict[str, Any]) -> str:
            # AI 문장은 P01처럼 부른다. 그 이름과 사진 종류(images.roleNames의 이름)만 보이고, 원래 이름(D04T02 같은 내부 코드)은
            # 마우스를 올리면 보인다 — 운영팀이 내부 코드를 사진 번호로 착각해 이유 칸에 적지 않게.
            rule = (image.get("tileRule") or {}).get("version")
            kind = role_names.get(str(image.get("role")))
            return (f'<b title="원래 이름 {e(image["imageId"])}">{e(image.get("viewId") or "")}</b>'
                    + (f' · {e(kind)}' if kind else "")
                    + (f'<span class="meta-rule" title="잘린 규칙 {e(rule)}"></span>' if rule else ""))
        shots = "".join(
            f'<figure><img loading="lazy" src="{e(os.path.relpath(output_path(root, image["path"]), root))}" '
            f'alt="{e(image.get("viewId") or "")} · {e(image["imageId"])} · {e(item["key"])}"><figcaption>{caption(image)}</figcaption></figure>'
            for image in item["images"]
        ) or ("" if item.get("text") else '<p class="why">사진도 글도 없는 건입니다.</p>')
        # 열 이름 대신 사람 이름(gtTask.columnNames)으로 — 운영팀은 notice 같은 영문 열 이름을 모른다.
        column_names = (profile.get("gtTask") or {}).get("columnNames") or {}
        text = "".join(f'<div class="textbox"><b>{e(str(column_names.get(name, name)))}</b>\n{e(str(value))}</div>'
                       for name, value in (item.get("text") or {}).items())
        if item.get("context"):
            text += ('<p class="why">AI에게 함께 준 맥락 — '
                     + " · ".join(f"{e(str(column_names.get(name, name)))}: {e(str(value))}" for name, value in item["context"].items())
                     + "</p>")
        gaps = ""
        if item.get("missing"):
            gaps += f'<p class="why">못 받은 사진: {e(" · ".join(m["imageId"] for m in item["missing"]))}</p>'
        if item.get("omitted"):
            gaps += f'<p class="why">너무 많아 고르게 뽑고 뺀 사진: {e(" · ".join(item["omitted"]))}</p>'
        cells = "".join(_cell_html(item, cell, fields.get(cell["field"]) or {}, signals, clear_ok,
                                   review.get("batchId")) for cell in item["cells"])
        holding = [cell for cell in item["cells"] if cell["status"] == "GT_HOLDS" and not cell.get("alternative")]
        # 무엇을 유지하는지 보여 준 뒤에만 누르게 한다 — 보지 않은 값을 «사람이 확인했다»고 남기지 않는다.
        listing = " · ".join(f'{e(fields.get(cell["field"], {}).get("name") or cell["field"])} = '
                             f'{e(_label(fields.get(cell["field"], {}), cell.get("current")))}' for cell in holding)
        keep_all = (f'<p class="keep-row"><button class="keep-all">이 값들을 GT로 유지 ({len(holding)}칸)</button>'
                    ' <b class="keep-note" style="font-size:13px"></b><br>'
                    f'<span class="why keep-guide">지금 GT: <b>{listing}</b> — 정답을 모르는 AI가 증거만 보고 같은 값을 냈습니다. '
                    '증거(사진·글)와 맞으면 누르세요. AI가 왜 같게 봤는지는 맨 위 «AI가 GT와 같게 본 칸도 펼치기»를 켜면 칸마다 보입니다. '
                    '사람의 «유지» 판정으로 남아 다시 나오지 않습니다.</span></p>'
                    if holding else "")
        title = " · ".join(part for part in (item.get("title"), item.get("group")) if part)
        subtitle = f' <small style="color:var(--muted);font-weight:400">{e(title)}</small>' if title else ""
        memo = f'<p class="why">AI 메모 — {e(item["note"])}</p>' if item.get("note") else ""
        blocks.append(
            f'<section class="item" id="{e(item["id"])}"><p class="kicker">{e(item["id"])}</p>'
            f'<h3 style="font-size:1.2rem;margin-top:4px">{e(item["key"])}{subtitle}</h3>'
            f'<div class="item-body"><aside class="evidence"><p class="side-kicker">증거 — AI가 본 그대로</p>'
            f'<div class="shots{" one" if len(item["images"]) == 1 else ""}">{shots}</div>{text}{gaps}{memo}</aside>'
            f'<div class="decide"><div class="gt-cell-grid">{cells}</div>{keep_all}</div></div></section>'
        )
    if not blocks:
        blocks.append('<p style="margin-top:30px">이번에 볼 칸이 없습니다. 남은 후보가 없거나 모두 답했습니다.</p>')
    excluded = review.get("excluded") or []
    sources = review.get("sources") or {}
    warn = ""
    if (review.get("warnings") or {}).get("notKorean"):
        warn = (f'<p class="notice">AI 설명 일부가 한국어가 아닙니다({e(", ".join(review["warnings"]["notKorean"]))}). '
                '판정에는 문제가 없지만, 읽기 어려우면 Claude에게 «이 건 한국어로 다시 봐줘»라고 말해 주세요.</p>')
    body = f"""<div class="wrap">
<header class="masthead">
  <div class="masthead-top"><p class="kicker">GT 개선 · {e(str(profile.get('subjectName') or ''))}{f" · {e(str(unit))} 단위" if unit else ""}</p><p id="made" data-at="{e(str(review['basedOn']['worklist'] or ''))}"></p></div>
  <h1>{e(str(profile.get('displayName') or review['profileId']))}</h1>
  <p class="lead">{e(title_note)} AI 둘이 따로 봤습니다 — 하나는 GT를 모른 채 증거만, 하나는 GT 편에서. 버튼을 누른 것만 사람의 판정으로 남습니다.</p>
  <p id="offline" class="notice" hidden>이 파일을 직접 열었습니다. 판정을 기록하려면 Claude에게 «GT 화면 열어줘»라고 말해 주세요.</p>
  {warn}
  <div class="bar"><label>판정하는 사람 <input id="reviewer" placeholder="이름"></label>
    <label><input type="checkbox" id="show-holds"> AI가 GT와 같게 본 칸도 펼치기{f' <span class="hidden-count">({counts["GT_HOLDS"]}칸 숨김)</span>' if counts["GT_HOLDS"] else ''}</label></div>
  <p id="name-hint" class="notice" hidden></p>
  <div class="statusline">
    <div class="tally" title="처음 그린 수 — 남은 칸은 오른쪽이 셉니다">{tally}</div>
    <div id="progress-short"></div>
  </div>
  <p id="progress"></p>
</header>
{''.join(blocks)}
<p id="progress-bottom" aria-live="polite"></p>
<footer style="margin:60px 0 40px;font-size:13px">
  <p>다 고르셨으면 Claude에게 <b>«반영해줘»</b>, 더 보려면 <b>«다음 거»</b>라고 말해 주세요.
  {f'이번 화면 뒤에 {len(excluded)}건이 더 기다립니다.' if excluded else '남은 후보가 없습니다.'}</p>
  <details class="meta"><summary>기록용 정보</summary>배치 {e(str(review.get('batchId') or ''))}<br>GT {e(str(sources.get('gt') or ''))}<br>정의 {e(str(sources.get('definitions') or ''))}</details>
</footer>
<div id="viewer" hidden><img alt=""><p></p></div>
</div>
<script id="gt-review-data" type="application/json">{json.dumps({"profileId": review["profileId"], "batchId": review.get("batchId"),
    "labelNames": {fid: spec.get("labelNames") or {} for fid, spec in fields.items()}}, ensure_ascii=False)}</script>
<script>{SCRIPT}</script>
</body></html>
"""
    page = head(f"GT 개선 · {profile.get('displayName') or review['profileId']}")
    page = page.replace("</head>", '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap" '
                        'rel="stylesheet">\n</head>', 1)
    return page.replace("</style>", EXTRA_STYLE + "</style>", 1) + body


def render(profile: dict[str, Any], worklist: dict[str, Any], sweep: dict[str, Any] | None,
           ledger: list[dict[str, Any]], root: Path) -> list[Path]:
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
            return [review_path, html_path, manifest]
    return []  # 새 배치가 준비됐다 — 아무것도 쓰지 않았다
