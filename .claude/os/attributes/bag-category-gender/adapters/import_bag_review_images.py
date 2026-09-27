#!/usr/bin/env python3
"""가방 골든셋 검수(gtTask)의 사진 색인을 만든다 — `runs/bag-category-gender/golden/bag-image-index.jsonl`.

감사 사이클의 갤러리(`bag-product-gallery.jsonl`)는 상세 사진을 원격 주소로만 갖고 있어 판독 AI가 열 수 없다.
그래서 두 곳에서 로컬 파일을 모은다.

- 대표 썸네일 — 사이클 가져오기가 이미 받아 둔 `runs/bag-category-gender/asset/thumbnails/`.
- 상세 조각 — 원본 저장소의 `work/hat-bag-policy-20260917/bags-detail-manifest.json`이 가리키는 조각
  (그날 평가 하네스가 잘라 둔 것). 원본 저장소가 `work/`를 지우면 사라지므로 `runs/…/asset/details/`로 복사한다.

찾지 못한 참조는 조용히 버리지 않고 표지(`bag-image-index.manifest.json`)의 `droppedImageReferences`에 남긴다.
GT를 읽기만 한다 — 어떤 원장도 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-repo", type=Path,
                        default=(PROJECT_ROOT / json.loads(PROFILE.read_text(encoding="utf-8"))["source"]["repository"]).resolve(),
                        help="원본 저장소(core-catalog-platfom). 기본은 프로필의 source.repository")
    args = parser.parse_args()
    source_repo = args.source_repo.resolve()
    detail_manifest_path = source_repo / DETAIL_MANIFEST
    if not detail_manifest_path.is_file():
        raise SystemExit(f"상세 조각 목록이 없습니다: {detail_manifest_path}")
    details = json.loads(detail_manifest_path.read_text(encoding="utf-8"))
    gallery = {row["productKey"]: row for row in read_jsonl(GALLERY)} if GALLERY.is_file() else {}
    gt_rows = read_jsonl(GT)

    rows, dropped, copies = [], [], 0
    (OUTPUT_ROOT / DETAIL_DIR).mkdir(parents=True, exist_ok=True)
    for gt in gt_rows:
        key, goods = str(gt["productKey"]), str(gt.get("goodsNo") or "")
        images: list[dict[str, Any]] = []
        for order, thumb in enumerate((gallery.get(key) or {}).get("thumbnails") or [], start=1):
            file = str(thumb.get("url") or "")
            if file.startswith("asset/") and (OUTPUT_ROOT / file).is_file():
                images.append({"role": "THUMBNAIL", "order": order, "imageId": f"T{order:02d}", "file": file})
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

    INDEX.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    manifest = {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "by": str(Path(__file__).relative_to(PROJECT_ROOT)),
        "sources": {
            "gt": {"path": str(GT.relative_to(PROJECT_ROOT)), "sha256": sha256_file(GT)},
            "detailManifest": {"path": str(DETAIL_MANIFEST), "sha256": sha256_file(detail_manifest_path)},
            "gallery": {"path": str(GALLERY.relative_to(PROJECT_ROOT))} if GALLERY.is_file() else None,
        },
        "detailTileRule": tile_rule.rule(tile_rule.rule_for_date(DETAIL_TILED_ON), tile_rule.HARNESS_PILLOW),
        "products": len(rows),
        "productsWithImages": sum(1 for row in rows if row["images"]),
        "copiedThisRun": copies,
        "droppedImageReferences": dropped,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("products", "productsWithImages", "copiedThisRun")}
                     | {"dropped": len(dropped)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
