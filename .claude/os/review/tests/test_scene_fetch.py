#!/usr/bin/env python3
"""장면 수집기가 지켜야 할 것.

이 수집기는 심사가 판독기 주장을 되짚을 재료를 만든다. 재료가 틀리면 재판독 전체가
틀린 자리를 본다. 그래서 세 가지를 기계가 지킨다 —
타일 경계가 산출물의 `DxxTyy`와 같은 조각을 가리키는가, 판독자에게 정답이 새지 않는가,
그리고 심사답게 `run-review/` 밖으로 쓰지 않는가.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude").is_dir():
            return parent
    raise RuntimeError("프로젝트 루트를 찾지 못했다")


PROJECT_ROOT = _find_project_root()
SCRIPT = PROJECT_ROOT / ".claude/os/review/scripts/fetch_review_scenes.py"

sys.path.insert(0, str(SCRIPT.parent))
import fetch_review_scenes  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from fetch_review_scenes import _tile_ranges, absolutize, save_tile, url_key  # noqa: E402


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TileBoundaryTest(unittest.TestCase):
    """`D01T06`이 어느 픽셀인지는 산출물이 말하지 않는다. 규칙이 갈라지면 다른 조각을 본다."""

    def test_long_detail_splits_the_way_the_scene_ids_say(self) -> None:
        # 실제 상세 원본 하나의 크기. 산출물은 이 원본에 D01T01~D01T07을 매단다.
        ranges = _tile_ranges(Image.new("RGB", (750, 7026), "white"))
        self.assertEqual(len(ranges), 7)
        self.assertEqual(ranges[0], (0, 1125))
        self.assertEqual(ranges[5], (5625, 6750))
        self.assertEqual(ranges[-1][1], 7026)

    def test_a_short_image_is_one_tile(self) -> None:
        self.assertEqual(_tile_ranges(Image.new("RGB", (750, 900), "white")), [(0, 900)])

    def test_a_sliver_shorter_than_the_floor_is_not_a_tile(self) -> None:
        # 마지막 조각이 64px 미만이면 타일로 세지 않는다. 세면 번호가 하나씩 밀린다.
        # 번호가 밀리는 회귀를 잡으려면 «모두 64 이상»이 아니라 정확한 자리를 못 박는다.
        self.assertEqual(_tile_ranges(Image.new("RGB", (100, 320), "white")), [(0, 150), (150, 300)])

    def test_a_source_under_the_quality_gate_gets_no_tiles(self) -> None:
        # 운영은 64px 미만 원본에 타일 번호를 매기지 않는다(ImageQualityGate). 여기서 자르면 운영에 없던 T01이 생긴다.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sliver.png"
            Image.new("RGB", (63, 900), "white").save(path)
            with self.assertRaises(OSError):
                fetch_review_scenes._open_rgb(path, "harness-pillow")

    def test_the_cut_follows_the_production_rule_not_a_fixed_height(self) -> None:
        """사진 사이 여백(700~760)이 명목 경계(600) 근처에 있으면 그 가운데서 자른다.
        고정 절단이었다면 T01이 600에서 끊겨 아래 사진의 머리가 T01로 넘어간다."""
        image = Image.new("RGB", (400, 1600), (40, 40, 40))
        draw = ImageDraw.Draw(image)
        draw.rectangle([150, 0, 250, 1599], fill=(230, 230, 230))
        draw.rectangle([0, 700, 399, 759], fill="white")
        self.assertEqual(_tile_ranges(image), [(0, 725), (725, 1325), (1325, 1600)])


class FixtureRun:
    """`run-summary.json`이 선언한 것만 읽는 심사의 입력 모양을 그대로 만든다."""

    SOURCE = "https://example.invalid/detail-1.jpg"

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "queue").mkdir(parents=True)
        (root / "review").mkdir(parents=True)
        (root / "golden").mkdir(parents=True)

        (root / "golden" / "gallery.jsonl").write_text(
            json.dumps(
                {
                    "productKey": "X:1",
                    # 이 번호를 매긴 판. 선언이 없으면 수집기는 자르지 않는다.
                    "tileRule": {"version": "v0-fixed", "decoder": "harness-pillow"},
                    "details": [
                        {"sceneId": "D01T01", "url": FixtureRun.SOURCE},
                        {"sceneId": "D01T02", "url": FixtureRun.SOURCE},
                        {"sceneId": "D01T03", "url": FixtureRun.SOURCE},
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "review" / "verdicts.jsonl").write_text(
            json.dumps(
                {
                    "productKey": "X:1",
                    "owner": "GOLDEN",
                    "ownerAction": "골든셋을 고친다",
                    "goldLabel": "FEMALE",
                    "observedLabel": "UNISEX",
                    "policyRule": "P3_MIXED_WEARER",
                    "policyStrength": "STRONG",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "queue" / "signal.jsonl").write_text(
            json.dumps(
                {
                    "productKey": "X:1",
                    "productName": "표본 가방",
                    "detailEvidence": "남성·여성 모델이 모두 확인됨.",
                    "sceneNotes": {"D01T01": "남성 · 착용", "D01T02": "여성 · 착용"},
                    "policyEvidenceSceneIds": ["D01T01", "D01T02"],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def seed_reference(self) -> None:
        """갤러리의 대표 사진을 run 폴더 안 로컬 경로로 둔다."""
        from PIL import Image as Pillow

        target = self.root / "asset" / "thumbnails" / "X-1.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        Pillow.new("RGB", (120, 120), "white").save(target)
        gallery = self.root / "golden" / "gallery.jsonl"
        row = json.loads(gallery.read_text(encoding="utf-8"))
        row["thumbnails"] = [{"url": "asset/thumbnails/X-1.jpg"}]
        gallery.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    def seed_source(self, width: int = 200, height: int = 900) -> None:
        """원본을 캐시에 미리 심어 네트워크 없이 장면이 만들어지게 한다.

        캐시 이름이 URL로만 결정되기 때문에 이렇게 심을 수 있다. 실행마다 바뀌는 이름이면
        여기서도 안 맞고, 실제 사용에서도 캐시가 한 번도 안 맞아 매번 다시 받는다.
        """
        from PIL import Image as Pillow

        cache = self.root / "run-review" / "scenes" / ".sources"
        cache.mkdir(parents=True, exist_ok=True)
        Pillow.new("RGB", (width, height), "white").save(
            cache / f"X-1-{url_key(self.SOURCE)}.jpg"
        )

    def summary(self, **artifacts: str) -> None:
        (self.root / "run-summary.json").write_text(
            json.dumps(
                {"generatedAt": "2026-01-01T00:00:00+00:00", "artifacts": artifacts},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def declared_everything(self) -> None:
        self.summary(
            gallery=str(self.root / "golden" / "gallery.jsonl"),
            arbiterVerdicts=str(self.root / "review" / "verdicts.jsonl"),
            queueDirectory=str(self.root / "queue"),
        )

    def fetch(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--run", str(self.root), *extra],
            capture_output=True,
            text=True,
        )


class ReaderBlindnessTest(unittest.TestCase):
    """판독자가 정답을 알고 사진을 보면 그 정답이 보인다. 지시가 아니라 입력으로 막는다."""

    def test_the_reader_view_carries_none_of_the_run_s_answers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = FixtureRun(Path(tmp) / "run")
            fixture.declared_everything()
            self.assertEqual(fixture.fetch().returncode, 0)

            scenes = fixture.root / "run-review" / "scenes"
            index = json.loads((scenes / "index.json").read_text(encoding="utf-8"))
            reader = (scenes / "reader-view.json").read_text(encoding="utf-8")

            # 심사자용 색인에는 실행의 주장이 들어 있어야 한다.
            self.assertEqual(
                index["products"][0]["judgeClaim"], "남성·여성 모델이 모두 확인됨."
            )
            # 판독자용 시야에는 그 어느 것도 없어야 한다.
            for leaked in ("judgeClaim", "judgeSceneNotes", "goldLabel", "observedLabel",
                           "owner", "policyRule", "남성", "여성", "UNISEX", "FEMALE"):
                self.assertNotIn(leaked, reader, f"판독자 시야에 «{leaked}»가 샜다")


class FanoutTest(unittest.TestCase):
    """비용을 정하는 것은 해상도가 아니라 판독자 수다. 묶는 단위가 곧 비용이다."""

    def _view(self, fixture: "FixtureRun", *extra: str) -> dict:
        self.assertEqual(fixture.fetch(*extra).returncode, 0)
        return json.loads(
            (fixture.root / "run-review" / "scenes" / "reader-view.json").read_text(
                encoding="utf-8"
            )
        )

    def test_the_default_gives_one_task_per_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = FixtureRun(Path(tmp) / "run")
            fixture.declared_everything()
            fixture.seed_source()
            view = self._view(fixture)

            self.assertEqual(view["fanout"], "product")
            self.assertEqual(len(view["tasks"]), 1, "상품 하나에 과제 하나여야 한다")
            task = view["tasks"][0]
            self.assertEqual(
                set(task), {"taskId", "productKey", "productName", "reference", "scenes"}
            )
            # 묶어도 장면은 낱개로 남는다. 판독자가 장면마다 따로 답할 수 있어야 한다.
            self.assertEqual(len(task["scenes"]), 2)
            for scene in task["scenes"]:
                self.assertEqual(set(scene), {"sceneId", "path"})

    def test_tile_fanout_splits_the_same_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = FixtureRun(Path(tmp) / "run")
            fixture.declared_everything()
            fixture.seed_source()
            view = self._view(fixture, "--fanout", "tile")

            self.assertEqual(view["fanout"], "tile")
            self.assertEqual(len(view["tasks"]), 2, "장면마다 과제 하나여야 한다")
            for task in view["tasks"]:
                self.assertEqual(
                    set(task),
                    {"taskId", "productKey", "productName", "reference", "sceneId", "path"},
                    "판독 과제에 필요 이상이 실렸다",
                )

    def test_the_reader_gets_the_reference_photo(self) -> None:
        """대상이 무엇인지는 상품명이 아니라 대표 판매 사진이 말한다."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = FixtureRun(Path(tmp) / "run")
            fixture.declared_everything()
            fixture.seed_source()
            fixture.seed_reference()
            task = self._view(fixture)["tasks"][0]
            self.assertTrue(task["reference"], "판독자에게 대표 사진이 안 갔다")
            self.assertTrue(
                (fixture.root / task["reference"]).is_file(), "대표 사진 파일이 없다"
            )

    def test_neither_fanout_leaks_the_run_s_answers(self) -> None:
        for extra in ((), ("--fanout", "tile")):
            with tempfile.TemporaryDirectory() as tmp:
                fixture = FixtureRun(Path(tmp) / "run")
                fixture.declared_everything()
                fixture.seed_source()
                raw = json.dumps(self._view(fixture, *extra), ensure_ascii=False)
                for leaked in ("judgeClaim", "goldLabel", "남성", "여성", "UNISEX"):
                    self.assertNotIn(leaked, raw, f"«{leaked}»가 판독자 시야에 샜다")


class TileCostTest(unittest.TestCase):
    """판독 비용은 픽셀 넓이를 따라간다. 쓰지도 않을 해상도를 매 타일마다 치르지 않는다."""

    def test_a_tile_is_narrowed_to_the_budget_and_keeps_its_shape(self) -> None:
        from PIL import Image as Pillow

        fetch_review_scenes.Image = Pillow
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tile.jpg"
            save_tile(Pillow.new("RGB", (1000, 1500), "white"), target, 640)
            saved = Pillow.open(target)
            self.assertEqual(saved.width, 640)
            self.assertEqual(saved.height, 960, "가로세로비가 틀어지면 다른 그림이 된다")

    def test_a_tile_already_within_budget_is_not_enlarged(self) -> None:
        from PIL import Image as Pillow

        fetch_review_scenes.Image = Pillow
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tile.jpg"
            save_tile(Pillow.new("RGB", (300, 400), "white"), target, 640)
            self.assertEqual(Pillow.open(target).width, 300)


class LedgerFallbackTest(unittest.TestCase):
    """원장은 산출물이 아니다. 보완한 자리는 보완했다고 남아야 한다."""

    def test_a_relative_url_borrows_the_host_from_a_sibling(self) -> None:
        self.assertEqual(
            absolutize("/item/a.jpg", ["https://cdn.example.com/item/b.jpg"]),
            "https://cdn.example.com/item/a.jpg",
        )

    def test_a_relative_url_with_no_sibling_stays_unresolved(self) -> None:
        # 호스트를 지어내면 조용히 다른 그림을 보게 된다. 못 푼 채로 두는 편이 낫다.
        self.assertIsNone(absolutize("/item/a.jpg", []))

    def test_an_absolute_url_passes_through(self) -> None:
        self.assertEqual(absolutize("https://x/y.jpg", []), "https://x/y.jpg")


class DeclaredOnlyTest(unittest.TestCase):
    """선언하지 않은 경로를 관습으로 추측하지 않는다. 건너뛴 것은 통과가 아니다."""

    def test_an_undeclared_gallery_is_recorded_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = FixtureRun(Path(tmp) / "run")
            fixture.summary(arbiterVerdicts=str(fixture.root / "review" / "verdicts.jsonl"))
            self.assertEqual(fixture.fetch().returncode, 0)

            index = json.loads(
                (fixture.root / "run-review" / "scenes" / "index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index["products"], [])
            self.assertIn("gallery", [item["artifact"] for item in index["skipped"]])


class WritesNothingElseTest(unittest.TestCase):
    """읽는 쪽이 원본을 고치면 다음 사람은 어느 값이 원본인지 알 수 없다."""

    def test_the_fetcher_touches_nothing_outside_run_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = FixtureRun(Path(tmp) / "run")
            fixture.declared_everything()
            before = snapshot(fixture.root)
            self.assertEqual(fixture.fetch().returncode, 0)
            after = snapshot(fixture.root)

            for path, content in before.items():
                self.assertIn(path, after, f"«{path}»가 사라졌다")
                self.assertEqual(after[path], content, f"«{path}»가 바뀌었다")
            for path in set(after) - set(before):
                self.assertTrue(
                    path.startswith("run-review/"), f"«{path}»가 run-review/ 밖에 생겼다"
                )



class MintingRuleTest(unittest.TestCase):
    """번호는 그 번호를 매긴 판으로 자른다. 판을 모르면 자르지 않는다."""

    def test_an_undeclared_rule_is_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = FixtureRun(Path(temporary) / "run")
            fixture.declared_everything()
            gallery = fixture.root / "golden" / "gallery.jsonl"
            row = json.loads(gallery.read_text(encoding="utf-8"))
            row.pop("tileRule")
            gallery.write_text(json.dumps(row) + "\n", encoding="utf-8")
            self.assertEqual(fixture.fetch().returncode, 0)
            index = json.loads((fixture.root / "run-review/scenes/index.json").read_text(encoding="utf-8"))
            reasons = [item["reason"] for item in index["skipped"] if item["artifact"] == "X:1"]
            self.assertTrue(any("tileRule" in reason for reason in reasons), reasons)
            self.assertEqual(index["products"], [])

    def test_a_carried_product_cut_by_another_rule_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index = Path(temporary) / "index.json"
            index.write_text(json.dumps({"basedOn": "t", "products": [
                {"productKey": "A:1", "tileRule": {"version": "v0-fixed", "decoder": "harness-pillow"}},
                {"productKey": "B:2", "tileRule": {"version": "v2-band-then-seam", "decoder": "harness-pillow"}},
                {"productKey": "C:3"},
                {"productKey": "D:4", "tileRule": {"version": "v0-fixed", "decoder": "java-imageio"}},
            ]}), encoding="utf-8")
            now = {"version": "v0-fixed", "decoder": "harness-pillow"}
            kept = fetch_review_scenes.carry_over(index, [], "t", {"A:1": now, "B:2": now, "C:3": now, "D:4": now})
            self.assertEqual([product["productKey"] for product in kept], ["A:1"], "판·디코더가 다르면 잇지 않는다")


class LedgerGuessTest(unittest.TestCase):
    """원장 사진은 번호를 매긴 실행의 원본 목록과 다른 출처다. D번째 원장 사진을 그 D로 믿고 자르지 않는다."""

    def run_with_ledger(self, ledger_detail: list[str]) -> dict:
        from PIL import Image as Pillow

        with tempfile.TemporaryDirectory() as tmp:
            fixture = FixtureRun(Path(tmp) / "run")
            fixture.declared_everything()
            fixture.seed_source()
            queue = fixture.root / "queue" / "signal.jsonl"
            row = json.loads(queue.read_text(encoding="utf-8"))
            row["policyEvidenceSceneIds"] = ["D01T01", "D02T01"]  # D02는 갤러리에 없다
            queue.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            fake = Path(tmp) / "fake_ledger.py"
            images = [{"type": "DETAIL", "image_url": url} for url in ledger_detail]
            fake.write_text(f"import json; print(json.dumps([{{'images': {images!r}}}]))\n", encoding="utf-8")
            cache = fixture.root / "run-review" / "scenes" / ".sources"
            Pillow.new("RGB", (200, 900), "white").save(cache / "X-1-D02T01.jpg")
            completed = fixture.fetch("--ledger", "--ledger-cmd", str(fake))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads((fixture.root / "run-review/scenes/index.json").read_text(encoding="utf-8"))

    def test_a_ledger_picture_is_kept_whole_and_marked_as_a_guess(self) -> None:
        index = self.run_with_ledger([FixtureRun.SOURCE.replace("detail-1", "a"), "https://example.invalid/ledger-2.jpg"])
        scene = next(scene for product in index["products"] for scene in product["scenes"] if scene["sceneId"] == "D02T01")
        self.assertTrue(scene["positionGuessed"])
        self.assertEqual(scene["crop"]["top"], 0)
        self.assertEqual(scene["crop"]["bottom"], 900, "추측한 자리는 T번호로 자르지 않고 통째로 둔다")

    def test_a_ledger_picture_equal_to_another_d_is_refused(self) -> None:
        index = self.run_with_ledger(["https://example.invalid/x.jpg", FixtureRun.SOURCE])
        self.assertFalse(any(scene["sceneId"] == "D02T01" for product in index["products"] for scene in product["scenes"]))
        self.assertTrue(any("자리가 어긋났다" in item["reason"] for item in index["skipped"]))


class RefusalReasonTest(unittest.TestCase):
    def test_a_policy_refusal_is_not_an_open_failure(self) -> None:
        """운영 디코드를 재현하지 못해 안 자른 파일과 손상·내려받기 실패는 다른 이유다 — 색인의 skipped 문구가 갈린다."""
        with tempfile.TemporaryDirectory() as temporary:
            webp = Path(temporary) / "a.webp"
            Image.new("RGB", (64, 200), "white").save(webp, "WEBP")
            with self.assertRaises(fetch_review_scenes.tile_rule.UnsupportedDecode):
                fetch_review_scenes._open_rgb(webp, "java-imageio")
            with self.assertRaises(OSError):
                fetch_review_scenes._open_rgb(Path(temporary) / "missing.jpg", "java-imageio")


class ZeroCoordinateTest(unittest.TestCase):
    def test_d00_and_t00_are_not_scene_ids(self) -> None:
        """운영 DetailTileId처럼 0번 자리는 없다. T00이 마지막 조각으로 새지 않게 풀지 않는다."""
        for scene in ("D01T00", "D00T01"):
            self.assertIsNone(fetch_review_scenes.SCENE_ID.match(scene), scene)
        self.assertIsNotNone(fetch_review_scenes.SCENE_ID.match("D01T01"))

    def test_a_one_digit_citation_is_refused_like_production(self) -> None:
        """운영 CITATION 문법은 두 자리 이상이다. 모델이 «D1T3»이라 인용하면 운영은 근거로 세지 않는다 — 여기서도 펴지 않는다."""
        self.assertTrue(fetch_review_scenes.refused_citation("D1T3"))
        self.assertFalse(fetch_review_scenes.refused_citation("D01T03"))
        self.assertFalse(fetch_review_scenes.refused_citation("TARGET_REFERENCE"), "좌표가 아닌 이름은 이 문법 밖이다")
        for zero in ("D00T01", "D01T00", "D0T0"):
            self.assertTrue(fetch_review_scenes.refused_citation(zero), f"0번 자리는 대표 사진으로 풀리지 않고 거절된다: {zero}")
        self.assertIsNone(fetch_review_scenes.SCENE_ID.match("D01T01\n"), "끝의 줄바꿈은 운영처럼 거절한다")
        self.assertEqual(fetch_review_scenes.canonical_scene("D1T3"), "D01T03", "서버가 매긴 이름(갤러리)은 편다")


class PartialRefetchTest(unittest.TestCase):
    """`--product`는 «그 상품만 다시 받는다»이지 «나머지를 잊는다»가 아니다.

    판독이 흐리다고 한 타일 하나를 크게 다시 받는 것이 이 스크립트의 사용법인데,
    그때 색인이 그 상품 하나로 덮이면 나머지 상품의 재판독이 조용히 증발한다.
    """

    def test_refetching_one_product_keeps_the_others_in_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "runs" / "demo"
            scenes = run / "run-review" / "scenes"
            scenes.mkdir(parents=True)
            (run / "run-summary.json").write_text(
                json.dumps({"generatedAt": "2026-01-01T00:00:00+00:00", "artifacts": {}}),
                encoding="utf-8",
            )
            previous = {
                "basedOn": "2026-01-01T00:00:00+00:00",
                "selector": {"maxWidth": 640},
                "products": [
                    {"productKey": "A:1", "maxWidth": 640, "scenes": [{"sceneId": "D01T01"}]},
                    {"productKey": "B:2", "maxWidth": 640, "scenes": [{"sceneId": "D01T01"}]},
                ],
                "skipped": [],
            }
            (scenes / "index.json").write_text(json.dumps(previous), encoding="utf-8")

            # 갤러리가 없으므로 수집은 아무 상품도 새로 만들지 못한다.
            # 그래도 지난 색인의 두 상품이 사라지면 안 된다.
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--run", str(run), "--product", "A:1"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            after = json.loads((scenes / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(product["productKey"] for product in after["products"]),
                ["A:1", "B:2"],
                "지정 수집이 다른 상품을 색인에서 지웠다",
            )

    def test_a_carried_product_keeps_the_width_it_was_fetched_at(self) -> None:
        """이어 붙인 상품에 이번 실행의 폭을 덮어쓰면, 어느 해상도의 판독인지 거짓말이 된다."""
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "runs" / "demo"
            scenes = run / "run-review" / "scenes"
            scenes.mkdir(parents=True)
            (run / "run-summary.json").write_text(
                json.dumps({"generatedAt": "2026-01-01T00:00:00+00:00", "artifacts": {}}),
                encoding="utf-8",
            )
            (scenes / "index.json").write_text(
                json.dumps(
                    {
                        "basedOn": "2026-01-01T00:00:00+00:00",
                        "selector": {"maxWidth": 640},
                        "products": [{"productKey": "B:2", "maxWidth": 640, "scenes": []}],
                        "skipped": [],
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(SCRIPT), "--run", str(run),
                 "--product", "A:1", "--max-width", "1400"],
                capture_output=True, text=True,
            )
            after = json.loads((scenes / "index.json").read_text(encoding="utf-8"))
            carried = next(p for p in after["products"] if p["productKey"] == "B:2")
            self.assertEqual(carried["maxWidth"], 640)
            self.assertEqual(after["selector"]["maxWidth"], 1400)

    def test_an_index_from_another_run_is_not_carried_over(self) -> None:
        """사이클이 다시 돌았으면 그 장면들은 옛 실행의 것이다. 섞으면 근거의 출처가 사라진다."""
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "runs" / "demo"
            scenes = run / "run-review" / "scenes"
            scenes.mkdir(parents=True)
            (run / "run-summary.json").write_text(
                json.dumps({"generatedAt": "2026-02-02T00:00:00+00:00", "artifacts": {}}),
                encoding="utf-8",
            )
            (scenes / "index.json").write_text(
                json.dumps(
                    {
                        "basedOn": "2026-01-01T00:00:00+00:00",
                        "products": [{"productKey": "B:2", "maxWidth": 640, "scenes": []}],
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(SCRIPT), "--run", str(run), "--product", "A:1"],
                capture_output=True, text=True,
            )
            after = json.loads((scenes / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(after["products"], [])



class AllScenesUnionTest(unittest.TestCase):
    """`--all-scenes`는 «전부»이지 «인용된 것 대신»이 아니다.

    갤러리의 상세 목록만 넣으면 실행이 인용한 이름(타일 좌표가 아닌 것)이 통째로 빠진다.
    하필 그 자리가 되짚어야 할 유일한 근거인 건이 있었다 — 인용 장면이 사라지면
    「확인했더니 아니었다」와 「아무도 안 봤다」가 같은 모양이 된다.
    """

    def test_a_cited_name_survives_all_scenes(self) -> None:
        from PIL import Image as PILImage

        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "runs" / "demo"
            for folder in ("golden", "queue", "review", "asset"):
                (run / folder).mkdir(parents=True, exist_ok=True)
            PILImage.new("RGB", (300, 450), "white").save(run / "asset" / "thumb.jpg")
            PILImage.new("RGB", (300, 450), "white").save(run / "asset" / "detail.jpg")
            (run / "golden" / "gallery.jsonl").write_text(
                json.dumps({
                    "productKey": "DEMO:1",
                    "tileRule": {"version": "v0-fixed", "decoder": "harness-pillow"},
                    "thumbnails": [{"url": "asset/thumb.jpg"}],
                    "details": [{"sceneId": "D01T01", "url": "asset/detail.jpg"}],
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (run / "review" / "verdicts.jsonl").write_text(
                json.dumps({"productKey": "DEMO:1", "owner": "GOLDEN"}) + "\n", encoding="utf-8"
            )
            # 실행이 타일 좌표가 아닌 이름을 근거로 인용했다.
            (run / "queue" / "signal.jsonl").write_text(
                json.dumps({"productKey": "DEMO:1",
                            "policyEvidenceSceneIds": ["TARGET_REFERENCE"]}) + "\n",
                encoding="utf-8",
            )
            (run / "run-summary.json").write_text(
                json.dumps({
                    "generatedAt": "2026-01-01T00:00:00+00:00",
                    "artifacts": {
                        "gallery": str(run / "golden" / "gallery.jsonl"),
                        "arbiterVerdicts": str(run / "review" / "verdicts.jsonl"),
                        "queueDirectory": str(run / "queue"),
                    },
                }),
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(SCRIPT), "--run", str(run), "--all-scenes"],
                capture_output=True, text=True,
            )
            index = json.loads((run / "run-review/scenes/index.json").read_text(encoding="utf-8"))
            product = index["products"][0]
            resolved = {scene["sceneId"] for scene in product["scenes"]}
            self.assertIn("TARGET_REFERENCE", resolved | set(product["unresolvedSceneIds"]),
                          "--all-scenes가 인용된 장면을 통째로 떨어뜨렸다")


class ReferencePickTest(unittest.TestCase):
    """대표 사진은 **대상의 정의**다. 여기가 작으면 「이 사람이 든 것이 파는 그 물건인가」가
    작은 그림 위에서 갈리고, 색·프린트처럼 한 축으로만 다른 대조가 거기서 무너진다.
    실제로 갤러리 첫 항목이 200px 목록용 축소본이고 뒤에 500px가 있던 상품이 있었다.
    """

    def _run_with_thumbnails(self, root: Path, sizes: list[tuple[int, int]]) -> dict:
        from PIL import Image as PILImage

        run = root / "runs" / "demo"
        (run / "golden").mkdir(parents=True, exist_ok=True)
        (run / "queue").mkdir(parents=True, exist_ok=True)
        (run / "review").mkdir(parents=True, exist_ok=True)
        thumbs = run / "thumbs"
        thumbs.mkdir(exist_ok=True)
        urls = []
        for index, size in enumerate(sizes):
            path = thumbs / f"t{index}.jpg"
            PILImage.new("RGB", size, "white").save(path)
            urls.append(f"thumbs/t{index}.jpg")

        detail = thumbs / "detail.jpg"
        PILImage.new("RGB", (300, 450), "white").save(detail)
        (run / "golden" / "gallery.jsonl").write_text(
            json.dumps(
                {
                    "productKey": "DEMO:1",
                    "tileRule": {"version": "v0-fixed", "decoder": "harness-pillow"},
                    "thumbnails": [{"url": url} for url in urls],
                    "details": [{"sceneId": "D01T01", "url": "detail.jpg"}],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (run / "review" / "verdicts.jsonl").write_text(
            json.dumps({"productKey": "DEMO:1", "owner": "GOLDEN"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run / "queue" / "signal.jsonl").write_text(
            json.dumps({"productKey": "DEMO:1"}, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (run / "run-summary.json").write_text(
            json.dumps(
                {
                    "generatedAt": "2026-01-01T00:00:00+00:00",
                    "artifacts": {
                        "gallery": str(run / "golden" / "gallery.jsonl"),
                        "arbiterVerdicts": str(run / "review" / "verdicts.jsonl"),
                        "queueDirectory": str(run / "queue"),
                    },
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [sys.executable, str(SCRIPT), "--run", str(run)], capture_output=True, text=True
        )
        return json.loads((run / "run-review/scenes/index.json").read_text(encoding="utf-8"))

    def test_the_biggest_thumbnail_wins_not_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index = self._run_with_thumbnails(Path(temporary), [(200, 240), (500, 600)])
            reference = index["products"][0]["reference"]
            self.assertEqual(reference["size"], [500, 600])

    def test_the_reference_size_is_recorded(self) -> None:
        """작은 대표 사진에서 나온 대조는 큰 것에서 뒤집힐 수 있다. 크기가 없으면 그걸 모른다."""
        with tempfile.TemporaryDirectory() as temporary:
            index = self._run_with_thumbnails(Path(temporary), [(200, 240)])
            self.assertEqual(index["products"][0]["reference"]["size"], [200, 240])


if __name__ == "__main__":
    unittest.main()
