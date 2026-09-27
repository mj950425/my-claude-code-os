#!/usr/bin/env python3
"""«다음 후보 받기» — 준비 · 두 눈의 판독(워크플로우) · 화면을 한 프로세스가 끝까지 돈다. **한 번에 하나만.**

    python3 gt_next.py run --task <id> [--reread] [--limit N]   # 끝날 때까지 돈다(서버는 이것을 떼어 띄운다)
    python3 gt_next.py status                                     # 지금 도는 것 — 없으면 마지막 결과

## 왜 따로 도는가

화면의 버튼이 새 후보를 받으려면 AI 두 개(`gt-blind-reader`·`gt-defender`)가 사진을 읽어야 한다. 그 일은
Claude Code의 워크플로우(`gt-review.js`)가 하고, 워크플로우는 Claude Code 세션 안에서만 돈다. 서버는 원장 말고
아무것도 쓰지 않는 가벼운 프로세스라(`serve_reports.py`) 그 일을 맡지 않는다 — 이 러너를 떼어 띄우기만 한다.
러너는 헤드리스 Claude Code(`claude -p`)에 워크플로우 **한 번 부르기**만 맡긴다. 고르기(`prepare`)와 화면(`finish`·`render`)은
스킬이 부르던 그 함수를 이 프로세스가 그대로 부른다 — 문이 둘이면 규격도 둘이 된다.

## 한 번에 하나 — 잠금 하나

`runs/.gt-next/lock`에 대한 `flock` 하나다. 러너는 시작부터 끝까지 그것을 쥐고, `prepare`도 도는 동안 쥔다.
- 과제가 달라도 둘째 러너는 잠금을 못 얻고 곧장 «바쁨»으로 끝난다. 기다리지 않는다 — 눌렀는데 몇십 분 뒤에 도는 것은 사람이 모른다.
- 프로세스가 죽으면 운영체제가 잠금을 푼다. 그래서 «잠금이 풀려 있는데 `preparing`이 참»은 죽은 배치다 — 워크플로우는 늘 이 러너
  안에서만 돌기 때문이다. 그런 배치는 새 준비가 버린다(`--force`).
- 파일에 적은 상태(`state.json`)는 화면에 보일 문장일 뿐 잠금이 아니다. 「도는 중인가」는 늘 잠금으로 가른다.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from catalog_profile import PROJECT_ROOT
from gt_task import TaskError

RUN_DIR = PROJECT_ROOT / ".claude/os/runs/.gt-next"
LOCK = RUN_DIR / "lock"
STATE = RUN_DIR / "state.json"
WORKFLOW = PROJECT_ROOT / ".claude/os/engine/workflows/gt-review.js"
# 60건 판독이 보통 수십 분이다. 이보다 길면 멈춘 것으로 보고 화면을 «못 읽음»으로 돌려 놓는다.
WORKFLOW_TIMEOUT = 3 * 60 * 60

_held: Any = None  # 이 프로세스가 쥔 잠금 파일. 같은 프로세스 안의 prepare는 다시 잡지 않는다(flock은 파일 열기마다 따로라 스스로 막힌다)


class Busy(TaskError):
    """다른 준비가 돌고 있다. 문장은 사람에게 그대로 보인다."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(**fields: Any) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    state = {**_read_state(), **fields, "updatedAt": _now()}
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE)


def _try_lock() -> Any:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _busy_message(state: dict[str, Any]) -> str:
    name = state.get("taskName") or state.get("task") or "다른 과제"
    return f"«{name}»의 다음 후보를 AI가 보는 중입니다 — 한 번에 하나만 돕니다. 끝난 뒤 다시 눌러 주세요."


def running() -> dict[str, Any] | None:
    """지금 도는 준비. 없으면 None. 파일이 아니라 잠금으로 가른다 — 죽은 러너의 상태 파일을 «도는 중»으로 읽지 않게."""
    if _held is not None:
        return _read_state()
    handle = _try_lock()
    if handle is None:
        return _read_state()
    fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()
    return None


def status() -> dict[str, Any]:
    """화면이 읽는 모양. `running`이 참이면 도는 중, 거짓이면 `state`는 마지막 결과다."""
    now = running()
    state = now if now is not None else _read_state()
    if now is None and state.get("phase") not in (None, "done", "failed", "idle"):
        # 러너가 끝을 적지 못하고 죽었다(강제 종료). 잠금이 풀렸으니 도는 것은 없다.
        state = {**state, "phase": "failed", "message": "지난 준비가 도중에 멈췄습니다. 다시 눌러 주세요."}
    return {"running": now is not None, **state}


@contextlib.contextmanager
def gate() -> Iterator[None]:
    """`prepare`가 도는 동안 쥔다. 러너 안에서 부른 것이면(이미 쥐었다) 그냥 지난다."""
    global _held
    if _held is not None:
        yield
        return
    handle = _try_lock()
    if handle is None:
        raise Busy(_busy_message(_read_state()))
    _held = handle
    try:
        yield
    finally:
        _held = None
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def claude_binary() -> str | None:
    return os.environ.get("GT_NEXT_CLAUDE") or shutil.which("claude")


def _call(func: Any, namespace: argparse.Namespace) -> str:
    """gt_review의 명령 함수를 이 프로세스에서 부르고 표준 출력을 받는다(스킬이 읽던 그 출력)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        func(namespace)
    return buffer.getvalue()


def _workflow_prompt(args_file: Path) -> str:
    return f"""GT 개선 화면의 «다음 후보 받기» 버튼으로 사용자가 이 워크플로우 실행을 명시적으로 요청했다.
다른 일은 하지 않는다. 파일을 만들거나 고치지 않는다.

1. Read로 {args_file} 를 끝까지 연다(길면 offset을 옮겨 가며 전부). 그 파일 전체가 JSON 객체 하나다.
2. Workflow 도구가 목록에 없으면 ToolSearch로 `select:Workflow`를 불러온다.
3. Workflow({{ scriptPath: "{WORKFLOW}", args: <1의 JSON 객체> }})를 한 번 부른다.
   args는 파일의 객체를 **그대로** 넣는다 — 문자열로 감싸지 않고, 키·값을 빼거나 줄이거나 고치지 않는다.
4. 워크플로우가 끝날 때까지 기다린다. 끝나면 알림에 적힌 출력 파일(워크플로우 반환값이 든 파일)의 절대 경로를 outputFile로 답한다.
   실패했거나 출력 파일이 없으면 outputFile은 빈 문자열, error에 한 문장."""


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"outputFile": {"type": "string"}, "error": {"type": "string"}},
    "required": ["outputFile"],
}


def _run_workflow(task_id: str, workflow_args: dict[str, Any], log: Path) -> Path:
    binary = claude_binary()
    if not binary:
        raise RuntimeError("이 컴퓨터에서 Claude Code(claude)를 찾지 못했습니다 — 개발자에게 전해 주세요.")
    args_file = RUN_DIR / f"workflow-args.{task_id}.json"
    # GT 값이 든 인자다(반론자용). runs/ 아래라 레포에 올라가지 않고, 판독자는 이 파일을 열지 않는다(역할 파일의 약속).
    args_file.write_text(json.dumps(workflow_args, ensure_ascii=False, indent=1), encoding="utf-8")
    command = [binary, "-p", _workflow_prompt(args_file),
               "--allowedTools", "Workflow,ToolSearch,Read",
               "--output-format", "json", "--json-schema", json.dumps(OUTPUT_SCHEMA)]
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"\n== {_now()} claude -p (workflow) ==\n")
        handle.flush()
        done = subprocess.run(command, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=handle, text=True, timeout=WORKFLOW_TIMEOUT)
        handle.write(done.stdout[-20000:] + "\n")
    try:
        reply = json.loads(done.stdout)
    except ValueError:
        raise RuntimeError("Claude Code가 알아볼 수 없는 답을 냈습니다.") from None
    text = reply.get("result") or ""
    if "Not logged in" in text or "/login" in text:
        raise RuntimeError("이 컴퓨터의 Claude Code(claude)가 로그인돼 있지 않습니다 — 터미널에서 «claude auth login»을 한 번 해 주세요.")
    answer = reply.get("structured_output")
    if not isinstance(answer, dict):
        try:
            answer = json.loads(text)
        except ValueError:
            answer = {}
    output = str((answer or {}).get("outputFile") or "")
    if reply.get("is_error") or not output or not Path(output).is_file():
        raise RuntimeError((answer or {}).get("error") or "AI가 이번에 읽지 못했습니다.")
    return Path(output)


def run(task_id: str, reread: bool = False, limit: int | None = None) -> dict[str, Any]:
    global _held
    import gt_review as review  # 늦게 — gt_review가 이 모듈의 gate를 import한다

    handle = _try_lock()
    if handle is None:
        raise Busy(_busy_message(_read_state()))
    _held = handle
    log = RUN_DIR / f"{task_id}.log"
    try:
        profile = review.find_profile(task_id)
        name = review.call_name(profile)
        _write_state(task=task_id, taskName=name, pid=os.getpid(), startedAt=_now(), phase="prepare",
                     message="볼 칸을 고르는 중입니다.", reread=reread, finishedAt=None,
                     warnings={}, items=None, newScreen=None)
        # 잠금을 쥐었는데 준비 중인 배치가 있으면 죽은 배치다 — 워크플로우는 이 러너 안에서만 돈다(머리말).
        stale = bool(review.status_of(profile).get("preparing"))
        namespace = argparse.Namespace(task=task_id, limit=limit, key=None, reread=reread, force=stale)
        out = _call(review.cmd_prepare, namespace)
        payload = json.loads(out[out.index("{"):]) if "{" in out else {}
        workflow_args = payload.get("workflowArgs")
        # 사람에게 전할 경고(사진을 못 찾음 같은) — 스킬이 작업 목록(GT 값이 든 파일)을 열지 않고 읽게 상태에 둔다.
        _write_state(warnings=payload.get("warnings") or {})
        if workflow_args is None:
            note = payload.get("note") or "볼 칸이 없습니다."
            _write_state(phase="done", message=note, finishedAt=_now(), newScreen=False)
            return status()
        prepared = True
        try:
            if workflow_args.get("items"):
                _write_state(phase="workflow", items=len(workflow_args["items"]),
                             message=f"AI 두 개가 {len(workflow_args['items'])}건을 보는 중입니다. 몇 분 걸립니다.")
                output = _run_workflow(task_id, workflow_args, log)
                _write_state(phase="finish", message="화면을 만드는 중입니다.")
                _call(review.cmd_finish, argparse.Namespace(task=task_id, source=str(output), sweep=None))
            else:
                _call(review.cmd_render, argparse.Namespace(task=task_id, sweep=None))
            prepared = False
        finally:
            if prepared:
                # 판독이 돌아오지 않았다 — «준비 중»으로 두지 않고 칸마다 «AI가 못 읽음»인 화면을 만든다(스킬 2단계의 표와 같다).
                with contextlib.suppress(Exception):
                    _call(review.cmd_render, argparse.Namespace(task=task_id, sweep=None))
        _write_state(phase="done", message="새 후보가 준비됐습니다.", finishedAt=_now(), newScreen=True)
        return status()
    except BaseException as error:  # noqa: BLE001 — 무엇이 멈췄든 사람에게 한 문장은 남긴다
        with log.open("a", encoding="utf-8") as trace:
            trace.write(f"{_now()} 멈춤: {error!r}\n")
        if isinstance(error, subprocess.TimeoutExpired):
            message = "AI 판독이 너무 오래 걸려 멈췄습니다. 다시 눌러 주세요."
        elif isinstance(error, (RuntimeError, ValueError)):
            message = str(error)
        else:
            message = "준비가 도중에 멈췄습니다."
        _write_state(phase="failed", message=message, finishedAt=_now())
        if not isinstance(error, Exception):
            raise
        return status()
    finally:
        _held = None
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--task", required=True)
    go.add_argument("--reread", action="store_true", help="지금 화면을 두고 못 읽은 건만 다시 판독한다")
    go.add_argument("--limit", type=int)
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    try:
        result = run(args.task, reread=args.reread, limit=args.limit)
    except Busy as busy:
        print(json.dumps({"ok": False, "busy": True, "error": str(busy)}, ensure_ascii=False))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("phase") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
