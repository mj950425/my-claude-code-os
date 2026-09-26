#!/usr/bin/env python3
"""정정 후보 시트가 계약을 지키는지 확인한다.

이 시트는 사람이 예·아니오를 주는 목록이라, 다른 보고서보다 두 가지가 더 위험하다.

1. 배지가 손으로 고른 확신도로 보이면 안 된다 — 심판의 귀책·근거 강도에서만 나와야 한다.
2. 건수를 문서에 적으면 안 된다 — 브라우저가 임베드된 데이터에서 센다 (프로젝트 규칙 8).

속성을 모른다. 소재 속성으로 돌려서 확인한다.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude").is_dir():
            return parent
    raise RuntimeError("프로젝트 루트를 찾지 못했습니다.")


PROJECT_ROOT = _find_project_root()
RENDERER = PROJECT_ROOT / ".claude/os/engine/scripts/render_catalog_report.py"

# 세 상품 · 세 귀책. 배지가 귀책에서만 나오는지 보려면 최소한 이만큼이 필요하다.
VERDICTS = [
    {"productKey": "T:1", "owner": "GOLDEN", "policyStrength": "STRONG", "policyAnswer": "COTTON",
     "policyRule": "R1", "goldLabel": "WOOL", "observedLabel": "COTTON", "ownerAction": "골든셋을 고친다",
     "reason": "라벨 택이 면을 지지한다.", "blockedBy": []},
    {"productKey": "T:2", "owner": "GOLDEN", "policyStrength": "WEAK", "policyAnswer": "WOOL",
     "policyRule": "R2", "goldLabel": "COTTON", "observedLabel": "WOOL", "ownerAction": "골든셋을 고친다",
     "reason": "약한 근거지만 정책을 그대로 적용하면 뒤집힌다.", "blockedBy": []},
    {"productKey": "T:3", "owner": "PENDING_PRECEDENT", "policyStrength": "WEAK", "policyAnswer": "WOOL",
     "policyRule": "R3", "goldLabel": "COTTON", "observedLabel": "WOOL", "ownerAction": "미결 판례가 답해야 한다",
     "reason": "판례가 먼저 답해야 한다.", "blockedBy": ["PM-0001"]},
    # 심판이 "답을 못 냈다"를 답 자리에 적었다. 이 값은 허용값이 아니라 제안이 될 수 없다.
    {"productKey": "T:5", "owner": "GOLDEN", "policyStrength": "WEAK", "policyAnswer": "UNRESOLVABLE",
     "policyRule": "NO_APPLICABLE_RULE", "goldLabel": "COTTON", "observedLabel": "WOOL",
     "ownerAction": "골든셋을 고친다", "reason": "정본 라벨이 없어 비교가 성립하지 않는다.", "blockedBy": []},
]
EXPECTED_GRADE = {"T:1": "SURE", "T:2": "POLICY", "T:3": "ASK", "T:4": "OPEN", "T:5": "POLICY"}


def unpack(report: Path) -> dict:
    """페이지에 실린 데이터를 브라우저와 같은 방식으로 편다."""
    body = report.read_text(encoding="utf-8")
    raw = re.search(r'<script id="audit-data" type="application/json">(.*?)</script>', body, re.S)
    assert raw, f"{report.name}: 임베드된 데이터가 없습니다"
    packed = json.loads(raw.group(1).replace("\\u003c", "<"))

    def thaw(value):
        if isinstance(value, list):
            return [thaw(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {"$"}:
                return packed["strings"][value["$"]]
            return {key: thaw(item) for key, item in value.items()}
        return value

    return thaw(packed["data"])


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_catalog_report", RENDERER)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(RENDERER.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


render = _load_renderer()


class FixSheetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.run = root / "run"
        (self.run / "queue").mkdir(parents=True)
        (self.run / "review").mkdir()
        (self.run / "reports").mkdir()

        rows = [
            {"signal": "MATERIAL_RATIO_GAP", "reason": "혼용률 기준이 없다.", "productKey": key,
             "productName": f"혼방 니트 {key}", "referenceLabel": verdict["goldLabel"],
             "observedLabel": verdict["observedLabel"], "standardCategory": "상의>니트"}
            for key, verdict in [(v["productKey"], v) for v in VERDICTS]
        ]
        # 심판이 보지 못한 상품. 배지는 미확정이어야 한다.
        rows.append({"signal": "MATERIAL_RATIO_GAP", "reason": "혼용률 기준이 없다.", "productKey": "T:4",
                     "productName": "혼방 니트 T:4", "referenceLabel": "COTTON", "observedLabel": "WOOL"})
        (self.run / "queue/material-ratio-gap.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        (self.run / "review/verdicts.jsonl").write_text(
            "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in VERDICTS), encoding="utf-8")
        (self.run / "run-summary.json").write_text(
            json.dumps({"completed": True, "products": 5, "artifacts": {}, "cycle": []}), encoding="utf-8")
        (self.run / "review/status.json").write_text(json.dumps({"pendingProducts": 5}), encoding="utf-8")
        (self.run / "manifest.json").write_text(
            json.dumps({"sourceDirty": False, "sourceCommit": "abc12345"}), encoding="utf-8")
        (self.run / "reports/policy-questions.json").write_text("[]", encoding="utf-8")

        self.profile = root / "material.json"
        self.profile.write_text(json.dumps({
            "schemaVersion": "catalog-data-profile-v1", "id": "product-material",
            "displayName": "상품 소재 감사", "attributeName": "대표 소재", "subjectName": "의류 상품",
            "outputRoot": str(self.run), "labels": ["COTTON", "WOOL", "UNKNOWN"],
            "signals": {"MATERIAL_RATIO_GAP": {"label": "혼용률 정책 공백",
                                               "description": "대표 소재 기준이 없습니다.", "priority": 1}},
        }, ensure_ascii=False), encoding="utf-8")

        subprocess.run([sys.executable, str(RENDERER), "--profile", str(self.profile)],
                       cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
        self.sheet = self.run / "reports/gt-fixes.html"

    def test_badge_comes_from_the_verdict_not_from_a_hand(self) -> None:
        data = unpack(self.sheet)
        grades = {row["productKey"]: row["fix"]["grade"] for row in data["rows"]}
        self.assertEqual(grades, EXPECTED_GRADE)

    def test_proposal_is_the_policy_answer_when_there_is_one(self) -> None:
        rows = {row["productKey"]: row["fix"] for row in unpack(self.sheet)["rows"]}
        self.assertEqual(rows["T:1"]["proposed"], "COTTON")
        self.assertTrue(rows["T:1"]["fromPolicy"])
        # 심판이 없으면 제안은 실행이 낸 값이고, 정책에서 온 것이 아니라고 표시된다.
        self.assertEqual(rows["T:4"]["proposed"], "WOOL")
        self.assertNotIn("fromPolicy", rows["T:4"])

    def test_a_policy_that_could_not_answer_never_becomes_a_proposal(self) -> None:
        """허용값이 아닌 심판 답이 제안 자리에 오면 아무도 적용할 수 없는 목록이 된다."""
        rows = {row["productKey"]: row["fix"] for row in unpack(self.sheet)["rows"]}
        labels = json.loads(self.profile.read_text(encoding="utf-8"))["labels"]
        self.assertTrue(all(row["proposed"] in labels for row in rows.values()))
        self.assertEqual(rows["T:5"]["proposed"], "WOOL")
        self.assertTrue(rows["T:5"]["policyStuck"])
        self.assertNotIn("fromPolicy", rows["T:5"])

    def test_no_counted_number_is_written_into_the_page(self) -> None:
        """건수는 문서에 적히지 않고 브라우저가 임베드된 데이터에서 센다 (프로젝트 규칙 8).

        어떤 요소가 그 숫자를 이고 있는지는 묻지 않는다 — 계기판을 걷어내고 필터 칩으로
        옮겨도 규칙은 그대로다. 묻는 것은 하나다: 마크업에 세어진 숫자가 박혀 있는가.
        """
        body = self.sheet.read_text(encoding="utf-8")
        markup = body.split('<script id="audit-data"')[0]
        self.assertEqual(re.findall(r"\d[\d,]*\s*(?:건|제안)(?![^<]*</dt>)", markup), [])
        # 건수를 이고 있는 자리는 전부 비어 있거나 0이어야 한다. JS가 채운다.
        for element in re.findall(r'<span[^>]*id="(?:report-count|shown)"[^>]*>([^<]*)</span>', markup):
            self.assertIn(element.strip(), ("", "0"), markup)
        # 필터 칩도 서버가 그리지 않는다.
        self.assertIn('id="grade-tabs"', markup)
        self.assertEqual(re.findall(r'id="grade-tabs"[^>]*>\s*<button', markup), [])

    def test_the_gt_lane_has_exactly_one_screen(self) -> None:
        """같은 것을 담는 두 화면은 고를 것이 없는데 고르게 만든다.

        전에는 「의심되는 GT 찾기」가 한 장 더 있었고, 이 자리의 시험은 **두 장이 같은
        상품을 보여야 한다**고 요구했다. 중복을 요구사항으로 못 박고 있었던 셈이다.
        필터가 글자 그대로 같았으니(`lanes:["GT","OPEN"]`) 늘 같은 11건이었는데,
        딱지는 「제안 단위」와 「건 단위」로 서로 다른 척했다.

        서버 홈에서 카드 메뉴를 없앤 이유가 정확히 그 겹침이었다. 보고서와 표지 링크가
        남아 있었으므로 그 수정은 절반만 된 것이었고, 여기서 나머지 절반을 못 박는다.
        """
        rendered = {path.name for path in (self.run / "reports").glob("*.html")}
        self.assertEqual(rendered, {"catalog-audit.html", "gt-fixes.html", "policy-gaps.html"})

        # 레인마다 목적지가 하나여야 한다. 둘이면 그 둘이 무엇이 다른지 사람이 매번 묻는다.
        specs = render.REPORTS
        by_lane: dict[tuple[str, ...], list[str]] = {}
        for key, spec in specs.items():
            by_lane.setdefault(tuple(spec["lanes"]), []).append(key)
        for lanes, keys in by_lane.items():
            self.assertEqual(len(keys), 1, f"같은 필터를 가진 화면이 둘이다: {lanes} → {keys}")

    def test_a_retired_screen_takes_its_declaration_with_it(self) -> None:
        """선언은 «이번 실행이 무엇을 냈는가»다. 지난 실행의 흔적이 남으면 심사가 없는 파일을 찾는다."""
        summary = json.loads((self.run / "run-summary.json").read_text(encoding="utf-8"))
        declared = summary.get("artifacts") or {}
        self.assertNotIn("suspectGtReport", declared)
        for key, value in declared.items():
            if key in render.OWNED_ARTIFACTS:
                self.assertTrue(Path(value).exists() or (PROJECT_ROOT / value).exists(), key)


if __name__ == "__main__":
    unittest.main()


class ReaderNotesAreNeverFoldedTest(unittest.TestCase):
    """판독기가 적어 둔 것은 인용 여부와 무관하게 보인다.

    이 시험은 실제 사고에서 나왔다. 실행이 「여성 모델만 대상 상품을 착용」이라 주장한
    상품에서, 판독기 자신이 다른 장면에 **「남성 · 대상 상품 아님」**을 적어 두었다.
    그 남성은 사진에서 대상 상품을 들고 있었고 — 즉 그 한 줄이 주장의 반증이었는데,
    인용하지 않았다는 이유로 접힌 채였다. 사람은 「인용 1장」만 보고 주장이 섰다고 읽었다.

    **인용하지 않은 장면의 기록이 주장과 어긋날 때가 가장 중요하다.** 그때 접으면 못 본다.
    """

    def row(self) -> dict:
        return {
            "productKey": "T:9",
            "evidence": {
                "sceneIds": ["D01T01"],
                "images": [],
                # 주장은 여성만인데, 인용하지 않은 장면에 남성 기록이 남아 있다.
                "notes": {"D01T01": "여성 · 착용", "D06T01": "남성 · 대상 상품 아님"},
            },
        }

    def gallery(self) -> dict:
        return {
            "T:9": {
                "thumbnails": [{"url": "thumb.jpg"}],
                "details": [
                    {"url": "a.jpg", "sceneId": "D01T01"},
                    {"url": "b.jpg", "sceneId": "D06T01"},
                ],
            }
        }

    def test_a_note_on_an_uncited_scene_survives_into_the_plate(self) -> None:
        plate = render.fix_plate(self.row(), self.gallery())
        found = {item["caption"]: item for item in plate if item["role"] == "DETAIL"}
        self.assertFalse(found["D06T01"]["cited"])
        self.assertEqual(found["D06T01"]["note"], "남성 · 대상 상품 아님")

    def test_the_sheet_keeps_noted_scenes_out_of_the_fold(self) -> None:
        """접는 규칙에 «기록이 있는 장면»이 빠지면 반증이 다시 숨는다."""
        source = RENDERER.read_text(encoding="utf-8")
        self.assertIn("x===firstTarget||x.cited||x.note", source)

    def test_the_note_is_drawn_even_when_the_scene_was_not_cited(self) -> None:
        source = RENDERER.read_text(encoding="utf-8")
        self.assertIn("const seen = item.note", source)


class TileCropFollowsTheMintingRuleTest(unittest.TestCase):
    """화면의 CSS 자르기는 고정 절단 판에서만 맞다. 다른 판이면 자르지 않고 원본을 통째로 보인다."""

    def plate(self, version: str | None) -> list[dict]:
        sys.path.insert(0, str(PROJECT_ROOT / ".claude/os/engine/scripts"))
        from render_catalog_report import fix_plate

        row = {"productKey": "K:1", "evidence": {"sceneIds": ["D01T02"]}}
        gallery = {"K:1": {"tileRule": {"version": version} if version else None, "details": [
            {"sceneId": "D01T01", "url": "https://x/1.jpg"}, {"sceneId": "D01T02", "url": "https://x/1.jpg"}]}}
        return [item for item in fix_plate(row, gallery) if item["role"] == "DETAIL"]

    def test_fixed_cut_is_cropped(self) -> None:
        self.assertEqual(sorted(item["tile"] for item in self.plate("v0-fixed")), [1, 2])

    def test_pixel_rules_and_undeclared_are_not_cropped(self) -> None:
        for version in ("v2-band-then-seam", "v1-background-band", None):
            self.assertTrue(all(item["tile"] == 0 for item in self.plate(version)), version)
