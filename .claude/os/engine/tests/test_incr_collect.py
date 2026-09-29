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
        # 새 상품의 조각은 운영의 지금 규칙으로 — 골든셋 색인의 선언(harness-pillow)을 따르지 않는다
        self.assertEqual(result["tileRule"], {"version": "v2-band-then-seam", "decoder": "java-imageio"})
        self.assertTrue(Path(tiles[0]["f"]).is_file())
        # 넣으면 증분 검수가 그대로 받는다 — 사진이 이어진다
        pushed = incr_review.push(self.profile, out / "input.jsonl", out / "images.jsonl", name="collect")
        self.assertEqual((pushed["rows"], pushed["rowsWithPhotos"]), (2, 2))
        # 두 번째로 모으면 이미 넣은 건은 빠진다
        again = incr_collect.pick(self.rows(), self.profile["incremental"], incr_collect.known_keys(self.profile, incr_review.load_task(self.profile)), 10)
        self.assertEqual([r["key"] for r in again], ["MUSINSA:903"])

    def test_items_with_detail_photos_are_picked_first(self) -> None:
        rows = [{**row, "spid": 20 + n} for n, row in enumerate(self.rows()[:2])]
        details = {"20": "상세정보 참고", "21": f'<img src="file://{self.root}/cdn/detail.png">'}  # 앞 건은 설명이 글뿐
        result = incr_collect.build(self.profile, rows, self.root / "inbox2", limit=1, details=details)
        inputs = [json.loads(line) for line in (self.root / "inbox2" / "input.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["sku"] for r in inputs], ["EGOOCM:902"], "상세 사진이 있는 건이 먼저 — 글뿐인 건은 모자랄 때만")
        self.assertGreaterEqual(result["detailTiles"], 1)

    def test_detail_photos_follow_the_production_inventory(self) -> None:
        # 운영(`MongoGenderImageInventoryAdapter` + `GenderDetailInputPolicy.effectiveDetailUrls`)과 같은 목록
        doc = {"seller_product_id": 7, "platform_code": "EGOOCM", "images": [
            {"type": "THUMBNAIL", "image_url": "/item/1/a.jpg", "position": 0},
            {"type": "DETAIL", "image_url": "/item/1/c.jpg", "position": 2},
            {"type": "DETAIL", "image_url": "/item/1/b.jpg", "position": 1},
            {"type": "DETAIL", "image_url": "/item/1/a.jpg", "position": 0},   # 대표 썸네일과 같은 사진 — 뺀다
            {"type": "DETAIL", "image_url": "/item/1/gone.jpg", "position": 3, "dt": "2026-09-01"},  # 지운 사진 — 뺀다
            {"type": "DETAIL", "image_url": "/item/1/b.jpg", "position": 4},   # 겹친 주소 — 한 번만
            {"type": "DETAIL", "image_url": " ", "position": 5}]}
        self.assertEqual(incr_collect.effective_detail_urls(doc, "https://img.example"),
                         ["https://img.example/item/1/b.jpg", "https://img.example/item/1/c.jpg"])
        profile = {**self.profile, "incremental": {**self.profile["incremental"], "ledgerFallbackPlatforms": ["EGOOCM"]}}
        pipeline = incr_collect.mongo_pipeline(profile, self.rows(), limit=1, spare=3)
        self.assertIn('"seller_product_id": {"$in": [12]}', pipeline, "보완 원장은 보완 플랫폼만 읽는다")

    def test_detail_source_is_the_description_first_then_the_ledger(self) -> None:
        # 운영(`SellerProductDetailImageSourceAdapter`)과 같은 순서 — 설명의 사진이 먼저, 없으면 보완 플랫폼만 Mongo DETAIL
        self.fx.profile["incremental"]["ledgerFallbackPlatforms"] = ["EGOOCM"]
        self.fx.save()
        profile = self.fx.saved_profile()
        rows = [{**row, "spid": 30 + n} for n, row in enumerate(self.rows()[:2])]  # 901(MUSINSA)·902(EGOOCM)
        details = {"30": "상세정보 참고", "31": "상세정보 참고"}
        ledger = {"30": [f"file://{self.root}/cdn/detail.png"], "31": [f"file://{self.root}/cdn/detail.png"]}
        result = incr_collect.build(profile, rows, self.root / "inbox3", limit=2, details=details, detail_lists=ledger)
        self.assertEqual((result["itemsWithDetail"], result["detailSource"]), (1, "cuve-then-ledger"),
                         "설명에 사진이 없으면 보완 플랫폼(EGOOCM)만 원장으로 채운다 — MUSINSA 원장은 상세가 아니다")

    def test_thumbnails_follow_the_production_ledger(self) -> None:
        # 운영 `allThumbnails` + `UrlGeneratorUtil.getThumbnailImageUrl` — THUMBNAIL 전부, 지운 것 빼고, position 순, 첫 장이 대표
        doc = {"images": [
            {"type": "THUMBNAIL", "image_url": "/b.jpg", "position": 1},
            {"type": "THUMBNAIL", "image_url": "a.jpg", "position": 0},
            {"type": "THUMBNAIL", "image_url": "/gone.jpg", "position": 2, "dt": "2026-09-01"},
            {"type": "DETAIL", "image_url": "/d.jpg", "position": 0},
            {"type": "THUMBNAIL", "image_url": "//cdn.example/c.jpg", "position": 3}]}
        self.assertEqual(incr_collect.ledger_thumbnails(doc, "https://img.example/thumbnails"),
                         ["https://img.example/thumbnails/a.jpg", "https://img.example/thumbnails/b.jpg", "https://cdn.example/c.jpg"])
        self.assertIn("AND sp.platform_product_status IN ('ONSALE')",
                      incr_collect.sql({**self.profile, "incremental": {**self.profile["incremental"], "productStatuses": ["ONSALE"]}}))

    def test_every_ledger_thumbnail_is_collected_with_the_representative_first(self) -> None:
        self.fx.profile["gtTask"]["images"]["roles"] = ["THUMB", "THUMB_EXTRA", "DETAIL_TILE"]
        self.fx.profile["incremental"].update({"thumbnailSource": "ledger", "extraThumbnailRole": "THUMB_EXTRA"})
        self.fx.save()
        profile = self.fx.saved_profile()
        cdn = f"file://{self.root}/cdn"
        rows = [{**self.rows()[1], "spid": 40}]  # EGOOCM:902 — 설명 없음
        ledger = {"40": {"seller_product_id": 40, "platform_code": "EGOOCM", "images": [
            {"type": "THUMBNAIL", "image_url": f"{cdn}/thumb.jpg", "position": 0},
            {"type": "THUMBNAIL", "image_url": f"{cdn}/detail.png", "position": 1}]}}
        incr_collect.build(profile, rows, self.root / "inbox4", limit=1, details={"40": ""}, ledger=ledger)
        index = [json.loads(line) for line in (self.root / "inbox4" / "images.jsonl").read_text(encoding="utf-8").splitlines()]
        pics = index[0]["pics"]
        self.assertEqual([(p["pid"], p["kind"]) for p in pics[:2]], [("T01", "THUMB"), ("T02", "THUMB_EXTRA")],
                         "대표가 첫 장, 나머지 썸네일은 추가 썸네일로")

    def test_a_task_without_the_block_says_so(self) -> None:
        profile = {**self.profile}
        profile.pop("incremental")
        with self.assertRaises(incr_collect.IncrCollectError):
            incr_collect.sql(profile)


if __name__ == "__main__":
    unittest.main()
