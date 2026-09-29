"""정책 화면에서 고치기 — 허용값 · 관찰 항목 · 값 규칙. 화면(`POST /gt-policy`)과 CLI(`value`·`observe`·`rule --when`)가 같은 함수를 지난다.

확인하는 것:
- 미리 보기는 아무것도 쓰지 않고, 넣기는 버전·변경 이력을 남긴다. 로더가 멈추는 모양이면 쓰지 않는다.
- 값을 빼기 전에 그 값을 내는 규칙이 있으면 막고, GT 칸·판정이 따라 움직이면 미리 보기에서 알린다.
- 새 관찰은 그 관찰을 쓰는 값 규칙과 함께만 들어가고, 넣을 자리를 고를 수 있고, 바뀌는 조합을 미리 보인다.
- 화면이 본 버전과 지금 버전이 다르면(그사이 다른 사람이 고쳤으면) 덮지 않는다.
- 서버는 JSON·로컬 출처만 받고, 넣은 뒤 정책 화면을 다시 그린다. 정책 화면에 편집 칸이 있다.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import subprocess
import sys
import time
import unittest

from test_gt_observe import ObservedFixture
from test_gt_review import SCRIPTS

sys.path.insert(0, str(SCRIPTS))


class EditFixture(ObservedFixture):
    def edit(self, kind: str, action: str, *args: str, check: bool = True):
        return self.fx.task(kind, action, *args, "--reviewer", "민준", check=check)

    def version(self) -> int:
        return int(re.search(r"^version:\s*(\d+)", self.definitions.read_text(encoding="utf-8"), re.M).group(1)) \
            if re.search(r"^version:", self.definitions.read_text(encoding="utf-8"), re.M) else 0


class ValueEditTest(EditFixture):
    def test_add_previews_then_writes(self) -> None:
        before = self.definitions.read_text(encoding="utf-8")
        preview = json.loads(self.edit("value", "add", "--field", "color", "--code", "ORANGE", "--name", "주황", "--desc", "주황색이다.").stdout)
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["after"], "- `ORANGE` 주황 — 주황색이다.")
        self.assertEqual(self.definitions.read_text(encoding="utf-8"), before, "미리 보기는 쓰지 않는다")
        done = json.loads(self.edit("value", "add", "--field", "색", "--code", "ORANGE", "--name", "주황", "--desc", "주황색이다.", "--yes").stdout)
        self.assertTrue(done["applied"])
        text = self.definitions.read_text(encoding="utf-8")
        self.assertIn("- `PURPLE` 보라 — 설명\n- `ORANGE` 주황 — 주황색이다.", text)
        self.assertIn("색 허용값 «ORANGE» 추가 — 민준", text[text.index("## 변경 이력"):])
        values = json.loads(self.fx.task("values", "--field", "color").stdout)["fields"][0]["values"]
        self.assertIn("ORANGE", [row["code"] for row in values])
        self.assertIn("주황", (self.fx.review_dir() / "policy.html").read_text(encoding="utf-8"), "넣으면 정책 화면을 다시 그린다")

    def test_add_refuses_bad_shapes(self) -> None:
        cases = {"영문 대문자로 시작": ("orange", "주황", "설명"), "이미 있습니다": ("RED", "빨강", "설명"),
                 "쓸 수 없는 글자": ("ORANGE", "주황: 밝은", "설명"), "값 설명": ("ORANGE", "주황", "")}
        for expected, (code, name, desc) in cases.items():
            with self.subTest(expected):
                done = self.edit("value", "add", "--field", "color", "--code", code, "--name", name, "--desc", desc, "--yes", check=False)
                self.assertEqual(done.returncode, 2)
                self.assertIn(expected, done.stderr)

    def test_an_observed_field_warns_that_no_rule_gives_the_new_value(self) -> None:
        preview = json.loads(self.edit("value", "add", "--field", "sheen", "--code", "MID", "--name", "중간", "--desc", "중간이다.").stdout)
        self.assertTrue(any("값 규칙" in warning for warning in preview["warnings"]))

    def test_edit_keeps_what_is_not_given(self) -> None:
        self.edit("value", "edit", "--field", "color", "--code", "RED", "--desc", "붉은 계열이다.", "--yes")
        self.assertIn("- `RED` 빨강 — 붉은 계열이다.", self.definitions.read_text(encoding="utf-8"))
        same = self.edit("value", "edit", "--field", "color", "--code", "RED", "--desc", "붉은 계열이다.", "--yes", check=False)
        self.assertIn("바뀌는 것이 없습니다", same.stderr)

    def test_remove_is_blocked_by_rules_and_shows_what_moves(self) -> None:
        self.assertIn("V2", self.edit("value", "remove", "--field", "sheen", "--code", "HIGH", "--yes", check=False).stderr)
        self.assertIn("둘뿐", self.edit("value", "remove", "--field", "finish", "--code", "MATTE", "--yes", check=False).stderr)
        preview = json.loads(self.edit("value", "remove", "--field", "color", "--code", "GREEN").stdout)
        self.assertEqual(set(preview["impact"]), {"gtCells", "correctedTo", "keptAs", "usedInProfile"})
        self.edit("value", "remove", "--field", "color", "--code", "GREEN", "--yes")
        self.assertNotIn("`GREEN`", self.definitions.read_text(encoding="utf-8").split("## 변경 이력")[0])


class ObserveEditTest(EditFixture):
    def test_a_new_observation_comes_with_its_value_rule_at_the_chosen_place(self) -> None:
        preview = json.loads(self.edit("observe", "add", "--field", "sheen", "--id", "LAMP", "--name", "조명", "--desc", "반사가 조명 모양이다.",
                                       "--when", "GLOSS & LAMP", "--value", "NONE", "--before", "V2").stdout)
        self.assertEqual(preview["table"][:3], ["V1 !GLOSS → NONE", "V3 GLOSS & LAMP → NONE", "V2 GLOSS & WIDE → HIGH"])
        self.assertEqual(preview["changes"]["changed"], 2, "LAMP가 참이면 (GLOSS·WIDE 둘) HIGH와 (GLOSS만) LOW가 없음이 된다")
        self.edit("observe", "add", "--field", "sheen", "--id", "LAMP", "--name", "조명", "--desc", "반사가 조명 모양이다.",
                  "--when", "GLOSS & LAMP", "--value", "NONE", "--before", "V2", "--yes")
        text = self.definitions.read_text(encoding="utf-8")
        self.assertIn("- `LAMP` 조명 — 반사가 조명 모양이다.\n\n### 값 규칙", text)
        self.assertLess(text.index("- `V3` `GLOSS & LAMP` → `NONE`"), text.index("- `V2` `GLOSS & WIDE`"))
        self.fx.task("status")

    def test_a_new_observation_is_refused_without_a_rule_that_uses_it(self) -> None:
        cases = {"«새 항목»을 예나 아니오로": ("GLOSS", "NONE"), "완전히 덮여": ("LAMP | !LAMP", "NONE"), "조합(식)과 값": ("", "NONE")}
        for expected, (when, value) in cases.items():
            with self.subTest(expected):
                done = self.edit("observe", "add", "--field", "sheen", "--id", "LAMP", "--name", "조명", "--desc", "조명이다.",
                                 "--when", when, "--value", value, "--yes", check=False)
                self.assertEqual(done.returncode, 2, done.stdout)
                self.assertIn(expected, done.stderr)

    def test_edit_rewrites_the_meaning(self) -> None:
        self.edit("observe", "edit", "--field", "sheen", "--id", "WIDE", "--name", "넓은 반사", "--desc", "반사가 표면 절반 넘게 덮는다.", "--yes")
        self.assertIn("- `WIDE` 넓은 반사 — 반사가 표면 절반 넘게 덮는다.", self.definitions.read_text(encoding="utf-8"))
        leak = self.edit("observe", "edit", "--field", "sheen", "--id", "WIDE", "--name", "넓은 반사", "--desc", "HIGH일 때 보인다.", "--yes", check=False)
        self.assertIn("값 코드", leak.stderr, "판독자에게 가는 뜻에 값 코드는 미리 보기에서 먼저 막는다")
        preview = self.edit("observe", "edit", "--field", "sheen", "--id", "WIDE", "--name", "넓은 반사", "--desc", "HIGH일 때 보인다.", check=False)
        self.assertIn("값 코드", preview.stderr)
        from gt_policy_edit import PolicyEditRejected, edit_policy

        with self.assertRaises(PolicyEditRejected) as caught:
            edit_policy(self.fx.saved_profile(), "derive-add", "sheen", "민준", False, value="HIGH")
        self.assertIn("관찰을 하나 이상", str(caught.exception), "화면의 말로 — CLI 옵션 이름을 말하지 않는다")

    def test_a_stale_screen_does_not_overwrite(self) -> None:
        from gt_policy_edit import PolicyEditRejected, edit_policy

        from gt_policy_edit import policy_stamp

        profile = self.fx.saved_profile()
        seen = policy_stamp(self.definitions)
        self.edit("value", "edit", "--field", "color", "--code", "RED", "--desc", "먼저 고친 사람.", "--yes")
        with self.assertRaises(PolicyEditRejected) as caught:
            edit_policy(profile, "value-edit", "color", "민준", True, expected_stamp=seen, code="RED", desc="늦게 고친 사람.")
        self.assertIn("그사이 정책이 바뀌었습니다", str(caught.exception))


class HardeningTest(EditFixture):
    """적대적 입력과 동시 요청 — 멈추지 않고 문장으로 거절하고, 정책 문서의 모양을 깨지 않는다."""

    def call(self, op: str, field: str = "sheen", confirm: bool = False, stamp: str | None = None, **params):
        from gt_policy_edit import edit_policy

        return edit_policy(self.fx.saved_profile(), op, field, "민준", confirm, expected_stamp=stamp, **params)

    def test_expressions_that_are_too_deep_or_long_are_refused(self) -> None:
        from gt_policy_edit import PolicyEditRejected

        for when in ("(" * 300 + "GLOSS" + ")" * 300, "!" * 5000 + "GLOSS", "GLOSS\n& !WIDE", "GLOSS & " * 60 + "WIDE"):
            with self.subTest(when[:20]), self.assertRaises(PolicyEditRejected):
                self.call("derive-add", when=when, value="LOW")

    def test_reason_is_one_clean_line(self) -> None:
        from gt_policy_edit import policy_stamp

        from gt_policy_edit import PolicyEditRejected

        with self.assertRaises(PolicyEditRejected) as caught:  # 백틱은 받지 않는다
            self.call("derive-retire", confirm=True, stamp=policy_stamp(self.definitions), id="V1", reason="정리 `X`")
        self.assertIn("백틱", str(caught.exception))
        self.edit("observe", "add", "--field", "sheen", "--id", "LAMP", "--name", "조명", "--desc", "조명이다.",
                  "--when", "LAMP & GLOSS", "--value", "NONE", "--before", "V2", "--yes")
        self.call("derive-add", confirm=True, stamp=policy_stamp(self.definitions), when="LAMP & WIDE", value="HIGH", before="V3")
        self.call("derive-retire", confirm=True, stamp=policy_stamp(self.definitions), id="V4", reason="줄\n  - 근거: 주입된 줄")
        text = self.definitions.read_text(encoding="utf-8")
        self.assertNotIn("\n  - 근거: 주입된 줄", text)
        self.assertIn("줄 - 근거: 주입된 줄", text, "이유는 한 줄로 접힌다")

    def test_an_observation_id_cannot_be_a_value_code(self) -> None:
        from gt_policy_edit import PolicyEditRejected

        with self.assertRaises(PolicyEditRejected) as caught:
            self.call("observe-add", id="HIGH", name="강한 것", desc="사진에 보인다.", when="HIGH & GLOSS", value="HIGH")
        self.assertIn("허용값 코드와 같습니다", str(caught.exception))
        with self.assertRaises(PolicyEditRejected) as caught:
            self.call("observe-add", id="MIRROR", name="광", desc="거울 같다.", when="MIRROR & GLOSS", value="HIGH", before="V2")
        self.assertIn("이름이 겹칩니다", str(caught.exception), "미리 보기가 로더 검사를 다 지난다")

    def test_look_alike_characters_do_not_slip_through(self) -> None:
        from gt_policy_edit import PolicyEditRejected

        with self.assertRaises(PolicyEditRejected):  # 전각·소프트 하이픈으로 쓴 값 코드도 값 코드다
            self.call("observe-add", id="MIRROR", name="거울", desc="ＨＩ\u00adＧＨ 광택이다.", when="MIRROR & GLOSS", value="HIGH", before="V2")
        with self.assertRaises(PolicyEditRejected) as caught:  # 보이지 않는 글자를 끼운 이름은 같은 이름이다
            self.call("value-add", field="color", code="U6", name="빨\u00ad강", desc="d")
        self.assertIn("이름", str(caught.exception))

    def test_a_shadow_is_told_in_words(self) -> None:
        from gt_policy_edit import PolicyEditRejected

        with self.assertRaises(PolicyEditRejected) as caught:
            self.call("derive-add", when="GLOSS", value="LOW")
        message = str(caught.exception)
        self.assertIn("V2(«광» · «넓은 반사» → 강함)", message)
        self.assertNotIn("아니오,", message, "관찰 조합 전체를 늘어놓지 않는다")

    def test_two_confirms_on_the_same_screen_let_only_one_in(self) -> None:
        import threading

        from gt_policy_edit import PolicyEditRejected, policy_stamp

        seen = policy_stamp(self.definitions)
        results: list[str] = []

        def go(when: str, value: str) -> None:
            try:
                self.call("derive-add", confirm=True, stamp=seen, when=when, value=value, before="end")
                results.append("ok")
            except PolicyEditRejected as rejected:
                results.append(str(rejected))

        # 두 요청 모두 혼자서는 들어갈 수 있는 줄이다 — 먼저 잠금을 쥔 쪽만 들어가고 다른 쪽은 «그사이 바뀌었습니다».
        threads = [threading.Thread(target=go, args=("GLOSS & !WIDE", "HIGH")), threading.Thread(target=go, args=("GLOSS & !WIDE", "NONE"))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results.count("ok"), 1, results)
        self.assertTrue(any("그사이 정책이 바뀌었습니다" in r for r in results))

    def test_preview_runs_every_check_and_names_duplicates(self) -> None:
        from gt_policy_edit import PolicyEditRejected

        plan = self.call("derive-retire", id="V2", reason="시험")  # V2를 빼면 WIDE가 놀게 된다 — 함께 뺀다
        self.assertEqual(plan["observationsRemoved"], ["WIDE"])
        with self.assertRaises(PolicyEditRejected) as caught:  # 미리 보기가 로더 검사를 다 지난다 — 겹치는 값 이름
            self.call("value-add", field="color", code="AA", name="빨강", desc="d")
        self.assertIn("이름", str(caught.exception))
        with self.assertRaises(PolicyEditRejected) as caught:
            self.call("derive-add", when="!GLOSS & WIDE", value="NONE", before="end")
        self.assertIn("이미 V1이(가) 같은 값", str(caught.exception))
        self.edit("observe", "add", "--field", "sheen", "--id", "LAMP", "--name", "조명", "--desc", "조명이다.",
                  "--when", "LAMP & GLOSS", "--value", "HIGH", "--before", "V2", "--yes")
        plan = self.call("derive-retire", id="V1", reason="시험")
        self.assertTrue(any("«없음»을(를) 내는 줄이 없습니다" in w for w in plan["warnings"]), plan.get("warnings"))

    def test_a_shared_observation_is_edited_everywhere(self) -> None:
        finish = ("### 관찰\n\n- `GLOSS` 광 — 표면에 빛 반사가 보인다.\n\n### 값 규칙\n\n- `V1` `GLOSS` → `GLOSSY`\n"
                  "  - 출처: 시험\n- 그 밖 → `MATTE`\n\n## sheen")
        self.with_text(self.base.replace("## sheen", finish, 1))
        plan = json.loads(self.edit("observe", "edit", "--field", "sheen", "--id", "GLOSS", "--name", "광", "--desc", "표면이 빛을 되돌린다.", "--yes").stdout)
        self.assertEqual(plan["sharedWith"], ["마감"])
        self.assertEqual(self.definitions.read_text(encoding="utf-8").count("- `GLOSS` 광 — 표면이 빛을 되돌린다."), 2)

    def test_values_with_continuation_lines_and_crlf_files(self) -> None:
        text = self.base.replace("- `RED` 빨강 — 설명", "- `RED` 빨강 — 설명\n  붉은 계열 전부.")
        self.definitions.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        self.edit("value", "edit", "--field", "color", "--code", "RED", "--desc", "붉은 계열이다.", "--yes")
        raw = self.definitions.read_bytes().decode("utf-8")
        self.assertIn("- `RED` 빨강 — 붉은 계열이다.\r\n- `BLUE`", raw, "이어짐 줄까지 한 값으로 고치고 줄 끝 모양(CRLF)을 지킨다")
        self.assertNotIn("붉은 계열 전부", raw)
        self.assertEqual(raw.count("\n"), raw.count("\r\n"))

    def test_a_refused_insert_leaves_no_undo_point_and_hand_edits_are_protected(self) -> None:
        from gt_policy_edit import PolicyEditRejected, policy_stamp

        self.edit("value", "add", "--field", "color", "--code", "AA", "--name", "에이", "--desc", "d", "--yes")
        with self.assertRaises(PolicyEditRejected):
            self.call("value-add", field="color", confirm=True, stamp=policy_stamp(self.definitions), code="BB", name="에이", desc="d")
        self.fx.task("policy", "undo", "--reviewer", "민준", "--yes")
        self.assertNotIn("`AA`", self.definitions.read_text(encoding="utf-8").split("## 변경 이력")[0], "거절된 시도는 되돌림 자리를 남기지 않는다")
        self.edit("value", "add", "--field", "color", "--code", "CC", "--name", "씨", "--desc", "d", "--yes")
        with self.definitions.open("a", encoding="utf-8") as handle:
            handle.write("\n손으로 적은 메모 줄\n")
        refused = self.fx.task("policy", "undo", "--reviewer", "민준", "--yes", check=False)
        self.assertIn("도구 밖에서 바뀌었습니다", refused.stderr)
        self.assertIn("손으로 적은 메모 줄", self.definitions.read_text(encoding="utf-8"))

    def test_undo_steps_back_one_change_at_a_time(self) -> None:
        start = self.definitions.read_text(encoding="utf-8")
        self.edit("value", "add", "--field", "color", "--code", "ORANGE", "--name", "주황", "--desc", "주황이다.", "--yes")
        after_first = self.definitions.read_text(encoding="utf-8")
        self.edit("value", "add", "--field", "color", "--code", "PINK", "--name", "분홍", "--desc", "분홍이다.", "--yes")
        preview = json.loads(self.fx.task("policy", "undo", "--reviewer", "민준").stdout)
        self.assertIn("PINK", preview["undoing"])
        self.fx.task("policy", "undo", "--reviewer", "민준", "--yes")
        text = self.definitions.read_text(encoding="utf-8")
        self.assertNotIn("`PINK`", text.split("## 변경 이력")[0])
        self.assertIn("되돌림 — «", text)
        self.fx.task("policy", "undo", "--reviewer", "민준", "--yes")
        self.assertEqual(self.definitions.read_text(encoding="utf-8").split("## 변경 이력")[0].strip(), start.split("## 변경 이력")[0].strip(),
                         "한 번 더 되돌리면 그 앞 변경으로 간다")
        self.assertTrue(after_first)

    def test_the_review_screen_follows_a_new_value(self) -> None:
        self.fx.task("prepare")
        self.edit("value", "add", "--field", "color", "--code", "ORANGE", "--name", "주황", "--desc", "주황이다.", "--yes")
        self.assertIn("주황", (self.fx.review_dir() / "review.html").read_text(encoding="utf-8"), "검수 화면의 «다른 값…»이 새 값을 안다")


class ServerEditTest(EditFixture):
    def setUp(self) -> None:
        super().setUp()
        self.fx.task("pages")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = subprocess.Popen([sys.executable, str(SCRIPTS / "serve_reports.py"), "--port", str(self.port)],
                                       env=self.fx.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    def tearDown(self) -> None:
        self.server.terminate()
        self.server.wait(timeout=5)
        super().tearDown()

    def post(self, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("POST", "/gt-policy", json.dumps(body), {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))

    def body(self, **extra) -> dict:
        return {"task": "fixture-color-sheen", "field": "sheen", "op": "derive-add", "reviewer": "민준",
                "params": {"when": "GLOSS & !WIDE", "value": "HIGH", "before": "end"}, **extra}

    def test_preview_then_apply_redraws_the_policy_page(self) -> None:
        page = (self.fx.review_dir() / "policy.html").read_text(encoding="utf-8")
        self.assertIn("이 칸 고치기", page)
        self.assertIn('id="policy-edit-data"', page)
        before = self.definitions.read_text(encoding="utf-8")
        status, answer = self.post(self.body(confirm=False))
        self.assertEqual((status, answer["ok"], answer["plan"]["applied"]), (200, True, False))
        self.assertEqual(answer["plan"]["changes"]["changed"], 1)
        self.assertEqual(self.definitions.read_text(encoding="utf-8"), before)
        from gt_policy_edit import policy_stamp

        status, answer = self.post(self.body(confirm=True, expectedStamp=policy_stamp(self.definitions)))
        self.assertEqual((status, answer["plan"]["applied"]), (200, True))
        self.assertIn("- `V3` `GLOSS & !WIDE` → `HIGH`", self.definitions.read_text(encoding="utf-8"))
        self.assertIn("«광» · «넓은 반사» 아님", (self.fx.review_dir() / "policy.html").read_text(encoding="utf-8"),
                      "넣은 줄이 정책 화면에 사람 말로 보인다")

    def test_refusals_come_back_as_sentences(self) -> None:
        cases = [({"reviewer": ""}, "이름"), ({"op": "rule-add"}, "가운데 하나"),
                 ({"confirm": True, "expectedStamp": "0000"}, "그사이 정책이 바뀌었습니다"),
                 ({"confirm": True}, "화면이 본 정책")]
        for change, expected in cases:
            with self.subTest(expected):
                status, answer = self.post(self.body(**change))
                self.assertEqual(status, 400)
                self.assertIn(expected, answer["error"])
        status, answer = self.post(self.body(confirm=True), {"Origin": "http://localhost:3000"})
        self.assertEqual(status, 400)
        self.assertIn("다른 출처", answer["error"])


if __name__ == "__main__":
    unittest.main()
