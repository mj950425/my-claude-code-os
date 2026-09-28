#!/usr/bin/env python3
"""가방 골든셋 검수(gtTask)의 사진 색인을 만든다 — `runs/bag-category-gender/golden/bag-image-index.jsonl`.

감사 사이클의 갤러리(`bag-product-gallery.jsonl`)는 상세 사진을 원격 주소로만 갖고 있어 판독 AI가 열 수 없다.
그래서 두 곳에서 로컬 파일을 모은다.

- 대표 썸네일 — 사이클 가져오기가 이미 받아 둔 `runs/bag-category-gender/asset/thumbnails/`.
- 상세 조각 — 원본 저장소의 `work/hat-bag-policy-20260917/bags-detail-manifest.json`이 가리키는 조각
  (그날 평가 하네스가 잘라 둔 것). 원본 저장소가 `work/`를 지우면 사라지므로 `runs/…/asset/details/`로 복사한다.

찾지 못한 참조는 조용히 버리지 않고 표지(`bag-image-index.manifest.json`)의 `droppedImageReferences`에 남긴다.
GT를 읽기만 한다 — 어떤 원장도 쓰지 않는다.

## 주소 확인 — 사진 파일 없이도 돌게

사진 파일(`asset/`)은 git에 없어 서버로 옮기려면 GB 단위를 옮겨야 한다. 그래서 사진마다 원본 주소를 색인에 싣고,
하네스(`gt_images`)는 로컬 파일이 없으면 그 주소로 받는다. 단 **로컬 파일과 같은 사진임을 여기서 대 본 주소만** 싣는다.

- 대표 썸네일 — 원본 저장소 `bags-product-context-gt-*.jsonl`의 `prodRepresentativeThumbnailUrl`. 로컬 파일은 240px로 줄인
  사본이라 받은 사진을 같은 크기로 줄여 대 본다. 실제로 몇 건은 **다른 사진**이었다(GT를 만든 날의 대표 사진과 수집한 날의
  사진이 다름) — 그런 주소는 싣지 않는다.
- 상세 조각 — 주소는 자르기 전 원본이다. 받은 원본을 그날의 판으로 다시 잘라 같은 번호의 조각과 대 본다.

같으면 주소와 **받은 바이트의 해시**(`sourceSha256`)를 적는다. 하네스는 그 해시가 맞는 바이트만 쓴다 — 나중에 판매자가
사진을 바꾸면 조용히 다른 사진이 끼는 대신 «못 받은 사진»이 된다. 대 보지 못했거나 다른 주소는 `sourceUrl`만 남기고
해시를 적지 않는다(하네스가 받지 않는다). 그 목록은 표지의 `urlVerification`에 있다. `--no-verify-urls`면 대 보지 않는다
(네트워크가 없을 때) — 그러면 해시가 하나도 없어 서버에서는 사진이 없는 과제가 된다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude" / "os").is_dir():
            return parent
    raise SystemExit("프로젝트 루트(.claude/os가 있는 폴더)를 찾지 못했습니다.")


PROJECT_ROOT = _find_project_root()
sys.path.insert(0, str(PROJECT_ROOT / ".claude" / "os" / "common"))
sys.path.insert(0, str(PROJECT_ROOT / ".claude" / "os" / "engine" / "scripts"))
import gt_images  # noqa: E402 — 하네스가 받을 때와 같은 방법으로 받는다(인증서·헤더·캐시 자리)
import tile_rule  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / "profile.json"
GT = PROJECT_ROOT / ".claude/gt/bag-category-gender/gt.jsonl"
OUTPUT_ROOT = PROJECT_ROOT / ".claude/os/runs/bag-category-gender"
GALLERY = OUTPUT_ROOT / "golden/bag-product-gallery.jsonl"
INDEX = OUTPUT_ROOT / "golden/bag-image-index.jsonl"
MANIFEST = OUTPUT_ROOT / "golden/bag-image-index.manifest.json"
HARNESS = Path("tool/image-gender/gt-harness")
DETAIL_MANIFEST = HARNESS / "work/hat-bag-policy-20260917/bags-detail-manifest.json"
# 그날 평가 하네스가 쓰던 타일 판. 조각을 만든 쪽이 판을 적어 두지 않아 날짜로 정한다(tile_rule.rule_for_date).
DETAIL_TILED_ON = "2026-09-17"
DETAIL_DIR = "asset/details"
# 대표 썸네일 주소가 있는 GT 파일. 날짜가 가장 늦은 판을 쓴다.
CONTEXT_GT_GLOB = "bags-product-context-gt-*.jsonl"
CONTEXT_THUMBNAIL_FIELD = "prodRepresentativeThumbnailUrl"
# 같은 사진인지 가르는 선 — 채널별 평균 절대 차이(0~255)의 최댓값. JPEG를 다시 저장하거나 240px로 줄인 사본과의
# 차이는 10 안팎이었고, 다른 사진은 80을 넘었다(2026-09-28 가방 썸네일 400장·상세 조각 표본). 그 사이에 선을 둔다.
SAME_PICTURE_MAX_DIFF = 20.0
VERIFY_WORKERS = 8


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def picture_difference(remote: Any, local_path: Path) -> float | None:
    """받은 사진(또는 다시 자른 조각)과 로컬 파일의 차이. 크기가 다르면 로컬 크기로 줄여 대 본다(240px 썸네일 사본)."""
    from PIL import Image, ImageChops, ImageStat

    local = Image.open(local_path).convert("RGB")
    remote = remote.convert("RGB")
    if remote.size != local.size:
        if abs(remote.size[0] / remote.size[1] - local.size[0] / local.size[1]) > 0.02:
            return None  # 비율이 다르면 다른 사진이거나 다른 조각이다
        remote = remote.resize(local.size, Image.LANCZOS)
    return max(ImageStat.Stat(ImageChops.difference(remote, local)).mean)


def verify_urls(images: list[tuple[str, dict[str, Any]]], cache: Path, detail_version: str) -> dict[str, Any]:
    """색인의 사진마다 주소를 받아 로컬 파일과 대 보고, 같으면 `sourceSha256`을 적는다. 받은 원본은 주소마다 한 번만 받는다."""
    urls = sorted({str(image["sourceUrl"]) for _, image in images if image.get("sourceUrl")})

    def get(url: str) -> tuple[str, Path | str]:
        try:
            return url, gt_images.fetch(url, cache)
        except gt_images.FetchError as error:
            return url, str(error)

    with ThreadPoolExecutor(VERIFY_WORKERS) as pool:
        fetched = dict(pool.map(get, urls))
    verified, rejected, failed = 0, [], []
    for key, image in images:
        url = image.get("sourceUrl")
        if not url:
            continue
        got = fetched.get(str(url))
        if not isinstance(got, Path):
            failed.append({"productKey": key, "imageId": image["imageId"], "reason": got})
            continue
        try:
            if image["role"] == "DETAIL_TILE":
                original = tile_rule.decode(got, tile_rule.HARNESS_PILLOW)
                piece = tile_rule.parse_piece(image["imageId"])
                ranges = tile_rule.tile_ranges(original, detail_version)
                if not piece or not 1 <= piece[1] <= len(ranges):
                    rejected.append({"productKey": key, "imageId": image["imageId"], "reason": "원본을 잘라도 그 번호의 조각이 없습니다"})
                    continue
                top, bottom = ranges[piece[1] - 1]
                remote = original.crop((0, top, original.size[0], bottom))
            else:
                remote = tile_rule.decode_for_display(got)
            difference = picture_difference(remote, OUTPUT_ROOT / image["file"])
        except Exception as error:  # noqa: BLE001 — 한 장이 가져오기 전체를 멈추게 두지 않는다
            failed.append({"productKey": key, "imageId": image["imageId"], "reason": f"{type(error).__name__}: {error}"})
            continue
        if difference is None or difference > SAME_PICTURE_MAX_DIFF:
            rejected.append({"productKey": key, "imageId": image["imageId"], "url": url,
                             "difference": None if difference is None else round(difference, 2)})
            continue
        image["sourceSha256"] = gt_images.sha256_bytes(got.read_bytes())
        verified += 1
    return {"threshold": SAME_PICTURE_MAX_DIFF, "urls": len(urls), "verified": verified,
            "rejected": rejected, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-repo", type=Path,
                        default=(PROJECT_ROOT / json.loads(PROFILE.read_text(encoding="utf-8"))["source"]["repository"]).resolve(),
                        help="원본 저장소(core-catalog-platfom). 기본은 프로필의 source.repository")
    parser.add_argument("--no-verify-urls", action="store_true",
                        help="주소를 받아 대 보지 않는다(네트워크가 없을 때). 해시가 없어 서버에서는 사진을 못 받는다")
    args = parser.parse_args()
    source_repo = args.source_repo.resolve()
    detail_manifest_path = source_repo / DETAIL_MANIFEST
    if not detail_manifest_path.is_file():
        raise SystemExit(f"상세 조각 목록이 없습니다: {detail_manifest_path}")
    details = json.loads(detail_manifest_path.read_text(encoding="utf-8"))
    gallery = {row["productKey"]: row for row in read_jsonl(GALLERY)} if GALLERY.is_file() else {}
    gt_rows = read_jsonl(GT)
    context_files = sorted((source_repo / HARNESS / "data").glob(CONTEXT_GT_GLOB))
    context_path = context_files[-1] if context_files else None
    thumbnail_urls: dict[str, str] = {}
    for row in read_jsonl(context_path) if context_path else []:
        url = str(row.get(CONTEXT_THUMBNAIL_FIELD) or "")
        if url.startswith(("http://", "https://")):
            thumbnail_urls.setdefault(str(row.get("productKey")), url)

    rows, dropped, copies = [], [], 0
    (OUTPUT_ROOT / DETAIL_DIR).mkdir(parents=True, exist_ok=True)
    for gt in gt_rows:
        key, goods = str(gt["productKey"]), str(gt.get("goodsNo") or "")
        images: list[dict[str, Any]] = []
        for order, thumb in enumerate((gallery.get(key) or {}).get("thumbnails") or [], start=1):
            file = str(thumb.get("url") or "")
            if file.startswith("asset/") and (OUTPUT_ROOT / file).is_file():
                # 주소는 대표 썸네일(첫 장)만 안다 — 둘째 장부터는 로컬 파일뿐이다.
                url = thumbnail_urls.get(key) if order == 1 else None
                images.append({"role": "THUMBNAIL", "order": order, "imageId": f"T{order:02d}", "file": file,
                               **({"sourceUrl": url} if url else {})})
            else:
                dropped.append({"productKey": key, "role": "THUMBNAIL", "reference": file})
        for order, tile in enumerate(details.get(goods) or [], start=1):
            source = source_repo / HARNESS / str(tile.get("localPath") or "")
            if not source.is_file():
                dropped.append({"productKey": key, "role": "DETAIL_TILE", "reference": tile.get("localPath")})
                continue
            target = OUTPUT_ROOT / DETAIL_DIR / source.name
            if not target.is_file():
                shutil.copy2(source, target)
                copies += 1
            images.append({"role": "DETAIL_TILE", "order": order,
                           "imageId": str(tile.get("id") or "").split("-", 1)[-1] or f"D{order:02d}",
                           "file": f"{DETAIL_DIR}/{source.name}", "sourceUrl": tile.get("sourceUrl")})
        rows.append({"productKey": key, "goodsNo": goods, "standardCategory": gt.get("standardCategory"), "images": images})

    detail_version = tile_rule.rule_for_date(DETAIL_TILED_ON)
    verification = None if args.no_verify_urls else verify_urls(
        [(row["productKey"], image) for row in rows for image in row["images"]],
        OUTPUT_ROOT / gt_images.URL_CACHE, detail_version)
    INDEX.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    manifest = {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "by": str(Path(__file__).relative_to(PROJECT_ROOT)),
        "sources": {
            "gt": {"path": str(GT.relative_to(PROJECT_ROOT)), "sha256": sha256_file(GT)},
            "detailManifest": {"path": str(DETAIL_MANIFEST), "sha256": sha256_file(detail_manifest_path)},
            "gallery": {"path": str(GALLERY.relative_to(PROJECT_ROOT))} if GALLERY.is_file() else None,
            "thumbnailUrls": {"path": str(context_path.relative_to(source_repo)), "sha256": sha256_file(context_path),
                              "field": CONTEXT_THUMBNAIL_FIELD} if context_path else None,
        },
        "detailTileRule": tile_rule.rule(detail_version, tile_rule.HARNESS_PILLOW),
        "urlVerification": verification,
        "products": len(rows),
        "productsWithImages": sum(1 for row in rows if row["images"]),
        "copiedThisRun": copies,
        "droppedImageReferences": dropped,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {k: manifest[k] for k in ("products", "productsWithImages", "copiedThisRun")} | {"dropped": len(dropped)}
    if verification:
        summary |= {"urlsVerified": verification["verified"], "urlsRejected": len(verification["rejected"]),
                    "urlsFailed": len(verification["failed"])}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
