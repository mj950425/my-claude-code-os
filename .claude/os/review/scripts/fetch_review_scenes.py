#!/usr/bin/env python3
"""심사가 다시 볼 장면을 로컬로 내려 놓는다. 판정하지 않는다.

엔진은 판독기가 쓴 **문장**만 산출물에 남긴다("남성·여성 모델이 모두 확인됨").
그 문장이 맞는지 되짚으려면 문장이 가리킨 **사진**이 있어야 하는데, 상세 타일은
URL로만 남아 있어 아무도 두 번 보지 못했다. 이 스크립트가 그 자리를 채운다.

하는 일은 셋뿐이다 — 어느 장면을 다시 볼지 고르고, 원본을 받고, 타일로 자른다.
무엇이 보이는지는 말하지 않는다. 그것은 `catalog-scene-reader`의 일이고,
그 판독이 주장을 지지하는지는 `catalog-evidence-reviewer`의 일이다.
셋을 나눈 이유는 하나다. 준비·판독·심사가 같은 눈이면 서로를 검증하지 못한다.

review 패키지의 규칙을 그대로 따른다.
- `run-summary.json`과 그 요약이 `artifacts`로 **선언한 경로만** 읽는다.
  선언되지 않았으면 추측하지 않고 건너뛴 이유를 남긴다.
- 프로필·어댑터·정책을 읽지 않는다.
- `run-review/` 밖으로 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 타일 경계 규칙은 `common/tile_rule.py` 하나다 — 운영 `BatchImageComposer.tileRanges`의 이식이다.
# 산출물은 `D01T06`이라고만 말하고 그것이 어느 픽셀인지는 말하지 않는다. 그 번호는 **그 번호를 매긴
# 실행의 규칙**으로 잘라야 같은 사진이 된다 — 최신 규칙이 아니다. 운영 규칙은 고정 절단에서 배경 띠,
# 사진 이음매로 바뀌어 왔고, 옛 실행의 T03을 새 규칙으로 자르면 다른 사진을 보고 동의하거나 반박한다.
# 그래서 갤러리가 상품마다 `tileRule`(판)을 선언하고, 선언이 없으면 자르지 않는다.
# 계산한 타일 수가 산출물이 참조한 최대 타일 번호를 담지 못해도 그 원본은 자르지 않고 건너뛴다.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))
import tile_rule  # noqa: E402

# 대표 사진 후보를 몇 개까지 받아 볼 것인가. 전부 받으면 상품마다 목록 전체를 내려받게 된다.
REFERENCE_CANDIDATES = 4
# 대상의 정의가 이보다 작으면 경고한다. 색·프린트 대조가 이 아래에서 흔들린 사례가 있다.
REFERENCE_MIN_LONG_SIDE = 400

# 운영 DetailTileId처럼 0번 자리는 없다. D00·T00은 풀지 않는다(T00이 ranges[-1], 마지막 조각으로 새지 않게).
# 끝은 `\Z` — `$`는 끝의 줄바꿈을 받아 «D01T01\n»도 통과시키지만 운영(Java Matcher.matches)은 거절한다.
SCENE_ID = re.compile(r"^D(0*[1-9]\d*)T(0*[1-9]\d*)\Z")
TILE_SHAPE = re.compile(r"^D\d+T\d+\Z")
# 운영 DetailTileId의 인용 문법(CITATION) — 모델이 인용한 이름은 두 자리 이상이어야 한다. «D1T3»은 화면에 없던 이름이라
# 운영이 근거로 세지 않는다. 서버가 매긴 이름(갤러리 · LAYOUT 문법)만 한 자리를 D01T03으로 편다.
CITATION = re.compile(r"^D(\d{2,})T(\d{2,})\Z")
Image = None  # main()에서 Pillow를 늦게 들여온다
USER_AGENT = "catalog-data-os-review/1.0"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _tile_ranges(image: "Image.Image", version: str = tile_rule.CURRENT) -> list[tuple[int, int]]:
    """원본 하나가 몇 개의 타일로 갈라지는가. `version`은 그 번호를 매긴 판이다."""
    return tile_rule.tile_ranges(image, version)


def _open_rgb(path: Path, decoder: str) -> "Image.Image":
    """번호를 매긴 구현이 읽은 것처럼 읽는다. 경계는 행의 밝기로 정하므로 같은 밝기로 읽어야 한다.
    그 디코드를 재현하지 못하는 파일은 UnsupportedDecode 그대로 올린다 — 손상·내려받기 실패(OSError)와 이유 문구를 가른다.
    운영의 품질 문턱(64px) 밑의 원본도 같다 — 운영은 그런 원본에 타일 번호를 매기지 않았다. 여기서 자르면
    운영에 없던 D0xT01..이 생기고 판독자가 그것을 운영 근거로 인용한다."""
    image = tile_rule.decode(path, decoder)
    if not tile_rule.usable(image):
        raise OSError(f"64px 미만({image.size[0]}x{image.size[1]}) — 운영은 이 원본에 타일을 매기지 않았다")
    return image


def canonical_scene(scene_id: str) -> str:
    """서버가 매긴 이름(갤러리 · 운영 LAYOUT 문법)의 «D1T3»·«D001T03»을 D01T03 모양으로. 장면 좌표가 아니면 그대로.
    모델이 인용한 이름은 먼저 `refused_citation`으로 운영 CITATION 문법을 거친다."""
    match = SCENE_ID.match(scene_id)
    return tile_rule.scene_id(int(match.group(1)), int(match.group(2))) if match else scene_id


def refused_citation(scene_id: str) -> bool:
    """조각 이름 모양(DxxTyy)인데 운영 인용 문법을 못 지난 인용 — 두 자리 미만이거나 0번 자리(D00T01·D01T00). 운영
    parseCitation은 둘 다 근거로 세지 않는다. 좌표 모양이 아닌 이름(TARGET_REFERENCE 같은)만 대표 사진으로 풀린다."""
    return bool(TILE_SHAPE.match(scene_id)) and not (CITATION.match(scene_id) and SCENE_ID.match(scene_id))


def safe_name(product_key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", product_key)


def url_key(url: str) -> str:
    """원본 URL을 캐시 파일 이름으로 바꾼다.

    파이썬의 `hash()`는 실행마다 소금이 달라 문자열에 같은 값을 주지 않는다. 그걸 쓰면
    캐시 이름이 매번 바뀌어 **캐시가 한 번도 맞지 않고**, 다시 돌릴 때마다 원본을 통째로
    다시 받는다. 조용히 느려지기만 해서 눈에 띄지도 않는다.
    """
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def save_tile(tile: "Image.Image", target: Path, max_width: int) -> None:
    """타일 하나를 저장한다. 판독 비용은 픽셀 넓이에 비례하므로 폭을 줄여 둔다.

    줄이는 것은 공짜가 아니다 — 작은 얼굴이 뭉개지면 판독이 `AMBIGUOUS`로 밀린다.
    그래서 기본값은 «사람이 무엇을 하고 있는지»가 남는 선에서 잡고, 판독이 흐리다고
    보고한 장면만 `--max-width`를 올려 다시 받는다. 처음부터 원본 크기로 전부 받으면
    대부분의 장면에 쓸모없는 해상도를 치르게 된다.
    """
    if max_width > 0 and tile.width > max_width:
        height = round(tile.height * max_width / tile.width)
        tile = tile.resize((max_width, height), Image.LANCZOS)
    tile.convert("RGB").save(target, quality=82, optimize=True)


LEDGER_COLLECTION = "seller_product_images"


def ledger_images(product_key: str, ledger_cmd: str) -> dict[str, list[str]]:
    """이미지 원장에서 이 상품의 이미지 목록을 종류별로 읽는다.

    산출물이 장면을 못 푸는 경우가 있다. 실행이 인용한 이름이 타일 좌표가 아니거나
    (대표 이미지를 가리키는 이름), 갤러리가 그 원본을 아예 싣지 않은 경우다.
    그때 «확인할 자리가 없다»로 끝내면 되짚지 못한 주장이 그대로 남는다.

    원장은 산출물이 아니므로 **기본 경로가 아니다.** `--ledger`로 명시할 때만 붙고,
    무엇을 원장에서 가져왔는지는 장면마다 `source`로 남긴다 — 산출물에서 온 장면과
    나중에 보충한 장면을 섞으면, 다음 사람이 무엇을 근거로 되짚었는지 모르게 된다.
    """
    platform, _, product_id = product_key.partition(":")
    if not platform or not product_id or not Path(ledger_cmd).exists():
        return {}
    try:
        result = subprocess.run(
            [
                sys.executable, ledger_cmd, "find", LEDGER_COLLECTION,
                "--filter", json.dumps(
                    {"platform_code": platform, "platform_product_id": product_id}
                ),
                "--limit", "1",
                "--projection", json.dumps({"images": 1}),
            ],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    by_type: dict[str, list[str]] = {}
    for row in rows if isinstance(rows, list) else []:
        for image in (row.get("images") or []):
            if not isinstance(image, dict):
                continue
            url = str(image.get("image_url") or "")
            kind = str(image.get("type") or "")
            if url and kind:
                by_type.setdefault(kind, []).append(url)
    return by_type


def absolutize(url: str, sibling_urls: list[str]) -> str | None:
    """원장은 상대 경로를 담기도 한다. 같은 상품의 다른 URL에서 호스트를 빌린다."""
    if url.startswith("http"):
        return url
    for other in sibling_urls:
        match = re.match(r"^(https?://[^/]+)", other)
        if match:
            return match.group(1) + ("" if url.startswith("/") else "/") + url
    return None


def download(url: str, target: Path) -> None:
    """원본을 받는다. 판매처 CDN이 섞여 있어 한 방법으로는 다 받아지지 않는다.

    장면 원본은 여러 호스트에 흩어져 있고 그중 일부는 중간 인증서를 안 내려
    파이썬의 엄격한 검증에서 막힌다. 받지 못한 장면은
    "판독기 주장을 반박할 근거가 없다"가 아니라 그냥 못 본 자리이므로,
    조용히 비우지 않고 curl로 한 번 더 시도한 뒤 실패를 기록으로 남긴다.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            target.write_bytes(response.read())
        return
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
        first = error
    result = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", "60", "-A", USER_AGENT, "-o", str(target), url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise OSError(f"{first} · curl: {result.stderr.strip() or result.returncode}")


def carry_over(
    index_path: Path,
    fresh: list[dict[str, Any]],
    based_on: Any,
    declared_rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """직전 색인에서 이번에 다시 받지 않은 상품을 그대로 이어 붙인다.

    `--product`는 «그 상품만 다시 받는다»는 뜻이지 «나머지를 잊는다»가 아니다.
    그런데 색인을 이번에 고른 것만으로 덮으면 지난 수집이 통째로 사라지고,
    접촉 시트와 판독 과제도 그 상품 하나만 남는다. 판독이 흐리다고 한 타일 하나를
    크게 다시 받으면 나머지 상품의 재판독이 조용히 증발하는 것이다.

    다른 실행 위에 선 색인이면 잇지 않는다. 사이클이 다시 돌았으면 그 장면들은
    옛 실행의 것이고, 섞으면 어느 실행의 근거인지 알 수 없다.
    """
    if not index_path.exists():
        return []
    try:
        previous = read_json(index_path)
    except json.JSONDecodeError:
        return []
    if previous.get("basedOn") != based_on:
        return []
    refetched = {product.get("productKey") for product in fresh}

    def same_rule(product: dict[str, Any]) -> bool:
        if declared_rules is None:
            # 갤러리가 없어 이번에 아무것도 새로 자르지 않았다. 섞일 새 조각이 없으니 지난 색인을 그대로 둔다.
            return True
        # 옛 색인의 조각이 지금 갤러리가 선언한 판으로 잘린 것이 아니면 잇지 않는다.
        # 판이 다른 조각을 한 색인에 두면 이름표와 내용이 어긋난다.
        # 판만이 아니라 디코더도 같아야 같은 조각이다(같은 v0라도 하네스·운영 디코드는 다른 밝기로 읽는다).
        was = product.get("tileRule") or {}
        now = declared_rules.get(product.get("productKey")) or {}
        return bool(was.get("version")) and (was.get("version"), was.get("decoder")) == (now.get("version"), now.get("decoder"))

    return [
        product
        for product in (previous.get("products") or [])
        if product.get("productKey") not in refetched and same_rule(product)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="runs/<프로필ID> 폴더")
    parser.add_argument(
        "--owner",
        default="GOLDEN",
        help="이 귀책으로 심판이 지목한 건만 고른다. ALL이면 전부.",
    )
    parser.add_argument("--product", action="append", default=[], help="productKey 지정. 반복 가능")
    parser.add_argument("--limit", type=int, default=0, help="상품 수 상한. 0이면 무제한")
    parser.add_argument(
        "--all-scenes",
        action="store_true",
        help="인용된 장면만이 아니라 갤러리의 상세 타일을 전부 자른다",
    )
    parser.add_argument(
        "--fanout",
        choices=("product", "tile"),
        default="product",
        help="판독 과제를 무엇 하나로 묶는가. 기본은 상품",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=640,
        help="저장할 타일의 최대 폭. 판독 비용은 넓이에 비례해서 붙는다",
    )
    parser.add_argument(
        "--ledger",
        action="store_true",
        help="산출물이 못 푸는 장면을 이미지 원장에서 보완한다(외부 조회)",
    )
    parser.add_argument(
        "--ledger-cmd",
        default=str(Path.home() / ".claude/skills/mongo-query/scripts/mongo_connect.py"),
        help="이미지 원장 조회기 경로",
    )
    args = parser.parse_args()

    run_root = args.run.resolve()
    summary_path = run_root / "run-summary.json"
    if not summary_path.exists():
        print(f"run-summary.json이 없다: {summary_path}")
        return 2
    summary = read_json(summary_path)
    artifacts = summary.get("artifacts") or {}

    project_root = next(
        (parent for parent in [run_root, *run_root.parents] if (parent / ".claude").is_dir()),
        run_root,
    )

    def declared(name: str) -> Path | None:
        value = artifacts.get(name)
        if not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else project_root / path

    skipped: list[dict[str, str]] = []
    gallery_path = declared("gallery")
    verdicts_path = declared("arbiterVerdicts")
    queue_dir = declared("queueDirectory")
    for name, path in (
        ("gallery", gallery_path),
        ("arbiterVerdicts", verdicts_path),
        ("queueDirectory", queue_dir),
    ):
        if path is None:
            skipped.append({"artifact": name, "reason": "요약이 이 산출물을 선언하지 않았다"})
        elif not path.exists():
            skipped.append({"artifact": name, "reason": f"선언된 경로가 없다: {path}"})
    if gallery_path is None or not gallery_path.exists():
        # 갤러리가 없으면 sceneId를 원본 URL로 되돌릴 수 없다. 추측하지 않는다.
        out_dir = run_root / "run-review" / "scenes"
        out_dir.mkdir(parents=True, exist_ok=True)
        # 갤러리가 없어 아무것도 못 받았을 때도 지난 색인을 덮어 지우지 않는다.
        kept = (
            carry_over(out_dir / "index.json", [], summary.get("generatedAt"))
            if args.product
            else []
        )
        (out_dir / "index.json").write_text(
            json.dumps(
                {
                    "basedOn": summary.get("generatedAt"),
                    "selector": {"maxWidth": args.max_width},
                    "products": kept,
                    "skipped": skipped,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        for item in skipped:
            print(f"SKIPPED {item['artifact']}: {item['reason']}")
        return 0

    gallery = {
        str(row.get("productKey")): row
        for row in read_jsonl(gallery_path)
        if row.get("productKey")
    }
    verdicts = {
        str(row.get("productKey")): row
        for row in read_jsonl(verdicts_path or Path("/nonexistent"))
        if row.get("productKey")
    }
    queue: dict[str, dict[str, Any]] = {}
    if queue_dir and queue_dir.exists():
        for queue_file in sorted(queue_dir.glob("*.jsonl")):
            for row in read_jsonl(queue_file):
                key = str(row.get("productKey") or "")
                if not key:
                    continue
                # 한 상품이 여러 신호 큐에 걸리고 큐마다 채운 필드가 다르다.
                # 먼저 만난 파일 하나만 잡으면 장면 필드가 통째로 비어, 되짚을 것이
                # 없는 것과 "이 신호 큐에는 안 적힌 것"을 구분하지 못한다.
                merged = queue.setdefault(key, {})
                for field, value in row.items():
                    if value in (None, "", [], {}):
                        continue
                    merged.setdefault(field, value)

    if args.product:
        selected = [key for key in args.product if key in verdicts or key in queue]
    else:
        selected = [
            key
            for key, verdict in verdicts.items()
            if args.owner == "ALL" or str(verdict.get("owner")) == args.owner
        ]
    selected.sort()
    if args.limit > 0:
        selected = selected[: args.limit]

    out_dir = run_root / "run-review" / "scenes"
    out_dir.mkdir(parents=True, exist_ok=True)
    source_cache = out_dir / ".sources"
    source_cache.mkdir(exist_ok=True)

    global Image
    try:
        from PIL import Image
    except ImportError:
        print("Pillow가 필요하다: python3 -m pip install pillow")
        return 2

    products: list[dict[str, Any]] = []
    for product_key in selected:
        verdict = verdicts.get(product_key, {})
        row = queue.get(product_key, {})
        images = gallery.get(product_key) or {}
        product_dir = out_dir / safe_name(product_key)
        details = [image for image in (images.get("details") or []) if isinstance(image, dict)]
        if not details:
            skipped.append({"artifact": product_key, "reason": "갤러리에 상세 타일이 없다"})
            continue
        try:
            version = tile_rule.rule_named(images.get("tileRule"))
            decoder = tile_rule.decoder_named(images.get("tileRule"))
        except ValueError:
            skipped.append({
                "artifact": product_key,
                "reason": "갤러리가 타일 규칙 판(tileRule)을 선언하지 않았다 — 어느 규칙으로 번호를 매겼는지 모르면 자르지 않는다",
            })
            continue

        # 대표 판매 사진. **이것이 «대상 상품»의 정의다.**
        # 지금까지 판독자에게는 상품명만 넘어갔고, 그래서 «펠라인 = 표범»을 이름으로 추측해야 했다.
        # 같은 상세 페이지에 실린 다른 컬러웨이를 대상으로 세는 사고가 바로 그 자리에서 났다 —
        # 파는 물건이 어떻게 생겼는지는 이름이 아니라 이 사진이 말한다.
        # 첫 썸네일을 그냥 집지 않는다. 갤러리의 첫 항목이 200px짜리 목록용 축소본이고
        # 뒤에 더 큰 것이 있는 일이 실제로 있었다 — 그 200px 위에서 「이 사람이 든 것이
        # 파는 그 물건인가」를 가르게 되고, 색·프린트처럼 한 축으로만 갈리는 대조는
        # 거기서 무너진다. **파이프라인에서 가장 하중이 큰 사진이므로 가장 큰 것을 고른다.**
        reference: dict[str, Any] | None = None
        best_area = 0
        target = product_dir / "_reference.jpg"
        candidates = [
            str(image.get("url"))
            for image in (images.get("thumbnails") or [])
            if isinstance(image, dict) and image.get("url")
        ][:REFERENCE_CANDIDATES]
        for position, url in enumerate(candidates):
            probe = source_cache / f"{safe_name(product_key)}-ref{position}.jpg"
            try:
                if url.startswith("http"):
                    if not probe.exists():
                        download(url, probe)
                else:
                    local = (run_root / url).resolve()
                    if not local.is_file():
                        continue
                    if not probe.exists():
                        probe.write_bytes(local.read_bytes())
                # 대표 사진도 보기용 디코드(EXIF 회전·ICC → sRGB·투명은 흰 바탕)로 — 원장 사진과 같은 길. 바이트를 그대로
                # 옮기면 EXIF를 무시하는 뷰어에서 기준 사진이 누운 채 대조에 쓰이고, 크기도 회전 전 값이 적힌다.
                decoded = tile_rule.decode_for_display(probe)
                width, height = decoded.size
            except OSError as error:
                skipped.append({"artifact": url, "reason": f"대표 사진을 받지 못했다: {error}"})
                continue
            if width * height <= best_area:
                continue
            best_area = width * height
            product_dir.mkdir(parents=True, exist_ok=True)
            decoded.save(target, "JPEG", quality=95)
            # 어느 해상도에서 대조했는지 남긴다. 작은 대표 사진에서 나온 `MATCH`는
            # 큰 것에서 뒤집힐 수 있고, 그 사실은 크기를 적어 두지 않으면 알 수 없다.
            reference = {
                "path": str(target.relative_to(run_root)),
                "sourceUrl": url,
                "size": [width, height],
            }

        # 장면 이름은 들어오는 자리에서 한 모양(D01T03)으로 — «D1T3»으로 인용된 것과 갤러리의 D01T03이 같은 조각이다.
        # 원래 표기는 citedSceneIds 원문에 그대로 남는다.
        # 모델이 인용한 이름은 운영 인용 문법으로 먼저 거른다 — 운영이 버린 «D1T3»을 여기서 D01T03으로 펴서 «심판이 인용함»으로
        # 보이면, 운영이 근거로 세지 않은 조각을 사람이 근거로 읽는다. 거른 것은 못 푼 장면(unresolved)으로 이유와 함께 남긴다.
        scene_notes = row.get("sceneNotes") if isinstance(row.get("sceneNotes"), dict) else {}
        raw_cited = [str(value) for value in (row.get("policyEvidenceSceneIds") or [])] + [str(key) for key in scene_notes]
        refused_citations = sorted({value for value in raw_cited if refused_citation(value)})
        scene_notes = {canonical_scene(str(key)): value for key, value in scene_notes.items() if not refused_citation(str(key))}
        cited = list(
            dict.fromkeys(
                [canonical_scene(str(value)) for value in (row.get("policyEvidenceSceneIds") or [])
                 if not refused_citation(str(value))]
                + [str(key) for key in scene_notes]
            )
        )
        # `--all-scenes`가 갤러리 목록을 뜻하면 «전부»가 아니다. 갤러리는 판독기가 **고른** 타일만
        # 싣기 때문에, 고르지 않은 자리가 조용히 빠지고 그 자리가 «없는 것»으로 기록된다.
        # 원본의 타일 수는 이미지에서 계산할 수 있으므로, 여기서는 원본 단위로 요구해 두고
        # 내려받은 뒤 실제 타일 수만큼 펼친다(아래 expand_all).
        # `--all-scenes`는 «전부»라는 뜻이지 «인용된 것 대신»이 아니다. 갤러리의 상세 목록만
        # 넣으면 실행이 인용한 이름(`TARGET_REFERENCE`처럼 타일 좌표가 아닌 것)이 통째로 빠지고,
        # **하필 그 자리가 되짚어야 할 유일한 근거인 건**이 있었다. 합집합으로 둔다.
        wanted = (
            list(dict.fromkeys([canonical_scene(str(image.get("sceneId"))) for image in details] + cited))
            if args.all_scenes
            else cited
        )

        # `D01T06`은 원본 `D01`의 6번째 조각이다. 갤러리는 판독기가 **고른** 타일만 싣지만,
        # 같은 원본의 다른 타일이 하나라도 있으면 그 URL로 빠진 타일도 되짚을 수 있다.
        # 이걸 안 하면 정작 반박해야 할 장면(고르지 않아 목록에 없는 쪽)만 늘 빠진다.
        url_by_source: dict[str, str] = {}
        for image in details:
            match = SCENE_ID.match(str(image.get("sceneId") or ""))
            url = str(image.get("url") or "")
            if match and url.startswith("http"):
                url_by_source.setdefault(f"D{int(match.group(1)):02d}", url)

        unresolved: list[str] = []  # 거른 인용은 원장으로 되살리지 않는다 — 아래 출력에서만 더한다
        by_source: dict[str, list[str]] = {}
        for scene_id in dict.fromkeys(wanted):
            match = SCENE_ID.match(scene_id)
            url = url_by_source.get(f"D{int(match.group(1)):02d}") if match else None  # D1과 D01은 같은 원본
            if not url:
                # 타일 좌표가 아닌 이름(`TARGET_REFERENCE` 등)이거나 원본을 모른다.
                unresolved.append(scene_id)
                continue
            by_source.setdefault(url, []).append(scene_id)

        # 산출물이 못 푼 장면만 원장에 묻는다. 산출물이 답한 것을 원장으로 덮지 않는다.
        ledger_urls: dict[str, str] = {}
        if args.ledger and unresolved:
            catalog = ledger_images(product_key, args.ledger_cmd)
            siblings = list(url_by_source.values()) + [
                url for urls in catalog.values() for url in urls
            ]
            still_unresolved: list[str] = []
            for scene_id in unresolved:
                match = SCENE_ID.match(scene_id)
                if match:
                    detail = catalog.get("DETAIL") or []
                    position = int(match.group(1)) - 1
                    picked = detail[position] if 0 <= position < len(detail) else None
                else:
                    # 타일 좌표가 아닌 이름은 대표 이미지를 가리킨다. 원장의 첫 썸네일이 그것이다.
                    thumbnails = catalog.get("THUMBNAIL") or []
                    picked = thumbnails[0] if thumbnails else None
                absolute = absolutize(picked, siblings) if picked else None
                if absolute:
                    ledger_urls[scene_id] = absolute
                else:
                    still_unresolved.append(scene_id)
            unresolved = still_unresolved

        product_dir = out_dir / safe_name(product_key)
        scenes: list[dict[str, Any]] = []
        if args.all_scenes:
            # 원본마다 실제 타일 수를 세어 T01..Tn을 전부 요구한다. 못 받은 원본은 건너뛴다.
            for source, url in sorted(url_by_source.items()):
                source_file = source_cache / f"{safe_name(product_key)}-{url_key(url)}.jpg"
                if not source_file.exists():
                    try:
                        download(url, source_file)
                    except OSError as error:
                        skipped.append({"artifact": url, "reason": f"내려받지 못했다: {error}"})
                        continue
                try:
                    probe = _open_rgb(source_file, decoder)
                except tile_rule.UnsupportedDecode as error:
                    skipped.append({"artifact": url, "reason": f"운영 디코드를 재현하지 못해 자르지 않았다: {error}"})
                    continue
                except OSError as error:
                    skipped.append({"artifact": url, "reason": f"이미지를 열지 못했다: {error}"})
                    continue
                total = len(_tile_ranges(probe, version))
                by_source[url] = [f"{source}T{index:02d}" for index in range(1, total + 1)]
        for url, scene_ids in by_source.items():
            indexes = [SCENE_ID.match(scene) for scene in scene_ids]
            if not all(indexes):
                unresolved.extend(scene for scene, m in zip(scene_ids, indexes) if not m)
                scene_ids = [scene for scene, m in zip(scene_ids, indexes) if m]
                if not scene_ids:
                    continue
            source_file = source_cache / f"{safe_name(product_key)}-{url_key(url)}.jpg"
            if not source_file.exists():
                try:
                    download(url, source_file)
                except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
                    skipped.append({"artifact": url, "reason": f"내려받지 못했다: {error}"})
                    continue
            try:
                image = _open_rgb(source_file, decoder)
            except tile_rule.UnsupportedDecode as error:
                skipped.append({"artifact": url, "reason": f"운영 디코드를 재현하지 못해 자르지 않았다: {error}"})
                continue
            except OSError as error:
                skipped.append({"artifact": url, "reason": f"이미지를 열지 못했다: {error}"})
                continue
            width, height = image.size
            ranges = _tile_ranges(image, version)
            max_index = max(int(SCENE_ID.match(scene).group(2)) for scene in scene_ids)
            if max_index > len(ranges):
                # 타일 규칙이 산출물과 어긋난다. 다른 조각을 보여주느니 보여주지 않는다.
                skipped.append(
                    {
                        "artifact": url,
                        "reason": (
                            f"타일 규칙 불일치: 계산 {len(ranges)}개인데 산출물은 T{max_index:02d}를 참조한다"
                        ),
                    }
                )
                continue
            product_dir.mkdir(parents=True, exist_ok=True)
            for scene_id in sorted(scene_ids):
                index = int(SCENE_ID.match(scene_id).group(2))
                top, bottom = ranges[index - 1]
                target = product_dir / f"{scene_id}.jpg"
                save_tile(image.crop((0, top, width, bottom)), target, args.max_width)
                scenes.append(
                    {
                        "sceneId": scene_id,
                        "path": str(target.relative_to(run_root)),
                        "sourceUrl": url,
                        "source": "ARTIFACT",
                        # 번호를 매길 때의 원본과 지금 받은 원본이 같은지 확인할 길이 없다(갤러리에 크기·해시가 없다).
                        # 조각 수가 맞아도 CDN이 사진을 바꿨을 수 있다 — 확인하지 못했다고 적어 둔다.
                        "sourceVerified": False,
                        "crop": {"top": top, "bottom": bottom, "width": width},
                        "citedByJudge": scene_id in cited,
                        "judgeSceneNote": scene_notes.get(scene_id),
                    }
                )

        known_sources = dict(url_by_source)
        for scene_id, url in ledger_urls.items():
            source_file = source_cache / f"{safe_name(product_key)}-{safe_name(scene_id)}.jpg"
            if not source_file.exists():
                try:
                    download(url, source_file)
                except OSError as error:
                    skipped.append({"artifact": url, "reason": f"원장 이미지를 받지 못했다: {error}"})
                    continue
            try:
                # 원장 사진은 자르지 않고 통째로 둔다 — 경계를 재현할 일이 없으니 번호를 매긴 디코드로 읽지 않는다.
                # 그 디코드는 CMYK·WebP·작은 대표 사진을 거절해, 대상 상품의 기준 사진이 조용히 빠진다.
                image = tile_rule.decode_for_display(source_file)
            except OSError as error:
                skipped.append({"artifact": url, "reason": f"원장 이미지를 열지 못했다: {error}"})
                continue
            width, height = image.size
            match = SCENE_ID.match(scene_id)
            # 원장의 상세 목록은 번호를 매긴 실행의 원본 목록과 다른 출처다. 원장에 묻는 것은 갤러리에 그 D가
            # 없을 때뿐이므로, D번째 원장 사진이 그 실행의 D번째 원본이라는 것은 **늘** 추측이다. 통째로 두고
            # «자리 추측»이라 적는다. 원장 사진이 갤러리의 **다른** D와 같으면 자리가 어긋났다는 증거라 쓰지 않는다.
            if match and url in known_sources.values():
                other = next(d for d, known in known_sources.items() if known == url)
                skipped.append({"artifact": scene_id,
                                "reason": f"원장의 D{match.group(1)}은 갤러리 {other}와 같은 사진 — 자리가 어긋났다"})
                continue
            guessed = bool(match)
            top, bottom = 0, height
            product_dir.mkdir(parents=True, exist_ok=True)
            target = product_dir / f"{safe_name(scene_id)}.jpg"
            save_tile(image.crop((0, top, width, bottom)), target, args.max_width)
            scenes.append(
                {
                    "sceneId": scene_id,
                    "path": str(target.relative_to(run_root)),
                    "sourceUrl": url,
                    "source": "LEDGER",
                    "positionGuessed": guessed,
                    "crop": {"top": top, "bottom": bottom, "width": width},
                    "citedByJudge": scene_id in cited,
                    "judgeSceneNote": scene_notes.get(scene_id),
                }
            )

        # 원장이 보충한 «타일 좌표가 아닌 이름»의 장면은 대개 대표 이미지다. 그것이 갤러리의
        # 썸네일보다 크면 대상의 정의를 그쪽으로 올린다 — 실제로 갤러리에는 200px밖에 없고
        # 원장 경로로 들어온 같은 상품 대표 사진이 500px이던 건이 있었다.
        # **두 경로가 같은 것을 다른 크기로 준다는 사실 자체가, 큰 쪽을 골라야 하는 이유다.**
        for scene in scenes:
            if SCENE_ID.match(str(scene["sceneId"])):
                continue
            try:
                with Image.open(run_root / scene["path"]) as named:
                    named.load()
                    width, height = named.size
            except OSError:
                continue
            if width * height <= best_area:
                continue
            best_area = width * height
            product_dir.mkdir(parents=True, exist_ok=True)
            (product_dir / "_reference.jpg").write_bytes((run_root / scene["path"]).read_bytes())
            reference = {
                "path": str((product_dir / "_reference.jpg").relative_to(run_root)),
                "sourceUrl": scene.get("sourceUrl"),
                "size": [width, height],
                # 갤러리가 아니라 이 장면에서 왔다는 것을 밝힌다. 어디서 온 정의인지
                # 모르면 다음 사람이 대조 결과를 되짚을 수 없다.
                "from": str(scene["sceneId"]),
            }

        products.append(
            {
                "productKey": product_key,
                "productName": row.get("productName") or verdict.get("productName"),
                # 이 상품을 **어느 폭에서** 받았는가. 상품마다 다를 수 있다 —
                # 다투는 상품만 크게 다시 받는 것이 이 스크립트의 사용법이기 때문이다.
                # 색인 전체의 `selector.maxWidth` 하나로는 그 사실을 말할 수 없다.
                "maxWidth": args.max_width,
                # 이 상품의 타일을 어느 판으로 잘랐는가. 판이 다른 조각끼리 한 색인에 섞이지 않게 한다.
                "tileRule": tile_rule.rule(version, decoder),
                "reference": reference,
                "goldLabel": verdict.get("goldLabel") or row.get("goldLabel"),
                "observedLabel": verdict.get("observedLabel") or row.get("observedLabel"),
                "owner": verdict.get("owner"),
                "ownerAction": verdict.get("ownerAction"),
                "policyRule": verdict.get("policyRule"),
                "policyStrength": verdict.get("policyStrength"),
                "judgeClaim": row.get("detailEvidence"),
                "judgeSceneNotes": scene_notes,
                "citedSceneIds": cited,
                "unresolvedSceneIds": sorted(set(unresolved) | set(refused_citations)),
                # 운영 인용 문법(두 자리 이상)을 못 지나 근거로 세지 않은 인용 — 운영도 세지 않는다.
                "refusedCitations": refused_citations,
                "scenes": sorted(scenes, key=lambda item: item["sceneId"]),
            }
        )

    declared_rules = {key: (row.get("tileRule") or {}) for key, row in gallery.items()}
    carried = (
        carry_over(out_dir / "index.json", products, summary.get("generatedAt"), declared_rules)
        if args.product
        else []
    )
    products = sorted(products + carried, key=lambda product: product["productKey"])

    index = {
        "basedOn": summary.get("generatedAt"),
        "selector": {
            "owner": args.owner,
            "products": args.product,
            "allScenes": args.all_scenes,
            # 저장 폭은 판독 결과를 바꾼다. 어느 해상도에서 나온 판독인지 남지 않으면
            # 같은 장면의 다른 답 둘 중 무엇이 맞는지 나중에 가릴 수 없다.
            # **이번 실행의 폭이다.** 이어 붙인 상품은 자기 `maxWidth`를 따로 갖는다.
            "maxWidth": args.max_width,
            "ledger": args.ledger,
            "fanout": args.fanout,
        },
        # 판은 상품마다 갤러리가 선언한다. 색인 전체에는 이번에 쓴 판들만 적는다.
        "tileRules": sorted({f'{(product.get("tileRule") or {}).get("version") or "undeclared"}/'
                             f'{(product.get("tileRule") or {}).get("decoder") or "undeclared"}' for product in products}),
        "products": products,
        "skipped": skipped,
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 판독자에게 줄 좁은 시야. 실행이 낸 라벨·근거 문장·장면별 판독을 **뺀** 목록이다.
    # "보지 말라"고 적어 두는 것과 볼 수 없게 하는 것은 다르다. 정답을 알고 사진을 보면
    # 그 정답이 보이고, 그것이 실행 판독기가 실패한 방식이다. 같은 실패를 심사에서 반복하지
    # 않으려면 금지가 아니라 입력으로 막아야 한다.
    # 과제를 무엇 하나로 묶는가. 기본은 **상품**이다.
    #
    # 판독자를 띄우는 값이 이미지 한 장 값보다 훨씬 크다 — 문맥의 대부분은 시스템 프롬프트와
    # 규칙이고 사진은 그 옆에 얹히는 정도다. 그래서 비용을 정하는 것은 해상도가 아니라
    # **판독자 수**이고, 타일마다 하나씩 띄우면 총량이 상품 단위의 두 배 가까이 든다.
    #
    # 상품 단위로 묶어도 상품 사이는 여전히 격리된다. 잃는 것은 한 상품 **안**에서
    # 앞 장면의 인상이 뒷 장면에 번지는 것을 막는 일뿐이다. 그것이 실제로 위험한 때는
    # 그 상품의 장면들이 서로 다른 답을 주장할 때이고, 그때만 `--fanout tile`로 쪼갠다.
    if args.fanout == "tile":
        tasks = [
            {
                "taskId": f"{product['productKey']}#{scene['sceneId']}",
                "productKey": product["productKey"],
                "productName": product["productName"],
                "reference": (product.get("reference") or {}).get("path"),
                "sceneId": scene["sceneId"],
                "path": scene["path"],
            }
            for product in products
            for scene in product["scenes"]
        ]
    else:
        tasks = [
            {
                "taskId": product["productKey"],
                "productKey": product["productKey"],
                "productName": product["productName"],
                # 대상이 무엇인지는 이름이 아니라 이 사진이 말한다.
                "reference": (product.get("reference") or {}).get("path"),
                "scenes": [
                    # 자리를 추측한 원장 사진이면 판독자도 알아야 한다 — «이 장면이 그 번호의 사진»이라고 믿지 않게.
                    {"sceneId": scene["sceneId"], "path": scene["path"],
                     **({"positionGuessed": True} if scene.get("positionGuessed") else {})}
                    for scene in product["scenes"]
                ],
            }
            for product in products
            if product["scenes"]
        ]
    reader_view = {
        "basedOn": index["basedOn"],
        "fanout": args.fanout,
        "tasks": tasks,
        "missing": [
            {"productKey": product["productKey"], "sceneIds": product["unresolvedSceneIds"]}
            for product in products
            if product["unresolvedSceneIds"]
        ],
    }
    (out_dir / "reader-view.json").write_text(
        json.dumps(reader_view, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(out_dir / "index.json")
    print(out_dir / "reader-view.json")
    print(f"  판독 과제 {len(reader_view['tasks'])}건 ({args.fanout} 단위)")
    carried_keys = {product["productKey"] for product in carried}
    for product in products:
        if product["productKey"] in carried_keys:
            print(f"  {product['productKey']}  이어 붙임 (폭 {product.get('maxWidth', '?')})")
            continue
        from_ledger = sum(1 for scene in product["scenes"] if scene.get("source") == "LEDGER")
        note = f" (원장 보완 {from_ledger})" if from_ledger else ""
        size = ((product.get("reference") or {}).get("size")) or []
        if size and max(size) < REFERENCE_MIN_LONG_SIDE:
            note += f"  ⚠ 대표 사진이 작다 {size[0]}x{size[1]} — 대상 대조가 흔들릴 수 있다"
        print(f"  {product['productKey']}  장면 {len(product['scenes'])}개{note}")
    for item in skipped:
        print(f"  SKIPPED {item['artifact']}: {item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
