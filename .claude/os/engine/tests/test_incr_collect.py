"""증분 데이터 모으기(`incr_collect`) — 조회문 · 상세 사진 주소 · 사진 받기와 조각 · 입력 두 장의 모양.

네트워크 없이 돈다: 사진 주소는 `file://`, 조회 결과는 손으로 만든 JSON이다. 운영 DB에는 붙지 않는다(조회는 스킬의 몫).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_gt_review import SCRIPTS, Fixture  # noqa: E402

sys.path.insert(0, str(SCRIPTS))

import incr_collect  # noqa: E402
import incr_review  # noqa: E402

PREFIX = "잡화>"


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        from PIL import Image, ImageDraw

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        self.fx = Fixture(root)
        images = self.fx.profile["gtTask"]["images"]
        # 상품 성별 과제처럼 — 대표 사진과 이미 자른 조각(DETAIL_TILE)
        images.pop("tileRoles", None)
        images.update({"roles": ["THUMB", "DETAIL_TILE"], "preTiledRoles": ["DETAIL_TILE"],
                       "preTiledRule": {"version": "v2-band-then-seam", "decoder": "harness-pillow"}})
        self.fx.profile["incremental"] = {
            "schemaVersion": "incr-source-v1", "platforms": ["MUSINSA", "EGOOCM"], "categoryPrefix": PREFIX,
            "key": "{platform}:{goodsNo}", "hosts": {"MUSINSA": "file://" + str(root / "cdn"), "EGOOCM": "file://" + str(root / "cdn")},
            "pdp": {"MUSINSA": "https://m.example/{goodsNo}", "EGOOCM": "https://e.example/{goodsNo}"}, "detail": True, "maxDetailImages": 2,
        }
        self.fx.profile["gtTask"]["keyField"] = "sku"
        self.fx.save()
        self.env = mock.patch.dict(os.environ, {**{k: v for k, v in self.fx.env.items() if k.startswith("CATALOG_OS_")},
                                                "CATALOG_OS_INCR_ROOT": str(root / "incr")})
        self.env.start()
        self.profile = self.fx.saved_profile()
        cdn = root / "cdn"
        cdn.mkdir()
        Image.new("RGB", (300, 360), (200, 30, 30)).save(cdn / "thumb.jpg")
        tall = Image.new("RGB", (400, 1600), (40, 40, 40))
        ImageDraw.Draw(tall).rectangle([0, 700, 399, 759], fill="white")
        tall.save(cdn / "detail.png")

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def rows(self) -> list[dict]:
        detail = f'<p><img data-src="file://{self.root}/cdn/detail.png" src="nope.jpg"></p>'
        return [
            {"spid": 11, "platform": "MUSINSA", "goodsNo": "901", "name": "상품 하나", "image": "/thumb.jpg", "full_name": PREFIX + "모자>캡", "description": detail},
            {"spid": 12, "platform": "EGOOCM", "goodsNo": "902", "name": "상품 둘", "image": "/thumb.jpg", "full_name": PREFIX + "양말>기타", "description": ""},
            {"spid": 13, "platform": "MUSINSA", "goodsNo": "903", "name": "사진 없음", "image": "/missing.jpg", "full_name": PREFIX + "모자>캡"},
            {"spid": 14, "platform": "MUSINSA", "goodsNo": "904", "name": "M cafe24 구매금지 테스트", "image": "/thumb.jpg", "full_name": PREFIX + "모자>캡"},
        ]

    def test_the_queries_are_read_only_and_bounded(self) -> None:
        query = incr_collect.sql(self.profile, per_platform=7)
        self.assertTrue(query.startswith("SELECT"), "읽기 전용 도구는 SELECT로 시작하는 문만 받는다")
        self.assertIn("'MUSINSA'", query)
        self.assertIn("'EGOOCM'", query)
        self.assertIn(f"LIKE '{PREFIX}%'", query)
        self.assertIn("LIMIT 7", query)
        self.assertNotIn("description", query, "후보 조회에는 설명(HTML)을 붙이지 않는다 — 제한 시간을 넘는다")
        details = incr_collect.detail_sql(self.profile, self.rows(), limit=2)
        self.assertIn("cuve_mss_product_description", details)
        self.assertIn("'11'", details)
        self.assertNotIn("'13'", details, "고른 건만 — 순서대로 둘을 고르면 셋째는 빠진다")

    def test_detail_urls_follow_the_production_extractor(self) -> None:
        html = ('<img data-src="https://a.com/x y.jpg" src="https://a.com/no.jpg">'
                '<img srcset="//b.com/s.jpg 2x, //b.com/t.jpg 1x"><img src="/c.jpg"><img src="/c.jpg">')
        self.assertEqual(incr_collect.detail_urls(html, "https://h.net/thumbnails"),
                         ["https://a.com/x%20y.jpg", "https://b.com/s.jpg", "https://h.net/c.jpg"])

    def test_build_writes_the_task_shaped_inputs_and_pushes(self) -> None:
        out = self.root / "inbox"
        result = incr_collect.build(self.profile, self.rows(), out, limit=10)
        self.assertEqual((result["written"], len(result["failed"])), (2, 1), result["failed"])
        self.assertEqual([row["key"] for row in result["skippedNotForSale"]], ["MUSINSA:904"], "시험 등록 상품은 빼고 알린다")
        inputs = [json.loads(line) for line in (out / "input.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["sku"] for r in inputs], ["MUSINSA:901", "EGOOCM:902"])
        self.assertEqual(inputs[0]["pdpUrl"], "https://m.example/901")
        index = [json.loads(line) for line in (out / "images.jsonl").read_text(encoding="utf-8").splitlines()]
        pics = index[0]["pics"]
        self.assertEqual(pics[0]["kind"], "THUMB")
        tiles = [p for p in pics if p["kind"] == "DETAIL_TILE"]
        self.assertGreaterEqual(len(tiles), 2, "긴 상세 사진은 운영 규칙으로 여러 조각이 된다")
        self.assertEqual(tiles[0]["pid"], "D01T01")
        self.assertTrue(Path(tiles[0]["f"]).is_file())
        # 넣으면 증분 검수가 그대로 받는다 — 사진이 이어진다
        pushed = incr_review.push(self.profile, out / "input.jsonl", out / "images.jsonl", name="collect")
        self.assertEqual((pushed["rows"], pushed["rowsWithPhotos"]), (2, 2))
        # 두 번째로 모으면 이미 넣은 건은 빠진다
        again = incr_collect.pick(self.rows(), self.profile["incremental"], incr_collect.known_keys(self.profile, incr_review.load_task(self.profile)), 10)
        self.assertEqual([r["key"] for r in again], ["MUSINSA:903"])

    def test_a_task_without_the_block_says_so(self) -> None:
        profile = {**self.profile}
        profile.pop("incremental")
        with self.assertRaises(incr_collect.IncrCollectError):
            incr_collect.sql(profile)


if __name__ == "__main__":
    unittest.main()
