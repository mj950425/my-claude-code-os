#!/usr/bin/env python3
"""증분 데이터 모으기 — 운영에서 새 상품을 뽑아 증분 검수의 입력 두 장(건 · 사진 색인)을 만든다. 속성을 모른다.

    python3 incr_collect.py sql        --task <id> [--per-platform N]        # ① 후보 조회문(mysql-query 스킬이 --json으로 돌린다)
    python3 incr_collect.py detail-sql --task <id> --rows <①결과> [--limit N] # ② 고른 건의 상세 설명 조회문(상세가 필요한 과제만)
    python3 incr_collect.py build      --task <id> --rows <①결과> [--details <②결과>] --name <이름> [--limit N] [--split K] [--push]

## 무엇이 과제마다 다른가 — 프로필의 `incremental` 블록

```jsonc
"incremental": {
  "schemaVersion": "incr-source-v1",
  "platforms": ["MUSINSA", "EGOOCM"],          // 뽑을 판매 플랫폼(운영 seller_product.platform_code)
  "categoryPrefix": "<대분류>>",                 // 표준 카테고리 경로의 앞머리 — 이 과제가 보는 상품
  "key": "{platform}:{goodsNo}",               // 건 키를 만드는 틀(GT의 키와 같은 모양) — {platform} · {goodsNo}
  "hosts": {"MUSINSA": "https://…", "EGOOCM": "https://…"},   // 대표 사진 경로(/images/…) 앞에 붙일 주소
  "pdp": {"MUSINSA": "https://…/{goodsNo}"},   // 상품 페이지 주소 틀(화면의 «상품 페이지»)
  "detail": true,                              // 상세 설명의 사진도 받아 조각으로 자른다(사진 색인이 조각 역할을 선언해야 한다)
  "maxDetailImages": 6                         // (선택) 상세 사진은 앞에서부터 이만큼만
}
```

키 모양·플랫폼·주소는 속성 팩이 안다(엔진은 어떤 속성인지 모른다). 엔진이 아는 것은 «대표 사진 한 장 + 상세 설명의 <img>들»이라는
운영 상품의 모양, 그리고 사진 색인의 모양(프로필 `gtTask.images` — 목록형이면 건마다 한 줄에 사진 목록, 아니면 사진마다 한 줄)이다.

## 왜 조회를 직접 하지 않나

엔진·팩에 운영 접속 정보를 두지 않는다. 조회는 읽기 전용 `mysql-query` 스킬이 하고, 이 스크립트는 그 조회문을 만들고(`sql`)
결과를 받아 사진을 받고 자른다(`build`). 조회문은 평가 하네스의 운영 입력 조회와 같은 자리에서 읽는다 —
대표 사진은 `seller_product.image`, 표준 카테고리는 `seller_product_standard_category`, 상세 설명은 CUVE 설명 표의
등록된 객체(`cuve_object`)만.

## 조각 — 운영과 같은 규칙으로

상세 사진은 긴 원본이라 그대로 주면 판독자가 못 읽는다. 사진 색인의 `preTiledRule`(판·디코더)로 `common/tile_rule`이 자르고
`DxxTyy`로 이름 붙인다 — 운영 `BatchImageComposer.tileRanges`의 이식이라 같은 번호가 같은 조각을 가리킨다. 따로 자르지 않는다.
대표 사진은 EXIF 회전을 적용해 저장한다(평가 하네스가 모은 사진과 같은 조건).

## 이미 있는 건은 빼고, 고르게

GT·과제의 사진 색인·지난 증분 묶음에 이미 있는 키는 뺀다. 남은 것에서 표준 카테고리 둘째 마디마다 돌아가며 고른다 —
최근 순으로만 자르면 한 종류가 묶음을 채운다. `--split K`면 K개 묶음으로 나눠 넣는다.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from catalog_profile import PROJECT_ROOT, output_root
from gt_task import TaskError, key_fields, key_of, load_gt, load_task, read_jsonl, resolve

sys.path.insert(0, str(PROJECT_ROOT / ".claude" / "os" / "common"))
import tile_rule  # noqa: E402

DESCRIPTION_TABLES = {"MUSINSA": "cuve_mss_product_description", "EGOOCM": "cuve_egoocm_product_description"}


class IncrCollectError(ValueError):
    """사람에게 그대로 보일 멈춤 문장."""


def source_of(profile: dict[str, Any]) -> dict[str, Any]:
    block = profile.get("incremental") or {}
    if not block:
        raise IncrCollectError(f"{profile.get('id')}: 프로필에 incremental 블록이 없습니다 — 이 과제는 운영에서 증분을 모으는 법을 선언하지 않았습니다.")
    for name in ("platforms", "categoryPrefix", "key", "hosts", "pdp"):
        if not block.get(name):
            raise IncrCollectError(f"{profile.get('id')}: incremental.{name}이 비었습니다.")
    unknown = [p for p in block["platforms"] if block.get("detail") and p not in DESCRIPTION_TABLES]
    if unknown:
        raise IncrCollectError(f"상세 설명 표를 모르는 플랫폼입니다: {', '.join(unknown)}")
    return block


def _literal(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def sql(profile: dict[str, Any], per_platform: int = 200, window: int = 400_000) -> str:
    """① 후보 조회문 — 플랫폼마다 최근에 표준 카테고리가 정해진 상품(상세 설명 없이 가볍게). 상세는 고른 뒤 ②로 따로 읽는다 —
    설명(HTML)을 후보 전체에 붙이면 조회가 도구의 제한 시간(30초)을 넘는다."""
    block = source_of(profile)
    parts = []
    for platform in block["platforms"]:
        parts.append(
            "(SELECT sp.id AS spid, sp.platform_code AS platform, sp.platform_product_id AS goodsNo, sp.name AS name,"
            " sp.image AS image, sc.full_name AS full_name"
            " FROM seller_product_standard_category spc"
            " JOIN seller_product sp ON sp.id = spc.seller_product_id"
            " JOIN standard_category sc ON sc.category_code = spc.standard_category_code AND sc.dt IS NULL"
            f" WHERE spc.id > (SELECT MAX(id) - {int(window)} FROM seller_product_standard_category)"
            " AND spc.dt IS NULL AND sp.dt IS NULL AND sp.image IS NOT NULL"
            f" AND sp.platform_code = {_literal(platform)} AND sc.full_name LIKE {_literal(block['categoryPrefix'] + '%')}"
            f" ORDER BY spc.id DESC LIMIT {int(per_platform)})")
    return "SELECT * FROM (" + " UNION ALL ".join(parts) + ") picked"  # 읽기 전용 도구는 SELECT로 시작하는 문만 받는다


def detail_sql(profile: dict[str, Any], rows: list[dict[str, Any]], limit: int) -> str | None:
    """② 상세 설명 조회문 — 고른 건(`pick`과 같은 규칙)의 CUVE 설명만, 등록된 객체만(평가 하네스와 같은 조건). 고를 건이 없으면 None."""
    block = source_of(profile)
    if not block.get("detail"):
        return None
    chosen = pick(rows, block, known_keys(profile, load_task(profile)), limit)
    parts = []
    for platform in block["platforms"]:
        ids = [str(row["spid"]) for row in chosen if row.get("platform") == platform and row.get("spid") is not None]
        if not ids:
            continue
        parts.append(
            f"(SELECT d.seller_product_id AS spid, d.description AS description FROM {DESCRIPTION_TABLES[platform]} d"
            " JOIN cuve_object o ON o.object_id = d.object_id AND o.data_status = 'REGISTERED'"
            " AND o.service_status = 'REGISTERED' AND o.dt IS NULL"
            f" WHERE d.seller_product_id IN ({', '.join(_literal(i) for i in ids)}))")
    return ("SELECT * FROM (" + " UNION ALL ".join(parts) + ") described") if parts else None


class _Images(HTMLParser):
    """상세 설명의 사진 주소 — 운영 ProductContentsImageUrlExtractor와 같은 순서(data-src → srcset 첫 후보 → src)."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = {name.lower(): value for name, value in attrs if value}
        source = values.get("data-src") or (values["srcset"].split(None, 1)[0] if values.get("srcset") else None) or values.get("src")
        if source and source.strip():
            self.urls.append(source.strip())


def detail_urls(html: str | None, host: str) -> list[str]:
    parser = _Images()
    parser.feed(html or "")
    base = re.match(r"^(https?://[^/]+)", host)
    out = []
    for url in parser.urls:
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/") and base:
            url = base.group(1) + url
        if url.startswith(("http://", "https://")):
            # 빈칸·한글이 든 주소는 그대로 보내면 받지 못한다 — 평가 하네스처럼 경로·질의만 퍼센트로 옮긴다(이미 옮긴 %xx는 둔다)
            parts = urlsplit(url)
            url = urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%:@!$&'()*+,;=~"),
                              quote(parts.query, safe="=&%:@!$'()*+,;/?~"), parts.fragment))
        if url.startswith(("http://", "https://", "file://")) and url not in out:
            out.append(url)
    return out


CACHE: Path | None = None  # build가 정한다 — 같은 주소를 두 번 받지 않게


def _fetch(url: str) -> bytes:
    """하네스가 사진을 받는 **같은 함수**(`gt_images.fetch` — 인증서 검사는 두고 AKI 없는 CDN 체인을 받는 설정, 다시 시도, 크기 제한).
    `file://`은 테스트용으로만 곧바로 읽는다."""
    import gt_images

    if url.startswith("file://"):
        with urllib.request.urlopen(url) as response:
            return response.read()
    try:
        return gt_images.fetch(url, CACHE or Path(".")).read_bytes()
    except gt_images.FetchError as error:
        raise OSError(str(error)) from None


def _upright(body: bytes) -> bytes:
    """EXIF 회전을 적용한다 — 태그가 없으면 받은 바이트 그대로(다시 인코딩하면 화질이 바뀐다)."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(body)) as image:
        if image.getexif().get(0x0112) in (None, 1):
            return body
        out = io.BytesIO()
        ImageOps.exif_transpose(image).convert("RGB").save(out, "JPEG", quality=92)
        return out.getvalue()


def known_keys(profile: dict[str, Any], task: dict[str, Any]) -> set[str]:
    """이미 있는 건 — GT · 과제의 사진 색인 · 지난 증분 묶음. 새로 넣을 것에서 뺀다."""
    import incr_review

    keys = set(load_gt(profile, task))
    images = task.get("images") or {}
    if images.get("path"):
        index_keys = key_fields(images.get("keyField") or task["keyField"])
        path = resolve(profile, images)
        if path.is_file():
            keys |= {key for key in (key_of(row, index_keys) for row in read_jsonl(path)) if key}
    for batch in incr_review.batches(profile):
        keys |= set(incr_review.input_rows(profile, task, batch))
    return keys




def pick(rows: list[dict[str, Any]], block: dict[str, Any], known: set[str], limit: int,
         skipped: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """이미 있는 키와 시험 등록 상품을 빼고, 표준 카테고리 둘째 마디마다 돌아가며 고른다."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    skipped = [] if skipped is None else skipped
    from incr_review import NOT_FOR_SALE as not_for_sale  # 시험 등록 상품 — 한 자리(incr_review)에서만 정한다
    for row in rows:
        key = block["key"].format(platform=row.get("platform"), goodsNo=row.get("goodsNo"))
        if key in known or key in seen or not row.get("image"):
            continue
        if not_for_sale.search(str(row.get("name") or "")):
            skipped.append({"key": key, "name": row.get("name")})  # 조용히 버리지 않는다 — build가 뺀 목록을 알린다
            continue
        seen.add(key)
        parts = str(row.get("full_name") or "").split(">")
        groups[parts[1] if len(parts) > 1 else parts[0]].append({**row, "key": key})
    picked: list[dict[str, Any]] = []
    while len(picked) < limit and any(groups.values()):
        for name in list(groups):
            if groups[name] and len(picked) < limit:
                picked.append(groups[name].pop(0))
    return picked


def _roles(task: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """사진 색인이 선언한 역할 — 대표 사진 역할(조각이 아닌 첫 역할), 조각 역할(preTiledRoles 첫 것)과 그 판."""
    spec = task.get("images") or {}
    tiled = [str(role) for role in spec.get("preTiledRoles") or []]
    plain = [str(role) for role in spec.get("roles") or [] if str(role) not in tiled and str(role) not in (spec.get("tileRoles") or [])]
    return (plain[0] if plain else None), (tiled[0] if tiled else None), spec.get("preTiledRule")


def build(profile: dict[str, Any], rows: list[dict[str, Any]], out: Path, limit: int,
          details: dict[str, str] | None = None) -> dict[str, Any]:
    """사진을 받고(상세는 조각으로) 건·사진 색인 두 장을 쓴다. 대표 사진을 못 받은 건은 뺀다 — 사진 없는 건은 AI가 못 읽는다."""
    from PIL import Image

    block = source_of(profile)
    task = load_task(profile)
    spec = task.get("images") or {}
    if not spec.get("path"):
        raise IncrCollectError(f"{profile['id']}: 과제가 사진 색인을 선언하지 않았습니다.")
    not_for_sale: list[dict[str, Any]] = []
    chosen = pick(rows, block, known_keys(profile, task), limit, not_for_sale)
    thumb_role, tile_role, tile_spec = _roles(task)
    if block.get("detail") and not tile_role:
        raise IncrCollectError(f"{profile['id']}: incremental.detail인데 사진 색인에 조각 역할(preTiledRoles)이 없습니다.")
    file_field, id_field, role_field = spec.get("fileField") or "file", spec.get("idField") or "id", spec.get("roleField") or "role"
    list_field = spec.get("listField")
    index_key = key_fields(spec.get("keyField") or task["keyField"])
    join = key_fields(spec.get("joinField") or task["keyField"])
    task_key = key_fields(task["keyField"])
    if len(task_key) != 1 or len(join) != 1 or len(index_key) != 1:
        raise IncrCollectError("복합 키 과제는 아직 모으지 못합니다.")
    img_dir = out / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    global CACHE
    CACHE = out.parent / ".cache"
    inputs, index, failed = [], [], []
    for row in chosen:
        platform, goods = str(row["platform"]), str(row["goodsNo"])
        host = block["hosts"].get(platform) or ""
        thumb_url = row["image"] if str(row["image"]).startswith(("http", "file:")) else host + str(row["image"])
        try:
            body = _upright(_fetch(thumb_url))
        except (OSError, ValueError) as error:
            failed.append({"key": row["key"], "reason": f"대표 사진을 받지 못했습니다: {error}"})
            continue
        thumb = img_dir / f"{hashlib.sha256(body).hexdigest()}.img"
        thumb.write_bytes(body)
        entries = [{file_field: str(thumb), id_field: "T01" if list_field else row["key"], "order": 1, "sourceUrl": thumb_url,
                    **({role_field: thumb_role} if thumb_role else {})}]
        if block.get("detail"):
            version, decoder = tile_rule.rule_named(tile_spec), tile_rule.decoder_named(tile_spec)
            html = (details or {}).get(str(row.get("spid"))) or row.get("description")
            for number, url in enumerate(detail_urls(html, host)[: int(block.get("maxDetailImages") or 6)], 1):
                try:
                    raw = _fetch(url)
                    source = img_dir / f"{hashlib.sha256(raw).hexdigest()}.src"
                    source.write_bytes(raw)
                    image = tile_rule.decode(source, decoder)
                    for piece, (top, bottom) in enumerate(tile_rule.tile_ranges(image, version), 1):
                        tile = image.crop((0, top, image.size[0], bottom))
                        name = tile_rule.scene_id(number, piece)
                        target = img_dir / f"{row['key'].replace(':', '-')}-{name}.jpg"
                        tile.convert("RGB").save(target, "JPEG", quality=90)
                        entries.append({file_field: str(target), id_field: name, role_field: tile_role, "order": number, "sourceUrl": url})
                except (OSError, ValueError, Image.DecompressionBombError) as error:
                    failed.append({"key": row["key"], "reason": f"상세 사진 {number}을 받지 못했습니다: {error}"})
        category = str(row.get("full_name") or "")
        record = {task_key[0]: row["key"], "platform": platform, "goodsNo": goods, "standardCategory": category,
                  "pdpUrl": str(block["pdp"].get(platform) or "").format(goodsNo=goods)}
        for column in (task.get("titleField"), *((task.get("evidence") or {}).get("textFields") or [])):
            if column and row.get("name"):
                record[str(column)] = str(row["name"])
        inputs.append(record)
        if list_field:
            index.append({index_key[0]: record.get(join[0], row["key"]), join[0]: record.get(join[0], row["key"]),
                          "standardCategory": category, list_field: entries})
        else:
            index.extend({index_key[0]: row["key"], **entry, "goodsNo": goods, "standardCategory": category} for entry in entries)
    out.mkdir(parents=True, exist_ok=True)
    (out / "input.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in inputs), encoding="utf-8")
    (out / "images.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in index), encoding="utf-8")
    tiles = sum(1 for row in index for entry in (row.get(list_field) or [row] if list_field else [row]) if entry.get(role_field) == tile_role)
    return {"candidates": len(rows), "picked": len(chosen), "written": len(inputs), "skippedNotForSale": not_for_sale, "detailTiles": tiles, "failed": failed,
            "input": str(out / "input.jsonl"), "images": str(out / "images.jsonl")}


def _split(profile: dict[str, Any], out: Path, parts: int) -> list[Path]:
    """한 번 받은 것을 K개 묶음으로 — 입력 줄을 번갈아 나누고, 사진 색인은 그 건의 줄(색인 키 = 입력의 이음 열)만 따라간다."""
    task = load_task(profile)
    spec = task.get("images") or {}
    index_key = key_fields(spec.get("keyField") or task["keyField"])[0]
    join = key_fields(spec.get("joinField") or task["keyField"])[0]
    rows, index = read_jsonl(out / "input.jsonl"), read_jsonl(out / "images.jsonl")
    folders = []
    for n in range(parts):
        mine = rows[n::parts]
        wanted = {str(row.get(join)) for row in mine}
        folder = out / f"part-{n + 1}"
        folder.mkdir(exist_ok=True)
        (folder / "input.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in mine), encoding="utf-8")
        chosen = [r for r in index if str(r.get(index_key)) in wanted]
        (folder / "images.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in chosen), encoding="utf-8")
        folders.append(folder)
    return folders


def _load(path: str) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    if "[" not in text:
        raise IncrCollectError(f"조회 결과가 비었거나 JSON이 아닙니다: {path} — {text.strip()[:200]}")
    return json.loads(text[text.index("["):])


def main() -> int:
    import gt_next
    import incr_review

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("sql", help="① 후보 조회문")
    q.add_argument("--task", required=True)
    q.add_argument("--per-platform", type=int, default=200)
    dq = sub.add_parser("detail-sql", help="② 고른 건의 상세 설명 조회문(상세가 필요한 과제만)")
    dq.add_argument("--task", required=True)
    dq.add_argument("--rows", required=True)
    dq.add_argument("--limit", type=int, default=100)
    b = sub.add_parser("build")
    b.add_argument("--task", required=True)
    b.add_argument("--rows", required=True, help="① 후보 조회 결과(mysql-query --json — 앞의 «(N rows)» 줄은 괜찮다)")
    b.add_argument("--details", help="② 상세 설명 조회 결과(상세가 필요한 과제)")
    b.add_argument("--name", required=True, help="묶음 이름(영문·숫자·-)")
    b.add_argument("--limit", type=int, default=100)
    b.add_argument("--split", type=int, default=1, help="이만큼의 묶음으로 나눠 넣는다")
    b.add_argument("--push", action="store_true", help="만든 뒤 곧바로 증분으로 넣는다(AI 추론은 비어 있는 잠금이면 첫 묶음만 띄운다)")
    args = parser.parse_args()
    try:
        profile = incr_review.find_profile(args.task)
        if args.command == "sql":
            print(sql(profile, args.per_platform))
            return 0
        rows = _load(args.rows)
        if args.command == "detail-sql":
            print(detail_sql(profile, rows, args.limit) or "")
            return 0
        details = {str(r["spid"]): r.get("description") or "" for r in _load(args.details)} if args.details else None
        out = output_root(profile) / "incr" / "_inbox" / args.name
        if source_of(profile).get("detail") and details is None:
            raise IncrCollectError("이 과제는 상세 사진이 필요합니다 — detail-sql로 상세 설명을 조회해 --details로 넘겨 주세요.")
        result: dict[str, Any] = build(profile, rows, out, args.limit, details)
        if args.push and result["written"]:
            folders = _split(profile, out, args.split) if args.split > 1 else [out]
            result["batches"] = []
            for n, folder in enumerate(folders, 1):
                name = f"{args.name}-{n}" if len(folders) > 1 else args.name
                pushed = incr_review.push(profile, folder / "input.jsonl", folder / "images.jsonl", name)
                if gt_next.running(gt_next.lock_dir(profile)) is None:
                    incr_review.start(profile, pushed["batch"])
                    pushed["aiStarted"] = True
                else:
                    pushed["aiStarted"] = False
                result["batches"].append(pushed)
                if n < len(folders):
                    import time

                    time.sleep(1.1)  # 묶음 이름이 초 단위 시각으로 시작한다 — 같은 초에 둘을 만들지 않게
    except (IncrCollectError, TaskError, incr_review.IncrRejected) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
