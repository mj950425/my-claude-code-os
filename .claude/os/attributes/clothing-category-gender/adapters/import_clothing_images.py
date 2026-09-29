#!/usr/bin/env python3
"""Import a local GT evidence index without fetching images or changing answers.

Source JSONL: {productKey, images: [{file, imageId, role}]}. Relative file paths
are resolved beside the source index. productKey is the platform-qualified GT
key, never a seller-product ID. Only real files belonging to current GT enter
the destination index; absent sources and uncovered GT keys remain explicit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / ".claude/os/engine/scripts"))
from catalog_profile import load_profile
from gt_task import resolve

PROFILE = Path(__file__).resolve().parents[1] / "profile.json"


def rows(path):
    result = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in result):
        raise ValueError("색인의 각 줄은 JSON 객체여야 합니다")
    return result


def atomic_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(body)
    try:
        os.replace(tmp.name, path)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def import_index(source, gt_path, destination, output_root, allowed_roles):
    gt = {}
    for row in rows(gt_path):
        key = row.get("productKey")
        if not isinstance(key, str) or not key.strip() or key in gt:
            raise ValueError("GT productKey가 비어 있거나 중복되었습니다")
        gt[key] = row
    seen, index, missing, ignored = set(), [], [], []
    copies = {}
    for row in rows(source):
        key = row.get("productKey")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("productKey가 없거나 중복되었습니다")
        seen.add(key)
        if key not in gt:
            ignored.append(key)
            continue
        raw_images = row.get("images")
        if not isinstance(raw_images, list):
            raise ValueError(f"{key}: images 목록이 필요합니다")
        images, identifiers = [], set()
        for image in raw_images:
            if not isinstance(image, dict):
                raise ValueError(f"{key}: 이미지 항목은 객체여야 합니다")
            identifier, role, raw = image.get("imageId"), image.get("role"), image.get("file")
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise ValueError(f"{key}: imageId가 없거나 중복되었습니다")
            identifiers.add(identifier)
            if role not in allowed_roles or not isinstance(raw, str) or not raw:
                raise ValueError(f"{key}/{identifier}: 등록된 role과 로컬 file이 필요합니다")
            path = Path(raw).expanduser()
            path = path if path.is_absolute() else source.parent / path
            if not path.is_file():
                missing.append({"productKey": key, "imageId": identifier, "file": str(path)})
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            relative = f"asset/{digest}{path.suffix.lower()}"
            copies[relative] = path
            images.append({"file": relative, "imageId": identifier, "role": role, "sha256": digest})
        if images:
            index.append({"productKey": key, "goodsNo": gt[key].get("goodsNo"),
                          "platformCode": gt[key].get("platformCode"), "images": images})
    if not index:
        raise ValueError("현재 GT에 연결되는 로컬 이미지가 없습니다. 기존 색인은 변경하지 않습니다")
    for relative, origin in copies.items():
        target = output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(origin.read_bytes()).hexdigest():
                raise ValueError(f"보관 이미지 내용이 다릅니다: {target}")
        else:
            shutil.copyfile(origin, target)
    manifest = {"sourceIndex": str(source.resolve()),
                "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "gtSha256": hashlib.sha256(gt_path.read_bytes()).hexdigest(),
                "indexedProducts": len(index), "imageReferences": sum(len(row["images"]) for row in index),
                "missingImages": missing, "uncoveredGtKeys": sorted(set(gt) - {row["productKey"] for row in index}),
                "ignoredNonGtKeys": sorted(ignored), "gtModified": False, "networkUsed": False}
    atomic_write(destination.with_suffix(".manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    atomic_write(destination, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in index))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-index", type=Path, required=True,
                        help="GT 상품에 연결된 로컬 이미지 JSONL 색인. 운영 API나 DB를 호출하지 않습니다")
    args = parser.parse_args()
    profile = load_profile(PROFILE)
    task = profile["gtTask"]
    manifest = import_index(args.source_index.resolve(), resolve(profile, task["gt"]),
                            resolve(profile, task["images"]),
                            resolve(profile, {"root": "project", "path": profile["outputRoot"]}),
                            set(task["images"]["roles"]))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
