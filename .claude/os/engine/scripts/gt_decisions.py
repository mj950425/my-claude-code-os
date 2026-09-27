#!/usr/bin/env python3
"""GT 개선 과제의 사람 판정 원장. (키, 필드) 한 칸에 사람이 무엇이라 답했는가.

## 지키는 선

1. **사람이 명시적으로 고른 것만 적는다.** 에이전트의 제안은 이 원장에 들어오지 않는다.
   제안을 그대로 적으면 「사람이 확인한 GT」라는 말이 거짓이 된다. 제안은 판정의
   `basedOn`에 **참고로** 붙을 뿐이다 — 사람이 무엇을 보고 답했는지 되짚기 위해서다.
2. **유지도 결정이다.** `CONFIRM`은 라벨을 바꾸지 않지만, 남아야 다음에 같은 칸을 다시 묻지 않는다.
   `LEAVE_EMPTY`도 결정이다 — «사진으로도 사람도 못 가른다, 빈칸이 정답이다».
3. **사람이 본 값에만 답한다.** 화면이 보여 준 GT 값(`expectedBefore`)이 지금 GT와 다르면 거절한다.
   화면이 그려진 뒤 원본이 바뀌었으면 사람은 다른 값을 보고 답한 것이다.
4. **덮어쓰지 않는다.** 같은 칸에 다시 답하면 새 줄이 옛 줄을 `supersedes`로 가리킨다.
5. **어느 경계 위의 판정인가를 남긴다.** 이 과제에는 판례가 없고, 경계는 정의 문서의 그 필드 절이다.
   그래서 `basis`는 늘 `definitions#<필드ID>`이고, 사람이 판례처럼 따로 댈 근거는 `reason`에 적는다.
6. **원장은 run 폴더에 두지 않는다.** `runs/`는 지워도 되는 산출물이지만 사람의 답은 아니다.
7. **동시에 눌러도 잃지 않는다.** 서버는 여러 요청을 함께 받는다. 읽고-붙이고-쓰는 동안 잠근다.

파생물은 원장에서 언제든 다시 만든다 — `corrections.jsonl`(무엇을 고쳤나)·`confirmations.jsonl`·
`gt.corrected.jsonl`(원본 순서 그대로, 고친 뒤의 GT 전체). 정정 줄의 모양은 외부 하네스의 사용자 정정
원장(`{id, field, before, after, previousSource, source, reason}`)과 같다. 그대로 넘겨 쓰게 하려는 것이다.
원본 GT에 넣는 일(`apply`)은 따로이고, 사람이 확인한 뒤에만 한다.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import stat
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from catalog_profile import PROJECT_ROOT
from gt_task import (
    MANY_SEPARATOR,
    TaskError,
    correction_prefix,
    check_one_ledger,
    clear_allowed,
    effective_row,
    export_value,
    field_map,
    gt_source,
    gt_value,
    in_range,
    is_many,
    key_fields,
    key_parts,
    load_gt_rows,
    load_task,
    normalize,
    raw_differs,
    resolve,
    violations,
)

LEDGER_SCHEMA = "gt-field-decisions-v1"
DECISIONS = {
    "CORRECT": "GT를 이 값으로 고친다",
    "CONFIRM": "지금 GT가 맞다",
    "LEAVE_EMPTY": "빈칸이 맞다 — 증거로도 가를 수 없다 (GT가 빈 칸에만)",
    "CLEAR": "값이 있지만 비워야 한다 — 가를 수 없는 칸에 값이 들어가 있다",
    "HOLD": "지금은 못 가른다 — 다음에 다시 묻는다",
}
# 다음 준비에서 이 칸을 다시 묻지 않는 결정. 보류는 여기 없다 — 보류는 답이 아니다.
SETTLED = ("CORRECT", "CONFIRM", "LEAVE_EMPTY", "CLEAR")
# 원본 GT의 값을 바꾸는 결정. 내보낼 때 정정(corrections)이 된다.
CHANGES = ("CORRECT", "CLEAR")


# 이 신호가 붙은 칸은 지난 답이 더 답이 아니다 — 그 화면(배치)에서 누른 답만 센다. 모순은 둘 중 하나가 틀렸다는 뜻이고,
# 정책 변경은 사람이 고른 값이 허용값에서 빠졌다는 뜻이다.
REASK_SIGNALS = frozenset({"GT_SELF_CONTRADICTION", "POLICY_CHANGED_SINCE_DECISION"})


def reasked(cell: dict[str, Any]) -> bool:
    return bool(REASK_SIGNALS & set(cell.get("signals") or []))


def answered_on_page(entry: dict[str, Any] | None, current: str | None, batch: str | None,
                     contradicted: bool = False) -> bool:
    """이 화면에서 답한 칸인가 — 화면의 load()·서버가 그린 done과 status가 같은 규칙을 쓴다.

    - 고침·유지·빈칸·비움: 사람이 본 값(before)이나 고른 값(after, 원본에 넣은 뒤)이 지금 GT이면 답이다.
    - 보류: 이 배치에서 누른 것만. 지난 배치의 보류는 다시 물어야 하는 칸이다.
    - 모순이 남은 칸: 어떤 답이든(유지·고침·비움) 이 배치에서 누른 것만. 지난 답은 모순을 풀지 못해 칸이 다시
      올라온 것이다 — 그것을 답으로 세면 같은 건이 매 배치 맨 앞에 오는데 화면은 «다 했습니다»라고 말한다.
    """
    if not entry:
        return False
    if entry.get("decision") == "HOLD":
        # 보류는 값을 정하지 않았다(after 없음). 사람이 본 값 그대로일 때만 답이다 — 원본이 비면 «답함»이 아니다.
        return entry.get("batchId") == batch and current == entry.get("before")
    if contradicted and entry.get("decision") in SETTLED:
        return entry.get("batchId") == batch and current in (entry.get("before"), entry.get("after"))
    return entry.get("decision") in SETTLED and current in (entry.get("before"), entry.get("after"))


_THREAD_LOCK = threading.Lock()


class DecisionRejected(ValueError):
    """사람이 읽을 거절 사유."""


def definition_gaps(profile: dict[str, Any], latest: dict[tuple[str, str], dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """«정의에 없는 경계»로 짚은 판정이 쌓인 필드 — 정의 문서에 절을 더할 자리다. 사람에게 읽어 줄 이름을 함께 낸다.
    보류는 판정이 아니라 따로 센다 — 확신이 없어 보류만 했는데 «기준을 넓혀 달라»가 가면 안 된다."""
    names = {spec["id"]: spec.get("name") or spec["id"] for spec in (profile.get("gtTask") or {}).get("fields") or []}
    gaps: dict[str, dict[str, Any]] = {}
    for entry in latest.values():
        if not str(entry.get("basis") or "").endswith(":gap"):
            continue
        row = gaps.setdefault(entry["field"], {"name": names.get(entry["field"], entry["field"]), "decided": 0, "held": 0})
        row["held" if entry.get("decision") == "HOLD" else "decided"] += 1
    return gaps


def relative(path: Path) -> str:
    """프로젝트 기준 경로. 밖(외부 레포)이면 «../»로 — 추적되는 산출물에 이 컴퓨터의 절대 경로를 남기지 않는다."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return os.path.relpath(path.resolve(), PROJECT_ROOT)


def gt_dir(profile: dict[str, Any]) -> Path:
    # 테스트가 진짜 원장 자리를 건드리지 않도록 뿌리만 바꿀 수 있다. 운영에서는 쓰지 않는다.
    base = Path(os.environ["CATALOG_OS_GT_ROOT"]) if os.environ.get("CATALOG_OS_GT_ROOT") else PROJECT_ROOT / ".claude" / "gt"
    return base / str(profile["id"]) / "gt-review"


def ledger_path(profile: dict[str, Any]) -> Path:
    return gt_dir(profile) / "decisions.json"


@contextmanager
def locked(profile: dict[str, Any]) -> Iterator[None]:
    """원장 하나에 한 번에 한 쓰기. 스레드(서버)와 프로세스(터미널) 둘 다 막는다."""
    folder = gt_dir(profile)
    folder.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, open(folder / "decisions.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_ledger(profile: dict[str, Any]) -> list[dict[str, Any]]:
    path = ledger_path(profile)
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != LEDGER_SCHEMA:
        raise DecisionRejected(f"{path}: {LEDGER_SCHEMA}가 아닙니다.")
    return list(value.get("decisions") or [])


def _write_atomic(path: Path, text: str) -> None:
    """임시 파일에 쓰고 바꿔 끼운다. 원본의 권한을 잇고, 심볼릭 링크면 가리키는 파일을 쓴다 —
    바꿔 끼우기만 하면 외부 레포의 GT가 0600이 되거나 링크가 평범한 파일로 바뀐다."""
    path = path.resolve() if path.is_symlink() else path
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(handle.name, mode)
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


def current_answers(profile: dict[str, Any], task: dict[str, Any] | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """칸마다 마지막 판정 — before·after를 지금 규칙(legacy)으로 읽은 것. 비교·셈은 이것으로 한다(원장 파일은 그대로)."""
    from gt_task import read_through_legacy

    return read_through_legacy(task or load_task(profile), effective(read_ledger(profile)))


def effective(decisions: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """칸마다 마지막 판정. 원장은 시간순으로만 자란다."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in decisions:
        latest[(str(entry["key"]), str(entry["field"]))] = entry
    return latest


def _current_batch(profile: dict[str, Any]) -> str | None:
    """화면 자리(`runs/<id>/gt-review/manifest.json`)가 말하는 지금 배치. 없으면 None."""
    path = export_dir(profile).parent / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("batchId")
    except (OSError, json.JSONDecodeError) as error:
        # 읽지 못하면 확인하지 못한 것이다 — 열린 채 통과하지 않는다.
        raise DecisionRejected("화면 정보를 읽지 못했습니다 — Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요.") from error


def _codes(spec: dict[str, Any], value: Any) -> Any:
    """사람 이름(labelNames의 값)을 허용값 코드로. 값 여럿은 조각마다. 이미 코드인 조각은 옮기지 않는다 — 이름이 다른 코드와
    글자가 같을 때(로더가 막지만) 화면 버튼의 코드가 다른 코드로 바뀌지 않게. 이름도 코드도 아니면 그대로 둔다."""
    if value is None:
        return None
    names = {str(name): str(code) for code, name in (spec.get("labelNames") or {}).items()}
    if not names:
        return value
    codes = {str(label) for label in spec.get("labels") or []}
    # 말로 받은 이름은 띄어쓰기·가운뎃점이 빠지기 쉽다(«판단불가»). 그것들을 뺀 모양이 이름 하나에만 맞을 때만 옮긴다.
    squash = lambda text: re.sub(r"[\s·]", "", str(text))  # noqa: E731
    loose: dict[str, list[str]] = {}
    for name, code in names.items():
        loose.setdefault(squash(name), []).append(code)

    def one(part: Any) -> Any:
        text = str(part)
        if text in codes:
            return part
        if text in names:
            return names[text]
        matches = loose.get(squash(text)) or []
        return matches[0] if len(matches) == 1 else part

    if isinstance(value, list):
        return [one(part) for part in value]
    return MANY_SEPARATOR.join(str(one(part.strip())) for part in str(value).split(MANY_SEPARATOR))


# 판정이 들어온 길. 규칙 5는 말로 받은 판정에 누가·무엇을 보고 답했는지를 요구한다 — 나중에 그 줄을 가릴 수 있어야 한다.
CHANNELS = ("screen", "screen-bulk", "spoken")


def record(
    profile: dict[str, Any],
    key: str,
    field: str,
    decision: str,
    reviewer: str,
    value: str | list[str] | None = None,
    reason: str = "",
    proposal: dict[str, Any] | None = None,
    *,
    expected_before: str | None,
    channel: str,
    batch: str | None = None,
    gap: bool = False,
) -> dict[str, Any]:
    """판정 한 줄을 검사하고 원장에 붙인다. 화면의 버튼과 터미널이 이 함수 하나를 지난다.

    `expected_before`(사람이 본 지금 GT 값, 빈칸이면 None)는 기본값 없이 반드시 받는다 — 새 문이 이 값을
    빠뜨리면 옛 화면에서 누른 답이 바뀐 GT 위에 조용히 올라간다. 지키는 것은 부르는 쪽의 기억이 아니라 이 함수다.
    `channel`(판정이 들어온 길 — CHANNELS)도 같다. 말로 받은 판정인지 화면에서 누른 것인지는 원장만 보고 가를 수 있어야 한다."""
    task = load_task(profile)
    fields = field_map(task)
    decision = (decision or "").strip().upper()
    reviewer = (reviewer or "").strip()
    if decision not in DECISIONS:
        raise DecisionRejected(f"판정은 {' · '.join(DECISIONS)} 중 하나여야 합니다.")
    if channel not in CHANNELS:
        raise DecisionRejected(f"판정이 들어온 길(channel)은 {' · '.join(CHANNELS)} 중 하나여야 합니다.")
    if not reviewer:
        raise DecisionRejected("누가 판정했는지 이름을 적어 주세요. 이름 없는 판정은 사람의 판정인지 알 수 없습니다.")
    # 말로 받은 판정은 사람 이름(«대표 색», «빨강»)으로 올 수 있다. 코드로 옮긴 뒤 비교한다 —
    # 옮기지 않으면 «빨강»이 RED와 달라 «그사이 원본이 바뀌었습니다»라는 거짓 문장이 나간다.
    # ID가 먼저다 — 이름이 다른 필드의 ID와 같아도(로더가 막지만) 버튼이 보낸 ID가 다른 필드로 가지 않게.
    if field not in fields:
        field = next((spec["id"] for spec in fields.values() if field == spec.get("name")), field)
    if field not in fields:
        raise DecisionRejected(f"이 과제에 없는 필드입니다: {field}")
    spec = fields[field]
    value = _codes(spec, value)
    seen_raw = expected_before
    expected_before = _codes(spec, expected_before)
    try:
        normalize(spec, value, lenient=False) if value is not None else None
    except TaskError as error:
        # 형이 틀린 값은 사람의 입력 문제다 — «설정 문제»로 돌리지 않고 입력으로 거절한다.
        raise DecisionRejected(f"{spec.get('name') or field}의 허용값이 아닙니다: {value!r}") from error
    # 고치기가 아닌 판정에 값이 붙어 오면 조용히 버리지 않는다 — «지금 GT가 맞다»에 다른 값을 붙인 말은 사람이 한 판정이 아니다.
    if decision != "CORRECT" and value not in (None, "", []):
        same = normalize(spec, value, lenient=True) == normalize(spec, expected_before, lenient=True) if expected_before is not None else False
        if decision == "HOLD" or decision in ("LEAVE_EMPTY", "CLEAR") or not same:
            raise DecisionRejected(f"{decision} 판정에는 값을 붙이지 않습니다 — 값을 바꾸려면 CORRECT로 답해 주세요: {value!r}")
    if decision == "CLEAR" and not clear_allowed(task):
        raise DecisionRejected("이 GT의 원본은 빈칸을 받지 못합니다(비우면 이전 값으로 돌아갑니다). "
                               "맞는 값이 없으면 «가를 수 없음»을 뜻하는 값을 골라 주세요.")
    check_one_ledger(profile)
    with locked(profile):
        # 지금 배치는 잠금 안에서 본다 — prepare도 같은 잠금 안에서 새 배치를 알린다. 밖에서 보면 그 사이에 옛 배치로 적힌다.
        current_batch = _current_batch(profile)
        # 배치를 모르고 온 판정(작업 목록이 비어 있던 사이의 말로 받은 판정)은 지금 화면의 배치로 적는다. 화면도 없으면
        # 보류는 받지 않는다 — 어느 화면의 보류인지 모르면 영영 «이 화면의 답»이 되지 못한다.
        if batch is None:
            batch = current_batch
            if batch is None and decision == "HOLD":
                raise DecisionRejected("보류할 화면이 없습니다 — «GT 개선해줘»로 화면을 먼저 만들어 주세요.")
        if batch and current_batch and batch != current_batch:
            raise DecisionRejected("새 화면이 준비됐거나 준비 중입니다 — 이 화면은 지난 화면입니다. 새로고침해 주세요.")
        gt_rows = dict(load_gt_rows(profile, task))
        if key not in gt_rows:
            raise DecisionRejected(f"GT에 없는 키입니다: {key}")
        row = gt_rows[key]
        before = gt_value(task, spec, row)
        # 사람이 본 값은 날 값 그대로 맞춰 보고, 안 맞으면 이름 → 코드로 옮겨 다시 본다. 원본에 이름이 날 값으로
        # 들어 있는 칸(범위 밖)은 옮기면 오히려 달라지므로, 한쪽만 보면 영영 답할 수 없는 칸이 생긴다.
        if normalize(spec, seen_raw, lenient=True) != before and normalize(spec, expected_before, lenient=True) != before:
            raise DecisionRejected("화면이 보여 준 GT와 지금 GT가 다릅니다 — 그사이 원본이 바뀌었습니다. "
                                   "이 칸은 이 화면에선 답할 수 없고, «다음 거»에서 새 값으로 다시 나옵니다.")
        after: str | None
        if decision == "CORRECT":
            after = normalize(spec, value)
            if not in_range(spec, after):
                names = spec.get("labelNames") or {}
                labels = " · ".join(f"{names[str(label)]} ({label})" if str(label) in names else str(label)
                                    for label in spec["labels"])
                raise DecisionRejected(f"{spec.get('name') or field}의 허용값이 아닙니다: {value}. 허용값: {labels}")
            if after == before:
                raise DecisionRejected("지금 GT와 같은 값입니다. 맞다는 뜻이면 «지금 GT가 맞다»를 눌러 주세요.")
        elif decision == "CONFIRM":
            if before is None:
                raise DecisionRejected("GT가 비어 있어 유지할 값이 없습니다. 값을 골라 고치거나 «빈칸이 맞다»를 눌러 주세요.")
            if not in_range(spec, before):
                raise DecisionRejected(f"지금 GT({before})가 허용값 밖이라 유지할 수 없습니다. 맞는 값을 골라 고쳐 주세요.")
            after = before
        elif decision == "LEAVE_EMPTY":
            if before is not None:
                raise DecisionRejected("GT에 값이 있습니다. 그 값을 지우려면 «비워야 한다»를 눌러 주세요.")
            after = None
        elif decision == "CLEAR":
            if before is None:
                raise DecisionRejected("이미 빈칸입니다. 빈칸이 맞다는 뜻이면 «빈칸이 맞다»를 눌러 주세요.")
            after = None
        else:
            after = None
        decisions = read_ledger(profile)
        latest = effective(decisions)
        previous = latest.get((key, field))
        # 이 판정을 얹은 줄이 선언된 제약을 어기면 알린다. 막지는 않는다 — 두 칸을 한 번에 고칠 수 없으니
        # 한 칸씩 고치는 중간에는 모순이 잠깐 생긴다. 모순이 남으면 그 칸들은 다음 준비에서 다시 올라온다.
        mine = {f: e for (k, f), e in latest.items() if k == key}
        # 이 판정이 같은 칸의 옛 판정을 대신한다 — 유지·보류여도 옛 정정을 얹은 채 따지면 안 된다.
        mine[field] = {"decision": decision, "before": before, "after": after}
        # 한 제약이 칸마다 한 번씩 걸려도 문장은 한 번 — 같은 문장이 두 번 보이면 사람은 둘이 다른 경고인 줄 안다.
        warnings = list(dict.fromkeys(item["text"] or item["constraint"]
                                      for item in violations(task, effective_row(task, row, mine))))
        number = max((int(str(item["decisionId"]).split("-")[-1]) for item in decisions), default=0) + 1
        entry = {
            "decisionId": f"GTD-{number:05d}",
            "key": key,
            "field": field,
            "decision": decision,
            "before": before,
            "after": after,
            "previousSource": gt_source(task, spec, row) if before is not None else None,
            # 경계는 정의 문서의 그 필드 절이다. 사람이 «정의에 없는 경계»라고 짚으면 :gap을 붙인다 —
            # 그런 판정이 쌓인 필드가 정의에 절을 더할 자리다.
            "basis": f"definitions#{field}" + (":gap" if gap else ""),
            # 이 판정을 한 화면(배치). 보류는 그 화면에서만 답이다 — 다음 배치에서는 다시 물어야 한다.
            "batchId": batch,
            "reviewer": reviewer,
            # 들어온 길 — 칸 버튼(screen) · 건의 «이 값들을 GT로 유지»(screen-bulk) · Claude가 말을 받아 적음(spoken).
            "channel": channel,
            "reason": (reason or "").strip(),
            "decidedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "supersedes": previous["decisionId"] if previous else None,
            # 사람이 본 제안. 판정의 근거가 아니라 되짚기용 기록이다.
            "basedOn": proposal or None,
            "constraintWarnings": warnings,
        }
        decisions.append(entry)
        _write_atomic(ledger_path(profile), json.dumps(
            {"schemaVersion": LEDGER_SCHEMA, "profileId": profile["id"], "decisions": decisions},
            ensure_ascii=False, indent=2) + "\n")
    return entry


def export_dir(profile: dict[str, Any]) -> Path:
    """고친 GT 사본의 자리. 정답이 아니라 파생물이라 지워도 되는 run 폴더에 둔다."""
    from catalog_profile import output_root

    return output_root(profile) / "gt-review" / "export"


def _build(profile: dict[str, Any], task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """원장에서 정정·확인·고친 GT를 만든다. 잠금 안에서만 부른다. (요약, 고친 줄들, 원본 해시)"""
    import hashlib

    fields = field_map(task)
    source = resolve(profile, task["gt"])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = load_gt_rows(profile, task)
    latest = current_answers(profile, task)
    by_key = dict(rows)
    keys = key_fields(task["keyField"])
    # 정정 출처 이름의 앞머리. 과제가 밝히지 않으면 과제 ID로 만든다 — 상류 하네스의 이름 규약을 엔진이 알지 않게.
    source_prefix = correction_prefix(task)
    sources_field = task["gt"].get("fieldSourcesField")
    row_source_field = task["gt"].get("sourceField") or "source"
    corrections: list[dict[str, Any]] = []
    confirmations: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    patches: dict[str, dict[str, Any]] = {}
    new_columns: set[str] = set()
    upstream_spec = task["gt"].get("upstream") or {}
    mirrors = [str(name) for name in upstream_spec.get("mirrorFields") or []]
    # 상류(검수 시트 같은)에서 이 줄을 찾는 열 — 시트 이름·칸 같은. 붙여 넣을 목록에 함께 싣는다.
    locators = [str(name) for name in upstream_spec.get("locatorFields") or []]
    orphaned: list[dict[str, Any]] = []
    # 사람이 고른(또는 유지한) 값이 지금 정책의 허용값에 없다 — 정책에서 값을 뺐다. 넣지 않고 다시 묻는다.
    out_of_policy: list[dict[str, Any]] = []
    for (key, field), entry in sorted(latest.items()):
        if entry["decision"] not in SETTLED:
            continue
        if key not in by_key or field not in fields:
            # 원장에는 있는데 GT에서 그 줄이나 필드가 사라졌다. 조용히 빼면 «무엇을 고쳤나»가 원장과 어긋난다.
            orphaned.append({"key": key, "field": field, "decisionId": entry["decisionId"],
                             "missing": "key" if key not in by_key else "field"})
            continue
        spec = fields[field]
        row = by_key[key]
        current = gt_value(task, spec, row)
        column = spec.get("gtField") or field
        chosen = entry["after"] if entry["decision"] in CHANGES else entry["before"]
        if chosen is not None and not in_range(spec, chosen):
            out_of_policy.append({"key": key, "field": field, "decisionId": entry["decisionId"], "value": chosen})
            continue
        # 이미 원본에 들어간 정정. 목록에는 «반영됨»으로 남긴다(무엇을 고쳤나는 원장에서 늘 다시 만들 수 있어야 한다).
        # 원본에 다시 얹지는 않는다.
        if entry["decision"] in CHANGES and current == entry["after"] and current != entry["before"]:
            # 들어간 정정도 운영 하네스의 사용자 정정 원장과 같은 모양이다 — source까지.
            corrections.append({"id": key, "keyFields": key_parts(key, keys, row), "field": field, "column": column,
                                # 고치기 전 값도 지금 열의 모양으로 — 한 줄에 목록과 «A|B» 글자가 섞이지 않게.
                                "before": export_value(spec, entry["before"], row.get(column)),
                                "after": export_value(spec, entry["after"], row.get(column)),
                                "previousSource": entry["previousSource"],
                                "source": f"{source_prefix}_{entry['decision']}_{entry['decidedAt'][:10].replace('-', '')}",
                                "reason": entry["reason"],
                                "decisionId": entry["decisionId"], "reviewer": entry["reviewer"], "applied": True})
            continue
        # GT가 판정 뒤에 바뀌었으면 옛 판정을 새 GT에 덮지 않는다. 사람이 본 값이 아니기 때문이다.
        if current != entry["before"]:
            stale.append({"key": key, "field": field, "decisionId": entry["decisionId"],
                          "sawBefore": entry["before"], "nowInGt": current})
            continue
        source = f"{source_prefix}_{entry['decision']}_{entry['decidedAt'][:10].replace('-', '')}"
        common = {"id": key, "keyFields": key_parts(key, keys, row), "field": field, "column": column,
                  "previousSource": entry["previousSource"], "source": source, "decisionId": entry["decisionId"],
                  "reviewer": entry["reviewer"]}
        patch = patches.setdefault(key, {})
        if entry["decision"] in CHANGES:
            # before는 원본 칸의 날 값 그대로(순서·형 포함) — 정렬하거나 옮긴 값이 아니다.
            corrections.append({**common, "before": row.get(column), "after": export_value(spec, entry["after"], row.get(column)),
                                "reason": entry["reason"], "applied": False,
                                "locator": {name: row.get(name) for name in locators},
                                # 상류 칸(거울 열)의 지금 값. 합친 GT(before)와 다를 수 있다 — 상류 칸이 비어 옛 GT에서 온 값이면.
                                "mirror": {name: row.get(name) for name in mirrors if name in row}})
            patch[column] = export_value(spec, entry["after"], row.get(column))
            # 같은 값을 다른 이름으로 비추는 열(검수 시트의 최종 열 같은)도 함께 고친다. 한쪽만 바뀌면 두 열이 다른 답을 말한다.
            for mirror in mirrors:
                if mirror in row:
                    patch[mirror] = export_value(spec, entry["after"], row.get(mirror))
            # 새 값이 대체 정답 목록에 있으면 뺀다 — 정답이 곧 대체 정답일 수는 없다. 비우면(CLEAR) 대체 정답도 비운다 —
            # 정답이 없는 칸에 «같이 맞는 값»만 남으면 다음 비교가 그 값을 GT처럼 다룬다.
            if spec.get("alternativesField") and isinstance(row.get(spec["alternativesField"]), list):
                patch[spec["alternativesField"]] = [] if entry["after"] is None else [
                    item for item in row[spec["alternativesField"]] if normalize(spec, item) != entry["after"]]
        else:
            confirmations.append({**common, "value": export_value(spec, entry["before"], row.get(column)), "decision": entry["decision"]})
            # 옛 이름으로 적힌 값을 «맞다»고 확인했으면 지금 이름으로 다시 쓴다. 옛 이름에 사람 확인 표시만 붙으면
            # 그 옛 이름이 사람이 확정한 값처럼 남는다.
            if raw_differs(spec, row):
                patch[column] = export_value(spec, entry["before"], row.get(column))
        if sources_field:
            if sources_field not in row:
                new_columns.add(sources_field)
            patch.setdefault(sources_field, {})[field] = source
        else:
            # 칸별 출처 열이 없는 과제는 필드가 하나다(로더가 보장). 줄 출처를 사람 판정으로 바꾼다 —
            # 원본에 없던 열을 만들면 원본을 읽는 쪽이 모르는 열이 생긴다.
            patch[row_source_field] = source

    corrected: list[dict[str, Any]] = []
    for key, row in rows:
        fixed = json.loads(json.dumps(row))
        for column, value in (patches.get(key) or {}).items():
            if column == sources_field:
                existing = fixed.get(sources_field) if isinstance(fixed.get(sources_field), dict) else {}
                fixed[sources_field] = {**existing, **value}
            else:
                fixed[column] = value
        corrected.append(fixed)
    if len(corrected) != len(rows):
        raise DecisionRejected("고친 GT의 줄 수가 원본과 다릅니다. 내보내지 않습니다.")
    # 고친 뒤에도 남는 모순. 막지 않고 알린다 — 다음 준비에서 그 칸들이 다시 올라온다.
    remaining = []
    for fixed in corrected:
        found = violations(task, fixed)
        if found:
            remaining.append({"key": key_of_row(task, fixed), "constraints": sorted({f["constraint"] for f in found})})
    decisions = read_ledger(profile)
    gaps = definition_gaps(profile, latest)
    summary = {
        "lastDecisionId": decisions[-1]["decisionId"] if decisions else None,
        "orphaned": orphaned,
        "outOfPolicy": out_of_policy,
        "definitionGaps": gaps,
        "corrections": corrections,
        "confirmations": confirmations,
        "stale": stale,
        "newColumns": sorted(new_columns),
        "constraintViolationsAfter": remaining,
        "rows": {"source": len(rows), "corrected": len(corrected)},
    }
    return summary, corrected, digest


def _patched_lines(source: Path, rows: list[tuple[str, dict[str, Any]]], corrected: list[dict[str, Any]]) -> str:
    """원본 줄 텍스트를 보존하고 바뀐 줄만 다시 쓴다. 빈 줄과 줄 끝(CRLF 같은)도 원본 그대로 둔다 —
    외부 레포의 diff에 사람이 고친 줄만 보여야 한다. 구분자 모양은 원본 첫 줄을 따른다."""
    # «\n»으로만 나눈다(줄 끝은 그대로 붙여 둔다) — splitlines는 문자열 안의 U+2028 같은 글자에서도 끊는다.
    text = source.read_bytes().decode("utf-8")
    lines = [part + "\n" for part in text.split("\n")[:-1]] + ([text.split("\n")[-1]] if text.split("\n")[-1] else [])
    content = [index for index, line in enumerate(lines) if line.strip()]
    if len(content) != len(rows) or len(corrected) != len(rows):
        raise DecisionRejected("원본 GT의 줄 수가 읽은 줄 수와 다릅니다. 넣지 않았습니다.")
    first = lines[content[0]] if content else ""
    compact = first and ", " not in first and '": ' not in first
    separators = (",", ":") if compact else (", ", ": ")
    for index, (_, row), fixed in zip(content, rows, corrected):
        if fixed == row:
            continue
        text = lines[index]
        ending = text[len(text.rstrip("\r\n")):]
        lines[index] = json.dumps(fixed, ensure_ascii=False, separators=separators) + ending
    return "".join(lines)


def changed_lines(rows: list[tuple[str, dict[str, Any]]], corrected: list[dict[str, Any]]) -> int:
    """원본에 넣으면 실제로 바뀌는 줄 수. 0이면 넣을 것이 없다 — 누적 판정 수로 «넣을까요?»를 묻지 않는다."""
    return sum(1 for (_, row), fixed in zip(rows, corrected) if fixed != row)


# 상류 목록의 머리글. 사람이 Excel로 열어 옮겨 붙이는 파일이라 스킬이 부르는 말과 같은 낱말을 쓴다.
UPSTREAM_HEADERS = ("줄 종류", "고칠 열", "옮길 칸의 지금 값", "지금 GT", "새 값", "새 값 이름", "판정한 사람", "이유")


def row_check(task: dict[str, Any], locator: dict[str, Any] | None) -> str | None:
    """위치 열끼리 맞는가. `upstream.rowCheck: {"row": <행 번호 열>, "cell": <A1 주소 열>}`을 선언한 상류만 본다.

    - 행 번호가 다른 탭의 번호면(`sheet`: 탭 열, `rowSheet`: 행 번호가 속한 탭) 행 번호만 믿지 않는다 — «other-tab».
      주소는 그 줄의 탭을 가리키므로 남긴다.
    - 같은 탭인데 주소(AQ56)의 숫자가 행 번호(14)와 다르면 어느 쪽이 맞는지 모른다 — «clash». 틀린 쪽을 고치면 다른 상품의
      줄을 덮으므로 둘 다 적지 않는다."""
    check = (task["gt"].get("upstream") or {}).get("rowCheck") or {}
    locator = locator or {}
    row, address = locator.get(check.get("row")), locator.get(check.get("cell"))
    if not check or row in (None, ""):
        return None
    if check.get("sheet") and check.get("rowSheet") and str(locator.get(check["sheet"]) or "") != str(check["rowSheet"]):
        return "other-tab"
    digits = re.search(r"([0-9]+)\s*\Z", str(address or ""))
    if digits and str(row).strip().lstrip("0") != digits.group(1).lstrip("0"):
        return "clash"
    return None


def locator_conflict(task: dict[str, Any], locator: dict[str, Any] | None) -> bool:
    return row_check(task, locator) == "clash"


def _upstream_patch(task: dict[str, Any], built: dict[str, Any], fresh: set[str] | None = None,
                    reverts: list[dict[str, Any]] | None = None) -> str:
    """원본의 상류(검수 시트 같은)에 붙여 넣을 모양. 키·위치 열 · 고칠 열 · 상류 칸의 지금 값 · 지금 GT · 새 값.

    - 키·위치 열의 머리글은 `upstream.headerNames`(열 이름 → 사람 이름)가 있으면 그것, 없으면 열 이름이다.
    - 이번 목록의 모든 줄에서 비어 있는 위치 열은 뺀다 — 빈 열은 찾을 자리를 가리키지 않는다.
    - «옮길 칸의 지금 값»은 거울 열(mirrorFields)의 값이다(상류 시트의 그 칸). 합친 GT가 상류 칸이 비어 옛 GT에서 왔으면 둘이 다르다 —
      하나만 적으면 사람이 시트에서 다른 값을 보고 행을 잘못 찾았다고 여긴다. 거울 열이 없으면 «(모름)».
    BOM을 붙인다 — 없으면 Excel이 한국어 머리글과 이유를 깨뜨린다."""
    import csv
    import io

    keys = key_fields(task["keyField"])
    upstream = task["gt"].get("upstream") or {}
    rows = [item for item in built["corrections"] if not item.get("applied")]
    reverts = reverts or []
    fresh = fresh or set()
    # 위치 열은 되돌림 줄까지 보고 고른다 — 되돌림만 있는 목록에서 위치 열이 빠지면 되돌릴 줄을 못 찾는다.
    locators = [str(name) for name in upstream.get("locatorFields") or []
                if any((item.get("locator") or {}).get(name) not in (None, "") for item in [*rows, *reverts])]
    headers = upstream.get("headerNames") or {}
    # 상류에서 고칠 열의 이름(검수 시트의 열 이름 같은). 선언이 없으면 파일의 열 이름.
    columns = upstream.get("columns") or {}
    names = {spec["id"]: spec.get("labelNames") or {} for spec in task["fields"]}

    def safe(text: Any) -> Any:
        # Excel은 =·+·-·@로 시작하는 칸을 수식으로 읽는다 — 자유 글이 수식이 되지 않게 앞에 '를 붙인다.
        return f"'{text}" if isinstance(text, str) and text[:1] in ("=", "+", "-", "@") else text

    def cell(value: Any) -> Any:
        if value in (None, "", []):
            return "(빈칸)"
        return safe(json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value)

    def named(field: str, value: Any) -> str:
        parts = value if isinstance(value, list) else ([] if value in (None, "") else [value])
        return " · ".join(names.get(field, {}).get(str(part), str(part)) for part in parts) or "(빈칸)"

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    # 판정한 사람·이유 열은 상류 시트의 칸 이름으로(새로 고침이 그 칸을 읽으면 옮겨야 사람·근거가 남는다).
    fixed = [headers.get("reviewer", name) if name == "판정한 사람" else headers.get("reason", name) if name == "이유" else name
             for name in UPSTREAM_HEADERS]
    writer.writerow([*(headers.get(name, name) for name in [*keys, *locators]), *fixed])
    check = upstream.get("rowCheck") or {}

    def place(item: dict[str, Any]) -> list[Any]:
        # 위치 열이 서로 다른 행을 가리키면 둘 다 적지 않는다 — 사람이 한쪽을 골라 다른 상품의 줄을 고치게 두지 않는다.
        locator = item.get("locator") or {}
        verdict = row_check(task, locator)
        blank = {"clash": {check.get("row"), check.get("cell")}, "other-tab": {check.get("row")}}.get(verdict or "", set())
        return [("" if name in blank else locator.get(name)) for name in locators]

    def kind(item: dict[str, Any], word: str) -> str:
        return f"{word} · 위치 불일치 — 키로 찾으세요" if locator_conflict(task, item.get("locator")) else word
    # 되돌림 줄을 맨 앞에, 그다음 가장 최근에 처음 건넨 줄(«새로»), 그다음 이미 건넨 줄 — 사람이 새 줄과 옛 줄을 가를 수 있게.
    for item in reverts:
        # 거둔 정정 — 건넬 때 상류 칸에 있던 값으로 되돌린다. 비어 있었으면 칸을 비운다(합친 GT 값을 적지 않는다 —
        # 비어 있던 «최종 검수» 칸에 옛 GT를 적으면 사람이 확정한 값처럼 읽힌다).
        restore = item.get("restore")
        restore_text = "(빈칸) — 칸을 비워 주세요" if restore in (None, "", []) else cell(restore)
        writer.writerow([*((item.get("keyFields") or {}).get(key) for key in keys),
                         *place(item),
                         kind(item, "되돌림"), columns.get(item["field"], item["column"]), cell(item.get("upstreamNow")), "",
                         restore_text, named(item["field"], restore), safe(item.get("reviewer") or ""),
                         f"건넸던 정정({cell(item.get('withdrawnAfter'))})을 거뒀습니다"])
    rows = sorted(rows, key=lambda item: item["decisionId"] not in fresh)
    for item in rows:
        mirror = item.get("mirror") or {}
        upstream_now = cell(next(iter(mirror.values()))) if mirror else "(모름)"
        writer.writerow([*(item["keyFields"].get(key) for key in keys), *place(item),
                         kind(item, "새로" if item["decisionId"] in fresh else "바꾸기"),
                         columns.get(item["field"], item["column"]), upstream_now, cell(item["before"]), cell(item["after"]),
                         named(item["field"], item["after"]), safe(item["reviewer"]), safe(item["reason"])])
    return "\ufeff" + buffer.getvalue()


HANDOUT_LOG = "handed-out.jsonl"


def _handout(profile: dict[str, Any], task: dict[str, Any], folder: Path,
             pending: list[dict[str, Any]], persist: bool = True) -> tuple[set[str], list[dict[str, Any]]]:
    """상류 목록에 실어 건넨 줄의 기록(`handed-out.jsonl`, 덧붙이기만 한다)을 늘리고, 이번 목록의 «새로»와 «되돌림»을 정한다.

    - 기록은 **상태**다 — 무엇을 이미 건넸는지는 원장에서 다시 만들 수 없다. 지우면 «새로»·«되돌림»을 잃는다.
    - «새로»: 가장 최근에 처음 건넨 줄. 새 판정 없이 다시 내보내면 같은 줄이 «새로»다(목록이 늘 때만 앞으로 간다).
    - «되돌림»: 건넨 정정을 사람이 그 뒤 거둔 칸(마지막 판정이 유지·빈칸 유지·보류). 새 정정이 그 칸을 대신하면 되돌림이 아니다
      (새 줄이 덮는다). 상류가 거둔 값을 이미 품었으면(거울 열·GT가 건넨 새 값) 되돌릴 때까지 계속 싣는다. 품지 않았으면
      처음 실은 목록(과 그 뒤 새 판정 없이 다시 낸 목록)에만 싣는다 — 옮기지 않은 줄을 영영 되돌리라고 하지 않게.
      되돌릴 값은 건넬 때의 **상류 칸 값**(거울 열)이다. 비어 있었으면 «칸을 비워 주세요».
    """
    log = folder / HANDOUT_LOG
    handed = read_jsonl_file(log)
    known = {(row["kind"], row["decisionId"]) for row in handed}
    # 목록 번호 — 시각은 같은 초에 두 번 내보내면 겹치므로, 건넬 때마다 하나씩 느는 번호로 «가장 최근»을 가른다.
    listing = max((int(row.get("listing") or 0) for row in handed), default=0) + 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = [{"kind": "correct", "decisionId": item["decisionId"], "key": item["id"], "field": item["field"],
              "column": item["column"], "keyFields": item.get("keyFields"), "locator": item.get("locator"),
              "mirror": item.get("mirror") or {}, "before": item["before"], "after": item["after"], "listedAt": now,
              "listing": listing}
             for item in pending if ("correct", item["decisionId"]) not in known]
    # 되돌림 후보 — 칸마다 마지막으로 건넨 정정.
    latest = current_answers(profile, task)
    rows = dict(load_gt_rows(profile, task))
    fields = field_map(task)
    last_listed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*handed, *added]:
        if row["kind"] == "correct":
            last_listed[(row["key"], row["field"])] = row
    by_id = {entry["decisionId"]: entry for entry in read_ledger(profile)}
    candidates = []
    for cell, listed in last_listed.items():
        entry = latest.get(cell)
        if not entry or entry["decisionId"] == listed["decisionId"] or entry["decision"] in CHANGES:
            continue
        # 거둔 것은 **정정 전 값**에 대한 답일 때만이다. 건넨 새 값에 «맞다»고 한 것(상류가 품은 뒤의 유지)은 거둔 것이 아니라
        # 받아들인 것이고, 그 밖의 값(상류가 다른 값으로 옮겨 갔다)에 대한 답도 이 정정과 상관없다.
        correction = by_id.get(listed["decisionId"]) or {}
        if entry.get("before") != correction.get("before"):
            continue
        row, spec = rows.get(cell[0]), fields.get(cell[1])
        if row is None or spec is None:
            continue
        mirrors = listed.get("mirror") or {}
        now_upstream = row.get(next(iter(mirrors))) if mirrors else row.get(listed["column"])
        absorbed = now_upstream == listed["after"]
        candidates.append((listed, entry, absorbed, now_upstream))
    # 되돌림은 **거둔 정정**마다 한 번 건넨다 — 보류를 다시 눌러 판정 번호가 새로 생겨도 같은 되돌림을 «새로» 내지 않는다.
    added += [{"kind": "revert", "decisionId": listed["decisionId"], "listedAt": now, "listing": listing}
              for listed, entry, absorbed, _ in candidates if ("revert", listed["decisionId"]) not in known]
    if added and persist:
        with log.open("a", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in added))
    # 미리 보기(persist=False)도 같은 목록을 셈한다 — 적지 않을 뿐이다.
    handed += added
    listed_at = {(row["kind"], row["decisionId"]): int(row.get("listing") or 0) for row in handed}
    shown_times = [listed_at[("correct", item["decisionId"])] for item in pending]
    shown_times += [listed_at[("revert", listed["decisionId"])] for listed, _, _, _ in candidates]
    newest = max(shown_times) if shown_times else None
    fresh = {item["decisionId"] for item in pending if listed_at[("correct", item["decisionId"])] == newest}
    reverts = []
    for listed, entry, absorbed, now_upstream in candidates:
        is_fresh = listed_at[("revert", listed["decisionId"])] == newest
        if not (absorbed or is_fresh):
            continue
        mirrors = listed.get("mirror") or {}
        original = next(iter(mirrors.values())) if mirrors else listed["before"]
        reverts.append({"key": listed["key"], "field": listed["field"], "column": listed["column"],
                        "keyFields": listed.get("keyFields"), "locator": listed.get("locator"),
                        "withdrawnAfter": listed["after"], "upstreamNow": now_upstream,
                        "restore": original, "absorbed": absorbed, "fresh": is_fresh,
                        "withdrawnBy": entry["decisionId"], "reviewer": entry.get("reviewer")})
    return fresh, reverts, bool(added)


def read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def key_of_row(task: dict[str, Any], row: dict[str, Any]) -> str:
    from gt_task import key_of

    return key_of(row, key_fields(task["keyField"])) or ""


def _jsonl(items: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)


def _this_batch_counts(profile: dict[str, Any]) -> dict[str, int]:
    latest = effective(read_ledger(profile))
    batches = [entry.get("batchId") for entry in latest.values() if entry.get("batchId")]
    last = max(batches, default=None)
    mine = [entry for entry in latest.values() if last and entry.get("batchId") == last]
    return {"corrections": sum(1 for entry in mine if entry["decision"] in CHANGES),
            "confirmations": sum(1 for entry in mine if entry["decision"] in ("CONFIRM", "LEAVE_EMPTY"))}


def export(profile: dict[str, Any], record_handout: bool = True) -> dict[str, Any]:
    """원장에서 정정 파일과 고친 GT 사본을 다시 만든다. 원본 GT는 건드리지 않는다.
    `record_handout=False`는 미리 보기다 — 무엇이 바뀌는지만 세고 **아무 파일도 쓰지 않는다**. 건넨 기록(handed-out.jsonl)은
    물론, 사람이 본 목록의 표지(export.json)도 — 표지가 옮겨지면 «넣어줘»가 사람이 보지 않은 판정까지 넣는다."""
    task = load_task(profile)
    check_one_ledger(profile)
    with locked(profile):
        built, corrected, digest = _build(profile, task)
        folder = gt_dir(profile)
        copies = export_dir(profile)
        write = record_handout
        if write:
            copies.mkdir(parents=True, exist_ok=True)
            _write_atomic(folder / "corrections.jsonl", _jsonl(built["corrections"]))
            _write_atomic(folder / "confirmations.jsonl", _jsonl(built["confirmations"]))
            _write_atomic(copies / "gt.corrected.jsonl", _jsonl(corrected))
        upstream = task["gt"].get("upstream")
        pending = [item for item in built["corrections"] if not item.get("applied")]
        fresh, reverts, grew = _handout(profile, task, folder, pending, record_handout) if upstream else (set(), [], False)
        if upstream and write:
            _write_atomic(copies / "upstream-patch.csv", _upstream_patch(task, built, fresh, reverts))
        # 상류가 있는 GT는 파일에 넣지 않고 목록을 붙여 넣는다 — 그때 «넣을 것»은 목록의 줄(아직 안 들어간 정정 + 되돌림)이다.
        # 줄 출처만 바뀌는 유지 확인은 상류로 가지 않으므로 세지 않는다.
        to_change = len(pending) + len(reverts) if upstream else changed_lines(load_gt_rows(profile, task), corrected)
        summary = {
            "schemaVersion": "gt-review-export-v4",
            "profileId": profile["id"],
            "gtSource": relative(resolve(profile, task["gt"])),
            "sourceDigest": digest,
            "lastDecisionId": built["lastDecisionId"],
            "upstream": upstream or None,
            "rows": built["rows"],
            "corrections": len(pending),
            "alreadyApplied": len(built["corrections"]) - len(pending),
            "confirmations": len(built["confirmations"]),
            # 마지막 배치(화면)에서 남긴 판정만 — 위 두 수는 원장 누적이라 «이번 화면에서 한 일»을 말하지 못한다.
            "thisBatch": _this_batch_counts(profile),
            # 원본에 넣으면 실제로 바뀌는 줄 수(상류가 있으면 붙여 넣을 줄 수). 위 두 수는 원장 누적이라, 이미 넣은 뒤에도 0이 아니다.
            "linesToChange": to_change,
            "withdrawn": reverts,
            # 상류가 있으면 «유지» 확인은 시트로 가지 않는다 — 사람이 확인한 사실은 이 저장소의 원장에만 남는다.
            "confirmationsNotUpstream": len(built["confirmations"]) if upstream else 0,
            # 상류 목록만의 수. «새로» 표시는 가장 최근 목록의 줄이라 다시 내보내도 같지만, «지난 목록과 같은가»는 이번
            # 내보내기가 목록에 줄을 더했는지로 가른다 — 같으면 스킬이 «이미 건넨 목록입니다 — 새로 고침만 부탁»으로 간다.
            "newSinceLastList": (sum(1 for item in pending if item["decisionId"] in fresh)
                                 + sum(1 for row in reverts if row.get("fresh"))) if upstream else None,
            "sameAsLastList": bool(upstream) and not grew and bool(pending or reverts),
            # 위치 열이 서로 다른 행을 가리켜 목록에서 위치를 비운 줄 — 사람은 키로 찾는다.
            "locatorConflicts": sum(1 for item in [*pending, *reverts] if locator_conflict(task, item.get("locator"))) if upstream else 0,
            "stale": built["stale"],
            "orphaned": built["orphaned"],
            # 사람이 고른 값을 정책에서 뺐다 — 넣지 않았고, 다음 화면에 다시 나온다.
            "outOfPolicy": built["outOfPolicy"],
            "definitionGaps": built["definitionGaps"],
            "newColumns": built["newColumns"],
            "constraintViolationsAfter": built["constraintViolationsAfter"],
            "appliedToSource": False,
            "files": {"corrections.jsonl": relative(folder / "corrections.jsonl"),
                      "confirmations.jsonl": relative(folder / "confirmations.jsonl"),
                      "gt.corrected.jsonl": relative(copies / "gt.corrected.jsonl"),
                      **({"upstream-patch.csv": relative(copies / "upstream-patch.csv")} if upstream else {})},
        }
        if write:
            _write_atomic(folder / "export.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def apply(profile: dict[str, Any], confirm: bool) -> dict[str, Any]:
    """고친 GT를 원본 GT 파일에 넣는다. `confirm`이 아니면 무엇이 바뀌는지만 말하고 아무 파일도 쓰지 않는다(보여 준 목록이 아니다).

    `confirm`이면 **사람이 본 목록 그대로** 넣는다 — 마지막 `export`가 적어 둔 원본 해시와 마지막 판정 번호가
    지금과 같을 때만. 그사이 원본이 바뀌었거나 판정이 늘었으면 거절하고 다시 보여 주게 한다.
    원본에 상류(검수 시트 같은)가 선언돼 있으면 넣지 않는다 — 다음 새로 고침에서 상류가 이 파일을 덮는다.
    원본 줄 텍스트는 보존하고 바뀐 줄만 다시 쓰며, 옆에 사본을 남긴다. 이 함수를 부르는 쪽(스킬)은 반드시 먼저 묻는다.
    """
    import hashlib

    task = load_task(profile)
    # 목록을 보여 준 뒤 다른 프로필이 같은 GT를 가리키기 시작했을 수 있다 — 넣기 직전에도 본다.
    check_one_ledger(profile)
    source = resolve(profile, task["gt"])
    upstream = task["gt"].get("upstream")
    if not confirm:
        summary = export(profile, record_handout=False)
        return {"target": str(source), "corrections": summary["corrections"], "confirmations": summary["confirmations"],
                "linesToChange": summary["linesToChange"],
                "stale": summary["stale"], "newColumns": summary["newColumns"], "upstream": upstream or None,
                "constraintViolationsAfter": summary["constraintViolationsAfter"], "applied": False, "backup": None}
    if upstream:
        raise DecisionRejected(f"이 GT의 원본은 {upstream.get('note') or upstream['kind']}입니다. 파일에 넣으면 다음 새로 고침에서 "
                               "덮입니다 — 정정 목록(upstream-patch.csv)을 원본에 붙여 넣어 주세요.")
    seen_path = gt_dir(profile) / "export.json"
    if not seen_path.is_file():
        raise DecisionRejected("넣기 전에 정정 목록을 먼저 만들어 보여 주어야 합니다(«반영해줘»).")
    seen = json.loads(seen_path.read_text(encoding="utf-8"))
    with locked(profile):
        built, corrected, digest = _build(profile, task)
        if digest != seen.get("sourceDigest") or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise DecisionRejected("원본 GT가 목록을 보여 드린 뒤에 바뀌었습니다. 넣지 않았습니다 — 다시 반영해 주세요.")
        if built["lastDecisionId"] != seen.get("lastDecisionId"):
            raise DecisionRejected("목록을 보여 드린 뒤에 판정이 늘었습니다. 넣지 않았습니다 — 다시 반영해 주세요.")
        rows = load_gt_rows(profile, task)
        if not changed_lines(rows, corrected):
            # 넣을 것이 없으면 사본도 만들지 않는다 — 외부 레포에 빈 사본만 쌓인다.
            return {"target": str(source), "corrections": 0, "confirmations": len(built["confirmations"]),
                    "linesToChange": 0, "applied": False, "backup": None}
        text = _patched_lines(source, rows, corrected)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = source.with_name(f"{source.name}.before-gt-review-{stamp}")
        shutil.copy2(source, backup)
        _write_atomic(source, text)
        # 넣은 뒤의 원본으로 보여 준 목록의 표지를 새로 적는다 — 옛 해시가 남으면 다음 «넣어줘»가 «원본이 바뀌었습니다»라는
        # 거짓 문장으로 멈춘다(바꾼 것은 우리다).
        seen["sourceDigest"] = hashlib.sha256(source.read_bytes()).hexdigest()
        _write_atomic(seen_path, json.dumps(seen, ensure_ascii=False, indent=2) + "\n")
    pending = [item for item in built["corrections"] if not item.get("applied")]
    return {"target": str(source), "corrections": len(pending), "confirmations": len(built["confirmations"]),
            "linesToChange": changed_lines(rows, corrected), "applied": True, "backup": str(backup)}


def many_hint(profile: dict[str, Any], field: str) -> bool:
    return is_many(field_map(load_task(profile))[field])
