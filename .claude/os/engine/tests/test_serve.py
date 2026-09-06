#!/usr/bin/env python3
"""산출물 서버가 지키는 두 가지를 확인한다.

1. **숫자를 만들지 않는다.** 화면의 건수는 전부 `run-summary.json`·`run-review.json`·
   `policy-index.json`이 스스로 이름 붙여 기록한 값이어야 한다. 서버가 큐를 다시 세면
   보고서와 조용히 어긋나고, 어긋난 채로 판단 근거가 된다(프로젝트 규칙 8).
2. **속성을 모른다.** 가짜 속성 하나만 두고 서버를 띄워도 네 화면이 전부 떠야 한다.
   특정 속성의 이름·라벨을 아는 순간 «속성 폴더를 지워도 엔진이 돈다»가 깨진다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude").is_dir():
            return parent
    raise RuntimeError("프로젝트 루트를 찾지 못했습니다.")


PROJECT_ROOT = _find_project_root()
OS_ROOT = PROJECT_ROOT / ".claude/os"
SERVE = OS_ROOT / "engine/scripts/serve_reports.py"


def load(name: str) -> object:
    """서버 모듈을 import해 순수 함수만 직접 부른다. 포트를 열지 않는다."""
    sys.path.insert(0, str(OS_ROOT / "engine/scripts"))
    try:
        module = __import__("serve_reports")
        return getattr(module, name)
    finally:
        sys.path.pop(0)


class MarkdownTest(unittest.TestCase):
    """정책과 판례는 손으로 쓴 마크다운이다. 원문이 진실이므로 변환은 원문을 바꾸지 않아야 한다."""

    def test_front_matter_is_stripped_not_rendered(self) -> None:
        front_matter = load("front_matter")
        meta, body = front_matter("---\nid: X-1\nstatus: OPEN\n---\n\n# 제목\n")
        self.assertEqual(meta["id"], "X-1")
        self.assertEqual(meta["status"], "OPEN")
        self.assertNotIn("---", body)

    def test_markdown_escapes_html_in_the_source(self) -> None:
        render = load("render_markdown")
        out = render("<script>alert(1)</script> 문장", lambda target: None)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_links_outside_the_os_folder_render_as_text(self) -> None:
        """서버는 프로젝트 밖을 열어 주지 않는다. 링크를 거는 대신 글자로만 남긴다."""
        render = load("render_markdown")
        out = render("[비밀](/etc/passwd)을 연다", lambda target: None)
        self.assertNotIn("href", out)
        self.assertIn("비밀", out)

    def test_tables_and_lists_survive(self) -> None:
        render = load("render_markdown")
        out = render("| ID | 상태 |\n|---|---|\n| A | OPEN |\n\n- 하나\n", lambda target: None)
        self.assertIn("<table>", out)
        self.assertIn("OPEN", out)
        self.assertIn("<li>하나</li>", out)


class EngineNeverInventsACountTest(unittest.TestCase):
    """서버 코드가 큐를 직접 세지 않는지 본다. 세는 일은 보고서의 몫이다."""

    def test_no_queue_recount_in_the_server(self) -> None:
        body = SERVE.read_text(encoding="utf-8")
        for forbidden in ('/ "queue"', "'queue'", '"queue"', "len(rows)", "decidableNow"):
            self.assertNotIn(
                forbidden,
                body,
                f"서버가 세거나 이름과 다른 값을 씁니다({forbidden}). "
                "건수는 요약 파일이 이름 붙였고 그 이름대로 세는 값만 씁니다.",
            )


class ServesAnyAttributeTest(unittest.TestCase):
    """이름을 모르는 가짜 속성 하나로 네 화면이 전부 떠야 한다."""

    def build(self, root: Path) -> Path:
        run = root / "run"
        (run / "reports").mkdir(parents=True)
        (run / "review").mkdir(parents=True)
        (run / "run-review").mkdir(parents=True)
        (run / "policy").mkdir(parents=True)
        policy_dir = root / "policy"
        (policy_dir / "precedents").mkdir(parents=True)
        (policy_dir / "policy.md").write_text(
            "---\nid: widget-finish\nversion: 3\nowner: tester\n---\n\n## 허용값\n\n- `MATTE`\n",
            encoding="utf-8",
        )
        (policy_dir / "precedents/WF-0001.md").write_text(
            "---\nid: WF-0001\nstatus: OPEN\n---\n\n# 질문\n\n광택을 어떻게 가르나?\n",
            encoding="utf-8",
        )
        (run / "reports/one.html").write_text("<!doctype html><p>보고서</p>", encoding="utf-8")
        (run / "run-summary.json").write_text(
            json.dumps(
                {
                    "profileId": "widget-finish",
                    "generatedAt": "2026-09-06T00:00:00+00:00",
                    "products": 40,
                    "surfaceAccuracy": 0.5,
                    "primaryFinding": {"label": "표면 후보", "count": 7, "description": "설명"},
                    "artifacts": {
                        "gtFixesReport": str(run / "reports/one.html"),
                        "policyGapReport": str(run / "reports/one.html"),
                        "htmlReport": str(run / "reports/one.html"),
                        "decisionLedger": str(run / "review/decisions.json"),
                    },
                }
            ),
            encoding="utf-8",
        )
        (run / "run-review/run-review.json").write_text(
            json.dumps(
                {
                    "verdict": "WARN",
                    "reviewLoad": {
                        "decidableNow": 5, "blockedProducts": 2,
                        "noConflictProducts": 9, "pendingProducts": 16,
                        "blockedByPrecedent": {"WF-0001": 2},
                    },
                }
            ),
            encoding="utf-8",
        )
        (run / "policy/policy-index.json").write_text(
            json.dumps(
                {
                    "counts": {"precedents": 1, "decided": 0, "questions": 1,
                               "questionsResolved": 0, "untrackedReviewViolations": 0},
                    "owned": {"path": str(policy_dir / "policy.md"), "version": "3",
                              "owner": "tester", "labels": ["MATTE"], "sha256": "abc123def"},
                    "precedents": [{"id": "WF-0001", "status": "OPEN", "answers": ["WQ-1"],
                                    "path": str(policy_dir / "precedents/WF-0001.md")}],
                    "questionPrecedents": {"WQ-1": ["WF-0001"]},
                }
            ),
            encoding="utf-8",
        )
        (run / "reports/policy-questions.json").write_text(
            json.dumps([{"id": "WQ-1", "question": "광택을 어떻게 가르나?",
                         "impact": {"affectedRows": 4}, "recommendation": "판례를 만든다"}]),
            encoding="utf-8",
        )
        (run / "review/status.json").write_text(
            json.dumps({"queuedProducts": 16, "adjudicatedProducts": 0, "pendingProducts": 16}),
            encoding="utf-8",
        )
        profile = root / "profile.json"
        profile.write_text(
            json.dumps(
                {
                    "schemaVersion": "catalog-data-profile-v1",
                    "id": "widget-finish",
                    "displayName": "표면 처리 감사",
                    "attributeName": "표면 처리",
                    "subjectName": "부품",
                    "outputRoot": str(run),
                    "labels": ["MATTE"],
                }
            ),
            encoding="utf-8",
        )
        return profile

    def test_every_screen_answers_for_an_attribute_the_engine_never_saw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build(root)
            sys.path.insert(0, str(OS_ROOT / "engine/scripts"))
            try:
                import serve_reports as srv

                attribute = srv.Attribute(root / "profile.json")
                found = [attribute]
                for name, page in (
                    ("홈", srv.page_home), ("GT 개선", srv.page_gt),
                    ("정책 보기", srv.page_policy), ("정책 개선", srv.page_gaps),
                ):
                    body = page(attribute, found).decode("utf-8")
                    self.assertIn("표면 처리 감사", body, f"{name}이 속성 이름을 싣지 않았습니다.")
                    self.assertNotIn("None", body, f"{name}에 빈 값이 새어 나왔습니다.")

                home = srv.page_home(attribute, found).decode("utf-8")
                self.assertIn("표면 후보", home, "홈이 요약의 primaryFinding 라벨을 쓰지 않았습니다.")
                # 홈은 큐의 잔여를 앞세우지 않는다. `decidableNow`는 이름과 달리
                # 라벨이 이미 일치하는 건과 심판이 «확정 불가»라 적은 건까지 세므로,
                # 「지금 가를 수 있는」으로 읽히면 그 자체가 틀린 판단 근거가 된다.
                for stale in (">5<", "지금 가를 수 있는", "지금 사람이 가를 수 있는"):
                    self.assertNotIn(stale, home, f"홈이 큐 잔여를 다시 앞세웁니다: {stale}")
            finally:
                sys.path.pop(0)

    def test_a_run_that_never_happened_says_so(self) -> None:
        """실행 전 속성도 화면이 떠야 한다. 빈 화면 대신 다음 한 걸음을 적는다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run").mkdir()
            (root / "profile.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "catalog-data-profile-v1",
                        "id": "empty-one", "displayName": "아직 안 돌린 속성",
                        "attributeName": "무엇", "subjectName": "무엇",
                        "outputRoot": str(root / "run"),
                    }
                ),
                encoding="utf-8",
            )
            sys.path.insert(0, str(OS_ROOT / "engine/scripts"))
            try:
                import serve_reports as srv

                attribute = srv.Attribute(root / "profile.json")
                self.assertFalse(attribute.has_run)
                body = srv.page_home(attribute, [attribute]).decode("utf-8")
                self.assertIn("run.sh", body, "다음 한 걸음을 적지 않았습니다.")
            finally:
                sys.path.pop(0)


class ProcessTest(unittest.TestCase):
    """serve.sh가 «두 번 눌러도 하나»를 지키는지, 죽은 PID에 속지 않는지 본다."""

    def test_start_is_idempotent_and_status_matches(self) -> None:
        serve = OS_ROOT / "serve.sh"
        port = "7519"
        env = {"CATALOG_OS_PORT": port, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(serve), *args], capture_output=True, text=True, env=env, timeout=30
            )

        run("stop")
        try:
            first = run("start")
            self.assertEqual(first.returncode, 0, first.stderr)
            again = run("start")
            self.assertIn("이미 떠 있습니다", again.stdout)
            self.assertEqual(run("status").returncode, 0)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
                self.assertEqual(response.read().strip(), b"ok")
        finally:
            run("stop")
        self.assertNotEqual(run("status").returncode, 0, "종료 후에도 떠 있다고 말합니다.")


if __name__ == "__main__":
    unittest.main()
