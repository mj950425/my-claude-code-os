#!/usr/bin/env python3
"""GT 개선 하네스의 계약을 지킨다.

여기서 쓰는 과제는 가짜 속성 «상품 색상·광택»이다. 실제 과제가 아닌 것으로 돌려야 하네스가 과제를
모른 채 도는지 보인다.

지키는 것은 다섯이다.
1. **고르기** — 신호가 선언대로 걸리고, 사람이 답한 칸은 다시 나오지 않되 답한 뒤 GT가 바뀌면 다시 나온다.
2. **눈가림** — 판독자가 받는 파일에 GT·실행 값·키가 없고, 사진 이름으로도 키를 알 수 없다.
3. **배치** — 옛 판독 결과는 새 목록에 붙지 않는다.
4. **상태** — 판독과 반론이 만나는 표가 규칙으로 정해진다.
5. **원장** — 사람의 판정만, 사람이 본 값에만, 동시에 눌러도 잃지 않고, 덮어쓰지 않는다.
"""

from __future__ import annotations

import http.client
import json
import shutil
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude").is_dir():
            return parent
    raise RuntimeError("프로젝트 루트를 찾지 못했습니다.")


PROJECT_ROOT = _find_project_root()
SCRIPTS = PROJECT_ROOT / ".claude/os/engine/scripts"
CLI = SCRIPTS / "gt_review.py"
WORKFLOW = PROJECT_ROOT / ".claude/os/engine/workflows/gt-review.js"
sys.path.insert(0, str(SCRIPTS))

from gt_review_render import cell_status, merge  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


GT_ROWS = [
    # 제약 위반: 광택 없음(MATTE)인데 광택 강도가 HIGH
    {"sku": "A", "color": "RED", "finish": "MATTE", "sheen": "HIGH", "tones": ["WARM"], "src": "USER_OK"},
    # 실행과 다름(옛 값 CRIMSON → RED로 옮긴 뒤에도 BLUE와 다름)
    {"sku": "B", "color": "BLUE", "finish": "GLOSSY", "sheen": "HIGH", "tones": ["COOL"], "src": "HAND_GUESS"},
    # 빈칸
    {"sku": "C", "color": None, "finish": "GLOSSY", "sheen": "HIGH", "tones": ["WARM"], "src": "USER_OK"},
    # 참고 등급인데 실행이 같은 값 → 올리지 않는다
    {"sku": "D", "color": "GREEN", "finish": "GLOSSY", "sheen": "HIGH", "tones": ["WARM"], "src": "HAND_GUESS"},
    # 참고 등급이고 실행 값 없음 → 올린다
    {"sku": "E", "color": "GREEN", "finish": "GLOSSY", "sheen": "HIGH", "tones": ["WARM"], "src": "HAND_GUESS"},
    # 허용값 밖의 GT(옛 값·오타)
    {"sku": "F", "color": "PURPEL", "finish": "GLOSSY", "sheen": "HIGH", "tones": ["WARM", "COOL"], "src": "USER_OK"},
]


class Fixture:
    """가짜 과제 하나. 필드 넷(색 · 마감 · 광택 강도 · 톤[여럿])과 제약 하나, 실행 결과, 사진 색인."""

    def __init__(self, root: Path) -> None:
        from PIL import Image, ImageDraw

        self.root = root
        data = root / "data"
        write_jsonl(data / "gt.jsonl", GT_ROWS)
        images = data / "img"
        images.mkdir(parents=True)
        Image.new("RGB", (300, 300), (200, 30, 30)).save(images / "a.jpg")
        # 긴 상세 원본 — 명목 경계(600) 근처 여백(700~760)에서 잘려야 한다
        long_image = Image.new("RGB", (400, 1600), (40, 40, 40))
        draw = ImageDraw.Draw(long_image)
        draw.rectangle([150, 0, 250, 1599], fill=(230, 230, 230))
        draw.rectangle([0, 700, 399, 759], fill="white")
        long_image.save(images / "long.png")
        write_jsonl(data / "images.jsonl", [
            {"sku": sku, "category": "잡화>테스트", "notice": f"{sku} 고시문", "pics": [
                {"f": "img/a.jpg", "pid": f"{sku}-1", "kind": "THUMB"},
                {"f": "img/long.png", "pid": f"{sku}-D", "kind": "DETAIL_SOURCE"},
                {"f": "img/nope.jpg", "pid": f"{sku}-X", "kind": "THUMB"},
            ]}
            for sku in "ABCDEF"
        ])
        # 필드 절의 본문. 허용값(`### 허용값`)은 save()가 프로필 사본의 labels·labelNames로 적는다 — 정본은 정책이지만,
        # 테스트는 값을 파이썬 딕셔너리로 바꾸는 편이 읽기 쉽다. 파일에 쓰는 순간 값은 정책으로만 간다.
        self.bodies = {"color": "색.", "finish": "마감.", "sheen": "광택.", "tones": "톤."}
        self.profile_path = root / "attributes" / "fixture" / "profile.json"
        self.profile = {
            "schemaVersion": "catalog-data-profile-v1",
            "id": "fixture-color-sheen",
            "displayName": "가짜 색상·광택",
            "attributeName": "색상·광택",
            "subjectName": "가짜 상품",
            "outputRoot": str(root / "run"),
            "gtTask": {
                "schemaVersion": "gt-task-v1",
                "unit": "상품",
                "keyField": "sku",
                "definitions": str(root / "definitions.md"),
                "correctionSourcePrefix": "USER_GT_REVIEW",
                "gt": {"path": str(data / "gt.jsonl"), "sourceField": "src", "fieldSourcesField": "fieldSources"},
                "authority": {"trusted": ["^USER_"], "reference": ["^HAND_"]},
                "fields": [
                    {"id": "color", "name": "색", "labels": ["RED", "BLUE", "GREEN", "PURPLE"],
                     "labelNames": {"RED": "빨강", "BLUE": "파랑", "GREEN": "초록", "PURPLE": "보라"},
                     "legacy": {"CRIMSON": "RED"}, "fillMissing": True},
                    {"id": "finish", "name": "마감", "labels": ["MATTE", "GLOSSY"], "labelNames": {"MATTE": "무광", "GLOSSY": "유광"}},
                    {"id": "sheen", "name": "광택 강도", "labels": ["NONE", "LOW", "HIGH"],
                     "labelNames": {"NONE": "없음", "LOW": "약함", "HIGH": "강함"}},
                    {"id": "tones", "name": "톤", "labels": ["WARM", "COOL"], "cardinality": "many",
                     "labelNames": {"WARM": "따뜻", "COOL": "차가움"}},
                ],
                "constraints": [
                    {"id": "MATTE_HAS_NO_SHEEN", "text": "무광이면 광택이 없어야 한다",
                     "when": {"finish": "MATTE"}, "require": {"sheen": ["NONE", "LOW"]}},
                ],
                "images": {
                    "path": str(data / "images.jsonl"), "keyField": "sku", "listField": "pics",
                    "fileField": "f", "fileBase": str(data), "idField": "pid", "roleField": "kind",
                    "tileRoles": ["DETAIL_SOURCE"], "maxImages": 16, "contextFields": ["category"],
                },
                "evidence": {"textFields": ["notice"]},
                "limit": 10,
            },
        }
        self.save()
        # 원장도 프로필 찾기도 임시 폴더로. 가짜 과제를 진짜 속성 층이나 진짜 원장 자리에 쓰지 않는다.
        self.env = {**os.environ, "CATALOG_OS_GT_ROOT": str(root / "gt"),
                    "CATALOG_OS_ATTRIBUTES_ROOT": str(root / "attributes")}

    def save(self, definitions: bool = True) -> None:
        """프로필을 쓰고, 필드의 허용값·이름표를 정책(정의 문서)으로 옮겨 적는다. 프로필 파일에는 목록이 남지 않는다."""
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        written = json.loads(json.dumps(self.profile))
        sections = []
        for field in written["gtTask"]["fields"]:
            labels = field.pop("labels", None)
            names = field.pop("labelNames", None) or {}
            if labels is None:
                continue
            lines = "\n".join(f"- `{label}` {names.get(str(label), '')}".rstrip() + " — 설명" for label in labels)
            body = self.bodies.get(field["id"], f"{field.get('name') or field['id']}.")
            sections.append(f"## {field['id']}\n{body}\n\n### 허용값\n\n{lines}\n")
        self.profile_path.write_text(json.dumps(written, ensure_ascii=False), encoding="utf-8")
        if definitions:
            (self.root / "definitions.md").write_text("\n".join(sections), encoding="utf-8")

    def cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        completed = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                                   cwd=PROJECT_ROOT, env=self.env)
        if check and completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return completed

    def task(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        # 테스트는 판독(워크플로우)을 돌리지 않고 prepare를 거듭 부른다 — 실제로는 «판독이 도는 중»으로 보여 거절되는 상태다.
        # 그래서 여기서만 --force로 그 보호를 넘는다. 보호 자체는 prepare_refuses_to_wipe_a_batch_still_being_read가 cli로 직접 본다.
        extra = ("--force",) if args[0] == "prepare" and "--force" not in args else ()
        return self.cli(args[0], "--task", str(self.profile_path), *args[1:], *extra, check=check)

    def record(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """사람이 말로 답한 판정. 사람이 본 GT 값(--expect)은 지금 GT 파일에서 읽어 붙인다 — CLI가 그것을 요구한다."""
        items = list(args)
        if "--expect" not in items and "--expect-empty" not in items:
            key, field = items[items.index("--key") + 1], items[items.index("--field") + 1]
            sys.path.insert(0, str(SCRIPTS))
            from gt_task import field_map, gt_value, load_gt, load_task

            task = load_task(self.saved_profile())
            value = gt_value(task, field_map(task)[field], load_gt(self.profile, task)[key])
            items += ["--expect-empty"] if value is None else ["--expect", value]
        return self.task("record", *items, check=check)

    def saved_profile(self) -> dict:
        """디스크에 쓴 프로필(허용값이 정책으로 옮겨진 모양) — 코드가 읽는 것과 같은 것."""
        return {**json.loads(self.profile_path.read_text(encoding="utf-8")), "_path": str(self.profile_path)}

    def review_dir(self) -> Path:
        return self.root / "run" / "gt-review"

    def worklist(self) -> dict:
        return json.loads((self.review_dir() / "worklist.json").read_text(encoding="utf-8"))

    def ledger(self) -> dict:
        return json.loads((self.root / "gt/fixture-color-sheen/gt-review/decisions.json").read_text(encoding="utf-8"))

    def only_first_field(self) -> None:
        """필드 하나짜리 과제로 줄인다. 제약·실행 열도 그 필드만 남긴다 — 선언 안 된 필드를 가리키면 로더가 멈춘다."""
        task = self.profile["gtTask"]
        task["fields"] = task["fields"][:1]
        task["constraints"] = []

    def sweep(self, readings: dict[str, list[dict]], defenses: dict[str, list[dict]] | None = None) -> Path:
        """워크플로우 반환값을 흉내 낸다. 건 id는 지금 작업 목록에서 키로 찾는다."""
        worklist = self.worklist()
        by_key = {item["key"]: item["id"] for item in worklist["items"]}
        items = [{"id": by_key[key], "reading": {"readings": rows},
                  "defense": {"rebuttals": (defenses or {}).get(key, [])} if (defenses or {}).get(key) else None}
                 for key, rows in readings.items()]
        value = {"schemaVersion": "gt-review-sweep-v2", "task": "fixture-color-sheen",
                 "batchId": worklist["batchId"], "items": items}
        path = self.root / f"out-{len(list(self.root.glob('out-*.json')))}.json"
        path.write_text(json.dumps({"summary": "x", "result": value}, ensure_ascii=False), encoding="utf-8")
        return path


def reading(field: str, value: str, confidence: str = "HIGH") -> dict:
    return {"field": field, "value": value, "confidence": confidence, "evidenceImageIds": ["P01"], "observation": "보인다"}


class GtReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))
        self.fx.task("prepare")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cells(self) -> dict[tuple[str, str], dict]:
        return {(item["key"], cell["field"]): cell for item in self.fx.worklist()["items"] for cell in item["cells"]}

    # ── 고르기 ──
    def test_signals_fire_as_declared(self) -> None:
        cells = self.cells()
        self.assertIn("GT_SELF_CONTRADICTION", cells[("A", "sheen")]["signals"])
        self.assertIn("GT_SELF_CONTRADICTION", cells[("A", "finish")]["signals"], "제약의 조건 칸도 함께 올라와야 한다")
        self.assertEqual(cells[("B", "color")]["signals"], ["GT_REFERENCE_ONLY"], "참고 등급 라벨은 실행 값과 상관없이 한 번 본다")
        self.assertNotIn("run", cells[("B", "color")], "모델 실행 값은 하네스에 없다 — 검수자가 알 수 없는 값이다")
        self.assertEqual(cells[("C", "color")]["signals"], ["GT_MISSING"])
        self.assertEqual(cells[("E", "color")]["signals"], ["GT_REFERENCE_ONLY"])
        self.assertIn("GT_OUT_OF_RANGE", cells[("F", "color")]["signals"])

    def test_many_values_compare_as_sets(self) -> None:
        cells = self.cells()
        self.assertNotIn(("C", "tones"), cells, "참고 등급이 아니고 모순도 없는 칸은 후보가 아니다")
        self.assertNotIn(("F", "tones"), cells, "순서만 다른 집합은 같은 값이다")

    def test_quiet_cells_stay_quiet(self) -> None:
        cells = self.cells()
        self.assertEqual(cells[("D", "color")]["signals"], ["GT_REFERENCE_ONLY"], "참고 등급은 모델 실행과 상관없이 한 번 본다")
        self.assertNotIn(("A", "color"), cells, "사람이 확정한 칸은 모순·범위 밖이 아니면 올리지 않는다")
        self.assertNotIn(("C", "sheen"), cells, "fillMissing이 아닌 필드의 빈칸은 올리지 않는다")

    def test_contradictions_come_first_then_unconfirmed(self) -> None:
        keys = [item["key"] for item in self.fx.worklist()["items"]]
        self.assertIn(keys[0], ("A", "F"))
        self.assertLess(keys.index("C"), keys.index("B"), "빈칸(4순위)이 사람 확인 전 라벨(5순위)보다 앞이다")

    def test_duplicate_keys_stop(self) -> None:
        write_jsonl(self.fx.root / "data/gt.jsonl", GT_ROWS + [GT_ROWS[0]])
        self.assertIn("키가 겹칩니다", self.fx.task("prepare", check=False).stderr)

    def test_composite_keys_and_no_evidence(self) -> None:
        rows = [{"goods": "1", "img": str(n), "color": "RED", "finish": "GLOSSY", "sheen": "LOW", "tones": ["WARM"],
                 "src": "HAND_GUESS"} for n in (1, 2)]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.profile["gtTask"]["keyField"] = ["goods", "img"]
        self.fx.profile["gtTask"].pop("images")
        self.fx.profile["gtTask"].pop("evidence")
        self.fx.save()
        out = self.fx.task("prepare").stdout
        items = self.fx.worklist()["items"]
        self.assertEqual([item["key"] for item in items], ["1|1", "1|2"], "증거가 없어도 사람 앞에는 온다")
        self.assertTrue(all(item["noEvidence"] for item in items))
        args = json.loads(out[out.index("{"):])["workflowArgs"]
        self.assertEqual(args["items"], [], "증거가 없는 건은 판독자를 띄우지 않는다")
        self.fx.task("render")
        review = json.loads((self.fx.review_dir() / "review.json").read_text(encoding="utf-8"))
        self.assertEqual({cell["status"] for item in review["items"] for cell in item["cells"]}, {"NO_EVIDENCE"})

    # ── 설정 ──
    def test_gt_under_runs_is_refused(self) -> None:
        self.fx.profile["gtTask"]["gt"] = {"path": ".claude/os/runs/x/golden/gt.jsonl"}
        self.fx.save()
        self.assertIn("runs/", self.fx.task("prepare", check=False).stderr)

    def test_missing_definition_section_is_refused(self) -> None:
        (self.fx.root / "definitions.md").write_text("## color\n", encoding="utf-8")
        self.assertIn("finish", self.fx.task("prepare", check=False).stderr)

    def test_a_profile_with_the_cycle_ledger_cannot_also_declare_a_task(self) -> None:
        self.fx.profile["gt"] = {"path": "x.jsonl"}
        self.fx.save()
        self.assertIn("판정 원장은 하나", self.fx.task("prepare", check=False).stderr)

    # ── 증거 ──
    def item(self, key: str) -> dict:
        return next(item for item in self.fx.worklist()["items"] if item["key"] == key)

    def test_long_detail_is_cut_by_the_production_tile_rule(self) -> None:
        tiles = [image for image in self.item("A")["images"] if image["role"] == "DETAIL_SOURCE"]
        self.assertEqual([tile["imageId"] for tile in tiles], ["D01T01", "D01T02", "D01T03"],
                         "D는 상세 원본 안의 순번이다 — 썸네일까지 세면 D02가 된다")
        self.assertEqual(tiles[0]["crop"], {"top": 0, "bottom": 725})
        self.assertEqual(tiles[0]["tileRule"]["version"], "v2-band-then-seam")

    def test_pre_tiled_images_must_say_which_rule_cut_them(self) -> None:
        self.fx.profile["gtTask"]["images"]["preTiledRoles"] = ["THUMB"]
        self.fx.save()
        self.assertIn("preTiledRule", self.fx.task("prepare", check=False).stderr)
        self.fx.profile["gtTask"]["images"]["preTiledRule"] = {"version": "v0-fixed", "by": "누가"}
        self.fx.save()
        self.assertIn("디코더", self.fx.task("prepare", check=False).stderr, "디코더를 적지 않은 선언은 거절한다")
        self.fx.profile["gtTask"]["images"]["preTiledRule"] = {"version": "v0-fixed", "decoder": "harness-pillow", "by": "누가"}
        self.fx.save()
        self.fx.task("prepare")
        thumb = next(image for image in self.item("A")["images"] if image["role"] == "THUMB")
        self.assertEqual(thumb["tileRule"]["version"], "v0-fixed")

    def test_missing_pictures_are_reported_not_hidden(self) -> None:
        self.assertEqual([m["imageId"] for m in self.item("A")["missing"]], ["A-X"])

    def test_text_evidence_is_given(self) -> None:
        self.assertEqual(self.item("A")["text"], {"notice": "A 고시문"})

    # ── 눈가림 ──
    def views(self) -> list[Path]:
        return sorted((self.fx.review_dir() / "reader").glob("*.json"))

    def test_reader_view_carries_no_answer_and_no_key(self) -> None:
        self.assertTrue(self.views())
        for path in self.views():
            view = json.loads(path.read_text(encoding="utf-8"))
            body = path.read_text(encoding="utf-8")
            for leaked in ("current", "currentSource", "run", "signals", "authority", "key", "USER_OK", "HAND_GUESS", "CRIMSON"):
                self.assertNotIn(f'"{leaked}"', body, f"판독자 파일에 답의 흔적이 있다: {leaked}")
            for image in view["images"]:
                self.assertRegex(image["imageId"], r"^P\d{2}$")
                self.assertNotRegex(image["path"], r"/[A-F]/|[A-F]-\d", "사진 경로로 키를 알 수 없어야 한다")

    def test_context_is_given_but_never_an_answer_column(self) -> None:
        # 답 열을 맥락으로 선언하면 조용히 빼지 않고 멈춘다 — 쓴 사람이 모르면 다음 선언도 같다.
        for leak in ("sheen", "src", "colorAlternatives", "colorMirror"):
            self.fx.profile["gtTask"]["images"]["contextFields"] = ["category", leak]
            if leak == "colorAlternatives":
                self.fx.profile["gtTask"]["fields"][0]["alternativesField"] = leak
            if leak == "colorMirror":
                self.fx.only_first_field()
                self.fx.profile["gtTask"]["constraints"] = []
                self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트", "mirrorFields": [leak]}
            self.fx.save()
            self.assertIn(leak, self.fx.task("prepare", check=False).stderr, f"{leak}는 답을 담거나 가리킨다")
        self.fx.profile["gtTask"]["images"]["contextFields"] = ["category"]
        self.fx.save()
        self.fx.task("prepare")
        view = json.loads(self.views()[0].read_text(encoding="utf-8"))
        self.assertEqual(view["context"], {"category": "잡화>테스트"})

    def test_workflow_never_puts_gt_into_the_reader_prompt(self) -> None:
        body = WORKFLOW.read_text(encoding="utf-8")
        reader = body[body.index("function readerPrompt"): body.index("function defensePrompt")]
        self.assertNotIn("GT[", reader)
        self.assertNotIn("${WORKLIST}", reader, "판독자에게 작업 목록 경로를 주지 않는다")
        self.assertIn("${item.view}", reader)

    def test_workflow_does_not_stand_in_for_a_missing_agent(self) -> None:
        body = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("needsRestart", body)
        calls = re.findall(r"agent\((\w+)Prompt\([^)]*\), \{\s*agentType", body)
        self.assertEqual(sorted(calls), ["defense", "reader"], "두 호출 모두 agentType을 갖는다 — 유형 없는 대역은 쓰지 않는다")
        self.assertEqual(len(re.findall(r"\bagent\(", body)), 2)

    def test_workflow_knows_no_task(self) -> None:
        body = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual([word for word in ("색상", "광택", "color", "sheen") if word in body], [])

    # ── AI의 물음 → 정책 문답 ──
    def test_a_standard_ask_binds_to_its_cell_and_an_answer_can_become_policy_qa(self) -> None:
        question = "조명 반사만 보이면 광택이 있는 것인가?"
        ask = {"question": question, "here": "P01에서 조명 반사만 보인다", "imageIds": ["P01"],
               "options": [{"answer": "아니다", "value": "NONE"}, {"answer": "맞다", "value": "HIGH"},
                           {"answer": "모름", "value": "SHINY"}]}
        self.fx.task("finish", "--from", str(self.fx.sweep({"A": [{**reading("sheen", "LOW", "LOW"), "askHuman": ask}]})))
        review = json.loads((self.fx.review_dir() / "review.json").read_text(encoding="utf-8"))
        cell = next(c for item in review["items"] if item["key"] == "A" for c in item["cells"] if c["field"] == "sheen")
        # 허용값 밖의 선택지는 버린다 — 버튼으로 이을 수 없고, 정책에 옮기면 없는 값을 가르친다.
        self.assertEqual([o["value"] for o in cell["ask"]["options"]], ["NONE", "HIGH"])
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        block = page[page.index('data-field="sheen"', page.index('id="GI-')):]
        self.assertIn("AI가 묻는 것", block[:block.index('class="act')])
        self.assertIn("아니다 → <b>없음</b>", page)

        entry = json.loads(self.fx.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "NONE",
                                          "--reviewer", "민준", "--ask", question).stdout)
        self.assertEqual(entry["basedOn"]["ask"]["question"], question)
        listed = json.loads(self.fx.task("qa").stdout)["answered"]
        self.assertEqual([(r["decisionId"], r["answer"], r["inPolicy"]) for r in listed], [(entry["decisionId"], "NONE", False)])

        definitions = self.fx.root / "definitions.md"
        before = definitions.read_text(encoding="utf-8")
        # 규칙 문장이 없으면 넣지 않는다 — «물음 → 답»은 이 상품에 붙은 말이라, 사람이 확인한 한 문장이 정본이다.
        refused = self.fx.task("qa", "--add", entry["decisionId"], "--reviewer", "민준", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("--rule", refused.stderr)
        rule = "조명 반사만 보이고 표면 결이 없으면 광택이 없다"
        preview = json.loads(self.fx.task("qa", "--add", entry["decisionId"], "--reviewer", "민준", "--rule", rule).stdout)
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["rule"][0], f"- `R1` {rule} → `NONE`")
        self.assertEqual(definitions.read_text(encoding="utf-8"), before, "--yes 없이는 정책을 건드리지 않는다")

        self.fx.task("qa", "--add", entry["decisionId"], "--reviewer", "민준", "--rule", rule, "--yes")
        after = definitions.read_text(encoding="utf-8")
        sheen = after[after.index("## sheen"):after.index("## tones")]
        self.assertIn("### 규칙", sheen)
        self.assertIn(f"- `R1` {rule} → `NONE`\n  - 물음: {question}\n  - 출처: 검수 문답 · ", sheen)
        self.assertIn("  - 근거: A", sheen)
        self.assertIn("## 변경 이력", after)
        self.assertTrue(json.loads(self.fx.task("qa").stdout)["answered"][0]["inPolicy"])
        # 규칙 줄이 허용값으로 새지 않는다.
        values = json.loads(self.fx.task("values", "--field", "sheen").stdout)["fields"][0]["values"]
        self.assertEqual([v["code"] for v in values], ["NONE", "LOW", "HIGH"])

        # 대체 — 옛 규칙은 칸 절에서 빠져 보관으로 간다. 판독자가 읽는 칸 절에는 적용 중인 규칙만 남는다.
        self.fx.task("qa", "--add", entry["decisionId"], "--reviewer", "민준", "--rule", "반사만 보이면 광택이 없다",
                     "--replace", "R1", "--yes")
        after = definitions.read_text(encoding="utf-8")
        sheen = after[after.index("## sheen"):after.index("## tones")]
        self.assertNotIn("`R1`", sheen)
        self.assertIn("- `R2` 반사만 보이면 광택이 없다 → `NONE`", sheen)
        archive = after[after.index("## 보관"):]
        self.assertIn(f"- `sheen/R1` {rule} → `NONE`", archive)
        self.assertIn("  - 대체: R2 · ", archive)

        self.fx.task("render")
        policy = (self.fx.review_dir() / "policy.html").read_text(encoding="utf-8")
        self.assertIn("반사만 보이면 광택이 없다", policy)
        self.assertIn('href="golden.html#q=A"', policy)
        self.assertIn("<h2>보관</h2>", policy)

    def test_an_answered_question_reaches_the_next_readers_without_a_promotion_step(self) -> None:
        # 옛 배치 — 물음이 메모에만 있고 칸마다의 표준 물음이 없다. 화면이 그 칸에 붙여 보인 물음이 기록에 남아야,
        # 다음 배치(화면이 바뀐 뒤)에도 «이 판정은 그 물음의 답»이 이어진다.
        out = self.fx.sweep({"A": [{**reading("sheen", "LOW", "LOW"), "observation": "조명 반사가 표면에 보입니다"}]})
        raw = json.loads(out.read_text(encoding="utf-8"))
        raw["result"]["items"][0]["reading"]["note"] = "P01에서 조명 반사만 보이는 표면을 광택이 있다고 볼 만큼인가요?"
        out.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.fx.task("finish", "--from", str(out))
        asked = (self.fx.root / "gt/fixture-color-sheen/gt-review/asked.jsonl").read_text(encoding="utf-8")
        self.assertIn('"field": "sheen"', asked)
        # 말로 받은 판정 — 물음을 붙이지 않았어도 화면이 그 칸에 보인 물음의 답으로 이어진다.
        self.fx.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "NONE", "--reviewer", "민준")
        listed = json.loads(self.fx.task("qa").stdout)["answered"]
        self.assertEqual([(r["key"], r["field"], r["answer"]) for r in listed], [("A", "sheen", "NONE")])

        # 다음 배치 — 판독자 인자에 사람이 답한 경계가 실린다. 그 상품 자신(A)의 판독에는 빼라고 표시한다.
        prepared = self.fx.task("prepare")
        args = json.loads(prepared.stdout[prepared.stdout.index("{"):])["workflowArgs"]
        self.assertEqual([q["question"] for q in args["common"]["answered"]], [listed[0]["question"]])
        mine = [item for item in args["items"] if item.get("skipQa")]
        self.assertTrue(all(q == listed[0]["id"] for item in mine for q in item["skipQa"]))

    def test_every_page_script_parses(self) -> None:
        # 화면 스크립트는 파이썬 문자열 안의 자바스크립트라 따옴표 하나가 빠져도 파이썬 테스트는 통과하고 화면만 죽는다.
        # 그린 화면의 스크립트를 노드로 모두 읽어 본다(노드가 없으면 건너뛴다).
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("node가 없습니다")
        self.fx.task("finish", "--from", str(self.fx.sweep({"A": [reading("sheen", "LOW")], "B": [reading("color", "RED", "LOW")]})))
        for page in ("review.html", "policy.html", "golden.html"):
            text = (self.fx.review_dir() / page).read_text(encoding="utf-8")
            for n, body in enumerate(re.findall(r"<script(?![^>]*\b(?:src|type)=)[^>]*>(.*?)</script>", text, re.S)):
                script = self.fx.root / f"{page}-{n}.js"
                script.write_text(body, encoding="utf-8")
                checked = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
                self.assertEqual(checked.returncode, 0, f"{page}의 스크립트 {n}: {checked.stderr[:400]}")

    def test_a_malformed_policy_stops_the_loader(self) -> None:
        definitions = self.fx.root / "definitions.md"
        base = definitions.read_text(encoding="utf-8")
        cases = {
            "출처": "\n### 규칙\n\n- `R1` 반사면 없다 → `NONE`\n  - 근거: A\n",
            "허용값이 아닌": "\n### 규칙\n\n- `R1` 반사면 없다 → `SHINY`\n  - 출처: 직접 작성 · 2026-09-27 · 민준\n",
            "모르는 줄": "\n### 규칙\n\n- `R1` 반사면 없다\n  - 출처: 직접 작성\n  - 메모: 아무거나\n",
            "모양이 아닙니다": "\n### 규칙\n\n* R1 반사면 없다\n",
            "겹칩니다": ("\n### 규칙\n\n- `R1` 반사면 없다\n  - 출처: 직접 작성\n"
                      "\n## 보관\n\n- `sheen/R1` 옛 규칙\n  - 출처: 직접 작성\n  - 대체: R1 · 2026-09-27 · 민준\n"),
        }
        for expected, block in cases.items():
            with self.subTest(expected):
                insert = base.index("## tones")
                definitions.write_text(base[:insert] + block.lstrip("\n") + "\n" + base[insert:] if "## 보관" not in block
                                       else base[:insert] + block.split("\n## 보관")[0].lstrip("\n") + "\n" + base[insert:]
                                       + "\n## 보관" + block.split("\n## 보관")[1], encoding="utf-8")
                completed = self.fx.task("status", check=False)
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertIn(expected, completed.stderr)
        definitions.write_text(base + "\n## 목적\n\n### 무엇을 가르나\n\n색과 광택.\n", encoding="utf-8")
        completed = self.fx.task("status", check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("세 소제목", completed.stderr)

    def test_a_standard_ask_turns_off_scraping_the_note(self) -> None:
        # 표준 물음이 있는 판독은 메모에서 물음 문장을 골라내지 않는다 — 한 물음이 두 자리에 두 번 보이지 않게.
        ask = {"question": "반사만 보이면 광택인가?", "here": "", "imageIds": [], "options": []}
        out = self.fx.sweep({"A": [{**reading("sheen", "LOW", "LOW"), "askHuman": ask}]})
        raw = json.loads(out.read_text(encoding="utf-8"))
        raw["result"]["items"][0]["reading"]["note"] = "메모입니다. 이 색이 같은지 확인해 주세요?"
        out.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.fx.task("finish", "--from", str(out))
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertEqual(page.count("AI가 묻는 것</b>"), 1)

    # ── 배치 ──
    def test_a_result_from_an_earlier_batch_is_refused(self) -> None:
        old = self.fx.sweep({"A": [reading("sheen", "LOW")]})
        self.fx.task("prepare")  # 새 배치. GI-01은 다시 매겨진다
        completed = self.fx.task("finish", "--from", str(old), check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("먼저 준비된 배치", completed.stderr)

    def test_needs_restart_is_reported(self) -> None:
        worklist = self.fx.worklist()
        path = self.fx.root / "restart.json"
        path.write_text(json.dumps({"schemaVersion": "gt-review-sweep-v2", "task": "fixture-color-sheen",
                                    "batchId": worklist["batchId"], "needsRestart": {"agentType": "gt-blind-reader"},
                                    "items": []}), encoding="utf-8")
        self.assertIn("껐다 켠", self.fx.task("finish", "--from", str(path), check=False).stderr)

    # ── 상태 ──
    def test_status_table(self) -> None:
        labels = ["RED", "BLUE"]
        read = lambda value, confidence="HIGH": {"value": value, "confidence": confidence}  # noqa: E731
        self.assertEqual(cell_status("RED", read("RED"), None, labels), "GT_HOLDS")
        self.assertEqual(cell_status("RED", read("BLUE"), {"verdict": "READER_RIGHT"}, labels), "FIX_PROPOSED")
        self.assertEqual(cell_status("RED", read("BLUE"), {"verdict": "GT_STANDS"}, labels), "CONTESTED")
        self.assertEqual(cell_status("RED", read("BLUE"), {"verdict": "CANT_TELL"}, labels), "CONTESTED")
        self.assertEqual(cell_status(None, read("BLUE"), {"verdict": "READER_RIGHT"}, labels), "FILL_PROPOSED")
        self.assertEqual(cell_status("RED", read("BLUE", "LOW"), None, labels), "NEEDS_HUMAN_LOOK")
        self.assertEqual(cell_status("RED", read("PINK"), None, labels), "NEEDS_HUMAN_LOOK")
        self.assertEqual(cell_status("RED", None, None, labels), "NOT_READ")
        self.assertEqual(cell_status("BLUE|RED", read("RED|BLUE"), None, labels, many=True), "GT_HOLDS")

    def test_finish_renders_a_page_with_buttons(self) -> None:
        out = self.fx.sweep({"A": [reading("sheen", "LOW")]},
                            {"A": [{"field": "sheen", "verdict": "READER_RIGHT", "why": "반사 없음", "evidenceImageIds": ["P01"]}]})
        self.fx.task("finish", "--from", str(out))
        review = json.loads((self.fx.review_dir() / "review.json").read_text(encoding="utf-8"))
        status = {(c["key"], c["field"]): c["status"] for i in review["items"] for c in i["cells"]}
        self.assertEqual(status[("A", "sheen")], "FIX_PROPOSED")
        self.assertEqual(status[("B", "color")], "NOT_READ")
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        for needle in ("/gt-decide", "/gt-decided", 'data-decision="CORRECT" data-value="LOW"', "광택 강도",
                       "expectedBefore", "빈칸이 맞다"):
            self.assertIn(needle, page)
        self.assertEqual(json.loads((self.fx.review_dir() / "manifest.json").read_text())["page"], "review.html")

    def test_unknown_label_gets_its_own_button(self) -> None:
        # 선언한 과제에서만 도는 가지라, 픽스처가 선언하지 않던 동안 화면이 통째로 죽는 것을 아무도 못 봤다.
        self.fx.profile["gtTask"]["fields"][2]["labels"].append("CANT_SEE")
        self.fx.profile["gtTask"]["fields"][2]["unknownLabel"] = "CANT_SEE"
        self.fx.profile["gtTask"]["fields"][2]["labelNames"]["CANT_SEE"] = "못 봄"
        self.fx.save()
        out = self.fx.sweep({"A": [reading("sheen", "LOW")]})
        self.fx.task("finish", "--from", str(out))
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertIn('data-value="CANT_SEE"', page)

    def test_next_batch_moves_past_cells_the_blind_reader_already_agreed_with(self) -> None:
        # 모순·범위 밖이 아닌 건으로 본다 — 그런 칸은 판독이 «맞다»고 해도 뒤로 밀리지 않는다(아래 테스트).
        self.fx.profile["gtTask"]["limit"] = 1
        self.fx.profile["gtTask"]["constraints"] = []
        # F는 허용값 안으로, C는 채워서 — 맨 앞 건이 «사람 확인 전» 칸을 가진 건(B)이 되게.
        write_jsonl(self.fx.root / "data/gt.jsonl", [dict(row, color="PURPLE") if row["sku"] == "F" else
                                                     dict(row, color="RED") if row["sku"] == "C" else row for row in GT_ROWS])
        self.fx.save()
        self.fx.task("prepare")
        first = self.fx.worklist()["items"][0]
        current = {cell["field"]: cell["current"] for cell in first["cells"]}
        agree = [reading(field, value) for field, value in current.items() if value]
        self.fx.task("finish", "--from", str(self.fx.sweep({first["key"]: agree})))
        self.fx.task("prepare")
        self.assertNotEqual(self.fx.worklist()["items"][0]["key"], first["key"], "«다음 거»는 다른 건을 보여 줘야 한다")

    # ── 원장 ──
    def record(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.fx.record(*args, check=check)

    def test_an_agreed_contradiction_keeps_its_place(self) -> None:
        # 판독이 모순의 두 칸에 다 «맞다»고 한 것은 판독이 모순을 놓쳤다는 뜻이다. 1순위를 맨 뒤로 밀지 않는다.
        self.fx.task("prepare")
        first = self.fx.worklist()["items"][0]
        self.assertEqual(first["key"], "A")
        self.fx.task("finish", "--from", str(self.fx.sweep({"A": [reading("finish", "MATTE"), reading("sheen", "HIGH")]})))
        # 합의 규칙만 본다 — 화면을 지워 «답 없이 넘어간 칸»(shown.jsonl) 효과는 빼고.
        (self.fx.review_dir() / "review.json").unlink()
        self.fx.task("prepare")
        self.assertEqual(self.fx.worklist()["items"][0]["key"], "A")

    def test_a_decision_given_by_name_is_mapped_to_the_code(self) -> None:
        # 말로 받은 판정(«색은 빨강 맞아»)이 이름으로 와도 코드로 옮겨 비교한다 — 거짓 «원본이 바뀌었습니다»가 나가지 않게.
        self.fx.save()
        entry = json.loads(self.fx.task("record", "--key", "B", "--field", "색", "--decision", "CORRECT", "--value", "빨강",
                                        "--expect", "파랑", "--reviewer", "민준").stdout)
        self.assertEqual((entry["field"], entry["before"], entry["after"]), ("color", "BLUE", "RED"))

    def test_ledger_rejects_what_a_human_did_not_say(self) -> None:
        no_name = self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", " ", check=False)
        self.assertIn("이름", no_name.stderr)
        outside = self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "SHINY", "--reviewer", "민준", check=False)
        self.assertIn("허용값", outside.stderr)
        empty = self.record("--key", "C", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준", check=False)
        self.assertIn("비어", empty.stderr)
        typo = self.record("--key", "F", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준", check=False)
        self.assertIn("허용값 밖", typo.stderr)
        stale = self.record("--key", "A", "--field", "sheen", "--decision", "CONFIRM", "--reviewer", "민준", "--expect", "LOW", check=False)
        self.assertIn("화면이 보여 준 GT", stale.stderr)

    def test_answered_cells_are_not_asked_again_but_holds_are(self) -> None:
        self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", "민준")
        self.record("--key", "B", "--field", "color", "--decision", "HOLD", "--reviewer", "민준")
        self.record("--key", "C", "--field", "color", "--decision", "LEAVE_EMPTY", "--reviewer", "민준")
        self.fx.task("prepare")
        cells = self.cells()
        self.assertNotIn(("A", "sheen"), cells)
        self.assertNotIn(("C", "color"), cells, "빈칸 유지도 결정이다")
        self.assertIn(("B", "color"), cells, "보류는 답이 아니다 — 다시 나온다")

    def test_a_decision_on_a_gt_that_changed_since_comes_back(self) -> None:
        self.record("--key", "E", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        rows = [dict(row) for row in GT_ROWS]
        rows[4]["color"] = "BLUE"
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.task("prepare")
        self.assertIn("GT_CHANGED_SINCE_DECISION", self.cells()[("E", "color")]["signals"])
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["stale"][0]["nowInGt"], "BLUE")

    def test_concurrent_decisions_are_all_kept(self) -> None:
        script = (
            "import sys, os, threading\n"
            f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
            "from catalog_profile import load_profile\n"
            "from gt_decisions import record\n"
            f"profile = load_profile(__import__('pathlib').Path({str(self.fx.profile_path)!r}))\n"
            "errors = []\n"
            f"seen = {{row['sku']: row.get('finish') for row in map(__import__('json').loads, open({str(self.fx.root / 'data/gt.jsonl')!r}))}}\n"
            "def one(key):\n"
            "    try: record(profile, key, 'finish', 'CONFIRM', '민준', expected_before=seen[key], channel='spoken')\n"
            "    except Exception as error: errors.append(repr(error))\n"
            "threads = [threading.Thread(target=one, args=(key,)) for key in 'ABCDEF']\n"
            "[t.start() for t in threads]; [t.join() for t in threads]\n"
            "print(errors)\n"
        )
        # 스레드 여섯과 프로세스 둘이 같은 원장에 동시에 쓴다. 서버(스레드)와 터미널(프로세스)이 겹치는 자리다.
        runs = [subprocess.Popen([sys.executable, "-c", script], env=self.fx.env, stdout=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [run.communicate(timeout=60)[0].strip() for run in runs]
        self.assertEqual(outputs, ["[]", "[]"])
        ids = [entry["decisionId"] for entry in self.fx.ledger()["decisions"]]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)

    def test_export_matches_the_harness_correction_shape_and_supersedes(self) -> None:
        self.record("--key", "A", "--field", "sheen", "--decision", "HOLD", "--reviewer", "민준")
        self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", "민준", "--reason", "무광이다")
        self.record("--key", "C", "--field", "tones", "--decision", "CORRECT", "--value", "WARM|COOL", "--reviewer", "민준")
        ledger = self.fx.ledger()["decisions"]
        self.assertEqual(ledger[1]["supersedes"], ledger[0]["decisionId"])
        self.assertEqual(ledger[1]["basis"], "definitions#sheen")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["rows"], {"source": len(GT_ROWS), "corrected": len(GT_ROWS)})
        self.assertFalse(summary["appliedToSource"])
        folder = self.fx.root / "gt/fixture-color-sheen/gt-review"
        self.assertFalse((folder / "gt.corrected.jsonl").exists(), "고친 GT 사본은 파생물이라 정답 자리에 두지 않는다")
        copies = self.fx.review_dir() / "export"
        corrections = [json.loads(line) for line in (folder / "corrections.jsonl").read_text(encoding="utf-8").splitlines()]
        sheen = next(c for c in corrections if c["field"] == "sheen")
        self.assertTrue({"id", "field", "before", "after", "previousSource", "source", "reason"} <= set(sheen))
        self.assertEqual((sheen["before"], sheen["after"], sheen["previousSource"]), ("HIGH", "LOW", "USER_OK"))
        tones = next(c for c in corrections if c["field"] == "tones")
        self.assertEqual(tones["after"], ["COOL", "WARM"], "값 여럿은 목록으로 되돌린다")
        self.assertEqual(sheen["keyFields"], {"sku": "A"}, "외부 하네스가 자기 키 열로 찾을 수 있게")
        fixed = [json.loads(line) for line in (copies / "gt.corrected.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["sku"] for row in fixed], [row["sku"] for row in GT_ROWS], "원본 줄 순서 그대로")
        self.assertTrue(re.match(r"USER_GT_REVIEW_CORRECT_\d{8}$", fixed[0]["fieldSources"]["sheen"]))

    def test_apply_needs_yes_and_keeps_a_backup(self) -> None:
        self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", "민준")
        gt = self.fx.root / "data/gt.jsonl"
        before = gt.read_text(encoding="utf-8")
        dry = json.loads(self.fx.task("apply").stdout)
        self.assertFalse(dry["applied"])
        self.assertEqual(gt.read_text(encoding="utf-8"), before)
        self.assertIn("먼저", self.fx.task("apply", "--yes", check=False).stderr, "미리 보기는 보여 준 목록이 아니다")
        self.fx.task("export")  # «반영해줘» — 사람에게 보여 준 목록
        done = json.loads(self.fx.task("apply", "--yes").stdout)
        self.assertTrue(done["applied"])
        self.assertEqual(Path(done["backup"]).read_text(encoding="utf-8"), before)
        self.assertIn('"sheen": "LOW"', gt.read_text(encoding="utf-8"))
        # 반영된 정정은 «답한 뒤 바뀐 GT»가 아니다 — 다시 나오지 않는다
        self.fx.task("prepare")
        self.assertNotIn(("A", "sheen"), self.cells())


class LedgerSemanticsTest(unittest.TestCase):
    """원장의 뜻 — 비우기·모순·옛 이름·보류 순서·배치 정리·눈가림·다시 읽기·형. 하나하나가 검토에서 재현된 실패다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))
        self.fx.task("prepare")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def record(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.fx.record(*args, check=check)

    def cells(self) -> dict:
        return {(item["key"], cell["field"]): cell for item in self.fx.worklist()["items"] for cell in item["cells"]}

    def test_leave_empty_only_on_empty_and_clear_blanks_a_value(self) -> None:
        refused = self.record("--key", "B", "--field", "color", "--decision", "LEAVE_EMPTY", "--reviewer", "민준", check=False)
        self.assertIn("비워야 한다", refused.stderr)
        self.record("--key", "B", "--field", "color", "--decision", "CLEAR", "--reviewer", "민준")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["corrections"], 1)
        fixed = [json.loads(line) for line in (self.fx.review_dir() / "export/gt.corrected.jsonl").read_text().splitlines()]
        self.assertIsNone(next(row for row in fixed if row["sku"] == "B")["color"])

    def test_a_contradiction_cannot_be_confirmed_away(self) -> None:
        entry = json.loads(self.record("--key", "A", "--field", "sheen", "--decision", "CONFIRM", "--reviewer", "민준").stdout)
        self.assertTrue(entry["constraintWarnings"], "모순을 유지하면 그 자리에서 알린다")
        self.record("--key", "A", "--field", "finish", "--decision", "CONFIRM", "--reviewer", "민준")
        self.fx.task("prepare")
        self.assertIn("GT_SELF_CONTRADICTION", self.cells()[("A", "sheen")]["signals"], "둘 다 맞다는 답은 답이 아니다")
        fixed = self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", "민준")
        self.assertEqual(json.loads(fixed.stdout)["constraintWarnings"], [])
        self.fx.task("prepare")
        self.assertNotIn(("A", "sheen"), self.cells())
        self.assertEqual(json.loads(self.fx.task("export").stdout)["constraintViolationsAfter"], [])

    def test_confirming_a_legacy_raw_value_rewrites_it(self) -> None:
        rows = [dict(row) for row in GT_ROWS]
        rows[1]["color"] = "CRIMSON"
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        self.fx.task("export")
        fixed = [json.loads(line) for line in (self.fx.review_dir() / "export/gt.corrected.jsonl").read_text().splitlines()]
        self.assertEqual(next(row for row in fixed if row["sku"] == "B")["color"], "RED")

    def test_held_items_wait_behind_fresh_ones(self) -> None:
        first = self.fx.worklist()["items"][0]
        for cell in first["cells"]:
            self.record("--key", first["key"], "--field", cell["field"], "--decision", "HOLD", "--reviewer", "민준")
        self.fx.task("prepare")
        keys = [item["key"] for item in self.fx.worklist()["items"]]
        self.assertEqual(keys[-1], first["key"], "보류한 건은 새 후보 뒤로 간다")

    def test_prepare_keeps_only_what_it_should(self) -> None:
        stray = self.fx.review_dir() / "reader-view.json"
        stray.write_text('{"items": [{"key": "A"}]}', encoding="utf-8")
        (self.fx.review_dir() / "agreed.jsonl").write_text("", encoding="utf-8")
        self.fx.task("prepare")
        self.assertFalse(stray.exists(), "옛 판의 파일도 다음 준비가 지운다")
        self.assertTrue((self.fx.review_dir() / "agreed.jsonl").exists())

    def test_nothing_the_reader_can_reach_names_a_key_or_a_gt_value(self) -> None:
        reader = self.fx.review_dir() / "reader"
        body = "\n".join(path.read_text(encoding="utf-8") for path in reader.rglob("*.json"))
        names = "\n".join(str(path.relative_to(reader)) for path in reader.rglob("*"))
        for leaked in ('"sku"', "USER_OK", "HAND_GUESS", "PURPEL", "GI-0"):
            self.assertNotIn(leaked, body)
            self.assertNotIn(leaked, names)
        args = json.loads((self.fx.review_dir() / "workflow-args.json").read_text(encoding="utf-8"))
        self.assertNotIn("gt", args, "GT 값은 파일에 쓰지 않고 워크플로우 인자로만 나간다")

    def test_reread_keeps_the_screen_and_merges(self) -> None:
        worklist = self.fx.worklist()
        a = next(item for item in worklist["items"] if item["key"] == "A")
        b = next(item for item in worklist["items"] if item["key"] == "B")
        self.fx.task("finish", "--from", str(self.fx.sweep({"A": [reading("sheen", "LOW"), reading("finish", "MATTE")]})))
        self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", "민준")
        out = self.fx.task("prepare", "--reread").stdout
        args = json.loads(out[out.index("{"):])["workflowArgs"]
        self.assertEqual(self.fx.worklist()["batchId"], worklist["batchId"], "화면을 버리지 않는다")
        self.assertIn(b["id"], [item["id"] for item in args["items"]])
        self.assertNotIn(a["id"], [item["id"] for item in args["items"]])
        self.fx.task("finish", "--from", str(self.fx.sweep({"B": [reading("color", "BLUE")]})))
        review = json.loads((self.fx.review_dir() / "review.json").read_text(encoding="utf-8"))
        status = {(c["key"], c["field"]): c["status"] for i in review["items"] for c in i["cells"]}
        self.assertNotEqual(status[("A", "sheen")], "NOT_READ", "지난 판독이 남는다")
        self.assertEqual(status[("B", "color")], "GT_HOLDS")

    def test_an_image_unit_gt_picks_its_own_picture(self) -> None:
        self.fx.profile["gtTask"]["images"].update({"matchField": "pic", "entryField": "pid", "tileRoles": []})
        rows = [dict(row, pic=f"{row['sku']}-1") for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        self.fx.task("prepare")
        item = next(item for item in self.fx.worklist()["items"] if item["key"] == "A")
        self.assertEqual([image["imageId"] for image in item["images"]], ["A-1"])

    def test_typed_values_need_a_declaration_and_keep_their_type(self) -> None:
        rows = [dict(row, lit=True) for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.profile["gtTask"]["fields"].append({"id": "lit", "name": "조명", "labels": ["true", "false"],
                                                    "labelNames": {"true": "켜짐", "false": "꺼짐"}})
        (self.fx.root / "definitions.md").write_text((self.fx.root / "definitions.md").read_text() + "\n## lit\n조명.\n")
        self.fx.save()
        # 선언 없이 참·거짓이 오면 멈추지 않고 그 칸을 «범위 밖»으로 올린다 — 한 줄의 오타가 과제 전체를 멈추지 않게.
        self.fx.task("prepare")
        lit = next(c for i in self.fx.worklist()["items"] for c in i["cells"] if c["field"] == "lit")
        self.assertIn("GT_OUT_OF_RANGE", lit["signals"])
        self.assertEqual(lit["current"], "<참·거짓 true>")
        self.fx.profile["gtTask"]["fields"][-1]["valueType"] = "boolean"
        self.fx.save()
        self.fx.task("prepare")
        self.record("--key", "A", "--field", "lit", "--decision", "CORRECT", "--value", "false", "--reviewer", "민준")
        self.fx.task("export")
        fixed = [json.loads(line) for line in (self.fx.review_dir() / "export/gt.corrected.jsonl").read_text().splitlines()]
        self.assertIs(fixed[0]["lit"], False)

    def test_a_single_field_task_updates_its_row_source_instead_of_adding_a_column(self) -> None:
        task = self.fx.profile["gtTask"]
        self.fx.only_first_field()
        task["gt"].pop("fieldSourcesField")
        task.pop("constraints")
        self.fx.save()
        self.fx.task("prepare")
        self.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["newColumns"], [])
        fixed = [json.loads(line) for line in (self.fx.review_dir() / "export/gt.corrected.jsonl").read_text().splitlines()]
        row = next(row for row in fixed if row["sku"] == "B")
        self.assertNotIn("fieldSources", row)
        self.assertTrue(row["src"].startswith("USER_GT_REVIEW_CORRECT_"))

    def test_the_cycle_s_lineage_files_count_as_its_gt(self) -> None:
        from gt_task import ledger_targets

        repo = self.fx.root / "repo"
        (repo / "data").mkdir(parents=True)
        (repo / "data/items-gt-20260101.jsonl").write_text("", encoding="utf-8")
        cycle = {"id": "cycle", "source": {"repository": str(repo)},
                 "gt": {"path": str(self.fx.root / "merged.jsonl"), "sourceDir": "data",
                        "lineages": [{"pattern": "^items-gt-(\\d{8})\\.jsonl$"}]}}
        self.assertIn((repo / "data/items-gt-20260101.jsonl").resolve(), ledger_targets(cycle))

    def test_a_missing_file_stops_with_a_sentence(self) -> None:
        completed = self.fx.task("finish", "--from", str(self.fx.root / "nope.json"), check=False)
        self.assertTrue(completed.stderr.startswith("멈춤:"), completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


class SourceAndUpstreamTest(unittest.TestCase):
    """원본 GT와 그 위(상류)를 지키는 자리 — 새 판, 값 검사, 정정 모양, 사람이 본 목록 그대로 넣기, 상류 목록, 대체 정답."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))
        self.fx.task("prepare")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def record(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.fx.record(*args, check=check)

    def test_a_gt_replaced_by_a_newer_one_is_refused(self) -> None:
        data = self.fx.root / "data"
        (data / "gt-new.provenance.json").write_text(json.dumps({"baseGt": "data/gt.jsonl"}), encoding="utf-8")
        self.fx.profile["gtTask"]["gt"]["supersededBy"] = {"glob": "*.provenance.json", "baseField": "baseGt",
                                                           "markerSuffix": ".provenance.json"}
        self.fx.save()
        completed = self.fx.task("prepare", check=False)
        self.assertIn("gt-new.jsonl", completed.stderr)

    def test_labels_and_constraint_values_are_checked(self) -> None:
        task = self.fx.profile["gtTask"]
        task["constraints"][0]["require"]["sheen"] = ["NONE", "LOWW"]
        self.fx.save()
        self.assertIn("LOWW", self.fx.task("prepare", check=False).stderr)
        task["constraints"][0]["require"]["sheen"] = ["NONE", "LOW"]
        self.fx.save()

    def test_a_many_value_constraint_matches_as_a_set(self) -> None:
        task = self.fx.profile["gtTask"]
        task["constraints"].append({"id": "BOTH_TONES_ARE_GLOSSY", "when": {"tones": ["WARM", "COOL"]},
                                    "require": {"finish": ["MATTE"]}})
        self.fx.save()
        self.fx.task("prepare")
        cells = {(i["key"], c["field"]): c for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertIn("GT_SELF_CONTRADICTION", cells[("F", "finish")]["signals"], "순서만 다른 집합도 같은 조건이다")

    def test_the_correction_line_names_the_column_and_keeps_applied_ones(self) -> None:
        self.fx.profile["gtTask"]["fields"][2]["gtField"] = "sheen"
        self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.task("apply", "--yes")
        self.fx.task("export")
        folder = self.fx.root / "gt/fixture-color-sheen/gt-review"
        line = json.loads((folder / "corrections.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(line["column"], "sheen")
        self.assertTrue(line["applied"], "원본에 들어간 뒤에도 «무엇을 고쳤나»에 남는다")

    def test_a_confirm_after_a_correct_does_not_carry_the_old_correction(self) -> None:
        self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", "민준")
        entry = json.loads(self.record("--key", "A", "--field", "sheen", "--decision", "CONFIRM", "--reviewer", "민준").stdout)
        self.assertTrue(entry["constraintWarnings"], "유지로 바꿨으니 옛 정정은 없고 모순이 다시 보여야 한다")

    def test_apply_puts_in_exactly_what_was_shown(self) -> None:
        self.record("--key", "A", "--field", "sheen", "--decision", "CORRECT", "--value", "LOW", "--reviewer", "민준")
        self.assertIn("먼저 만들어", self.fx.task("apply", "--yes", check=False).stderr)
        self.fx.task("export")
        self.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        self.assertIn("판정이 늘었습니다", self.fx.task("apply", "--yes", check=False).stderr)
        self.fx.task("export")
        gt = self.fx.root / "data/gt.jsonl"
        before = gt.read_text(encoding="utf-8").splitlines()
        self.fx.task("apply", "--yes")
        after = gt.read_text(encoding="utf-8").splitlines()
        self.assertEqual(after[2:], before[2:], "바뀌지 않은 줄은 글자 그대로")
        self.assertNotEqual(after[0], before[0])

    def test_a_gt_with_an_upstream_sheet_is_not_written_but_listed(self) -> None:
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트", "mirrorFields": ["colorMirror"]}
        rows = [dict(row, colorMirror=row["color"]) for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        # 거울 열은 필드가 하나인 과제에서만 — 여럿이면 한 필드의 답이 다른 필드의 거울을 덮는다.
        self.assertIn("mirrorFields", self.fx.task("prepare", check=False).stderr)
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["constraints"] = []
        self.fx.save()
        self.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.assertIn("붙여 넣어", self.fx.task("apply", "--yes", check=False).stderr)
        csv_text = (self.fx.review_dir() / "export/upstream-patch.csv").read_text(encoding="utf-8")
        # 상류 칸의 지금 값(거울 열)과 합친 GT를 따로 싣는다 — 시트 칸이 비어 옛 GT에서 온 값이면 둘이 다르다.
        self.assertIn("B,새로,color,BLUE,BLUE,RED", csv_text, "지난 목록에 없던 줄은 «새로»")
        # Excel로 열어 옮기는 파일이다 — BOM과, 스킬이 부르는 «새 값» 머리글.
        self.assertTrue(csv_text.startswith("\ufeff"))
        self.assertIn("새 값", csv_text.splitlines()[0])
        # «다음 거»로 새 배치를 만들어도 붙여 넣을 목록은 남는다.
        self.fx.task("prepare")
        self.assertTrue((self.fx.review_dir() / "export/upstream-patch.csv").is_file())
        fixed = [json.loads(line) for line in (self.fx.review_dir() / "export/gt.corrected.jsonl").read_text().splitlines()]
        self.assertEqual(next(row for row in fixed if row["sku"] == "B")["colorMirror"], "RED", "거울 열도 함께")

    def test_alternatives_are_not_disagreements(self) -> None:
        self.fx.profile["gtTask"]["fields"][0]["alternativesField"] = "colorAlternatives"
        rows = [dict(row) for row in GT_ROWS]
        rows[1]["colorAlternatives"] = ["RED"]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        self.fx.task("prepare")
        cells = {(i["key"], c["field"]): c for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertEqual(cells[("B", "color")]["alternatives"], ["RED"], "대체 정답은 칸에 실려 판독·화면이 GT와 같은 편으로 본다")

    def test_an_image_unit_gt_can_point_at_one_cut_piece(self) -> None:
        self.fx.profile["gtTask"]["images"].update({"matchField": "pic", "entryField": "pid"})
        rows = [dict(row, pic="D01T02") for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        self.assertIn("images.tileRule", self.fx.task("prepare", check=False).stderr, "조각 이름을 매긴 규칙을 밝혀야 한다")
        self.fx.profile["gtTask"]["images"]["tileRule"] = {"version": "v2-band-then-seam", "decoder": "java-imageio"}
        self.fx.save()
        # 운영의 D번호는 그 실행의 원본 목록 자리다. 색인이 전부를 같은 순서로 가진다는 선언 없이는 세지 않는다.
        self.assertIn("sourceListComplete", self.fx.task("prepare", check=False).stderr)
        self.fx.profile["gtTask"]["images"]["sourceListComplete"] = True
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        self.fx.task("prepare")
        item = next(item for item in self.fx.worklist()["items"] if item["key"] == "A")
        self.assertEqual([image["imageId"] for image in item["images"]], ["D01T02"])

    def test_no_file_outside_the_reader_folder_names_a_reader_file(self) -> None:
        out = self.fx.task("prepare").stdout
        views = [item["view"] for item in json.loads(out[out.index("{"):])["workflowArgs"]["items"]]
        tokens = {Path(view).stem for view in views}
        for path in self.fx.review_dir().rglob("*"):
            if path.is_file() and "reader" not in path.relative_to(self.fx.review_dir()).parts:
                body = path.read_text(encoding="utf-8", errors="ignore")
                self.assertFalse(any(token in body for token in tokens), f"{path.name}이 판독 파일 이름을 적는다")

    def test_holds_count_as_answered_on_this_screen(self) -> None:
        self.fx.task("render")
        review = json.loads((self.fx.review_dir() / "review.json").read_text())
        cell = review["items"][0]["cells"][0]
        self.record("--key", cell["key"], "--field", cell["field"], "--decision", "HOLD", "--reviewer", "민준")
        status = json.loads(self.fx.task("status").stdout)
        self.assertEqual(status["heldOnThisPage"], 1)
        self.assertIn("CLEAR", status["decisions"])

    def test_reread_before_any_result_rereads_everything(self) -> None:
        # 판독이 도는 중(화면이 아직 없음)에는 다시 읽지 않는다 — 판독자가 열 파일을 지우게 된다.
        self.assertIn("아직 읽는 중", self.fx.task("prepare", "--reread", check=False).stderr)
        # 판독이 끝나지 못했을 때의 길(스킬): 화면을 먼저 만들고(전부 «못 읽음») 다시 읽는다 — 전부 다시 읽는다.
        self.fx.task("render")
        out = self.fx.task("prepare", "--reread").stdout
        args = json.loads(out[out.index("{"):])["workflowArgs"]
        self.assertEqual(len(args["items"]), len([i for i in self.fx.worklist()["items"] if not i["noEvidence"]]))

    def test_a_broken_profile_stops_the_one_ledger_check(self) -> None:
        broken = self.fx.root / "attributes/broken/profile.json"
        broken.parent.mkdir(parents=True)
        broken.write_text("{", encoding="utf-8")
        self.assertIn("원장 하나", self.fx.task("prepare", check=False).stderr)

    def test_a_reading_keyed_by_the_field_name_still_lands(self) -> None:
        worklist = self.fx.worklist()
        a = next(item for item in worklist["items"] if item["key"] == "A")
        self.fx.task("finish", "--from", str(self.fx.sweep({"A": [reading("광택 강도", "LOW")]})))
        review = json.loads((self.fx.review_dir() / "review.json").read_text(encoding="utf-8"))
        status = {(c["key"], c["field"]): c["status"] for i in review["items"] for c in i["cells"]}
        self.assertNotEqual(status[("A", "sheen")], "NOT_READ", "한국어 이름으로 적어도 그 칸에 붙는다")
        self.assertTrue(a)

    def test_the_contract_names_everything_kept_across_batches(self) -> None:
        from gt_review import KEEP_ACROSS_BATCHES

        contract = (PROJECT_ROOT / ".claude/os/engine/contracts/gt-task.md").read_text(encoding="utf-8")
        self.assertEqual([name for name in sorted(KEEP_ACROSS_BATCHES) if f"`{name}" not in contract], [],
                         "prepare가 남기는 것은 계약에 적혀 있어야 한다 — 판독자가 닿을 수 있는 자리다")

    def test_prompts_and_agent_docs_match_the_reader_layout(self) -> None:
        body = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("runRoot", body)
        agent = (PROJECT_ROOT / ".claude/os/engine/agents/gt-blind-reader.md").read_text(encoding="utf-8")
        self.assertIn("gt-review/reader/", agent)
        self.assertNotIn("reader-view/", agent)


class ScreenAndBatchTest(unittest.TestCase):
    """화면과 배치의 규칙 — 보류는 그 배치에서만 답, 반영된 칸은 반영됨, 보여 주기만 할 사진은 버리지 않는다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))
        self.fx.task("prepare")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_hold_from_an_earlier_batch_is_asked_again(self) -> None:
        self.fx.task("render")
        cell = json.loads((self.fx.review_dir() / "review.json").read_text())["items"][0]["cells"][0]
        self.fx.record("--key", cell["key"], "--field", cell["field"], "--decision", "HOLD", "--reviewer", "민준")
        self.assertEqual(json.loads(self.fx.task("status").stdout)["heldOnThisPage"], 1)
        self.fx.task("prepare", "--key", cell["key"])
        self.fx.task("render")
        status = json.loads(self.fx.task("status").stdout)
        self.assertEqual(status["heldOnThisPage"], 0, "지난 배치의 보류는 이 화면의 답이 아니다")

    def test_answered_on_page_rule(self) -> None:
        from gt_review import answered_on_page

        self.assertTrue(answered_on_page({"decision": "CORRECT", "before": "A", "after": "B"}, "B", "x"), "넣은 뒤도 답이다")
        self.assertFalse(answered_on_page({"decision": "CORRECT", "before": "A", "after": "B"}, "C", "x"))
        self.assertTrue(answered_on_page({"decision": "HOLD", "before": "A", "batchId": "x"}, "A", "x"))
        self.assertFalse(answered_on_page({"decision": "HOLD", "before": "A", "batchId": "old"}, "A", "x"))

    def test_an_old_keep_on_a_cell_still_contradicted_is_not_an_answer(self) -> None:
        # 모순 칸에 «유지»를 누르면 다음 배치에 다시 올라온다. 그 옛 유지를 화면·status가 답으로 세면
        # 같은 건이 매 배치 맨 앞에 오는데 «다 했습니다»라고 말한다(검토에서 재현).
        for field, value in (("finish", "MATTE"), ("sheen", "HIGH")):
            self.fx.record("--key", "A", "--field", field, "--decision", "CONFIRM", "--reviewer", "민준")
        self.fx.task("prepare", "--key", "A")
        self.fx.task("finish", "--from", str(self.fx.sweep({"A": [reading("finish", "MATTE"), reading("sheen", "HIGH")]})))
        status = json.loads(self.fx.task("status").stdout)
        self.assertGreaterEqual(status["waitingForHuman"], 1)
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertNotRegex(page, r'class="cell [A-Z_]+ done"[^>]*data-key="A"', "옛 유지를 끝난 칸으로 칠하지 않는다")
        self.assertIn('data-contradicted="1"', page)
        # 이 배치에서 다시 누르면 그때는 답이다.
        from gt_decisions import answered_on_page
        self.assertFalse(answered_on_page({"decision": "CONFIRM", "before": "HIGH", "batchId": "old"}, "HIGH", "x", True))
        self.assertTrue(answered_on_page({"decision": "CONFIRM", "before": "HIGH", "batchId": "x"}, "HIGH", "x", True))

    def test_reason_and_gap_are_asked_for_before_the_button(self) -> None:
        self.fx.task("render")
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        cell = page[page.index('<div class="cell'):]
        self.assertIn('class="gap"', cell.split('<div class="cell')[1] if '<div class="cell' in cell[5:] else cell, "경계 표시는 칸 안에 있다")
        self.assertIn('<details class="memo-add">', page, "이유·경계 표시는 접어 둔다 — AI가 기준 밖이라고 짚은 칸만 펼친다")
        self.assertIn("아직 저장되지 않았습니다", page, "누른 뒤 바꾸면 다시 누르라고 말한다")

    def test_apply_keeps_line_endings_and_does_nothing_twice(self) -> None:
        gt = self.fx.root / "data/gt.jsonl"
        lines = gt.read_text(encoding="utf-8").splitlines()
        gt.write_bytes(("\r\n".join(lines[:2]) + "\r\n\r\n" + "\r\n".join(lines[2:]) + "\r\n").encode("utf-8"))
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.assertEqual(json.loads(self.fx.task("export").stdout)["linesToChange"], 1)
        self.fx.task("apply", "--yes")
        after = gt.read_bytes().decode("utf-8")
        self.assertEqual(after.count("\r\n"), len(lines) + 1, "줄 끝과 빈 줄은 원본 그대로")
        self.assertEqual(sum(1 for a, b in zip(after.splitlines(), [*lines[:2], "", *lines[2:]]) if a != b), 1)
        # 넣은 뒤 다시 «반영해줘»·«넣어줘» — 넣을 것이 없으면 묻지도, 사본을 만들지도 않는다.
        self.assertEqual(json.loads(self.fx.task("export").stdout)["linesToChange"], 0)
        backups = len(list(gt.parent.glob("gt.jsonl.before-gt-review-*")))
        again = json.loads(self.fx.task("apply", "--yes").stdout)
        self.assertFalse(again["applied"])
        self.assertEqual(len(list(gt.parent.glob("gt.jsonl.before-gt-review-*"))), backups)

    def test_apply_checks_one_ledger_again(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        # 목록을 보여 준 뒤 감사 사이클 프로필이 같은 GT를 가리키기 시작했다.
        other = self.fx.profile_path.parent.parent / "cycle" / "profile.json"
        other.parent.mkdir(parents=True)
        cycle = {key: value for key, value in self.fx.profile.items() if key != "gtTask"}
        cycle.update({"id": "cycle", "outputRoot": str(self.fx.root / "cycle-run"),
                      "gt": {"path": str(self.fx.root / "data/gt.jsonl")}})
        other.write_text(json.dumps(cycle), encoding="utf-8")
        completed = self.fx.task("apply", "--yes", check=False)
        self.assertIn("프로필 cycle도 고칩니다", completed.stderr)

    def test_an_old_correction_that_left_a_contradiction_is_not_an_answer(self) -> None:
        # 고쳤는데도 모순이 남았으면(한쪽만 고침) 그 옛 고침도 이 배치의 답이 아니다 — 유지와 같다.
        self.fx.record("--key", "A", "--field", "finish", "--decision", "CORRECT", "--value", "GLOSSY", "--reviewer", "민준")
        from gt_decisions import answered_on_page
        entry = {"decision": "CORRECT", "before": "MATTE", "after": "GLOSSY", "batchId": "old"}
        self.assertFalse(answered_on_page(entry, "GLOSSY", "x", True))
        self.assertTrue(answered_on_page({**entry, "batchId": "x"}, "GLOSSY", "x", True))
        self.assertTrue(answered_on_page(entry, "GLOSSY", "x", False), "모순이 없으면 옛 고침도 답이다")

    def test_loader_stops_on_likely_profile_mistakes(self) -> None:
        task = self.fx.profile["gtTask"]
        cases = [
            (lambda t: t["images"].__setitem__("contextFields", ["category", "sku"]), "sku"),
            (lambda t: t["fields"][0]["legacy"].__setitem__("CRIMSON", "CRIMSONISH"), "legacy"),
            (lambda t: t["fields"][0].__setitem__("unknownLabel", "UNSURE"), "unknownLabel"),
        ]
        original = json.dumps(task)
        for change, needle in cases:
            self.fx.profile["gtTask"] = json.loads(original)
            change(self.fx.profile["gtTask"])
            self.fx.save()
            self.assertIn(needle, self.fx.task("prepare", check=False).stderr)
        self.fx.profile["gtTask"] = json.loads(original)
        self.fx.save()

    def test_quiet_misconfigurations_are_warned(self) -> None:
        self.fx.profile["gtTask"]["evidence"]["textFields"] = ["notise"]
        self.fx.save()
        self.fx.task("prepare")
        warnings = self.fx.worklist()["warnings"]
        self.assertIn("notise", warnings["evidenceColumnsNeverSeen"])

    def test_upstream_confirmations_are_nothing_to_paste(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트"}
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["linesToChange"], 0, "유지 확인은 상류로 가지 않는다 — 붙여 넣을 것이 없다")

    def test_apply_keeps_the_file_mode(self) -> None:
        import os
        import stat

        gt = self.fx.root / "data/gt.jsonl"
        os.chmod(gt, 0o644)
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.task("apply", "--yes")
        self.assertEqual(stat.S_IMODE(gt.stat().st_mode), 0o644, "외부 레포의 GT가 0600이 되면 다른 계정·CI가 못 읽는다")

    def test_a_declared_source_index_is_never_guessed(self) -> None:
        from PIL import Image

        images = self.fx.root / "data/images.jsonl"
        rows = [json.loads(line) for line in images.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.fx.profile["gtTask"]["images"]["sourceIndexField"] = "srcIdx"
        self.fx.save()
        for value in (None, "D03", 0):
            for row in rows:
                for pic in row["pics"]:
                    if pic.get("kind") == "DETAIL_SOURCE":
                        pic.pop("srcIdx", None)
                        if value is not None:
                            pic["srcIdx"] = value
            write_jsonl(images, rows)
            self.fx.task("prepare")
            item = next(item for item in self.fx.worklist()["items"] if item["key"] == "A")
            self.assertFalse(any(image["imageId"].startswith("D") for image in item["images"]), f"srcIdx={value!r}")
            self.assertTrue(any("D번호" in m["reason"] for m in item["missing"]), f"srcIdx={value!r}")

    def test_another_attribute_s_missing_lineage_does_not_block_this_task(self) -> None:
        other = self.fx.profile_path.parent.parent / "cycle" / "profile.json"
        other.parent.mkdir(parents=True)
        cycle = {key: value for key, value in self.fx.profile.items() if key != "gtTask"}
        cycle.update({"id": "cycle", "outputRoot": str(self.fx.root / "cycle-run"),
                      "source": {"repository": str(self.fx.root / "absent-repo")},
                      "gt": {"path": str(self.fx.root / "merged.jsonl"), "sourceDir": "data",
                             "lineages": [{"pattern": "^x\\.jsonl$"}]}})
        other.write_text(json.dumps(cycle), encoding="utf-8")
        self.fx.task("prepare")  # 이 과제의 GT는 없는 폴더 밖에 있으니 겹칠 수 없다

    def test_screen_says_what_a_button_does_and_many_values_are_checkboxes(self) -> None:
        self.fx.task("render")
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertIn("반영하기 — GT에 바로 넣기", page, "원본이 이 컴퓨터의 파일이면 버튼이 곧바로 넣는다고 이름이 말한다")
        self.assertIn("GT에 넣었습니다", page)
        self.assertNotIn("<th>키</th><th>항목</th>", page, "누른 뒤 목록 표를 펼치지 않는다 — 결과는 한 줄")
        self.assertNotIn("GT가 고쳐집니다", page)
        self.assertIn('class="pick"', page, "값 여럿은 체크 상자")
        self.assertNotIn("GT로 유지</button>", page, "AI가 GT와 같게 본 칸은 누를 것이 없다")
        status = json.loads(self.fx.task("status").stdout)
        self.assertIn("holdsUnconfirmed", status)

    def test_a_flat_index_groups_its_rows_and_a_listed_index_refuses_duplicates(self) -> None:
        # 한 줄에 사진 한 장인 색인은 같은 키의 줄을 모은다 — 마지막 줄만 조용히 남기면 판독이 사진 한 장만 본다.
        from catalog_profile import load_profile
        from gt_images import ImageIndex
        from gt_task import load_task

        flat = self.fx.root / "data/flat.jsonl"
        write_jsonl(flat, [{"sku": "A", "f": "img/a.jpg", "pid": f"A-{n}"} for n in range(3)])
        self.fx.profile["gtTask"]["images"] = {"path": str(flat), "keyField": "sku", "fileField": "f",
                                               "fileBase": str(self.fx.root / "data"), "idField": "pid"}
        self.fx.save()
        profile = load_profile(self.fx.profile_path)
        index = ImageIndex(profile, load_task(profile))
        self.assertEqual([entry["pid"] for entry in index.entries({"sku": "A"})], ["A-0", "A-1", "A-2"])
        listed = self.fx.root / "data/images.jsonl"
        rows = [json.loads(line) for line in listed.read_text(encoding="utf-8").splitlines() if line.strip()]
        write_jsonl(listed, rows + [rows[0]])
        self.fx.profile["gtTask"]["images"] = {"path": str(listed), "keyField": "sku", "listField": "pics", "fileField": "f",
                                               "fileBase": str(self.fx.root / "data"), "idField": "pid"}
        self.fx.save()
        self.assertIn("키가 겹칩니다", self.fx.task("prepare", check=False).stderr)

    def test_context_needs_no_image_index(self) -> None:
        self.fx.profile["gtTask"]["images"] = {"contextFields": ["category"]}
        write_jsonl(self.fx.root / "data/gt.jsonl", [dict(row, category="잡화>테스트") for row in GT_ROWS])
        self.fx.save()
        self.fx.task("prepare")
        self.assertTrue(all(item["context"] == {"category": "잡화>테스트"} for item in self.fx.worklist()["items"]))

    def test_status_does_not_count_cells_whose_source_changed(self) -> None:
        self.fx.task("finish", "--from", str(self.fx.sweep({"F": [reading("color", "PURPLE", "LOW")]})))
        before = json.loads(self.fx.task("status").stdout)
        rows = [dict(row, color="GREEN") if row["sku"] == "F" else row for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        after = json.loads(self.fx.task("status").stdout)
        self.assertEqual(after["staleOnPage"], 1)
        self.assertEqual(after["waitingForHuman"], before["waitingForHuman"] - 1, "화면처럼 원본이 바뀐 칸은 할 일에서 뺀다")

    def test_typed_values_written_as_text_keep_their_shape(self) -> None:
        from gt_task import export_value, raw_differs

        field = {"id": "n", "labels": ["1", "2"], "valueType": "integer"}
        self.assertFalse(raw_differs(field, {"n": "2"}), "«유지»만 눌러도 글자 2가 정수 2로 바뀌면 안 된다")
        self.assertEqual(export_value(field, "1", "2"), "1")
        self.assertEqual(export_value(field, "1", 2), 1)

    def test_bare_value_codes_in_ai_sentences_are_flagged(self) -> None:
        from gt_review import korean_warnings

        codes = {"color": {"RED": "빨강", "NONE": "무늬 없음"}, "sheen": {"NONE": "광택 없음"}, "n": {"2": "둘"}}

        def sweep(text: str, field: str = "color") -> dict:
            return {"items": [{"id": "GI-01", "reading": {"readings": [{"field": field, "observation": text}]}}]}

        self.assertEqual(korean_warnings(sweep("사진에서 빨강 (RED)이 보인다"), set(), codes), [])
        self.assertEqual(korean_warnings(sweep("사진에서 RED가 보인다"), set(), codes), ["GI-01"])
        self.assertEqual(korean_warnings(sweep("사진에서 붉은색(RED)이 보인다"), set(), codes), ["GI-01"])
        # 같은 코드를 필드마다 다른 이름으로 쓰면 자기 필드의 이름으로 본다.
        self.assertEqual(korean_warnings(sweep("광택 없음 (NONE)", "sheen"), set(), codes), [])
        self.assertEqual(korean_warnings(sweep("무늬 없음 (NONE)", "color"), set(), codes), [])
        # 숫자 코드는 보지 않는다 — «사진 2장»의 2는 코드가 아니다.
        self.assertEqual(korean_warnings(sweep("사진 2장을 보았다", "n"), set(), codes), [])

    def test_piece_names_are_read_like_production(self) -> None:
        self.fx.profile["gtTask"]["images"].update({"matchField": "pic", "entryField": "pid", "sourceListComplete": True,
                                                   "tileRule": {"version": "v2-band-then-seam", "decoder": "java-imageio"}})
        self.fx.save()
        for name in ("D1T2", "A-D01T02"):
            write_jsonl(self.fx.root / "data/gt.jsonl", [dict(row, pic=name) for row in GT_ROWS])
            self.fx.task("prepare")
            item = next(item for item in self.fx.worklist()["items"] if item["key"] == "A")
            self.assertEqual([image["imageId"] for image in item["images"]], ["D01T02"], name)

    def test_prepare_keeps_the_screen_when_the_index_is_missing(self) -> None:
        # 지우고 나서 멈추면 사람이 답하던 화면이 사라진다. 멈출 수 있는 것은 지우기 전에 본다.
        self.fx.task("render")
        (self.fx.root / "data/images.jsonl").unlink()
        self.assertIn("사진 색인", self.fx.task("prepare", check=False).stderr)
        self.assertTrue((self.fx.review_dir() / "review.html").is_file())
        self.assertTrue((self.fx.review_dir() / "manifest.json").is_file())

    def test_a_raw_name_in_the_gt_can_still_be_answered(self) -> None:
        # 원본 칸에 이름(«빨강»)이 날 값으로 들어 있으면(범위 밖), 화면이 보낸 그 날 값도 «본 값»으로 받는다.
        write_jsonl(self.fx.root / "data/gt.jsonl", [dict(row, color="빨강") if row["sku"] == "E" else row for row in GT_ROWS])
        self.fx.save()
        self.fx.task("record", "--key", "E", "--field", "color", "--decision", "CORRECT", "--value", "RED",
                     "--expect", "빨강", "--reviewer", "민준")

    def test_new_marks_survive_a_repeat_export(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트"}
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        first = json.loads(self.fx.task("export").stdout)
        again = json.loads(self.fx.task("export").stdout)
        self.assertEqual((first["newSinceLastList"], again["newSinceLastList"]), (1, 1), "새 판정 없이 다시 내면 같은 줄이 «새로»")
        self.assertTrue(again["sameAsLastList"])
        self.fx.record("--key", "E", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.assertEqual(json.loads(self.fx.task("export").stdout)["newSinceLastList"], 1)

    def test_a_hold_is_answered_only_on_the_value_it_was_made_on(self) -> None:
        from gt_decisions import answered_on_page

        self.assertFalse(answered_on_page({"decision": "HOLD", "before": "A", "after": None, "batchId": "x"}, None, "x"))

    def test_many_values_written_as_text_keep_their_shape(self) -> None:
        from gt_task import export_value, raw_differs

        field = {"id": "tones", "labels": ["WARM", "COOL"], "cardinality": "many"}
        self.assertFalse(raw_differs(field, {"tones": "COOL|WARM"}))
        self.assertEqual(export_value(field, "COOL|WARM", "WARM"), "COOL|WARM")
        self.assertEqual(export_value(field, "COOL|WARM", ["WARM"]), ["COOL", "WARM"])

    def test_group_can_be_context_and_title_needs_an_opt_in(self) -> None:
        task = self.fx.profile["gtTask"]
        write_jsonl(self.fx.root / "data/gt.jsonl", [dict(row, maker="M", name=f"n{row['sku']}") for row in GT_ROWS])
        task.update({"groupField": "maker", "titleField": "name"})
        task["images"]["contextFields"] = ["category", "maker"]
        self.fx.save()
        self.fx.task("prepare")
        task["evidence"]["textFields"] = ["notice", "name"]
        self.fx.save()
        self.assertIn("titleAsEvidence", self.fx.task("prepare", check=False).stderr)
        task["titleAsEvidence"] = True
        self.fx.save()
        self.fx.task("prepare")

    def test_agent_tools_as_a_yaml_list(self) -> None:
        from gt_task import agent_tools

        self.assertEqual(agent_tools("name: x\ntools:\n  - Read\n  - Grep\nmodel: y"), {"Read", "Grep"})
        self.assertEqual(agent_tools("tools: Read, Glob"), {"Read", "Glob"})

    def test_a_piece_that_is_really_an_original_is_cut_again(self) -> None:
        from PIL import Image

        tall = self.fx.root / "data/img/tall-piece.png"
        Image.new("RGB", (100, 900), "white").save(tall)
        images = self.fx.root / "data/images.jsonl"
        rows = [json.loads(line) for line in images.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            row["pics"] = [{"f": "img/tall-piece.png", "pid": "D01T02", "kind": "PIECE"},
                           {"f": "img/tall-piece.png", "pid": "PLAIN", "kind": "PIECE"}]
        write_jsonl(images, rows)
        self.fx.profile["gtTask"]["images"].update({"preTiledRoles": ["PIECE"], "tileRoles": [],
                                                   "preTiledRule": {"version": "v0-fixed", "decoder": "harness-pillow"}})
        self.fx.save()
        self.fx.task("prepare")
        item = next(item for item in self.fx.worklist()["items"] if item["key"] == "A")
        self.assertEqual([image["imageId"] for image in item["images"]], ["D01T02"])
        self.assertTrue(any("원본" in m["reason"] for m in item["missing"]), "몇 번째 조각인지 모르면 보이지 않는다")

    def test_locators_that_point_at_different_rows_are_not_handed_out(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트", "mirrorFields": ["colorMirror"],
                                                       "locatorFields": ["tab", "sheetRow", "sourceCell"],
                                                       "rowCheck": {"row": "sheetRow", "cell": "sourceCell"}}
        rows = [dict(row, colorMirror=None, tab="9/3", sheetRow=14, sourceCell="AQ56" if row["sku"] == "B" else "AQ14")
                for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.record("--key", "E", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["locatorConflicts"], 1)
        lines = (self.fx.review_dir() / "export/upstream-patch.csv").read_text(encoding="utf-8").splitlines()
        b_line = next(line for line in lines if line.startswith("B,"))
        self.assertIn("위치 불일치", b_line)
        self.assertNotIn("AQ56", b_line, "어긋난 위치는 적지 않는다 — 한쪽을 믿으면 다른 상품의 줄을 고친다")
        self.assertIn("AQ14", next(line for line in lines if line.startswith("E,")))

    def test_a_row_number_from_another_tab_is_dropped_but_the_cell_kept(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {
            "kind": "sheet", "note": "검수 시트", "mirrorFields": ["colorMirror"],
            "locatorFields": ["tab", "sheetRow", "sourceCell"],
            "rowCheck": {"row": "sheetRow", "cell": "sourceCell", "sheet": "tab", "rowSheet": "첫 탭"}}
        rows = [dict(row, colorMirror=None, tab="둘째 탭" if row["sku"] == "B" else "첫 탭", sheetRow=14,
                     sourceCell="AQ56" if row["sku"] == "B" else "AQ14") for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["locatorConflicts"], 0, "다른 탭의 행 번호는 어긋남이 아니다")
        b_line = next(line for line in (self.fx.review_dir() / "export/upstream-patch.csv").read_text(encoding="utf-8").splitlines()
                      if line.startswith("B,"))
        self.assertIn("둘째 탭,,AQ56", b_line, "행 번호만 비우고 주소는 남긴다")

    def test_a_row_check_must_name_locator_columns(self) -> None:
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "locatorFields": ["sheetRow"],
                                                       "rowCheck": {"row": "sheetRow", "cell": "cell"}}
        self.fx.save()
        self.assertIn("rowCheck", self.fx.task("prepare", check=False).stderr)

    def test_a_withdrawn_correction_becomes_a_revert_line(self) -> None:
        # 시트 칸(거울 열)이 비어 있던 줄을 건넸다가 거두면, 되돌림은 «칸을 비워 주세요»다 — 합친 GT 값을 적지 않는다.
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트", "mirrorFields": ["colorMirror"],
                                                       "locatorFields": ["sheetRow"]}
        rows = [dict(row, colorMirror=None, sheetRow=n + 2) for n, row in enumerate(GT_ROWS)]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.record("--key", "B", "--field", "color", "--decision", "HOLD", "--reviewer", "민준")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual([row["field"] for row in summary["withdrawn"]], ["color"])
        self.assertEqual((summary["linesToChange"], summary["newSinceLastList"]), (1, 1), "되돌림만 있어도 건넬 목록이다")
        csv_text = (self.fx.review_dir() / "export/upstream-patch.csv").read_text(encoding="utf-8")
        self.assertIn("칸을 비워 주세요", csv_text)
        self.assertIn("sheetRow", csv_text.splitlines()[0], "되돌림만 있어도 시트 행 열은 남는다")
        # 다시 내보내도(새 판정 없음) 같은 되돌림이 남는다.
        self.assertEqual(len(json.loads(self.fx.task("export").stdout)["withdrawn"]), 1)
        # 시트가 거둔 값을 이미 품었으면(새로 고침 뒤 거울 열 = 건넨 새 값), 새 목록이 생겨도 되돌릴 때까지 남는다.
        write_jsonl(self.fx.root / "data/gt.jsonl", [dict(row, colorMirror="RED") if row["sku"] == "B" else row for row in rows])
        self.fx.record("--key", "E", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.assertEqual(len(json.loads(self.fx.task("export").stdout)["withdrawn"]), 1)

    def test_a_replaced_correction_is_not_a_revert(self) -> None:
        # 건넨 정정을 다른 정정으로 바꾸면 새 줄이 덮는다 — 되돌리라고 하지 않는다.
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트"}
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "GREEN", "--reviewer", "민준")
        self.assertEqual(json.loads(self.fx.task("export").stdout)["withdrawn"], [])

    def test_endorsing_the_handed_out_value_is_not_a_revert(self) -> None:
        # 시트가 건넨 값을 품은 뒤(GT = 새 값) 그 값에 «맞다»고 하면 받아들인 것이지 거둔 것이 아니다.
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트", "mirrorFields": ["colorMirror"]}
        rows = [dict(row, colorMirror=row["color"]) for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        write_jsonl(self.fx.root / "data/gt.jsonl",
                    [dict(row, color="RED", colorMirror="RED") if row["sku"] == "B" else row for row in rows])
        self.fx.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        self.assertEqual(json.loads(self.fx.task("export").stdout)["withdrawn"], [])

    def test_pressing_hold_again_does_not_reissue_the_revert(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트"}
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.record("--key", "B", "--field", "color", "--decision", "HOLD", "--reviewer", "민준")
        self.assertEqual(json.loads(self.fx.task("export").stdout)["newSinceLastList"], 1)
        self.fx.record("--key", "E", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.record("--key", "B", "--field", "color", "--decision", "HOLD", "--reviewer", "민준", "--reason", "다시 보류")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["newSinceLastList"], 1, "다시 누른 보류가 옛 되돌림을 «새로» 만들지 않는다 (E만 새로)")

    def test_the_apply_preview_leaves_no_handout_record(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트"}
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("apply")
        log = self.fx.root / "gt/fixture-color-sheen/gt-review/handed-out.jsonl"
        self.assertFalse(log.exists() and log.read_text(encoding="utf-8").strip(), "미리 보기는 «건넸다»를 적지 않는다")

    def test_a_confirmation_that_does_not_reach_the_sheet_is_counted(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트"}
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        self.assertEqual(json.loads(self.fx.task("export").stdout)["confirmationsNotUpstream"], 1)

    def test_an_alternative_answer_is_not_called_the_same_value(self) -> None:
        from gt_review_render import merge

        worklist = {"fields": [{"id": "f", "labels": ["X", "Y"]}], "items": [
            {"id": "GI-01", "key": "k", "cells": [{"key": "k", "field": "f", "current": "X", "signals": ["GT_REFERENCE_ONLY"],
                                                  "alternatives": ["Y"]}]}]}
        sweep = {"items": [{"id": "GI-01", "reading": {"readings": [{"field": "f", "value": "Y", "confidence": "HIGH"}]}}]}
        cell = merge(worklist, sweep, [])["items"][0]["cells"][0]
        self.assertEqual(cell["status"], "GT_HOLDS")
        self.assertTrue(cell["alternative"])
        self.assertIn("같이 맞다고 적어 둔 다른 값", cell["hint"])

    def test_many_value_proposals_drop_empty_pieces(self) -> None:
        from gt_review_render import merge

        worklist = {"fields": [{"id": "t", "labels": ["WARM", "COOL"], "cardinality": "many"}], "items": [
            {"id": "GI-01", "key": "k", "cells": [{"key": "k", "field": "t", "current": "COOL", "signals": ["GT_REFERENCE_ONLY"]}]}]}
        sweep = {"items": [{"id": "GI-01", "reading": {"readings": [{"field": "t", "value": "WARM|", "confidence": "HIGH"}]},
                            "defense": {"rebuttals": [{"field": "t", "verdict": "READER_RIGHT"}]}}]}
        self.assertEqual(merge(worklist, sweep, [])["items"][0]["cells"][0]["proposal"], "WARM")

    def test_a_line_separator_inside_a_value_is_not_a_new_row(self) -> None:
        rows = [dict(row, note="첫 줄\u2028둘째 줄") for row in GT_ROWS]
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.task("prepare")

    def test_a_declared_tile_rule_is_checked_even_without_pieces(self) -> None:
        self.fx.profile["gtTask"]["images"]["tileRule"] = {"version": "v2-band-then-seam"}
        self.fx.save()
        completed = self.fx.task("prepare", check=False)
        self.assertIn("tileRule", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_names_for_the_ops_team_are_required(self) -> None:
        self.fx.profile["gtTask"]["fields"][0]["labelNames"].pop("PURPLE")
        self.fx.save()
        self.assertIn("PURPLE", self.fx.task("prepare", check=False).stderr)
        self.fx.profile["gtTask"]["fields"][0]["labelNames"]["PURPLE"] = "보라"
        self.fx.profile["gtTask"]["fields"][0].pop("name")
        self.fx.save()
        self.assertIn("이름(name)", self.fx.task("prepare", check=False).stderr)

    def test_the_defender_reads_text_evidence_too(self) -> None:
        body = (PROJECT_ROOT / ".claude/os/engine/agents/gt-defender.md").read_text(encoding="utf-8")
        self.assertIn("글", body)
        self.assertIn("columnNames", body)

    def test_prepare_with_nothing_to_show_keeps_the_screen(self) -> None:
        self.fx.task("render")
        out = self.fx.task("prepare", "--key", "NOPE").stdout
        self.assertIsNone(json.loads(out[out.index("{"):])["workflowArgs"])
        manifest = json.loads((self.fx.review_dir() / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest.get("preparing"), "볼 칸이 없으면 지우지 않고 «준비 중»으로 남기지 않는다")
        self.assertTrue((self.fx.review_dir() / "review.html").is_file())

    def test_a_crash_after_clearing_does_not_leave_it_preparing(self) -> None:
        import gt_review

        original = gt_review._prepare_batch
        gt_review._prepare_batch = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            import argparse
            import os
            from unittest import mock
            args = argparse.Namespace(task="fixture-color-sheen", reread=False, key=None, limit=None, force=True)
            with mock.patch.dict(os.environ, {k: v for k, v in self.fx.env.items() if k.startswith("CATALOG_OS_")}):
                with self.assertRaises(RuntimeError):
                    gt_review.cmd_prepare(args)
        finally:
            gt_review._prepare_batch = original
        manifest = json.loads((self.fx.review_dir() / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["preparing"])
        self.assertTrue(manifest["failed"])

    def test_status_reports_a_batch_still_preparing(self) -> None:
        self.fx.task("prepare")
        status = json.loads(self.fx.task("status").stdout)
        self.assertTrue(status["preparing"])
        self.assertTrue(status["preparedAt"])

    def test_a_decision_from_an_old_batch_is_refused_by_the_recorder(self) -> None:
        # 서버 밖(말로 받은 판정)에서도 — 기록기가 잠금 안에서 지금 배치를 본다.
        from catalog_profile import load_profile
        from gt_decisions import DecisionRejected, record
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {k: v for k, v in self.fx.env.items() if k.startswith("CATALOG_OS_")}):
            profile = load_profile(self.fx.profile_path)
            with self.assertRaises(DecisionRejected):
                record(profile, "B", "color", "HOLD", "민준", expected_before="BLUE", channel="screen", batch="old-batch")

    def test_images_given_as_addresses_are_named_as_such(self) -> None:
        images = self.fx.root / "data/images.jsonl"
        rows = [json.loads(line) for line in images.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            row["pics"][0]["f"] = "https://example.invalid/a.jpg"
        write_jsonl(images, rows)
        self.fx.task("prepare")
        self.assertTrue(any("주소" in reason for reason in self.fx.worklist()["warnings"]["imagesMissing"]))

    def test_sources_matching_no_grade_are_counted(self) -> None:
        self.fx.task("prepare")
        # 픽스처의 출처 가운데 등급 패턴(^USER_, ^HAND_)에 안 맞는 것은 없다 — 하나를 바꿔 본다.
        write_jsonl(self.fx.root / "data/gt.jsonl", [dict(row, src="MYSTERY") if row["sku"] == "E" else row for row in GT_ROWS])
        self.fx.task("prepare")
        self.assertIn("MYSTERY", self.fx.worklist()["warnings"]["sourcesUnclassified"])

    def test_a_typed_field_fixes_a_wrong_text_value_to_its_type(self) -> None:
        from gt_task import export_value

        field = {"id": "lit", "labels": ["true", "false"], "valueType": "boolean"}
        self.assertIs(export_value(field, "false", "yes"), False, "형이 틀린 글자를 고치는 정정은 선언된 형으로")
        self.assertEqual(export_value(field, "false", "true"), "false", "원본이 글자로 적은 형이면 글자로")

    def test_prepare_prints_its_warnings_and_splits_the_empty_note(self) -> None:
        out = self.fx.task("prepare").stdout
        self.assertIn("warnings", json.loads(out[out.index("{"):]))
        self.fx.profile["gtTask"]["constraints"] = []
        self.fx.profile["gtTask"]["fields"][0]["fillMissing"] = False
        write_jsonl(self.fx.root / "data/gt.jsonl", [dict(row, src="USER_OK", color="RED", tones=["WARM"]) for row in GT_ROWS])
        self.fx.save()
        out = self.fx.task("prepare").stdout
        empty = json.loads(out[out.index("{"):])
        self.assertIsNone(empty["workflowArgs"])
        self.assertIn("설정", empty["note"], "답한 것이 없는데 볼 칸이 없으면 설정을 의심한다")

    def test_status_reports_a_failed_prepare_and_keeps_the_prepared_time(self) -> None:
        self.fx.task("render")
        status = json.loads(self.fx.task("status").stdout)
        self.assertFalse(status["failed"])
        self.assertTrue(status["preparedAt"], "화면을 그린 뒤에도 준비 시각을 잃지 않는다")

    def test_the_loader_refuses_a_bad_pattern_and_a_shared_name(self) -> None:
        self.fx.profile["gtTask"]["authority"]["trusted"] = ["^USER_("]
        self.fx.save()
        self.assertIn("정규식", self.fx.task("prepare", check=False).stderr)
        self.fx.profile["gtTask"]["authority"]["trusted"] = ["^USER_"]
        self.fx.profile["gtTask"]["fields"][1]["name"] = "색"
        self.fx.save()
        self.assertIn("두 필드", self.fx.task("prepare", check=False).stderr)

    def test_a_missing_seed_source_is_not_called_changed(self) -> None:
        definitions = self.fx.root / "definitions.md"
        body = definitions.read_text(encoding="utf-8")
        definitions.write_text("---\nseededFrom: .claude/nope/missing.md\nseededFromSha256: abc\n---\n" + body, encoding="utf-8")
        out = self.fx.task("prepare").stdout
        warnings = json.loads(out[out.index("{"):])["warnings"]
        self.assertEqual(warnings["definitionsSeedChanged"], [])
        self.assertEqual(warnings["definitionsSeedMissing"], [".claude/nope/missing.md"])

    def test_an_applied_correction_keeps_the_ledger_shape(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.task("apply", "--yes")
        self.fx.task("export")
        lines = [json.loads(line) for line in (self.fx.root / "gt/fixture-color-sheen/gt-review/corrections.jsonl")
                 .read_text(encoding="utf-8").splitlines() if line.strip()]
        applied = [line for line in lines if line.get("applied")]
        self.assertTrue(applied)
        for line in applied:
            self.assertTrue({"id", "field", "before", "after", "previousSource", "source", "reason"} <= set(line))

    def test_without_a_declared_prefix_the_source_name_comes_from_the_task(self) -> None:
        self.fx.profile["gtTask"].pop("correctionSourcePrefix")
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        fixed = [json.loads(line) for line in (self.fx.review_dir() / "export/gt.corrected.jsonl").read_text().splitlines()]
        row = next(row for row in fixed if row["sku"] == "B")
        self.assertTrue(row["fieldSources"]["color"].startswith("GT_REVIEW_FIXTURE_COLOR_SHEEN_CORRECT_"))

    def test_unread_counts_only_open_cells_and_includes_missing_rebuttals(self) -> None:
        # 판독은 GT와 달랐는데 반론이 안 온 칸도 «못 읽음»이다. 사람이 답한 칸은 세지 않는다.
        self.fx.task("finish", "--from", str(self.fx.sweep({"B": [reading("color", "RED")]})))
        before = json.loads(self.fx.task("status").stdout)["notRead"]
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertIn("nodefense", page)
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.assertEqual(json.loads(self.fx.task("status").stdout)["notRead"], before - 1)

    def test_free_text_cannot_become_an_excel_formula(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트"}
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준",
                       "--reason", "=HYPERLINK(\"x\")")
        self.fx.task("export")
        csv_text = (self.fx.review_dir() / "export/upstream-patch.csv").read_text(encoding="utf-8")
        self.assertIn("'=HYPERLINK", csv_text)

    def test_photos_that_never_join_the_gt_are_warned(self) -> None:
        self.fx.profile["gtTask"]["images"]["roleField"] = "type"
        self.fx.profile["gtTask"]["images"]["roles"] = ["THUMB", "DETAIL_SOURCE"]
        self.fx.save()
        out = self.fx.task("prepare").stdout
        warnings = json.loads(out[out.index("{"):])["warnings"]
        self.assertTrue(warnings["imagesCoverage"]["noneMatched"], "역할 열 오타로 사진이 다 걸러졌다")
        self.assertIn("THUMB", warnings["rolesNeverSeen"])

    def test_photo_kinds_reach_the_reader_and_the_screen(self) -> None:
        self.fx.profile["gtTask"]["images"]["roleNames"] = {"THUMB": "대표 사진", "DETAIL_SOURCE": "긴 상세"}
        self.fx.save()
        out = self.fx.task("prepare").stdout
        view = json.loads(Path(PROJECT_ROOT / json.loads(out[out.index("{"):])["workflowArgs"]["items"][0]["view"]).read_text())
        self.assertIn("대표 사진", [picture.get("role") for picture in view["images"]])
        self.fx.task("render")
        self.assertIn("대표 사진", (self.fx.review_dir() / "review.html").read_text(encoding="utf-8"))
        self.fx.profile["gtTask"]["images"]["roles"] = ["THUMB", "DETAIL_SOURCE"]
        self.fx.profile["gtTask"]["images"]["roleNames"] = {"NOPE": "없는 종류"}
        self.fx.save()
        self.assertIn("roleNames", self.fx.task("prepare", check=False).stderr)

    def test_a_harness_written_source_is_always_human_confirmed(self) -> None:
        # 정정 출처 앞머리를 등급 패턴에 안 넣어도, 이 하네스가 쓴 출처는 사람 확정이다 — 넣고 나서 «등급 모름»이 되지 않게.
        self.fx.profile["gtTask"].pop("correctionSourcePrefix")
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.task("apply", "--yes")
        out = self.fx.task("prepare").stdout
        self.assertEqual(json.loads(out[out.index("{"):])["warnings"]["sourcesUnclassified"], {})

    def test_two_values_for_one_field_go_to_a_person(self) -> None:
        out = self.fx.sweep({"B": [reading("color", "RED"), reading("color", "BLUE")]})
        self.fx.task("finish", "--from", str(out))
        cell = next(c for i in json.loads((self.fx.review_dir() / "review.json").read_text())["items"]
                    for c in i["cells"] if c["key"] == "B" and c["field"] == "color")
        self.assertEqual(cell["status"], "NEEDS_HUMAN_LOOK")
        agreed = (self.fx.review_dir() / "agreed.jsonl")
        self.assertNotIn('"B"', agreed.read_text(encoding="utf-8") if agreed.exists() else "")

    def test_a_wrongly_typed_value_is_rejected_as_input(self) -> None:
        from catalog_profile import load_profile
        from gt_decisions import DecisionRejected, record
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {k: v for k, v in self.fx.env.items() if k.startswith("CATALOG_OS_")}):
            profile = load_profile(self.fx.profile_path)
            with self.assertRaises(DecisionRejected):
                record(profile, "B", "tones", "CORRECT", "민준", value=["WARM", 3], expected_before="COOL", channel="screen")

    def test_the_screen_says_why_the_second_ai_was_not_called(self) -> None:
        self.fx.task("finish", "--from", str(self.fx.sweep({"B": [reading("color", "BLUE")]})))
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        cell = page.split('data-key="B" data-field="color"')[1].split('<div class="cell')[0]
        self.assertRegex(cell, r'class="chip chosen" data-decision="CONFIRM" data-label="BLUE" aria-pressed="true"',
                         "같게 본 칸은 지금 GT 값이 처음부터 골라진 모양으로 보인다(누르지 않으면 기록하지 않는다)")
        self.assertIn("두 값이 같아 묻지 않았음", cell, "반론 AI를 부르지 않은 까닭")

    def test_the_saved_workflow_args_hold_no_gt_value_and_no_reader_file(self) -> None:
        out = self.fx.task("prepare").stdout
        args = json.loads(out[out.index("{"):])["workflowArgs"]
        saved = json.loads((self.fx.review_dir() / "workflow-args.json").read_text(encoding="utf-8"))
        self.assertNotIn("gt", saved)
        self.assertNotIn("alternatives", saved)
        body = json.dumps(saved, ensure_ascii=False)
        self.assertFalse(any(Path(item["view"]).stem in body for item in args["items"]), "판독 파일 이름이 남으면 안 된다")

    def test_a_repeat_export_says_it_is_the_same_list(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "검수 시트"}
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.assertFalse(json.loads(self.fx.task("export").stdout)["sameAsLastList"])
        self.assertTrue(json.loads(self.fx.task("export").stdout)["sameAsLastList"], "새 판정 없이 다시 내면 «같은 목록»")

    def test_a_missing_gt_file_is_named_before_any_other_check(self) -> None:
        (self.fx.root / "data/gt.jsonl").unlink()
        self.assertIn("GT 파일이 없습니다", self.fx.task("prepare", check=False).stderr)

    def test_a_hold_without_any_screen_is_refused(self) -> None:
        (self.fx.review_dir() / "manifest.json").unlink()
        worklist = self.fx.review_dir() / "worklist.json"
        worklist.unlink()
        completed = self.fx.task("record", "--key", "B", "--field", "color", "--decision", "HOLD", "--reviewer", "민준",
                                 "--expect", "BLUE", check=False)
        self.assertIn("보류할 화면이 없습니다", completed.stderr)

    def test_apply_twice_says_nothing_to_apply(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.task("apply", "--yes")
        again = json.loads(self.fx.task("apply", "--yes").stdout)
        self.assertFalse(again["applied"], "우리가 바꾼 원본을 «원본이 바뀌었습니다»로 거절하지 않는다")

    def test_an_empty_gt_cell_ignores_alternatives_everywhere(self) -> None:
        from gt_review_render import cell_status

        self.assertEqual(cell_status(None, {"value": "BLUE", "confidence": "HIGH"}, None, ["RED", "BLUE"],
                                     alternatives=["BLUE"]), "CONTESTED")

    def test_a_single_value_reading_with_a_bar_is_not_an_agreement(self) -> None:
        self.fx.task("finish", "--from", str(self.fx.sweep({"B": [reading("color", "BLUE|")]})))
        agreed = self.fx.review_dir() / "agreed.jsonl"
        self.assertNotIn('"B"', agreed.read_text(encoding="utf-8") if agreed.exists() else "")

    def test_skipping_again_and_again_rotates_inside_the_top_rank(self) -> None:
        self.fx.profile["gtTask"]["limit"] = 1
        self.fx.save()
        firsts = []
        for _ in range(4):
            self.fx.task("prepare")
            self.fx.task("render")
            firsts.append(self.fx.worklist()["items"][0]["key"])
        self.assertEqual(set(firsts), {"A", "F"}, "모순·허용값 밖(맨 앞 등급)은 몇 번을 넘겨도 뒤 등급 밑에 묻히지 않는다")
        self.assertTrue(all(a != b for a, b in zip(firsts, firsts[1:])), f"넘길 때마다 다음 건으로 돈다: {firsts}")

    def test_a_proposal_against_a_trusted_gt_is_not_painted(self) -> None:
        out = self.fx.sweep({"A": [reading("sheen", "LOW")], "B": [reading("color", "RED")]},
                            {"A": [{"field": "sheen", "verdict": "READER_RIGHT", "why": "x", "evidenceImageIds": ["P01"]}],
                             "B": [{"field": "color", "verdict": "READER_RIGHT", "why": "x", "evidenceImageIds": ["P01"]}]})
        self.fx.task("finish", "--from", str(out))
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertNotRegex(page, r'class="chip primary[^"]*" data-decision="CORRECT" data-value="LOW"', "사람이 확정한 GT를 칠한 버튼으로 덮지 않는다")
        self.assertRegex(page, r'class="chip primary[^"]*" data-decision="CORRECT" data-value="RED"', "참고 등급 GT는 제안을 칠한다")

    def test_a_low_confidence_many_value_reading_is_offered_in_its_canonical_form(self) -> None:
        self.fx.task("finish", "--from", str(self.fx.sweep({"B": [reading("tones", "WARM|COOL|", "LOW")]})))
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertIn('data-decision="CORRECT" data-value="COOL|WARM"', page)

    def test_cells_waiting_to_be_read_again_are_not_waiting_for_a_person(self) -> None:
        out = self.fx.sweep({"B": [reading("color", "RED")]})
        value = json.loads(Path(out).read_text(encoding="utf-8"))
        value["result"]["needsRestart"] = {"agentType": "gt-defender", "role": "defender"}
        Path(out).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.fx.task("finish", "--from", str(out))
        status = json.loads(self.fx.task("status").stdout)
        self.assertEqual(status["waitingForHuman"], 0, "반론을 못 받은 칸은 다시 읽을 일이지 사람이 가를 일이 아니다")

    def test_a_declared_default_grade_lets_the_blind_reader_check_a_gt_with_no_source_and_no_run(self) -> None:
        task = self.fx.profile["gtTask"]
        task["gt"].pop("sourceField")  # 출처 열이 없는 GT — 칸별 출처 열(선언은 남긴다)도 줄에 없다
        self.fx.save()
        self.fx.task("prepare")
        cells = {(i["key"], c["field"]) for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertNotIn(("D", "color"), cells, "등급을 모르면 채워진 칸은 올라오지 않는다")
        task["authority"]["default"] = "reference"
        self.fx.save()
        self.fx.task("prepare")
        signals = {(i["key"], c["field"]): c["signals"] for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertEqual(signals[("D", "color")], ["GT_REFERENCE_ONLY"])
        task["authority"]["default"] = "maybe"
        self.fx.save()
        self.assertIn("default", self.fx.task("prepare", check=False).stderr)

    def test_a_profile_that_does_not_load_is_named_with_its_reason(self) -> None:
        broken = self.fx.root / "attributes" / "half-done" / "profile.json"
        broken.parent.mkdir(parents=True)
        broken.write_text(json.dumps({"id": "half-done", "gtTask": {}}), encoding="utf-8")
        listed = json.loads(self.fx.cli("tasks").stdout)
        self.assertIn("half-done", [row["id"] for row in listed["brokenProfiles"]])
        refused = self.fx.cli("prepare", "--task", "half-done", check=False)
        self.assertIn("읽지 못했습니다", refused.stderr)

    def test_each_decision_says_how_it_came_in(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        self.assertEqual(self.fx.ledger()["decisions"][-1]["channel"], "spoken")
        from gt_decisions import DecisionRejected, record

        with self.assertRaises(DecisionRejected):
            record(self.fx.saved_profile(), "B", "color", "CONFIRM", "민준", expected_before="BLUE", channel="guess")

    def test_one_button_when_the_low_reading_is_the_unknown_value(self) -> None:
        self.fx.profile["gtTask"]["fields"][2]["unknownLabel"] = "NONE"
        self.fx.save()
        self.fx.task("prepare")
        self.fx.task("finish", "--from", str(self.fx.sweep({"A": [reading("sheen", "NONE", "LOW")]})))
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        cell = page.split('data-key="A" data-field="sheen"')[1].split('<div class="cell')[0]
        self.assertEqual(cell.count('data-value="NONE"'), 1, "이름이 다른 두 버튼이 같은 판정을 남기지 않는다")
        self.assertIn("AI 의견 · 확신 낮음", cell, "AI도 그 값을 냈다는 것은 버튼이 아니라 제안 자리에 한 번만")

    def test_a_sweep_left_from_another_batch_is_ignored(self) -> None:
        stray = self.fx.review_dir() / "sweep-raw.json"
        stray.write_text(json.dumps({"schemaVersion": "gt-review-sweep-v2", "batchId": "old", "items": [
            {"id": "GI-01", "reading": {"readings": [reading("color", "日本")]}}]}), encoding="utf-8")
        self.fx.task("render")  # 멈추지 않고 «못 읽음» 화면을 그린다
        self.assertTrue((self.fx.review_dir() / "review.html").is_file())

    def test_a_late_finish_after_the_next_batch_is_refused(self) -> None:
        out = self.fx.sweep({"B": [reading("color", "RED")]})
        self.fx.task("prepare")
        self.assertIn("먼저 준비된 배치", self.fx.task("finish", "--from", str(out), check=False).stderr)

    def test_the_loader_refuses_columns_that_collide(self) -> None:
        cases = [
            (lambda t: t["fields"][1].__setitem__("gtField", "color"), "같은 GT 열"),
            (lambda t: t["fields"][1].__setitem__("gtField", "sku"), "키 열"),
            (lambda t: t["fields"][1].__setitem__("gtField", "src"), "출처 열"),
            (lambda t: t["fields"][0]["labelNames"].__setitem__("BLUE", "빨강"), "같은 이름"),
        ]
        original = json.loads(json.dumps(self.fx.profile))
        for change, words in cases:
            self.fx.profile = json.loads(json.dumps(original))
            change(self.fx.profile["gtTask"])
            self.fx.save()
            self.assertIn(words, self.fx.task("prepare", check=False).stderr)

    def test_an_applied_correction_keeps_one_shape_for_before_and_after(self) -> None:
        rows = [dict(row) for row in GT_ROWS]
        rows[1]["tones"] = "COOL"  # 값 여럿을 «A|B» 글자로 담은 열
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.record("--key", "B", "--field", "tones", "--decision", "CORRECT", "--value", "COOL|WARM", "--reviewer", "민준")
        rows[1]["tones"] = "COOL|WARM"  # 원본이 정정을 받아들였다
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.task("export")
        lines = [json.loads(line) for line in (self.fx.root / "gt/fixture-color-sheen/gt-review/corrections.jsonl")
                 .read_text(encoding="utf-8").splitlines() if line.strip()]
        line = next(line for line in lines if line["field"] == "tones")
        self.assertEqual((line["before"], line["after"]), ("COOL", "COOL|WARM"))

    def test_a_tracking_pixel_is_left_out_but_not_counted_as_a_failure(self) -> None:
        from PIL import Image

        Image.new("RGB", (1, 1), "white").save(self.fx.root / "data/img/dot.png")
        rows = [json.loads(line) for line in (self.fx.root / "data/images.jsonl").read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row["pics"].append({"f": "img/dot.png", "pid": f"{row['sku']}-dot", "kind": "THUMB"})
        write_jsonl(self.fx.root / "data/images.jsonl", rows)
        self.fx.task("prepare")
        item = next(item for item in self.fx.worklist()["items"] if item["key"] == "A")
        self.assertEqual(item["skippedAsNonContent"], ["A-dot"])
        self.assertNotIn("A-dot", [entry["imageId"] for entry in item["missing"]], "운영처럼 실패가 아니라 «내용 아님»")

    def test_a_name_that_is_another_code_or_another_field_is_refused(self) -> None:
        original = json.loads(json.dumps(self.fx.profile))
        self.fx.profile["gtTask"]["fields"][2]["labelNames"]["NONE"] = "LOW"
        self.fx.save()
        self.assertIn("다른 허용값 코드와 같습니다", self.fx.task("prepare", check=False).stderr)
        self.fx.profile = json.loads(json.dumps(original))
        self.fx.profile["gtTask"]["fields"][1]["name"] = "sheen"
        self.fx.save()
        self.assertIn("다른 필드의 id와 같습니다", self.fx.task("prepare", check=False).stderr)

    def test_a_code_is_never_translated_as_a_name(self) -> None:
        from gt_decisions import _codes

        spec = {"labels": ["NONE", "LOW"], "labelNames": {"NONE": "LOW", "LOW": "약함"}}
        self.assertEqual(_codes(spec, "LOW"), "LOW", "코드면 그대로 — 이름 풀이는 코드가 아닐 때만")
        self.assertEqual(_codes(spec, "약함"), "LOW")

    def test_the_apply_preview_does_not_move_what_the_person_saw(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        self.fx.task("export")
        self.fx.record("--key", "E", "--field", "color", "--decision", "CORRECT", "--value", "BLUE", "--reviewer", "지훈")
        self.fx.task("apply")  # 미리 보기
        refused = self.fx.task("apply", "--yes", check=False)
        self.assertIn("판정이 늘었습니다", refused.stderr, "사람이 보지 않은 판정은 넣지 않는다")

    def test_one_constraint_says_its_sentence_once(self) -> None:
        self.fx.record("--key", "D", "--field", "finish", "--decision", "CORRECT", "--value", "MATTE", "--reviewer", "민준")
        warnings = self.fx.ledger()["decisions"][-1]["constraintWarnings"]
        self.assertEqual(len(warnings), len(set(warnings)))

    def test_a_spoken_name_without_its_space_still_finds_the_value(self) -> None:
        from gt_decisions import _codes

        spec = {"labels": ["NONE", "LOW"], "labelNames": {"NONE": "판단 불가", "LOW": "약함"}}
        self.assertEqual(_codes(spec, "판단불가"), "NONE")
        twins = {"labels": ["A", "B"], "labelNames": {"A": "가 나", "B": "가·나"}}
        self.assertEqual(_codes(twins, "가나"), "가나", "둘 이상에 맞으면 옮기지 않는다")

    def test_reading_again_keeps_the_files_a_running_reader_may_open(self) -> None:
        views = sorted((self.fx.review_dir() / "reader").rglob("*.json"))
        self.assertTrue(views)
        self.fx.task("render")
        self.fx.task("prepare", "--reread")
        self.assertTrue(all(view.exists() for view in views), "첫 판독이 아직 돌 수 있다 — 지난 판독 파일을 지우지 않는다")

    def test_a_confident_reading_on_a_quiet_cell_reaches_the_screen(self) -> None:
        # A는 모순(마감·광택)으로 골랐다. 색(RED, 사람이 확정)은 후보가 아니지만, 판독자가 확신 있게 BLUE라 하고 반론자도 동의하면 올라온다.
        item = next(item for item in self.fx.worklist()["items"] if item["key"] == "A")
        self.assertIn("color", [cell["field"] for cell in item["quiet"]])
        out = self.fx.sweep({"A": [reading("color", "BLUE"), reading("tones", "WARM")]},
                            {"A": [{"field": "color", "verdict": "READER_RIGHT", "why": "x", "evidenceImageIds": ["P01"]}]})
        self.fx.task("finish", "--from", str(out))
        review = json.loads((self.fx.review_dir() / "review.json").read_text(encoding="utf-8"))
        cells = {(c["field"]): c for i in review["items"] if i["key"] == "A" for c in i["cells"]}
        self.assertEqual(cells["color"]["signals"], ["BLIND_DISAGREES"])
        self.assertEqual(cells["color"]["proposal"], "BLUE")
        self.assertNotIn("tones", cells, "GT와 같은 판독은 후보가 아닌 칸을 올리지 않는다")

    def test_the_workflow_args_carry_quiet_fields_but_the_reader_view_does_not_rank_them(self) -> None:
        printed = json.loads(self.fx.task("prepare").stdout.split("\n", 1)[1])["workflowArgs"]
        entry = next(entry for entry in printed["items"] if "color" in entry["quietFields"])
        self.assertIn("color", printed["gt"][entry["id"]], "반론자에게 건넬 GT는 조용한 칸에도 있다")
        view = json.loads((PROJECT_ROOT / entry["view"]).read_text(encoding="utf-8"))
        self.assertNotIn("quiet", json.dumps(view), "판독 파일은 어느 칸이 후보인지 모른다")

    def _promote_color_on_a(self) -> None:
        out = self.fx.sweep({"A": [reading("color", "BLUE")]},
                            {"A": [{"field": "color", "verdict": "READER_RIGHT", "why": "x", "evidenceImageIds": ["P01"]}]})
        self.fx.task("finish", "--from", str(out))

    def test_a_held_blind_disagreement_comes_back_in_the_next_batch(self) -> None:
        self._promote_color_on_a()
        self.fx.record("--key", "A", "--field", "color", "--decision", "HOLD", "--reviewer", "민준")
        for key, field in (("A", "sheen"), ("A", "finish"), ("A", "tones")):
            self.fx.record("--key", key, "--field", field, "--decision", "CONFIRM", "--reviewer", "민준")
        self.fx.task("prepare")
        cells = {(i["key"], c["field"]): c for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertEqual(cells[("A", "color")]["signals"], ["BLIND_DISAGREES"], "보류한 승격 칸은 새 후보로 돌아온다")

    def test_a_skipped_blind_disagreement_is_offered_again(self) -> None:
        self._promote_color_on_a()
        for key, field in (("A", "sheen"), ("A", "finish"), ("A", "tones")):
            self.fx.record("--key", key, "--field", field, "--decision", "CONFIRM", "--reviewer", "민준")
        self.fx.task("prepare")  # «다음 거» — A의 색 칸은 답하지 않았다
        cells = {(i["key"], c["field"]) for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertIn(("A", "color"), cells, "넘긴 승격 칸도 후보로 남는다")
        self.fx.record("--key", "A", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        self.fx.task("prepare")
        cells = {(i["key"], c["field"]) for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertNotIn(("A", "color"), cells, "답하면 더 나오지 않는다")

    def test_a_value_that_contradicts_a_keep_is_refused(self) -> None:
        refused = self.fx.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--value", "RED", "--reviewer", "민준", check=False)
        self.assertIn("값을 붙이지 않습니다", refused.stderr)
        self.fx.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--value", "BLUE", "--reviewer", "민준")
        refused = self.fx.record("--key", "B", "--field", "color", "--decision", "HOLD", "--value", "BLUE", "--reviewer", "민준", check=False)
        self.assertIn("값을 붙이지 않습니다", refused.stderr)

    def test_after_every_answer_is_applied_prepare_says_all_answered(self) -> None:
        self.fx.only_first_field()
        self.fx.profile["gtTask"]["fields"][0].pop("fillMissing", None)
        self.fx.save()
        self.fx.task("prepare")
        for item in self.fx.worklist()["items"]:
            for cell in item["cells"]:
                if cell["current"] is None:
                    self.fx.record("--key", item["key"], "--field", "color", "--decision", "LEAVE_EMPTY", "--reviewer", "민준")
                else:
                    value = "BLUE" if cell["current"] == "GREEN" else "GREEN"
                    self.fx.record("--key", item["key"], "--field", "color", "--decision", "CORRECT", "--value", value, "--reviewer", "민준")
        self.fx.task("export")
        self.fx.task("apply", "--yes")
        note = json.loads(self.fx.task("prepare").stdout)["note"]
        self.assertIn("모두 답했습니다", note)

    def test_prepare_refuses_to_wipe_a_batch_still_being_read(self) -> None:
        views = sorted((self.fx.review_dir() / "reader").rglob("*.json"))
        refused = self.fx.cli("prepare", "--task", str(self.fx.profile_path), check=False)  # 화면이 아직 없다 — 판독이 도는 중
        self.assertIn("아직 읽는 중", refused.stderr)
        self.assertTrue(all(view.exists() for view in views), "도는 배치의 판독 파일을 지우지 않는다")
        self.fx.task("render")  # «GT 화면 다시 열어줘»가 탈출구다
        self.fx.cli("prepare", "--task", str(self.fx.profile_path))

    def test_a_render_after_a_newer_batch_writes_nothing_and_says_so(self) -> None:
        self.fx.task("render")
        manifest = self.fx.review_dir() / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["batchId"] = "newer-batch"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        before = (self.fx.review_dir() / "review.html").read_bytes()
        refused = self.fx.task("render", check=False)
        self.assertIn("새 배치", refused.stderr)
        self.assertEqual((self.fx.review_dir() / "review.html").read_bytes(), before)

    def test_a_keep_with_the_same_value_in_another_shape_is_accepted(self) -> None:
        # 값 여럿을 순서만 바꿔 말해도 같은 값이다 — 글자로 대 보면 거짓 거절이 난다.
        self.fx.record("--key", "F", "--field", "tones", "--decision", "CONFIRM", "--value", "WARM|COOL", "--reviewer", "민준")

    def test_a_definition_section_ends_at_any_next_heading(self) -> None:
        path = self.fx.root / "definitions.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n## 필드끼리의 제약\n무광이면 광택이 없어야 한다.\n", encoding="utf-8")
        from gt_task import definition_texts

        self.assertNotIn("무광이면", definition_texts(path)["tones"], "제목에 공백이 있는 절도 앞 필드 절의 경계다")

    def test_a_list_group_field_stops_with_a_sentence(self) -> None:
        self.fx.profile["gtTask"]["groupField"] = ["sku", "src"]
        self.fx.save()
        refused = self.fx.task("prepare", check=False)
        self.assertIn("멈춤:", refused.stderr)
        self.assertNotIn("Traceback", refused.stderr)

    def test_a_predictions_block_is_refused(self) -> None:
        self.fx.profile["gtTask"]["predictions"] = {"path": "x.jsonl", "keyField": "sku"}
        self.fx.save()
        self.assertIn("predictions는 더 쓰지 않습니다", self.fx.task("prepare", check=False).stderr)

    def test_allowed_values_come_only_from_the_policy(self) -> None:
        # 정책(정의 문서)의 `### 허용값` 목록이 화면의 «다른 값…»이다 — 순서까지 그대로.
        text = (self.fx.root / "definitions.md").read_text(encoding="utf-8")
        text = text.replace("- `PURPLE` 보라 — 설명\n", "").replace("- `RED` 빨강 — 설명", "- `PURPLE` 보라 — 설명\n- `RED` 빨강 — 설명")
        (self.fx.root / "definitions.md").write_text(text, encoding="utf-8")
        self.fx.task("prepare")
        self.fx.task("render")
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        cell = page.split('data-field="color"')[1].split('<div class="cell')[0]
        options = re.findall(r'data-label="([^"]+)"', cell)
        self.assertEqual(options, ["PURPLE", "RED", "BLUE", "GREEN"], "값 버튼은 정책의 순서 그대로다")

    def test_an_open_screen_redraws_its_choices_from_the_policy(self) -> None:
        self.fx.task("prepare")
        text = (self.fx.root / "definitions.md").read_text(encoding="utf-8")
        (self.fx.root / "definitions.md").write_text(text.replace("- `PURPLE` 보라 — 설명", "- `PURPLE` 보라 — 설명\n- `ORANGE` 주황 — 설명"),
                                                     encoding="utf-8")
        self.fx.task("render")  # 새 배치 없이 다시 그리기만
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        cell = page.split('data-field="color"')[1].split('<div class="cell')[0]
        self.assertRegex(cell, r'data-label="ORANGE">주황', "정책에 더한 값이 열린 화면의 값 버튼에 나온다")

    def test_a_profile_that_still_lists_values_is_refused(self) -> None:
        written = json.loads(self.fx.profile_path.read_text(encoding="utf-8"))
        written["gtTask"]["fields"][0]["labels"] = ["RED", "BLUE"]
        self.fx.profile_path.write_text(json.dumps(written, ensure_ascii=False), encoding="utf-8")
        self.assertIn("정책(정의 문서)이 정본", self.fx.task("prepare", check=False).stderr)

    def test_a_field_without_a_values_list_in_the_policy_is_refused(self) -> None:
        text = (self.fx.root / "definitions.md").read_text(encoding="utf-8")
        head, rest = text.split("## sheen", 1)
        rest = rest.split("## tones", 1)[1]
        (self.fx.root / "definitions.md").write_text(head + "## sheen\n광택.\n\n## tones" + rest, encoding="utf-8")
        self.assertIn("`### 허용값` 목록이 없습니다", self.fx.task("prepare", check=False).stderr)

    def _policy_block(self, field: str, block: str) -> None:
        """정의 문서의 한 필드 절을 통째로 바꿔 쓴다(허용값 목록 모양을 시험하려고)."""
        text = (self.fx.root / "definitions.md").read_text(encoding="utf-8")
        head, rest = text.split(f"## {field}\n", 1)
        tail = rest.split("\n## ", 1)
        (self.fx.root / "definitions.md").write_text(head + f"## {field}\n" + block + ("\n## " + tail[1] if len(tail) > 1 else ""),
                                                     encoding="utf-8")

    def test_a_malformed_policy_list_stops_instead_of_dropping_or_adding_values(self) -> None:
        cases = [
            ("### 허용값\n\n- `MATTE` 무광 — 설명\n- `GLOSSY` 유광 — 설명\n\n참고:\n- `finish`는 광택과 함께 본다.\n", "빈 줄 뒤에 다시"),
            ("### 허용값\n\n- `MATTE` 무광 — 설명\n* `GLOSSY` 유광 — 설명\n", "모양이 아닙니다"),
            ("### 허용값\n\n- `MATTE` 무광 — 설명\n- GLOSSY 유광 — 설명\n", "모양이 아닙니다"),
            ("### 허용값\n\n- `MATTE` 무광 - 설명\n- `GLOSSY` 유광 — 설명\n", "모양이 아닙니다"),
            ("### 허용값\n\n- `MATTE` 무광: 광이 없다 — 설명\n- `GLOSSY` 유광 — 설명\n", "이름이 이상합니다"),
            ("### 허용값\n\n- `MATTE` 무광 — 설명\n  - `GLOSSY` 유광 — 설명\n", "들여쓴 글머리표"),
            ("### 허용값\n\n- `MATTE` 무광 — 설명\n\n### 허용값\n\n- `GLOSSY` 유광 — 설명\n", "둘 있습니다"),
        ]
        for block, words in cases:
            self._policy_block("finish", "마감.\n\n" + block)
            self.assertIn(words, self.fx.task("prepare", check=False).stderr, block)

    def test_a_value_description_may_run_over_several_lines(self) -> None:
        self._policy_block("finish", "마감.\n\n### 허용값\n\n- `MATTE` 무광 — 빛을 되돌리지 않는다.\n  주름 사이도 마찬가지다.\n"
                                     "- `GLOSSY` 유광 — 빛이 난다.\n\n### 경계\n\n- `MATTE`와 `GLOSSY` 사이는 사진으로 가른다.\n")
        from gt_task import definition_values

        self.assertEqual(definition_values(self.fx.root / "definitions.md")["finish"], [("MATTE", "무광"), ("GLOSSY", "유광")],
                         "다른 ### 절의 글머리표는 값이 아니다")

    def test_a_private_key_in_the_profile_is_refused(self) -> None:
        written = json.loads(self.fx.profile_path.read_text(encoding="utf-8"))
        written["gtTask"]["fields"][0]["_valuesFrom"] = "definitions"
        self.fx.profile_path.write_text(json.dumps(written, ensure_ascii=False), encoding="utf-8")
        self.assertIn("적을 수 없는 키", self.fx.task("prepare", check=False).stderr)

    def test_a_decision_for_a_value_removed_from_the_policy_is_asked_again_and_not_handed_out(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "PURPLE", "--reviewer", "민준")
        text = (self.fx.root / "definitions.md").read_text(encoding="utf-8")
        (self.fx.root / "definitions.md").write_text(text.replace("- `PURPLE` 보라 — 설명\n", ""), encoding="utf-8")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual([row["value"] for row in summary["outOfPolicy"]], ["PURPLE"])
        lines = (self.fx.root / "gt/fixture-color-sheen/gt-review/corrections.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertNotIn("B", [json.loads(line)["id"] for line in lines if line.strip()], "정책에 없는 값은 넣을 목록에 싣지 않는다")
        self.fx.task("prepare")
        cells = {(i["key"], c["field"]): c for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertIn("POLICY_CHANGED_SINCE_DECISION", cells[("B", "color")]["signals"], "정책에서 뺀 값으로 고친 칸은 다시 묻는다")

    def test_renaming_a_code_through_legacy_keeps_earlier_decisions(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "PURPLE", "--reviewer", "민준")
        color = self.fx.profile["gtTask"]["fields"][0]
        color["labels"] = ["RED", "BLUE", "GREEN", "VIOLET"]
        color["labelNames"] = {"RED": "빨강", "BLUE": "파랑", "GREEN": "초록", "VIOLET": "보라"}
        color["legacy"] = {"CRIMSON": "RED", "PURPLE": "VIOLET"}
        self.fx.save()
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["outOfPolicy"], [], "옛 코드로 적힌 판정은 legacy로 새 코드가 된다 — 정책에서 빠진 값이 아니다")
        lines = (self.fx.root / "gt/fixture-color-sheen/gt-review/corrections.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["after"] for line in lines if json.loads(line)["id"] == "B"], ["VIOLET"])
        self.fx.task("prepare")
        cells = {(i["key"], c["field"]): c for i in self.fx.worklist()["items"] for c in i["cells"]}
        signals = (cells.get(("B", "color")) or {}).get("signals") or []
        self.assertFalse({"POLICY_CHANGED_SINCE_DECISION", "GT_CHANGED_SINCE_DECISION"} & set(signals),
                         "코드 이름을 바꿨다고 지난 답을 다시 묻지 않는다")

    def test_a_forbid_constraint_names_only_the_value_it_bans(self) -> None:
        constraint = self.fx.profile["gtTask"]["constraints"][0]
        del constraint["require"]
        constraint["forbid"] = {"sheen": ["HIGH"]}
        self.fx.save()
        self.fx.task("prepare")
        cells = {(i["key"], c["field"]): c for i in self.fx.worklist()["items"] for c in i["cells"]}
        self.assertIn("GT_SELF_CONTRADICTION", cells[("A", "sheen")]["signals"], "무광인데 광택이 강함 — 막은 값이다")
        # 정책에 값을 더해도 제약을 고칠 필요가 없다 — require로 나머지를 늘어놓았다면 새 값이 모두 모순이 된다.
        sheen = self.fx.profile["gtTask"]["fields"][2]
        sheen["labels"].insert(2, "MID")
        sheen["labelNames"]["MID"] = "중간"
        self.fx.save()
        self.fx.task("prepare")
        constraint["forbid"] = {"sheen": ["SHINY"]}
        self.fx.save()
        self.assertIn("허용값이 아닌", self.fx.task("prepare", check=False).stderr)
        del constraint["forbid"]
        self.fx.save()
        self.assertIn("require", self.fx.task("prepare", check=False).stderr, "막을 것도 요구할 것도 없는 제약은 멈춘다")

    def test_more_policy_shapes_that_would_be_read_wrong_stop(self) -> None:
        cases = [
            ("### 허용값\n\n  - `MATTE` 무광 — 설명\n- `GLOSSY` 유광 — 설명\n", "들여쓴 글머리표"),
            ("### 허용값\n\n- `MATTE` 무광 — 설명\n- `GLOSSY` 유광 — 설명\n\n참고.\n  - `SATIN` 반광 — 설명\n", "들여쓴 글머리표"),
            ("### 허용값\n\n1. `MATTE` 무광 — 설명\n2. `GLOSSY` 유광 — 설명\n", "모양이 아닙니다"),
            ("### 허용값 (마감)\n\n- `MATTE` 무광 — 설명\n- `GLOSSY` 유광 — 설명\n", "한 가지 모양만"),
            ("### 허용값\n\n- `MATTE` 무광 — 설명\n- `GLOSSY` 유광 — 설명\n뒤에 붙은 문장.\n", "들여쓰지 않은 줄"),
            ("### 허용값\n\n- `MATTE` 무*광 — 설명\n- `GLOSSY` 유광 — 설명\n", "이름이 이상합니다"),
        ]
        for block, words in cases:
            self._policy_block("finish", "마감.\n\n" + block)
            self.assertIn(words, self.fx.task("prepare", check=False).stderr, block)

    def test_a_field_section_written_twice_stops(self) -> None:
        text = (self.fx.root / "definitions.md").read_text(encoding="utf-8")
        (self.fx.root / "definitions.md").write_text(text + "\n## finish\n\n### 허용값\n\n- `SATIN` 반광 — 설명\n", encoding="utf-8")
        self.assertIn("같은 절이 둘", self.fx.task("prepare", check=False).stderr)

    def test_values_counts_what_moves_with_each_policy_value(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "PURPLE", "--reviewer", "민준")
        before = (self.fx.root / "gt/fixture-color-sheen/gt-review/decisions.json").read_bytes()
        report = json.loads(self.fx.task("values", "--field", "color").stdout)["fields"][0]
        by_code = {row["code"]: row for row in report["values"]}
        self.assertEqual([row["code"] for row in report["values"]], ["RED", "BLUE", "GREEN", "PURPLE"], "정책의 순서")
        self.assertEqual(by_code["GREEN"]["gtCells"], 2)
        self.assertEqual(by_code["PURPLE"]["correctedTo"], 1, "이 값으로 고친 판정 — 빼면 다시 묻게 된다")
        self.assertIn("legacy CRIMSON→RED", by_code["RED"]["usedInProfile"])
        self.assertEqual(report["notInPolicy"], {"PURPEL": 1})
        self.assertEqual((self.fx.root / "gt/fixture-color-sheen/gt-review/decisions.json").read_bytes(), before, "읽기만 한다")

    def test_every_signal_is_in_the_contract(self) -> None:
        from gt_task import SIGNALS

        contract = (SCRIPTS.parent / "contracts" / "gt-task.md").read_text(encoding="utf-8")
        self.assertEqual([name for name in SIGNALS if f"`{name}`" not in contract], [], "신호를 더하면 계약의 신호 표에도 적는다")

    def test_a_label_with_a_slash_is_refused(self) -> None:
        self.fx.profile["gtTask"]["fields"][0]["labels"].append("N/A")
        self.fx.profile["gtTask"]["fields"][0]["labelNames"]["N/A"] = "해당 없음"
        self.fx.save()
        self.assertIn("«/»", self.fx.task("prepare", check=False).stderr)

    def test_export_counts_this_screen_apart_from_the_total(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["thisBatch"], {"corrections": 1, "confirmations": 0})


        self.fx.profile["gtTask"]["limit"] = 1
        self.fx.save()
        self.fx.task("prepare")
        self.fx.task("render")
        first = self.fx.worklist()["items"][0]["key"]
        self.fx.task("prepare")  # 답 없이 «다음 거»
        self.assertNotEqual(self.fx.worklist()["items"][0]["key"], first, "넘어간 건은 뒤로 — 같은 건이 다시 맨 앞에 오지 않는다")
        self.assertTrue((self.fx.review_dir() / "shown.jsonl").is_file())

    def test_every_screen_opens_at_the_same_compact_density(self) -> None:
        # 누가 어느 브라우저로 열어도 같은 크기 — 화면마다 같은 비율 한 줄(--zoom)로 줄인다.
        self.fx.task("render")
        folder = self.fx.review_dir()
        home = (SCRIPTS.parent / "templates" / "gt-home.html").read_text(encoding="utf-8")
        for name, page in [("review.html", (folder / "review.html").read_text(encoding="utf-8")),
                           ("policy.html", (folder / "policy.html").read_text(encoding="utf-8")),
                           ("golden.html", (folder / "golden.html").read_text(encoding="utf-8")), ("gt-home.html", home)]:
            self.assertIn(":root{--zoom:.75}", page, name)
            self.assertIn("html{zoom:var(--zoom)}", page, name)

    def test_the_screen_shows_each_field_s_definition(self) -> None:
        self.fx.task("render")
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertIn("이 항목의 정책 보기", page)
        self.assertIn("광택.", page, "정의 문서의 그 필드 절이 실린다")

    def test_a_low_confidence_reading_can_be_picked_in_one_press(self) -> None:
        self.fx.task("finish", "--from", str(self.fx.sweep({"B": [reading("color", "RED", "LOW")]})))
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        cell = page.split('data-key="B" data-field="color"')[1].split('<div class="cell')[0]
        self.assertRegex(cell, r'class="chip is-ai" data-decision="CORRECT" data-value="RED"[^>]*>빨강<span class="vmark ai">AI 의견</span>',
                         "확신 낮은 판독 값도 그 값 버튼 하나로 고른다 — 칠하지 않고 «AI 의견» 글자만")

    def test_a_missing_defender_keeps_the_readings(self) -> None:
        out = self.fx.sweep({"B": [reading("color", "RED")]})
        value = json.loads(Path(out).read_text(encoding="utf-8"))
        value["result"]["needsRestart"] = {"agentType": "gt-defender", "role": "defender"}
        Path(out).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.fx.task("finish", "--from", str(out))
        status = json.loads(self.fx.task("status").stdout)
        self.assertGreaterEqual(status["notRead"], 1, "반론이 없는 칸은 «못 읽음»으로 남아 다시 읽는다")
        value["result"]["needsRestart"] = {"agentType": "gt-blind-reader", "role": "reader"}
        Path(out).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.assertIn("판독 에이전트", self.fx.task("finish", "--from", str(out), check=False).stderr)

    def test_a_true_false_field_needs_names_too(self) -> None:
        self.fx.profile["gtTask"]["fields"].append({"id": "lit", "name": "조명", "labels": ["true", "false"], "valueType": "boolean"})
        (self.fx.root / "definitions.md").write_text((self.fx.root / "definitions.md").read_text() + "\n## lit\n조명.\n")
        self.fx.save()
        self.assertIn("이름이 빠진", self.fx.task("prepare", check=False).stderr)

    def test_record_needs_what_the_person_saw(self) -> None:
        completed = self.fx.task("record", "--key", "A", "--field", "sheen", "--decision", "CONFIRM", "--reviewer", "민준", check=False)
        self.assertIn("--expect", completed.stderr)

    def test_a_long_original_the_production_decode_refuses_is_cut_by_the_harness_decode(self) -> None:
        from PIL import Image

        long_path = self.fx.root / "data/img/long.png"
        Image.open(long_path).save(self.fx.root / "data/img/long.webp")
        rows = [json.loads(line) for line in (self.fx.root / "data/images.jsonl").read_text(encoding="utf-8").splitlines()]
        for row in rows:
            for pic in row["pics"]:
                if pic["f"] == "img/long.png":
                    pic["f"] = "img/long.webp"
        write_jsonl(self.fx.root / "data/images.jsonl", rows)
        self.fx.task("prepare")
        tiles = [image for image in self.item("A")["images"] if image["role"] == "DETAIL_SOURCE"]
        self.assertEqual([tile["imageId"] for tile in tiles], ["D01T01", "D01T02", "D01T03"], "버리지 않고 같은 판으로 자른다")
        self.assertEqual(tiles[0]["tileRule"]["decoder"], "harness-pillow", "어느 디코드로 잘랐는지 남긴다")

    def item(self, key: str) -> dict:
        return next(item for item in self.fx.worklist()["items"] if item["key"] == key)

    def test_an_uncut_picture_the_cut_decode_refuses_is_still_shown(self) -> None:
        from PIL import Image

        Image.new("CMYK", (300, 300), (0, 0, 0, 0)).save(self.fx.root / "data/img/a.jpg")
        self.fx.task("prepare")
        item = next(item for item in self.fx.worklist()["items"] if item["key"] == "A")
        self.assertIn("A-1", [image["imageId"] for image in item["images"]], "썸네일은 자르지 않으니 버리지 않는다")

    def test_a_contradicted_cell_is_never_bundled_as_holding(self) -> None:
        from gt_review_render import cell_status

        read = {"value": "HIGH", "confidence": "HIGH"}
        self.assertEqual(cell_status("HIGH", read, None, ["LOW", "HIGH"], contradicted=True), "NEEDS_HUMAN_LOOK")
        self.assertEqual(cell_status("HIGH", {"value": "LOW", "confidence": "HIGH"}, None, ["LOW", "HIGH"],
                                     alternatives=["LOW"]), "GT_HOLDS", "대체 정답을 낸 판독은 GT와 같은 편이다")

    def test_clear_is_refused_where_the_upstream_cannot_take_a_blank(self) -> None:
        self.fx.profile["gtTask"]["gt"]["upstream"] = {"kind": "sheet", "note": "시트"}
        self.fx.save()
        refused = self.fx.record("--key", "B", "--field", "color", "--decision", "CLEAR", "--reviewer", "민준", check=False)
        self.assertIn("빈칸을 받지 못합니다", refused.stderr)

    def test_gaps_and_orphans_are_counted(self) -> None:
        self.fx.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준", "--gap")
        self.assertEqual(json.loads(self.fx.task("status").stdout)["definitionGaps"],
                         {"color": {"name": "색", "decided": 1, "held": 0}}, "사람에게 읽어 줄 이름과 함께, 보류는 따로")
        self.fx.record("--key", "E", "--field", "color", "--decision", "HOLD", "--reviewer", "민준", "--gap")
        self.assertEqual(json.loads(self.fx.task("status").stdout)["definitionGaps"]["color"]["held"], 1)
        write_jsonl(self.fx.root / "data/gt.jsonl", [row for row in GT_ROWS if row["sku"] != "B"])
        summary = json.loads(self.fx.task("export").stdout)
        self.assertEqual(summary["orphaned"][0]["key"], "B", "원장에는 있는데 GT에서 사라진 줄을 알린다")

    def test_a_replacement_agent_must_be_read_only(self) -> None:
        self.fx.profile["gtTask"]["agents"] = {"reader": "general-purpose"}
        self.fx.save()
        self.assertIn("general-purpose", self.fx.task("prepare", check=False).stderr)

    def test_jargon_before_a_particle_is_caught(self) -> None:
        from gt_review import korean_warnings

        value = {"items": [{"id": "GI-01", "reading": {"readings": [{"observation": "sheen은 낮다"}]}}]}
        self.assertEqual(korean_warnings(value, {"sheen"}), ["GI-01"])

    def test_the_reader_sees_every_field_in_declared_order(self) -> None:
        view = json.loads(sorted((self.fx.review_dir() / "reader").glob("*.json"))[0].read_text(encoding="utf-8"))
        self.assertEqual([field["id"] for field in view["fields"]], ["color", "finish", "sheen", "tones"])


class ServerTest(unittest.TestCase):
    """화면의 버튼이 지나는 문. JSON만, 로컬 출처만, 사람이 본 값과 함께."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fx = Fixture(Path(cls.tmp.name))
        cls.fx.task("prepare")
        # 서버는 프로필 찾기 뿌리를 환경 변수로 받는다 — 가짜 프로필을 진짜 속성 층에 쓰지 않는다.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.server = subprocess.Popen([sys.executable, str(SCRIPTS / "serve_reports.py"), "--port", str(cls.port)],
                                      env=cls.fx.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.tmp.cleanup()

    def raw(self, method: str, body: str, headers: dict) -> int:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(method, "/gt-decide", body, headers)
        return connection.getresponse().status

    def test_another_local_port_is_another_origin(self) -> None:
        status, answer = self.post({"task": "fixture-color-sheen", "key": "B", "field": "color", "reviewer": "민준",
                                    "decision": "HOLD", "expectedBefore": "BLUE"}, {"Origin": "http://localhost:3000"})
        self.assertEqual(status, 400)
        self.assertIn("다른 출처", answer["error"])

    def test_a_foreign_host_name_is_refused_even_for_reading(self) -> None:
        # DNS 리바인딩 — 남의 이름이 127.0.0.1로 풀리면 같은 출처로 원장을 읽는다. Host로 막는다.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/gt-decided?task=fixture-color-sheen", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(connection.getresponse().status, 403)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/gt-decided?task=fixture-color-sheen")
        self.assertEqual(connection.getresponse().status, 200)

    def test_other_request_shapes_are_refused(self) -> None:
        body = json.dumps({"task": "fixture-color-sheen", "key": "B", "field": "color", "reviewer": "민준",
                           "decision": "CONFIRM", "expectedBefore": "BLUE"})
        self.assertEqual(self.raw("POST", body, {"Content-Type": "text/plain"}), 400, "평범한 폼은 JSON을 못 보낸다")
        self.assertEqual(self.raw("POST", body, {"Content-Type": "application/json", "Origin": "null"}), 400)
        self.assertNotEqual(self.raw("OPTIONS", "", {"Origin": "http://evil.example"}), 200, "사전 요청을 허락하지 않는다")

    def test_the_page_learns_the_current_gt(self) -> None:
        self.fx.task("render")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/gt-decided?task=fixture-color-sheen")
        current = json.loads(connection.getresponse().read())["current"]
        self.assertEqual(current["B\u0000color"], "BLUE")

    def post(self, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("POST", "/gt-decide", json.dumps(body), {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"{}")

    def test_decide_then_read_back(self) -> None:
        base = {"task": "fixture-color-sheen", "key": "B", "field": "color", "reviewer": "민준",
                "batch": self.fx.worklist()["batchId"]}
        status, answer = self.post({**base, "decision": "CONFIRM"})
        self.assertEqual(status, 400, "사람이 본 GT 값 없이 오면 거절한다")
        status, answer = self.post({**base, "decision": "CONFIRM", "expectedBefore": "RED"})
        self.assertIn("화면이 보여 준 GT", answer["error"])
        status, answer = self.post({**base, "decision": "CONFIRM", "expectedBefore": "BLUE"}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 400)
        status, answer = self.post({**base, "decision": "CONFIRM", "expectedBefore": "BLUE"})
        self.assertTrue(answer["ok"], answer)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/gt-decided?task=fixture-color-sheen")
        latest = json.loads(connection.getresponse().read())["latest"]
        self.assertEqual(latest["B\u0000color"]["decision"], "CONFIRM")


    def test_the_window_between_prepare_and_the_new_screen(self) -> None:
        # 판독이 도는 동안: 새 배치는 알려져 있고(manifest preparing), 옛 탭의 클릭은 거절되고, 화면 주소는 «다시 요청하지 말라».
        self.fx.task("render")
        old_batch = self.fx.worklist()["batchId"]
        self.fx.task("prepare")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/gt-decided?task=fixture-color-sheen")
        answer = json.loads(connection.getresponse().read())
        self.assertTrue(answer["preparing"])
        self.assertNotEqual(answer["batchId"], old_batch)
        status, refused = self.post({"task": "fixture-color-sheen", "key": "B", "field": "color", "reviewer": "민준",
                                     "decision": "HOLD", "expectedBefore": "BLUE", "batch": old_batch})
        self.assertEqual(status, 400)
        self.assertIn("새 후보를 보는 중", refused["error"])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/gt/fixture-color-sheen")
        page = connection.getresponse()
        self.assertEqual(page.status, 503)
        self.assertEqual(page.getheader("Refresh"), "20", "기다리던 탭이 준비가 끝나면 스스로 화면이 된다")
        body = page.read().decode("utf-8")
        self.assertIn("새로 요청하지", body)
        self.assertIn("GT 화면 다시 열어줘", body, "10분이 지나도 그대로일 때의 길을 말한다")
        self.fx.task("render")

    def test_a_click_without_a_batch_is_refused(self) -> None:
        status, answer = self.post({"task": "fixture-color-sheen", "key": "B", "field": "color", "reviewer": "민준",
                                    "decision": "HOLD", "expectedBefore": "BLUE"})
        self.assertEqual(status, 400)
        self.assertIn("어느 화면", answer["error"])

    def test_an_old_tab_cannot_record(self) -> None:
        self.fx.task("render")
        status, answer = self.post({"task": "fixture-color-sheen", "key": "B", "field": "color", "reviewer": "민준",
                                    "decision": "HOLD", "expectedBefore": "BLUE", "batch": "old-batch"})
        self.assertEqual(status, 400)
        self.assertIn("새 화면이 준비됐습니다", answer["error"])

    def test_the_page_is_told_which_cells_are_answered_and_which_batch_is_current(self) -> None:
        # «이 화면의 답인가»는 status와 같은 함수로 서버가 정해 보낸다 — 화면 스크립트가 따로 셈하지 않는다.
        self.fx.task("render")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/gt-decided?task=fixture-color-sheen")
        answer = json.loads(connection.getresponse().read())
        self.assertIn("answered", answer)
        self.assertEqual(answer["batchId"], self.fx.worklist()["batchId"], "옛 탭이 새 배치를 알아보게")
        page = (self.fx.review_dir() / "review.html").read_text(encoding="utf-8")
        self.assertIn("answer.answered", page)
        self.assertIn("새 화면이 준비됐습니다", page)


class ApplyButtonTest(unittest.TestCase):
    """«반영하기» 버튼 한 번이 원본 GT를 바꾼다 — 목록만 만들고 «넣어줘»를 기다리지 않는다. GT를 바꾸므로 서버와 픽스처를 따로 둔다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))
        self.fx.task("prepare")
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
        self.tmp.cleanup()

    def test_one_press_puts_the_decisions_into_the_gt(self) -> None:
        self.fx.save()
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("POST", "/gt-export", json.dumps({"task": "fixture-color-sheen"}), {"Content-Type": "application/json"})
        answer = json.loads(connection.getresponse().read())
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["summary"]["applied"])
        self.assertEqual(answer["summary"]["linesChanged"], 1)
        gt = {row["sku"]: row for row in map(json.loads, (self.fx.root / "data/gt.jsonl").read_text(encoding="utf-8").splitlines())}
        self.assertEqual(gt["B"]["color"], "RED", "버튼 한 번에 원본이 바뀐다")
        self.assertTrue(list((self.fx.root / "data").glob("gt.jsonl.before-gt-review-*")), "넣기 전 원본은 사본으로 남는다")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("POST", "/gt-export", json.dumps({"task": "fixture-color-sheen"}), {"Content-Type": "application/json"})
        again = json.loads(connection.getresponse().read())
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["summary"]["applied"], "다시 눌러도 넣을 것이 없으면 아무것도 바꾸지 않는다")


@unittest.skipUnless(shutil.which("node"), "node가 없으면 워크플로우를 돌려 볼 수 없다")
class WorkflowParityTest(unittest.TestCase):
    """워크플로우가 반론을 부르는 칸 = 화면(merge·cell_status)이 «두 눈이 갈림»으로 볼 칸. 두 규칙이 같다는 것을 실제로 돌려 본다.
    워크플로우 스크립트를 그대로 node에서 돌리고, 판독자·반론자만 가짜로 둔다."""

    def run_workflow(self, args: dict, readings: dict) -> list[tuple[str, str]]:
        body = WORKFLOW.read_text(encoding="utf-8").replace("export const meta", "const meta", 1)
        harness = (
            f"const args = {json.dumps(args)};\n"
            f"const READINGS = {json.dumps(readings)};\n"
            "const defended = [];\n"
            "const log = () => {};\n"
            "async function agent(prompt, opts) {\n"
            "  if (opts.agentType === 'gt-blind-reader') {\n"
            "    const item = args.items.find((i) => prompt.includes(i.view));\n"
            "    return { readings: READINGS[item.id] };\n"
            "  }\n"
            "  const id = /건\\((GI-\\d+)\\)/.exec(prompt)[1];\n"
            "  for (const m of prompt.matchAll(/^- (\\w+): 지금 GT/gm)) defended.push([id, m[1]]);\n"
            "  return { rebuttals: [] };\n"
            "}\n"
            "async function pipeline(items, ...stages) {\n"
            "  const out = [];\n"
            "  for (let i = 0; i < items.length; i++) { let r = items[i];\n"
            "    for (const stage of stages) { r = await stage(r, items[i], i); } out.push(r); }\n"
            "  return out;\n"
            "}\n"
            "(async () => {\n" + body + "\n})().then(() => console.log(JSON.stringify(defended)));\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "run.mjs"
            script.write_text(harness, encoding="utf-8")
            completed = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return sorted(tuple(pair) for pair in json.loads(completed.stdout.strip().splitlines()[-1]))

    def test_the_workflow_and_the_screen_dispute_the_same_cells(self) -> None:
        from gt_review_render import merge

        fields = [{"id": "c", "labels": ["RED", "BLUE"]}, {"id": "t", "labels": ["WARM", "COOL"], "cardinality": "many"}]
        cases = {  # 건 → (GT, 판독들, 대체 정답)
            "GI-01": ({"c": "RED"}, [{"field": "c", "value": "BLUE", "confidence": "HIGH"}], {}),        # 갈림
            "GI-02": ({"c": "RED"}, [{"field": "c", "value": "BLUE", "confidence": "LOW"}], {}),         # 확신 낮음
            "GI-03": ({"c": "RED"}, [{"field": "c", "value": "PINK", "confidence": "HIGH"}], {}),        # 허용값 밖
            "GI-04": ({"c": "RED"}, [{"field": "c", "value": "BLUE", "confidence": "HIGH"}], {"c": ["BLUE"]}),  # 대체 정답
            "GI-05": ({"c": "RED"}, [{"field": "c", "value": "RED|BLUE", "confidence": "HIGH"}], {}),    # 값 하나 필드에 «|»
            "GI-06": ({"t": "WARM"}, [{"field": "t", "value": "WARM|", "confidence": "HIGH"}], {}),      # 빈 조각 = 같음
            "GI-07": ({"t": "WARM"}, [{"field": "t", "value": "COOL|WARM", "confidence": "HIGH"}], {}),  # 값 여럿 갈림
            "GI-08": ({"c": "RED"}, [{"field": "c", "value": "BLUE", "confidence": "HIGH"},
                                     {"field": "c", "value": "RED", "confidence": "HIGH"}], {}),          # 한 칸에 두 값
            "GI-09": ({"c": None}, [{"field": "c", "value": "BLUE", "confidence": "HIGH"}], {"c": ["BLUE"]}),  # 빈 GT + 대체 정답
            "GI-10": ({"t": "WARM"}, [{"field": "t", "value": "WARM|WARM", "confidence": "HIGH"}], {}),       # 겹친 조각 = 같음
            "GI-11": ({"c": "RED"}, [{"field": "c", "value": "BLUE", "confidence": "HIGH"},
                                     {"field": "c", "value": "BLUE", "confidence": "HIGH"}], {}),         # 같은 판독 두 번
        }
        args = {"task": "x", "batchId": "b", "definitions": "d.md", "worklist": "w.json",
                "items": [{"id": gid, "fields": list(gt), "fieldNames": {}, "view": f"view-{gid}.json",
                           "labels": {f["id"]: f["labels"] for f in fields},
                           "many": {f["id"]: f.get("cardinality") == "many" for f in fields},
                           "labelNames": {}} for gid, (gt, _, _) in cases.items()],
                "gt": {gid: gt for gid, (gt, _, _) in cases.items()},
                "alternatives": {gid: alts for gid, (_, _, alts) in cases.items()}}
        defended = self.run_workflow(args, {gid: readings for gid, (_, readings, _) in cases.items()})
        worklist = {"fields": fields, "items": [
            {"id": gid, "key": gid, "cells": [{"key": gid, "field": f, "current": v, "signals": ["GT_REFERENCE_ONLY"],
                                               "alternatives": alts.get(f, [])} for f, v in gt.items()]}
            for gid, (gt, _, alts) in cases.items()]}
        sweep = {"items": [{"id": gid, "reading": {"readings": readings}} for gid, (_, readings, _) in cases.items()]}
        contested = sorted((item["id"], cell["field"]) for item in merge(worklist, sweep, [])["items"]
                           for cell in item["cells"] if cell["status"] == "CONTESTED")
        self.assertEqual(defended, contested)
        self.assertEqual(defended, [("GI-01", "c"), ("GI-07", "t"), ("GI-09", "c"), ("GI-11", "c")])


class MergeTest(unittest.TestCase):
    def test_merge_counts_come_from_the_rows(self) -> None:
        worklist = {"fields": [{"id": "f", "labels": ["X", "Y"]}], "items": [
            {"id": "GI-01", "key": "k", "cells": [{"key": "k", "field": "f", "current": "X", "signals": ["GT_REFERENCE_ONLY"]}]}]}
        self.assertEqual(merge(worklist, None, [])["counts"]["byStatus"]["NOT_READ"], 1)


if __name__ == "__main__":
    unittest.main()


class SkillTextTest(unittest.TestCase):
    """스킬은 Claude가 읽는 절차라 코드가 대신 지키지 못한다 — 운영팀이 부딪치는 문장만 못 박는다."""

    skill = (PROJECT_ROOT / ".claude/os/engine/skills/gt-improve/SKILL.md").read_text(encoding="utf-8")

    def test_a_bare_request_asks_which_task_instead_of_saying_it_is_missing(self) -> None:
        self.assertIn("요청이 GT를 이름으로 가리키지 않으면", self.skill)
        self.assertIn("«아직 올라와 있지 않습니다»라고 말하지 않는다", self.skill)

    def test_the_step_four_message_names_the_unpainted_button(self) -> None:
        # 화면은 값마다 버튼 하나다 — 스킬이 화면에 없는 버튼 이름(«AI 제안대로 → …»)을 부르면 사람은 그 버튼을 찾는다.
        self.assertIn("«AI 제안» 표시", self.skill)
        self.assertNotIn("AI 제안대로 →", self.skill)

    def test_counts_come_from_status_not_the_screen_file(self) -> None:
        self.assertNotIn("화면과 `review.json`이 센다", self.skill)


class PublishTest(unittest.TestCase):
    """«올려줘» — 고친 GT와 원장만 담은 커밋을 새 브랜치로 민다. 지금 브랜치·작업 트리는 건드리지 않는다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(self.remote)], check=True)
        self.fx = Fixture(root / "work")
        git = lambda *a: subprocess.run(["git", "-C", str(self.fx.root), *a], check=True, capture_output=True, text=True)
        self.git = git
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        # GT·원장이 이 레포 안에 있다 — 원장 뿌리를 작업 폴더 안으로.
        self.fx.env["CATALOG_OS_GT_ROOT"] = str(self.fx.root / "gt")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        git("remote", "add", "origin", str(self.remote))
        git("push", "-q", "-u", "origin", "main")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_publish_pushes_only_the_task_files_on_a_new_branch(self) -> None:
        self.fx.only_first_field()
        self.fx.save()
        self.fx.task("prepare")
        self.fx.record("--key", "B", "--field", "color", "--decision", "CORRECT", "--value", "RED", "--reviewer", "민준")
        (self.fx.root / "unrelated.txt").write_text("개발자가 하던 일", encoding="utf-8")
        refused = self.fx.task("publish", "--yes", "--no-pr", check=False)
        self.assertIn("아직 넣지 않은 정정", refused.stderr, "PR의 GT와 원장이 다른 말을 하지 않게 — 넣기가 먼저다")
        self.fx.task("export")
        self.fx.task("apply", "--yes")
        preview = json.loads(self.fx.task("publish").stdout)
        self.assertFalse(preview["published"])
        self.assertIn("data/gt.jsonl", preview["files"])
        done = json.loads(self.fx.task("publish", "--yes", "--no-pr").stdout)
        self.assertTrue(done["published"])
        head_before = self.git("rev-parse", "HEAD").stdout
        pushed = subprocess.run(["git", "-C", str(self.remote), "show", "--name-only", "--format=", done["branch"]],
                                check=True, capture_output=True, text=True).stdout.split()
        self.assertIn("data/gt.jsonl", pushed)
        self.assertIn("gt/fixture-color-sheen/gt-review/decisions.json", pushed)
        self.assertNotIn("unrelated.txt", pushed, "과제 밖의 변경은 싣지 않는다")
        self.assertFalse(any(name.endswith("decisions.lock") or "before-gt-review" in name for name in pushed))
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, head_before, "지금 브랜치는 그대로다")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")
        self.assertTrue((self.fx.root / "unrelated.txt").exists())
        self.assertIn("definitions.md", pushed, "허용값을 정한 정책이 바뀌었으면 GT와 한 PR로 간다")
        self.assertIn("definitions.md", done["policyFiles"])

    def test_publish_leaves_out_policy_files_that_match_the_base(self) -> None:
        self.fx.only_first_field()
        self.fx.save()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "policy")
        self.git("push", "-q", "origin", "main")
        self.fx.record("--key", "B", "--field", "color", "--decision", "CONFIRM", "--reviewer", "민준")
        preview = json.loads(self.fx.task("publish").stdout)
        self.assertEqual(preview["policyFiles"], [], "기준 브랜치와 같은 정책은 싣지 않는다")
