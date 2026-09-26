#!/usr/bin/env python3
"""타일 규칙이 운영 코드와 같은 조각을 내는지 본다.

픽스처와 기대값은 core-catalog-platform의 두 테스트에서 그대로 가져왔다. Java 쪽의 사례는 전부 옮겼다.
- `core/src/test/java/.../BatchImageComposerTileRangesTest.java`
- `tool/image-gender/gt-harness/tests/test_collect_product_detail_images.py`

여기 숫자가 틀어지면 이 저장소가 고칠 것이 아니다. 운영 규칙이 바뀌었는지 먼저 본다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

import tile_rule  # noqa: E402
from tile_rule import V0_FIXED, V1_BAND, decode, rule_for_date, rule_named, scene_id, tile_ranges, usable  # noqa: E402


def stacked_photos(width: int, height: int, gaps: list[tuple[int, int]]) -> Image.Image:
    """사진 블록이 세로로 쌓이고 그 사이만 흰 여백인 상세컷. 사진 행은 가운데 밝은 띠가 있어 균일하지 않다."""
    image = Image.new("RGB", (width, height), (40, 40, 40))
    draw = ImageDraw.Draw(image)
    draw.rectangle([width * 3 // 8, 0, width * 5 // 8, height - 1], fill=(230, 230, 230))
    for top, bottom in gaps:
        draw.rectangle([0, top, width - 1, bottom - 1], fill="white")
    return image


def _gray_png(width: int, height: int, depth: int) -> bytes:
    """비트 깊이를 직접 적은 회색 PNG. Pillow는 4비트 회색으로 저장하지 않으므로 손으로 만든다."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes((width * depth + 7) // 8)
    header = struct.pack(">IIBBBBB", width, height, depth, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b"")


class TileRuleTest(unittest.TestCase):
    def test_odd_width_uses_java_half_up_rounding(self) -> None:
        image = Image.new("RGB", (401, 2000), "white")
        self.assertEqual(tile_ranges(image), [(0, 602), (602, 1204), (1204, 1806), (1806, 2000)])

    def test_cut_moves_to_the_background_band_near_the_nominal_cut(self) -> None:
        """사람이 경계에 잘려 상반신·하반신이 다른 타일로 갈리는 일을 이 규칙이 줄인다."""
        self.assertEqual(
            tile_ranges(stacked_photos(400, 1600, [(700, 760)])), [(0, 725), (725, 1325), (1325, 1600)]
        )

    def test_nominal_cut_inside_a_band_is_kept(self) -> None:
        self.assertEqual(
            tile_ranges(stacked_photos(400, 1600, [(560, 640), (1180, 1260)])),
            [(0, 600), (600, 1200), (1200, 1600)],
        )

    def test_band_outside_the_window_is_ignored(self) -> None:
        self.assertEqual(
            tile_ranges(stacked_photos(400, 1600, [(200, 260)])), [(0, 600), (600, 1200), (1200, 1600)]
        )

    def test_thin_band_is_not_a_band_but_is_a_photo_seam(self) -> None:
        self.assertEqual(
            tile_ranges(stacked_photos(400, 1600, [(700, 706)])), [(0, 700), (700, 1300), (1300, 1600)]
        )

    def test_widest_band_wins(self) -> None:
        self.assertEqual(tile_ranges(stacked_photos(400, 1600, [(480, 500), (700, 760)]))[0], (0, 725))

    def test_equal_bands_pick_the_one_closest_to_nominal(self) -> None:
        self.assertEqual(tile_ranges(stacked_photos(400, 1600, [(460, 480), (690, 710)]))[0], (0, 699))

    def test_abutting_photos_are_cut_at_the_seam(self) -> None:
        image = Image.new("RGB", (400, 1600), (40, 40, 40))
        draw = ImageDraw.Draw(image)
        for x in range(0, 400, 40):
            draw.rectangle([x, 0, x + 19, 699], fill=(90, 90, 90))
        draw.rectangle([0, 700, 399, 1599], fill=(200, 200, 200))
        for x in range(0, 400, 40):
            draw.rectangle([x, 700, x + 19, 1599], fill=(240, 240, 240))
        self.assertEqual(tile_ranges(image), [(0, 700), (700, 1300), (1300, 1600)])

    def test_person_across_the_nominal_cut_stays_in_one_tile(self) -> None:
        """Java personAcrossTheNominalCutStaysInOneTile — 사람 위쪽 여백에서 잘라 사람이 한 타일에 남는다."""
        image = Image.new("RGB", (400, 1600), (40, 40, 40))
        draw = ImageDraw.Draw(image)
        draw.rectangle([150, 500, 249, 899], fill=(230, 230, 230))
        draw.rectangle([0, 420, 399, 479], fill=(255, 255, 255))
        draw.rectangle([0, 920, 399, 979], fill=(255, 255, 255))
        draw.rectangle([0, 1100, 399, 1599], fill=(255, 255, 255))
        draw.rectangle([150, 1150, 249, 1549], fill=(230, 230, 230))
        ranges = tile_ranges(image)
        self.assertTrue(450 <= ranges[0][1] <= 499)
        self.assertLessEqual(ranges[1][0], 500)
        self.assertGreaterEqual(ranges[1][1], 900)

    def test_continuous_photo_without_seam_keeps_the_nominal_cut(self) -> None:
        """Java continuousPhotoWithoutSeamKeepsTheNominalCut."""
        image = Image.new("RGB", (400, 1600), (40, 40, 40))
        draw = ImageDraw.Draw(image)
        for x in range(0, 400, 40):
            draw.rectangle([x, 0, x + 19, 1599], fill=(90, 90, 90))
        self.assertEqual(tile_ranges(image)[0], (0, 600))

    def test_each_version_cuts_as_it_did_when_it_minted_ids(self) -> None:
        """같은 사진이라도 판마다 경계가 다르다. 번호를 매긴 판으로 잘라야 같은 조각이다."""
        banded = stacked_photos(400, 1600, [(700, 760)])
        self.assertEqual(tile_ranges(banded, V0_FIXED), [(0, 600), (600, 1200), (1200, 1600)])
        self.assertEqual(tile_ranges(banded, V1_BAND), [(0, 725), (725, 1325), (1325, 1600)])
        seam = stacked_photos(400, 1600, [(700, 706)])
        self.assertEqual(tile_ranges(seam, V1_BAND)[0], (0, 600), "v1에는 이음매 규칙이 없다")
        self.assertEqual(tile_ranges(seam)[0], (0, 700))

    def test_unknown_version_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            tile_ranges(Image.new("RGB", (400, 1600)), "v9")
        with self.assertRaises(ValueError):
            rule_named(None)
        self.assertEqual(rule_named({"version": V0_FIXED}), V0_FIXED)

    def test_version_by_date(self) -> None:
        self.assertEqual(rule_for_date("2026-08-31"), V0_FIXED)
        self.assertEqual(rule_for_date("2026-09-20"), tile_rule.CURRENT)
        with self.assertRaises(ValueError, msg="운영 산출물은 커밋일로 판을 정하지 않는다"):
            rule_for_date("2026-09-20", tile_rule.JAVA_IMAGEIO)
        with self.assertRaises(ValueError, msg="다른 조각 비율로 매긴 조각은 재현하지 않는다"):
            rule_named({"version": V0_FIXED, "decoder": tile_rule.HARNESS_PILLOW, "aspect": 1.2})
        for day in ("2026-08-01", "2026-08-22", "2026-09-14", "2026-09-15"):
            # 이식 전, 그리고 판이 바뀐 날(그날 안에서 바뀌어 날짜로 못 가른다)은 거절한다.
            with self.assertRaises(ValueError, msg=day):
                rule_for_date(day)

    def test_gray_source_is_read_as_java_reads_it(self) -> None:
        """Java ImageIO는 회색조를 선형으로 보고 sRGB로 옮긴다(128 → 188 근처)."""
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gray.png"
            Image.new("L", (64, 64), 128).save(path)
            self.assertIn(decode(path).getpixel((0, 0))[0], (187, 188))
            color = Path(folder) / "color.png"
            Image.new("RGB", (64, 64), (128, 128, 128)).save(color)
            self.assertEqual(decode(color).getpixel((0, 0)), (128, 128, 128))

    def test_the_harness_decoder_reads_like_the_harness(self) -> None:
        """하네스(Pillow)는 회색조를 그대로 두고 모든 형식의 EXIF를 돌린다. 하네스가 매긴 번호는 그렇게 읽어야 한다."""
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gray.png"
            Image.new("L", (64, 64), 128).save(path)
            self.assertEqual(decode(path, tile_rule.HARNESS_PILLOW).getpixel((0, 0)), (128, 128, 128))
            with self.assertRaises(ValueError):
                decode(path, "opencv")

    def test_a_jpeg_pillow_calls_mpo_is_still_rotated(self) -> None:
        """운영은 파일 머리(FF D8)로 JPEG를 안다. EXIF 회전이 붙은 JPEG는 형식 이름과 무관하게 돌린다."""
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rotated.jpg"
            exif = Image.Exif()
            exif[0x0112] = 6  # 시계 방향 90도로 돌려 보라는 표시
            Image.new("RGB", (100, 60), "white").save(path, exif=exif.tobytes())
            self.assertEqual(decode(path).size, (60, 100))
            png = Path(folder) / "rotated.png"
            Image.new("RGB", (100, 60), "white").save(png, exif=exif.tobytes())
            self.assertEqual(decode(png).size, (100, 60), "운영은 PNG의 EXIF를 읽지 않는다")

    def test_sixteen_bit_gray_keeps_its_shadows(self) -> None:
        """16비트 값 그대로 옮긴다. 8비트로 먼저 줄이면 그늘 0~13단계가 0으로 뭉개진다(실측: Java 0 2 4 6 8 …)."""
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gray16.png"
            ramp = Image.new("I;16", (8, 64))
            ramp.putdata([x * 40 for y in range(64) for x in range(8)])
            ramp.save(path)
            row = [decode(path).getpixel((x, 0))[0] for x in range(8)]
            self.assertEqual(row[:4], [0, 2, 4, 6], "Java가 내는 그늘 값")
            mid = Path(folder) / "mid.png"
            Image.new("I;16", (64, 64), 32896).save(mid)
            self.assertIn(decode(mid).getpixel((0, 0))[0], (187, 188))

    def test_a_non_srgb_jpeg_is_refused_not_guessed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            cmyk = Path(folder) / "cmyk.jpg"
            Image.new("CMYK", (64, 64), (0, 0, 0, 0)).save(cmyk)
            with self.assertRaises(tile_rule.UnsupportedDecode):
                decode(cmyk)
            self.assertEqual(decode(cmyk, tile_rule.HARNESS_PILLOW).size, (64, 64), "하네스는 그대로 읽는다")

    def test_decoder_is_declared_with_the_rule(self) -> None:
        self.assertEqual(tile_rule.rule(V0_FIXED, tile_rule.HARNESS_PILLOW)["decoder"], "harness-pillow")
        with self.assertRaises(ValueError, msg="디코더를 적지 않은 선언은 거절한다"):
            tile_rule.decoder_named({"version": V0_FIXED})
        with self.assertRaises(ValueError):
            tile_rule.decoder_named({"decoder": "x"})

    def test_unverified_formats_are_refused_by_the_production_decode(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            webp = Path(folder) / "a.webp"
            Image.new("RGB", (64, 64), "white").save(webp)
            with self.assertRaises(tile_rule.UnsupportedDecode):
                decode(webp)
            four = Path(folder) / "four.png"
            four.write_bytes(_gray_png(64, 64, depth=4))
            with self.assertRaises(tile_rule.UnsupportedDecode, msg="8비트 미만 회색 PNG는 운영과 맞춰 보지 않았다"):
                decode(four)
            # TIFF는 Pillow가 열면서 방향 태그대로 돌린다 — 운영(Java)은 돌리지 않아 크기부터 달라진다.
            tiff = Path(folder) / "turned.tif"
            exif = Image.Exif()
            exif[0x0112] = 6
            Image.new("RGB", (1500, 300), "white").save(tiff, exif=exif)
            with self.assertRaises(tile_rule.UnsupportedDecode):
                decode(tiff)

    def test_display_decode_keeps_what_the_cut_decode_refuses(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            cmyk = Path(folder) / "cmyk.jpg"
            Image.new("CMYK", (64, 64), (0, 0, 0, 0)).save(cmyk)
            shown = tile_rule.decode_for_display(cmyk)
            self.assertEqual((shown.mode, shown.size), ("RGB", (64, 64)))
            clear = Path(folder) / "clear.png"
            Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(clear)
            self.assertEqual(tile_rule.decode_for_display(clear).getpixel((0, 0)), (255, 255, 255), "투명은 흰 바탕")

    def test_common_imports_nothing_from_the_repo(self) -> None:
        """common은 어느 패키지도 모른다. 엔진과 심사가 같은 함수를 쓰되 서로를 모르게 하려는 자리다."""
        import ast

        allowed = {"__future__", "hashlib", "math", "pathlib", "re", "typing", "io", "PIL"}
        for source in Path(__file__).resolve().parents[1].glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                for name in names:
                    self.assertIn(name.split(".")[0], allowed, f"{source.name}: {name}")

    def test_short_image_is_one_tile(self) -> None:
        self.assertEqual(tile_ranges(Image.new("RGB", (400, 700), "white")), [(0, 700)])

    def test_separator_is_not_usable(self) -> None:
        self.assertFalse(usable(Image.new("RGB", (900, 1), "white")))
        self.assertTrue(usable(Image.new("RGB", (64, 64), "white")))

    def test_unmatched_jpeg_chroma_layouts_are_refused(self) -> None:
        """4:4:0(휘도 1×2)은 Java와 libjpeg-turbo가 색차를 다르게 키워 경계가 움직였다(실측). 맞춰 본 배치만 받는다."""
        import io
        import tempfile

        buffer = io.BytesIO()
        Image.new("RGB", (64, 200), "white").save(buffer, "JPEG", subsampling=0)
        data = bytearray(buffer.getvalue())
        sof = data.index(bytes([0xFF, 0xC0]))
        self.assertEqual(tile_rule.jpeg_sampling(bytes(data)), [(1, 1)] * 3)
        data[sof + 11] = 0x12  # 첫 성분(휘도)을 가로 1 · 세로 2로
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "440.jpg"
            path.write_bytes(bytes(data))
            with self.assertRaises(tile_rule.UnsupportedDecode):
                decode(path)
        for sub in (0, 1, 2):
            ok = io.BytesIO()
            Image.new("RGB", (64, 64), "white").save(ok, "JPEG", subsampling=sub)
            layout = tile_rule.jpeg_sampling(ok.getvalue())
            self.assertIn(layout[0], tile_rule.MATCHED_JPEG_SAMPLING)

    def test_only_the_matched_icc_profile_is_accepted_by_the_production_decode(self) -> None:
        """HP «sRGB IEC61966-2.1»(Java의 내장 sRGB 프로필 그 자체)은 Java가 변환 없이 읽어 비트 동일 — 받는다.
        LittleCMS가 만드는 «sRGB built-in»은 ±1 달랐다 — 받지 않는다. 프로필 바이트는 실제 상품 상세 JPEG에서 떴다."""
        import hashlib
        import tempfile

        from PIL import ImageCms

        built_in = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        hp = (Path(__file__).parent / "fixtures" / "hp-srgb-iec61966-2.1.icc").read_bytes()
        self.assertIn(hashlib.sha1(hp).hexdigest(), tile_rule.MATCHED_JPEG_ICC_SHA1)
        self.assertEqual(len(hp), 3144)
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / "built-in.jpg"
            Image.new("RGB", (64, 200), "white").save(other, "JPEG", icc_profile=built_in)
            with self.assertRaises(tile_rule.UnsupportedDecode, msg="맞춰 보지 않은 프로필은 받지 않는다"):
                decode(other)
            self.assertEqual(decode(other, tile_rule.HARNESS_PILLOW).size, (64, 200), "하네스 디코더는 ICC를 보지 않는다")
            matched = Path(folder) / "hp.jpg"
            plain = Path(folder) / "plain.jpg"
            Image.new("RGB", (64, 200), (120, 130, 140)).save(matched, "JPEG", icc_profile=hp, quality=95)
            Image.new("RGB", (64, 200), (120, 130, 140)).save(plain, "JPEG", quality=95)
            self.assertEqual(list(decode(matched).getdata()), list(decode(plain).getdata()), "맞춰 본 프로필은 평문과 같이 읽는다")

    def test_gif_bmp_and_huge_images_outside_what_was_matched_are_refused(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            full = Path(folder) / "full.gif"
            Image.new("P", (64, 200)).save(full)
            self.assertEqual(decode(full).size, (64, 200), "화면을 다 채운 GIF는 받는다")
            offset = Path(folder) / "offset.gif"
            data = bytearray(full.read_bytes())
            start = data.index(b"\x2c")  # 이미지 기술자 — 첫 프레임을 (0, 8)에서 시작하게 옮긴다
            data[start + 3:start + 5] = (8).to_bytes(2, "little")
            offset.write_bytes(bytes(data))
            with self.assertRaises(tile_rule.UnsupportedDecode):
                decode(offset)
            ok = Path(folder) / "ok.bmp"
            Image.new("RGB", (64, 64)).save(ok)
            self.assertEqual(decode(ok).size, (64, 64))
            sixteen = Path(folder) / "sixteen.bmp"
            raw = bytearray(ok.read_bytes())
            raw[28:30] = (16).to_bytes(2, "little")
            sixteen.write_bytes(bytes(raw))
            with self.assertRaises(tile_rule.UnsupportedDecode):
                decode(sixteen)
            limit = Image.MAX_IMAGE_PIXELS
            try:
                Image.MAX_IMAGE_PIXELS = 1000
                with self.assertRaises(tile_rule.UnsupportedDecode):
                    decode(ok)
            finally:
                Image.MAX_IMAGE_PIXELS = limit

    def test_max_piece_height_follows_the_declared_version(self) -> None:
        # 폭 1034: 명목 1551, v2 창 543 — 창 끝에서 자른 진짜 조각(2094)을 원본으로 오인하지 않는다.
        self.assertEqual(tile_rule.max_piece_height(1034, tile_rule.V2_BAND_THEN_SEAM), 1551 + 543)
        self.assertEqual(tile_rule.max_piece_height(1034, V0_FIXED), int(1551 * 1.25))

    def test_exif_orientation_reads_like_production(self) -> None:
        """운영 ExifOrientationTest의 픽스처 그대로 — 빅엔디언 TIFF 헤더에 방향 엔트리 하나. 형·개수가 다르면 운영은 돌리지 않는다."""
        import io

        buffer = io.BytesIO()
        Image.new("RGB", (40, 80), "white").save(buffer, "JPEG")
        jpeg = buffer.getvalue()

        def with_exif(orientation: int, kind: int = 3) -> bytes:
            exif = bytes([0xFF, 0xE1, 0, 34, *b"Exif", 0, 0, *b"MM", 0, 42, 0, 0, 0, 8, 0, 1,
                          1, 18, 0, kind, 0, 0, 0, 1, 0, orientation, 0, 0, 0, 0, 0, 0])
            return jpeg[:2] + exif + jpeg[2:]

        self.assertEqual(tile_rule.exif_orientation(with_exif(6)), 6)
        self.assertEqual(tile_rule.exif_orientation(with_exif(3)), 3)
        self.assertEqual(tile_rule.exif_orientation(jpeg), 1)
        self.assertEqual(tile_rule.exif_orientation(with_exif(9)), 1, "규격 밖은 1로 잠근다")
        self.assertEqual(tile_rule.exif_orientation(with_exif(0)), 1)
        self.assertEqual(tile_rule.exif_orientation(with_exif(6, kind=4)), 1, "LONG 형은 운영이 읽지 않는다")
        broken = bytes([0xFF, 0xD8, 0xFF, 0xE1, 0, 8, *b"Ex"]) + jpeg[2:]
        self.assertEqual(tile_rule.exif_orientation(broken), 1, "깨진 APP1은 예외가 아니라 1")

    def test_each_exif_orientation_moves_pixels_like_production(self) -> None:
        """운영 ExifOrientation.apply의 2~8 — 픽셀이 어디로 가는지를 EXIF 규격 식으로 직접 적고 맞춘다(Pillow 상수를 믿지 않는다)."""
        import io

        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, "JPEG")
        jpeg = buffer.getvalue()

        def with_exif(orientation: int) -> bytes:
            exif = bytes([0xFF, 0xE1, 0, 34, *b"Exif", 0, 0, *b"MM", 0, 42, 0, 0, 0, 8, 0, 1,
                          1, 18, 0, 3, 0, 0, 0, 1, 0, orientation, 0, 0, 0, 0, 0, 0])
            return jpeg[:2] + exif + jpeg[2:]

        width, height = 3, 2
        source = Image.new("RGB", (width, height))
        for x in range(width):
            for y in range(height):
                source.putpixel((x, y), (x * 80, y * 80, 7))
        # 출력 (x, y)가 원본의 어느 칸에서 오는가 — EXIF 2.3 표 그대로.
        where = {
            2: lambda x, y: (width - 1 - x, y),
            3: lambda x, y: (width - 1 - x, height - 1 - y),
            4: lambda x, y: (x, height - 1 - y),
            5: lambda x, y: (y, x),
            6: lambda x, y: (y, height - 1 - x),
            7: lambda x, y: (width - 1 - y, height - 1 - x),
            8: lambda x, y: (width - 1 - y, x),
        }
        for orientation, origin in where.items():
            result = tile_rule._exif_only_transpose(source, with_exif(orientation))
            swapped = orientation >= 5
            self.assertEqual(result.size, (height, width) if swapped else (width, height), orientation)
            for x in range(result.size[0]):
                for y in range(result.size[1]):
                    self.assertEqual(result.getpixel((x, y)), source.getpixel(origin(x, y)), (orientation, x, y))

    def test_piece_names_accept_only_what_production_accepts(self) -> None:
        self.assertEqual(tile_rule.parse_piece("D02T03"), (2, 3))
        self.assertEqual(tile_rule.parse_piece("12345-D2T3"), (2, 3), "평가 하네스 파일 이름")
        for bad in ("D０１T０３", "D01T03\n", "D00T01", "D01T00", "fooD01T02", "D²T1"):
            self.assertIsNone(tile_rule.parse_piece(bad), bad)

    def test_scene_id_matches_the_production_format(self) -> None:
        self.assertEqual(scene_id(1, 3), "D01T03")
        self.assertEqual(scene_id(12, 1), "D12T01")
        # 운영 DetailTileId는 0 이하를 거절한다. D00을 매기면 운영의 어느 이름과도 맞지 않는다.
        for bad in ((0, 1), (1, 0), (-1, 2)):
            with self.assertRaises(ValueError):
                scene_id(*bad)


if __name__ == "__main__":
    unittest.main()
