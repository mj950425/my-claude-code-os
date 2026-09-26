#!/usr/bin/env python3
"""GT 개선 과제의 한 건에 딸린 증거(사진·글)를 run 폴더로 내려 놓는다. 판정하지 않는다.

1. **사진을 복사하고 줄인다.** 원본은 외부 레포나 run 자산에 흩어져 있다. 판독 에이전트와 사람이
   **같은 파일**을 보도록 `runs/<id>/gt-review/images/`에 한 벌로 모은다.
2. **긴 상세 원본은 타일로 자른다.** 규칙은 `common/tile_rule.py` 하나 — 운영 코드의 이식이다.
   여기서 자르는 것은 새로 번호를 매기는 일이라 **현재 판**으로 자르고, 그 판을 사진마다 적는다.
   이미 다른 곳에서 잘려 온 조각은 선언된 판(`preTiledRule`)을 그대로 적는다 — 어느 규칙이 그
   조각을 만들었는지 모르면, 같은 `D02T03`이 다른 곳의 `D02T03`과 같은 사진인지 가릴 수 없다.
3. **판독자에게 줄 글을 모은다.** 상품 정보 고시처럼 근거가 글인 GT도 있다.

## 눈가림을 입력으로 지킨다

판독자에게 가는 사진의 폴더 이름과 파일 이름에 **키를 쓰지 않는다.** 키가 보이면 판독자가 그 키로
GT 파일을 찾아 읽을 수 있다. 폴더는 불투명한 이름(`opaque`)이고, 사진은 `P01`처럼 순번이다.
원래 이미지 id는 작업 목록(`worklist.json`)에만 남는다 — 사람의 화면과 원장이 쓴다.

어떤 사진·글이 필요한지는 프로필의 `gtTask.images`·`gtTask.evidence`가 선언한다. 여기서 추측하지 않는다.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from gt_task import TaskError, key_fields, key_of, missing_input, read_jsonl, resolve

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))
import tile_rule  # noqa: E402

# 사람 화면과 판독 에이전트가 같이 보는 크기. 긴 변 기준이다. 더 크면 비용만 늘고,
# 더 작으면 작은 글자·얼굴이 뭉개진다(접촉 시트에서 384px가 그 경계였다).
DEFAULT_MAX_EDGE = 1024
DEFAULT_MAX_IMAGES = 16
JPEG_QUALITY = 85


def opaque(batch: str, key: str) -> str:
    """키를 드러내지 않는 폴더 이름. 배치마다 달라 지난 배치의 폴더와도 섞이지 않는다."""
    return hashlib.sha1(f"{batch}\n{key}".encode("utf-8")).hexdigest()[:12]


def evenly(values: list[Any], limit: int) -> list[Any]:
    """앞에서 자르지 않고 고르게 뽑는다. 상세 페이지는 뒤로 갈수록 다른 종류의 컷이 나온다."""
    if limit <= 0 or len(values) <= limit:
        return values
    if limit == 1:
        return values[:1]
    picks = [int(index * (len(values) - 1) / (limit - 1) + 0.5) for index in range(limit)]
    return [values[index] for index in dict.fromkeys(picks)]


class ImageIndex:
    """선언된 사진 색인. 키 하나에 사진 목록 하나. 판독자에게 줄 맥락·글도 여기서 고른다."""

    def __init__(self, profile: dict[str, Any], task: dict[str, Any]) -> None:
        self.profile = profile
        self.spec = task.get("images") or {}
        self.evidence = task.get("evidence") or {}
        self.rows: dict[str, dict[str, Any]] = {}
        self.join_fields = key_fields(self.spec.get("joinField") or task["keyField"])
        # 답이 들어 있는 열. 맥락·글로 선언돼도 판독자에게 주지 않는다 — 주면 눈가림이 풀린다.
        self.blocked = {str(field.get("gtField") or field["id"]) for field in task["fields"]}
        gt_spec = task.get("gt") or {}
        self.blocked |= {str(gt_spec.get("sourceField") or "source"), str(gt_spec.get("fieldSourcesField") or "fieldSources")}
        # 로더가 선언에서 막지만, 여기서도 한 번 더 뺀다 — 대체 정답·상류 거울·상류 위치 열도 답을 가리킨다.
        self.blocked |= {str(field["alternativesField"]) for field in task["fields"] if field.get("alternativesField")}
        upstream = gt_spec.get("upstream") or {}
        self.blocked |= {str(name) for name in [*(upstream.get("mirrorFields") or []), *(upstream.get("locatorFields") or [])]}
        title = None if task.get("titleAsEvidence") else task.get("titleField")
        for spec in (task.get("keyField"), title, self.spec.get("keyField"), self.spec.get("joinField")):
            self.blocked |= set(key_fields(spec)) if spec else set()
        # 한 줄에 사진 한 장인 평평한 색인(listField 없음)은 같은 키의 줄을 모아 한 건의 사진 목록으로 삼는다.
        self.groups: dict[str, list[dict[str, Any]]] = {}
        # 사진 없이 맥락만 주는 과제도 있다(images에 contextFields만) — 그때 맥락은 GT 줄에서 읽는다.
        if not self.spec or not self.spec.get("path"):
            return
        path = resolve(profile, self.spec)
        if not path.is_file():
            raise missing_input(task, "사진 색인", path)
        keys = key_fields(self.spec.get("keyField") or task["keyField"])
        flat = not self.spec.get("listField")
        for number, row in enumerate(read_jsonl(path), 1):
            key = key_of(row, keys)
            if key is None:
                continue
            if flat:
                self.groups.setdefault(key, []).append(row)
                self.rows.setdefault(key, row)
            elif key in self.rows:
                # 한 줄에 목록이 있는 색인에서 키가 겹치면 어느 목록이 그 건의 사진인지 모른다. 뒤 줄이 조용히 이기게 두지 않는다.
                raise TaskError(f"사진 색인에 키가 겹칩니다: {key} ({number}번째 줄). 한 줄에 사진 목록 하나(listField)이거나, "
                                "listField 없이 한 줄에 사진 한 장이어야 합니다.")
            else:
                self.rows[key] = row

    def row_for(self, gt_row: dict[str, Any]) -> dict[str, Any]:
        key = key_of(gt_row, self.join_fields)
        return self.rows.get(key) or {} if key else {}

    @property
    def has_photos(self) -> bool:
        """사진 색인인가. 파일 열(fileField)을 선언하지 않은 색인은 맥락·글만 주는 표다 — 사진 경고를 내지 않는다
        (소재 고시처럼 글이 근거인 GT가 따로 된 상품 표를 이을 때)."""
        return bool(self.spec.get("path") and self.spec.get("fileField"))

    def raw_entries(self, gt_row: dict[str, Any]) -> list[dict[str, Any]]:
        """역할로 거르기 전의 사진 목록 — 역할 설정이 사진을 다 걸러 냈는지 셈할 때 쓴다."""
        if not self.has_photos:
            return []
        row = self.row_for(gt_row)
        if not row:
            return []
        list_field = self.spec.get("listField")
        raw = row.get(list_field) if list_field else self.groups.get(key_of(gt_row, self.join_fields) or "", [row])
        return [entry for entry in raw or [] if isinstance(entry, dict)]

    def entries(self, gt_row: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.has_photos:
            return []
        row = self.row_for(gt_row)
        if not row:
            return []
        list_field = self.spec.get("listField")
        raw = row.get(list_field) if list_field else self.groups.get(key_of(gt_row, self.join_fields) or "", [row])
        entries = [entry for entry in raw or [] if isinstance(entry, dict)]
        roles = self.spec.get("roles")
        role_field = self.spec.get("roleField") or "role"
        if roles:
            entries = [entry for entry in entries if entry.get(role_field) in roles]
        return entries

    def _pick(self, gt_row: dict[str, Any], names: list[str]) -> dict[str, Any]:
        row = self.row_for(gt_row)
        picked: dict[str, Any] = {}
        for name in names:
            if name in self.blocked:
                continue
            value = row.get(name, gt_row.get(name))
            if value not in (None, "", []):
                picked[name] = value
        return picked

    def context(self, gt_row: dict[str, Any]) -> dict[str, Any]:
        """판독자에게 함께 줄 맥락(카테고리 같은 것). 답이 아니라 «무엇을 파는가»를 가르는 데 쓴다."""
        return self._pick(gt_row, [str(name) for name in self.spec.get("contextFields") or []])

    def text(self, gt_row: dict[str, Any]) -> dict[str, Any]:
        """근거가 글인 GT의 증거(상품 정보 고시 같은 것). 선언된 열만, 답 열은 막는다."""
        return self._pick(gt_row, [str(name) for name in self.evidence.get("textFields") or []])

    def file_of(self, entry: dict[str, Any]) -> Path | None:
        value = entry.get(self.spec.get("fileField") or "file")
        if not value:
            return None
        base = {"root": self.spec.get("fileRoot") or "project",
                "path": str(Path(self.spec.get("fileBase") or ".") / str(value))}
        return resolve(self.profile, base)


def _save(image: Any, target: Path, max_edge: int) -> dict[str, int]:
    from PIL import Image

    width, height = image.size
    scale = min(1.0, max_edge / max(width, height)) if max_edge > 0 else 1.0
    if scale < 1.0:
        image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return {"width": image.size[0], "height": image.size[1]}


def canonical_piece(name: str) -> str:
    """조각 이름을 D02T03 모양으로. 운영 DetailTileId.parseLayout은 «D(\\d+)T(\\d+)» 통째만 읽는다(«D2T3» → D02T03).
    «12345-D02T03» 같은 앞머리는 운영과 같은 것이 아니라 확장이다 — 평가 하네스의 조각 파일 이름(<상품 번호>-DxxTyy,
    collect_product_detail_images.save_tiles)을 받으려고. 조각 이름이 아니면 그대로 둔다."""
    piece = tile_rule.parse_piece(name)  # 운영이 받는 것만 — ASCII 숫자, 끝 줄바꿈 없이, 1 이상
    return tile_rule.scene_id(*piece) if piece else name


def require_pillow() -> None:
    try:
        import PIL  # noqa: F401
    except ImportError as error:
        raise TaskError("사진 처리 라이브러리(Pillow)가 없습니다. 개발자에게 `pip install pillow`를 부탁하세요.") from error


def prepare(index: ImageIndex, gt_row: dict[str, Any], key: str, out_dir: Path, run_root: Path, batch: str) -> dict[str, Any]:
    """한 건의 증거를 준비한다. 무엇을 못 받았는지도 함께 돌려준다 — 조용히 빠지면 없는 사진을 믿는다."""
    if index.has_photos:
        # 사진이 있는 과제만 사진 처리 도구가 필요하다. 글·맥락만 주는 과제는 없이도 돈다.
        require_pillow()
    spec = index.spec
    id_field = spec.get("idField") or "id"
    role_field = spec.get("roleField") or "role"
    tile_roles = set(spec.get("tileRoles") or [])
    pre_tiled_roles = set(spec.get("preTiledRoles") or [])
    pre_tiled_rule = spec.get("preTiledRule")
    # 자를 원본의 규칙. GT가 조각 이름을 가리키면(matchField) 그 이름을 매긴 규칙으로, 아니면 여기서 새로 매기므로 현재 운영 규칙으로.
    cut_rule = spec.get("tileRule") or tile_rule.RULE
    cut_version = tile_rule.rule_named(cut_rule)
    cut_decoder = tile_rule.decoder_named(cut_rule)
    source_index_field = spec.get("sourceIndexField")
    images: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    skipped: list[str] = []
    max_edge = int(spec.get("maxEdge") or DEFAULT_MAX_EDGE)
    folder = out_dir / opaque(batch, key)
    # 이미지·장면 단위 GT가 상품 단위 색인을 쓸 때 — GT 줄이 가리키는 사진 한 장(또는 잘라 낸 조각 하나)만.
    # 대조는 **자른 뒤에** 한다. GT가 가리키는 것이 긴 원본의 한 조각(D02T03)일 수 있기 때문이다.
    wanted = canonical_piece(str(gt_row.get(spec["matchField"]))) if spec.get("matchField") else None
    entry_field = spec.get("entryField")
    detail_position = 0
    for position, entry in enumerate(index.entries(gt_row), 1):
        image_id = str(entry.get(id_field) or f"IMG{position:02d}")
        entry_key = str(entry.get(entry_field)) if entry_field else image_id
        role = str(entry.get(role_field) or "")
        if role in tile_roles:
            # 운영은 상세 원본 목록 안의 순번으로 D를 센다. 썸네일까지 센 순번을 쓰면 D가 밀린다.
            detail_position += 1
        path = index.file_of(entry)
        if path is None or not path.is_file():
            raw = str(entry.get(spec.get("fileField") or "file") or "")
            # 색인이 주소(https://…)를 가리키면 하네스는 내려받지 않는다 — 내려받기는 가져오기(prerequisite)의 일이다.
            missing.append({"imageId": image_id, "reason": "사진이 로컬 파일이 아니라 주소입니다 — 가져오기가 내려받아야 합니다"
                            if raw.startswith(("http://", "https://")) else "파일이 없습니다"})
            continue
        piece_decoder = cut_decoder
        try:
            # 자를 원본만 번호를 매긴 쪽의 디코드로 읽는다(경계가 밝기로 정해지므로). 보여 주기만 할 사진은
            # 보기용 디코드로 읽는다 — 자르지 않을 사진을 «경계를 재현하지 못한다»는 이유로 버리지 않는다.
            image = tile_rule.decode(path, cut_decoder) if role in tile_roles else tile_rule.decode_for_display(path)
        except OSError as error:
            missing.append({"imageId": image_id, "reason": f"열지 못했습니다: {error}"})
            continue
        except tile_rule.UnsupportedDecode as error:
            if role in tile_roles and not spec.get("matchField"):
                # 번호를 여기서 새로 매기는 과제(운영의 조각 이름을 되짚지 않음)는 운영 경계를 재현할 까닭이 없다 — 버리지 않고
                # 평가 하네스의 디코드(harness-pillow)로 읽어 같은 판으로 자른다. 어느 디코드로 잘랐는지는 tileRule에 남는다.
                try:
                    image = tile_rule.decode(path, tile_rule.HARNESS_PILLOW)
                except (OSError, tile_rule.UnsupportedDecode):
                    missing.append({"imageId": image_id, "reason": str(error)})
                    continue
                piece_decoder = tile_rule.HARNESS_PILLOW
            else:
                # 운영 디코드를 재현하지 못하는 파일은 자르지도 보여 주지도 않는다 — 조각 이름이 다른 사진을 가리킬 수 있다.
                missing.append({"imageId": image_id, "reason": str(error)})
                continue
        except Exception as error:  # noqa: BLE001 — 사진 한 장(지나치게 큰 사진 같은)이 배치 전체를 멈추게 두지 않는다
            missing.append({"imageId": image_id, "reason": f"열지 못했습니다: {type(error).__name__}"})
            continue
        if not tile_rule.usable(image):
            # 운영(ImageQualityGate)과 평가 하네스는 64px 밑의 원본을 실패가 아니라 «내용이 아님»으로 빼기만 한다 — 구분선이나
            # 추적 픽셀이다. 못 연 사진(missing)에 섞으면 진짜 실패를 묻는다. D 순번은 위에서 이미 셌다(운영과 같다).
            skipped.append(image_id)
            continue
        if role in tile_roles:
            if source_index_field:
                # D번호 열을 선언했으면 그 값만 쓴다. 빈 칸을 세어 채우면 로더가 막으려던 «조용히 밀린 D»가 되고,
                # 다른 줄이 선언한 번호와 부딪칠 수도 있다. 모양이 틀린 값(D03 같은)도 멈추지 않고 이 원본만 뺀다.
                declared = entry.get(source_index_field)
                text = str(declared).strip() if declared is not None and not isinstance(declared, bool) else ""
                # ASCII 숫자만 — isdigit()은 «²»·전각 숫자도 참이라 int()에서 멈춘다.
                if not re.fullmatch(r"[0-9]+", text) or int(text) < 1:
                    # 운영 DetailTileId는 0 이하를 거절한다. 여기서 D00을 매기면 운영의 어느 이름과도 맞지 않는다.
                    missing.append({"imageId": image_id,
                                    "reason": f"원본의 D번호가 없거나 1 이상의 정수가 아닙니다({source_index_field}={declared!r})"})
                    continue
                source_index = int(text)
            else:
                source_index = detail_position
            for tile_index, (top, bottom) in enumerate(tile_rule.tile_ranges(image, cut_version), 1):
                scene = tile_rule.scene_id(source_index, tile_index)
                images.append({"imageId": scene, "role": role, "_match": {scene, entry_key},
                               "crop": {"top": top, "bottom": bottom},
                               "tileRule": tile_rule.rule(cut_version, piece_decoder),
                               "_image": image.crop((0, top, image.size[0], bottom))})
            continue
        if role in pre_tiled_roles and image.size[1] > tile_rule.max_piece_height(image.size[0], tile_rule.rule_named(pre_tiled_rule)):
            # «이미 잘린 조각»이라 선언됐는데 어떤 판으로도 조각이 될 수 없는 높이다 — 자르지 않은 원본이 섞여 들어왔다.
            # 이름이 DxxTyy면 선언된 규칙으로 다시 잘라 그 조각을 꺼내고, 아니면 보이지 않는다(다른 사진을 조각이라 보이지 않게).
            name = tile_rule.parse_piece(image_id)  # canonical_piece와 같은 규칙
            try:
                original = tile_rule.decode(path, tile_rule.decoder_named(pre_tiled_rule))
                ranges = tile_rule.tile_ranges(original, tile_rule.rule_named(pre_tiled_rule))
            except (OSError, ValueError) as error:
                missing.append({"imageId": image_id, "reason": f"조각이 아니라 원본인데 다시 자르지 못했습니다: {error}"})
                continue
            if not name or not 1 <= name[1] <= len(ranges):
                missing.append({"imageId": image_id, "reason": "조각이라 선언됐지만 자르지 않은 원본이고, 몇 번째 조각인지 모릅니다"})
                continue
            top, bottom = ranges[name[1] - 1]
            image = original.crop((0, top, original.size[0], bottom))
        record: dict[str, Any] = {"imageId": image_id, "role": role, "_image": image,
                                  "_match": {image_id, entry_key, canonical_piece(image_id), canonical_piece(entry_key)}}
        if role in pre_tiled_roles:
            record["tileRule"] = pre_tiled_rule
        images.append(record)
    if wanted is not None:
        images = [image for image in images if wanted in image["_match"]]
        if not images:
            missing.append({"imageId": wanted, "reason": "GT 줄이 가리키는 사진(또는 조각)이 색인에 없습니다"})
    limit = int(spec.get("maxImages") or DEFAULT_MAX_IMAGES)
    kept = evenly(images, limit)
    # 같은지는 자리(id)로 본다 — 사전 비교(==)는 사진 픽셀까지 대 보아, 픽셀이 같은 빈 조각 둘을 하나로 보고 뺀 쪽을 놓친다.
    kept_ids = {id(image) for image in kept}
    omitted = [image["imageId"] for image in images if id(image) not in kept_ids]
    for number, image in enumerate(kept, 1):
        view_id = f"P{number:02d}"
        target = folder / f"{view_id}.jpg"
        image.pop("_match", None)
        image.update(_save(image.pop("_image"), target, max_edge))
        image["viewId"] = view_id
        image["path"] = relative_path(target)
    return {"images": kept, "omitted": omitted, "missing": missing, "skippedAsNonContent": skipped,
            "context": index.context(gt_row), "text": index.text(gt_row)}


def relative_path(path: Path) -> str:
    from catalog_profile import relative_or_absolute

    return relative_or_absolute(path)


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or hashlib.sha1(value.encode()).hexdigest()[:12]
