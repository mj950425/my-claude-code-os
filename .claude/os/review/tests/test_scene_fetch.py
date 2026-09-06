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
from fetch_review_scenes import _tile_ranges, absolutize, save_tile  # noqa: E402


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
        ranges = _tile_ranges(750, 7026)
        self.assertEqual(len(ranges), 7)
        self.assertEqual(ranges[0], (0, 1125))
        self.assertEqual(ranges[5], (5625, 6750))
        self.assertEqual(ranges[-1][1], 7026)

    def test_a_short_image_is_one_tile(self) -> None:
        self.assertEqual(_tile_ranges(750, 900), [(0, 900)])

    def test_a_sliver_shorter_than_the_floor_is_not_a_tile(self) -> None:
        # 마지막 조각이 64px 미만이면 타일로 세지 않는다. 세면 번호가 하나씩 밀린다.
        ranges = _tile_ranges(100, 320)
        self.assertTrue(all(bottom - top >= 64 for top, bottom in ranges))


class FixtureRun:
    """`run-summary.json`이 선언한 것만 읽는 심사의 입력 모양을 그대로 만든다."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "queue").mkdir(parents=True)
        (root / "review").mkdir(parents=True)
        (root / "golden").mkdir(parents=True)

        (root / "golden" / "gallery.jsonl").write_text(
            json.dumps(
                {
                    "productKey": "X:1",
                    "details": [{"sceneId": "D01T01", "url": "asset/local-only.jpg"}],
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
                    "sceneNotes": {"D01T01": "남성 · 착용"},
                    "policyEvidenceSceneIds": ["D01T01"],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
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

    def test_the_reader_gets_one_tile_per_task_not_a_product(self) -> None:
        """상품 단위로 묶어 주면 앞 장면의 인상이 뒷 장면에 번진다."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = FixtureRun(Path(tmp) / "run")
            fixture.declared_everything()
            self.assertEqual(fixture.fetch().returncode, 0)

            view = json.loads(
                (fixture.root / "run-review" / "scenes" / "reader-view.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("tasks", view)
            self.assertNotIn("products", view, "과제가 상품 단위로 묶였다")
            for task in view["tasks"]:
                self.assertEqual(
                    set(task),
                    {"taskId", "productKey", "productName", "sceneId", "path"},
                    "판독 과제에 필요 이상이 실렸다",
                )


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


if __name__ == "__main__":
    unittest.main()
