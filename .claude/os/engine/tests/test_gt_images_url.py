"""사진 파일이 없는 자리(서버)에서 색인의 주소로 받는다 — `gt_images` 머리말 4.

로컬 HTTP 서버로 돌린다. 지키는 것은 넷이다. 확인된 바이트(해시)만 쓴다. 해시가 없는 주소는 받지 않는다.
이미 잘린 조각의 주소는 자르기 전 원본이라 그 번호의 조각을 다시 잘라 꺼낸다. 로컬 파일이 있으면 주소를 부르지 않는다.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPTS = next(parent for parent in Path(__file__).resolve().parents if (parent / ".claude").is_dir()) / ".claude/os/engine/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[1] / "common"))

import gt_images  # noqa: E402
import tile_rule  # noqa: E402


def png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


class _Files(http.server.BaseHTTPRequestHandler):
    files: dict[str, bytes] = {}
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 — http.server의 이름
        type(self).hits.append(self.path)
        body = type(self).files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class UrlImageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _Files.files, _Files.hits = {}, []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Files)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        (self.root / "local").mkdir()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def serve(self, name: str, body: bytes) -> tuple[str, str]:
        _Files.files["/" + name] = body
        return f"{self.base}/{name}", hashlib.sha256(body).hexdigest()

    def prepare(self, pictures: list[dict], **spec: object) -> dict:
        index_path = self.root / "index.jsonl"
        import json

        index_path.write_text(json.dumps({"sku": "A", "pics": pictures}) + "\n", encoding="utf-8")
        images = {"path": str(index_path), "keyField": "sku", "listField": "pics", "fileField": "f",
                  "fileBase": str(self.root / "local"), "idField": "pid", "roleField": "kind",
                  "urlField": "url", "urlSha256Field": "sha", **spec}
        profile = {"id": "url-fixture", "outputRoot": str(self.root / "run"), "gtTask": {}}
        task = {"keyField": "sku", "fields": [{"id": "color"}], "images": images}
        index = gt_images.ImageIndex(profile, task)
        return gt_images.prepare(index, {"sku": "A"}, "A", self.root / "run/gt-review/images", self.root / "run", "b1")

    def test_a_missing_file_is_fetched_from_its_checked_address(self) -> None:
        url, sha = self.serve("t.png", png(100, 100, (200, 30, 30)))
        out = self.prepare([{"f": "gone.jpg", "pid": "T01", "kind": "THUMB", "url": url, "sha": sha}])
        self.assertEqual([image["imageId"] for image in out["images"]], ["T01"])
        self.assertEqual(out["missing"], [])
        cached = list((self.root / "run" / gt_images.URL_CACHE).iterdir())
        self.assertEqual(len(cached), 1, "받은 바이트는 run 폴더의 캐시에 한 벌")
        self.prepare([{"f": "gone.jpg", "pid": "T01", "kind": "THUMB", "url": url, "sha": sha}])
        self.assertEqual(len(_Files.hits), 1, "캐시가 해시와 맞으면 다시 받지 않는다")

    def test_bytes_that_are_not_the_checked_picture_are_not_shown(self) -> None:
        """판매자가 사진을 바꾸면 주소는 같아도 바이트가 다르다 — 다른 사진을 이 건의 증거로 보이지 않는다."""
        url, _ = self.serve("t.png", png(100, 100, (200, 30, 30)))
        out = self.prepare([{"f": "gone.jpg", "pid": "T01", "kind": "THUMB", "url": url, "sha": "0" * 64}])
        self.assertEqual(out["images"], [])
        self.assertIn("다릅니다", out["missing"][0]["reason"])

    def test_an_unchecked_address_is_not_fetched(self) -> None:
        """해시 열을 선언한 색인에서 해시가 없는 주소는 가져오기가 대 보지 못했거나 다른 사진이었던 주소다."""
        url, _ = self.serve("t.png", png(100, 100, (200, 30, 30)))
        out = self.prepare([{"f": "gone.jpg", "pid": "T01", "kind": "THUMB", "url": url}])
        self.assertEqual(out["images"], [])
        self.assertEqual(_Files.hits, [])

    def test_a_local_file_wins_and_the_address_is_not_called(self) -> None:
        (self.root / "local/t.png").write_bytes(png(100, 100, (10, 10, 200)))
        url, sha = self.serve("t.png", png(100, 100, (200, 30, 30)))
        out = self.prepare([{"f": "t.png", "pid": "T01", "kind": "THUMB", "url": url, "sha": sha}])
        self.assertEqual(len(out["images"]), 1)
        self.assertEqual(_Files.hits, [], "로컬 파일이 있으면(이 맥) 네트워크를 부르지 않는다")

    def test_a_piece_address_is_the_original_and_the_named_piece_is_cut_again(self) -> None:
        """이미 잘린 조각의 주소는 자르기 전 원본이다 — 선언된 판으로 다시 잘라 이름(D01T02)의 조각을 꺼낸다."""
        from PIL import Image

        original = png(100, 400, (60, 60, 60))
        url, sha = self.serve("detail.png", original)
        rule = {"version": "v0-fixed", "decoder": "harness-pillow"}
        out = self.prepare([{"f": "gone.jpg", "pid": "D01T02", "kind": "PIECE", "url": url, "sha": sha}],
                           preTiledRoles=["PIECE"], preTiledRule=rule)
        self.assertEqual([image["imageId"] for image in out["images"]], ["D01T02"])
        ranges = tile_rule.tile_ranges(Image.open(io.BytesIO(original)), "v0-fixed")
        self.assertGreater(len(ranges), 1)
        top, bottom = ranges[1]
        self.assertEqual((out["images"][0]["width"], out["images"][0]["height"]), (100, bottom - top))


if __name__ == "__main__":
    unittest.main()
