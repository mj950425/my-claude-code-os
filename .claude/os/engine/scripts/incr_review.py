#!/usr/bin/env python3
"""증분 검수 — 새로 들어온 데이터(라벨 없음)를 AI가 먼저 읽고, 데이터 운영팀은 클릭으로 확정한다. 속성을 모른다.

    python3 incr_review.py push   --task <id> --file <증분.jsonl> [--images <사진 색인>] [--name <이름>] [--no-run]
    python3 incr_review.py run    --task <id> [--batch <묶음>] [--reread]   # AI 추론(워크플로우 read 단계)까지 끝까지 돈다
    python3 incr_review.py record --task <id> --batch <묶음> --key K --field F --value V --reviewer 이름 --expect-ai <본 AI 값>   # 말로 받은 판정
    python3 incr_review.py export --task <id> --batch <묶음>                 # 확정한 줄을 라벨 붙은 파일로
    python3 incr_review.py status --task <id> [--batch <묶음>]
    python3 incr_review.py tasks                                            # 첫 화면·메뉴가 읽는 목록

## 골든셋 검수와 무엇이 같고 무엇이 다른가

같은 것 — **과제 선언**(프로필 `gtTask`: 키·필드·사진·맥락)과 **정책**(정의 문서의 허용값·규칙·검수 문답), 그리고
**판독자**(`gt-blind-reader`, 워크플로우 `gt-review.js`의 `read` 단계)와 **잠금**(`gt_next`, 워크플로우는 한 번에 하나).
새 과제를 따로 선언하지 않는다 — 골든셋 검수를 올린 과제는 곧바로 증분도 받는다. 판독 기준이 둘로 갈라지지 않게.

다른 것 — 증분에는 GT가 없다. 그래서
- **모든 줄의 모든 칸이 후보다.** 신호(모순·범위 밖·참고 등급)로 고르지 않는다.
- **반론 단계가 없다.** 지킬 GT가 없다. AI의 값이 곧 제안이고, 사람이 누르는 버튼이 곧 검증이다.
- **원장이 GT 원장과 따로 있다** — `.claude/incr/<과제>/decisions.jsonl`. 증분의 답은 GT를 고치는 판정이 아니다(규칙 5의
  «한 GT에 원장은 하나»를 건드리지 않는다). `runs/`에 두지 않는 이유는 GT 원장과 같다 — 지워도 되는 자리에 사람의 답을 두지 않는다.

## 어디에 무엇이 있나

    .claude/incr/<과제>/batches/<묶음>/input.jsonl   밀어넣은 원본 그대로(사람이 무엇을 검수했는지의 기록 — 지우지 않는다)
    .claude/incr/<과제>/batches/<묶음>/batch.json    언제·어디서 밀어넣었나, 사진 색인을 따로 받았나
    .claude/incr/<과제>/batches/<묶음>/images.jsonl  (선택) 증분 전용 사진 색인. 없으면 과제의 사진 색인(gtTask.images)을 쓴다
    .claude/incr/<과제>/decisions.jsonl             사람 판정 원장. 덧붙이기만 한다(고치면 새 줄이 supersedes로 옛 줄을 가리킨다)
    .claude/incr/<과제>/labeled/<묶음>.jsonl         파생물 — 모든 칸을 확정한 줄만, 라벨을 채워. 지워도 export가 다시 만든다
    runs/<과제>/incr/<묶음>/                         파생물 — 사진(images/), 판독자 몫(reader/), worklist.json, readings.json(AI 추론)

## AI 추천만으로는 원장에 한 줄도 생기지 않는다

AI의 판독은 `readings.json`(파생물)에 있다. 원장에는 사람이 누른 판정만 가고, 그 줄에 **그 사람이 본** AI 값(`aiValue` — 화면이 보낸
`expectedAi`와 지금 판독이 같을 때만 받는다)과 둘이 같았는지(`agreedWithAi`)를 곁들인다 —
«AI가 맞힌 비율»은 사람이 확인한 칸에서만 센다. 한 건의 «AI 값으로 모두 확정»도 사람이 누른 판정이다(`channel: screen-bulk`).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from catalog_profile import PROJECT_ROOT, output_root, relative_or_absolute
from gt_task import (MANY_SEPARATOR, TaskError, export_value, field_map, in_range, is_many, key_fields, key_of, load_task, normalize,
                     read_jsonl)

SCHEMA = "incr-review-v1"
DECISIONS = ("LABEL", "HOLD")
LEDGER = "decisions.jsonl"
BATCH_NAME = re.compile(r"[^0-9A-Za-z가-힣_-]+")


class IncrRejected(ValueError):
    """사람에게 그대로 보일 거절 문장."""


# ---------------------------------------------------------------- 자리


def incr_home(profile: dict[str, Any]) -> Path:
    # 테스트가 진짜 원장 자리를 건드리지 않도록 뿌리만 바꿀 수 있다(gt_decisions.gt_dir와 같은 방식).
    base = Path(os.environ["CATALOG_OS_INCR_ROOT"]) if os.environ.get("CATALOG_OS_INCR_ROOT") else PROJECT_ROOT / ".claude" / "incr"
    return base / str(profile["id"])


def batch_home(profile: dict[str, Any], batch: str) -> Path:
    return incr_home(profile) / "batches" / batch


def work_dir(profile: dict[str, Any], batch: str) -> Path:
    return output_root(profile) / "incr" / batch


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


@contextlib.contextmanager
def _locked(profile: dict[str, Any]) -> Iterator[None]:
    """원장 덧붙이기 잠금. 화면 버튼 두 개가 같이 와도 판정 번호가 겹치지 않게."""
    home = incr_home(profile)
    home.mkdir(parents=True, exist_ok=True)
    with (home / ".lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def find_profile(value: str) -> dict[str, Any]:
    from gt_review import find_profile as find

    return find(value)


def task_profiles() -> list[dict[str, Any]]:
    from gt_review import task_profiles as profiles

    return profiles()


def batches(profile: dict[str, Any]) -> list[str]:
    """밀어넣은 묶음, 최근 것이 먼저. 이름이 시각으로 시작하므로 이름 순서가 곧 시간 순서다."""
    root = incr_home(profile) / "batches"
    return sorted((path.name for path in root.iterdir() if (path / "input.jsonl").is_file()), reverse=True) if root.is_dir() else []


def pick_batch(profile: dict[str, Any], batch: str | None) -> str:
    found = batches(profile)
    if batch:
        if batch not in found:
            raise IncrRejected(f"그런 증분 묶음이 없습니다: {batch}")
        return batch
    if not found:
        raise IncrRejected("아직 밀어넣은 증분이 없습니다 — incr_review.py push로 넣어 주세요.")
    return found[0]


# ---------------------------------------------------------------- 입력


def input_rows(profile: dict[str, Any], task: dict[str, Any], batch: str) -> dict[str, dict[str, Any]]:
    keys = key_fields(task["keyField"])
    rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(batch_home(profile, batch) / "input.jsonl"):
        key = key_of(row, keys)
        if key is not None:
            rows[key] = row
    return rows


def batch_task(profile: dict[str, Any], task: dict[str, Any], batch: str) -> dict[str, Any]:
    """이 묶음의 사진은 어디서 오나. 묶음에 사진 색인을 따로 받았으면 과제 선언의 모양(키·열·역할·파일 기준)은 그대로 두고 색인 파일만 바꾼다.
    색인 안의 상대 파일 경로는 색인 자리가 아니라 과제 선언의 `fileRoot`·`fileBase` 기준이다(계약에 적었다)."""
    own = batch_home(profile, batch) / "images.jsonl"
    return _with_images(task, own) if own.is_file() else task


def _with_images(task: dict[str, Any], path: Path) -> dict[str, Any]:
    return {**task, "images": {**task["images"], "path": str(path.resolve()), "root": "project"}}


def push(profile: dict[str, Any], source: Path, images: Path | None = None, name: str | None = None) -> dict[str, Any]:
    """증분 한 묶음을 받는다. 멈출 수 있는 검사는 쓰기 전에 다 한다 — 반쯤 들어간 묶음을 남기지 않는다."""
    from gt_images import ImageIndex

    task = load_task(profile)
    source = source.resolve()
    if not source.is_file():
        raise IncrRejected(f"파일이 없습니다: {source}")
    keys = key_fields(task["keyField"])
    rows: dict[str, dict[str, Any]] = {}
    for number, row in enumerate(read_jsonl(source), 1):
        key = key_of(row, keys)
        if key is None:
            raise IncrRejected(f"{number}번째 줄에 키({', '.join(keys)})가 없습니다.")
        if key in rows:
            raise IncrRejected(f"키가 겹칩니다: {key} ({number}번째 줄). 한 줄이 한 건이어야 합니다.")
        rows[key] = row
    if not rows:
        raise IncrRejected("빈 파일입니다.")
    if images is not None:
        if not task.get("images"):
            raise IncrRejected("이 과제는 사진을 선언하지 않았습니다 — 사진 색인(--images)을 받을 자리가 없습니다.")
        if not images.resolve().is_file():
            raise IncrRejected(f"사진 색인이 없습니다: {images}")
    # 사진 색인을 쓰기 **전에** 읽어 본다 — 키 겹침·없는 파일 같은 멈춤이 묶음을 반쯤 만든 뒤에 나지 않게.
    index = ImageIndex(profile, _with_images(task, images) if images is not None else task)
    joined = sum(1 for row in rows.values() if index.entries(row)) if index.has_photos else None
    # 결과 파일은 밀어넣은 줄에 라벨 열과 출처 열(<열>Source)을 채운다 — 이미 값이 있는 열이면 덮어쓰게 되므로 알린다(막지는 않는다).
    columns = [str(spec.get("gtField") or fid) for fid, spec in field_map(task).items()]
    filled = sorted({column for row in rows.values() for column in columns
                     if row.get(column) not in (None, "", []) or row.get(f"{column}Source") not in (None, "")})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    label = BATCH_NAME.sub("-", name or source.stem).strip("-")[:40] or "batch"
    batch = f"{stamp}-{label}"
    home = batch_home(profile, batch)
    if home.exists():
        raise IncrRejected(f"같은 이름의 묶음이 이미 있습니다: {batch} — 잠시 뒤 다시 넣어 주세요.")
    try:
        home.mkdir(parents=True)
        shutil.copyfile(source, home / "input.jsonl")
        if images is not None:
            shutil.copyfile(images.resolve(), home / "images.jsonl")
        _write_json(home / "batch.json", {"schemaVersion": SCHEMA, "profileId": profile["id"], "batchId": batch,
                                          "pushedAt": _now(), "source": relative_or_absolute(source), "rows": len(rows),
                                          "ownImages": images is not None})
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    return {"ok": True, "task": profile["id"], "batch": batch, "rows": len(rows), "rowsWithPhotos": joined,
            "answerColumnsAlreadyFilled": filled, "screen": f"/incr?task={profile['id']}&batch={batch}"}


# ---------------------------------------------------------------- AI 추론


def _item_id(number: int) -> str:
    return f"IN-{number:03d}"


def _complete(reading: dict[str, Any] | None, fields: list[str]) -> bool:
    """판독이 모든 칸을 말했는가. «가를 수 없음»(빈 값)도 말한 것이다 — 칸이 통째로 빠진 판독만 다시 읽을 거리다."""
    cells = (reading or {}).get("cells") or {}
    return all(field in cells for field in fields)


def prepare(profile: dict[str, Any], batch: str, reread: bool = False) -> dict[str, Any] | None:
    """사진·판독자 파일을 준비하고 워크플로우 인자를 돌려준다. 볼 건이 없으면 None.

    - 처음(또는 `reread` 없이 다시): 사진을 새로 준비하고, **아직 확정하지 않은 건**의 지난 판독은 버리고 다시 읽는다 —
      사진을 다시 만들면 지난 판독의 근거 사진 번호(P01…)가 다른 사진을 가리킬 수 있고, 정책이 바뀌었으면 새로 읽어야 한다.
    - `reread`: 지금 사진을 두고, 판독이 없거나 모자란(칸이 빠진) 건만.

    판독자 파일은 골든셋 검수와 **같은 함수**(`gt_review.emit_workflow_args`)가 쓴다 — 눈가림(무작위 이름·키 없음)과
    판독 파일의 모양이 한 벌이어야 판독자가 두 문에서 같은 일을 한다. 증분에는 GT가 없으므로 칸의 «지금 값»은 모두 빈칸이다.
    준비마다 새 `runId`를 매기고 `finish`는 그 값이 맞는 결과만 받는다 — 지난 실행의 출력이 이번 판독으로 붙지 않게."""
    from gt_images import ImageIndex, prepare as prepare_evidence, require_pillow
    import gt_review

    task = load_task(profile)
    rows = input_rows(profile, task, batch)
    work = work_dir(profile, batch)
    worklist = _read_json(work / "worklist.json") or {}
    readings = _read_json(work / "readings.json", {}) or {}
    fields = list(field_map(task))
    done = done_keys(effective_decisions(profile, task, batch), rows, fields)
    if reread and worklist.get("items"):
        items = [item for item in worklist["items"]
                 if item["key"] not in done and not _complete(readings.get(item["key"]), fields) and not item.get("noEvidence")]
    else:
        index = ImageIndex(profile, batch_task(profile, task, batch))
        if index.has_photos:
            require_pillow()
        shutil.rmtree(work / "reader", ignore_errors=True)
        shutil.rmtree(work / "images", ignore_errors=True)
        stamp = f"{batch}-{secrets.token_hex(4)}"  # 사진 폴더 이름(opaque)의 씨앗 — 판독자가 사진 폴더로 키를 짐작하지 못하게
        items = []
        for number, (key, row) in enumerate(rows.items(), 1):
            evidence = prepare_evidence(index, row, key, work / "images", output_root(profile), stamp)
            items.append({"id": _item_id(number), "key": key,
                          "title": row.get(task["titleField"]) if task.get("titleField") else None,
                          "group": row.get(task["groupField"]) if task.get("groupField") else None,
                          "link": row.get(task["linkField"]) if task.get("linkField") else None,
                          **evidence, "noEvidence": not evidence["images"] and not evidence["text"],
                          "cells": [{"field": field, "current": None} for field in fields], "quiet": []})
        worklist = {"schemaVersion": SCHEMA, "profileId": profile["id"], "batch": batch, "generatedAt": _now(),
                    "noEvidence": sum(1 for item in items if item["noEvidence"]), "items": items}
        readings = {key: value for key, value in readings.items() if key in done}
        _write_json(work / "readings.json", readings)
        items = [item for item in items if item["key"] not in done and not item["noEvidence"]]
    run_id = f"{batch}-{_now()}-{secrets.token_hex(3)}"
    worklist["runId"] = run_id
    _write_json(work / "worklist.json", worklist)
    if not items:
        return None
    # 판독자 파일과 인자 — 골든셋 검수의 그 함수. 표준 출력으로 내는 것을 받아 읽는다. 워크플로우의 batchId가 이번 runId다.
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        gt_review.emit_workflow_args(profile, task, work, {**worklist, "batchId": run_id}, items)
    out = buffer.getvalue()
    args = json.loads(out[out.index("{"):])["workflowArgs"]
    for name in ("gt", "alternatives"):
        args.pop(name, None)
    (work / "workflow-args.json").unlink(missing_ok=True)  # 골든셋 검수의 되짚기 기록 — 증분에는 worklist가 그 일을 한다
    return {**args, "stage": "read"}


def finish(profile: dict[str, Any], batch: str, output: Path) -> dict[str, Any]:
    """워크플로우 반환값에서 판독만 받아 `readings.json`(키 → 판독)에 합친다. 이번 준비(runId)의 결과만 받는다.
    칸이 하나도 없는 판독은 받지 않는다 — 받으면 «읽었다»로 세어져 다시 읽히지 않는다."""
    work = work_dir(profile, batch)
    worklist = _read_json(work / "worklist.json") or {}
    raw = _read_json(output) or {}
    returned = raw.get("result") if isinstance(raw.get("result"), dict) else raw
    if not worklist.get("runId") or returned.get("batchId") != worklist.get("runId"):
        raise IncrRejected("다른 준비의 판독입니다 — 받지 않습니다.")
    by_id = {item["id"]: item["key"] for item in worklist.get("items") or []}
    readings = _read_json(work / "readings.json", {}) or {}
    fields = field_map(load_task(profile))
    got = 0
    for entry in returned.get("items") or []:
        key = by_id.get(entry.get("id"))
        reading = entry.get("reading")
        if not key or not isinstance(reading, dict):
            continue
        cells: dict[str, Any] = {}
        for row in reading.get("readings") or []:
            field = row.get("field")
            if field in fields:
                cells[field] = {name: row.get(name) for name in
                                ("value", "confidence", "evidenceImageIds", "observation", "definitionGap", "askHuman")}
                cells[field]["value"] = normalize(fields[field], cells[field]["value"], lenient=True)
        if not cells:
            continue
        readings[key] = {"readAt": _now(), "runId": worklist["runId"], "cells": cells, "note": reading.get("note")}
        got += 1
    _write_json(work / "readings.json", readings)
    return {"read": got, "items": len(by_id)}


def _state(profile: dict[str, Any], batch: str, **fields: Any) -> None:
    path = work_dir(profile, batch) / "state.json"
    _write_json(path, {**(_read_json(path, {}) or {}), **fields, "updatedAt": _now()})


BUSY = "다른 AI 작업(골든셋 검수나 다른 증분)이 도는 중입니다 — 끝난 뒤 다시 눌러 주세요."


def run(profile: dict[str, Any], batch: str, reread: bool = False) -> dict[str, Any]:
    """준비 → 판독(워크플로우 read 단계, 헤드리스) → 합치기. 잠금은 골든셋 검수의 러너와 **같은 하나**다 —
    워크플로우는 한 번에 하나만 돈다(CLAUDE.md «다음 후보 받기»). 러너의 공용 상태(`runs/.gt-next/state.json`)에도
    «증분이 돈다»를 적는다 — 골든셋 화면이 잠금이 쥐어진 것만 보고 지난 실행의 문장을 지금 것으로 보이지 않게."""
    import gt_next

    folder = gt_next.lock_dir(profile)
    handle = gt_next._try_lock(folder, patience=1.0)
    if handle is None:
        # 떼어 띄운 러너의 표준 출력은 아무도 읽지 않는다 — 화면이 읽는 상태 파일에 남긴다.
        _state(profile, batch, phase="failed", pid=os.getpid(), finishedAt=_now(), message=BUSY)
        raise gt_next.Busy(BUSY)
    gt_next._held[folder] = handle
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, gt_next._on_signal)  # 헤드리스 자식 묶음도 함께 끝낸다(골든셋 러너와 같다)
    work = work_dir(profile, batch)
    work.mkdir(parents=True, exist_ok=True)
    log = work / "run.log"
    shared = {"task": f"incr:{profile['id']}", "taskName": f"{task_name(profile)} 증분 검수", "pid": os.getpid()}
    _state(profile, batch, phase="prepare", pid=os.getpid(), startedAt=_now(), finishedAt=None, message="사진을 준비하는 중입니다.")
    gt_next._write_state(folder, **shared, startedAt=_now(), finishedAt=None, phase="workflow", newScreen=None,
                         message="증분 검수의 AI가 읽는 중입니다.")
    try:
        args = prepare(profile, batch, reread=reread)
        if args is None:
            _state(profile, batch, phase="done", finishedAt=_now(), message="AI가 볼 건이 남아 있지 않습니다.")
        else:
            count = len(args["items"])
            _state(profile, batch, phase="workflow", items=count, message=f"AI가 {count}건을 먼저 읽는 중입니다. 몇 분 걸립니다.")
            output = gt_next._run_workflow(profile["id"], args, log, folder)
            result = finish(profile, batch, output)
            _state(profile, batch, phase="done", finishedAt=_now(), message=f"AI가 {result['read']}건을 읽었습니다."
                   + ("" if result["read"] == count else " 못 읽은 건은 «못 읽은 것 다시»로 다시 봅니다."))
    except BaseException as error:  # noqa: BLE001 — 무엇이 멈췄든 사람에게 한 문장은 남긴다
        with log.open("a", encoding="utf-8") as trace:
            trace.write(f"{_now()} 멈춤: {error!r}\n")
        message = str(error) if isinstance(error, (RuntimeError, ValueError, TaskError)) else "AI 추론이 도중에 멈췄습니다."
        _state(profile, batch, phase="failed", finishedAt=_now(), message=message)
        if not isinstance(error, Exception):
            raise
    finally:
        with contextlib.suppress(Exception):
            gt_next._write_state(folder, **shared, phase="done", finishedAt=_now(), newScreen=False, message="증분 검수의 AI가 끝났습니다.")
        gt_next._held.pop(folder, None)
        gt_next._release(handle)
    return status(profile, batch)


def runner_state(profile: dict[str, Any], batch: str) -> dict[str, Any]:
    """러너 상태. «도는 중»은 파일이 아니라 잠금으로 가른다 — 죽은 러너의 상태 파일을 도는 중으로 읽지 않게.
    `lockBusy`는 잠금이 쥐어졌는가(누가 쥐었든), `running`은 **이 묶음의** 러너가 도는가."""
    import gt_next

    state = _read_json(work_dir(profile, batch) / "state.json", {}) or {}
    busy = gt_next.running(gt_next.lock_dir(profile)) is not None
    mine = busy and state.get("phase") in ("prepare", "workflow")
    if not busy and state.get("phase") in ("prepare", "workflow"):
        state = {**state, "phase": "failed", "message": "지난 AI 추론이 도중에 멈췄습니다. 다시 눌러 주세요."}
    return {**state, "running": mine, "lockBusy": busy}


def start(profile: dict[str, Any], batch: str, reread: bool = False) -> subprocess.Popen[bytes]:
    """러너를 떼어 띄운다 — push 뒤와 화면 버튼이 같은 길을 쓴다. 잠금을 못 얻으면 러너가 상태 파일에 «바쁨»을 남기고 3으로 끝난다."""
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run", "--task", profile["id"], "--batch", batch,
                             *(["--reread"] if reread else [])],
                            cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


# ---------------------------------------------------------------- 사람 판정


def read_ledger(profile: dict[str, Any]) -> list[dict[str, Any]]:
    path = incr_home(profile) / LEDGER
    return read_jsonl(path) if path.is_file() else []


def effective_decisions(profile: dict[str, Any], task: dict[str, Any], batch: str,
                        ledger: list[dict[str, Any]] | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """칸마다 마지막 판정 — 값은 **지금 정책**으로 읽는다. 옛 코드는 `legacy`로 옮기고(원장 파일은 그대로), 정책에서 빠진 값의
    판정은 확정으로 세지 않고 `outOfPolicy`로 표시해 다시 묻는다(골든셋 검수의 POLICY_CHANGED_SINCE_DECISION과 같은 뜻)."""
    fields = field_map(task)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in read_ledger(profile) if ledger is None else ledger:
        if entry.get("batch") == batch and entry.get("field") in fields:
            latest[(entry["key"], entry["field"])] = entry
    for cell, entry in list(latest.items()):
        if entry["decision"] != "LABEL":
            continue
        spec = fields[cell[1]]
        value = normalize(spec, entry.get("value"), lenient=True)
        ai = normalize(spec, entry.get("aiValue"), lenient=True)
        latest[cell] = {**entry, "value": value, "agreedWithAi": ai is not None and ai == value,
                        "outOfPolicy": not in_range(spec, value)}
    return latest


def done_keys(latest: dict[tuple[str, str], dict[str, Any]], rows: dict[str, Any], fields: list[str]) -> set[str]:
    return {key for key in rows if all(_labeled(latest.get((key, field))) for field in fields)}


def _labeled(entry: dict[str, Any] | None) -> bool:
    return bool(entry) and entry["decision"] == "LABEL" and not entry.get("outOfPolicy")


def record(profile: dict[str, Any], batch: str, key: str, field: str, decision: str, reviewer: str,
           value: Any = None, reason: str = "", channel: str = "spoken", expected_ai: Any = None) -> dict[str, Any]:
    """사람 판정 한 줄. 화면 버튼과 CLI가 같은 함수를 지난다 — 문이 둘이면 규격도 둘이 된다.

    `expected_ai`는 **사람이 본 AI 제안**(없었으면 None)이다. 지금 판독과 다르면 거절한다 — 화면을 연 뒤 «다시 읽기»가 판독을
    바꿨으면, 원장에 사람이 보지 않은 AI 값이 사람 판정과 짝지어 남는다(골든셋 원장의 expectedBefore와 같은 선)."""
    task = load_task(profile)
    fields = field_map(task)
    if batch not in batches(profile):
        raise IncrRejected(f"그런 증분 묶음이 없습니다: {batch}")
    if field not in fields:
        raise IncrRejected(f"이 과제에 없는 칸입니다: {field}")
    if key not in input_rows(profile, task, batch):
        raise IncrRejected(f"이 묶음에 없는 건입니다: {key}")
    if not reviewer.strip():
        raise IncrRejected("판정한 사람의 이름이 없습니다 — 화면 맨 위에 이름을 적어 주세요.")
    if decision not in DECISIONS:
        raise IncrRejected(f"판정은 {' · '.join(DECISIONS)} 중 하나입니다: {decision}")
    if channel not in ("screen", "screen-bulk", "spoken"):
        raise IncrRejected(f"들어온 길을 알 수 없습니다: {channel}")
    spec = fields[field]
    chosen = None
    if decision == "LABEL":
        try:
            chosen = normalize(spec, value, legacy=False)
        except TaskError as error:
            raise IncrRejected(f"값을 읽지 못했습니다: {error}") from None
        if chosen is None:
            raise IncrRejected("고른 값이 없습니다.")
        if not in_range(spec, chosen):
            outside = [part for part in chosen.split(MANY_SEPARATOR) if not in_range(spec, part if is_many(spec) else chosen)]
            raise IncrRejected(f"정책의 허용값이 아닙니다: {', '.join(outside) or chosen}")
    reading = ((_read_json(work_dir(profile, batch) / "readings.json", {}) or {}).get(key) or {}).get("cells", {}).get(field) or {}
    ai_value = normalize(spec, reading.get("value"), lenient=True)
    if normalize(spec, expected_ai, lenient=True) != ai_value:
        raise IncrRejected("AI 제안이 화면을 연 뒤 바뀌었습니다 — 화면을 새로고침해 주세요.")
    with _locked(profile):
        ledger = read_ledger(profile)
        previous = next((entry for entry in reversed(ledger)
                         if entry.get("batch") == batch and entry["key"] == key and entry["field"] == field), None)
        entry = {
            "decisionId": f"INC-{len(ledger) + 1:06d}", "batch": batch, "key": key, "field": field,
            "decision": decision, "value": chosen, "aiValue": ai_value, "aiConfidence": reading.get("confidence"),
            "aiRunId": ((_read_json(work_dir(profile, batch) / "readings.json", {}) or {}).get(key) or {}).get("runId"),
            "agreedWithAi": decision == "LABEL" and ai_value is not None and chosen == ai_value,
            "reviewer": reviewer.strip(), "reason": reason, "channel": channel, "decidedAt": _now(),
            "basis": f"definitions#{field}",
        }
        if previous:
            entry["supersedes"] = previous["decisionId"]
        path = incr_home(profile) / LEDGER
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


# ---------------------------------------------------------------- 내보내기·상태


def export(profile: dict[str, Any], batch: str) -> dict[str, Any]:
    """모든 칸을 사람이 확정한 줄만, 밀어넣은 줄 그대로에 라벨을 채워 쓴다. 칸마다 누가 확정했는지(`<열>Source`)도 함께.
    값은 원래 형으로 되돌린다(`gt_task.export_value` — 골든셋 «반영해줘»와 같은 함수)."""
    task = load_task(profile)
    fields = field_map(task)
    rows = input_rows(profile, task, batch)
    latest = effective_decisions(profile, task, batch)
    out: list[dict[str, Any]] = []
    for key, row in rows.items():
        cells = [latest.get((key, field)) for field in fields]
        if not all(_labeled(cell) for cell in cells):
            continue
        labeled = dict(row)
        for (field, spec), cell in zip(fields.items(), cells):
            column = str(spec.get("gtField") or field)
            labeled[column] = export_value(spec, cell["value"], original=row.get(column))
            labeled[f"{column}Source"] = "INCR_REVIEW_AI_AGREED" if cell["agreedWithAi"] else "INCR_REVIEW_HUMAN"
        labeled["incrReview"] = {"batch": batch, "decisionIds": [cell["decisionId"] for cell in cells],
                                 "reviewers": sorted({cell["reviewer"] for cell in cells})}
        out.append(labeled)
    target = incr_home(profile) / "labeled" / f"{batch}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".jsonl.tmp")
    temp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in out), encoding="utf-8")
    temp.replace(target)
    return {"ok": True, "file": relative_or_absolute(target), "labeled": len(out), "pending": len(rows) - len(out)}


def status(profile: dict[str, Any], batch: str, task: dict[str, Any] | None = None,
           ledger: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """한 묶음의 셈 — 화면·목록·스킬이 이 함수 하나를 읽는다(규칙 8: 수를 다른 자리에서 다시 세지 않는다).
    목록은 과제마다 과제 선언과 원장을 한 번만 읽어 넘긴다(`task`·`ledger`)."""
    task = task or load_task(profile)
    fields = list(field_map(task))
    rows = input_rows(profile, task, batch)
    work = work_dir(profile, batch)
    readings = _read_json(work / "readings.json", {}) or {}
    worklist_head = _worklist_head(work)
    latest = effective_decisions(profile, task, batch, ledger)
    labeled = [entry for entry in latest.values() if _labeled(entry)]
    items_done = len(done_keys(latest, rows, fields))
    return {
        "batch": batch, "meta": _read_json(batch_home(profile, batch) / "batch.json", {}),
        "items": len(rows), "itemsRead": sum(1 for key in rows if key in readings),
        "itemsReadComplete": sum(1 for key in rows if _complete(readings.get(key), fields)),
        "itemsNoEvidence": worklist_head.get("noEvidence") or 0, "itemsDone": items_done, "itemsLeft": len(rows) - items_done,
        "cells": len(rows) * len(fields), "cellsLabeled": len(labeled),
        "cellsHeld": sum(1 for entry in latest.values() if entry["decision"] == "HOLD"),
        "cellsOutOfPolicy": sum(1 for entry in latest.values() if entry.get("outOfPolicy")),
        "cellsAgreedWithAi": sum(1 for entry in labeled if entry["agreedWithAi"]),
        "prepared": bool(worklist_head.get("prepared")),
        "labeledFile": relative_or_absolute(incr_home(profile) / "labeled" / f"{batch}.jsonl")
        if (incr_home(profile) / "labeled" / f"{batch}.jsonl").is_file() else None,
        "runner": runner_state(profile, batch),
    }


def _worklist_head(work: Path) -> dict[str, Any]:
    """작업 목록 전체(사진·맥락이 든 큰 파일)를 읽지 않고 머리만 — 목록 화면은 이것만 필요하다."""
    worklist = _read_json(work / "worklist.json", {}) or {}
    return {"prepared": bool(worklist.get("items")), "noEvidence": worklist.get("noEvidence")}


def task_name(profile: dict[str, Any]) -> str:
    """증분 화면·메뉴의 과제 이름 — 골든셋 검수의 이름에서 «골든셋» 꼬리만 뗀 것(«<과제> 골든셋» → «<과제>»). 한 과제가 메뉴·제목·카드에서
    다른 이름으로 보이지 않게 이 함수 하나만 쓴다. 증분은 골든셋이 아니라 새 데이터라 꼬리를 뗀다."""
    from gt_review_render import call_name

    return re.sub(r"\s*골든셋$", "", call_name(profile)) or call_name(profile)


LISTED_BATCHES = 12


def home_rows() -> dict[str, Any]:
    """첫 화면·메뉴가 읽는 목록. 골든셋 검수 과제 전부가 증분을 받는다(과제 선언이 같다).
    남은 건(`itemsLeft`)은 목록에 싣지 않은 옛 묶음까지 과제마다 더해 낸다 — 메뉴의 수를 화면이 다시 세지 않게."""
    rows = []
    for profile in task_profiles():
        found: list[str] = []
        try:
            task = load_task(profile)
            ledger = read_ledger(profile)
            found = batches(profile)
            every = [status(profile, batch, task, ledger) for batch in found]
            problem = None
        except (TaskError, OSError, ValueError) as error:
            every, problem = [], str(error)
        spec = profile["gtTask"]
        # menuName — 사이드바의 과제 이름은 골든셋 검수 메뉴와 같은 이름에서 «골든셋» 꼬리만 뗀 것이다: 한 과제가 두 메뉴에서
        # 다른 이름으로 보이지 않게, 그러나 증분(골든셋이 아닌 새 데이터) 밑에 «골든셋»이 보이지 않게.
        rows.append({"task": profile["id"], "name": task_name(profile), "menuName": task_name(profile),
                     "subject": profile.get("subjectName"),
                     "unit": spec.get("unit"),
                     "fields": [field.get("name") or field["id"] for field in spec.get("fields") or []],
                     "batches": every[:LISTED_BATCHES], "moreBatches": max(0, len(every) - LISTED_BATCHES),
                     "itemsLeft": sum(entry["itemsLeft"] for entry in every), "problem": problem})
    return {"tasks": rows, "itemsLeft": sum(row["itemsLeft"] for row in rows)}


def screen_data(profile: dict[str, Any], batch: str) -> dict[str, Any]:
    """검수 화면이 읽는 모든 것. 서버는 이것을 그대로 넘기기만 한다 — 화면을 그리지 않는다.
    사진 준비 전(또는 준비가 멈춘) 묶음도 밀어넣은 줄로 건을 싣는다 — 사람은 사진 없이도 손으로 고를 수 있어야 한다."""
    task = load_task(profile)
    fields = field_map(task)
    work = work_dir(profile, batch)
    worklist = _read_json(work / "worklist.json", {}) or {}
    readings = _read_json(work / "readings.json", {}) or {}
    latest = effective_decisions(profile, task, batch)
    base = output_root(profile).resolve()
    names = task.get("columnNames") or {}
    role_names = (task.get("images") or {}).get("roleNames") or {}

    def url(path: str) -> str:
        absolute = Path(path) if Path(path).is_absolute() else (PROJECT_ROOT / path).resolve()
        return f"/f/{profile['id']}/{absolute.relative_to(base).as_posix()}"

    prepared = {item["key"]: item for item in worklist.get("items") or []}
    items = []
    for key, row in input_rows(profile, task, batch).items():
        item = prepared.get(key) or {"key": key, "title": row.get(task["titleField"]) if task.get("titleField") else None,
                                     "group": row.get(task["groupField"]) if task.get("groupField") else None,
                                     "link": row.get(task["linkField"]) if task.get("linkField") else None,
                                     "notPrepared": True}
        link = str(item.get("link") or "")
        items.append({
            "key": key, "title": item.get("title"), "group": item.get("group"),
            "link": link if link.startswith(("http://", "https://")) else None,
            "images": [{"viewId": image["viewId"], "url": url(image["path"]),
                        "role": role_names.get(str(image.get("role")), image.get("role"))} for image in item.get("images") or []],
            "context": [{"name": names.get(k, k), "value": v} for k, v in (item.get("context") or {}).items()],
            "text": [{"name": names.get(k, k), "value": v} for k, v in (item.get("text") or {}).items()],
            "noEvidence": item.get("noEvidence"), "notPrepared": bool(item.get("notPrepared")),
            "missing": len(item.get("missing") or []),
            "reading": readings.get(key),
            "decisions": {field: latest[(key, field)] for field in fields if (key, field) in latest},
        })
    return {
        "ok": True, "task": profile["id"], "name": task_name(profile), "unit": task.get("unit"), "batch": batch,
        "batches": batches(profile),
        "fields": [{"id": fid, "name": spec.get("name") or fid, "many": spec.get("cardinality") == "many",
                    "unknownLabel": spec.get("unknownLabel"),
                    "labels": [{"code": str(label), "name": (spec.get("labelNames") or {}).get(str(label)) or str(label)}
                               for label in spec["labels"]]} for fid, spec in fields.items()],
        "items": items, "status": status(profile, batch, task),
    }


# ---------------------------------------------------------------- CLI


def main() -> int:
    import gt_next

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tasks")
    go = sub.add_parser("push")
    go.add_argument("--task", required=True)
    go.add_argument("--file", required=True)
    go.add_argument("--images", help="증분 전용 사진 색인(과제 gtTask.images와 같은 모양). 없으면 과제의 색인")
    go.add_argument("--name", help="묶음 이름(없으면 파일 이름)")
    go.add_argument("--no-run", action="store_true", help="AI 추론을 띄우지 않는다")
    for name in ("run", "status", "export", "record"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--task", required=True)
        cmd.add_argument("--batch", required=name in ("record", "export"))
        if name == "run":
            cmd.add_argument("--reread", action="store_true", help="못 읽은 건만 다시(없으면 확정 안 한 건을 처음부터 다시 읽는다)")
        if name == "record":
            cmd.add_argument("--key", required=True)
            cmd.add_argument("--field", required=True)
            cmd.add_argument("--value")
            cmd.add_argument("--hold", action="store_true")
            cmd.add_argument("--reviewer", required=True, help="말한 사람")
            seen = cmd.add_mutually_exclusive_group(required=True)
            seen.add_argument("--expect-ai", help="그 사람이 본 AI 제안(코드)")
            seen.add_argument("--expect-ai-empty", action="store_true", help="AI 제안이 없던 칸")
            cmd.add_argument("--reason", default="")
    args = parser.parse_args()
    try:
        if args.command == "tasks":
            result: Any = home_rows()
        elif args.command == "push":
            profile = find_profile(args.task)
            result = push(profile, Path(args.file), Path(args.images) if args.images else None, args.name)
            if not args.no_run:
                # 다른 AI 작업이 돌면 띄우지 않고 말한다 — 떼어 띄운 러너는 잠금을 못 얻고 조용히 끝나 버린다.
                if gt_next.running(gt_next.lock_dir(profile)) is not None:
                    result["aiStarted"] = False
                    result["aiNote"] = BUSY
                else:
                    start(profile, result["batch"])
                    result["aiStarted"] = True
        else:
            profile = find_profile(args.task)
            batch = pick_batch(profile, args.batch)
            if args.command == "run":
                result = run(profile, batch, reread=args.reread)
            elif args.command == "status":
                result = status(profile, batch)
            elif args.command == "export":
                result = export(profile, batch)
            else:
                result = record(profile, batch, args.key, args.field, "HOLD" if args.hold else "LABEL", args.reviewer,
                                value=args.value, reason=args.reason, channel="spoken",
                                expected_ai=None if args.expect_ai_empty else args.expect_ai)
    except gt_next.Busy as busy:
        print(json.dumps({"ok": False, "busy": True, "error": str(busy)}, ensure_ascii=False))
        return 3
    except (IncrRejected, TaskError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    # 러너는 gt_next의 잠금 기록(_held)을 쓴다 — 이 파일을 __main__으로 띄워도 gt_next는 import한 한 벌이다.
    sys.exit(main())
