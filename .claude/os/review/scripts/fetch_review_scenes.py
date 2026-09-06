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
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 긴 상세 원본을 폭×1.5 높이로 자르는 타일 경계 규칙. 이 값을 여기 적는 것은
# 산출물이 `D01T06`이라고만 말하고 그것이 어느 픽셀인지는 말하지 않기 때문이다.
# 규칙이 갈라지면 다른 조각을 보게 되므로, 계산한 타일 수가 산출물이 참조한
# 최대 타일 번호를 담지 못하면 그 원본은 자르지 않고 건너뛴다(_tile_ranges 참조).
TILE_ASPECT = 1.5
MIN_TILE_PX = 64

SCENE_ID = re.compile(r"^D(\d+)T(\d+)$")
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


def _tile_ranges(width: int, height: int) -> list[tuple[int, int]]:
    """원본 하나가 몇 개의 타일로 갈라지는가. 외부 파이프라인의 경계 규칙과 같다."""
    tile_height = max(MIN_TILE_PX, round(width * TILE_ASPECT))
    if height <= tile_height * 1.25:
        return [(0, height)]
    ranges: list[tuple[int, int]] = []
    for top in range(0, height, tile_height):
        bottom = min(top + tile_height, height)
        if bottom - top >= MIN_TILE_PX:
            ranges.append((top, bottom))
    return ranges


def safe_name(product_key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", product_key)


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
        (out_dir / "index.json").write_text(
            json.dumps(
                {"basedOn": summary.get("generatedAt"), "products": [], "skipped": skipped},
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
        details = [image for image in (images.get("details") or []) if isinstance(image, dict)]
        if not details:
            skipped.append({"artifact": product_key, "reason": "갤러리에 상세 타일이 없다"})
            continue

        scene_notes = row.get("sceneNotes") if isinstance(row.get("sceneNotes"), dict) else {}
        cited = list(
            dict.fromkeys(
                [str(value) for value in (row.get("policyEvidenceSceneIds") or [])]
                + [str(key) for key in scene_notes]
            )
        )
        wanted = [str(image.get("sceneId")) for image in details] if args.all_scenes else cited

        # `D01T06`은 원본 `D01`의 6번째 조각이다. 갤러리는 판독기가 **고른** 타일만 싣지만,
        # 같은 원본의 다른 타일이 하나라도 있으면 그 URL로 빠진 타일도 되짚을 수 있다.
        # 이걸 안 하면 정작 반박해야 할 장면(고르지 않아 목록에 없는 쪽)만 늘 빠진다.
        url_by_source: dict[str, str] = {}
        for image in details:
            match = SCENE_ID.match(str(image.get("sceneId") or ""))
            url = str(image.get("url") or "")
            if match and url.startswith("http"):
                url_by_source.setdefault(f"D{match.group(1)}", url)

        unresolved: list[str] = []
        by_source: dict[str, list[str]] = {}
        for scene_id in dict.fromkeys(wanted):
            match = SCENE_ID.match(scene_id)
            url = url_by_source.get(f"D{match.group(1)}") if match else None
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
        for url, scene_ids in by_source.items():
            indexes = [SCENE_ID.match(scene) for scene in scene_ids]
            if not all(indexes):
                unresolved.extend(scene for scene, m in zip(scene_ids, indexes) if not m)
                scene_ids = [scene for scene, m in zip(scene_ids, indexes) if m]
                if not scene_ids:
                    continue
            source_file = source_cache / f"{safe_name(product_key)}-{abs(hash(url)) % (10**10)}.jpg"
            if not source_file.exists():
                try:
                    download(url, source_file)
                except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
                    skipped.append({"artifact": url, "reason": f"내려받지 못했다: {error}"})
                    continue
            try:
                image = Image.open(source_file)
                image.load()
            except OSError as error:
                skipped.append({"artifact": url, "reason": f"이미지를 열지 못했다: {error}"})
                continue
            width, height = image.size
            ranges = _tile_ranges(width, height)
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
                        "crop": {"top": top, "bottom": bottom, "width": width},
                        "citedByJudge": scene_id in cited,
                        "judgeSceneNote": scene_notes.get(scene_id),
                    }
                )

        for scene_id, url in ledger_urls.items():
            source_file = source_cache / f"{safe_name(product_key)}-{safe_name(scene_id)}.jpg"
            if not source_file.exists():
                try:
                    download(url, source_file)
                except OSError as error:
                    skipped.append({"artifact": url, "reason": f"원장 이미지를 받지 못했다: {error}"})
                    continue
            try:
                image = Image.open(source_file)
                image.load()
            except OSError as error:
                skipped.append({"artifact": url, "reason": f"원장 이미지를 열지 못했다: {error}"})
                continue
            width, height = image.size
            match = SCENE_ID.match(scene_id)
            if match:
                ranges = _tile_ranges(width, height)
                position = int(match.group(2))
                if position > len(ranges):
                    skipped.append(
                        {"artifact": url, "reason": f"원장 원본이 T{position:02d}를 담지 못한다"}
                    )
                    continue
                top, bottom = ranges[position - 1]
            else:
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
                    "crop": {"top": top, "bottom": bottom, "width": width},
                    "citedByJudge": scene_id in cited,
                    "judgeSceneNote": scene_notes.get(scene_id),
                }
            )

        products.append(
            {
                "productKey": product_key,
                "productName": row.get("productName") or verdict.get("productName"),
                "goldLabel": verdict.get("goldLabel") or row.get("goldLabel"),
                "observedLabel": verdict.get("observedLabel") or row.get("observedLabel"),
                "owner": verdict.get("owner"),
                "ownerAction": verdict.get("ownerAction"),
                "policyRule": verdict.get("policyRule"),
                "policyStrength": verdict.get("policyStrength"),
                "judgeClaim": row.get("detailEvidence"),
                "judgeSceneNotes": scene_notes,
                "citedSceneIds": cited,
                "unresolvedSceneIds": sorted(set(unresolved)),
                "scenes": sorted(scenes, key=lambda item: item["sceneId"]),
            }
        )

    index = {
        "basedOn": summary.get("generatedAt"),
        "selector": {
            "owner": args.owner,
            "products": args.product,
            "allScenes": args.all_scenes,
            # 저장 폭은 판독 결과를 바꾼다. 어느 해상도에서 나온 판독인지 남지 않으면
            # 같은 장면의 다른 답 둘 중 무엇이 맞는지 나중에 가릴 수 없다.
            "maxWidth": args.max_width,
            "ledger": args.ledger,
        },
        "tileRule": {"aspect": TILE_ASPECT, "minTilePx": MIN_TILE_PX},
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
    # 목록이 **타일 단위로 평평하다.** 상품 단위로 묶어 주면 판독자 하나가 여러 장을 한꺼번에
    # 들고 앞 장면의 인상이 뒷 장면에 번진다("아까 남성이 있었으니 이것도"). 한 번에 한 장이면
    # 각 판독이 다른 장면에 오염되지 않고, 문맥이 짧아 판독 한 건의 비용도 작다.
    reader_view = {
        "basedOn": index["basedOn"],
        "tasks": [
            {
                "taskId": f"{product['productKey']}#{scene['sceneId']}",
                "productKey": product["productKey"],
                "productName": product["productName"],
                "sceneId": scene["sceneId"],
                "path": scene["path"],
            }
            for product in products
            for scene in product["scenes"]
        ],
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
    print(f"  판독 과제 {len(reader_view['tasks'])}건")
    for product in products:
        from_ledger = sum(1 for scene in product["scenes"] if scene.get("source") == "LEDGER")
        note = f" (원장 보완 {from_ledger})" if from_ledger else ""
        print(f"  {product['productKey']}  장면 {len(product['scenes'])}개{note}")
    for item in skipped:
        print(f"  SKIPPED {item['artifact']}: {item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
