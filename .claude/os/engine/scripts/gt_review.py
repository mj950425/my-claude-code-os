#!/usr/bin/env python3
"""GT 개선 하네스의 단일 진입점. 속성을 모른다 — 과제는 프로필의 `gtTask`가 선언한다.

    python3 gt_review.py tasks                       # 고칠 수 있는 GT 과제 목록
    python3 gt_review.py prepare --task <id>         # 볼 칸을 고르고 증거를 내려 놓는다
    (워크플로우 gt-review.js)                         # 사진만 보는 판독, GT 편 반론
    python3 gt_review.py finish --task <id> --from <워크플로우 출력>   # 결과를 받아 화면을 만든다
    python3 gt_review.py render --task <id>          # 화면만 다시 만든다
    python3 gt_review.py record --task <id> ...      # 사람 판정 한 줄 (화면 버튼과 같은 함수)
    python3 gt_review.py export --task <id>          # 원장에서 정정 파일·고친 GT를 만든다
    python3 gt_review.py apply --task <id> [--yes]   # 고친 GT를 원본에 넣는다(사람 확인 뒤에만)
    python3 gt_review.py status --task <id>          # 어디까지 왔나

## 셋을 나눈 이유 — 고르기 · 판단 · 기록

- **고르기**는 스크립트다. 무엇이 몇 칸인지는 여기서만 센다. 에이전트가 센 숫자는 실행마다 다르다.
- **판단**은 워크플로우의 에이전트다. 한 눈은 GT를 모른 채 사진만 읽고(`gt-blind-reader`),
  다른 눈은 그 판독에 맞서 GT를 지킨다(`gt-defender`). 같은 눈이 제안하고 검토하면 검증이 아니다.
- **기록**은 사람이다. 화면의 버튼이 `gt_decisions.record()`를 부른다.

## 배치 — 한 번의 준비와 그 결과를 묶는 이름

`prepare`는 부를 때마다 `batchId`를 새로 만든다. 워크플로우는 그 값을 돌려주고, `finish`는 값이 다르면
거절한다. 건 이름(`GI-01`)은 준비할 때마다 다시 매겨지므로, 이름만으로는 지난 배치의 판독이 이번
배치의 다른 상품에 붙는 것을 막지 못한다 — 실제로 그렇게 붙는 것을 검토에서 재현했다.

출력은 전부 `runs/<id>/gt-review/`에 쓴다 — 원장만 예외로 GT 옆에 둔다(`gt_decisions.py` 참조).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from catalog_profile import PROJECT_ROOT, discover_profiles, load_profile, output_root, relative_or_absolute
from gt_decisions import (DECISIONS, SETTLED, DecisionRejected, answered_on_page, apply, current_answers, reasked, definition_gaps, effective, export,
                          locked, read_ledger, record)
from gt_next import gate, lock_dir
from gt_images import ImageIndex, prepare as prepare_evidence, require_pillow
from gt_review_render import call_name, change_moment, flow_steps, legacy_ask
from gt_task import (
    AGENTS,
    definition_texts,
    authority,
    definition_policy,
    next_rule_id,
    reader_policy,
    rule_line,
    gt_source,
    SIGNALS,
    TaskError,
    candidates,
    quiet_cells,
    check_one_ledger,
    field_map,
    group_items,
    MANY_SEPARATOR,
    gt_value,
    in_range,
    is_many,
    load_gt,
    load_task,
    resolve,
)

WORKLIST_SCHEMA = "gt-review-worklist-v2"
SWEEP_SCHEMA = "gt-review-sweep-v2"
# 한 번에 사람 앞에 놓는 건수. 에이전트 수는 «건 × 2»를 넘지 않는다(판독 + 반론).
DEFAULT_LIMIT = 8
HANGUL = re.compile(r"[가-힣]")


def all_profiles(strict: bool = False) -> list[dict[str, Any]]:
    """모든 프로필. `strict`면 읽지 못한 프로필이 있을 때 멈춘다 — 원장이 하나인지 확인할 때는 빠진 프로필이
    곧 확인하지 못한 프로필이다(조용히 건너뛰면 검사가 열린 채 통과한다)."""
    found = []
    for path in discover_profiles():
        try:
            found.append(load_profile(path))
        except (ValueError, OSError) as error:
            if strict:
                raise TaskError(f"프로필 {path}을 읽지 못해 «한 GT에 원장 하나»를 확인할 수 없습니다: {error}") from error
    return found


def broken_profiles() -> list[dict[str, str]]:
    """읽지 못한 프로필과 그 까닭. 조용히 빠지면 새 GT를 올린 사람은 «그런 과제가 없습니다»만 듣는다."""
    broken = []
    for path in discover_profiles():
        try:
            load_profile(path)
        except (ValueError, OSError) as error:
            broken.append({"path": relative_or_absolute(path), "id": path.parent.name, "reason": str(error)})
    return broken


def task_profiles() -> list[dict[str, Any]]:
    return [profile for profile in all_profiles() if isinstance(profile.get("gtTask"), dict)]


def check_one_ledger_per_gt(profile: dict[str, Any]) -> None:
    check_one_ledger(profile)


def find_profile(value: str) -> dict[str, Any]:
    """프로필 ID나 경로. 데이터 운영팀은 경로를 모른다 — ID만으로 찾는다."""
    path = Path(value)
    if path.suffix == ".json" and path.exists():
        return load_profile(path)
    for profile in task_profiles():
        if profile["id"] == value:
            return profile
    broken = next((row for row in broken_profiles() if row["id"] == value), None)
    if broken:
        raise TaskError(f"GT 과제 {value}의 프로필을 읽지 못했습니다 — 설정이 덜 됐습니다: {broken['reason']}")
    known = ", ".join(profile["id"] for profile in task_profiles()) or "(없음)"
    raise TaskError(f"그런 GT 과제가 없습니다: {value}. 있는 과제: {known}")


def review_root(profile: dict[str, Any]) -> Path:
    return output_root(profile) / "gt-review"


def portable(path: Path) -> str:
    """프로젝트 기준 경로, 밖이면 «../…» — 원장·워크플로우 인자·화면에 이 컴퓨터의 절대 경로를 남기지 않는다."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return os.path.relpath(path.resolve(), PROJECT_ROOT)


def write_json_atomic(path: Path, value: Any) -> None:
    """서버가 잠금 없이 읽는 파일(manifest) — 한 번에 바꿔 끼운다. 반쯤 쓴 파일을 읽으면 지금 배치를 모른다."""
    from gt_decisions import _write_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def field_digests(profile: dict[str, Any], task: dict[str, Any]) -> dict[str, str]:
    """칸마다 정의 문서의 그 절 지문. 판독자가 읽는 기준이 바뀌었는지 가른다 — 바뀌었으면 AI의 옛 동의는 이제 근거가 아니다."""
    definitions = resolve(profile, {"path": task["definitions"], "root": task.get("definitionsRoot") or "project"})
    return {field: hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] for field, text in definition_texts(definitions).items()}


def agreed_cells(root: Path, digests: dict[str, str] | None = None) -> set[tuple[str, str, str | None]]:
    """지난 판독에서 GT를 모르는 눈이 **그때의 GT와 같은 값**을 낸 칸. 사람 판정이 아니다(원장에 적지 않는다).

    `digests`를 주면 그 칸 절이 그때와 같은 동의만 센다 — 정책이 바뀐 뒤의 옛 동의는 버린다. 지문이 없는 옛 줄은 센다
    (지문을 적기 전의 동의다 — 버리면 이미 끝낸 칸이 한꺼번에 다시 올라온다)."""
    path = root / "agreed.jsonl"
    cells: set[tuple[str, str, str | None]] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if digests is not None and row.get("policy") and row["policy"] != digests.get(str(row["field"])):
                    continue
                cells.add((str(row["key"]), str(row["field"]), row.get("gtValue")))
    return cells


# AI의 동의로 끝나는 칸 — 이 신호 하나로만 뽑힌 칸이다(«사람 확인 전 라벨이니 한 번 보자»). GT를 모르는 눈이 같은 값을 냈으면
# 그 확인은 끝났다. 모순·허용값 밖·답한 뒤 바뀜 같은 신호가 함께 있으면 동의가 있어도 계속 올린다.
SETTLED_BY_AGREEMENT = frozenset({"GT_REFERENCE_ONLY"})


def settled_by_agreement(cell: dict[str, Any], agreed: set[tuple[str, str, str | None]]) -> bool:
    return (cell["key"], cell["field"], cell["current"]) in agreed and set(cell.get("signals") or []) <= SETTLED_BY_AGREEMENT


def blind_cells(root: Path) -> set[tuple[str, str, str | None]]:
    """화면에 올라갔던 «증거만 본 AI와 다름» 칸(`blind.jsonl`). 그 칸은 건이 다른 칸 때문에 뽑혔을 때만 함께 올라오므로,
    보류하거나 넘기면 고르기가 다시 볼 까닭이 없다 — 여기 적어 두면 다음 고르기가 그 칸을 후보로 올린다. 원장이 아니다."""
    path = root / "blind.jsonl"
    cells: set[tuple[str, str, str | None]] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                cells.add((str(row["key"]), str(row["field"]), row.get("gtValue")))
    return cells


def remember_blind(root: Path) -> None:
    """방금 그린 화면에 올라간 승격 칸을 적어 둔다 — 같은 GT 값일 때만 다시 올린다(값이 바뀌면 그 판독은 옛 값에 대한 것이다)."""
    review = read_json(root / "review.json") or {}
    known = blind_cells(root)
    lines = []
    for item in review.get("items") or []:
        for cell in item.get("cells") or []:
            if cell.get("signals") == ["BLIND_DISAGREES"] and (item["key"], cell["field"], cell.get("current")) not in known:
                known.add((item["key"], cell["field"], cell.get("current")))
                lines.append(json.dumps({"key": item["key"], "field": cell["field"], "gtValue": cell.get("current"),
                                         "proposal": cell.get("proposal"), "batchId": review.get("batchId")}, ensure_ascii=False))
    if lines:
        with (root / "blind.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def shown_cells(root: Path) -> dict[tuple[str, str, str | None], int]:
    """사람 앞에 놓였지만 답 없이 넘어간 칸 → 마지막으로 넘어간 차례(파일의 줄 순서, 1부터). 사람 판정이 아니다 —
    순서에만 쓴다(agreed.jsonl과 같다). 마지막으로 넘어간 때를 쓰는 까닭: 몇 번 넘겨도 «다음 거»가 가장 오래전에 본 건부터
    돌아가게(한 번씩만 세면 모두 한 번 넘긴 뒤에는 처음 순서로 돌아간다). 배치 이름이 아니라 줄 순서로 세는 까닭: 배치
    이름은 초 단위 시각 뒤에 무작위 꼬리가 붙어, 같은 초에 넘긴 두 배치는 순서가 뒤바뀔 수 있다. 파일은 덧붙이기만 한다."""
    path = root / "shown.jsonl"
    cells: dict[tuple[str, str, str | None], int] = {}
    if path.is_file():
        for turn, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                row = json.loads(line)
                cells[(str(row["key"]), str(row["field"]), row.get("gtValue"))] = turn
    return cells


def skipped_on_screen(profile: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """지금 화면에서 사람이 답하지 않은 칸 — 새 배치로 넘어가기 전에 적어 두면 다음 화면이 다른 건부터 보인다."""
    review = read_json(root / "review.json") or {}
    latest = current_answers(profile)
    batch = review.get("batchId")
    return [{"key": cell["key"], "field": cell["field"], "gtValue": cell.get("current"), "batchId": batch}
            for item in review.get("items") or [] for cell in item.get("cells") or []
            if not answered_on_page(latest.get((cell["key"], cell["field"])), cell.get("current"), batch,
                                    reasked(cell))]


def cmd_tasks(_: argparse.Namespace) -> int:
    rows = []
    for profile in task_profiles():
        task = profile["gtTask"]
        latest = current_answers(profile)
        rows.append({
            "task": profile["id"],
            "name": profile.get("displayName"),
            "subject": profile.get("subjectName"),
            "attribute": profile.get("attributeName"),
            "fields": [field.get("name") or field["id"] for field in task.get("fields") or []],
            "prepared": (review_root(profile) / "worklist.json").exists(),
            "settledCells": sum(1 for entry in latest.values() if entry["decision"] in SETTLED),
            # 과제를 고를 때 «보던 화면 있음»을 가르는 수 — status와 같은 셈. 스킬이 과제마다 status를 따로 부르지 않게.
            **_screen_counts(profile),
        })
    # 이 문으로는 못 고치는 GT. 감사 사이클의 원장을 쓰는 속성이다 — 요청이 여기 걸리면 그 문으로 보낸다.
    other_doors = [
        {"profile": profile["id"], "name": profile.get("displayName"), "attribute": profile.get("attributeName"),
         "subject": profile.get("subjectName"),
         "door": "감사 사이클의 GT 정정 화면(http://127.0.0.1:7391/audit?a=" + profile["id"] + ")에서 조서마다 판정"}
        for profile in all_profiles() if profile.get("gt") and not profile.get("gtTask")
    ]
    print(json.dumps({"tasks": rows, "otherDoors": other_doors, "brokenProfiles": broken_profiles()},
                     ensure_ascii=False, indent=2))
    return 0


# 배치가 바뀌어도 남기는 것. 이 밖의 것은 전부 지운다(남길 것 목록 방식).
# export는 사람에게 «이 파일을 붙여 넣어 주세요»라고 건넨 목록이라, «다음 거» 한 번에 사라지면 안 된다.
# 지울 것 목록으로 두면 옛 판의 파일(키가 든 판독 목록 같은)이 이름이 달라 살아남는다.
KEEP_ACROSS_BATCHES = {"agreed.jsonl", "shown.jsonl", "blind.jsonl", "export", "golden-thumbs"}  # golden-thumbs: 골든셋 화면의 작은 사진 — 배치와 무관해 다시 줄이지 않게 남긴다


def clear_previous_batch(root: Path) -> None:
    """지난 배치의 산출물을 지운다. 남아 있으면 판독자가 지난 화면에서 GT를 읽을 수 있고,
    옛 판독 결과가 새 목록에 붙을 수 있다. 사람의 원장은 여기 없다(GT 옆에 있다)."""
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.relative_to(root).parts[0] in KEEP_ACROSS_BATCHES:
            continue
        path.unlink() if path.is_file() or path.is_symlink() else path.rmdir()


def cmd_prepare(args: argparse.Namespace) -> int:
    # 한 번에 하나 — «다음 후보 받기» 러너가 도는 동안에는 준비하지 않는다(과제가 달라도). 러너 안에서 부른 것이면 그냥 지난다.
    with gate(lock_dir(find_profile(args.task))):
        return _prepare(args)


def _prepare(args: argparse.Namespace) -> int:
    profile = find_profile(args.task)
    task = load_task(profile)
    check_one_ledger_per_gt(profile)
    root = review_root(profile)
    if args.reread:
        return reread(profile, task, root)
    run_root = output_root(profile)
    gt_rows = load_gt(profile, task)
    found, counts = candidates(task, gt_rows, current_answers(profile), blind=blind_cells(root))
    skipped = [] if args.key else skipped_on_screen(profile, root)
    shown = shown_cells(root)
    latest_turn = max(shown.values(), default=0) + 1  # 지금 넘기는 칸은 파일의 어느 줄보다 뒤다
    for row in skipped:
        shown[(row["key"], row["field"], row["gtValue"])] = latest_turn
    # AI가 GT를 모른 채 같은 값을 낸 칸은 끝났다 — 다시 올리지 않는다. GT가 바뀌거나(값이 달라져 동의가 안 맞는다) 그 칸의 정의 절이
    # 바뀌면(지문이 달라진다) 다시 후보가 된다. 사람이 누른 판정이 아니므로 원장에는 여전히 적지 않는다.
    agreed = agreed_cells(root, field_digests(profile, task))
    settled = [cell for cell in found if settled_by_agreement(cell, agreed)]
    found = [cell for cell in found if not settled_by_agreement(cell, agreed)]
    counts["settledByAgreement"] = len(settled)
    items = group_items(task, found, gt_rows, agreed, shown)
    only = set(args.key or [])
    if only:
        items = [item for item in items if item["key"] in only]
    limit = args.limit if args.limit is not None else int(task.get("limit") or DEFAULT_LIMIT)

    # 멈출 수 있는 것은 **지우기 전에** 다 해 본다 — 사진 색인(없음·키 겹침)과 사진 처리 도구. 지운 뒤에 멈추면
    # 사람이 답하던 화면이 사라지고, 다시 요청해도 같은 자리에서 멈춘다.
    index = ImageIndex(profile, task)
    if index.has_photos:
        require_pillow()
    if not items:
        # 볼 칸이 없으면 지우지 않는다 — 지우고 끝내면 «준비 중»인 채로 화면이 영영 돌아오지 않는다. 지금 화면은 그대로다.
        # «모두 답함»과 «이 설정으로는 걸리는 칸이 없음»을 가른다 — 뒤엣것은 설정 실수일 수 있어 경고와 함께 낸다.
        definitions = resolve(profile, {"path": task["definitions"], "root": task.get("definitionsRoot") or "project"})
        # 답이 원본에 들어간 칸은 신호가 없어 alreadyDecided에 안 센다 — 원장에 확정 판정이 있으면 «모두 답함»이다.
        answered = (counts.get("alreadyDecided") or 0) or any(
            entry.get("decision") in SETTLED for entry in current_answers(profile).values())
        note = ("볼 칸이 없습니다 — 남은 후보를 모두 답했습니다." if answered else
                f"볼 칸이 없습니다 — 요청한 키({', '.join(args.key)})에는 남은 후보가 없습니다." if args.key else
                "볼 칸이 없습니다 — 이 설정으로는 걸리는 칸이 하나도 없습니다. 개발자에게 설정(등급 패턴)을 봐 달라고 전해 주세요.")
        print(json.dumps({"workflowArgs": None, "note": note, "counts": counts,
                          "warnings": batch_warnings(profile, task, definitions, gt_rows, [], index)},
                         ensure_ascii=False, indent=2))
        return 0
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    batch = f"{generated}-{secrets.token_hex(3)}"
    # 지우기와 새 배치 알리기는 판정 기록과 같은 잠금 안에서 — 그 사이에 옛 배치로 적히는 판정이 없게.
    with locked(profile):
        running = read_json(root / "manifest.json") or {}
        if running.get("preparing") and not (root / "review.json").is_file() and not args.force:
            # 판독이 도는 배치를 지우면 판독자가 열 파일이 사라지고, 끝난 결과는 «먼저 준비된 배치»로 거절된다 — 두 번 기다리게 된다.
            # 정말 죽은 배치는 «GT 화면 다시 열어줘»(render가 preparing을 푼다)나 --force로 푼다.
            raise TaskError("AI가 아직 읽는 중입니다 — 끝날 때까지 기다려 주세요. 세션이 끊겨 끝나지 않는다면 "
                            "«GT 화면 다시 열어줘»라고 한 뒤 «다음 거»라고 해 주세요.")
        if skipped:
            # 넘어간 칸을 남긴 뒤에 지운다 — 지운 뒤에는 무엇을 넘겼는지 모른다.
            with (root / "shown.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in skipped))
        clear_previous_batch(root)
        # 지운 **바로 뒤에** 새 배치를 알린다. 판독이 도는 몇 분 동안 서버가 지금 배치를 모르면 옛 탭이 잠기지 않고,
        # 화면 주소는 «다시 요청하라»고 해서 도는 배치를 또 지우게 만든다. 화면(page)은 finish·render가 채운다.
        write_json_atomic(root / "manifest.json", {"schemaVersion": "gt-review-manifest-v1", "profileId": profile["id"],
                                                   "page": None, "review": None, "batchId": batch, "preparing": True,
                                                   "preparedAt": generated})
    try:
        return _prepare_batch(args, profile, task, root, run_root, gt_rows, found, counts, items, limit, index, batch, generated)
    except BaseException:
        # 지운 뒤에 멈췄다 — «준비 중»으로 남기지 않는다. 화면 주소는 «화면이 없습니다 — 다시 요청»을 말하게 된다.
        write_json_atomic(root / "manifest.json", {"schemaVersion": "gt-review-manifest-v1", "profileId": profile["id"],
                                                   "page": None, "review": None, "batchId": batch, "preparing": False,
                                                   "failed": True})
        raise


def _prepare_batch(args: argparse.Namespace, profile: dict[str, Any], task: dict[str, Any], root: Path, run_root: Path,
                   gt_rows: dict[str, Any], found: list[dict[str, Any]], counts: dict[str, Any],
                   items: list[dict[str, Any]], limit: int, index: ImageIndex, batch: str, generated: str) -> int:
    fields = field_map(task)
    prepared: list[dict[str, Any]] = []
    ledger = current_answers(profile)
    for item in items[: limit if limit > 0 else None]:
        # 사람 화면의 사진은 images/에. 판독자 몫은 emit_workflow_args가 reader/에 따로 복사한다.
        evidence = prepare_evidence(index, gt_rows[item["key"]], item["key"], root / "images", run_root, batch)
        # 증거가 없는 건도 화면에는 싣는다 — 모순·범위 밖은 판독 없이도 사람이 가를 수 있다. 판독자만 띄우지 않는다.
        no_evidence = not evidence["images"] and not evidence["text"]
        # 후보가 아닌 칸도 판독자는 읽는다 — 그 판독이 GT와 확신 있게 다르면 화면에 올린다(BLIND_DISAGREES).
        quiet = quiet_cells(task, item["key"], gt_rows[item["key"]], ledger, {cell["field"] for cell in item["cells"]})
        prepared.append({"id": f"GI-{len(prepared) + 1:02d}", **item, **evidence, "noEvidence": no_evidence, "quiet": quiet})
    excluded = items[len(prepared):] if limit > 0 else []

    by_signal: dict[str, int] = {name: 0 for name in SIGNALS}
    for cell in found:
        for signal in cell["signals"]:
            by_signal[signal] += 1
    definitions = resolve(profile, {"path": task["definitions"], "root": task.get("definitionsRoot") or "project"})
    worklist = {
        "schemaVersion": WORKLIST_SCHEMA,
        "profileId": profile["id"],
        "displayName": profile.get("displayName"),
        "unit": task.get("unit"),
        "batchId": batch,
        "generatedAt": generated,
        "sources": {"gt": portable(resolve(profile, task["gt"])), "definitions": relative_or_absolute(definitions)},
        "fields": [{"id": field["id"], "name": field.get("name") or field["id"], "labels": field["labels"],
                    "labelNames": field.get("labelNames") or {}, "cardinality": field.get("cardinality", "one")}
                   for field in fields.values()],
        "signals": SIGNALS,
        "counts": {"cells": len(found), "items": len(items), "selectedItems": len(prepared),
                   "noEvidence": sum(1 for item in prepared if item["noEvidence"]),
                   "remainingItems": len(excluded), "bySignal": by_signal, **counts},
        "items": prepared,
        "excluded": [{"key": item["key"], "cells": len(item["cells"])} for item in excluded],
    }
    worklist["warnings"] = batch_warnings(profile, task, definitions, gt_rows, prepared, index)
    # 잠금 밖에서 사진을 준비하는 동안 다른 prepare가 시작됐을 수 있다 — 그 배치의 manifest 위에 이 작업 목록을 얹지 않는다.
    with locked(profile):
        if (read_json(root / "manifest.json") or {}).get("batchId") != batch:
            raise TaskError("그사이 다른 준비가 시작됐습니다 — 이 준비는 버립니다. 나중 것을 기다려 주세요.")
        write_json_atomic(root / "worklist.json", worklist)
    return emit_workflow_args(profile, task, root, worklist, [item for item in prepared if not item["noEvidence"]])


def _all_cells(item: dict[str, Any]) -> list[dict[str, Any]]:
    """후보 칸과 조용한 칸(후보가 아닌 칸) — 반론자에게 건넬 GT·이름표는 둘 다에 필요하다."""
    return [*(item.get("cells") or []), *(item.get("quiet") or [])]


def batch_warnings(profile: dict[str, Any], task: dict[str, Any], definitions: Path, gt_rows: dict[str, Any],
                   prepared: list[dict[str, Any]], index: ImageIndex | None = None) -> dict[str, Any]:
    """준비의 경고 — 조용한 설정 실수가 드러나는 자리. 볼 칸이 없는 준비도 같은 경고를 낸다(설정 실수가 «볼 칸 없음»으로
    숨지 않게). 사진·글 경고는 고른 건이 있을 때만 셈할 수 있다."""
    fields = field_map(task)
    SEED_MISSING.clear()
    warnings: dict[str, Any] = {
        "definitionsSeedChanged": seed_changed(profile, definitions),
        "definitionsSeedMissing": list(SEED_MISSING),
    }
    # 못 연 사진을 이유별로 센다. 경로가 틀렸거나 주소(URL)인 색인은 건마다 «못 연 사진»으로만 남아 조용히 증거가 빈다.
    missing_reasons: dict[str, int] = {}
    for item in prepared:
        for entry in item.get("missing") or []:
            missing_reasons[entry["reason"]] = missing_reasons.get(entry["reason"], 0) + 1
    warnings["imagesMissing"] = missing_reasons
    # 출처가 어느 등급 패턴에도 맞지 않는 GT 칸 — 등급이 «모름»이면 후보로 올라오지 않는다.
    # 등급 패턴의 오타는 이렇게만 드러난다.
    unclassified: dict[str, int] = {}
    for key, row in gt_rows.items():
        for spec in fields.values():
            source = gt_source(task, spec, row)
            if gt_value(task, spec, row) is not None and authority(task, source) == "UNKNOWN":
                unclassified[source or "(출처 없음)"] = unclassified.get(source or "(출처 없음)", 0) + 1
    # 등급 선언이 없어도 센다 — 출처 열이 없는 GT는 «(출처 없음)»으로 드러나야 authority.default를 적을 자리를 안다.
    warnings["sourcesUnclassified"] = unclassified
    # 맥락·글로 선언했는데 이번에 고른 건 어디에도 값이 없는 열 — 대개 열 이름 오타다. 글이 근거인 GT라면
    # 모든 건이 «증거 없음»으로 떨어져 판독이 통째로 빠진다.
    declared = [*((task.get("images") or {}).get("contextFields") or []), *((task.get("evidence") or {}).get("textFields") or [])]
    seen_columns = {name for item in prepared for part in ("context", "text") for name in (item.get(part) or {})}
    warnings["evidenceColumnsNeverSeen"] = sorted(set(map(str, declared)) - seen_columns) if prepared else []
    # 사진 색인이 GT와 이어졌나 — 이음 열(joinField)·역할 열(roleField)·역할 이름(roles)을 잘못 적으면 사진이 한 장도 오지 않는데,
    # 줄마다 «못 연 사진»도 남지 않아 조용하다.
    if index is not None and index.has_photos:
        joined = [row for row in gt_rows.values() if index.row_for(row)]
        before = sum(len(index.raw_entries(row)) for row in joined)
        after = sum(len(index.entries(row)) for row in joined)
        role_field = index.spec.get("roleField") or "role"
        seen_roles = {str(entry.get(role_field)) for row in joined for entry in index.raw_entries(row)}
        declared = [str(role) for key in ("roles", "tileRoles", "preTiledRoles") for role in index.spec.get(key) or []]
        warnings["imagesCoverage"] = {
            "gtKeysWithIndexRow": len(joined), "gtKeys": len(gt_rows),
            "entriesBeforeRoleFilter": before, "entriesAfterRoleFilter": after,
            "selectedItemsWithoutImages": sum(1 for item in prepared if not item.get("images")),
            "noneMatched": not joined or after == 0,
        }
        warnings["rolesNeverSeen"] = sorted(set(declared) - seen_roles) if joined else []
    return warnings


# 마지막 seed_changed가 찾지 못한 원문. 준비 경고 definitionsSeedMissing으로 싣는다.
SEED_MISSING: list[str] = []


def seed_changed(profile: dict[str, Any], definitions: Path) -> list[str]:
    """정의 문서가 다른 문서에서 옮겨 온 것이면(seededFrom), 원문이 옮긴 뒤 바뀌었는지 본다. 바뀌었으면 알린다 —
    막지는 않는다. 해시(seededFromSha256)는 원문마다 하나씩, 같은 순서로 적는다. `.claude/`로 시작하는 경로는 이
    저장소, 아니면 외부 레포(source.repository) 기준이다. 해시가 없으면 확인하지 않는다(계약에 적었다)."""
    import hashlib

    head = definitions.read_text(encoding="utf-8").split("---")
    if len(head) < 3:
        return []
    meta = head[1]

    def listed(name: str) -> list[str]:
        block = re.search(rf"^{name}:\s*\n((?:\s*-\s*\S+\s*\n)+)", meta, re.M)
        single = re.search(rf"^{name}:\s*(\S+)\s*$", meta, re.M)
        if block:
            return re.findall(r"-\s*(\S+)", block.group(1))
        return [single.group(1)] if single else []

    sources, hashes = listed("seededFrom"), listed("seededFromSha256")
    changed = []
    repository = (profile.get("source") or {}).get("repository")
    for source, expected in zip(sources, hashes):
        path = PROJECT_ROOT / source if source.startswith(".claude/") else (
            (PROJECT_ROOT / str(repository)).resolve() / source if repository else PROJECT_ROOT / source)
        if not path.is_file():
            # 원문이 이 저장소·컴퓨터에 없다 — «바뀌었다»가 아니라 «확인하지 못했다»다. 따로 알린다.
            SEED_MISSING.append(source)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            changed.append(source)
    return changed


def emit_workflow_args(profile: dict[str, Any], task: dict[str, Any], root: Path, worklist: dict[str, Any],
                       items: list[dict[str, Any]]) -> int:
    """판독자 파일을 쓰고 워크플로우 인자를 낸다.

    판독자 파일은 `reader/` 한 폴더에만 둔다. 그 폴더에는 키도 GT 값도 없다 — 파일 이름도 불투명하고,
    건 이름(GI-01)도 적지 않는다(건 이름으로 작업 목록을 찾아 키를 알 수 있기 때문이다).
    GT 값은 판독자 파일에 쓰지 않고 워크플로우 인자(표준 출력)로만 나간다.

    정직하게 적는다 — 같은 `gt-review/` 폴더의 작업 목록(`worklist.json`)에는 사람 화면을 그리려고 키·지금 GT·
    실행 값이 있고, GT 원본도 저장소에서 닿는 자리에 있다. 판독자의 Grep·Glob은 거기 닿을 수 있다(한 건짜리
    배치면 대응도 뻔하다). 여기서 구조로 없앤 것은 판독자 **자기 입력 안의 실마리**(이름·경로·순서·열)이고,
    그 밖은 역할 파일·프롬프트의 금지, 읽기 전용 도구, 뒤따르는 반론과 사람 판정으로 지킨다(계약 «배치와 눈가림»).
    """
    fields = field_map(task)
    definitions = resolve(profile, {"path": task["definitions"], "root": task.get("definitionsRoot") or "project"})
    reader = root / "reader"
    views: dict[str, str] = {}
    labels = {fid: [str(label) for label in spec["labels"]] for fid, spec in fields.items()}
    many = {fid: spec.get("cardinality") == "many" for fid, spec in fields.items()}
    # 사례(조회층) — 사람이 AI의 물음에 답한 기록. 규칙이 된 것은 정책 파일에 이미 있고, 여기에는 물음·답·그 사진의 사정이 있다.
    cases = reader_cases(profile, task, definitions)
    given = read_json(root / "policy-given.json") or {}
    if given.get("batchId") != worklist["batchId"]:
        given = {"batchId": worklist["batchId"], "items": {}}
    for item in items:
        # 판독자 몫은 따로 복사한 사진에 **아무 데도 적지 않는** 무작위 이름을 붙인다. 사람 화면의 사진 경로는
        # 작업 목록에 있지만 이 이름은 없다 — 판독자가 자기 파일 이름으로 작업 목록을 뒤져 키를 찾을 실마리가 없다.
        # 이름과 건의 대응은 워크플로우 인자(표준 출력)와 그 반환값에만 있다.
        token = secrets.token_hex(8)
        folder = reader / token
        pictures = []
        for image in item["images"]:
            target = folder / f"{image['viewId']}.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT_ROOT / image["path"], target)
            picture = {"imageId": image["viewId"], "path": relative_or_absolute(target)}
            # 사진 종류(프로필 images.roleNames의 이름)는 답을 가리키지 않는다 — 정의가 종류로 가르면 판독자도 알아야 한다.
            role_name = ((task.get("images") or {}).get("roleNames") or {}).get(str(image.get("role")))
            if role_name:
                picture["role"] = role_name
            pictures.append(picture)
        # 필수층 — 이 상품에 걸리는 정의·허용값·규칙만 잘라 쓴 정책 파일. 판독자는 원래 정의 문서를 열지 않는다.
        policy_text, policy_meta = reader_policy(definitions, labels, many, item.get("context") or {})
        policy_path = reader / f"{token}.policy.md"
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(policy_text, encoding="utf-8")
        given["items"][item["id"]] = policy_meta
        # 조회층 — 사례 목록(제목)과 사례 파일. 이 상품 자신의 답은 싣지 않는다(그건 이 칸의 정답이다).
        cases_index = write_reader_cases(folder / "cases", [case for case in cases if case["key"] != item["key"]])
        view = {
            "schemaVersion": "gt-review-reader-view-v5",
            "policy": relative_or_absolute(policy_path),
            **({"cases": relative_or_absolute(cases_index)} if cases_index else {}),
            # 필드는 **과제가 선언한 순서로 전부** 준다. 후보 칸만 신호 순서대로 주면, 어느 칸이 다투는 칸이고
            # 어느 칸이 이미 끝났는지가 목록 모양으로 새어 나간다. 판독이 쓰이는 것은 후보 칸뿐이다.
            "fields": [{"id": field["id"], "name": field.get("name") or field["id"], "labels": field["labels"],
                        "labelNames": field.get("labelNames") or {},
                        "cardinality": field.get("cardinality", "one")} for field in fields.values()],
            # 사진 경로는 프로젝트 루트 기준이다.
            "images": pictures,
            "context": item.get("context") or {},
            "text": item.get("text") or {},
            # 맥락·글 열의 사람 이름. 판독자는 문장에서 열 이름 대신 이 이름을 쓴다(열 이름은 경고 대상이다).
            "columnNames": {name: str((task.get("columnNames") or {}).get(name, name))
                            for name in [*(item.get("context") or {}), *(item.get("text") or {})]},
        }
        path = reader / f"{token}.json"
        write_json(path, view)
        views[item["id"]] = relative_or_absolute(path)
    # 무엇을 실었는지는 사람 쪽에 남긴다 — 판독이 인용한 규칙(rulesApplied)이 실제로 받은 규칙인지 finish가 대조한다.
    write_json(root / "policy-given.json", given)
    agents = dict(AGENTS)  # 모든 과제에 공통 — 과제는 판독자를 고르지 않는다(gt_task.AGENTS)
    workflow_args = {
        "task": profile["id"],
        "batchId": worklist["batchId"],
        "definitions": relative_or_absolute(definitions),
        "agents": agents,
        "items": [{"id": item["id"], "fields": [cell["field"] for cell in item["cells"]],
                   # 후보가 아닌 칸 — 판독이 확신 있게 GT와 다를 때만 반론을 부른다(화면의 BLIND_DISAGREES).
                   "quietFields": [cell["field"] for cell in item.get("quiet") or []],
                   "images": len(item["images"])} for item in items],
        # 필드 설명은 과제 전체에 한 번만 — 건마다 되풀이하면 60건 배치의 인자가 100KB를 넘는다(워크플로우가 건마다 합친다).
        "common": {
            # 판독자가 필드 칸에 한국어 이름을 적으면 id로 되돌리는 표. 이름은 GT 값이 아니다.
            "fieldNames": {spec.get("name") or fid: fid for fid, spec in fields.items()},
            # 반론자에게 GT·판독 값을 «이름 (코드)»로 건네려고. 코드만 주면 반론 문장에 코드가 그대로 샌다.
            "labelNames": {fid: spec.get("labelNames") or {} for fid, spec in fields.items()},
            # 허용값 — 허용값 밖의 판독(화면에서는 «AI가 확신 못 함»)에는 반론을 부르지 않는다.
            "labels": {fid: [str(label) for label in spec["labels"]] for fid, spec in fields.items()},
            "many": {fid: spec.get("cardinality") == "many" for fid, spec in fields.items()},
        },
        "worklist": relative_or_absolute(root / "worklist.json"),
    }
    # 사람이 검수에서 답한 경계는 프롬프트에 싣지 않는다 — 규칙이 된 것은 건마다의 정책 파일에, 나머지는 사례 목록(판독 파일의 cases)에 있다.
    # 파일에는 GT 값도 판독 파일 이름도 없이 남긴다(무엇을 돌렸는지 되짚는 용도). 둘 다 아래 표준 출력에만 있다.
    write_json(root / "workflow-args.json", workflow_args)
    for entry in workflow_args["items"]:
        entry["view"] = views[entry["id"]]
    workflow_args["gt"] = {item["id"]: {cell["field"]: cell["current"] for cell in _all_cells(item)} for item in items}
    workflow_args["alternatives"] = {item["id"]: {cell["field"]: cell.get("alternatives") or [] for cell in _all_cells(item)
                                                  if cell.get("alternatives")} for item in items}
    print(relative_or_absolute(root / "worklist.json"))
    # 경고도 함께 낸다 — 스킬이 작업 목록(GT 값이 든 파일)을 열지 않고 사람에게 알릴 문장을 고르게.
    print(json.dumps({"workflowArgs": workflow_args, "warnings": worklist.get("warnings") or {}}, ensure_ascii=False, indent=2))
    return 0


def reader_cases(profile: dict[str, Any], task: dict[str, Any], definitions: Path) -> list[dict[str, Any]]:
    """판독자가 조회할 사례 — 사람이 AI의 물음에 답한 판정마다 하나. 규칙으로 옮겨졌으면 그 규칙 ID를 붙인다.
    키는 사람 쪽 대조(자기 상품 빼기)에만 쓰고 사례 파일에는 적지 않는다."""
    fields = field_map(task)
    policy = definition_policy(definitions, {fid: [str(c) for c in spec["labels"]] for fid, spec in fields.items()},
                               {fid: spec.get("cardinality") == "many" for fid, spec in fields.items()})
    by_question = {(rule["field"], rule.get("물음")): rule for rules in policy["rules"].values() for rule in rules if rule.get("물음")}
    cases = []
    for row in answered_questions(profile, task):
        rule = by_question.get((row["field"], row["question"]))
        cases.append({"key": row["key"], "field": row["field"], "fieldName": row["fieldName"], "question": row["question"],
                      "here": row.get("here") or "", "answer": row["answer"], "answerName": row["answerName"],
                      "rule": rule["id"] if rule else None, "ruleText": rule_line(rule) if rule else None,
                      "decidedAt": row.get("decidedAt")})
    return cases


def write_reader_cases(folder: Path, cases: list[dict[str, Any]]) -> Path | None:
    """사례 목록(index.json — 제목만)과 사례 파일(C01.json …)을 쓴다. 사례가 없으면 아무것도 쓰지 않는다.
    사례 번호는 배치 안에서만 뜻이 있다 — 판정 ID를 쓰면 판독자가 원장에서 그 키를 찾을 실마리가 된다."""
    if not cases:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    index = []
    for number, case in enumerate(cases, 1):
        case_id = f"C{number:02d}"
        detail = {"id": case_id, "field": case["field"], "fieldName": case["fieldName"], "question": case["question"],
                  "answer": case["answer"], "answerName": case["answerName"], "here": case["here"],
                  "rule": case["rule"], "ruleText": case["ruleText"], "decidedAt": case["decidedAt"]}
        write_json(folder / f"{case_id}.json", detail)
        index.append({"id": case_id, "field": case["field"], "fieldName": case["fieldName"], "title": case["question"],
                      "answerName": case["answerName"], "rule": case["rule"], "file": relative_or_absolute(folder / f"{case_id}.json")})
    path = folder / "index.json"
    write_json(path, {"schemaVersion": "gt-review-cases-v1", "cases": index})
    return path


def reread(profile: dict[str, Any], task: dict[str, Any], root: Path) -> int:
    """지금 화면을 두고, 판독이 돌아오지 않은(또는 한국어가 아닌) 건만 다시 판독하게 한다.

    새 배치를 만들지 않는다 — 사람이 보던 화면과 답한 칸이 그대로 남는다. `finish`가 결과를 지금 배치에 합친다.
    """
    worklist = read_json(root / "worklist.json")
    review = read_json(root / "review.json") or {}
    sweep = read_json(root / "sweep-raw.json") or {}
    if worklist and sweep.get("batchId") != worklist.get("batchId"):
        sweep = {}  # 다른 배치의 판독 — 건 번호(GI-03 같은)가 배치마다 다시 매겨지므로 섞으면 엉뚱한 건을 다시 읽는다
    if (read_json(root / "manifest.json") or {}).get("preparing") and not review:
        # 첫 판독이 도는 중에 다시 읽으면 판독자가 열 파일을 지운다 — 끝난 뒤에 다시 한다.
        raise TaskError("AI가 아직 읽는 중입니다 — 끝난 뒤에 다시 요청해 주세요.")
    if not worklist:
        raise TaskError("준비된 화면이 없습니다. 처음부터 준비합니다.")
    if not review:
        # 판독 결과를 한 번도 받지 못했다(워크플로우가 중간에 멈춤). 증거가 있는 건은 전부 다시 읽는다.
        unread = {item["id"] for item in worklist["items"]}
    else:
        # 판독이 돌아오지 않은 칸, 그리고 판독은 왔는데 반론이 돌아오지 않은 칸 — 둘 다 다시 읽는다.
        # 사람이 이미 답한 칸은 다시 읽지 않는다(답이 남아 있으니 AI를 다시 부를 까닭이 없다).
        latest = current_answers(profile)
        batch = review.get("batchId")
        unread = {item["id"] for item in review.get("items") or []
                  if any((cell["status"] == "NOT_READ" or cell.get("noDefense"))
                         and not answered_on_page(latest.get((cell["key"], cell["field"])), cell.get("current"), batch,
                                                  reasked(cell))
                         for cell in item.get("cells") or [])}
    # 저장된 옛 경고가 아니라 지금 규칙으로 다시 셈한다 — 화면(render)이 보이는 경고와 같은 목록이어야 한다.
    # 한국어가 아닌 건도 사람이 이미 모든 칸에 답했으면 다시 읽지 않는다 — 위의 «못 읽음» 가지와 같은 규칙.
    if sweep.get("items"):
        latest_now = current_answers(profile)
        page = {item["id"]: item for item in review.get("items") or []}
        for item_id in sweep_warnings(profile, sweep)["notKorean"]:
            cells = (page.get(item_id) or {}).get("cells") or []
            if not cells or any(not answered_on_page(latest_now.get((cell["key"], cell["field"])), cell.get("current"),
                                                     review.get("batchId"),
                                                     reasked(cell))
                                for cell in cells):
                unread.add(item_id)
    items = [item for item in worklist["items"] if item["id"] in unread and not item.get("noEvidence")]
    # 지난 판독 파일은 지우지 않는다 — 첫 판독이 아직 도는 중일 수 있고(화면만 먼저 연 경우), 그 판독자가 열 파일이 사라진다.
    # 새 판독 파일은 새 불투명 폴더에 쓰이고, 치우는 일은 다음 배치(prepare)가 한다. 옛 파일에도 키·GT는 없다.
    if not items:
        print(json.dumps({"workflowArgs": None, "note": "다시 판독할 건이 없습니다."}, ensure_ascii=False))
        return 0
    return emit_workflow_args(profile, task, root, worklist, items)


def korean_warnings(value: dict[str, Any], jargon: set[str] | None = None,
                    codes: dict[str, dict[str, str]] | None = None) -> list[str]:
    """운영팀이 읽는 문장이 한국어가 아니거나 내부 이름(필드 ID·파일 열 이름)을 쓰면 알린다. 막지는 않는다 —
    판독 자체가 틀린 것은 아니다. 다시 읽기(«한국어로 다시 봐줘»)의 대상이 된다.

    허용값 코드(`codes`: 필드 → {코드 → 화면의 이름})는 **바로 앞에 그 이름이 붙은 괄호 안**일 때만 괜찮다(«빨강 (RED)»).
    코드만 쓰거나 다른 이름을 붙이면(«붉은색(RED)» — 버튼은 «빨강») 사람이 같은 값인지 망설인다. 판독·반론 문장은
    **자기 필드의** 코드표로 본다(두 필드가 같은 코드를 다른 이름으로 쓸 수 있다). 숫자 코드(정수 필드)는 보지 않는다 —
    «사진 2장»의 2를 코드로 잡는다. 건 전체 메모는 어느 필드의 이름이든 맞으면 괜찮다."""
    jargon = jargon or set()
    codes = codes or {}

    def bare_code(text: str, tables: list[dict[str, str]]) -> bool:
        names: dict[str, set[str]] = {}
        for table in tables:
            for code, name in table.items():
                if not code.isdigit():
                    names.setdefault(code, set()).add(name)
        for code, allowed in names.items():
            for match in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(code)}(?![A-Za-z0-9_])", text):
                before = text[:match.start()].rstrip()
                head = before[:-1].rstrip().rstrip("»'\"")
                if not (before.endswith("(") and text[match.end():].startswith(")") and any(head.endswith(n) for n in allowed)):
                    return True
        return False

    def bad(text: str | None, tables: list[dict[str, str]]) -> bool:
        # 한글은 낱말 문자라 «필드ID는»에서 \b가 걸리지 않는다. 영문 경계만 본다.
        return bool(text) and (not HANGUL.search(text) or bare_code(text, tables) or any(
            re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text) for word in jargon))

    found = []
    for item in value.get("items") or []:
        rows = [(r.get("observation"), r.get("field")) for r in ((item.get("reading") or {}).get("readings") or [])]
        rows += [(r.get("why"), r.get("field")) for r in ((item.get("defense") or {}).get("rebuttals") or [])]
        rows.append(((item.get("reading") or {}).get("note"), None))
        if any(bad(text, [codes.get(field) or {}] if field else list(codes.values())) for text, field in rows):
            found.append(item.get("id"))
    return found


def sweep_warnings(profile: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    """판독 결과의 경고. finish와 render가 같은 계산을 쓴다 — render가 저장된 옛 경고를 되쓰면 규칙이 바뀐 뒤에도
    화면이 옛 판정을 보인다."""
    task = load_task(profile)
    jargon = {field["id"] for field in task["fields"]} | {"text", "context", "images"}
    # 판독 파일의 맥락·글 열 이름도 내부 이름이다(문장에 영문 열 이름이 나오면 운영팀이 못 읽는다).
    jargon |= {str(name) for name in (task.get("images") or {}).get("contextFields") or []}
    jargon |= {str(name) for name in (task.get("evidence") or {}).get("textFields") or []}
    codes = {spec["id"]: {str(code): str(name) for code, name in (spec.get("labelNames") or {}).items()}
             for spec in task["fields"]}
    return {"notKorean": korean_warnings(value, jargon, codes), "rulesUnknown": rule_warnings(review_root(profile), value)}


def rule_warnings(root: Path, value: dict[str, Any]) -> list[dict[str, Any]]:
    """판독이 인용한 규칙(rulesApplied) 가운데 그 건의 정책 파일에 없던 것 — 받지 않은 규칙을 따랐다고 적었거나(지어냄),
    범위 밖이라 뺀 규칙을 댔다. 어느 쪽이든 그 칸의 판독 근거를 사람이 한 번 더 봐야 한다. 받은 규칙은 준비가 남긴
    `policy-given.json`에 있다 — 배치가 다르면 대조하지 않는다."""
    given = read_json(root / "policy-given.json") or {}
    if not given.get("items") or given.get("batchId") != value.get("batchId"):
        return []
    found = []
    for item in value.get("items") or []:
        rules = ((given["items"].get(item.get("id")) or {}).get("rules")) or {}
        for reading in (item.get("reading") or {}).get("readings") or []:
            cited = [str(rule) for rule in reading.get("rulesApplied") or []]
            unknown = [rule for rule in cited if rule not in (rules.get(str(reading.get("field"))) or [])]
            if unknown:
                found.append({"item": item.get("id"), "field": reading.get("field"), "rules": unknown})
    return found


def remember_agreement(root: Path, worklist: dict[str, Any], sweep: dict[str, Any],
                       task_fields: list[dict[str, Any]] | None = None, digests: dict[str, str] | None = None) -> None:
    """GT를 모르는 눈이 GT와 같은 값을 확신 있게 낸 칸을 적어 둔다. 원장이 아니다 — 다음 준비가 그 칸을 끝난 것으로 본다.
    그 칸 절의 지문(`policy`)을 함께 적는다 — 정의 문서의 그 절이 바뀌면 옛 동의는 근거가 되지 않는다."""
    path = root / "agreed.jsonl"
    known = agreed_cells(root, digests)
    current = {item["id"]: {cell["field"]: cell["current"] for cell in item["cells"]} for item in worklist["items"]}
    keys = {item["id"]: item["key"] for item in worklist["items"]}
    # 모순·범위 밖 칸은 적지 않는다. 판독이 모순의 두 칸에 다 «맞다»고 했다면 GT가 맞다는 뜻이 아니라 판독이
    # 모순을 놓쳤다는 뜻이다 — 적으면 1순위 모순이 «이미 합의»로 맨 뒤에 밀린다.
    urgent = {(item["id"], cell["field"]) for item in worklist["items"] for cell in item["cells"]
              if {"GT_SELF_CONTRADICTION", "GT_OUT_OF_RANGE"} & set(cell.get("signals") or [])}
    # 허용값은 지금 정책에서 — 화면(render)과 같은 목록으로 «같은 값»을 가른다.
    policy_fields = task_fields if task_fields is not None else worklist.get("fields") or []
    many = {field["id"] for field in policy_fields if field.get("cardinality") == "many"}
    labels = {field["id"]: {str(label) for label in field.get("labels") or []} for field in policy_fields}
    lines = []
    for item in sweep.get("items") or []:
        values: dict[str, set[str]] = {}
        for reading in (item.get("reading") or {}).get("readings") or []:
            values.setdefault(str(reading.get("field")), set()).add(str(reading.get("value")))
        for reading in (item.get("reading") or {}).get("readings") or []:
            if len(values.get(str(reading.get("field")), set())) > 1:
                continue  # 한 칸에 두 값을 낸 판독은 합의가 아니다
            gt = current.get(item["id"], {}).get(reading.get("field"), "__none__")
            # 화면(cell_status)과 같은 규칙 — 값 여럿만 조각으로 나눠 정렬하고, 값 하나는 통째로 본다. 허용값 밖은 합의가 아니다.
            raw = str(reading.get("value") or "")
            parts = sorted(set(raw.split("|")) - {""}) if reading.get("field") in many else [raw]
            if not parts or not all(part in labels.get(reading.get("field"), set()) for part in parts):
                continue
            canonical = "|".join(parts)
            if (item["id"], reading.get("field")) in urgent:
                continue
            if reading.get("confidence") == "HIGH" and canonical and canonical == gt:
                cell = (keys[item["id"]], reading["field"], gt)
                if cell not in known:
                    known.add(cell)
                    lines.append(json.dumps({"key": cell[0], "field": cell[1], "gtValue": gt, "batchId": sweep.get("batchId"),
                                             **({"policy": digests[cell[1]]} if digests and cell[1] in digests else {})},
                                            ensure_ascii=False))
    if lines:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def cmd_finish(args: argparse.Namespace) -> int:
    """워크플로우 출력 파일을 그대로 받아 저장하고 화면을 만든다. JSON을 손으로 옮기지 않게 하려는 문이다."""
    profile = find_profile(args.task)
    root = review_root(profile)
    raw = json.loads(Path(args.source).read_text(encoding="utf-8"))
    value = raw.get("result") if isinstance(raw, dict) and isinstance(raw.get("result"), dict) else raw
    if not isinstance(value, dict) or value.get("schemaVersion") != SWEEP_SCHEMA:
        raise TaskError(f"{args.source}: 워크플로우 gt-review의 반환값({SWEEP_SCHEMA})이 아닙니다.")
    restart = value.get("needsRestart")
    if restart and restart.get("role") != "defender":
        raise TaskError("판독 에이전트가 이 세션에 등록돼 있지 않아 판독이 돌지 않았습니다. "
                        "Claude Code를 한 번 껐다 켠 뒤 다시 요청해 주세요.")
    # 반론자만 없었으면 받은 판독은 저장한다 — 반론이 없는 칸은 «못 읽음»(noDefense)으로 남아 다시 읽기 대상이 된다.
    if value.get("task") != profile["id"]:
        raise TaskError(f"다른 과제의 판독 결과입니다: {value.get('task')} != {profile['id']}")
    worklist = read_json(review_root(profile) / "worklist.json")
    if not worklist:
        raise TaskError("준비된 작업 목록이 없습니다. 준비부터 다시 합니다.")
    if value.get("batchId") != worklist.get("batchId"):
        raise TaskError("이 판독 결과는 지금 작업 목록보다 먼저 준비된 배치의 것입니다. 옛 결과를 새 목록에 붙이지 않습니다.")
    expected = {item["id"] for item in worklist.get("items") or []}
    wrong = [item.get("id") for item in value.get("items") or [] if item.get("id") not in expected]
    if wrong:
        raise TaskError(f"작업 목록에 없는 건이 판독 결과에 있습니다: {wrong}")
    declared = load_task(profile)["fields"]
    ids = {field["id"] for field in declared}
    names = {field.get("name") or field["id"]: field["id"] for field in declared}
    for item in value.get("items") or []:
        for part, key in (("reading", "readings"), ("defense", "rebuttals")):
            for row in ((item.get(part) or {}).get(key) or []):
                # 이미 id면 그대로 — 이름 풀이는 id가 아닐 때만(워크플로우의 fieldId와 같은 규칙).
                if row.get("field") not in ids:
                    row["field"] = names.get(row.get("field"), row.get("field"))
    # 쓰기는 «다음 거»(prepare)와 같은 잠금 안에서, 배치를 다시 본 뒤에 — 확인과 쓰기 사이에 새 배치가 폴더를 비우면
    # 옛 배치의 판독이 새 폴더에 남아, 다시 그리기를 멈추고 다시 읽기에 엉뚱한 건을 넣는다.
    with locked(profile):
        current = read_json(root / "worklist.json") or {}
        manifest = read_json(root / "manifest.json") or {}
        if current.get("batchId") != value.get("batchId") or manifest.get("batchId") not in (None, value.get("batchId")):
            raise TaskError("이 판독 결과는 지금 작업 목록보다 먼저 준비된 배치의 것입니다. 옛 결과를 새 목록에 붙이지 않습니다.")
        # 같은 배치의 다시 읽기(reread)면 지난 결과에 합친다 — 다시 읽은 건만 바꾸고 나머지는 그대로 둔다.
        previous = read_json(root / "sweep-raw.json")
        if previous and previous.get("batchId") == value.get("batchId"):
            merged = {item["id"]: item for item in previous.get("items") or []}
            merged.update({item["id"]: item for item in value.get("items") or [] if item.get("reading")})
            value = {**value, "items": list(merged.values())}
        value["warnings"] = sweep_warnings(profile, value)
        write_json_atomic(root / "sweep-raw.json", value)
        task_now = load_task(profile)
        remember_agreement(root, current, value, task_now["fields"], field_digests(profile, task_now))
    args.sweep = None
    return cmd_render(args)


def cmd_render(args: argparse.Namespace) -> int:
    from gt_review_render import render

    profile = find_profile(args.task)
    root = review_root(profile)
    worklist = read_json(root / "worklist.json")
    if not worklist:
        raise TaskError("준비된 작업 목록이 없습니다. 준비부터 다시 합니다.")
    sweep_path = Path(args.sweep) if args.sweep else root / "sweep-raw.json"
    sweep = read_json(sweep_path) if sweep_path.is_file() else None
    if sweep is not None and not args.sweep and sweep.get("batchId") != worklist.get("batchId"):
        # 폴더에 남은 다른 배치의 판독은 없는 것으로 본다 — 멈추면 «못 읽음» 화면조차 못 그린다.
        sweep = None
    if sweep is not None and (sweep.get("schemaVersion") != SWEEP_SCHEMA or sweep.get("batchId") != worklist.get("batchId")):
        raise TaskError("판독 결과가 지금 작업 목록의 배치와 다릅니다. 두 실행을 섞지 않습니다.")
    if sweep is not None:
        sweep["warnings"] = sweep_warnings(profile, sweep)  # 저장된 옛 경고가 아니라 지금 규칙으로
    written = render(profile, worklist, sweep, list(current_answers(profile).values()), root)
    if not written:
        raise TaskError("새 배치가 준비돼 이 결과는 화면에 올리지 않았습니다 — 새 배치의 판독을 기다려 주세요.")
    for path in written:
        print(relative_or_absolute(path))
    record_asked(profile)
    remember_blind(root)
    return 0


def cmd_pages(args: argparse.Namespace) -> int:
    """정책·골든셋 두 장만 쓴다 — 배치(작업 목록·판독)가 없어도 된다. 과제를 새로 붙였을 때 메뉴에 곧바로 오르게."""
    from gt_decisions import _write_atomic
    from gt_review_render import render_golden_html, render_policy_html

    profile = find_profile(args.task)
    root = review_root(profile)
    root.mkdir(parents=True, exist_ok=True)
    for name, build in (("policy.html", render_policy_html), ("golden.html", lambda p: render_golden_html(p, root))):
        _write_atomic(root / name, build(profile))
        print(relative_or_absolute(root / name))
    return 0

def cmd_record(args: argparse.Namespace) -> int:
    """말로 받은 판정. 사람이 본 GT 값(--expect)이 늘 있어야 한다 — 화면의 버튼과 같은 선이다."""
    profile = find_profile(args.task)
    if args.expect is None and not args.expect_empty:
        raise TaskError("사람이 본 지금 GT 값을 --expect로(빈칸이면 --expect-empty) 함께 적어야 합니다.")
    worklist = read_json(review_root(profile) / "worklist.json") or {}
    # AI의 물음에 말로 답한 판정 — 화면의 버튼처럼 그 물음을 함께 남긴다. 그래야 `qa`가 정책 문답으로 옮길 수 있다.
    proposal = {"ask": {"question": args.ask.strip(), "here": ""}} if (args.ask or "").strip() else None
    entry = record(profile, args.key, args.field, args.decision, args.reviewer, args.value, args.reason or "", proposal,
                   expected_before=None if args.expect_empty else args.expect,
                   channel="spoken", batch=worklist.get("batchId"), gap=args.gap)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    print(json.dumps(export(find_profile(args.task)), ensure_ascii=False, indent=2))
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    from gt_publish import publish

    print(json.dumps(publish(find_profile(args.task), base=args.base, confirm=args.yes, open_pr=not args.no_pr),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    print(json.dumps(apply(find_profile(args.task), confirm=args.yes), ensure_ascii=False, indent=2))
    return 0


def _screen_counts(profile: dict[str, Any]) -> dict[str, Any]:
    try:
        status = status_of(profile)
    except (TaskError, OSError, ValueError):
        return {"waitingForHuman": None, "notRead": None, "holdsUnconfirmed": None}
    return {name: status.get(name) for name in ("waitingForHuman", "notRead", "holdsUnconfirmed")}


def handoff_state(profile: dict[str, Any]) -> dict[str, int]:
    """고친 판정 가운데 아직 원본 쪽으로 넘어가지 않은 수 — 첫 화면이 «반영해줘»를 권할지 가른다.
    원본이 상류(시트)면 건넨 목록(handed-out.jsonl)에 아직 없는 정정, 파일이면 원본 값이 아직 고른 값이 아닌 정정."""
    from gt_decisions import CHANGES, HANDOUT_LOG, gt_dir, load_gt_rows, read_jsonl_file

    task = load_task(profile)
    latest = current_answers(profile, task)
    changes = [(cell, entry) for cell, entry in latest.items() if entry["decision"] in CHANGES]
    if (task.get("gt") or {}).get("upstream"):
        handed = {row["decisionId"] for row in read_jsonl_file(gt_dir(profile) / HANDOUT_LOG) if row.get("kind") == "correct"}
        waiting = sum(1 for _, entry in changes if entry["decisionId"] not in handed)
    else:
        rows = dict(load_gt_rows(profile, task))
        fields = field_map(task)
        waiting = sum(1 for (key, field), entry in changes
                      if key in rows and field in fields and gt_value(task, fields[field], rows[key]) != entry.get("after"))
    return {"changesRecorded": len(changes), "toHandOff": waiting}


def home_rows() -> dict[str, Any]:
    """첫 화면(과제 목록)이 읽는 것. 수는 `status_of`가 센 그대로다 — 화면·스킬·목록이 한 셈을 쓴다.
    아무것도 쓰지 않는다. 과제를 모른다 — 이름·필드·원본 자리는 전부 프로필에서 온다."""
    rows = []
    for profile in task_profiles():
        task = profile["gtTask"]
        spec = task.get("gt") or {}
        upstream = spec.get("upstream") or {}
        try:
            status = status_of(profile)
            status.update(handoff_state(profile))
            problem = None
        except (TaskError, OSError, ValueError) as error:
            status, problem = {}, str(error)
        source = resolve(profile, spec) if spec.get("path") else None
        # 원본이 어디에 있고 판정이 어디로 가는가 — 운영팀이 «반영해줘» 뒤에 무엇이 일어날지 미리 안다.
        home = ("upstream" if upstream else
                "repo" if source is not None and _inside_project(source) else "file")
        rows.append({
            "task": profile["id"],
            "name": call_name(profile),
            "steps": flow_steps(profile),
            "changeMoment": change_moment(profile),
            "subject": profile.get("subjectName"),
            "attribute": profile.get("attributeName"),
            "fields": [field.get("name") or field["id"] for field in task.get("fields") or []],
            "gtHome": home,
            "gtHomeNote": upstream.get("note"),
            "problem": problem,
            # 정책·골든셋 두 장이 있는가 — 배치와 무관한 읽기 전용 화면이라, 아직 검수를 돌리지 않은 과제도 메뉴에 오른다.
            "pages": (review_root(profile) / "policy.html").is_file() and (review_root(profile) / "golden.html").is_file(),
            **{name: status.get(name) for name in (
                "prepared", "preparing", "failed", "preparedAt", "page", "remainingItems", "waitingForHuman",
                "holdsUnconfirmed", "alternativesUnconfirmed", "notRead", "staleOnPage", "heldOnThisPage", "decisions",
                "answeredOnPage", "holdsItems", "changesRecorded", "toHandOff")},
        })
    other_doors = [{"profile": profile["id"], "name": profile.get("displayName"), "subject": profile.get("subjectName"),
                    "attribute": profile.get("attributeName")}
                   for profile in all_profiles() if profile.get("gt") and not profile.get("gtTask")]
    return {"tasks": rows, "otherDoors": other_doors, "brokenProfiles": broken_profiles()}


def _inside_project(path: Path) -> bool:
    try:
        path.resolve().relative_to(PROJECT_ROOT)
        return True
    except ValueError:
        return False


def values_of(profile: dict[str, Any], only: str | None = None) -> dict[str, Any]:
    """정책의 값마다 GT 칸 수·판정 수·프로필에서 쓰인 자리. 값을 빼거나 이름을 바꾸기 전에 «무엇이 따라 움직이나»를 본다.
    아무것도 쓰지 않는다. GT 값은 legacy를 거친 뒤로 센다 — 옛 코드는 새 코드에 합쳐 센다."""
    task = load_task(profile)
    rows = load_gt(profile, task)
    latest = current_answers(profile, task)
    fields = [field for field in task["fields"] if only in (None, field["id"])]
    if not fields:
        raise TaskError(f"이 과제에 {only} 필드가 없습니다 — {', '.join(field['id'] for field in task['fields'])}")
    report = []
    for field in fields:
        counts: dict[str, int] = {}
        for row in rows.values():
            value = gt_value(task, field, row)
            for part in (value.split(MANY_SEPARATOR) if value and is_many(field) else [value] if value else []):
                counts[part] = counts.get(part, 0) + 1
        mine = [entry for (_, name), entry in latest.items() if name == field["id"]]
        used = {label: [] for label in field["labels"]}
        for old, new in (field.get("legacy") or {}).items():
            used.setdefault(new, []).append(f"legacy {old}→{new}")
        if field.get("unknownLabel"):
            used.setdefault(field["unknownLabel"], []).append("unknownLabel")
        for constraint in task.get("constraints") or []:
            for part in ("when", "require", "forbid"):
                wanted = (constraint.get(part) or {}).get(field["id"])
                for value in (wanted if isinstance(wanted, list) else [wanted] if wanted else []):
                    used.setdefault(str(value), []).append(f"constraints {constraint.get('id')}.{part}")
        report.append({
            "field": field["id"], "name": field.get("name"),
            "values": [{"code": label, "name": field["labelNames"].get(label), "gtCells": counts.get(label, 0),
                        "correctedTo": sum(1 for entry in mine if entry["decision"] == "CORRECT" and entry.get("after") == label),
                        "keptAs": sum(1 for entry in mine if entry["decision"] == "CONFIRM" and entry.get("before") == label),
                        "usedInProfile": used.get(label) or []}
                       for label in field["labels"]],
            "notInPolicy": {value: count for value, count in sorted(counts.items()) if not in_range(field, value)},
        })
    return {"task": profile["id"], "fields": report}


def cmd_values(args: argparse.Namespace) -> int:
    print(json.dumps(values_of(find_profile(args.task), args.field), ensure_ascii=False, indent=2))
    return 0


# 검수 문답 — 사람이 AI의 물음에 답하면 그 답은 곧 사례다. 따로 옮기는 단계 없이 정책 페이지와 다음 판독의 사례 목록(조회층)으로 간다.
# 정본은 원장(사람의 판정)이고, 그 판정이 어느 물음에 대한 답인지는 판정에 함께 남은 물음(`basedOn.ask`)이나,
# 화면이 그 칸에 붙여 보인 물음의 기록(`asked.jsonl`)에서 읽는다. 뒤엣것은 표준 물음 전의 판정을 잇기 위해 있다 —
# 화면(review.json)은 다음 배치에서 바뀌므로 «그때 무엇을 보였나»를 덧붙이기만 하는 기록으로 남겨야 이음이 끊기지 않는다.
ASKED_LOG = "asked.jsonl"


def record_asked(profile: dict[str, Any]) -> int:
    """지금 화면이 칸에 붙여 보인 물음을 `asked.jsonl`에 덧붙인다(같은 배치·키·칸은 한 번). CLI의 render만 부른다 — 서버는 쓰지 않는다."""
    from gt_decisions import gt_dir, locked, read_jsonl_file

    review = read_json(review_root(profile) / "review.json") or {}
    batch = review.get("batchId")
    if not batch:
        return 0
    fields = {field["id"]: field for field in review.get("fields") or []}
    path = gt_dir(profile) / ASKED_LOG
    rows = []
    with locked(profile):
        seen = {(r.get("batchId"), r.get("key"), r.get("field")) for r in read_jsonl_file(path)} if path.is_file() else set()
        for item in review.get("items") or []:
            shown = {cell["field"]: cell["ask"] for cell in item["cells"] if cell.get("ask")}
            if not shown:
                cell, text = legacy_ask(item, fields)
                if cell is not None:
                    shown = {cell["field"]: {"question": text, "here": "", "legacy": True}}
            for field_id, ask in shown.items():
                if (batch, item["key"], field_id) in seen:
                    continue
                rows.append({"batchId": batch, "key": item["key"], "field": field_id, "question": ask["question"],
                             "here": ask.get("here") or "", "kind": "legacy" if ask.get("legacy") else "standard",
                             "shownAt": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        if rows:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def answered_questions(profile: dict[str, Any], task: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """사람이 답한 AI의 물음 — 칸마다 지금 유효한 판정 하나. 정책 페이지(`/gt-qa`)와 다음 판독(`prepare`)이 같은 함수로 읽는다."""
    from gt_decisions import gt_dir, read_jsonl_file

    task = task or load_task(profile)
    fields = {field["id"]: field for field in task["fields"]}
    path = gt_dir(profile) / ASKED_LOG
    shown = {(r.get("batchId"), r.get("key"), r.get("field")): r for r in read_jsonl_file(path)} if path.is_file() else {}
    rows = []
    for (key, field_id), entry in effective(read_ledger(profile)).items():
        if entry["decision"] not in ("CORRECT", "CONFIRM") or field_id not in fields:
            continue
        ask = (entry.get("basedOn") or {}).get("ask") or shown.get((entry.get("batchId"), key, field_id)) or {}
        if not str(ask.get("question") or "").strip():
            continue
        field = fields[field_id]
        value = entry["after"] if entry["decision"] == "CORRECT" else entry["before"]
        rows.append({"id": f"QA-{entry['decisionId']}", "decisionId": entry["decisionId"], "key": key, "field": field_id,
                     "fieldName": field.get("name") or field_id, "question": ask["question"], "here": ask.get("here") or "",
                     "answer": value, "answerName": _value_name(field, value),
                     "reviewer": entry.get("reviewer"), "decidedAt": entry.get("decidedAt")})
    return sorted(rows, key=lambda row: (row["field"], row["decidedAt"] or ""))


# 규칙 — 사람이 AI의 물음에 답한 판정을 정책(정의 문서)의 그 칸 절 `### 규칙`으로 옮긴다. 정본은 사람이 확인한 **규칙 문장**이고
# AI의 물음은 그 규칙의 출처(`물음:`)로 남는다 — «물음 → 답»은 이번 상품에 붙은 말이라 다음 판독자에게 경계가 흐리다.
# 옮긴 규칙은 다음 판독자의 정책 파일(필수층)에 실린다. 대체된 규칙은 `## 보관`으로 옮겨 칸 절에는 적용 중인 규칙만 남는다.
# 옮기는 일은 사람이 고른 판정(`--add`)과 사람이 확인한 문장(`--rule`)으로, `--yes`가 있을 때만 한다. 서버는 부르지 않는다.
def _definitions_path(profile: dict[str, Any]) -> Path:
    spec = profile.get("gtTask") or {}
    return resolve(profile, {"path": spec["definitions"], "root": spec.get("definitionsRoot") or "project"})


def _policy(profile: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    return definition_policy(_definitions_path(profile), {f["id"]: [str(c) for c in f["labels"]] for f in task["fields"]},
                             {f["id"]: f.get("cardinality") == "many" for f in task["fields"]})


def qa_candidates(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """규칙으로 다듬을 거리 — 답한 물음(`answered_questions`)에, 이미 규칙의 `물음:`으로 들어갔는지를 붙인다."""
    task = load_task(profile)
    policy = _policy(profile, task)
    asked_in_policy = {(rule["field"], rule.get("물음")) for rule in
                       [*(r for rules in policy["rules"].values() for r in rules), *policy["archive"]] if rule.get("물음")}
    return [{**row, "inPolicy": (row["field"], row["question"]) in asked_in_policy} for row in answered_questions(profile, task)]


def _value_name(field: dict[str, Any], value: Any) -> str:
    names = field.get("labelNames") or {}
    return " + ".join(names.get(part, part) for part in str(value or "").split("|") if part) or "(빈칸)"


def _section_span(text: str, name: str) -> tuple[int, int] | None:
    heads = list(re.finditer(r"^##\s+(.+?)\s*$", text, re.M))
    for n, head in enumerate(heads):
        if head.group(1).strip() == name:
            return head.start(), heads[n + 1].start() if n + 1 < len(heads) else len(text)
    return None


def _append_to_block(text: str, section: str, block_head: str | None, lines: list[str]) -> str:
    """`## section` 안(`block_head`가 있으면 그 `###` 목록 끝)에 줄을 붙인다. 없으면 만든다(절은 문서 끝에)."""
    span = _section_span(text, section)
    chunk = "\n".join(lines)
    if span is None:
        body = f"## {section}\n\n" + (f"{block_head}\n\n" if block_head else "") + chunk + "\n"
        return text.rstrip("\n") + "\n\n" + body
    start, end = span
    body = text[start:end]
    if block_head:
        head = re.search(rf"^{re.escape(block_head)}\s*$", body, re.M)
        if not head:
            return text[:start] + body.rstrip("\n") + f"\n\n{block_head}\n\n{chunk}\n\n" + text[end:]
        rest = body[head.end():]
        cut = re.search(r"^###\s", rest, re.M)
        stop = head.end() + (cut.start() if cut else len(rest))
        tail = body[stop:]
        body = body[:stop].rstrip("\n") + "\n" + chunk + "\n" + ("\n" + tail.lstrip("\n") if tail.strip() else "\n")
        return text[:start] + body + text[end:]
    return text[:start] + body.rstrip("\n") + "\n" + chunk + "\n" + ("\n" if text[end:].strip() else "") + text[end:]


def _remove_rule(text: str, field_id: str, rule_id: str) -> tuple[str, list[str]]:
    """칸 절의 `### 규칙`에서 규칙 한 건(딸린 줄 포함)을 떼어 낸다. 뗀 줄을 돌려준다(보관으로 옮긴다)."""
    span = _section_span(text, field_id)
    body = text[span[0]:span[1]]
    lines = body.split("\n")
    for n, line in enumerate(lines):
        if line.startswith(f"- `{rule_id}` "):
            m = n + 1
            while m < len(lines) and re.match(r"^\s{2,}- ", lines[m]):
                m += 1
            taken = lines[n:m]
            body = "\n".join(lines[:n] + lines[m:])
            return text[:span[0]] + body + text[span[1]:], taken
    raise DecisionRejected(f"`## {field_id}`의 규칙에 {rule_id}가 없습니다 — 대체할 규칙 ID를 확인해 주세요.")


def _categories(profile: dict[str, Any], keys: list[str]) -> dict[str, str]:
    """근거 키의 맥락(상품 카테고리 같은) — 지금 화면(review.json)에 있을 때만. 범위 이름이 근거와 맞는지 볼 때 쓴다."""
    review = read_json(review_root(profile) / "review.json") or {}
    found = {}
    for item in review.get("items") or []:
        if item.get("key") in keys:
            found[item["key"]] = " › ".join(str(v) for v in (item.get("context") or {}).values())
    return found


def qa_add(profile: dict[str, Any], decision_ids: list[str], rule: str, reviewer: str, confirm: bool,
           scope: str | None = None, replace: str | None = None) -> dict[str, Any]:
    """고른 판정을 규칙 한 건으로 정책에 옮긴다. 한 번에 규칙 하나 — 같은 칸·같은 답의 판정만 묶는다.
    답이 갈린 판정을 한 규칙으로 묶지 않는다(정책이 스스로 모순된다)."""
    reviewer, rule = (reviewer or "").strip(), (rule or "").strip()
    if not reviewer:
        raise DecisionRejected("누가 정책에 넣기로 했는지 이름을 적어 주세요(--reviewer).")
    task = load_task(profile)
    wanted = set(decision_ids)
    picked = [row for row in qa_candidates(profile) if row["decisionId"] in wanted]
    missing = sorted(wanted - {row["decisionId"] for row in picked})
    if missing:
        raise DecisionRejected(f"AI 물음에 답한 유효한 판정이 아닙니다: {', '.join(missing)} — `qa --task`로 목록을 보세요.")
    if len({row["field"] for row in picked}) > 1:
        raise DecisionRejected("한 번에 한 칸의 규칙 하나만 넣습니다 — 칸이 다른 판정은 따로 넣어 주세요.")
    answers = {str(row["answer"]) for row in picked}
    if len(answers) > 1:
        raise DecisionRejected(f"고른 판정의 답이 갈렸습니다: {', '.join(sorted(answers))} — 한 답끼리만 골라 주세요.")
    if not rule:
        asked = " / ".join(dict.fromkeys(row["question"] for row in picked))
        raise DecisionRejected(f"정책에 넣을 규칙 문장을 --rule로 주세요. 물음: {asked} → 답: {picked[0]['answerName']}. "
                               "이 상품을 떠나서도 통하는 한 문장으로, 사람이 확인한 문장이어야 합니다.")
    field_id, first = picked[0]["field"], picked[0]
    if ((profile.get("gtTask") or {}).get("definitionsRoot") or "project") != "project":
        raise DecisionRejected("정의 문서가 이 저장소 밖에 있습니다 — 그 저장소에서 고쳐 주세요.")
    keys = list(dict.fromkeys(row["key"] for row in picked))
    notes = []
    if scope:
        seen = _categories(profile, keys)
        names = [name.strip() for name in scope.split(",") if name.strip()]
        for key in keys:
            path = seen.get(key)
            if path is None:
                notes.append(f"{key}의 카테고리를 지금 화면에서 찾지 못해 범위를 대조하지 못했습니다.")
            elif not any(name in [part.strip() for part in re.split(r"[›>]", path)] for name in names):
                raise DecisionRejected(f"범위 «{scope}»가 근거 {key}의 카테고리({path})의 어느 마디와도 맞지 않습니다.")
    path = _definitions_path(profile)
    policy = _policy(profile, task)
    rule_id = next_rule_id(policy, field_id)
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [f"- `{rule_id}` {rule} → `{first['answer']}`",
             f"  - 물음: {first['question']}",
             f"  - 출처: 검수 문답 · {today} · {reviewer} · {', '.join(row['decisionId'] for row in picked)}"]
    if scope:
        lines.append(f"  - 범위: {scope}")
    lines.append(f"  - 근거: {', '.join(keys)}")
    original = path.read_text(encoding="utf-8")
    version = re.search(r"^version:\s*(\d+)\s*$", original, re.M)
    new_version = int(version.group(1)) + 1 if version else None
    history = (f"- {today} · " + (f"v{new_version} · " if new_version else "") + f"{first['fieldName']} {rule_id} 추가"
               + (f", {replace} 대체" if replace else "") + f"({reviewer}) — 근거: {', '.join(keys)}")
    plan = {"definitions": relative_or_absolute(path), "rule": lines, "replaces": replace, "history": history,
            "notes": notes, "applied": False}
    text = original
    if replace:
        text, taken = _remove_rule(text, field_id, replace)
        taken[0] = taken[0].replace(f"- `{replace}` ", f"- `{field_id}/{replace}` ", 1)
        plan["archive"] = taken + [f"  - 대체: {rule_id} · {today} · {reviewer}"]
    if not confirm:
        return plan
    text = _append_to_block(text, field_id, "### 규칙", lines)
    if replace:
        text = _append_to_block(text, "보관", None, plan["archive"])
    _commit_policy(profile, path, original, text, history, new_version, today)
    plan["applied"] = True
    return plan


def _commit_policy(profile: dict[str, Any], path: Path, original: str, text: str, history: str,
                   new_version: int | None, today: str) -> None:
    """고친 정책을 쓴다 — 변경 이력 한 줄, 머리의 version·updatedAt. 쓴 뒤 정책 전체를 다시 읽어 로더가 멈추는 모양이면 되돌린다.
    규칙을 넣는 문(qa·rule)은 모두 여기를 지난다 — 문이 둘이면 이력이 한쪽에만 남는다."""
    text = _append_to_block(text, "변경 이력", None, [history])
    if new_version:
        text = re.sub(r"^version:\s*\d+\s*$", f"version: {new_version}", text, count=1, flags=re.M)
    text = re.sub(r"^updatedAt:.*$", f"updatedAt: {today}", text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")
    try:
        load_task(profile)
    except TaskError:
        path.write_text(original, encoding="utf-8")
        raise


# 규칙을 사람이 직접 쓴다 — 문답에서 오지 않은 규칙(더하기), 있는 규칙 고치기, 빼기. 고치기와 빼기도 줄을 지우지 않는다:
# 옛 규칙은 `## 보관`으로 가고 `대체:`가 무엇으로 바뀌었는지(또는 폐지) 남긴다 — 옛 판정이 어느 규칙 위에 섰는지 되짚을 수 있어야 한다.
# 고친 규칙은 새 ID를 받는다. 같은 ID의 문장이 바뀌면 «R3을 따랐다»는 옛 판독이 무엇을 따랐는지 모르게 된다.
RULE_ACTIONS = ("add", "edit", "retire")


def rule_change(profile: dict[str, Any], action: str, field_id: str, reviewer: str, confirm: bool,
                text: str | None = None, value: str | None = None, scope: str | None = None,
                rule_id: str | None = None, reason: str | None = None) -> dict[str, Any]:
    reviewer = (reviewer or "").strip()
    if action not in RULE_ACTIONS:
        raise DecisionRejected(f"규칙 작업은 {' · '.join(RULE_ACTIONS)} 가운데 하나입니다.")
    if not reviewer:
        raise DecisionRejected("누가 정책을 고치는지 이름을 적어 주세요(--reviewer).")
    if ((profile.get("gtTask") or {}).get("definitionsRoot") or "project") != "project":
        raise DecisionRejected("정의 문서가 이 저장소 밖에 있습니다 — 그 저장소에서 고쳐 주세요.")
    task = load_task(profile)
    fields = field_map(task)
    if field_id not in fields:
        # 이름으로 불러도 받는다 — 운영팀은 칸 이름으로 말한다.
        by_name = {str(spec.get("name")): fid for fid, spec in fields.items() if spec.get("name")}
        if field_id not in by_name:
            raise DecisionRejected(f"칸 «{field_id}»이 이 과제에 없습니다 — {', '.join(fields)} 가운데 하나입니다.")
        field_id = by_name[field_id]
    field = fields[field_id]
    path = _definitions_path(profile)
    policy = _policy(profile, task)
    active = {rule["id"]: rule for rule in policy["rules"].get(field_id, [])}
    today = datetime.now(timezone.utc).date().isoformat()
    original = path.read_text(encoding="utf-8")
    version = re.search(r"^version:\s*(\d+)\s*$", original, re.M)
    new_version = int(version.group(1)) + 1 if version else None
    stamp = f"- {today} · " + (f"v{new_version} · " if new_version else "")
    name = field.get("name") or field_id

    def check_value(code: str | None) -> str | None:
        if not code:
            return None
        parts = [part for part in code.split(MANY_SEPARATOR) if part] if is_many(field) else [code]
        odd = [part for part in parts if part not in [str(label) for label in field["labels"]]]
        if odd:
            raise DecisionRejected(f"«{', '.join(odd)}»은 {name}의 허용값이 아닙니다 — "
                                   f"{', '.join(str(label) for label in field['labels'])} 가운데서 고릅니다(값을 더하려면 «값 추가»).")
        return MANY_SEPARATOR.join(parts)

    body = original
    plan: dict[str, Any] = {"definitions": relative_or_absolute(path), "action": action, "field": field_id, "applied": False}
    if action == "add":
        if not (text or "").strip():
            raise DecisionRejected("넣을 규칙 문장을 --text로 주세요. 이 상품을 떠나서도 통하는 한 문장이어야 합니다.")
        new_id = next_rule_id(policy, field_id)
        code = check_value(value)
        lines = [f"- `{new_id}` {text.strip()}" + (f" → `{code}`" if code else ""),
                 f"  - 출처: 직접 작성 · {today} · {reviewer}"]
        if (scope or "").strip():
            lines.append(f"  - 범위: {scope.strip()}")
        body = _append_to_block(body, field_id, "### 규칙", lines)
        plan.update(rule=lines, ruleId=new_id)
        history = stamp + f"{name} {new_id} 추가 — 직접 작성({reviewer})"
    else:
        if not rule_id or rule_id not in active:
            raise DecisionRejected(f"{name}의 적용 중인 규칙에 «{rule_id}»가 없습니다 — "
                                   + (f"지금 규칙: {', '.join(active)}" if active else "지금 규칙이 없습니다") + ".")
        old = active[rule_id]
        body, taken = _remove_rule(body, field_id, rule_id)
        taken[0] = taken[0].replace(f"- `{rule_id}` ", f"- `{field_id}/{rule_id}` ", 1)
        if action == "edit":
            new_id = next_rule_id(policy, field_id)
            new_text = (text or "").strip() or old["text"]
            code = old.get("value") if value is None else check_value(value)
            new_scope = old.get("범위") if scope is None else scope.strip()
            if (new_text, code, new_scope or None) == (old["text"], old.get("value"), old.get("범위") or None):
                raise DecisionRejected(f"{rule_id}에서 바뀌는 것이 없습니다 — 문장(--text)·값(--value)·범위(--scope) 중 하나를 바꿔 주세요.")
            lines = [f"- `{new_id}` {new_text}" + (f" → `{code}`" if code else "")]
            if old.get("물음"):
                lines.append(f"  - 물음: {old['물음']}")  # 이 규칙을 낳은 물음은 고쳐도 같다 — 사례와 규칙을 잇는 줄이다
            lines.append(f"  - 출처: 직접 작성 · {today} · {reviewer} · {rule_id} 고침")
            if new_scope:
                lines.append(f"  - 범위: {new_scope}")
            if old.get("근거"):
                lines.append(f"  - 근거: {old['근거']}")
            archive = taken + [f"  - 대체: {new_id} · {today} · {reviewer}"]
            body = _append_to_block(body, field_id, "### 규칙", lines)
            plan.update(rule=lines, ruleId=new_id, replaces=rule_id)
            history = stamp + f"{name} {new_id} 추가, {rule_id} 대체 — 직접 고침({reviewer})"
        else:
            if not (reason or "").strip():
                raise DecisionRejected("규칙을 빼는 이유를 --reason으로 주세요 — 보관에 남아 다음 사람이 읽습니다.")
            archive = taken + [f"  - 대체: 없음(폐지) · {today} · {reviewer} · {reason.strip()}"]
            plan.update(retired=rule_id)
            history = stamp + f"{name} {rule_id} 폐지 — {reason.strip()}({reviewer})"
        body = _append_to_block(body, "보관", None, archive)
        plan["archive"] = archive
    plan["history"] = history
    if not confirm:
        return plan
    _commit_policy(profile, path, original, body, history, new_version, today)
    plan["applied"] = True
    return plan


def rule_list(profile: dict[str, Any]) -> dict[str, Any]:
    task = load_task(profile)
    policy = _policy(profile, task)
    fields = field_map(task)
    return {"task": profile["id"], "definitions": relative_or_absolute(_definitions_path(profile)),
            "fields": [{"field": fid, "name": spec.get("name") or fid,
                        "rules": [{k: v for k, v in rule.items() if k != "field"} for rule in policy["rules"].get(fid, [])]}
                       for fid, spec in fields.items()],
            "archive": policy["archive"]}


def cmd_rule(args: argparse.Namespace) -> int:
    profile = find_profile(args.task)
    if not args.action:
        print(json.dumps(rule_list(profile), ensure_ascii=False, indent=2))
        return 0
    if not args.field:
        raise DecisionRejected("어느 칸의 규칙인지 --field로 주세요(칸 ID나 이름).")
    print(json.dumps(rule_change(profile, args.action, args.field, args.reviewer or "", args.yes, text=args.text,
                                 value=args.value, scope=args.scope, rule_id=args.id, reason=args.reason),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_qa(args: argparse.Namespace) -> int:
    profile = find_profile(args.task)
    if args.add:
        print(json.dumps(qa_add(profile, args.add, args.rule or "", args.reviewer or "", args.yes, args.scope, args.replace),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"task": profile["id"], "answered": qa_candidates(profile)}, ensure_ascii=False, indent=2))
    return 0

def cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(status_of(find_profile(args.task)), ensure_ascii=False, indent=2))
    return 0


def status_of(profile: dict[str, Any]) -> dict[str, Any]:
    """«얼마나 남았어»·과제 고르기가 읽는 수. 화면(/gt-decided·progress())과 같은 규칙으로 센다."""
    root = review_root(profile)
    latest = current_answers(profile)
    worklist = read_json(root / "worklist.json") or {}
    status: dict[str, Any] = {
        "task": profile["id"],
        "prepared": bool(worklist),
        "decisions": {name: sum(1 for entry in latest.values() if entry["decision"] == name) for name in DECISIONS},
        "page": f"/gt/{profile['id']}",
        "remainingItems": (worklist.get("counts") or {}).get("remainingItems"),
    }
    manifest = read_json(root / "manifest.json") or {}
    # 준비는 됐는데 화면이 아직 없다 — 판독이 도는 중이거나, 도는 중에 세션이 끊겼다. 스킬이 가를 수 있게 알린다.
    status["preparing"] = bool(manifest.get("preparing"))
    status["failed"] = bool(manifest.get("failed"))
    status["preparedAt"] = manifest.get("preparedAt") or str(manifest.get("batchId") or "")[:25] or None
    review = read_json(root / "review.json")
    if review:
        # 사람이 누를 칸만 센다. «AI가 GT와 같게 봄»(판독이 GT와 같음)과 «판독이 돌아오지 않음»은 누를 칸이 아니다.
        actionable = ("FIX_PROPOSED", "FILL_PROPOSED", "CONTESTED", "NEEDS_HUMAN_LOOK", "NO_EVIDENCE")
        cells = [cell for item in review.get("items") or [] for cell in item.get("cells") or []
                 if cell["status"] in actionable]
        batch = review.get("batchId")
        # 지금 원본의 값 — 화면(/gt-decided)과 같게 본다. 화면을 그린 뒤 원본이 바뀐 칸은 이 화면에서 답할 수 없다.
        task = load_task(profile)
        rows = load_gt(profile, task)
        fields = field_map(task)

        def now(cell: dict[str, Any]) -> str | None:
            row = rows.get(cell["key"])
            return gt_value(task, fields[cell["field"]], row) if row is not None and cell["field"] in fields else cell.get("current")

        def answered(cell: dict[str, Any]) -> bool:
            # 화면과 같은 함수(answered_on_page). 보류는 **이 배치에서** 누른 것만 이 화면의 답이다.
            return answered_on_page(latest.get((cell["key"], cell["field"])), now(cell), batch,
                                    reasked(cell))

        def stale(cell: dict[str, Any]) -> bool:
            return now(cell) != cell.get("current") and not answered(cell)

        every = [cell for item in review.get("items") or [] for cell in item.get("cells") or []]
        # 화면이 잠그는 칸(.stale)과 같게 — 누를 칸만이 아니라 «AI가 GT와 같게 봄»·«못 읽음» 칸도 원본이 바뀌면 이 화면에서 답할 수 없다.
        status["staleOnPage"] = sum(1 for cell in every if stale(cell))
        # 반론이 안 온 칸(noDefense)은 «못 읽음»(notRead)으로만 센다 — 두 수에 겹쳐 세면 사람에게 할 일을 두 번 말한다.
        waiting = [cell for cell in cells if not answered(cell) and not stale(cell) and not cell.get("noDefense")]
        status["heldOnThisPage"] = sum(1 for item in review.get("items") or [] for cell in item.get("cells") or []
                                       if (latest.get((cell["key"], cell["field"])) or {}).get("decision") == "HOLD"
                                       and (latest.get((cell["key"], cell["field"])) or {}).get("batchId") == batch)
        status["waitingForHuman"] = len(waiting)
        # AI가 GT와 같게 본 칸 가운데 아직 «유지»를 누르지 않은 칸. 할 일로 세지는 않지만, 둘 다 0이어야 «다 했다».
        # AI가 GT와 같게 본 칸은 누를 것이 없다(화면이 그리지 않는다). 대체 정답을 낸 칸만 사람이 «지금 GT»를 골라야 한다.
        holds = [cell for item in review.get("items") or [] for cell in item.get("cells") or []
                 if cell["status"] == "GT_HOLDS" and cell.get("alternative") and not answered(cell) and not stale(cell)]
        status["holdsUnconfirmed"] = sum(1 for cell in holds if not cell.get("alternative"))
        # 대체 정답을 낸 칸 — 묶음 유지에서 빠지므로 칸마다 «지금 GT가 맞다»를 눌러야 한다.
        status["alternativesUnconfirmed"] = sum(1 for cell in holds if cell.get("alternative"))
        status["waitingByStatus"] = {name: sum(1 for cell in waiting if cell["status"] == name) for name in actionable}
        # AI가 못 읽은 칸(판독 또는 반론이 돌아오지 않음) 가운데 사람이 아직 답하지 않은 칸 — 화면의 셈과 같다.
        status["notRead"] = sum(1 for item in review.get("items") or [] for cell in item.get("cells") or []
                                if (cell["status"] == "NOT_READ" or cell.get("noDefense"))
                                and not answered(cell) and not stale(cell))  # 원본이 바뀐 칸은 다시 읽어도 이 화면에서 못 답한다
        # 첫 화면이 «시작하기»와 «이어서»를 가르고, «유지»를 몇 번 눌러야 끝나는지(상품 수) 말하는 데 쓴다.
        status["answeredOnPage"] = sum(1 for cell in cells if answered(cell))
        status["holdsItems"] = sum(1 for item in review.get("items") or []
                                   if False)  # 묶음 유지 버튼이 없어졌다 — 누를 횟수는 늘 0
    status["definitionGaps"] = definition_gaps(profile, latest)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tasks").set_defaults(run=cmd_tasks)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--task", required=True)
    prepare.add_argument("--limit", type=int, help="사람 앞에 놓을 건수. 0이면 전부")
    prepare.add_argument("--key", action="append", help="이 키만. 반복 가능")
    prepare.add_argument("--reread", action="store_true", help="지금 화면을 두고 못 읽은 건만 다시 판독한다")
    prepare.add_argument("--force", action="store_true", help="판독이 도는 중이라도 새 배치를 만든다(죽은 배치를 버릴 때만)")
    prepare.set_defaults(run=cmd_prepare)
    finish = sub.add_parser("finish")
    finish.add_argument("--task", required=True)
    finish.add_argument("--from", dest="source", required=True, help="워크플로우 출력 파일(감싼 모양이어도 된다)")
    finish.set_defaults(run=cmd_finish)
    render = sub.add_parser("render")
    render.add_argument("--task", required=True)
    render.add_argument("--sweep", help="워크플로우 반환값 JSON. 기본은 gt-review/sweep-raw.json")
    render.set_defaults(run=cmd_render)
    rec = sub.add_parser("record")
    rec.add_argument("--task", required=True)
    rec.add_argument("--key", required=True)
    rec.add_argument("--field", required=True)
    rec.add_argument("--decision", required=True, choices=("CORRECT", "CONFIRM", "LEAVE_EMPTY", "CLEAR", "HOLD"))
    rec.add_argument("--reviewer", required=True)
    rec.add_argument("--value", help="값 여럿 필드는 «A|B»")
    rec.add_argument("--reason")
    rec.add_argument("--expect", help="사람이 본 지금 GT 값. 다르면 거절한다")
    rec.add_argument("--expect-empty", action="store_true", help="사람이 본 GT가 빈칸이었다")
    rec.add_argument("--gap", action="store_true", help="정의 문서가 다루지 않는 경계 위의 판정이다")
    rec.add_argument("--ask", help="이 판정이 답한 AI의 물음(화면의 «AI가 묻는 것» 문장)")
    rec.set_defaults(run=cmd_record)
    for name, runner in (("export", cmd_export), ("status", cmd_status)):
        command = sub.add_parser(name)
        command.add_argument("--task", required=True)
        command.set_defaults(run=runner)
    pages = sub.add_parser("pages", help="정책·골든셋 두 장만 쓴다(배치가 없어도 된다)")
    pages.add_argument("--task", required=True)
    pages.set_defaults(run=cmd_pages)
    vals = sub.add_parser("values", help="정책의 값마다 GT 칸·판정 수 — 값을 빼거나 바꾸기 전에 본다. 아무것도 쓰지 않는다")
    vals.add_argument("--task", required=True)
    vals.add_argument("--field")
    vals.set_defaults(run=cmd_values)
    qa = sub.add_parser("qa", help="AI 물음에 사람이 답한 판정을 보고, 고른 것을 정책의 규칙으로 옮긴다")
    qa.add_argument("--task", required=True)
    qa.add_argument("--add", action="append", help="정책에 옮길 판정 ID(GTD-…). 반복 가능. 없으면 목록만 본다")
    qa.add_argument("--reviewer", help="옮기기로 한 사람")
    qa.add_argument("--rule", help="정책에 넣을 규칙 문장(사람이 확인한 것). 이 상품을 떠나서도 통하는 한 문장")
    qa.add_argument("--scope", help="이 규칙이 걸리는 상품 카테고리 이름(쉼표로 여럿). 없으면 모든 상품")
    qa.add_argument("--replace", help="이 규칙이 대체하는 같은 칸의 규칙 ID(R0 같은) — 보관으로 옮긴다")
    qa.add_argument("--yes", action="store_true", help="사람이 확인했다. 없으면 무엇을 넣을지만 말한다")
    qa.set_defaults(run=cmd_qa)
    rule = sub.add_parser("rule", help="정책의 규칙을 보고, 사람이 직접 더하거나(add) 고치거나(edit) 뺀다(retire)")
    rule.add_argument("action", nargs="?", choices=RULE_ACTIONS, help="없으면 지금 규칙 목록만 본다")
    rule.add_argument("--task", required=True)
    rule.add_argument("--field", help="칸 ID나 칸 이름")
    rule.add_argument("--id", help="고치거나 뺄 규칙 ID(R1 같은)")
    rule.add_argument("--text", help="규칙 문장. 이 상품을 떠나서도 통하는 한 문장(edit에서 없으면 그대로)")
    rule.add_argument("--value", help="규칙이 가리키는 허용값 코드. 값 여럿 필드는 «A|B». edit에서 «»(빈 문자열)이면 값을 뗀다")
    rule.add_argument("--scope", help="걸리는 상품 카테고리 이름(쉼표로 여럿). edit에서 «»(빈 문자열)이면 모든 상품")
    rule.add_argument("--reason", help="retire의 이유 — 보관에 남는다")
    rule.add_argument("--reviewer", help="고치는 사람")
    rule.add_argument("--yes", action="store_true", help="사람이 확인했다. 없으면 무엇이 바뀌는지만 말한다")
    rule.set_defaults(run=cmd_rule)
    app = sub.add_parser("apply")
    app.add_argument("--task", required=True)
    app.add_argument("--yes", action="store_true", help="사람이 확인했다. 없으면 무엇이 바뀌는지만 말한다")
    app.set_defaults(run=cmd_apply)
    pub = sub.add_parser("publish", help="고친 GT와 판정 원장을 새 브랜치로 올리고 PR을 연다")
    pub.add_argument("--task", required=True)
    pub.add_argument("--base", help="PR의 기준 브랜치. 없으면 지금 브랜치가 따라가는 원격 브랜치")
    pub.add_argument("--yes", action="store_true", help="사람이 확인했다. 없으면 무엇을 올릴지만 말한다")
    pub.add_argument("--no-pr", action="store_true", help="브랜치만 올리고 PR은 열지 않는다")
    pub.set_defaults(run=cmd_publish)
    args = parser.parse_args()
    try:
        return args.run(args)
    except (TaskError, DecisionRejected) as error:
        print(f"멈춤: {error}", file=sys.stderr)
        return 2
    except FileNotFoundError as error:
        print(f"멈춤: 파일을 찾지 못했습니다: {error.filename}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as error:
        print(f"멈춤: 파일을 읽지 못했습니다: {error}", file=sys.stderr)
        return 2
    except (ValueError, TypeError, KeyError) as error:
        # 설정 값이 틀린 경우(타일 규칙 이름·열 모양 같은). 파이썬 오류 대신 한 줄로 멈춘다 — 스킬은 «그 밖의 멈춤»으로 옮긴다.
        print(f"멈춤: 설정 값이 올바르지 않습니다: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
