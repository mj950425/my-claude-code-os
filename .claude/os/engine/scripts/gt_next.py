#!/usr/bin/env python3
"""«다음 후보 받기» — 준비 · 두 눈의 판독(워크플로우) · 화면을 한 프로세스가 끝까지 돈다. **한 번에 하나만.**

    python3 gt_next.py run --task <id> [--reread] [--limit N]   # 끝날 때까지 돈다(서버는 이것을 떼어 띄운다)
    python3 gt_next.py status                                     # 지금 도는 것 — 없으면 마지막 결과

## 왜 따로 도는가

화면의 버튼이 새 후보를 받으려면 AI 두 개(`gt-blind-reader`·`gt-defender`)가 사진을 읽어야 한다. 그 일은
Claude Code의 워크플로우(`gt-review.js`)가 하고, 워크플로우는 Claude Code 세션 안에서만 돈다. 서버는 원장 말고
아무것도 쓰지 않는 가벼운 프로세스라(`serve_reports.py`) 그 일을 맡지 않는다 — 이 러너를 떼어 띄우기만 한다.
러너는 헤드리스 Claude Code(`claude -p`)에 워크플로우 **부르기**만 맡긴다 — 판독과 반론, 두 번(아래 «눈가림»). 고르기(`prepare`)와 화면(`finish`·`render`)은
스킬이 부르던 그 함수를 이 프로세스가 그대로 부른다 — 문이 둘이면 규격도 둘이 된다.

## 한 번에 하나 — 잠금 하나

`runs/.gt-next/lock`에 대한 `flock` 하나다. 러너는 시작부터 끝까지 그것을 쥐고, `prepare`도 도는 동안 쥔다.
- 과제가 달라도 둘째 러너는 잠금을 못 얻고 곧장 «바쁨»으로 끝난다. 기다리지 않는다 — 눌렀는데 몇십 분 뒤에 도는 것은 사람이 모른다.
- 프로세스가 죽으면 운영체제가 잠금을 푼다. 그래서 «잠금이 풀려 있는데 `preparing`이 참»은 죽은 배치다 — 워크플로우는 늘 이 러너
  안에서만 돌기 때문이다. 그런 배치는 새 준비가 버린다(`--force`).
- 파일에 적은 상태(`state.json`)는 화면에 보일 문장일 뿐 잠금이 아니다. 「도는 중인가」는 늘 잠금으로 가른다.
- 잠금은 헤드리스 Claude Code 자식에게도 물려준다(`pass_fds`). 러너가 강제로 죽어도 자식이 판독을 마칠 때까지 잠금이 남아,
  둘째 워크플로우가 겹쳐 돌지 않는다. 러너가 종료 신호를 받으면 자식 프로세스 묶음도 함께 끝낸다.

## 눈가림 — 판독자가 도는 동안 GT가 든 파일은 없다

워크플로우 사본에는 인자가 박힌다. 반론자 몫의 GT가 판독 파일 경로와 함께 박힌 사본이 판독 중에 디스크에 있으면, 판독자(Read·Grep·Glob)는
Grep 한 번으로 자기 건의 GT에 닿는다. 사본을 프로젝트 밖에 두는 길은 막혀 있다 — 워크플로우 도구는 읽을 수 있는 경로만 받고,
그 경로는 판독자도 읽는다. 그래서 두 번 나눠 부른다: **판독**(`stage: read`, GT 없음) → 판독이 모두 끝난 뒤 **반론**(`stage: defend`,
GT와 판독 결과). 원래 설계가 판독 파일 이름과 작업 목록을 잇지 못하게 해 둔 것과 같은 원칙이다. 사본은 단계가 끝나면 지운다.
반론 단계가 실패하면 판독만으로 화면을 만든다(반론이 없는 칸은 «못 읽음»으로 남아 다시 읽기 대상이 된다).

## 헤드리스 세션의 권한

`--tools`로 쓸 수 있는 도구를 워크플로우와 읽기 도구로 줄이고, 쓰기·셸·웹 도구는 `--disallowedTools`로 막는다. 권한 모드는
`dontAsk`로 못박아 사용자 전역의 자동 승인 설정을 물려받지 않고, MCP 서버도 싣지 않는다(`--strict-mcp-config`).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from catalog_profile import PROJECT_ROOT
from gt_task import TaskError

# 실제 과제들의 산출물은 모두 runs/<id>에 있으므로 잠금은 runs/.gt-next 하나다. 과제마다 `lock_dir(profile)`로 구한다 —
# 테스트의 임시 과제는 제 임시 폴더에서 잠그고, 같은 컴퓨터에서 도는 진짜 러너와 부딪히지 않는다.
RUN_DIR = PROJECT_ROOT / ".claude/os/runs/.gt-next"
WORKFLOW = PROJECT_ROOT / ".claude/os/engine/workflows/gt-review.js"
# 60건 판독이 보통 수십 분이다. 이보다 길면 멈춘 것으로 보고 화면을 «못 읽음»으로 돌려 놓는다.
WORKFLOW_TIMEOUT = 3 * 60 * 60

# 이 프로세스가 쥔 잠금 {폴더: 파일}. 같은 프로세스 안의 prepare는 다시 잡지 않는다(flock은 파일 열기마다 따로라 스스로 막힌다)
_held: dict[Path, Any] = {}


class Busy(TaskError):
    """다른 준비가 돌고 있다. 문장은 사람에게 그대로 보인다."""


def lock_dir(profile: dict[str, Any]) -> Path:
    from catalog_profile import output_root
    return output_root(profile).parent / ".gt-next"


def _dir(run_dir: Path | None) -> Path:
    return Path(run_dir) if run_dir is not None else RUN_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_state(run_dir: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads((_dir(run_dir) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(run_dir: Path | None = None, **fields: Any) -> None:
    folder = _dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    state = {**_read_state(folder), **fields, "updatedAt": _now()}
    temp = folder / "state.tmp"
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(folder / "state.json")


def _try_lock(run_dir: Path | None = None, patience: float = 0.0) -> Any:
    """잠금을 쥔다. `patience`초 동안은 짧게 다시 해 본다 — 상태 조회(running)가 잠금을 아주 잠깐 쥐는 순간과 겹쳐
    막 뜬 러너가 «바쁨»으로 끝나지 않게. 진짜로 도는 러너가 있으면 그만큼 기다린 뒤 None."""
    folder = _dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / "lock", "a+")
    deadline = time.monotonic() + patience
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                return None
            time.sleep(0.05)


def _release(handle: Any) -> None:
    fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


def _busy_message(state: dict[str, Any]) -> str:
    return "이미 AI가 검수중입니다."


def running(run_dir: Path | None = None) -> dict[str, Any] | None:
    """지금 도는 준비. 없으면 None. 파일이 아니라 잠금으로 가른다 — 죽은 러너의 상태 파일을 «도는 중»으로 읽지 않게."""
    folder = _dir(run_dir)
    if folder in _held:
        return _read_state(folder)
    handle = _try_lock(folder)
    if handle is None:
        return _read_state(folder)
    _release(handle)
    return None


def status(run_dir: Path | None = None) -> dict[str, Any]:
    """화면이 읽는 모양. `running`이 참이면 도는 중, 거짓이면 `state`는 마지막 결과다."""
    now = running(run_dir)
    state = now if now is not None else _read_state(run_dir)
    if now is None and state.get("phase") not in (None, "done", "failed", "idle"):
        # 러너가 끝을 적지 못하고 죽었다(강제 종료). 잠금이 풀렸으니 도는 것은 없다.
        state = {**state, "phase": "failed", "message": "지난 준비가 도중에 멈췄습니다. 다시 눌러 주세요."}
    return {"running": now is not None, **state}


@contextlib.contextmanager
def gate(run_dir: Path | None = None) -> Iterator[None]:
    """`prepare`가 도는 동안 쥔다. 러너 안에서 부른 것이면(이미 쥐었다) 그냥 지난다."""
    folder = _dir(run_dir)
    if folder in _held:
        yield
        return
    handle = _try_lock(folder, patience=1.0)
    if handle is None:
        raise Busy(_busy_message(_read_state(folder)))
    _held[folder] = handle
    try:
        yield
    finally:
        _held.pop(folder, None)
        _release(handle)


# 판독 모델. 판독·반론 에이전트는 모델을 따로 적지 않아 이 헤드리스 세션의 모델을 물려받는다.
MODEL = os.environ.get("GT_NEXT_MODEL") or "claude-opus-5-5"
# 데스크톱 앱이 들고 있는 Claude Code. npm의 설치 지연 규칙(min-release-age) 탓에 PATH의 claude가 이 모델을 모를 수 있다.
DESKTOP_CLAUDE = Path.home() / "Library/Application Support/Claude/claude-code"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def claude_binary() -> str | None:
    """가장 새 Claude Code. 모델이 CLI 버전을 요구하므로(낮으면 400) 설치본 가운데 가장 새 것을 고른다."""
    if os.environ.get("GT_NEXT_CLAUDE"):
        return os.environ["GT_NEXT_CLAUDE"]
    found: list[tuple[tuple[int, ...], str]] = []
    if DESKTOP_CLAUDE.is_dir():
        for folder in DESKTOP_CLAUDE.iterdir():
            binary = folder / "claude.app/Contents/MacOS/claude"
            if binary.is_file() and _version(folder.name):
                found.append((_version(folder.name), str(binary)))
    on_path = shutil.which("claude")
    if on_path:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            text = subprocess.run([on_path, "--version"], capture_output=True, text=True, timeout=20).stdout.split()[0]
            found.append((_version(text), on_path))
    return max(found)[1] if found else None


# 이 서버를 데스크톱 앱 안의 세션이 띄웠으면 그 세션의 연결 정보가 환경에 남아 있다 — 헤드리스 CLI는 제 로그인으로 돌아야 한다.
_HOST_ONLY = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_SCOPES", "CLAUDE_CODE_SESSION_ID",
              "CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
              "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_EXECPATH")


def _headless_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in _HOST_ONLY}


def _call(func: Any, namespace: argparse.Namespace) -> str:
    """gt_review의 명령 함수를 이 프로세스에서 부르고 표준 출력을 받는다(스킬이 읽던 그 출력)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        func(namespace)
    return buffer.getvalue()


EMBED_LINE = "const INPUT = typeof args === 'string' ? JSON.parse(args) : (args || {})"


def embedded_workflow(workflow_args: dict[str, Any], target: Path) -> Path:
    """워크플로우 사본 — 인자를 AI가 옮겨 적지 않게 스크립트에 박는다. 원본의 인자 줄 하나만 바꾼다."""
    source = WORKFLOW.read_text(encoding="utf-8")
    if source.count(EMBED_LINE) != 1:
        raise RuntimeError("워크플로우 gt-review.js의 인자 줄을 찾지 못했습니다 — 개발자에게 전해 주세요.")
    target.write_text(source.replace(EMBED_LINE, "const INPUT = " + json.dumps(workflow_args, ensure_ascii=False)), encoding="utf-8")
    return target


def _workflow_prompt(script: Path) -> str:
    return f"""GT 개선 화면의 «다음 후보 받기» 버튼으로 사용자가 이 워크플로우 실행을 명시적으로 요청했다.
다른 일은 하지 않는다. 파일을 읽거나 만들거나 고치지 않는다.

1. Workflow 도구가 목록에 없으면 ToolSearch로 `select:Workflow`를 불러온다.
2. Workflow({{ scriptPath: "{script}" }})를 한 번 부른다. args는 넣지 않는다 — 인자는 스크립트 안에 이미 있다.
3. 워크플로우가 끝날 때까지 기다린다. 끝나면 알림에 적힌 출력 파일(워크플로우 반환값이 든 파일)의 절대 경로를 outputFile로 답한다.
   실패했거나 출력 파일이 없으면 outputFile은 빈 문자열, error에 한 문장."""


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"outputFile": {"type": "string"}, "error": {"type": "string"}},
    "required": ["outputFile"],
}


# 헤드리스 세션이 쓸 수 있는 도구. 본 세션은 워크플로우 한 단계만 부르고, 워크플로우의 판독자·반론자는 읽기 도구만 쓴다.
HEADLESS_TOOLS = "Workflow,ToolSearch,Read,Grep,Glob"
HEADLESS_DENIED = "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch"


def headless_command(binary: str, script: Path) -> list[str]:
    return [binary, "-p", _workflow_prompt(script), "--model", MODEL,
            "--tools", HEADLESS_TOOLS, "--allowedTools", HEADLESS_TOOLS, "--disallowedTools", HEADLESS_DENIED,
            "--permission-mode", "dontAsk", "--strict-mcp-config",
            "--output-format", "json", "--json-schema", json.dumps(OUTPUT_SCHEMA)]


# 지금 도는 헤드리스 자식 — 러너가 종료 신호를 받으면 그 프로세스 묶음을 함께 끝낸다.
_child: subprocess.Popen[str] | None = None


def _stop_child() -> None:
    if _child is not None and _child.poll() is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(_child.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            _child.wait(timeout=10)
        if _child.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(_child.pid, signal.SIGKILL)


def _on_signal(signum: int, _frame: Any) -> None:
    _stop_child()
    raise SystemExit(128 + signum)  # finally가 돌아 «못 읽음» 화면과 상태를 남긴다


# 단계마다 사본에 싣지 않는 칸. 판독 단계에는 GT가 한 글자도 실리지 않는다.
_GT_KEYS = ("gt", "alternatives")


def stage_args(workflow_args: dict[str, Any], stage: str, readings: dict[str, Any] | None = None) -> dict[str, Any]:
    if stage == "read":
        return {**{key: value for key, value in workflow_args.items() if key not in _GT_KEYS}, "stage": "read"}
    return {**workflow_args, "stage": "defend", "readings": readings or {}}


def _run_workflow(task_id: str, workflow_args: dict[str, Any], log: Path, run_dir: Path | None = None) -> Path:
    """워크플로우 한 단계를 헤드리스 Claude Code로 돌리고 출력 파일 경로를 돌려준다."""
    global _child
    binary = claude_binary()
    if not binary:
        raise RuntimeError("이 컴퓨터에서 Claude Code(claude)를 찾지 못했습니다 — 개발자에게 전해 주세요.")
    folder = _dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    # 사본은 작업 폴더 안에 둔다(워크플로우 도구가 읽을 수 있는 자리). 단계가 끝나면 지운다 — 머리말 «눈가림».
    script = embedded_workflow(workflow_args, folder / f"gt-review.{task_id}.{workflow_args.get('stage') or 'both'}.js")
    try:
        lock = _held.get(folder)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"\n== {_now()} claude -p (workflow {workflow_args.get('stage') or 'both'}) ==\n")
            handle.flush()
            # 잠금 파일을 자식에게도 물려준다 — 러너가 강제로 죽어도 자식이 끝날 때까지 둘째 워크플로우가 시작되지 않는다.
            _child = subprocess.Popen(headless_command(binary, script), cwd=PROJECT_ROOT, env=_headless_env(),
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=handle, text=True,
                                      start_new_session=True, pass_fds=(lock.fileno(),) if lock is not None else ())
            try:
                stdout, _ = _child.communicate(timeout=WORKFLOW_TIMEOUT)
            except subprocess.TimeoutExpired:
                _stop_child()
                raise
            finally:
                if _child.poll() is None:
                    _stop_child()
                _child = None
            handle.write(stdout[-4000:] + "\n")
    finally:
        script.unlink(missing_ok=True)
    try:
        reply = json.loads(stdout)
    except ValueError:
        raise RuntimeError("Claude Code가 알아볼 수 없는 답을 냈습니다.") from None
    text = reply.get("result") or ""
    if "does not support this model" in text:
        raise RuntimeError(f"이 컴퓨터의 Claude Code가 판독 모델({MODEL})을 모르는 옛 판입니다 — 개발자에게 Claude Code를 올려 달라고 전해 주세요.")
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
    try:
        raw = json.loads(Path(output).read_text(encoding="utf-8"))
    except ValueError:
        raise RuntimeError("AI가 이번에 읽지 못했습니다.") from None
    returned = raw.get("result") if isinstance(raw.get("result"), dict) else raw
    if workflow_args.get("items") and returned.get("batchId") != workflow_args.get("batchId"):
        # 인자가 워크플로우에 닿지 않았다 — 빈 결과를 화면에 붙이면 «못 읽음»도 아닌 이상한 화면이 된다.
        raise RuntimeError("AI 판독이 시작되지 못했습니다. 다시 눌러 주세요.")
    return Path(output)


def run(task_id: str, reread: bool = False, limit: int | None = None) -> dict[str, Any]:
    import gt_review as review  # 늦게 — gt_review가 이 모듈의 gate를 import한다

    profile = review.find_profile(task_id)  # 읽기만 — 잠금보다 먼저 해도 된다
    folder = lock_dir(profile)
    handle = _try_lock(folder, patience=1.0)
    if handle is None:
        raise Busy(_busy_message(_read_state(folder)))
    _held[folder] = handle
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, _on_signal)
    # 옛 판의 러너가 프로젝트 안에 남긴 GT 든 파일 — 판독자가 닿을 수 있는 자리라 지운다(머리말 «눈가림»).
    for leftover in [*folder.glob("workflow-args.*.json"), *folder.glob("gt-review.*.js")]:  # 죽은 러너가 남긴 사본까지
        leftover.unlink(missing_ok=True)
    log = folder / f"{profile['id']}.log"
    say = lambda **fields: _write_state(folder, **fields)  # noqa: E731
    try:
        say(task=profile["id"], taskName=review.call_name(profile), pid=os.getpid(), startedAt=_now(), phase="prepare",
            message="볼 칸을 고르는 중입니다.", reread=reread, finishedAt=None, warnings={}, items=None, newScreen=None)
        # 잠금을 쥐었는데 준비 중인 배치가 있으면 죽은 배치다 — 워크플로우는 이 러너 안에서만 돈다(머리말).
        stale = bool(review.status_of(profile).get("preparing"))
        namespace = argparse.Namespace(task=task_id, limit=limit, key=None, reread=reread, force=stale)
        out = _call(review.cmd_prepare, namespace)
        payload = json.loads(out[out.index("{"):]) if "{" in out else {}
        workflow_args = payload.get("workflowArgs")
        # 사람에게 전할 경고(사진을 못 찾음 같은) — 스킬이 작업 목록(GT 값이 든 파일)을 열지 않고 읽게 상태에 둔다.
        say(warnings=payload.get("warnings") or {})
        if workflow_args is None:
            say(phase="done", message=payload.get("note") or "볼 칸이 없습니다.", finishedAt=_now(), newScreen=False)
            return status(folder)
        prepared = True
        try:
            if workflow_args.get("items"):
                count = len(workflow_args["items"])
                say(phase="workflow", items=count, message=f"AI가 {count}건의 사진을 읽는 중입니다. 몇 분 걸립니다.")
                output = _run_workflow(profile["id"], stage_args(workflow_args, "read"), log, folder)
                raw = json.loads(output.read_text(encoding="utf-8"))
                read = raw.get("result") if isinstance(raw.get("result"), dict) else raw
                readings = {item["id"]: item["reading"] for item in read.get("items") or [] if item.get("reading")}
                if readings and not read.get("needsRestart"):
                    say(phase="defend", message="판독이 GT와 다른 칸을 반론 AI가 다시 보는 중입니다.")
                    try:
                        output = _run_workflow(profile["id"], stage_args(workflow_args, "defend", readings), log, folder)
                    except RuntimeError as error:
                        # 반론만 실패했다 — 받은 판독은 버리지 않는다. 반론이 없는 칸은 «못 읽음»으로 남아 다시 읽기 대상이 된다.
                        with log.open("a", encoding="utf-8") as trace:
                            trace.write(f"{_now()} 반론 단계 멈춤(판독만으로 화면을 만든다): {error!r}\n")
                say(phase="finish", message="화면을 만드는 중입니다.")
                _call(review.cmd_finish, argparse.Namespace(task=task_id, source=str(output), sweep=None))
            else:
                _call(review.cmd_render, argparse.Namespace(task=task_id, sweep=None))
            prepared = False
        finally:
            if prepared:
                # 판독이 돌아오지 않았다 — «준비 중»으로 두지 않고 칸마다 «AI가 못 읽음»인 화면을 만든다(스킬의 결과 표와 같다).
                with contextlib.suppress(Exception):
                    _call(review.cmd_render, argparse.Namespace(task=task_id, sweep=None))
        say(phase="done", message="새 후보가 준비됐습니다.", finishedAt=_now(), newScreen=True)
        return status(folder)
    except BaseException as error:  # noqa: BLE001 — 무엇이 멈췄든 사람에게 한 문장은 남긴다
        with log.open("a", encoding="utf-8") as trace:
            trace.write(f"{_now()} 멈춤: {error!r}\n")
        if isinstance(error, subprocess.TimeoutExpired):
            message = "AI 판독이 너무 오래 걸려 멈췄습니다. 다시 눌러 주세요."
        elif isinstance(error, (RuntimeError, ValueError)):
            message = str(error)
        else:
            message = "준비가 도중에 멈췄습니다."
        say(phase="failed", message=message, finishedAt=_now())
        if not isinstance(error, Exception):
            raise
        return status(folder)
    finally:
        _held.pop(folder, None)
        _release(handle)


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
    # `python3 gt_next.py`로 띄우면 이 파일이 __main__과 gt_next(gt_review가 import) 두 벌로 올라와 «쥔 잠금» 기록이 갈린다 —
    # 그러면 러너 안의 prepare가 러너 자신의 잠금에 막힌다. 늘 import한 한 벌로 돈다.
    import gt_next
    sys.exit(gt_next.main())
