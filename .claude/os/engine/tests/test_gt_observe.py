"""관찰 칸 — 정의 문서의 `### 관찰`·`### 값 규칙`. 판독자는 값을 모른 채 사실에만 답하고, 값은 표가 정한다.

확인하는 것:
- 로더가 표의 모양을 허용값처럼 엄격하게 읽는다(모르는 항목·«그 밖» 없음·가려진 줄·값 코드가 판독자 글에 샘·쓰이지 않는 관찰).
- 판독자에게는 관찰만 가고 값 이름·값 규칙은 가지 않는다.
- 워크플로우(JS)가 값을 파이썬(gt_derive)과 똑같이 계산한다 — COUNT까지. 흔들리는 항목만 다시 보고, 갈리면 확신을 낮춘다.
- 값 규칙을 명령으로 더하고·고치고·뺀다. 가려지는 줄이 생기면 넣지 않는다. 문답의 답도 값 규칙 한 줄이 된다.
- 화면이 관찰을 칩으로 보인다.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_gt_review import PROJECT_ROOT, SCRIPTS, WORKFLOW, Fixture, _path, reading

sys.path.insert(0, str(SCRIPTS))

OBSERVED = """### 관찰

- `GLOSS` 광 — 표면에 빛 반사가 보인다.
- `WIDE` 넓은 반사 — 반사가 표면 절반 넘게 퍼져 있다.

### 값 규칙

- `V1` `!GLOSS` → `NONE`
  - 출처: 시험 정의
- `V2` `GLOSS & WIDE` → `HIGH`
  - 출처: 시험 정의
- 그 밖 → `LOW`
"""


class ObservedFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))
        self.definitions = self.fx.root / "definitions.md"
        text = self.definitions.read_text(encoding="utf-8")
        self.base = text.replace("## tones", OBSERVED + "\n## tones", 1)
        self.definitions.write_text(self.base, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def with_text(self, text: str) -> None:
        self.definitions.write_text(text, encoding="utf-8")


class LoaderTest(ObservedFixture):
    def test_the_table_loads(self) -> None:
        from gt_task import definition_policy, load_task

        task = load_task(self.fx.saved_profile())
        policy = definition_policy(self.definitions, {f["id"]: f["labels"] for f in task["fields"]},
                                   {f["id"]: f.get("cardinality") == "many" for f in task["fields"]})
        table = policy["observed"]["sheen"]
        self.assertEqual(table["observe"], ["GLOSS", "WIDE"])
        self.assertEqual([(r["id"], r["value"]) for r in table["rules"]], [("V1", "NONE"), ("V2", "HIGH")])
        self.assertEqual(table["else"], "LOW")

    def test_bad_shapes_stop_the_loader(self) -> None:
        cases = {
            "없는 항목": self.base.replace("`GLOSS & WIDE`", "`GLOSS & SHINY`"),
            "«- 그 밖": self.base.replace("- 그 밖 → `LOW`\n", ""),
            "가려": self.base.replace("- 그 밖 → `LOW`", "- `V3` `!GLOSS & WIDE` → `HIGH`\n  - 출처: 시험\n- 그 밖 → `LOW`"),
            "값 코드가 있습니다": self.base.replace("## sheen\n광택.", "## sheen\n광택. NONE이면 없다."),
            "함께 있어야": self.base.replace(self.base[self.base.index("### 값 규칙"):self.base.index("## tones")], ""),
            "쓰지 않습니다": self.base.replace("- `WIDE` 넓은", "- `ODD` 이상함 — 쓰이지 않는다.\n- `WIDE` 넓은"),
            "값은 `### 값 규칙`만": self.base.replace("### 관찰", "### 규칙\n\n- `R1` 반사면 없다 → `NONE`\n  - 조건: 반사면\n  - 출처: 시험\n\n### 관찰"),
            "허용값이 아닌": self.base.replace("→ `HIGH`\n  - 출처: 시험 정의", "→ `SHINY`\n  - 출처: 시험 정의"),
            "«출처» 줄이 없습니다": self.base.replace("- `V1` `!GLOSS` → `NONE`\n  - 출처: 시험 정의", "- `V1` `!GLOSS` → `NONE`"),
            "값 규칙 V2: 식에": self.base.replace("`GLOSS & WIDE`", "`GLOSS && WIDE`"),
            "관찰 ID가 겹칩니다": self.base.replace("- `WIDE` 넓은", "- `GLOSS` 광 둘 — 또.\n- `WIDE` 넓은"),
            "값 규칙 ID가 겹칩니다": self.base.replace("- `V2` `GLOSS & WIDE`", "- `V1` `GLOSS & WIDE`"),
            "마지막 줄이어야": self.base.replace("- `V2` `GLOSS & WIDE` → `HIGH`\n  - 출처: 시험 정의\n- 그 밖 → `LOW`",
                                               "- 그 밖 → `LOW`\n- `V2` `GLOSS & WIDE` → `HIGH`\n  - 출처: 시험 정의"),
            "COUNT가 세는 관찰이 없습니다": self.base.replace("`GLOSS & WIDE`", "`COUNT(ZZ_*) >= 1 & WIDE`"),
            "최대 14": self.base.replace("- `GLOSS` 광 — 표면에 빛 반사가 보인다.\n- `WIDE` 넓은 반사 — 반사가 표면 절반 넘게 퍼져 있다.",
                                       "\n".join(f"- `X_{n}` 표지 {n} — 보인다." for n in range(15)))
                                 .replace("`!GLOSS`", "`!X_0`").replace("`GLOSS & WIDE`", "`COUNT(X_*) >= 2`"),
            "비어 있습니다": self.base.replace("- `GLOSS` 광 — 표면에 빛 반사가 보인다.\n- `WIDE` 넓은 반사 — 반사가 표면 절반 넘게 퍼져 있다.\n", ""),
            "`### 규칙`에 값 코드": self.base.replace("### 관찰", "### 규칙\n\n- `R1` 조명만 비치면 NONE으로 본다\n  - 출처: 시험\n\n### 관찰"),
            "서문(첫 `##` 앞)": "# 시험 정책\n\nNONE은 드물다.\n\n" + self.base,
            "앞줄 V1이 먼저 걸립니다": self.base.replace("`GLOSS & WIDE`", "`!GLOSS & WIDE`"),
            "ID는 영문 대문자로 시작": self.base.replace("- `WIDE` 넓은", "- `1WIDE` 넓은"),
        }
        for expected, text in cases.items():
            with self.subTest(expected):
                self.with_text(text)
                done = self.fx.task("status", check=False)
                self.assertEqual(done.returncode, 2, done.stdout)
                self.assertIn(expected, done.stderr)


class SharedAndOutsideTest(ObservedFixture):
    FINISH = "### 관찰\n\n- `GLOSS` 광 — {desc}\n\n### 값 규칙\n\n- `V1` `GLOSS` → `GLOSSY`\n  - 출처: 시험\n- 그 밖 → `MATTE`\n\n## sheen"

    def test_a_shared_observation_must_mean_the_same(self) -> None:
        self.with_text(self.base.replace("## sheen", self.FINISH.format(desc="표면이 번들거린다."), 1))
        done = self.fx.task("status", check=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("뜻이 다릅니다", done.stderr)
        self.with_text(self.base.replace("## sheen", self.FINISH.format(desc="표면에 빛 반사가 보인다."), 1))
        self.fx.task("status")

    def test_a_section_outside_fields_that_names_observed_values_does_not_reach_the_reader(self) -> None:
        self.with_text(self.base + "\n## 필드끼리의 제약\n\n- 무광이면 광택은 NONE이나 LOW다.\n")
        prepared = self.fx.task("prepare")
        args = json.loads(prepared.stdout[prepared.stdout.index("{"):])["workflowArgs"]
        view = json.loads(_path(args["items"][0]["view"]).read_text(encoding="utf-8"))
        policy = _path(view["policy"]).read_text(encoding="utf-8")
        self.assertNotIn("필드끼리의 제약", policy)
        self.assertNotIn("NONE", policy)


class BlindingTest(ObservedFixture):
    def test_the_reader_gets_observations_but_not_values(self) -> None:
        prepared = self.fx.task("prepare")
        args = json.loads(prepared.stdout[prepared.stdout.index("{"):])["workflowArgs"]
        view = json.loads(_path(args["items"][0]["view"]).read_text(encoding="utf-8"))
        sheen = next(f for f in view["fields"] if f["id"] == "sheen")
        self.assertEqual([o["id"] for o in sheen["observe"]], ["GLOSS", "WIDE"])
        self.assertNotIn("labels", sheen, "관찰 칸의 판독자는 허용값을 모른다")
        policy = _path(view["policy"]).read_text(encoding="utf-8")
        section = policy[policy.index("## sheen"):policy.index("## tones")]
        self.assertIn("관찰 칸이다", section)
        self.assertIn("`GLOSS` 광", section)
        for hidden in ("NONE", "HIGH", "LOW", "### 허용값", "### 값 규칙", "V1"):
            self.assertNotIn(hidden, section)
        self.assertIn("### 허용값", policy[policy.index("## color"):policy.index("## finish")], "다른 칸은 그대로다")
        derive = args["common"]["derive"]["sheen"]
        self.assertEqual([r["id"] for r in derive["rules"]], ["V1", "V2"])
        self.assertEqual(args["common"]["observeNames"]["sheen"], {"GLOSS": "광", "WIDE": "넓은 반사"})


def run_read_stage(items: list[dict], common: dict, answers: dict, second: dict | None = None, extra: dict | None = None) -> dict:
    """워크플로우를 node에서 돌린다 — 판독 단계만(extra로 반론 단계도). 판독자·둘째 관찰자·반론자는 가짜다."""
    body = WORKFLOW.read_text(encoding="utf-8").replace("export const meta", "const meta", 1)
    args = {"task": "x", "batchId": "b", "worklist": "w.json", "stage": "read", "items": items, "common": common, **(extra or {})}
    harness = (
        f"const args = {json.dumps(args)};\n"
        f"const ANSWERS = {json.dumps(answers)};\n"
        f"const SECOND = {json.dumps(second or {})};\n"
        "const calls = [];\n"
        "const log = () => {};\n"
        "async function agent(prompt, opts) {\n"
        "  const item = args.items.find((i) => prompt.includes(i.view));\n"
        "  calls.push([opts.label, item.id]);\n"
        "  if (opts.label.startsWith('다시 보기')) return { observations: SECOND[item.id] || [] };\n"
        "  return ANSWERS[item.id];\n"
        "}\n"
        "async function pipeline(items, ...stages) {\n"
        "  const out = [];\n"
        "  for (let i = 0; i < items.length; i++) { let r = items[i];\n"
        "    for (const stage of stages) { r = await stage(r, items[i], i); } out.push(r); }\n"
        "  return out;\n"
        "}\n"
        "(async () => {\n" + body + "\n})().then((r) => console.log(JSON.stringify({result: r, calls})));\n"
    )
    with tempfile.TemporaryDirectory() as folder:
        script = Path(folder) / "run.mjs"
        script.write_text(harness, encoding="utf-8")
        done = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


class WorkflowDeriveTest(unittest.TestCase):
    """JS가 파이썬과 같은 값을 낸다 — COUNT까지. 표는 파이썬 파서가 만든 트리를 그대로 쓴다."""

    def table(self) -> dict:
        from gt_derive import parse

        return {"observe": ["A", "B_1", "B_2"], "else": "NONE",
                "rules": [{"id": "V1", "expr": "COUNT(B_*) >= 2", "value": "HIGH"},
                          {"id": "V2", "expr": "A & !B_1", "value": "LOW"}],
                "_parse": parse}

    def test_every_combination_matches_python(self) -> None:
        from gt_derive import derive, parse

        table = self.table()
        for rule in table["rules"]:
            rule["ast"] = parse(rule["expr"])
        combos = list(itertools.product((False, True), repeat=3))
        items, answers = [], {}
        for n, bits in enumerate(combos):
            gid = f"GI-{n:02d}"
            items.append({"id": gid, "view": f"view-{gid}.json", "fields": ["s"], "quietFields": []})
            answers[gid] = {"readings": [{"field": "s", "value": "", "confidence": "HIGH", "evidenceImageIds": [], "observation": "",
                                          "rulesApplied": [], "casesApplied": [],
                                          "observations": [{"id": k, "v": v, "sure": True, "why": "보임"} for k, v in zip(table["observe"], bits)]}]}
        common = {"derive": {"s": {"observe": table["observe"], "else": table["else"],
                                   "rules": [{"id": r["id"], "ast": r["ast"], "value": r["value"]} for r in table["rules"]]}},
                  "observeNames": {"s": {"A": "가", "B_1": "나1", "B_2": "나2"}}, "fieldNames": {}, "labels": {}, "many": {}}
        out = run_read_stage(items, common, answers)
        got = {item["id"]: item["reading"]["readings"][0] for item in out["result"]["items"]}
        for n, bits in enumerate(combos):
            expected, rule_id = derive({**table}, dict(zip(table["observe"], bits)))
            reading_ = got[f"GI-{n:02d}"]
            self.assertEqual(reading_["value"], expected, bits)
            self.assertEqual(reading_["rulesApplied"], [rule_id] if rule_id else [], bits)
            self.assertEqual(reading_["confidence"], "HIGH")
        self.assertFalse([c for c in out["calls"] if c[0].startswith("다시 보기")], "모두 확실하면 다시 보지 않는다")

    def test_only_unsure_pivotal_items_are_looked_at_again(self) -> None:
        from gt_derive import parse

        table = self.table()
        for rule in table["rules"]:
            rule["ast"] = parse(rule["expr"])
        common = {"derive": {"s": {"observe": table["observe"], "else": table["else"],
                                   "rules": [{"id": r["id"], "ast": r["ast"], "value": r["value"]} for r in table["rules"]]}},
                  "observeNames": {"s": {}}, "fieldNames": {}, "labels": {}, "many": {}}

        def item(gid: str, obs: list[tuple[str, bool, bool]]) -> tuple[dict, dict]:
            return ({"id": gid, "view": f"view-{gid}.json", "fields": ["s"], "quietFields": []},
                    {"readings": [{"field": "s", "value": "", "confidence": "HIGH", "evidenceImageIds": [], "observation": "",
                                   "rulesApplied": [], "casesApplied": [],
                                   "observations": [{"id": k, "v": v, "sure": sure, "why": "x"} for k, v, sure in obs]}]})
        # GI-01: A 참(애매)·B 거짓 → LOW. A를 뒤집으면 NONE이 되므로 다시 본다. 둘째가 «거짓» → 갈림.
        # GI-02: 같은 모양, 둘째가 «참» → 확실해져 HIGH 확신.
        # GI-03: B_2가 애매하지만 뒤집어도 값이 안 바뀐다(A 거짓·B_1 거짓 → NONE, B_2만 참이어도 COUNT 1) → 다시 보지 않는다.
        cells = [item("GI-01", [("A", True, False), ("B_1", False, True), ("B_2", False, True)]),
                 item("GI-02", [("A", True, False), ("B_1", False, True), ("B_2", False, True)]),
                 item("GI-03", [("A", False, True), ("B_1", False, True), ("B_2", True, False)])]
        second = {"GI-01": [{"field": "s", "id": "A", "v": False, "sure": True, "why": "안 보임"}],
                  "GI-02": [{"field": "s", "id": "A", "v": True, "sure": True, "why": "보임"}]}
        out = run_read_stage([c[0] for c in cells], common, {c[0]["id"]: c[1] for c in cells}, second)
        got = {i["id"]: i["reading"]["readings"][0] for i in out["result"]["items"]}
        self.assertEqual((got["GI-01"]["value"], got["GI-01"]["confidence"], got["GI-01"]["split"]), ("LOW", "LOW", ["A"]))
        self.assertEqual((got["GI-02"]["value"], got["GI-02"]["confidence"], got["GI-02"]["split"]), ("LOW", "HIGH", []))
        self.assertEqual(got["GI-03"]["split"], [])
        again = sorted(c[1] for c in out["calls"] if c[0].startswith("다시 보기"))
        self.assertEqual(again, ["GI-01", "GI-02"], "값을 뒤집지 않는 애매한 항목은 다시 보지 않는다")


    def common(self, fields: dict[str, dict]) -> dict:
        from gt_derive import parse

        return {"derive": {f: {"observe": t["observe"], "else": t["else"],
                               "rules": [{"id": r["id"], "ast": parse(r["expr"]), "value": r["value"]} for r in t["rules"]]}
                           for f, t in fields.items()},
                "observeNames": {f: {} for f in fields}, "fieldNames": {}, "labels": {}, "many": {}}

    @staticmethod
    def cell(field: str, obs: list[tuple[str, bool, bool]]) -> dict:
        return {"field": field, "value": "", "confidence": "HIGH", "evidenceImageIds": [], "observation": "", "rulesApplied": [],
                "casesApplied": [], "observations": [{"id": k, "v": v, "sure": sure, "why": "x"} for k, v, sure in obs]}

    def test_items_that_only_matter_together_are_looked_at_again(self) -> None:
        # B_1·B_2가 둘 다 애매하고 거짓 — 하나씩 뒤집으면 COUNT가 1이라 값이 안 바뀌지만, 둘 다 참이면 HIGH다.
        common = self.common({"s": self.table()})
        items = [{"id": "GI-01", "view": "view-GI-01.json", "fields": ["s"], "quietFields": []}]
        answers = {"GI-01": {"readings": [self.cell("s", [("A", False, True), ("B_1", False, False), ("B_2", False, False)])]}}
        out = run_read_stage(items, common, answers, {"GI-01": []})
        self.assertEqual([c[0] for c in out["calls"] if c[0].startswith("다시 보기")], ["다시 보기 GI-01"])
        self.assertEqual(out["result"]["items"][0]["reading"]["readings"][0]["confidence"], "LOW")

    def test_a_shared_observation_that_disagrees_splits_both_fields(self) -> None:
        table = {"observe": ["A"], "else": "NONE", "rules": [{"id": "V1", "expr": "A", "value": "HIGH"}]}
        common = self.common({"s": table, "t": table})
        items = [{"id": "GI-01", "view": "view-GI-01.json", "fields": ["s", "t"], "quietFields": []}]
        answers = {"GI-01": {"readings": [self.cell("s", [("A", True, True)]), self.cell("t", [("A", False, True)])]}}
        got = {r["field"]: r for r in run_read_stage(items, common, answers)["result"]["items"][0]["reading"]["readings"]}
        for field in ("s", "t"):
            self.assertEqual((got[field]["split"], got[field]["confidence"]), (["A"], "LOW"), field)

    def test_no_answers_means_no_value_and_a_second_look_can_fill_a_gap(self) -> None:
        common = self.common({"s": self.table()})
        items = [{"id": "GI-01", "view": "view-GI-01.json", "fields": ["s"], "quietFields": []},
                 {"id": "GI-02", "view": "view-GI-02.json", "fields": ["s"], "quietFields": []}]
        answers = {"GI-01": {"readings": []},
                   "GI-02": {"readings": [self.cell("s", [("B_1", False, True), ("B_2", False, True)])]}}
        second = {"GI-01": [], "GI-02": [{"field": "s", "id": "A", "v": True, "sure": True, "why": "보임"}]}
        got = {i["id"]: i["reading"]["readings"][0] for i in run_read_stage(items, common, answers, second)["result"]["items"]}
        self.assertEqual((got["GI-01"]["value"], got["GI-01"]["confidence"]), ("", "LOW"), "답이 없으면 값을 만들지 않는다")
        self.assertEqual((got["GI-02"]["value"], got["GI-02"]["split"], got["GI-02"]["confidence"]), ("LOW", [], "HIGH"),
                         "첫 눈이 빠뜨린 항목은 다시 본 눈의 답을 쓴다")

    def test_a_defender_question_on_an_observed_field_gets_options_too(self) -> None:
        """반론자가 관찰 칸에서 물어도 판독자의 관찰 답 위에서 선택지를 채운다 — 사람의 답이 다음 판독자의 사례가 되게."""
        common = self.common({"s": self.table()})
        items = [{"id": "GI-01", "view": "view-GI-01.json", "fields": ["s"], "quietFields": []}]
        reading_ = {"readings": [{**self.cell("s", [("A", True, True), ("B_1", False, True), ("B_2", False, True)]), "value": "LOW"}]}
        ask = {"question": "이런 경우를 그 항목이 참인 것으로 보는가?", "here": "P01", "imageIds": ["P01"], "options": [], "observe": "A"}
        rebuttal = {"rebuttals": [{"field": "s", "verdict": "CANT_TELL", "why": "경계", "askHuman": ask}]}
        out = run_read_stage(items, common, {"GI-01": rebuttal}, None,
                             {"stage": "defend", "readings": {"GI-01": reading_}, "gt": {"GI-01": {"s": "NONE"}}})
        got = out["result"]["items"][0]["defense"]["rebuttals"][0]["askHuman"]["options"]
        self.assertEqual(got, [{"answer": "예", "value": "LOW"}, {"answer": "아니오", "value": "NONE"}])

    def test_question_on_an_observed_field_gets_its_options_from_the_table(self) -> None:
        """판독자는 값을 몰라 «답마다 될 값»을 못 적는다 — 물음이 가르는 항목을 뒤집어 표가 채운다. 값이 안 바뀌는 물음은 내린다."""
        from gt_derive import parse

        table = self.table()
        common = {"derive": {"s": {"observe": table["observe"], "else": table["else"],
                                   "rules": [{"id": r["id"], "ast": parse(r["expr"]), "value": r["value"]} for r in table["rules"]]}},
                  "observeNames": {"s": {}}, "fieldNames": {}, "labels": {}, "many": {}}

        def item(gid: str, ask: dict) -> tuple[dict, dict]:
            obs = [("A", True), ("B_1", False), ("B_2", False)]
            return ({"id": gid, "view": f"view-{gid}.json", "fields": ["s"], "quietFields": []},
                    {"readings": [{"field": "s", "value": "", "confidence": "HIGH", "evidenceImageIds": [], "observation": "",
                                   "rulesApplied": [], "casesApplied": [], "askHuman": ask,
                                   "observations": [{"id": k, "v": v, "sure": True, "why": "x"} for k, v in obs]}]})
        ask = {"question": "이런 경우를 그 항목이 참인 것으로 보는가?", "here": "P01", "imageIds": ["P01"], "options": []}
        cells = [item("GI-01", {**ask, "observe": "A"}),      # A를 뒤집으면 LOW ↔ NONE — 물을 까닭이 있다
                 item("GI-02", {**ask, "observe": "B_2"}),    # B_2 하나로는 COUNT가 2가 안 된다 — 값이 같아 물음을 내린다
                 item("GI-03", dict(ask))]                    # 어느 항목인지 모른다 — 답을 값으로 옮길 수 없다
        out = run_read_stage([c[0] for c in cells], common, {c[0]["id"]: c[1] for c in cells})
        got = {i["id"]: i["reading"]["readings"][0] for i in out["result"]["items"]}
        self.assertEqual(got["GI-01"]["askHuman"]["options"], [{"answer": "예", "value": "LOW"}, {"answer": "아니오", "value": "NONE"}])
        self.assertEqual(got["GI-01"]["confidence"], "LOW")
        for gid in ("GI-02", "GI-03"):
            self.assertNotIn("askHuman", got[gid], gid)
            self.assertEqual(got[gid]["confidence"], "HIGH", gid)


class CommandTest(ObservedFixture):
    def rule(self, *args: str, check: bool = True):
        return self.fx.task("rule", *args, "--reviewer", "민준", check=check)

    def test_value_rules_are_added_edited_and_retired(self) -> None:
        refused = self.rule("add", "--field", "sheen", "--text", "x", "--value", "HIGH", "--yes", check=False)
        self.assertIn("--when", refused.stderr, "관찰 칸의 값은 식으로만 넣는다")
        preview = json.loads(self.rule("add", "--field", "sheen", "--when", "GLOSS & !WIDE", "--value", "HIGH").stdout)
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["table"][0], "V3 GLOSS & !WIDE → HIGH", "새 줄은 표 맨 위에 들어간다")
        self.rule("add", "--field", "sheen", "--when", "GLOSS & !WIDE", "--value", "HIGH", "--yes")
        text = self.definitions.read_text(encoding="utf-8")
        self.assertLess(text.index("- `V3` `GLOSS & !WIDE` → `HIGH`"), text.index("- `V1` `!GLOSS`"))
        # 가려지는 줄이 생기면 넣지 않는다 — «GLOSS»만으로 LOW면 V3·V2가 영영 안 쓰인다.
        shadow = self.rule("add", "--field", "sheen", "--when", "GLOSS", "--value", "LOW", "--yes", check=False)
        self.assertIn("어떤 사진에서도 쓰이지 않", shadow.stderr)
        # 고치기 — 같은 자리에 새 ID, 옛 줄은 보관.
        self.rule("edit", "--field", "sheen", "--id", "V2", "--value", "LOW", "--yes")
        text = self.definitions.read_text(encoding="utf-8")
        self.assertIn("- `V4` `GLOSS & WIDE` → `LOW`", text)
        self.assertIn("- `sheen/V2` `GLOSS & WIDE` → `HIGH`", text[text.index("## 보관"):])
        # 빼기 — 이유가 있어야 한다.
        self.assertIn("이유", self.rule("retire", "--field", "sheen", "--id", "V3", "--yes", check=False).stderr)
        self.rule("retire", "--field", "sheen", "--id", "V3", "--reason", "경계를 다시 정함", "--yes")
        self.assertIn("대체: 없음(폐지)", self.definitions.read_text(encoding="utf-8"))
        # 값 없는 안내 규칙은 그대로 들어간다 — 관찰의 뜻을 다듬는다.
        self.rule("add", "--field", "sheen", "--text", "조명 반사만 보이면 광으로 보지 않는다", "--yes")
        self.fx.task("status")

    def test_value_rule_commands_refuse_what_they_cannot_do(self) -> None:
        self.assertIn("지금 규칙: V1, V2", self.rule("edit", "--field", "sheen", "--id", "V9", "--value", "LOW", "--yes", check=False).stderr)
        self.assertIn("관찰에 없는 항목", self.rule("add", "--field", "sheen", "--when", "SHINY", "--value", "LOW", "--yes", check=False).stderr)
        self.assertIn("COUNT가 셀 관찰이 없습니다",
                      self.rule("add", "--field", "sheen", "--when", "COUNT(ZZ_*) >= 1", "--value", "LOW", "--yes", check=False).stderr)
        before = self.definitions.read_text(encoding="utf-8")
        # V2를 빼면 WIDE를 쓰는 줄이 없다 — 그 관찰도 이 칸에서 함께 빠진다고 미리 보기가 말한다(쓰이지 않는 관찰을 남기지 않는다).
        idle = json.loads(self.rule("retire", "--field", "sheen", "--id", "V2", "--reason", "시험").stdout)
        self.assertEqual(idle["observationsRemoved"], ["WIDE"])
        self.assertTrue(any("함께 빠집니다" in w for w in idle["warnings"]))
        self.assertEqual(self.definitions.read_text(encoding="utf-8"), before)
        # 값 코드를 말하는 안내 규칙도 되돌린다 — 판독자에게 가는 글이다.
        leak = self.rule("add", "--field", "sheen", "--text", "조명만 비치면 NONE으로 본다", "--yes", check=False)
        self.assertIn("값 코드", leak.stderr)
        self.assertEqual(self.definitions.read_text(encoding="utf-8"), before)

    def test_only_rule_left_cannot_be_retired(self) -> None:
        self.with_text(self.base.replace("- `V2` `GLOSS & WIDE` → `HIGH`\n  - 출처: 시험 정의\n", "")
                       .replace("- `WIDE` 넓은 반사 — 반사가 표면 절반 넘게 퍼져 있다.\n", ""))
        refused = self.rule("retire", "--field", "sheen", "--id", "V1", "--reason", "시험", "--yes", check=False)
        self.assertIn("마지막 값 규칙", refused.stderr)

    def test_an_answer_on_an_observed_field_becomes_a_value_rule(self) -> None:
        self.fx.task("prepare")
        entry = json.loads(self.fx.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "NONE",
                                          "--reviewer", "민준", "--ask", "조명 반사만 보이면 광택이 있는 것인가?").stdout)
        unread = json.loads(self.fx.task("qa").stdout)["answered"][0]
        self.assertFalse(unread["reachesReader"], "값으로만 답한 물음은 관찰 칸 판독자에게 못 간다")
        self.assertIn("옮겨 주세요", unread["todo"])
        needs = self.fx.task("qa", "--add", entry["decisionId"], "--reviewer", "민준", "--yes", check=False)
        self.assertIn("--when", needs.stderr)
        self.fx.task("qa", "--add", entry["decisionId"], "--when", "GLOSS & !WIDE", "--reviewer", "민준", "--yes")
        text = self.definitions.read_text(encoding="utf-8")
        self.assertIn("- `V3` `GLOSS & !WIDE` → `NONE`\n  - 물음: 조명 반사만 보이면 광택이 있는 것인가?", text)
        self.assertIn("  - 출처: 검수 문답 · ", text)
        listed = json.loads(self.fx.task("qa").stdout)["answered"]
        self.assertTrue(listed[0]["inPolicy"], "답한 사례가 값 규칙에 이어진다")
        # 고쳐도 물음·근거·처음 출처(판정 ID)가 이어진다 — 사례와 규칙의 연결이 끊기지 않는다.
        self.rule("edit", "--field", "sheen", "--id", "V3", "--value", "LOW", "--yes")
        text = self.definitions.read_text(encoding="utf-8")
        head = text[text.index("- `V4` `GLOSS & !WIDE` → `LOW`"):]
        self.assertIn("  - 물음: 조명 반사만 보이면 광택이 있는 것인가?", head[:400])
        self.assertIn(entry["decisionId"], head[:600])
        self.assertTrue(json.loads(self.fx.task("qa").stdout)["answered"][0]["inPolicy"])
        # 값 규칙을 가진 칸도 다음 준비가 돈다. 값으로만 답한 사례(어느 관찰인지 모르는)는 관찰 칸 판독자에게 가지 않는다.
        prepared = self.fx.task("prepare")
        args = json.loads(prepared.stdout[prepared.stdout.index("{"):])["workflowArgs"]
        view = json.loads(_path(args["items"][0]["view"]).read_text(encoding="utf-8"))
        for case in (view.get("cases") and json.loads(_path(view["cases"]).read_text(encoding="utf-8"))["cases"]) or []:
            self.assertNotEqual(case["field"], "sheen")

    def test_an_answer_about_one_observation_reaches_the_next_reader_as_yes_or_no(self) -> None:
        self.fx.task("prepare")
        ask = {"question": "조명 반사만 보이면 광으로 보는가?", "here": "P01에 조명 반사", "imageIds": ["P01"], "observe": "GLOSS",
               "options": [{"answer": "예", "value": "LOW"}, {"answer": "아니오", "value": "NONE"}]}
        out = self.fx.sweep({"A": [{**reading("sheen", "LOW", "LOW"), "askHuman": ask,
                                    "observations": [{"id": "GLOSS", "v": True, "sure": False, "why": "조명"},
                                                     {"id": "WIDE", "v": False, "sure": True, "why": "좁음"}]}]})
        self.fx.task("finish", "--from", str(out))
        entry = json.loads(self.fx.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "NONE",
                                          "--reviewer", "민준").stdout)
        listed = json.loads(self.fx.task("qa").stdout)["answered"][0]
        self.assertTrue(listed["reachesReader"], "어느 관찰의 예/아니오인지 아는 답은 사례로 간다")
        # 안내 규칙으로도 옮길 수 있다 — «이런 경우를 그 관찰의 예/아니오로 본다»(값을 말하지 않는다).
        self.fx.task("qa", "--add", entry["decisionId"], "--rule", "조명 반사만 보이면 광으로 보지 않는다", "--reviewer", "민준", "--yes")
        text = self.definitions.read_text(encoding="utf-8")
        self.assertIn("- `R1` 조명 반사만 보이면 광으로 보지 않는다\n  - 물음: 조명 반사만 보이면 광으로 보는가?", text)
        prepared = self.fx.task("prepare")
        args = json.loads(prepared.stdout[prepared.stdout.index("{"):])["workflowArgs"]
        view = json.loads(_path(args["items"][0]["view"]).read_text(encoding="utf-8"))
        index = json.loads(_path(view["cases"]).read_text(encoding="utf-8"))["cases"]
        case = json.loads(_path(next(c for c in index if c["field"] == "sheen")["file"]).read_text(encoding="utf-8"))
        self.assertEqual((case["answer"], case["answerName"]), ("GLOSS=아니오", "관찰 «광»: 아니오"))
        self.assertNotIn("NONE", json.dumps(case, ensure_ascii=False))
        policy = _path(view["policy"]).read_text(encoding="utf-8")
        self.assertIn("`R1` 조명 반사만 보이면 광으로 보지 않는다", policy)


class ScreenTest(ObservedFixture):
    def test_the_screen_shows_what_the_ai_saw(self) -> None:
        self.fx.task("prepare")
        obs = [{"id": "GLOSS", "v": True, "sure": True, "why": "반사가 보임"},
               {"id": "WIDE", "v": False, "sure": False, "why": "경계", "second": {"v": True, "why": "넓어 보임"}}]
        out = self.fx.sweep({"A": [{**reading("sheen", "LOW", "LOW"), "observations": obs, "split": ["WIDE"], "rulesApplied": []}]})
        self.fx.task("finish", "--from", str(out))
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertIn('<div class="obs" aria-label="AI가 본 것">', page)
        self.assertIn('<span class="yes" title="반사가 보임">광</span>', page)
        self.assertIn("다시 본 눈과 갈린 항목: 넓은 반사", page)
        policy = (self.fx.review_dir() / "policy.html").read_text(encoding="utf-8")
        self.assertIn("AI가 답하는 관찰", policy)
        self.assertIn("«광» 아님 → <b>없음</b>", policy)
        self.assertIn("«광» · «넓은 반사» → <b>강함</b>", policy)


class DescribeTest(unittest.TestCase):
    def test_expressions_read_as_words(self) -> None:
        from gt_derive import describe, parse

        names = {"A": "가", "B_1": "나1", "B_2": "나2"}
        self.assertEqual(describe(parse("A & !B_1"), names), "«가» · «나1» 아님")
        self.assertEqual(describe(parse("!(A | B_1)"), names), "(«가» 또는 «나1») 아님")
        self.assertEqual(describe(parse("COUNT(B_*) >= 2"), names), "«나1·나2» 가운데 2개 이상")


class RealTasksTest(unittest.TestCase):
    """이 저장소의 모든 과제 정책 — 관찰 칸의 값 코드가 판독자 글에 없다. 새 과제가 절을 더해도 이 그물에 걸린다."""

    def test_real_tasks_leak_no_observed_codes(self) -> None:
        import re

        from gt_task import definition_policy, field_map, load_task, reader_policy, resolve

        packs = sorted((PROJECT_ROOT / ".claude/os/attributes").glob("*/profile.json"))
        for path in packs:
            profile = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(profile.get("gtTask"), dict):
                continue
            with self.subTest(profile["id"]):
                task = load_task(profile)
                fields = field_map(task)
                labels = {f: [str(c) for c in spec["labels"]] for f, spec in fields.items()}
                many = {f: spec.get("cardinality") == "many" for f, spec in fields.items()}
                definitions = resolve(profile, {"path": task["definitions"], "root": task.get("definitionsRoot") or "project"})
                observed = definition_policy(definitions, labels, many)["observed"]
                text, _ = reader_policy(definitions, labels, many, {})
                shared = {c for f in labels if f not in observed for c in labels[f]}
                for field_id in observed:
                    for code in set(labels[field_id]) - shared:
                        self.assertIsNone(re.search(rf"(?<![A-Za-z0-9_]){re.escape(code)}(?![A-Za-z0-9_])", text), code)


if __name__ == "__main__":
    unittest.main()
