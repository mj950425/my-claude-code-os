"""증분 검수 — 밀어넣기 → AI 추론(워크플로우 read 단계) → 사람 클릭 → 라벨 붙은 파일.

골든셋 검수의 가짜 과제(`test_gt_review.Fixture`)를 그대로 쓴다 — 증분은 과제를 따로 선언하지 않고 골든셋 검수의 선언을 쓰기 때문이다.
워크플로우는 돌리지 않고 그 반환값을 흉내 낸다(러너 테스트와 같은 방식). 서버의 문은 진짜 서버를 임시 과제에 띄워 두드린다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_gt_review import PROJECT_ROOT, SCRIPTS, Fixture, write_jsonl  # noqa: E402

sys.path.insert(0, str(SCRIPTS))

import gt_next  # noqa: E402
import incr_review  # noqa: E402

ROWS = [
    {"sku": "N1", "category": "잡화>테스트", "notice": "첫째 고시문"},
    # 답 열·출처 열이 섞여 와도 판독자에게 가지 않는다
    {"sku": "N2", "category": "잡화>테스트", "notice": "둘째 고시문", "color": "RED", "src": "USER_OK"},
]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        self.fx = Fixture(root)
        self.env = mock.patch.dict(os.environ, {**{k: v for k, v in self.fx.env.items() if k.startswith("CATALOG_OS_")},
                                                "CATALOG_OS_INCR_ROOT": str(root / "incr")})
        self.env.start()
        self.lock = mock.patch.object(gt_next, "lock_dir", lambda profile: root / "lock")
        self.lock.start()
        self.profile = self.fx.saved_profile()
        self.data = root / "data"
        write_jsonl(self.data / "incr.jsonl", ROWS)
        # 증분 전용 사진 색인 — 과제 색인과 같은 모양(listField pics)에 새 키만
        write_jsonl(self.data / "incr-images.jsonl", [
            {"sku": row["sku"], "category": row["category"], "notice": row["notice"],
             "pics": [{"f": "img/a.jpg", "pid": f"{row['sku']}-1", "kind": "THUMB"}]} for row in ROWS])
        self.pushed = incr_review.push(self.profile, self.data / "incr.jsonl", self.data / "incr-images.jsonl", name="첫 증분")
        self.batch = self.pushed["batch"]

    def tearDown(self) -> None:
        self.lock.stop()
        self.env.stop()
        self.temp.cleanup()

    def fake_workflow(self, values: dict[str, str], seen: dict | None = None, fields: tuple = ("color", "finish", "sheen", "tones")):
        """워크플로우 흉내 — 받은 인자로 판독 결과를 쓴다(건 id → 색). 받은 인자를 남겨 눈가림을 확인한다."""
        rows = {"color": None, "finish": ("GLOSSY", "HIGH"), "sheen": ("HIGH", "LOW"), "tones": ("WARM", "HIGH")}

        def run(task_id, args, log, folder):
            if seen is not None:
                seen.update(args)
            items = []
            for item in args["items"]:
                readings = []
                for field in fields:
                    value, confidence = (values.get(item["id"], ""), "HIGH") if field == "color" else rows[field]
                    readings.append({"field": field, "value": value, "confidence": confidence, "evidenceImageIds": ["P01"],
                                     "observation": "보인다"})
                items.append({"id": item["id"], "reading": {"readings": readings}})
            out = self.root / "out.json"
            out.write_text(json.dumps({"result": {"batchId": args["batchId"], "items": items}}), encoding="utf-8")
            return out

        return run

    def read(self, values: dict[str, str], reread: bool = False, **kw) -> dict:
        with mock.patch.object(gt_next, "_run_workflow", self.fake_workflow(values, **kw)):
            return incr_review.run(self.profile, self.batch, reread=reread)

    def ai(self, key: str, field: str):
        readings = json.loads((incr_review.work_dir(self.profile, self.batch) / "readings.json").read_text(encoding="utf-8"))
        return ((readings.get(key) or {}).get("cells") or {}).get(field, {}).get("value")

    def label(self, key: str, field: str, value, **kw) -> dict:
        return incr_review.record(self.profile, self.batch, key, field, "LABEL", "민준", value=value,
                                  expected_ai=self.ai(key, field), **kw)


class FlowTest(Base):
    def test_ai_reads_first_without_answers_then_a_click_records(self) -> None:
        self.assertEqual(self.pushed["rowsWithPhotos"], 2)
        self.assertEqual(self.pushed["answerColumnsAlreadyFilled"], ["color"], "결과 파일이 덮을 열을 밀어넣을 때 알린다")
        seen: dict = {}
        state = self.read({"IN-001": "RED", "IN-002": "BLUE"}, seen=seen)
        self.assertEqual(state["itemsRead"], 2, state["runner"])
        self.assertFalse(state["runner"]["lockBusy"], "러너가 잠금을 푼 뒤에 셈한다")
        # 판독 단계만 — GT도 대체 정답도 없고, 판독자 파일에는 키도, 섞여 온 답 열·출처 열도 없다
        self.assertEqual(seen["stage"], "read")
        self.assertNotIn("gt", seen)
        for item in seen["items"]:
            view = Path(item["view"]) if Path(item["view"]).is_absolute() else PROJECT_ROOT / item["view"]
            text = view.read_text(encoding="utf-8")
            for leak in ("N1", "N2", "USER_OK", '"color": "RED"'):
                self.assertNotIn(leak, text)
        self.assertFalse((incr_review.work_dir(self.profile, self.batch) / "workflow-args.json").exists())
        # AI 추천만으로는 원장에 한 줄도 생기지 않는다
        self.assertEqual(incr_review.read_ledger(self.profile), [])

        entry = self.label("N1", "color", "RED", channel="screen")
        self.assertTrue(entry["agreedWithAi"])
        changed = self.label("N2", "color", "GREEN", channel="screen")
        self.assertFalse(changed["agreedWithAi"])
        self.assertEqual(changed["aiValue"], "BLUE")
        with self.assertRaises(incr_review.IncrRejected):
            self.label("N1", "color", "PINK")
        with self.assertRaises(incr_review.IncrRejected):
            incr_review.record(self.profile, self.batch, "N1", "color", "LABEL", "", value="RED", expected_ai="RED")
        # 값 여럿 칸은 순서 없는 집합으로 받는다
        tones = self.label("N1", "tones", "WARM|COOL")
        self.assertEqual(tones["value"], "COOL|WARM")
        again = self.label("N1", "tones", "WARM", channel="screen-bulk")
        self.assertEqual(again["supersedes"], tones["decisionId"])

        # 모든 칸을 확정한 줄만 결과 파일로
        for field, value in (("finish", "GLOSSY"), ("sheen", "HIGH")):
            self.label("N1", field, value)
        incr_review.record(self.profile, self.batch, "N2", "finish", "HOLD", "민준", expected_ai="GLOSSY")
        result = incr_review.export(self.profile, self.batch)
        self.assertEqual((result["labeled"], result["pending"]), (1, 1))
        labeled = [json.loads(line) for line in (self.root / "incr" / self.profile["id"] / "labeled" /
                                                 f"{self.batch}.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((labeled[0]["sku"], labeled[0]["color"], labeled[0]["tones"]), ("N1", "RED", ["WARM"]))
        self.assertEqual(labeled[0]["colorSource"], "INCR_REVIEW_AI_AGREED")

        status = incr_review.status(self.profile, self.batch)
        self.assertEqual((status["itemsDone"], status["cellsHeld"]), (1, 1))
        screen = incr_review.screen_data(self.profile, self.batch)
        self.assertEqual(screen["items"][0]["reading"]["cells"]["color"]["value"], "RED")
        self.assertTrue(screen["items"][0]["images"][0]["url"].startswith(f"/f/{self.profile['id']}/incr/"))

    def test_a_decision_on_an_ai_value_the_person_did_not_see_is_refused(self) -> None:
        self.read({"IN-001": "RED"})
        with self.assertRaises(incr_review.IncrRejected) as refused:
            incr_review.record(self.profile, self.batch, "N1", "color", "LABEL", "민준", value="RED", expected_ai="BLUE")
        self.assertIn("새로고침", str(refused.exception))
        with self.assertRaises(incr_review.IncrRejected):
            incr_review.record(self.profile, self.batch, "N1", "color", "LABEL", "민준", value="RED", expected_ai=None)

    def test_reread_takes_only_missing_or_partial_readings_and_a_full_run_keeps_done_items(self) -> None:
        # 첫 판독이 색 칸을 빠뜨렸다 — 칸이 빠진 판독은 «못 읽음»이다
        self.read({"IN-001": "RED"}, fields=("finish", "sheen", "tones"))
        self.assertEqual(incr_review.status(self.profile, self.batch)["itemsReadComplete"], 0)
        again = incr_review.prepare(self.profile, self.batch, reread=True)
        self.assertEqual(len(again["items"]), 2)
        self.read({"IN-001": "RED", "IN-002": "BLUE"}, reread=True)
        self.assertIsNone(incr_review.prepare(self.profile, self.batch, reread=True))
        # N1을 끝낸 뒤 처음부터 다시 읽으면 N1(확정)은 남기고 N2만 다시 읽는다
        for field in ("color", "finish", "sheen", "tones"):
            self.label("N1", field, self.ai("N1", field))
        fresh = incr_review.prepare(self.profile, self.batch)
        self.assertEqual(len(fresh["items"]), 1)
        self.assertIsNotNone(self.ai("N1", "color"))
        self.assertIsNone(self.ai("N2", "color"), "확정 안 한 건의 지난 판독은 버린다(사진 번호가 바뀔 수 있다)")

    def test_an_output_from_another_run_is_refused(self) -> None:
        incr_review.prepare(self.profile, self.batch)
        stale = self.root / "stale.json"
        stale.write_text(json.dumps({"result": {"batchId": "옛-실행", "items": []}}), encoding="utf-8")
        with self.assertRaises(incr_review.IncrRejected):
            incr_review.finish(self.profile, self.batch, stale)

    def test_the_ledger_is_read_through_the_current_policy(self) -> None:
        self.read({"IN-001": "RED", "IN-002": "RED"})
        for field in ("color", "finish", "sheen", "tones"):
            self.label("N1", field, self.ai("N1", field))
        self.assertEqual(incr_review.status(self.profile, self.batch)["itemsDone"], 1)
        # 정책에서 RED를 빼면 그 판정은 확정이 아니다 — 다시 묻는다
        self.fx.profile["gtTask"]["fields"][0]["labels"] = ["BLUE", "GREEN", "PURPLE"]
        self.fx.profile["gtTask"]["fields"][0]["legacy"] = {"CRIMSON": "BLUE"}
        self.fx.save()
        self.profile = self.fx.saved_profile()
        status = incr_review.status(self.profile, self.batch)
        self.assertEqual((status["itemsDone"], status["cellsOutOfPolicy"]), (0, 1))
        self.assertEqual(incr_review.export(self.profile, self.batch)["labeled"], 0)
        # 코드를 바꾸면(RED → SCARLET, legacy에 적음) 지난 판정도 새 코드로 읽힌다
        self.fx.profile["gtTask"]["fields"][0]["labels"] = ["SCARLET", "BLUE", "GREEN", "PURPLE"]
        self.fx.profile["gtTask"]["fields"][0]["labelNames"]["SCARLET"] = "다홍"
        self.fx.profile["gtTask"]["fields"][0]["legacy"] = {"RED": "SCARLET"}
        self.fx.save()
        self.profile = self.fx.saved_profile()
        self.assertEqual(incr_review.status(self.profile, self.batch)["itemsDone"], 1)
        incr_review.export(self.profile, self.batch)
        line = json.loads((self.root / "incr" / self.profile["id"] / "labeled" / f"{self.batch}.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(line["color"], "SCARLET")


class PushTest(Base):
    def test_push_refuses_duplicate_keys_before_writing(self) -> None:
        bad = self.root / "dup.jsonl"
        write_jsonl(bad, [{"sku": "Z"}, {"sku": "Z"}])
        with self.assertRaises(incr_review.IncrRejected):
            incr_review.push(self.profile, bad)
        self.assertEqual(incr_review.batches(self.profile), [self.batch])

    def test_a_broken_image_index_leaves_no_half_batch(self) -> None:
        broken = self.root / "broken-images.jsonl"
        write_jsonl(broken, [{"sku": "N1", "pics": []}, {"sku": "N1", "pics": []}])  # 목록 색인에서 키가 겹친다
        with self.assertRaises(incr_review.TaskError):
            incr_review.push(self.profile, self.data / "incr.jsonl", broken)
        self.assertEqual(incr_review.batches(self.profile), [self.batch], "반쯤 만든 묶음이 «가장 최근»이 되지 않는다")

    def test_a_held_lock_is_refused_and_leaves_a_sentence_for_the_screen(self) -> None:
        handle = gt_next._try_lock(self.root / "lock")
        try:
            code = ("import sys; sys.path.insert(0, %r); import gt_next, incr_review\n"
                    "from pathlib import Path\n"
                    "gt_next.lock_dir = lambda p: Path(%r)\n"
                    "try:\n incr_review.run(incr_review.find_profile(%r), %r)\nexcept gt_next.Busy: print('busy')\n") % (
                str(SCRIPTS), str(self.root / "lock"), self.fx.profile_path.as_posix(), self.batch)
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ.copy())
            self.assertIn("busy", out.stdout, out.stderr)
            state = incr_review.runner_state(self.profile, self.batch)
            self.assertEqual(state["phase"], "failed")
            self.assertIn("다른 AI 작업", state["message"])
        finally:
            gt_next._release(handle)


class ServerTest(Base):
    """진짜 서버를 임시 과제에 띄워 세 문을 두드린다."""

    def setUp(self) -> None:
        super().setUp()
        import serve_reports

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), serve_reports.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def post(self, path: str, body: dict, origin: str | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
            return json.loads(response.read())

    def test_the_doors(self) -> None:
        self.read({"IN-001": "RED", "IN-002": "BLUE"})
        task = self.profile["id"]
        base = {"task": task, "key": "N1", "field": "color", "decision": "LABEL", "value": "RED", "reviewer": "민준"}
        # 묶음을 암묵으로 고르지 않는다 · 본 AI 값이 없으면 받지 않는다 · 다른 출처는 거절한다
        self.assertEqual(self.post("/incr-decide", {**base, "expectedAi": "RED"})[0], 400)
        self.assertEqual(self.post("/incr-decide", {**base, "batch": self.batch})[0], 400)
        self.assertEqual(self.post("/incr-decide", {**base, "batch": self.batch, "expectedAi": "RED"}, origin="https://evil.example")[0], 400)
        self.assertEqual(incr_review.read_ledger(self.profile), [])
        code, reply = self.post("/incr-decide", {**base, "batch": self.batch, "expectedAi": "RED", "bulk": True})
        self.assertEqual(code, 200, reply)
        self.assertEqual(reply["decision"]["channel"], "screen-bulk")
        self.assertEqual(reply["status"]["cellsLabeled"], 1, "수는 status가 센 그대로 돌아온다")
        # 다른 AI 작업이 잠금을 쥐고 있으면 409
        handle = gt_next._try_lock(self.root / "lock")
        try:
            code, reply = self.post("/incr-run", {"task": task, "batch": self.batch})
            self.assertEqual(code, 409, reply)
        finally:
            gt_next._release(handle)
        code, reply = self.post("/incr-export", {"task": task, "batch": self.batch})
        self.assertEqual((code, reply["labeled"]), (200, 0))
        listing = self.get("/incr-tasks")
        self.assertEqual(listing["itemsLeft"], 2)
        data = self.get(f"/incr-data?task={task}&batch={urllib.parse.quote(self.batch)}")
        self.assertEqual(len(data["items"]), 2)


class ScreenTest(unittest.TestCase):
    def test_every_menu_opens_the_same_task_list_for_incremental_review(self) -> None:
        """증분 검수도 골든셋 검수처럼 서브 메뉴(과제 고르기)가 있다 — 렌더된 화면·첫 화면·증분 화면 모두."""
        from gt_review_render import SIDEBAR_SCRIPT, sidebar_html

        for side in (sidebar_html("review", "t", "/f/t/gt-review/"), sidebar_html("incr", None)):
            self.assertIn('data-menu="incr"', side)
            self.assertIn('href="/incr"', side)
        self.assertIn("'/incr?task=' + encodeURIComponent(task.task)", SIDEBAR_SCRIPT)
        self.assertNotIn('href="review.html"', sidebar_html("incr", None), "과제 없는 화면의 메뉴가 없는 파일을 가리키지 않는다")
        home = (SCRIPTS.parent / "templates" / "gt-home.html").read_text(encoding="utf-8")
        self.assertIn('data-menu="incr"', home)
        self.assertIn('"/incr?task=" + encodeURIComponent(task.task)', home)

    def test_the_screen_is_generated_from_the_golden_review_styles(self) -> None:
        """증분 화면의 CSS는 골든셋 검수의 상수 그대로다 — 두 벌을 두면 조용히 어긋난다. 파일은 생성기 출력과 같아야 한다."""
        import incr_render
        from gt_review_render import EXTRA_STYLE, SIDEBAR_STYLE, THEME_STYLE

        page = (SCRIPTS.parent / "templates" / "incr.html").read_text(encoding="utf-8")
        self.assertEqual(page, incr_render.page(), "templates/incr.html이 뒤처졌습니다 — python3 incr_render.py로 다시 쓰세요")
        for style in (EXTRA_STYLE, THEME_STYLE, SIDEBAR_STYLE):
            self.assertIn(style, page)
        for shared in ('class="masthead"', 'class="statusline"', 'id="viewer"', "'act chips'", "'vmark ai'", "'item-body'", "'gist'"):
            self.assertIn(shared, page, "골든셋 검수와 같은 마크업 클래스")

    def test_the_check_mode_fails_when_the_file_is_stale(self) -> None:
        import incr_render

        with tempfile.TemporaryDirectory() as folder:
            stale = Path(folder) / "incr.html"
            stale.write_text("옛 화면", encoding="utf-8")
            with mock.patch.object(incr_render, "TARGET", stale), mock.patch.object(sys, "argv", ["incr_render.py", "--check"]):
                self.assertEqual(incr_render.main(), 1)
            with mock.patch.object(incr_render, "TARGET", stale), mock.patch.object(sys, "argv", ["incr_render.py"]):
                incr_render.main()
            with mock.patch.object(incr_render, "TARGET", stale), mock.patch.object(sys, "argv", ["incr_render.py", "--check"]):
                self.assertEqual(incr_render.main(), 0)

    def test_incremental_styles_never_override_the_golden_review(self) -> None:
        """증분 전용 CSS(INCR_STYLE)와 목록 표(LIST_STYLE)가 골든셋 검수의 셀렉터를 다시 적으면 두 화면이 갈라진다."""
        import re

        import incr_render
        from gt_review_render import EXTRA_STYLE, LIST_STYLE, SIDEBAR_STYLE, THEME_STYLE
        from page_style import STYLE

        def selectors(css: str) -> set[str]:
            css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            css = re.sub(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)[^{}]*\}", r"\1", css)
            return {part.strip() for block in re.findall(r"([^{}]+)\{[^{}]*\}", css) for part in block.split(",") if part.strip()}

        golden = selectors(STYLE + EXTRA_STYLE + THEME_STYLE + SIDEBAR_STYLE)
        self.assertEqual(selectors(incr_render.INCR_STYLE) & golden, set())
        self.assertEqual(selectors(LIST_STYLE) & golden, set())

    def test_the_home_menu_is_the_shared_sidebar(self) -> None:
        """첫 화면의 사이드바는 손으로 쓴 사본이다 — 메뉴 순서·이름·아이콘·서브 메뉴가 공통 사이드바와 같아야 한다."""
        import re

        from gt_review_render import sidebar_html

        def menu(markup: str) -> list[tuple[str | None, str, str]]:
            nav = markup[markup.index('<nav class="side-nav"'):markup.index("</nav>")]
            return [(key or None, path, label.strip()) for key, path, label in re.findall(
                r'<div class="side-item"(?: data-menu="(\w+)")?>.*?<path d="([^"]+)"></path></svg>([^<]+)', nav, re.S)]

        home = (SCRIPTS.parent / "templates" / "gt-home.html").read_text(encoding="utf-8")
        self.assertEqual(menu(home), menu(sidebar_html("home", None)))
        self.assertEqual(home.count('aria-haspopup="true"'), 4)

    def test_the_screen_does_not_recount_or_misfire(self) -> None:
        page = (SCRIPTS.parent / "templates" / "incr.html").read_text(encoding="utf-8")
        self.assertIn("event.metaKey || event.ctrlKey", page, "⌘A(전체 선택)가 확정이 되지 않는다")
        self.assertIn("event.code === 'KeyA'", page, "한글 입력 중에도 A가 듣는다")
        self.assertIn("expectedAi", page)
        self.assertNotIn("agreed += 1", page, "화면이 원장을 다시 세지 않는다")
        # 채점에서 나온 결함들 — 다시 생기지 않게 자리를 고정한다(동작 자체는 헤드리스 브라우저 채점으로 확인했다)
        self.assertIn("delete PICKS[item.key][field.id]", page, "값 여럿 칸은 기록 뒤 기록된 값에서 다시 시작한다")
        self.assertIn("!n.classList.contains('rested')", page, "보류만 남은 건은 포커스·A·J가 건너뛴다")
        self.assertIn("at(item) > LAST_AT", page, "다음 페이지는 그린 마지막 건 뒤에서 — «남은 건» 보기에서 건을 건너뛰지 않는다")
        self.assertIn("PENDING.push(", page, "이름 없이 누른 판정은 여럿이어도 차례대로 기록된다")
        self.assertIn("INFLIGHT", page, "같은 판정이 겹쳐 와도 한 번만 보낸다")
        self.assertNotIn("innerHTML", page, "데이터는 글자로만 넣는다")

    def test_derived_incremental_files_are_not_tracked(self) -> None:
        for path in (".claude/os/runs/x/incr/b/readings.json", ".claude/incr/x/labeled/b.jsonl"):
            out = subprocess.run(["git", "check-ignore", "-q", path], cwd=PROJECT_ROOT)
            self.assertEqual(out.returncode, 0, f"추적하면 안 되는 파생물: {path}")
        out = subprocess.run(["git", "check-ignore", "-q", ".claude/incr/x/decisions.jsonl"], cwd=PROJECT_ROOT)
        self.assertNotEqual(out.returncode, 0, "사람 판정 원장은 추적한다")


if __name__ == "__main__":
    unittest.main()
