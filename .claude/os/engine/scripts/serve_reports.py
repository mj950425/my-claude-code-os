#!/usr/bin/env python3
"""산출물을 로컬 웹으로 띄운다. 여는 순간 GT 정정 후보가 나온다.

왜 서버인가 — 보고서는 파일이라 열 때마다 경로를 찾아야 하고, 어느 것이 최신인지
파일 이름으로는 알 수 없다. 상시 뜬 프로세스 하나가 그 둘을 없앤다. 주소는 고정이고,
경로는 요청이 올 때마다 `run-summary.json`의 `artifacts` 선언에서 다시 읽는다.
사이클을 다시 돌리면 새로고침만으로 바뀐다.

**메뉴를 두지 않는다.** 전에는 홈에 카드 넷을 놓았는데, 그 화면이 스스로 문제를 만들었다 —
「GT 정정 후보」와 「의심되는 GT 찾기」가 같은 상품 열한 건을 담고 있었는데 카드 문구는
서로 다른 묶음처럼 말했고, 「재판독 판정」은 위 둘과 한 건도 겹치지 않으면서 «위 보고서의
주장이 서지 않는다»고 적혀 있었다. **고를 것이 없는데 고르게 만드는 화면**이었다.

그래서 서버는 이제 고르지 않는다. 뿌리를 열면 GT 정정 후보로 곧장 보낸다. 나머지 보고서는
그 화면 안의 상대 링크로 이어져 있으므로, 리다이렉트 한 번이 갈 곳을 전부 살려 둔다.

**여기서 숫자를 만들지 않는다.** 세는 일은 보고서가 한다. 서버는 «어디에 그 숫자가 있는지»만
가리킨다. 이제는 그것조차 화면에 그리지 않으므로, 보고서와 어긋날 자리 자체가 없다.

## 쓰는 자리는 하나다 — 승인

전에는 아무것도 쓰지 않았다. 그래서 보고서가 「이 GT를 이렇게 고치자」고 열 건을 늘어놔도
사람은 그 화면에서 답할 수 없었다. 터미널을 열고, 상품 키를 옮겨 적고, 플래그 여섯 개를
채워야 한 건이 기록됐다. **읽는 자리와 답하는 자리가 갈려 있으면 답은 안 쌓인다.**

그래서 문을 냈다 — **원장마다 하나씩, 셋이다.** `POST /decide`는 감사 사이클의 원장(`runs/<id>/review/decisions.json`)에,
`POST /gt-decide`는 GT 개선 과제의 원장(`.claude/gt/<id>/gt-review/decisions.json`)에, `POST /incr-decide`는 증분 검수의 원장
(`.claude/incr/<id>/decisions.jsonl`)에 쓴다. 읽는 문은 `/decided`(사이클)·`/gt-decided`(과제)·`/incr-data`(증분)와 화면으로 보내는
`/gt/<과제ID>`·`/incr`다. 세 쓰기 문이 지키는 선은 같다.

1. **원장과 그 파생물 말고는 아무것도 쓰지 않는다.** 보고서도 정책도 서버가 건드리지 않는다. 파생물은 둘이다 — «반영하기»(`/gt-export`,
   정정 목록을 만들고 원본 GT에 넣는다)와 «결과 파일 만들기»(`/incr-export`, 증분 원장에서 라벨 붙은 줄). 둘 다 CLI와 같은 함수다.
2. **규격은 서버가 정하지 않는다.** `record_review_decision.record()`·`gt_decisions.record()`를 그대로 지난다 —
   터미널로 들어온 판정과 버튼으로 들어온 판정이 같은 검사를 받아야 원장이 한 벌로 남는다.
3. **사람의 클릭만 받는다.** JSON 본문만 받고 다른 출처의 요청은 거절한다. 브라우저의
   평범한 폼은 JSON을 보낼 수 없으므로, 다른 페이지가 몰래 판정을 심을 수 없다.

**«다음 후보 받기»(`POST /gt-next`)와 증분의 «AI 추론»(`POST /incr-run`)도 서버가 쓰지 않는다.** 러너(`gt_next.py run`)를 떼어 띄우고, 상태(`GET /gt-next`)를 읽어 줄 뿐이다.
준비·판독·화면은 러너가 쓴다. 한 번에 하나는 러너의 잠금이 지킨다.

쓰고 나면 파생기를 다시 돌려 «방금 그것이 GT에 나갔는지, 미결 판례에 막혔는지»를
그 자리에서 돌려준다. 그 갈림은 서버가 판단하지 않는다 — `build_gt_decisions.derive()`가 낸다.

속성을 모른다. 어떤 속성이 있는지는 프로필을 훑어서 알고, 어느 파일을 여는지는
`run-summary.json`의 `artifacts`가 선언한 키로만 안다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from build_gt_decisions import derive
from catalog_profile import PROJECT_ROOT, discover_profiles, load_profile, output_root, project_path
from gt_decisions import DecisionRejected as FieldDecisionRejected
from gt_decisions import answered_on_page, reasked
from gt_decisions import current_answers as current_field_answers
from gt_decisions import apply as apply_fields
from gt_decisions import export as export_fields
from gt_decisions import record as record_field
from gt_next import lock_dir as next_lock_dir
from gt_next import running as next_running
from gt_next import status as next_status
from gt_review import answered_questions, home_rows
from gt_task import TaskError, field_map, gt_value, load_gt, load_task
import incr_review
from record_review_decision import DecisionRejected, record

DEFAULT_PORT = 7391

# «다음 후보 받기» — 러너가 잠금을 쥐고 제 상태를 쓸 때까지 기다리는 최대 시간(초). 파이썬 기동과 과제 읽기에 몇 초 걸린다.
NEXT_START_WAIT = 30.0

# 승인 본문이 이 크기를 넘으면 읽지 않는다. 판정 한 줄은 몇 백 바이트다.
MAX_BODY = 64 * 1024

# 뿌리를 열면 가는 곳. 파일 이름이 아니라 `artifacts`가 선언한 **키**를 적는다 —
# 파일 이름을 여기 적으면 렌더러가 이름을 바꿀 때 조용히 끊긴다.
LANDING_ARTIFACT = "gtFixesReport"
# 첫 화면 — GT 개선 과제 목록. 고정된 파일 한 장이고 과제를 모른다: 목록과 수는 /gt-tasks(JSON)에서 읽는다.
# 서버가 화면을 그리지 않는다는 선은 그대로다 — 이 파일을 보내고, 수는 엔진의 status와 같은 함수가 센다.
HOME_PAGE = Path(__file__).resolve().parent.parent / "templates" / "gt-home.html"
# 증분 검수 — 목록과 검수 화면이 한 장이다(주소의 task·batch로 가른다). 이것도 고정 파일이고 수는 /incr-tasks·/incr-data(JSON)에서 온다.
INCR_PAGE = Path(__file__).resolve().parent.parent / "templates" / "incr.html"

MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".jsonl": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
}


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class Attribute:
    """프로필 하나와 그 산출물. 산출물은 읽기만 한다 — 쓰는 것은 판정 원장 하나뿐이다."""

    def __init__(self, profile_path: Path) -> None:
        self.profile = load_profile(profile_path)
        self.id = str(self.profile["id"])
        self.root = output_root(self.profile)
        self.summary = read_json(self.root / "run-summary.json", {})

    @property
    def has_run(self) -> bool:
        return bool(self.summary)

    def waiting(self) -> dict[str, str]:
        """미결 판례에 막혀 GT로 못 나간 판정. 파생기가 쓴 파일을 그대로 읽는다.

        여기서 판례 상태를 다시 보지 않는다. 막을지 말지는 파생기의 규칙이고,
        서버가 같은 판단을 한 벌 더 가지면 화면과 파일이 다른 말을 하게 된다.
        """
        gt = (self.profile.get("gt") or {}).get("path")
        if not gt:
            return {}
        path = project_path(str(gt)).parent / "from-decisions" / "pending-precedent.jsonl"
        held: dict[str, str] = {}
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    held[str(entry.get("productKey"))] = str(entry.get("waitingOn") or "")
        except (OSError, json.JSONDecodeError):
            return {}
        return held

    def artifact(self, key: str) -> Path | None:
        """선언된 산출물만 연다. 선언에 없으면 서버에도 없는 것으로 둔다."""
        declared = self.summary.get("artifacts")
        if not isinstance(declared, dict) or not declared.get(key):
            return None
        path = project_path(str(declared[key]))
        return path if path.is_file() else None


def scan() -> list[Attribute]:
    found = []
    for path in discover_profiles():
        try:
            found.append(Attribute(path))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda item: (not item.has_run, item.id))


def artifact_location(attribute: Attribute, key: str) -> str | None:
    """선언된 산출물의 주소. 선언에 없으면 없는 것으로 둔다 — 경로를 관습으로 추측하지 않는다."""
    path = attribute.artifact(key)
    if path is None:
        return None
    return f"/f/{urllib.parse.quote(attribute.id)}/{path.relative_to(attribute.root).as_posix()}"


class Handler(BaseHTTPRequestHandler):
    server_version = "CatalogOS"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"{datetime.now():%H:%M:%S} {self.address_string()} {fmt % args}\n")

    def send(self, body: bytes, kind: str = "text/html; charset=utf-8", status: int = 200, refresh: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        if refresh:
            self.send_header("Refresh", str(refresh))
        self.send_header("Content-Length", str(len(body)))
        # 산출물은 사이클마다 갈린다. 새로고침이 항상 지금 파일을 보게 한다.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def fail(self, status: int, message: str, refresh: int | None = None) -> None:
        """실패는 글자로만 답한다. 꾸밀 화면이 없으므로 꾸미는 코드도 두지 않는다. `refresh`는 기다리면 풀리는 실패에만 —
        브라우저가 그 초마다 다시 물어, 준비가 끝나면 그 탭이 곧 화면이 된다."""
        self.send(f"{status} {message}\n".encode(), "text/plain; charset=utf-8", status, refresh)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def pick(self, found: list[Attribute], query: dict[str, list[str]]) -> Attribute | None:
        wanted = (query.get("a") or [""])[0]
        for item in found:
            if item.id == wanted:
                return item
        return found[0] if found else None

    def serve_file(self, attribute: Attribute, rel: str) -> None:
        """산출물 폴더 안에서만 연다. 상대 링크가 살아 있도록 폴더 구조를 그대로 노출한다."""
        try:
            path = (attribute.root / urllib.parse.unquote(rel)).resolve()
            path.relative_to(attribute.root.resolve())
        except (ValueError, OSError):
            return self.fail(HTTPStatus.FORBIDDEN, "산출물 폴더 밖은 열지 않습니다.")
        if not path.is_file():
            return self.fail(HTTPStatus.NOT_FOUND, f"파일이 없습니다: {rel}")
        kind = MIME.get(path.suffix.lower(), "application/octet-stream")
        try:
            self.send(path.read_bytes(), kind)
        except OSError as error:
            self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def send_json(self, value: Any, status: int = 200) -> None:
        self.send(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def read_submission(self) -> dict[str, Any]:
        """승인 본문. 사람의 클릭이 아닌 것은 여기서 걸러진다.

        JSON만 받는 이유는 CSRF 때문이다. 다른 페이지가 몰래 만든 평범한 폼은
        `application/json`을 보낼 수 없고, 스크립트로 보내면 브라우저가 먼저 물어본다.
        이 서버는 사람의 컴퓨터에서 아무 인증 없이 도는 자리라 그 선이 유일한 문턱이다.
        """
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            raise DecisionRejected("JSON 본문만 받습니다.")
        origin = self.headers.get("Origin")
        if origin:
            # 자기 출처(스킴·호스트·포트)만 — 같은 컴퓨터의 다른 로컬 서버(localhost:3000 같은)도 다른 출처다.
            parsed = urllib.parse.urlparse(origin)
            host = self.headers.get("Host") or ""
            same_port = not host or f"{parsed.hostname}:{parsed.port or 80}" == (host if ":" in host else f"{host}:80")
            if parsed.hostname not in ("127.0.0.1", "localhost") or not same_port:
                raise DecisionRejected(f"다른 출처의 요청은 받지 않습니다: {origin}")
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            raise DecisionRejected("다른 출처의 요청은 받지 않습니다.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as error:
            raise DecisionRejected("본문 길이를 읽지 못했습니다.") from error
        if length <= 0 or length > MAX_BODY:
            raise DecisionRejected("본문 크기가 규격을 벗어났습니다.")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DecisionRejected(f"JSON을 읽지 못했습니다: {error}") from error
        if not isinstance(body, dict):
            raise DecisionRejected("본문은 객체여야 합니다.")
        return body

    def export_field_list(self) -> None:
        """화면의 «반영하기» — 원장에서 정정 목록을 만들고(CLI `export`) 곧바로 원본 GT에 넣는다(CLI `apply --yes`). 같은 두 함수다.
        누르는 것이 곧 «넣어줘»다 — 목록을 따로 보고 한 번 더 말하던 단계를 없앴다. 넣기 전 원본은 옆에 사본으로 남고(`apply`),
        원본은 git이 기억하므로 되돌릴 수 있다. 원본이 상류(시트)에서 다시 만들어지는 과제만 예외로 미리 보기에 머문다 —
        파일에 넣어도 다음 새로 고침에서 덮이기 때문이다."""
        try:
            body = self.read_submission()
            wanted = str(body.get("task") or "")
            attribute = next((item for item in scan() if item.id == wanted and item.profile.get("gtTask")), None)
            if attribute is None:
                raise FieldDecisionRejected(f"GT 개선 과제를 찾지 못했습니다: {wanted}")
            # 원본이 다른 원천(시트)에서 다시 만들어지는 과제는 목록을 «건넸다»는 기록이 남는다 — 화면 버튼으로는 미리 보기만 한다.
            # 붙여 넣을 Excel 목록은 Claude가 «반영해줘»로 만들어 연다.
            upstream = bool(((attribute.profile.get("gtTask") or {}).get("gt") or {}).get("upstream"))
            summary = export_fields(attribute.profile, record_handout=not upstream)
            lines = []
            listing = project_path(summary["files"]["corrections.jsonl"])
            for raw in listing.read_text(encoding="utf-8").splitlines() if listing.is_file() and not upstream else []:
                if raw.strip():
                    item = json.loads(raw)
                    if not item.get("applied"):
                        lines.append({"key": item.get("id"), "field": item.get("field"), "before": item.get("before"),
                                      "after": item.get("after"), "reviewer": item.get("reviewer")})
            # 방금 만든 목록 그대로 넣는다. 그사이 판정이 늘었거나 원본이 바뀌었으면 `apply`가 거절한다 — 다시 누르면 된다.
            applied = None if upstream else apply_fields(attribute.profile, confirm=True)
        except (FieldDecisionRejected, DecisionRejected) as rejected:
            return self.send_json({"ok": False, "error": str(rejected)}, HTTPStatus.BAD_REQUEST)
        except (TaskError, OSError, ValueError, KeyError) as error:
            self.log_message("gt-export 설정 문제: %s", error)
            return self.send_json({"ok": False, "error": "반영하지 못했습니다 — Claude에게 «반영해줘»라고 말해 주세요."},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)
        keep = ("corrections", "alreadyApplied", "confirmations", "thisBatch", "linesToChange", "outOfPolicy", "stale", "upstream")
        result = {name: summary.get(name) for name in keep}
        result["applied"] = bool(applied and applied.get("applied"))
        result["linesChanged"] = (applied or {}).get("linesToChange", 0) if result["applied"] else 0
        return self.send_json({"ok": True, "summary": result, "lines": lines[:200]})

    def start_next(self) -> None:
        """«다음 후보 받기». 준비·판독·화면은 러너(`gt_next.py run`)가 따로 떠서 하고, 서버는 띄우기만 한다 —
        서버는 여전히 아무것도 쓰지 않는다. 한 번에 하나는 러너의 잠금이 지킨다: 여기서 먼저 보고 거절하는 것은
        사람에게 빨리 답하려는 것일 뿐, 두 요청이 같이 들어와도 둘째 러너는 잠금을 못 얻고 아무것도 하지 않은 채 끝난다."""
        try:
            body = self.read_submission()
            wanted = str(body.get("task") or "")
            attribute = next((item for item in scan() if item.id == wanted and item.profile.get("gtTask")), None)
            if attribute is None:
                raise FieldDecisionRejected(f"GT 개선 과제를 찾지 못했습니다: {wanted}")
            # 러너가 잠그는 자리와 같은 자리를 본다 — 과제의 산출물 폴더 옆(runs/.gt-next).
            folder = next_lock_dir(attribute.profile)
            # 스킬도 이 문으로 온다 — «못 읽은 거 다시 봐줘»(reread)와 «20건씩»(limit)도 같은 러너가 돈다.
            extra = ["--reread"] if body.get("reread") is True else []
            if isinstance(body.get("limit"), int) and not isinstance(body.get("limit"), bool) and body["limit"] >= 0:
                extra += ["--limit", str(body["limit"])]
        except (FieldDecisionRejected, DecisionRejected) as rejected:
            return self.send_json({"ok": False, "error": str(rejected)}, HTTPStatus.BAD_REQUEST)
        now = next_running(folder)
        if now is not None:
            return self.send_json({"ok": False, "busy": True, "error": "이미 AI가 검수중입니다."}, HTTPStatus.CONFLICT)
        # 서버와 떨어져 돈다 — 서버를 다시 켜도(serve.sh restart) 판독은 멈추지 않는다.
        runner = subprocess.Popen([sys.executable, str(Path(__file__).with_name("gt_next.py")), "run", "--task", wanted, *extra],
                                  cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        # 러너가 잠금을 쥐고 제 상태를 쓸 때까지 기다린 뒤 답한다. 곧바로 답하면 화면의 첫 조회가 러너보다 먼저 도착해
        # 지난 실행의 상태를 이번 결과로 읽는다. 답에는 러너의 pid를 싣는다 — 화면과 스킬은 그 pid의 상태만 이번 것으로 본다.
        deadline = time.monotonic() + NEXT_START_WAIT
        while time.monotonic() < deadline:
            state = next_status(folder)
            if state.get("pid") == runner.pid:
                return self.send_json({"ok": True, "started": wanted, "pid": runner.pid, "startedAt": state.get("startedAt")})
            if runner.poll() is not None:
                # 제 상태를 한 줄도 못 쓰고 끝났다 — 잠금을 못 얻었거나(3) 과제를 읽다 멈췄다.
                if runner.returncode == 3:
                    return self.send_json({"ok": False, "busy": True, "error": "이미 AI가 검수중입니다."}, HTTPStatus.CONFLICT)
                return self.send_json({"ok": False, "error": "준비를 시작하지 못했습니다 — Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요."},
                                      HTTPStatus.INTERNAL_SERVER_ERROR)
            time.sleep(0.2)
        return self.send_json({"ok": False, "error": "준비가 시작되지 않았습니다 — 잠시 뒤 다시 눌러 주세요."},
                              HTTPStatus.INTERNAL_SERVER_ERROR)

    def decide_field(self) -> None:
        """GT 개선 과제의 칸 하나. `/decide`와 같은 선을 지킨다 — JSON만, 로컬 출처만,
        규격은 기록기(`gt_decisions.record`)가 정한다. 서버는 원장 말고 아무것도 쓰지 않는다."""
        try:
            body = self.read_submission()
            wanted = str(body.get("task") or "")
            attribute = next((item for item in scan() if item.id == wanted and item.profile.get("gtTask")), None)
            if attribute is None:
                raise FieldDecisionRejected(f"GT 개선 과제를 찾지 못했습니다: {wanted}")
            proposal = body.get("proposal")
            # 화면이 보여 준 GT 값을 늘 함께 받는다. 그사이 원본이 바뀌었으면 기록기가 거절한다.
            if "expectedBefore" not in body:
                raise FieldDecisionRejected("화면이 보여 준 GT 값이 요청에 없습니다. 화면을 새로고침해 주세요.")
            # 새로고침하지 않은 옛 탭의 클릭. 옛 배치로 적힌 보류는 새 화면에서 «지난번 보류»가 된다 — 받지 않는다.
            manifest = read_json(attribute.root / "gt-review" / "manifest.json", {})
            current_batch = manifest.get("batchId")
            if not body.get("batch"):
                raise FieldDecisionRejected("어느 화면에서 누른 것인지 알 수 없습니다 — 화면을 새로고침해 주세요.")
            if not current_batch:
                raise FieldDecisionRejected("준비된 화면이 없습니다 — Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요.")
            if body.get("batch") != current_batch:
                raise FieldDecisionRejected(
                    "AI가 새 후보를 보는 중입니다 — Claude가 새 화면을 열어 드릴 때까지 기다려 주세요." if manifest.get("preparing")
                    else "새 화면이 준비됐습니다 — 이 탭은 지난 화면입니다. 새로고침해 주세요.")
            # 화면이 본 그 칸의 마지막 판정 — 없으면 옛 화면(이 대조를 모르는 탭)이다. 다른 사람의 답을 조용히 덮지 않게 받지 않는다.
            if "expectedLatest" not in body:
                raise FieldDecisionRejected("화면이 오래됐습니다 — 새로고침한 뒤 다시 눌러 주세요.")
            entry = record_field(
                attribute.profile,
                key=str(body.get("key") or ""),
                field=str(body.get("field") or ""),
                decision=str(body.get("decision") or ""),
                reviewer=str(body.get("reviewer") or ""),
                value=body.get("value") or None,
                reason=str(body.get("reason") or ""),
                proposal=proposal if isinstance(proposal, dict) else None,
                expected_before=body.get("expectedBefore"),
                channel="screen-bulk" if body.get("bulk") is True else "screen",
                batch=str(body.get("batch") or "") or None,
                gap=bool(body.get("gap")),
                expected_latest=body.get("expectedLatest"),
            )
        except (FieldDecisionRejected, DecisionRejected) as rejected:
            # 기록기의 거절은 사람에게 쓴 문장이다. 그대로 보인다.
            return self.send_json({"ok": False, "error": str(rejected)}, HTTPStatus.BAD_REQUEST)
        except (TaskError, ValueError) as broken:
            # GT·설정 문제의 원문에는 파일 경로·설정 키가 있다 — 운영팀 화면에 내지 않고 서버 기록에만 남긴다.
            self.log_message("gt-decide 설정 문제: %s", broken)
            return self.send_json({"ok": False, "error": "GT 원본이나 설정에 문제가 있습니다 — Claude에게 알려 주세요."},
                                  HTTPStatus.BAD_REQUEST)
        except (OSError, ValueError) as error:
            return self.send_json({"ok": False, "error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return self.send_json({"ok": True, "decision": entry})

    def incr_post(self, route: str) -> None:
        """증분 검수의 세 문 — 판정(`/incr-decide` → 증분 원장), AI 추론 띄우기(`/incr-run` → 러너를 떼어 띄우기만),
        결과 파일(`/incr-export` → 원장의 파생물). 셋 다 CLI와 **같은 함수**다(`incr_review.record`·`start`·`export`)."""
        try:
            body = self.read_submission()
            wanted = str(body.get("task") or "")
            attribute = next((item for item in scan() if item.id == wanted and item.profile.get("gtTask")), None)
            if attribute is None:
                raise incr_review.IncrRejected(f"과제를 찾지 못했습니다: {wanted}")
            # 쓰는 문은 묶음을 암묵으로 고르지 않는다 — 같은 키가 두 묶음에 있으면 판정이 엉뚱한 묶음에 적힌다.
            if not body.get("batch"):
                raise incr_review.IncrRejected("어느 묶음에서 누른 것인지 알 수 없습니다 — 화면을 새로고침해 주세요.")
            batch = incr_review.pick_batch(attribute.profile, str(body["batch"]))
            if route == "/incr-decide":
                if "expectedAi" not in body or "expectedLatest" not in body:
                    raise incr_review.IncrRejected("화면이 보여 준 AI 제안·지난 판정이 요청에 없습니다 — 화면을 새로고침해 주세요.")
                entry = incr_review.record(
                    attribute.profile, batch, key=str(body.get("key") or ""), field=str(body.get("field") or ""),
                    decision=str(body.get("decision") or ""), reviewer=str(body.get("reviewer") or ""),
                    value=body.get("value"), reason=str(body.get("reason") or ""), expected_ai=body.get("expectedAi"),
                    gap=body.get("gap") is True, expected_latest=body.get("expectedLatest"),
                    channel="screen-recheck" if body.get("recheck") is True else "screen-bulk" if body.get("bulk") is True else "screen")
                # 수는 status가 센 그대로 돌려준다 — 화면이 원장을 다시 세지 않게(규칙 8).
                return self.send_json({"ok": True, "decision": entry, "status": incr_review.status(attribute.profile, batch)})
            if route == "/incr-export":
                return self.send_json(incr_review.export(attribute.profile, batch))
            folder = next_lock_dir(attribute.profile)
            if next_running(folder) is not None:
                return self.send_json({"ok": False, "busy": True, "error": incr_review.BUSY}, HTTPStatus.CONFLICT)
            runner = incr_review.start(attribute.profile, batch, reread=body.get("reread") is True)
            # 러너가 잠금을 쥐고 제 상태(pid)를 쓸 때까지 기다린 뒤 답한다 — «다음 후보 받기»와 같은 이유(곧바로 답하면 화면의 첫 조회가
            # 러너보다 먼저 와서 «아무것도 안 돈다»로 읽고 멈춘다). 잠금을 못 얻고 끝났으면(3) 바쁨이다.
            deadline = time.monotonic() + NEXT_START_WAIT
            while time.monotonic() < deadline:
                state = incr_review.runner_state(attribute.profile, batch)
                if state.get("pid") == runner.pid:
                    if state.get("phase") == "failed":  # 잠금을 못 얻은 러너가 남긴 «바쁨»
                        return self.send_json({"ok": False, "busy": True, "error": state.get("message") or incr_review.BUSY},
                                              HTTPStatus.CONFLICT)
                    return self.send_json({"ok": True, "started": batch, "pid": runner.pid})
                if runner.poll() is not None:
                    if runner.returncode == 3:
                        return self.send_json({"ok": False, "busy": True, "error": incr_review.BUSY},
                                              HTTPStatus.CONFLICT)
                    return self.send_json({"ok": False, "error": "AI 추론을 시작하지 못했습니다 — Claude에게 «증분 AI 다시 돌려줘»라고 말해 주세요."},
                                          HTTPStatus.INTERNAL_SERVER_ERROR)
                time.sleep(0.2)
            return self.send_json({"ok": False, "error": "AI 추론이 시작되지 않았습니다 — 잠시 뒤 다시 눌러 주세요."},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)
        except (incr_review.IncrRejected, DecisionRejected) as rejected:
            return self.send_json({"ok": False, "error": str(rejected)}, HTTPStatus.BAD_REQUEST)
        except (TaskError, OSError, ValueError, KeyError) as error:
            self.log_message("incr 설정 문제: %s", error)
            return self.send_json({"ok": False, "error": "과제 설정이나 증분 파일에 문제가 있습니다 — Claude에게 알려 주세요."},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)

    def local_host(self) -> bool:
        """Host 머리가 이 컴퓨터를 가리키는가. DNS 리바인딩(남의 이름이 127.0.0.1로 풀리는 페이지)은
        같은 출처로 원장·GT·사진을 읽을 수 있다 — Origin 검사는 쓰기만 막으므로 읽기도 여기서 막는다."""
        host = (self.headers.get("Host") or "").strip()
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        if name in ("127.0.0.1", "localhost", "[::1]"):
            return True
        self.fail(HTTPStatus.FORBIDDEN, "이 컴퓨터의 주소(127.0.0.1)로만 열 수 있습니다.")
        return False

    def do_POST(self) -> None:
        if not self.local_host():
            return None
        parsed = urllib.parse.urlparse(self.path)
        if (parsed.path.rstrip("/") or "/") == "/gt-decide":
            return self.decide_field()
        if (parsed.path.rstrip("/") or "/") == "/gt-export":
            return self.export_field_list()
        if (parsed.path.rstrip("/") or "/") == "/gt-next":
            return self.start_next()
        if (parsed.path.rstrip("/") or "/") in ("/incr-decide", "/incr-run", "/incr-export"):
            return self.incr_post(parsed.path.rstrip("/"))
        if (parsed.path.rstrip("/") or "/") != "/decide":
            return self.fail(HTTPStatus.NOT_FOUND, "그런 자리는 없습니다.")

        found = scan()
        if not found:
            return self.fail(HTTPStatus.NOT_FOUND, "프로필을 찾지 못했습니다.")
        try:
            body = self.read_submission()
            attribute = self.pick(found, {"a": [str(body.get("attribute") or "")]})
            if attribute is None:
                raise DecisionRejected("속성을 찾지 못했습니다.")
            # 서버는 규격을 고르지 않는다. 어떤 필드가 필요한지는 기록기가 정한다 —
            # 여기서 한 번 더 검사하면 두 규격이 생기고, 곧 서로 어긋난다.
            entry = record(
                profile=attribute.profile,
                root=attribute.root,
                product_key=str(body.get("productKey") or ""),
                decision=str(body.get("decision") or ""),
                reviewer=str(body.get("reviewer") or ""),
                reason=str(body.get("reason") or ""),
                corrected_label=body.get("correctedLabel") or None,
                confirmed_label=body.get("confirmedLabel") or None,
                precedent_id=body.get("precedentId") or None,
                no_precedent=bool(body.get("noPrecedent")),
                rule_id=body.get("ruleId") or None,
                supersedes=body.get("supersedes") or None,
                allow_unqueued=bool(body.get("allowUnqueued")),
            )
        except DecisionRejected as rejected:
            # 거절 사유는 사람이 읽을 문장 그대로 돌려준다. 화면이 다시 쓰지 않아야
            # 규격을 고쳤을 때 안내도 같이 바뀐다.
            return self.send_json({"ok": False, "error": str(rejected)}, HTTPStatus.BAD_REQUEST)
        except (OSError, ValueError) as error:
            return self.send_json({"ok": False, "error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        # 방금 그것이 GT까지 갔는지, 미결 판례에 막혔는지. 서버가 판단하지 않고 파생기가 낸다.
        outcome = derive(attribute.profile, attribute.root)
        holder = outcome.get("blockedBy", {}).get(entry["productKey"], "")
        return self.send_json({
            "ok": True,
            "decision": entry,
            "waitingOn": holder,
            "reachedGt": not holder and entry["decision"] in ("GOLDEN_CONFIRMED", "GOLDEN_CORRECTION_NEEDED"),
        })

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self.local_host():
            return None
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if route == "/healthz":
            return self.send(b"ok\n", "text/plain; charset=utf-8")

        # 매 요청마다 다시 읽는다. 사이클을 다시 돌리면 새로고침만으로 바뀐다.
        found = scan()
        if not found:
            return self.fail(HTTPStatus.NOT_FOUND, "프로필을 찾지 못했습니다.")

        if route == "/" and HOME_PAGE.is_file() and any(item.profile.get("gtTask") for item in found):
            # GT 개선 과제가 있으면 첫 화면은 과제 목록이다. 감사 사이클의 화면은 목록 아래 «이 목록 밖의 GT»에서 /audit로 간다.
            return self.send(HOME_PAGE.read_bytes(), "text/html; charset=utf-8")

        if route == "/gt-next":
            # 과제를 주면 그 과제의 잠금 자리를 본다(러너와 같은 자리). 안 주면 기본 자리 — 지금 과제들은 모두 거기서 잠근다.
            wanted = (query.get("task") or [""])[0]
            attribute = next((item for item in found if item.id == wanted and item.profile.get("gtTask")), None)
            return self.send_json(next_status(next_lock_dir(attribute.profile) if attribute else None))

        if route == "/incr" and INCR_PAGE.is_file():
            return self.send(INCR_PAGE.read_bytes(), "text/html; charset=utf-8")

        if route == "/incr-tasks":
            try:
                return self.send_json(incr_review.home_rows())
            except (TaskError, OSError, ValueError) as error:
                self.log_message("incr-tasks 설정 문제: %s", error)
                return self.send_json({"ok": False, "error": "증분 검수 목록을 읽지 못했습니다."}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if route == "/incr-data":
            wanted = (query.get("task") or [""])[0]
            item = next((entry for entry in found if entry.id == wanted and entry.profile.get("gtTask")), None)
            if item is None:
                return self.send_json({"ok": False, "error": f"과제를 찾지 못했습니다: {wanted}"}, HTTPStatus.NOT_FOUND)
            try:
                batch = incr_review.pick_batch(item.profile, (query.get("batch") or [""])[0] or None)
                return self.send_json(incr_review.screen_data(item.profile, batch))
            except incr_review.IncrRejected as rejected:
                return self.send_json({"ok": False, "error": str(rejected)}, HTTPStatus.NOT_FOUND)
            except (TaskError, OSError, ValueError) as error:
                self.log_message("incr-data 설정 문제: %s", error)
                return self.send_json({"ok": False, "error": "증분 검수 화면을 읽지 못했습니다."}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if route == "/gt-tasks":
            try:
                return self.send_json(home_rows())
            except (TaskError, OSError, ValueError) as error:
                self.log_message("gt-tasks 설정 문제: %s", error)
                return self.send_json({"ok": False, "error": "과제 목록을 읽지 못했습니다."}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if route.startswith("/f/"):
            _, _, rest = route[3:].partition("/")
            wanted = route[3:].split("/", 1)[0]
            for item in found:
                if item.id == urllib.parse.unquote(wanted):
                    return self.serve_file(item, rest)
            return self.fail(HTTPStatus.NOT_FOUND, "속성을 찾지 못했습니다.")

        if route == "/gt" or route.startswith("/gt/"):
            # GT 개선 과제의 화면. 주소는 과제 ID 하나로 끝난다 — 운영팀이 경로를 알 필요가 없다.
            # 과제를 안 적으면 가장 최근에 준비된 화면으로 보낸다. 고를 것이 없는데 고르게 하지 않는다.
            tasks = [item for item in found if item.profile.get("gtTask")]
            wanted = urllib.parse.unquote(route[4:]) if route.startswith("/gt/") else ""
            if wanted:
                tasks = [item for item in tasks if item.id == wanted]
            # 화면의 자리는 렌더러가 선언한다(`gt-review/manifest.json`). 관습으로 추측하지 않는다.
            pages = []
            preparing = []
            for item in tasks:
                manifest = read_json(item.root / "gt-review" / "manifest.json", {})
                if manifest.get("preparing"):
                    preparing.append(item.id)
                page = item.root / "gt-review" / str(manifest.get("page") or "") if manifest.get("page") else None
                if page is not None and page.is_file():
                    pages.append((item, page))
            if not pages and preparing:
                # prepare는 끝났고 AI 판독이 도는 중이다. «다시 요청하라»고 하면 도는 배치를 지우고 새로 시작한다.
                return self.fail(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "AI가 새 후보를 보는 중입니다 — 끝나면 Claude가 화면을 열어 드립니다. 새로 요청하지 말고 잠시 기다려 주세요. "
                    "10분이 지나도 그대로면 Claude에게 «GT 화면 다시 열어줘»라고 말해 주세요(지금까지 준비된 것으로 화면을 엽니다). "
                    "이 탭은 20초마다 스스로 다시 봅니다.",
                    refresh=20,
                )
            if not pages and wanted and tasks:
                # 준비 전 과제 — 막다른 404 대신 첫 화면의 그 과제 카드로(«○○ 개선해줘»라고 말하는 법이 거기 있다)
                return self.redirect(f"/#task-{urllib.parse.quote(wanted)}")
            if not pages:
                return self.fail(
                    HTTPStatus.NOT_FOUND,
                    f"아직 준비된 GT 개선 화면이 없습니다{f': {wanted}' if wanted else ''}. "
                    "Claude Code에 «GT 개선해줘»라고 요청하면 만들어집니다.",
                )
            item, page = max(pages, key=lambda pair: pair[1].stat().st_mtime)
            return self.redirect(f"/f/{urllib.parse.quote(item.id)}/{page.relative_to(item.root).as_posix()}")

        if route == "/gt-qa":
            # 검수 문답 — 사람이 AI의 물음에 답한 판정. 읽기만 한다(원장이 정본). 정책 페이지가 열릴 때 읽는다.
            wanted = (query.get("task") or [""])[0]
            item = next((entry for entry in found if entry.id == wanted and entry.profile.get("gtTask")), None)
            if item is None:
                return self.send_json({"ok": False, "error": f"GT 개선 과제를 찾지 못했습니다: {wanted}"}, HTTPStatus.NOT_FOUND)
            try:
                return self.send_json({"ok": True, "answered": answered_questions(item.profile)})
            except (TaskError, OSError, ValueError) as error:
                return self.send_json({"ok": False, "error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        if route == "/gt-decided":
            # 화면이 열릴 때 읽는 원장. 칸마다 마지막 판정을 그대로 넘기고, «이 화면의 답인가»는
            # status와 **같은 함수**(answered_on_page)로 정해 칸마다 참·거짓으로 함께 넘긴다 — 규칙을 화면의
            # 스크립트에 한 벌 더 두면 모순 칸·지난 배치 같은 가장자리에서 둘이 어긋난다(실제로 어긋났다).
            wanted = (query.get("task") or [""])[0]
            item = next((entry for entry in found if entry.id == wanted and entry.profile.get("gtTask")), None)
            if item is None:
                return self.send_json({"ok": False, "error": f"GT 개선 과제를 찾지 못했습니다: {wanted}"}, HTTPStatus.NOT_FOUND)
            latest = current_field_answers(item.profile)
            # 화면에 있는 칸의 지금 GT 값. 화면을 그린 뒤 원본이 바뀐 칸을 화면이 알아보게 한다 —
            # 모르면 사람은 «새로고침»만 되풀이한다(화면의 값은 준비할 때 굳었으므로).
            current: dict[str, Any] = {}
            answered: dict[str, bool] = {}
            review = read_json(item.root / "gt-review" / "review.json", {})
            if review:
                try:
                    task = load_task(item.profile)
                    rows = load_gt(item.profile, task)
                    fields = field_map(task)
                    for entry in review.get("items") or []:
                        for cell in entry.get("cells") or []:
                            name = f"{entry['key']}\u0000{cell['field']}"
                            if entry["key"] in rows and cell["field"] in fields:
                                current[name] = gt_value(task, fields[cell["field"]], rows[entry["key"]])
                            answered[name] = answered_on_page(
                                latest.get((entry["key"], cell["field"])), current.get(name, cell.get("current")),
                                review.get("batchId"), reasked(cell))
                except (ValueError, OSError):
                    current = {}
            # 지금 배치. 열어 둔 옛 탭이 «새 화면이 있다»를 알게 한다 — 모르면 옛 탭에서 계속 답하고 다 했다고 여긴다.
            manifest = read_json(item.root / "gt-review" / "manifest.json", {})
            return self.send_json({"ok": True,
                                   "latest": {f"{key}\u0000{field}": entry for (key, field), entry in latest.items()},
                                   "current": current, "answered": answered,
                                   "batchId": manifest.get("batchId") or review.get("batchId"),
                                   "preparing": bool(manifest.get("preparing"))})

        attribute = self.pick(found, query)
        if attribute is None:
            return self.fail(HTTPStatus.NOT_FOUND, "속성을 찾지 못했습니다.")

        if route == "/decided":
            # 이미 답한 건이 무엇인지. 새로고침해도 승인 자국이 남으려면 화면이 이걸 읽어야 한다.
            # 원장과 파생 파일을 **그대로** 넘긴다. 무엇이 지금 유효한 판정인지, 무엇이 미결
            # 판례에 막혔는지는 파생기가 정하는 규칙이라, 여기서 흉내 내면 답이 두 벌이 된다.
            ledger = read_json(attribute.root / "review" / "decisions.json", {})
            return self.send_json({
                "attribute": attribute.id,
                "decisions": ledger.get("decisions") or [],
                "waiting": attribute.waiting(),
            })

        # 선언된 산출물 아무거나 여는 자리. 뿌리도 이 길을 쓴다.
        key = LANDING_ARTIFACT if route in ("/", "/audit") else (query.get("k") or [""])[0] if route == "/r" else None
        if key is None:
            return self.fail(HTTPStatus.NOT_FOUND, f"그런 화면은 없습니다: {route}")
        location = artifact_location(attribute, key)
        if location is None:
            return self.fail(HTTPStatus.NOT_FOUND, f"선언된 산출물이 없습니다: {key}")
        return self.redirect(location)


def main() -> int:
    parser = argparse.ArgumentParser(description="카탈로그 OS 산출물을 로컬 웹으로 띄운다.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CATALOG_OS_PORT", DEFAULT_PORT)))
    parser.add_argument("--host", default=os.environ.get("CATALOG_OS_HOST", "127.0.0.1"))
    args = parser.parse_args()

    found = scan()
    if not found:
        print("프로필을 찾지 못했습니다. 속성 패키지에 profile.json이 필요합니다.", file=sys.stderr)
        return 1

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as error:
        print(f"{args.host}:{args.port}를 열지 못했습니다 — {error}", file=sys.stderr)
        return 1
    server.daemon_threads = True

    print(f"Catalog OS → http://{args.host}:{args.port}", flush=True)
    for item in found:
        mark = "실행 있음" if item.has_run else "실행 전"
        print(f"  · {item.id} ({item.profile['displayName']}) — {mark}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("종료합니다.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
