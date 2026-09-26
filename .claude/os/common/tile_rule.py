#!/usr/bin/env python3
"""긴 상세 이미지를 타일로 가르는 규칙. core-catalog-platform 운영 코드의 이식이다.

## 왜 직접 만들지 않고 옮기는가

타일 번호(`D01T03`)는 **그 번호를 매긴 실행이 본 조각**의 이름이다. 판독기가 「T03에 모델 모델」이라고
적으면, 그 문장을 되짚는 쪽도 **같은 픽셀 범위**를 잘라야 한다. 한 픽셀이라도 다르면 T03은 다른
사진이 되고, 되짚기는 엉뚱한 사진을 보고 동의하거나 반박한다. 그래서 이 파일은 설계하지 않는다. 옮긴다.

- 원본: `core/src/main/java/com/musinsa/ccp/core/promptflow/batch/render/BatchImageComposer.java`
- 같은 저장소의 파이썬 이식: `tool/image-gender/gt-harness/scripts/collect_product_detail_images.py`
- 테스트 픽스처와 기대값: `common/tests/test_tile_rule.py` (운영 Java·파이썬 테스트와 같은 숫자)

## 규칙에는 판이 있다 — 번호를 매긴 판으로 잘라야 한다

운영 규칙은 바뀌어 왔다. **최신 규칙이 아니라 그 번호를 매긴 규칙**으로 잘라야 같은 사진이 나온다.

| 판 | 운영에 들어온 커밋 | 경계 |
|---|---|---|
| `v0-fixed` | 906c7168 (2026-08-22) | 폭×1.5 고정 절단 |
| `v1-background-band` | 6486a107 (2026-09-14) | 명목 ±25% 안의 배경 띠 → 없으면 명목 |
| `v2-band-then-seam` | 344ca53b (2026-09-15) | 배경 띠 → 사진 이음매(±35%) → 명목 |

판을 모르면 자르지 않는다(`rule_named`가 거절한다). 추측으로 자른 조각은 다른 사진일 수 있고,
그 사진에 대한 판독은 원래 문장을 검증하지 못한다. 왜 띠·이음매가 들어왔는지(고정 절단이 사람을
두 타일로 가른 측정)는 위 커밋 메시지와 `BatchImageComposer`의 주석이 원본이다.

## 디코드도 번호를 매긴 쪽과 같게 — `decode(path, decoder)`

경계는 행의 밝기로 정하므로, 같은 파일을 **번호를 매긴 쪽과 같은 밝기로** 읽어야 한다. 번호를 매긴 구현은 둘이다.

| `decoder` | 누가 | 회색조 | EXIF 회전 |
|---|---|---|---|
| `java-imageio` | 운영 서버(`BatchImageComposer`) | 선형 회색 → sRGB(128 → 188 근처) | JPEG(마법 바이트 FF D8, MPO 포함)만 |
| `harness-pillow` | 평가 하네스(`collect_product_detail_images.save_tiles`) | 그대로 | 모든 형식 |

평가 하네스가 매긴 `DxxTyy`를 운영 서버 규칙으로 읽으면 오히려 다른 사진이 된다. 그래서 판(`version`)과
함께 구현(`decoder`)도 **반드시** 선언하고, 되짚는 쪽은 그 선언을 따른다. 디코더를 적지 않은 선언은 거절한다.

`java-imageio`가 흉내 내지 못하는 파일이 있다 — 맞춰 보지 않은 ICC 프로필을 품은 JPEG(HP sRGB IEC61966-2.1은 Java의 내장
프로필과 같아 비트 동일 — 받는다. LittleCMS built-in sRGB는 ±1 달라 받지 않는다)와 CMYK JPEG, 그리고 맞춰 보지 않은 색 표본 배치(4:4:0 같은). Java의 JPEG
판독기는 이를 sRGB로 색 변환한 뒤 밝기를 내는데, 그 변환을 픽셀 단위로 맞추지 못했다(실측에서 경계가 1px 움직였다).
그래서 그런 파일은 자르지 않고 거절한다 — 모르면 자르지 않는다. 하네스 디코더는 ICC를 보지 않으므로 그대로 읽는다.
같은 이유로 실측으로 맞춰 보지 않은 형식도 거절한다 — JPEG·PNG·GIF·BMP 밖의 형식(WebP·TIFF 같은 — TIFF는
Pillow가 열면서 방향 태그대로 돌려 버려 운영과 크기부터 달라진다), 8비트가 아닌 PNG(16비트 회색만 확인했다),
그리고 EXIF 없이 XMP로만 회전을 적은 JPEG(운영은 XMP를 읽지 않는다 — 여기서도 EXIF 회전만 쓴다).

**자르지 않을 사진은 이 규칙이 필요 없다.** 썸네일처럼 통째로 보여 주기만 하는 사진은 `decode_for_display`로 읽는다
(EXIF 회전, ICC → sRGB, 투명은 흰 바탕). 경계를 재현할 일이 없으니 거절할 이유도 없다.

## 지키는 것

- **숫자를 바꾸지 않는다.** 바꾸고 싶으면 운영 코드가 먼저 바뀌고, 여기는 새 판을 더한다.
- 반올림은 Java `Math.round`(양수 half-up). 파이썬 `round`는 .5에서 짝수로 가서 홀수 폭의 경계가 1px 달라진다.
- 64px보다 작은 조각은 버린다. 이어 붙이지 않는다.
- 패키지를 import하지 않는다. Pillow는 `decode`가 부를 때만 늦게 들여온다.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Callable

TILE_ASPECT = 1.5
MIN_TILE_PX = 64
SINGLE_TILE_SLACK = 1.25
BACKGROUND_SAMPLE_COLUMNS = 64
BACKGROUND_ROW_SPREAD_MAX = 12
BACKGROUND_BAND_MIN_ROWS = 8
BACKGROUND_SEARCH_WINDOW = 0.25
PHOTO_SEAM_SEARCH_WINDOW = 0.35
PHOTO_SEAM_MIN_MEAN_JUMP = 28
PHOTO_SEAM_MIN_COLUMN_JUMP = 10
PHOTO_SEAM_MIN_COLUMN_SHARE = 0.7

V0_FIXED = "v0-fixed"
V1_BAND = "v1-background-band"
V2_BAND_THEN_SEAM = "v2-band-then-seam"
CURRENT = V2_BAND_THEN_SEAM
# 판이 운영에 들어온 날. 산출물이 날짜만 알 때 판을 가르는 데 쓴다(`rule_for_date`).
SINCE = {V0_FIXED: "2026-08-22", V1_BAND: "2026-09-14", V2_BAND_THEN_SEAM: "2026-09-15"}


# Java ImageIO와 비트 동일하게 읽히는 것을 실측한 JPEG ICC 프로필(sha1). HP «sRGB IEC61966-2.1»(3144바이트)은 Java의 내장 sRGB
# 프로필 그 자체라 Java가 변환 없이 읽는다. 다른 프로필은 받지 않는다 — LittleCMS가 만드는 «sRGB built-in»조차 ±1 달랐다.
MATCHED_JPEG_ICC_SHA1 = frozenset({"9eaea0911d89d63e39e95f2e2116eaec7e0bb91e"})
JAVA_IMAGEIO = "java-imageio"
HARNESS_PILLOW = "harness-pillow"
DECODERS = (JAVA_IMAGEIO, HARNESS_PILLOW)
# 판이 바뀐 날. 이 날의 결과는 바뀌기 전인지 뒤인지 날짜만으로 가를 수 없다.
# v0 첫날(08-22)도 그날 안에서 반올림 방식이 바뀌었다(298ef19a → 906c7168). 그날보다 이른 날은 이미 거절한다.
AMBIGUOUS_DAYS = set(SINCE.values())


def rule(name: str, decoder: str = JAVA_IMAGEIO) -> dict[str, Any]:
    """산출물에 함께 적는 규칙의 이름표. 판·구현이 다른 조각끼리 섞이지 않게 한다."""
    if decoder not in DECODERS:
        raise ValueError(f"모르는 디코더입니다: {decoder}")
    return {"name": "ccp-batch-image-composer-tile-ranges", "version": name, "decoder": decoder,
            "since": SINCE[name], "aspect": TILE_ASPECT, "minTilePx": MIN_TILE_PX}


RULE = rule(CURRENT)


def rule_for_date(day: str, decoder: str = HARNESS_PILLOW) -> str:
    """`YYYY-MM-DD`에 **평가 하네스가** 쓰던 판. **대체 수단이다** — 결과를 만든 쪽이 판을 직접 적는 것이 먼저다.

    `SINCE`는 커밋 날짜다. 하네스는 그 코드를 그날 바로 돌리지만, 운영 서버(java-imageio)는 배포가 늦을 수
    있어 커밋 뒤 하루 이틀의 결과가 옛 판으로 잘렸을 수 있다. 그래서 운영 산출물은 날짜로 판을 정하지 않는다.
    그보다 이른 날, 그리고 판이 바뀐 날(그날 안에서 바뀌어 날짜로 가를 수 없다)은 거절한다.
    """
    if decoder != HARNESS_PILLOW:
        raise ValueError("운영 서버 산출물은 날짜로 타일 규칙 판을 정하지 않습니다(커밋일 ≠ 배포일). 판을 직접 선언하세요.")
    if day in AMBIGUOUS_DAYS:
        raise ValueError(f"{day}은 타일 규칙 판이 바뀐 날이라 날짜로 판을 정할 수 없습니다. 판을 직접 선언하세요.")
    known = [name for name, since in SINCE.items() if since <= day]
    if not known:
        raise ValueError(f"{day}에는 이식된 타일 규칙이 없습니다(가장 이른 판 {SINCE[V0_FIXED]}).")
    return max(known, key=lambda name: SINCE[name])


def half_up(value: float) -> int:
    """Java `Math.round`와 같은 양수 반올림."""
    return math.floor(value + 0.5)


def tile_height_for(width: int, aspect: float = TILE_ASPECT) -> int:
    return max(MIN_TILE_PX, half_up(width * aspect))


def row_luminances(image: Any) -> list[list[int]]:
    """행마다 64열 표본의 밝기. 배경 띠 판정과 사진 이음매 판정이 같은 표본을 본다."""
    width, height = image.size
    columns = min(BACKGROUND_SAMPLE_COLUMNS, width)
    xs = [0] if columns == 1 else [index * (width - 1) // (columns - 1) for index in range(columns)]
    pixels = image.load()
    result: list[list[int]] = []
    for y in range(height):
        row = []
        for x in xs:
            r, g, b = pixels[x, y][:3]
            row.append((299 * r + 587 * g + 114 * b) // 1000)
        result.append(row)
    return result


def background_rows(luminances: list[list[int]]) -> list[bool]:
    return [max(row) - min(row) <= BACKGROUND_ROW_SPREAD_MAX for row in luminances]


def photo_seam_cut(luminances: list[list[int]], nominal: int, top: int, height: int, window: int) -> int:
    """명목 ±window 안의 사진 이음매 가운데 가장 큰 것. 없으면 -1. 동률이면 먼저 나온 행."""
    low = max(top + MIN_TILE_PX, nominal - window)
    high = min(height - MIN_TILE_PX, nominal + window)
    best, best_jump = -1, 0
    for y in range(max(low, 1), high + 1):
        jumps = [abs(a - b) for a, b in zip(luminances[y - 1], luminances[y])]
        mean = sum(jumps) // len(jumps)
        changed = sum(1 for jump in jumps if jump >= PHOTO_SEAM_MIN_COLUMN_JUMP)
        if (
            mean >= PHOTO_SEAM_MIN_MEAN_JUMP
            and changed >= PHOTO_SEAM_MIN_COLUMN_SHARE * len(jumps)
            and mean > best_jump
        ):
            best, best_jump = y, mean
    return best


def background_cut(rows: list[bool], nominal: int, top: int, height: int, window: int) -> int:
    """명목 ±window 안의 가장 넓은 배경 띠 가운데. 명목이 이미 띠 안이면 명목. 없으면 -1."""
    low = max(top + MIN_TILE_PX, nominal - window)
    high = min(height - MIN_TILE_PX, nominal + window)
    if low > high:
        return -1
    if low <= nominal <= high and rows[nominal]:
        start = nominal
        while start - 1 >= low and rows[start - 1]:
            start -= 1
        end = nominal
        while end + 1 <= high and rows[end + 1]:
            end += 1
        if end - start + 1 >= BACKGROUND_BAND_MIN_ROWS:
            return nominal
    best: tuple[int, int, int] | None = None
    y = low
    while y <= high:
        if rows[y]:
            start = y
            while y + 1 <= high and rows[y + 1]:
                y += 1
            length = y - start + 1
            if length >= BACKGROUND_BAND_MIN_ROWS:
                center = (start + y) // 2
                candidate = (length, -abs(center - nominal), center)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate
        y += 1
    return best[2] if best else -1


def _walk(height: int, tile_height: int, choose: Callable[[int, int], int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    top = 0
    while top < height:
        nominal = top + tile_height
        bottom = height if nominal >= height else choose(nominal, top)
        if bottom - top >= MIN_TILE_PX:
            ranges.append((top, bottom))
        top = bottom
    return ranges


def tile_ranges(image: Any, version: str = CURRENT, aspect: float = TILE_ASPECT) -> list[tuple[int, int]]:
    """RGB 이미지 하나의 타일 경계 `[(top, bottom), ...]`. bottom은 포함하지 않는다.

    `version`은 그 타일 번호를 매긴 판이다. 모르는 판이면 멈춘다.
    """
    if version not in SINCE:
        raise ValueError(f"모르는 타일 규칙 판입니다: {version}. 알려진 판: {', '.join(SINCE)}")
    width, height = image.size
    if width < 1 or height < 1:
        raise ValueError("image dimensions must be positive")
    tile_height = tile_height_for(width, aspect)
    if height <= tile_height * SINGLE_TILE_SLACK:
        return [(0, height)]
    if version == V0_FIXED:
        return _walk(height, tile_height, lambda nominal, top: nominal)
    luminances = row_luminances(image)
    rows = background_rows(luminances)
    window = half_up(tile_height * BACKGROUND_SEARCH_WINDOW)
    seam_window = half_up(tile_height * PHOTO_SEAM_SEARCH_WINDOW)

    def choose(nominal: int, top: int) -> int:
        cut = background_cut(rows, nominal, top, height, window)
        if cut < 0 and version == V2_BAND_THEN_SEAM:
            cut = photo_seam_cut(luminances, nominal, top, height, seam_window)
        return cut if cut >= 0 else nominal

    return _walk(height, tile_height, choose)


def max_piece_height(width: int, version: str) -> int:
    """그 판으로 자른 조각이 가질 수 있는 가장 큰 높이(정수). 한 장짜리 원본(명목×1.25 이하)과, 경계를 명목에서
    창만큼 아래로 옮긴 조각(v1 배경 띠 창·v2 이음매 창) 가운데 큰 쪽. 이보다 높으면 조각이 아니라 자르지 않은 원본이다."""
    if version not in SINCE:
        raise ValueError(f"모르는 타일 규칙 판입니다: {version}")
    tile_height = tile_height_for(width)
    window = {V0_FIXED: 0, V1_BAND: half_up(tile_height * BACKGROUND_SEARCH_WINDOW),
              V2_BAND_THEN_SEAM: max(half_up(tile_height * BACKGROUND_SEARCH_WINDOW),
                                     half_up(tile_height * PHOTO_SEAM_SEARCH_WINDOW))}[version]
    return max(math.floor(tile_height * SINGLE_TILE_SLACK), tile_height + window)


def rule_named(value: Any) -> str:
    """산출물이 선언한 판 이름을 검사한다. 선언이 없거나 모르는 이름이면 멈춘다 — 추측해서 자르지 않는다."""
    name = value.get("version") if isinstance(value, dict) else value
    if name not in SINCE:
        raise ValueError(f"타일 규칙 판이 선언되지 않았거나 모르는 판입니다: {value!r}")
    # 하네스는 조각 비율(--tile-aspect)·최소 크기를 바꿔 돌릴 수 있다. 이식은 운영 값(1.5·64)만 재현한다 —
    # 다른 값으로 매긴 조각을 운영 값으로 다시 자르면 T03이 다른 사진이 된다.
    if isinstance(value, dict):
        if value.get("aspect") not in (None, TILE_ASPECT):
            raise ValueError(f"조각 비율 {value['aspect']}로 매긴 조각은 재현하지 않습니다(운영은 {TILE_ASPECT}).")
        if value.get("minTilePx") not in (None, MIN_TILE_PX):
            raise ValueError(f"최소 조각 {value['minTilePx']}px로 매긴 조각은 재현하지 않습니다(운영은 {MIN_TILE_PX}).")
    return str(name)


def decoder_named(value: Any) -> str:
    """산출물이 선언한 디코더. 선언이 없으면 거절한다 — 짐작한 디코더로 자른 조각은 다른 사진일 수 있다."""
    name = value.get("decoder") if isinstance(value, dict) else None
    if name not in DECODERS:
        raise ValueError(f"디코더가 선언되지 않았거나 모르는 디코더입니다: {name!r}. 알려진 것: {', '.join(DECODERS)}")
    return str(name)


class UnsupportedDecode(ValueError):
    """운영 디코드를 재현하지 못하는 파일. 자르지 않는다."""


def _srgb_from_linear_gray() -> list[int]:
    table = []
    for level in range(256):
        linear = level / 255
        encoded = 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055
        table.append(min(255, max(0, half_up(encoded * 255))))
    return table


SRGB_FROM_LINEAR_GRAY = _srgb_from_linear_gray()


def _srgb_from_linear_gray_16() -> list[int]:
    table = []
    for level in range(65536):
        linear = level / 65535
        encoded = 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055
        table.append(min(255, max(0, half_up(encoded * 255))))
    return table


SRGB_FROM_LINEAR_GRAY_16 = _srgb_from_linear_gray_16()


def decode(path: Path | str, decoder: str = JAVA_IMAGEIO) -> Any:
    """번호를 매긴 구현이 읽은 것처럼 읽는다. 표는 모듈 머리말에 있다."""
    from PIL import Image, ImageOps

    if decoder not in DECODERS:
        raise ValueError(f"모르는 디코더입니다: {decoder}")
    try:
        opened_file = Image.open(path)
    except Image.DecompressionBombError as error:
        # 운영(Java)은 픽셀 수 한도가 없어 자른다. 여기서는 열지 않는다 — 조각 번호가 같은지 확인하지 못한다.
        raise UnsupportedDecode(f"{Path(path).name}: 너무 큰 사진이라 열지 않았습니다(운영은 잘랐을 수 있다).") from error
    with opened_file as opened:
        if decoder == HARNESS_PILLOW:
            return ImageOps.exif_transpose(opened).convert("RGB")
        # 운영은 형식 이름이 아니라 파일 머리(FF D8)로 JPEG를 안다. Pillow는 APP2(MPF)가 있는 JPEG를
        # «MPO»라 부르지만 운영에게는 그냥 JPEG다.
        with open(path, "rb") as handle:
            data = handle.read()
        head = data[:32]
        is_jpeg = head[:2] == b"\xff\xd8"
        if opened.format == "WEBP":
            raise UnsupportedDecode(f"{Path(path).name}: WebP는 운영 디코드와 맞춰 보지 않았습니다. 자르지 않습니다.")
        if opened.format == "GIF" and opened.tile and tuple(opened.tile[0][1]) != (0, 0, *opened.size):
            # Java ImageIO는 첫 프레임의 상자만 돌려주고 Pillow는 논리 화면 전체를 준다 — 크기부터 달라 조각이 바뀐다.
            raise UnsupportedDecode(f"{Path(path).name}: 첫 프레임이 화면 전체가 아닌 GIF는 운영 디코드와 크기가 다릅니다. 자르지 않습니다.")
        if opened.format == "BMP" and not _bmp_matched(data):
            raise UnsupportedDecode(f"{Path(path).name}: 24·32비트(무압축)나 8비트 팔레트가 아닌 BMP는 운영 디코드와 맞춰 보지 않았습니다.")
        if not is_jpeg and opened.format not in ("PNG", "GIF", "BMP"):
            # 남길 것 목록 방식. TIFF는 Pillow가 열면서 방향 태그대로 돌려, 운영(Java)과 크기·타일 수부터 다르다.
            raise UnsupportedDecode(f"{Path(path).name}: {opened.format} 형식은 운영 디코드와 맞춰 보지 않았습니다. 자르지 않습니다.")
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            depth, color = head[24], head[25]
            # 팔레트 PNG(색 유형 3)는 팔레트 값이 곧 sRGB라 Java와 같다 — 비트 깊이와 상관없이 받는다.
            if depth != 8 and color != 3 and not (depth == 16 and color == 0):
                raise UnsupportedDecode(f"{Path(path).name}: {depth}비트 PNG(색 유형 {color})는 운영 디코드와 맞춰 보지 않았습니다.")
        if is_jpeg and opened.mode in ("CMYK", "YCCK"):
            raise UnsupportedDecode(f"{Path(path).name}: CMYK JPEG입니다. 운영 디코드를 재현하지 못해 자르지 않습니다.")
        icc = opened.info.get("icc_profile") if is_jpeg else None
        if icc and hashlib.sha1(icc).hexdigest() not in MATCHED_JPEG_ICC_SHA1:
            # 맞춰 본 프로필(HP sRGB IEC61966-2.1 — Java 자체 sRGB 프로필과 같아 Java가 색 변환 없이 읽는다, 실파일 19/19 비트 동일)만
            # 받는다. 다른 프로필(LittleCMS built-in sRGB는 ±1 달랐다 · Display P3 · CMYK)은 Java가 다른 색 경로로 읽어 경계가
            # 문턱 근처면 조각이 바뀐다. 모르면 자르지 않는다.
            raise UnsupportedDecode(f"{Path(path).name}: 맞춰 보지 않은 ICC 색 프로필을 품은 JPEG입니다. 운영 디코드를 재현하지 못해 자르지 않습니다.")
        if is_jpeg:
            sampling = jpeg_sampling(data)
            matched = sampling is not None and (len(sampling) == 1 or (
                len(sampling) == 3 and sampling[0] in MATCHED_JPEG_SAMPLING and sampling[1] == sampling[2] == (1, 1)))
            if not matched:
                raise UnsupportedDecode(f"{Path(path).name}: 색 표본 배치 {sampling}의 JPEG는 운영 디코드와 맞춰 보지 않았습니다. 자르지 않습니다.")
        image = _exif_only_transpose(opened, data) if is_jpeg else opened.copy()
        if image.mode in ("I;16", "I;16B", "I;16L", "I"):
            # 16비트 회색은 16비트 값 그대로 선형 → sRGB로 옮긴다. 8비트로 먼저 줄이면 그늘(0~13단계)이 뭉개져
            # 배경 행(편차 12)과 이음매(점프 10·28) 판정이 달라진다.
            wide = image.convert("I")
            flat = wide.get_flattened_data() if hasattr(wide, "get_flattened_data") else wide.getdata()
            values = [max(0, min(65535, int(v))) for v in flat]
            gray = Image.new("L", image.size)
            gray.putdata([SRGB_FROM_LINEAR_GRAY_16[v] for v in values])
            return Image.merge("RGB", (gray, gray, gray))
        if image.mode in ("L", "LA", "1"):
            gray = image.convert("L").point(SRGB_FROM_LINEAR_GRAY)
            return Image.merge("RGB", (gray, gray, gray))
        return image.convert("RGB")


_TRANSPOSE = {2: ("FLIP_LEFT_RIGHT",), 3: ("ROTATE_180",), 4: ("FLIP_TOP_BOTTOM",), 5: ("TRANSPOSE",),
              6: ("ROTATE_270",), 7: ("TRANSVERSE",), 8: ("ROTATE_90",)}


def exif_orientation(data: bytes) -> int:
    """운영 `ExifOrientation.exifOrientation`·`tiffOrientation`을 바이트 그대로 옮긴 것.

    Pillow의 getexif는 태그의 형(type)·개수(count)를 가리지 않고, 첫 APP1이 아닌 것도 읽는다. 운영은
    - 마커를 SOS(DA)·EOI(D9)까지 훑고, 채움 바이트 없이 FF가 아니면 멈춘다,
    - 첫 `Exif\\0\\0` APP1만, 그 안의 IFD0만 본다,
    - 방향 태그(0x0112)는 형이 SHORT(3)이고 개수가 1일 때만 쓴다,
    - 1~8 밖의 값·깨진 구조는 1(보정 없음)이다.
    LONG 형으로 적힌 방향 6을 Pillow는 돌리고 운영은 돌리지 않는다 — 그러면 모든 DxxTyy가 다른 조각을 가리킨다."""
    def short(at: int, little: bool) -> int:
        return data[at] | data[at + 1] << 8 if little else data[at] << 8 | data[at + 1]

    def integer(at: int, little: bool) -> int:
        raw = int.from_bytes(data[at:at + 4], "little" if little else "big")
        return raw - (1 << 32) if raw >= 1 << 31 else raw  # Java int는 부호가 있다

    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return 1
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            break
        marker = data[offset + 1]
        offset += 2
        if marker in (0xD9, 0xDA) or offset + 2 > len(data):
            break
        length = data[offset] << 8 | data[offset + 1]
        if length < 2 or offset + length > len(data):
            break
        payload = offset + 2
        if marker == 0xE1 and length >= 14 and data[payload:payload + 6] == b"Exif\x00\x00":
            tiff, end = payload + 6, offset + length
            if tiff + 8 > end:
                return 1
            little = data[tiff:tiff + 2] == b"II"
            if not little and data[tiff:tiff + 2] != b"MM":
                return 1
            ifd = tiff + integer(tiff + 4, little)
            if ifd < tiff or ifd + 2 > end:
                return 1
            for index in range(short(ifd, little)):
                entry = ifd + 2 + index * 12
                if entry + 12 > end:
                    break
                if short(entry, little) == 0x0112 and short(entry + 2, little) == 3 and integer(entry + 4, little) == 1:
                    value = short(entry + 8, little)
                    return value if 1 <= value <= 8 else 1
            return 1
        offset += length
    return 1


# 운영(Java ImageIO)과 픽셀까지 맞춰 본 JPEG 색 표본 배치(휘도의 가로·세로 표본 수, 색차 둘은 1×1).
# 4:4:0(휘도 1×2) 같은 배치는 Java와 libjpeg-turbo의 색차 확대 방식이 달라 경계가 움직였다(실측) — 받지 않는다.
MATCHED_JPEG_SAMPLING = {(2, 2), (2, 1), (4, 1), (1, 1)}


def jpeg_sampling(data: bytes) -> list[tuple[int, int]] | None:
    """JPEG 프레임 머리(SOF0·1·2)의 성분별 표본 수 [(가로, 세로), …]. 찾지 못하면 None."""
    offset = 2
    while offset + 4 <= len(data) and data[offset] == 0xFF:
        marker = data[offset + 1]
        if marker in (0xD9, 0xDA):
            return None
        length = data[offset + 2] << 8 | data[offset + 3]
        if marker in (0xC0, 0xC1, 0xC2) and offset + 2 + length <= len(data):
            count = data[offset + 9]
            parts = data[offset + 10: offset + 10 + 3 * count]
            return [(parts[i + 1] >> 4, parts[i + 1] & 0x0F) for i in range(0, len(parts), 3)]
        offset += 2 + length
    return None


def _bmp_matched(data: bytes) -> bool:
    """BMP 머리의 비트 수와 압축. 맞춰 본 것은 무압축 24·32비트와 8비트 팔레트뿐이다 — 16비트(RGB555)는 5→8비트 넓히기가
    Java와 달랐다(실측)."""
    if len(data) < 34:
        return False
    bits = int.from_bytes(data[28:30], "little")
    compression = int.from_bytes(data[30:34], "little")
    return compression == 0 and bits in (8, 24, 32)


def _exif_only_transpose(image: Any, data: bytes) -> Any:
    """EXIF(APP1)의 회전만, 운영이 읽는 방식 그대로(`exif_orientation`) 따른다. XMP의 회전은 읽지 않는다."""
    from PIL import Image

    result = image.copy()
    for move in _TRANSPOSE.get(exif_orientation(data), ()):
        result = result.transpose(getattr(Image.Transpose, move))
    return result


def decode_for_display(path: Path | str) -> Any:
    """보여 주기만 할 사진을 읽는다(자르지 않는다). EXIF 회전 · ICC → sRGB · 투명은 흰 바탕 —
    운영 `BatchImageComposer.rgbOnWhite`처럼. 경계를 재현하지 않으므로 형식을 거절하지 않는다."""
    import io

    from PIL import Image, ImageCms

    try:
        opened_file = Image.open(path)
    except Image.DecompressionBombError as error:
        raise OSError(f"{Path(path).name}: 너무 큰 사진이라 열지 않았습니다") from error
    with opened_file as opened:
        # 회전은 운영의 상세 경로(BatchImageComposer.decode)처럼 — JPEG의 EXIF(IFD0·SHORT·count 1)만 따르고, 다른 형식이나
        # XMP 회전은 따르지 않는다. 운영의 썸네일 경로(renderVisualId → decodeRaw)는 돌리지 않는다는 점이 다르다 — 색인이
        # 회전을 적용하지 않은 원본을 직접 가리키면 모델이 본 방향과 여기서 보이는 방향이 다를 수 있다.
        with open(path, "rb") as handle:
            data = handle.read()
        icc = opened.info.get("icc_profile")
        image = _exif_only_transpose(opened, data) if data[:2] == b"\xff\xd8" else opened.copy()
        if icc and image.mode in ("RGB", "RGBA", "CMYK"):
            try:
                source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                target = ImageCms.createProfile("sRGB")
                mode = "RGBA" if image.mode == "RGBA" else "RGB"
                image = ImageCms.profileToProfile(image, source, target, outputMode=mode)
            except (ImageCms.PyCMSError, OSError, ValueError):
                image = image.convert("RGB")
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            white = Image.new("RGB", image.size, "white")
            white.paste(image, mask=image.getchannel("A"))
            return white
        return image.convert("RGB")


def usable(image: Any) -> bool:
    """운영의 품질 문턱(`ImageQualityGate.MIN_EDGE_PIXELS`). 1px 구분선·추적 픽셀은 자르지 않는다."""
    width, height = image.size
    return width >= MIN_TILE_PX and height >= MIN_TILE_PX


def parse_piece(name: str) -> tuple[int, int] | None:
    """조각 이름에서 (D, T)를 읽는다. 운영 DetailTileId가 받는 것만 받는다 — ASCII 숫자만(전각·다른 문자 숫자는 거절),
    끝 줄바꿈 없이, 두 번호 모두 1 이상. 앞머리 «<상품 번호>-»는 평가 하네스 파일 이름(save_tiles)이라 받는다. 아니면 None."""
    piece = re.search(r"(?:^|-)D([0-9]+)T([0-9]+)\Z", name or "")
    if not piece or int(piece.group(1)) < 1 or int(piece.group(2)) < 1:
        return None
    return int(piece.group(1)), int(piece.group(2))


def scene_id(source_index: int, tile_index: int) -> str:
    """운영 `DetailTileId.format`과 같은 이름. 둘 다 1부터 센다 — 0 이하는 운영이 거절하므로 여기서도 거절한다."""
    if source_index < 1 or tile_index < 1:
        raise ValueError(f"장면 번호는 1부터입니다: D{source_index} T{tile_index}")
    return f"D{source_index:02d}T{tile_index:02d}"
