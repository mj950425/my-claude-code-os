#!/usr/bin/env python3
"""여러 계보로 흩어진 골든셋을 상품 하나에 라벨 하나인 원장으로 합친다.

정답이 두 파일에 있으면 「지금 정답이 무엇인가」에 답이 둘이다. 화면과 큐가 서로
다른 쪽을 집어도 아무도 모른다 — 실제로 그런 일이 났다. 그래서 합치는 자리를 하나 만든다.

이 스크립트는 어느 속성인지 모른다. 계보의 위치와 순위는 인자로 받는다.
합치는 규칙만 여기 있다: **순위가 낮은 계보가 이긴다.** 진 계보는 지워지지 않고
`otherLineages`에 남는다 — 왜 이 라벨이 이겼는지 나중에 되짚어야 하기 때문이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "catalog-gt-v1"

# 상품을 가리키는 값. 계보가 달라도 같은 이름으로 온다.
IDENTITY_FIELDS = (
    "productKey",
    "goodsNo",
    "platformCode",
    "productName",
    "standardCategory",
    "brand",
    "pdpUrl",
)
# 이긴 계보에서만 가져오는 검수 이력.
REVIEW_FIELDS = ("reviewStatus", "reviewer", "reviewNote", "sourceSheet", "sheetRow")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL을 읽는다.

    `splitlines()`를 쓰지 않는다 — 상세 HTML에 섞여 오는 유니코드 줄 구분자(U+2028)를
    줄바꿈으로 세어 한 객체를 둘로 쪼갠다. JSON에서 그것은 줄바꿈이 아니다.
    """
    rows = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            rows.append(json.loads(line))
    return rows


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_lineage(raw: str) -> dict[str, Any]:
    spec = json.loads(raw)
    for key in ("id", "rank", "path"):
        if key not in spec:
            raise SystemExit(f"계보 명세에 {key}가 없습니다: {raw}")
    spec.setdefault("labelField", "goldLabel")
    spec.setdefault("sourceField", "goldSource")
    spec["path"] = Path(spec["path"])
    if not spec["path"].is_file():
        raise SystemExit(f"계보 파일이 없습니다: {spec['path']}")
    return spec


def build(
    lineages: list[dict[str, Any]],
    corrections: list[Path],
    out: Path,
    index_out: Path,
    profile_id: str,
    confirmations: Path | None = None,
) -> dict[str, Any]:
    lineages = sorted(lineages, key=lambda spec: spec["rank"])
    merged: dict[str, dict[str, Any]] = {}
    lineage_stats = []

    for spec in lineages:
        rows = read_jsonl(spec["path"])
        lineage_stats.append(
            {
                "id": spec["id"],
                "rank": spec["rank"],
                "path": str(spec["path"]),
                "sha256": sha256(spec["path"]),
                "rows": len(rows),
            }
        )
        for row in rows:
            key = str(row.get("productKey") or "")
            if not key:
                continue
            label = row.get(spec["labelField"])
            source = row.get(spec["sourceField"])
            seen = {
                "lineage": spec["id"],
                "goldLabel": label,
                "goldSource": source,
            }
            entry = merged.get(key)
            if entry is None:
                entry = {field: row.get(field) for field in IDENTITY_FIELDS}
                entry["productKey"] = key
                entry.update({field: row.get(field) for field in REVIEW_FIELDS})
                entry["goldLabel"] = label
                entry["goldSource"] = source
                entry["goldLineage"] = spec["id"]
                entry["otherLineages"] = []
                merged[key] = entry
                continue
            # 이미 더 높은 순위가 라벨을 잡았다. 진 계보는 흔적으로만 남는다.
            for field in IDENTITY_FIELDS:
                if entry.get(field) in (None, "") and row.get(field) not in (None, ""):
                    entry[field] = row.get(field)
            entry["otherLineages"].append(seen)

    # 정정은 여러 곳에서 온다. 외부 하네스가 넘겨준 것과, **이 저장소의 사람이 확정한 것**이
    # 서로 다른 파일이기 때문이다. 뒤에 준 것이 앞을 덮는다 — 우리 결정을 마지막에 두면
    # 외부가 무엇을 보냈든 사람이 확정한 값이 최종이다.
    applied_corrections = []
    correction_stats = []
    for source_path in corrections:
        applied_here = []
        for row in read_jsonl(source_path):
            key = str(row.get("productKey") or "")
            entry = merged.get(key)
            if entry is None or not row.get("goldLabel"):
                continue
            if entry["goldLabel"] != row.get("goldLabel"):
                entry["otherLineages"].append(
                    {
                        "lineage": entry["goldLineage"],
                        "goldLabel": entry["goldLabel"],
                        "goldSource": entry["goldSource"],
                    }
                )
            entry["goldLabel"] = row.get("goldLabel")
            entry["goldSource"] = row.get("goldSource") or row.get("goldLabelSource")
            entry["goldLineage"] = "corrections"
            # 누가 언제 왜 고쳤는지. 정정된 라벨만 남으면 다음 사람이 그 값을 되짚을 수 없다.
            if row.get("decisionId"):
                entry["correctedBy"] = {
                    "decisionId": row.get("decisionId"),
                    "reviewer": row.get("reviewer"),
                    "at": row.get("reviewedAt"),
                    "reason": row.get("reason"),
                }
            applied_here.append(key)
            applied_corrections.append(key)
        correction_stats.append(
            {
                "path": str(source_path),
                "sha256": sha256(source_path),
                "applied": len(applied_here),
            }
        )

    # **유지도 결정이다.** 사람이 「이 GT가 맞다」고 확정한 사실을 라벨 옆에 남긴다.
    # 남기지 않으면 다음 사이클에 같은 건이 같은 이유로 다시 올라오고, 사람은 자기가
    # 이미 답한 것을 또 답한다. 라벨은 바꾸지 않는다 — 확인은 변경이 아니다.
    confirmed = []
    for row in read_jsonl(confirmations) if confirmations is not None else []:
        entry = merged.get(str(row.get("productKey") or ""))
        if entry is None:
            continue
        entry["humanConfirmed"] = {
            "decisionId": row.get("decisionId"),
            "reviewer": row.get("reviewer"),
            "at": row.get("reviewedAt"),
            "reason": row.get("reason"),
            # 무엇을 확인했는지 남긴다. 나중에 라벨이 바뀌면 이 확인은 그 라벨의 것이 아니다.
            "label": row.get("goldLabel"),
        }
        confirmed.append(entry["productKey"])

    conflicts = []
    for key, entry in merged.items():
        disagreeing = [
            other
            for other in entry["otherLineages"]
            if other["goldLabel"] not in (None, "") and other["goldLabel"] != entry["goldLabel"]
        ]
        entry["conflict"] = bool(disagreeing)
        entry["resolvedBy"] = "CORRECTION" if entry["goldLineage"] == "corrections" else "LINEAGE_RANK"
        if disagreeing:
            conflicts.append(key)

    rows = [merged[key] for key in sorted(merged)]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    index = {
        "schemaVersion": SCHEMA_VERSION,
        "profileId": profile_id,
        "builtAt": datetime.now(UTC).isoformat(),
        "gt": str(out),
        "precedence": [spec["id"] for spec in lineages],
        "lineages": lineage_stats,
        "corrections": correction_stats or None,
        "confirmations": (
            {
                "path": str(confirmations),
                "sha256": sha256(confirmations),
                "applied": len(confirmed),
            }
            if confirmations is not None
            else None
        ),
        "counts": {
            "products": len(rows),
            "conflicts": len(conflicts),
            "labels": {
                label: sum(1 for row in rows if row["goldLabel"] == label)
                for label in sorted({str(row["goldLabel"]) for row in rows})
            },
        },
        "conflictProductKeys": sorted(conflicts),
        # 사람이 확정한 라벨. 다음 사이클이 이 건을 다시 묻지 않게 하는 근거다.
        "humanConfirmedProductKeys": sorted(confirmed),
    }
    index_out.parent.mkdir(parents=True, exist_ok=True)
    index_out.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument(
        "--lineage",
        action="append",
        required=True,
        metavar="JSON",
        help='{"id":"scoring","rank":1,"path":"...","labelField":"goldLabel","sourceField":"goldSource"}',
    )
    parser.add_argument(
        "--corrections",
        type=Path,
        action="append",
        default=[],
        help="정정 파일. 반복 가능하고 **뒤에 준 것이 앞을 덮는다**",
    )
    parser.add_argument(
        "--confirmations",
        type=Path,
        help="사람이 «이 GT가 맞다»고 확정한 목록. 라벨을 바꾸지 않고 확인 사실만 붙인다",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lineage-index", type=Path, required=True)
    args = parser.parse_args()

    index = build(
        [parse_lineage(raw) for raw in args.lineage],
        args.corrections,
        args.out,
        args.lineage_index,
        args.profile_id,
        args.confirmations,
    )
    print(json.dumps({k: index[k] for k in ("gt", "precedence", "counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
