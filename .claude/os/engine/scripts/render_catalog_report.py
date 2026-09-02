#!/usr/bin/env python3
"""공통 큐 계약과 심판 결과를 읽어 속성에 독립적인 단일 HTML 의심 원장을 만든다.

보고서는 두 장의 목록이다 — 의심되는 GT(건 단위)와 의심되는 정책(군집 단위).
실행 결함은 분리해서 넘기고, 충돌 없는 근거 수집 기록은 부록으로 접는다.
어느 상품이 어느 목록에 가는지는 심판(`review/verdicts.jsonl`)의 귀책이 정한다.
심판이 없으면 프로필 신호의 `lane`, 그것도 없으면 미확정이다.

화면에 세는 숫자를 직접 적지 않는다. 건수는 전부 임베드된 데이터에서 브라우저가 센다.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from catalog_profile import PROJECT_ROOT, default_profile, load_profile, output_root, relative_or_absolute

# 보고서의 목록. 순서가 곧 기본 탭 순서다. 심판의 귀책(owner)이 여기로 접힌다.
LANES: list[dict[str, str]] = [
    {
        "id": "GT",
        "title": "의심되는 GT",
        "unit": "건 단위",
        "note": "정책이 답을 내는데 GT가 다르거나, GT 자체가 정본을 갖지 못했다. 고칠 곳은 골든셋이다.",
    },
    {
        "id": "POLICY",
        "title": "의심되는 정책",
        "unit": "군집 단위",
        "note": "GT에는 답이 있는데 정책이 그 답을 낼 근거가 없거나, 정책을 기계로 적용할 수 없다. 사람이 경계를 정해 판례로 남긴다.",
    },
    {
        "id": "RUNTIME",
        "title": "실행 결함",
        "unit": "분리해서 넘긴다",
        "note": "정책은 답을 내는데 실행이 다른 값을 만들었다. 판정 대상이 아니라 버그다.",
    },
    {
        "id": "OPEN",
        "title": "미확정",
        "unit": "심판 없음",
        "note": "귀책을 정할 심판 결과가 없다.",
    },
    {
        "id": "NONE",
        "title": "충돌 없음",
        "unit": "근거 수집 기록",
        "note": "정책·GT·실행이 같다. 어떻게 근거를 모았는지만 기록으로 남는다.",
    },
]
LANE_IDS = [lane["id"] for lane in LANES]
OWNER_LANE = {
    "GOLDEN": "GT",
    "POLICY": "POLICY",
    "EVIDENCE": "POLICY",
    "GOAL": "POLICY",
    "RUNTIME": "RUNTIME",
    "NONE": "NONE",
}
OWNER_SHORT = {
    "GOLDEN": "GT",
    "POLICY": "정책",
    "EVIDENCE": "근거",
    "GOAL": "목표",
    "PENDING_PRECEDENT": "판례 대기",
    "RUNTIME": "실행",
    "NONE": "없음",
}


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: object expected")
            rows.append(value)
    return rows


def read_queues(queue_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(queue_dir.glob("*.jsonl")):
        rows.extend(read_jsonl(path))
    return rows


def js_data(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def compact(value: Any) -> Any:
    """비어 있는 값은 싣지 않는다. 브라우저 쪽은 없는 키를 빈 값으로 읽는다."""
    if isinstance(value, dict):
        return {
            key: compact(item)
            for key, item in value.items()
            if item is not None and item != "" and item is not False
        }
    if isinstance(value, list):
        return [compact(item) for item in value]
    return value


def intern_strings(value: Any, min_length: int = 16, min_uses: int = 3) -> dict[str, Any]:
    """여러 행이 같은 긴 문장을 반복하면 표로 빼고 번호로 가리킨다. 브라우저가 다시 편다."""
    uses: Counter[str] = Counter()

    def count(item: Any) -> None:
        if isinstance(item, str):
            if len(item) >= min_length:
                uses[item] += 1
        elif isinstance(item, dict):
            for child in item.values():
                count(child)
        elif isinstance(item, list):
            for child in item:
                count(child)

    count(value)
    table = [string for string, n in uses.most_common() if n >= min_uses]
    index = {string: position for position, string in enumerate(table)}

    def swap(item: Any) -> Any:
        if isinstance(item, str):
            return {"$": index[item]} if item in index else item
        if isinstance(item, dict):
            return {key: swap(child) for key, child in item.items()}
        if isinstance(item, list):
            return [swap(child) for child in item]
        return item

    return {"strings": table, "data": swap(value)}


def text(value: Any) -> str:
    return "" if value is None else str(value)


def first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if row.get(key) not in (None, ""):
            return text(row.get(key))
    return ""


def link_from(output: Path, project_relative: str) -> str:
    """보고서 파일에서 프로젝트 안 파일로 가는 상대 링크."""
    if not project_relative:
        return ""
    return os.path.relpath((PROJECT_ROOT / project_relative).resolve(), output.parent)


def lane_of_verdict(verdict: dict[str, Any]) -> str:
    owner = text(verdict.get("owner"))
    if owner == "PENDING_PRECEDENT":
        # 약한 근거로 미결. 정책 답이 실행과 같으면 의심받는 쪽은 GT, GT가 실행과 같으면 정책이다.
        answer, gold, observed = (
            verdict.get("policyAnswer"),
            verdict.get("goldLabel"),
            verdict.get("observedLabel"),
        )
        return "GT" if answer == observed and answer != gold else "POLICY"
    return OWNER_LANE.get(owner, "OPEN")


def lane_of_signals(signals: list[str], catalog: dict[str, Any]) -> str:
    """심판이 없을 때. 프로필이 신호마다 `lane`을 선언했으면 그것을 쓴다."""
    declared = [
        text(catalog.get(signal, {}).get("lane"))
        for signal in signals
        if text(catalog.get(signal, {}).get("lane")) in LANE_IDS
    ]
    for lane_id in LANE_IDS:
        if lane_id in declared:
            return lane_id
    return "OPEN"


def normalize_rows(
    rows: list[dict[str, Any]],
    verdicts: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    products: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        product_key = text(row.get("productKey")) or f"ROW:{index + 1}"
        item = products.setdefault(
            product_key,
            {
                "productKey": product_key,
                "productName": text(row.get("productName")) or product_key,
                "brand": text(row.get("brand")),
                "category": first(row, "standardCategory", "category"),
                "url": first(row, "pdpUrl", "url"),
                # 세 라벨. GT는 사람 정답, observed는 실행(판단기) 출력. 정책 답은 심판에서 온다.
                "referenceLabel": first(row, "referenceLabel", "goldLabel", "canonicalGold"),
                "observedLabel": text(row.get("observedLabel")),
                "goldSource": text(row.get("goldSource")),
                "gtReviewStatus": text(row.get("gtReviewStatus")),
                "sourceConflict": None,
                "signals": [],
                "policySentences": [],
                "evidence": {
                    "text": first(row, "detailEvidence", "textSignal", "evidence"),
                    "type": first(row, "detailEvidenceType", "evidenceType"),
                    "sceneIds": [],
                    "images": [],
                },
                # 판단기가 이미지를 보고 어떤 단계를 거쳐 답에 도달했는지. 어댑터가 넣은 만큼만 그린다.
                "judge": {
                    "firstStage": text(row.get("thumbnailFold")),
                    "detailStage": first(row, "detailFold", "detailStageGender"),
                    "decisionSource": text(row.get("decisionSource")),
                    "status": text(row.get("detailStatus")),
                    "promptVersion": text(row.get("policyPromptVersion")),
                    "classification": text(row.get("mismatchClassification")),
                    "basis": text(row.get("mismatchClassificationBasis")),
                    "reviewRecommendation": text(row.get("reviewRecommendation")),
                },
                "input": {
                    "preparedTiles": row.get("preparedTileCount"),
                    "allTiles": row.get("allImageTileCount"),
                    "selectedImages": row.get("selectedImageCount"),
                    "omittedImages": row.get("omittedImageCount"),
                    "coverage": text(row.get("fullImageCoverageStatus")),
                    "sources": [text(source) for source in (row.get("collectionSources") or [])],
                    "collectionRecovered": bool(row.get("collectionRecovered")),
                    "previousCollectionError": text(row.get("previousCollectionError")),
                    "retryReason": text(row.get("judgeRetryReason")),
                },
                "verdict": None,
                "lane": "OPEN",
                "dual": False,
            },
        )
        signal = text(row.get("signal"))
        reason = text(row.get("reason"))
        if signal and all(entry["id"] != signal for entry in item["signals"]):
            item["signals"].append({"id": signal, "reason": reason})
        policy_sentence = text(row.get("policyRule"))
        if policy_sentence and policy_sentence not in item["policySentences"]:
            item["policySentences"].append(policy_sentence)
        if not item["evidence"]["text"] and row.get("detailEvidence"):
            item["evidence"]["text"] = text(row.get("detailEvidence"))
            item["evidence"]["type"] = text(row.get("detailEvidenceType"))
        for url in row.get("evidenceImageUrls") or []:
            if url and url not in item["evidence"]["images"]:
                item["evidence"]["images"].append(str(url))
        for scene_id in row.get("policyEvidenceSceneIds") or []:
            if scene_id and scene_id not in item["evidence"]["sceneIds"]:
                item["evidence"]["sceneIds"].append(str(scene_id))
        if row.get("conflictKind") and item["sourceConflict"] is None:
            item["sourceConflict"] = {
                "kind": text(row.get("conflictKind")),
                "canonical": text(row.get("canonicalGold")),
                "canonicalSource": text(row.get("canonicalSource")),
                "canonicalVersion": text(row.get("canonicalDatasetVersion")),
                "evaluation": text(row.get("evaluationGold")),
                "evaluationSource": text(row.get("evaluationSource")),
            }

    for item in products.values():
        priority = lambda entry: int(catalog.get(entry["id"], {}).get("priority", 999))  # noqa: E731
        item["signals"].sort(key=priority)
        verdict = verdicts.get(item["productKey"])
        if verdict:
            owner = text(verdict.get("owner"))
            item["verdict"] = {
                "owner": owner,
                "ownerShort": OWNER_SHORT.get(owner, owner),
                "action": text(verdict.get("ownerAction")),
                "reason": text(verdict.get("reason")),
                "policyAnswer": text(verdict.get("policyAnswer")),
                "ruleId": text(verdict.get("policyRule")),
                "strength": text(verdict.get("policyStrength")),
                "note": text(verdict.get("policyNote")),
                "blockedBy": [text(pid) for pid in (verdict.get("blockedBy") or [])],
                "evidenceGap": bool(verdict.get("evidenceGap")),
            }
            item["lane"] = lane_of_verdict(verdict)
            # 실행이 값을 지어냈지만 정책도 그 상품에 답을 낼 근거가 없다 — 양쪽 목록에 걸린다.
            item["dual"] = bool(verdict.get("evidenceGap")) and item["lane"] != "POLICY"
        else:
            item["lane"] = lane_of_signals([entry["id"] for entry in item["signals"]], catalog)
    return sorted(products.values(), key=lambda item: (LANE_IDS.index(item["lane"]), item["productKey"]))


STYLE = r"""
:root{
  color-scheme:light;
  --paper:#FAF9F5; --inset:#F2F1EB; --ink:#17150F; --muted:#6E6A5E; --faint:#9A9689;
  --rule:#DCD8CB; --accent:#8C2B18; --accent-soft:#EFE5E0;
  --serif:"Hahmlet",Georgia,"Apple SD Gothic Neo",serif;
  --sans:"IBM Plex Sans KR","Apple SD Gothic Neo",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,monospace;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-weight:400;font-size:14.5px;line-height:1.6;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,p,ul,ol,dl,dd,figure{margin:0}
a{color:inherit;text-decoration:none;border-bottom:1px solid var(--rule);transition:border-color .18s}
a:hover{border-color:var(--accent)}
button{font:inherit;color:inherit}
button:focus-visible,input:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{width:min(1320px,calc(100% - 56px));margin:0 auto}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.kicker{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.18em;text-transform:uppercase;color:var(--faint)}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:500;letter-spacing:-.02em}

/* 귀책을 색이 아니라 형태로 구분한다. 인쇄해도 남는다. */
.mark{display:inline-block;width:9px;height:9px;border:1.25px solid var(--ink);flex:0 0 auto;translate:0 -1px}
.mark.GT{background:var(--ink)}
.mark.POLICY{background:transparent}
.mark.RUNTIME{border-radius:50%;background:transparent}
.mark.OPEN{background:linear-gradient(135deg,var(--ink) 0 50%,transparent 50% 100%)}
.mark.NONE{border-style:dotted}

/* masthead */
.masthead{padding:40px 0 0}
.masthead-top{display:flex;justify-content:space-between;align-items:baseline;gap:24px;padding-bottom:10px;font-family:var(--mono);font-size:10.5px;color:var(--faint)}
.masthead-top .dirty{color:var(--accent)}
.masthead h1{font-family:var(--serif);font-weight:300;letter-spacing:-.035em;line-height:1.08;font-size:clamp(2.2rem,5vw,3.8rem);padding:16px 0 14px;border-top:1.5px solid var(--ink)}
.standfirst{max-width:64ch;font-size:15px;color:var(--muted);padding-bottom:24px}
.standfirst b{color:var(--ink);font-weight:500}
.runbar{display:flex;flex-wrap:wrap;border-top:1px solid var(--ink);border-bottom:1px solid var(--ink)}
.runbar div{flex:1 1 140px;padding:9px 14px 10px;border-left:1px solid var(--rule)}
.runbar div:first-child{border-left:0;padding-left:0}
.runbar dt{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint)}
.runbar dd{margin:2px 0 0;font-family:var(--mono);font-size:14px;font-weight:500;font-variant-numeric:tabular-nums}

/* 세 라벨의 뜻. 화면 어디서든 같은 이름을 쓴다. */
.legend{display:grid;grid-template-columns:repeat(3,1fr);margin-top:34px;border:1px solid var(--rule)}
.legend div{padding:14px 18px;border-left:1px solid var(--rule)}
.legend div:first-child{border-left:0}
.legend dt{display:flex;align-items:baseline;gap:8px;font-family:var(--mono);font-size:12px;font-weight:600;letter-spacing:.04em}
.legend dt small{font-weight:400;color:var(--faint);letter-spacing:.1em;font-size:9.5px;text-transform:uppercase}
.legend dd{margin-top:4px;font-size:12.5px;color:var(--muted);line-height:1.55}
.legend-foot{margin-top:8px;font-size:12px;color:var(--faint)}

/* 두 목록 */
.lanes{display:grid;grid-template-columns:1fr 1fr;margin-top:48px}
.lane{padding:0 34px 22px 0}
.lane + .lane{border-left:1px solid var(--rule);padding:0 0 22px 34px}
.lane-head{display:flex;align-items:baseline;gap:10px}
.lane-head h2{font-family:var(--serif);font-weight:400;font-size:1.6rem;letter-spacing:-.03em}
.lane-head small{font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
.lane-count{margin:6px 0 4px;display:flex;align-items:baseline;gap:10px}
.lane-count .num{font-size:3.2rem;font-weight:300;line-height:1}
.lane-count span{font-size:12.5px;color:var(--muted);font-family:var(--mono)}
.lane-note{font-size:12.5px;color:var(--muted);max-width:52ch;margin-bottom:14px}
.sig{display:grid;grid-template-columns:1fr auto;gap:2px 16px;padding:10px 0;border-top:1px solid var(--rule)}
.sig strong{font-weight:500;font-size:13px}
.sig strong i{font-style:normal;font-family:var(--mono);font-size:10px;letter-spacing:.08em;color:var(--accent);margin-right:8px}
.sig .num{font-size:14px}
.sig p{grid-column:1/-1;font-size:12px;color:var(--muted);line-height:1.5}
.lane-more{margin-top:12px;font-family:var(--mono);font-size:11px;color:var(--muted)}
.lane-more a{border-bottom-color:var(--faint)}
.aside-strip{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--ink);border-bottom:1px solid var(--ink)}
.aside-strip div{display:grid;grid-template-columns:auto 1fr;gap:0 14px;align-items:center;padding:14px 18px;border-left:1px solid var(--rule)}
.aside-strip div:first-child{border-left:0;padding-left:0}
.aside-strip .num{font-size:1.9rem;font-weight:300;line-height:1}
.aside-strip p{font-size:12.5px;color:var(--muted)}
.aside-strip p b{display:flex;align-items:center;gap:7px;color:var(--ink);font-weight:500}

/* 섹션 제목 */
.sec{margin-top:64px}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;gap:20px;padding-bottom:10px;border-bottom:1.5px solid var(--ink)}
.sec-head h2{font-family:var(--serif);font-weight:400;font-size:1.8rem;letter-spacing:-.03em}
.sec-head h2 small{display:block;font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);margin-bottom:6px}
.sec-head p{font-size:12.5px;color:var(--muted);max-width:52ch;text-align:right}

/* 질문 */
.q{display:grid;grid-template-columns:184px 1fr;gap:0 34px;padding:22px 0;border-bottom:1px solid var(--rule)}
.q-rail .qid{font-family:var(--mono);font-size:12px;font-weight:600;color:var(--accent);letter-spacing:.04em}
.q-rail .prec{margin-top:8px;font-family:var(--mono);font-size:10.5px;color:var(--muted);line-height:1.7}
.q-rail .status{display:inline-block;margin-top:4px;padding:2px 6px;border:1px solid var(--accent);color:var(--accent);font-family:var(--mono);font-size:9.5px;letter-spacing:.1em}
.q-rail .status.DECIDED{border-color:var(--ink);color:var(--ink)}
.q-body h3{font-family:var(--serif);font-weight:400;font-size:1.2rem;line-height:1.42;letter-spacing:-.02em;max-width:60ch}
.q-impact{display:flex;flex-wrap:wrap;margin-top:12px;border:1px solid var(--rule);width:fit-content;max-width:100%}
.q-impact div{padding:6px 14px 7px;border-left:1px solid var(--rule)}
.q-impact div:first-child{border-left:0}
.q-impact dt{font-family:var(--mono);font-size:9px;letter-spacing:.11em;text-transform:uppercase;color:var(--faint)}
.q-impact dd{font-family:var(--mono);font-size:13.5px;font-weight:600}
.q-rec{margin-top:12px;font-size:13.5px;max-width:70ch}
.q-rec b,.q-blocked b{font-family:var(--mono);font-size:10px;letter-spacing:.11em;text-transform:uppercase;color:var(--faint);display:block;margin-bottom:2px}
.q-blocked{margin-top:10px;font-family:var(--mono);font-size:11px;color:var(--muted)}

/* 작업대 */
.filters{display:flex;flex-wrap:wrap;border-bottom:1px solid var(--rule)}
.filters button{appearance:none;background:none;border:0;border-right:1px solid var(--rule);font-family:var(--mono);font-size:11px;letter-spacing:.06em;color:var(--muted);padding:9px 15px;cursor:pointer;transition:color .16s,background .16s;display:flex;align-items:center;gap:7px}
.filters button:hover{background:var(--inset);color:var(--ink)}
.filters button[aria-pressed=true]{background:var(--ink);color:var(--paper)}
.filters button[aria-pressed=true] .mark{border-color:var(--paper)}
.filters button[aria-pressed=true] .mark.GT{background:var(--paper)}
.filters button[aria-pressed=true] .mark.OPEN{background:linear-gradient(135deg,var(--paper) 0 50%,transparent 50% 100%)}
.filters .search{margin-left:auto;display:flex;align-items:center;gap:10px}
.filters input{border:0;border-left:1px solid var(--rule);background:transparent;padding:9px 12px;font-family:var(--mono);font-size:11.5px;min-width:260px}
.filters input::placeholder{color:var(--faint)}
.filters .shown{font-family:var(--mono);font-size:11px;color:var(--faint);padding-right:4px}
.bench{display:grid;grid-template-columns:372px minmax(0,1fr);border:1px solid var(--ink);border-top:0;height:min(900px,calc(100svh - 96px));min-height:660px}
.list{display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--rule)}
.list-body{overflow:auto;min-height:0;overscroll-behavior:contain;scrollbar-color:var(--rule) transparent}
.item{display:block;width:100%;padding:13px 16px 12px;border:0;border-bottom:1px solid var(--rule);background:none;text-align:left;cursor:pointer;transition:background .14s}
.item:hover{background:var(--inset)}
.item[aria-selected=true]{background:var(--inset);box-shadow:inset 3px 0 0 var(--ink)}
.item .idx{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:10px;letter-spacing:.08em;color:var(--muted)}
.item .idx .dual{margin-left:auto;color:var(--accent);letter-spacing:.04em}
.item h4{margin-top:5px;font-family:var(--serif);font-weight:400;font-size:15px;line-height:1.35;letter-spacing:-.01em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.item .key{margin-top:2px;font-family:var(--mono);font-size:10.5px;color:var(--faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.trio{display:flex;margin-top:8px;border:1px solid var(--rule);width:fit-content;max-width:100%}
.trio span{display:block;padding:3px 9px 4px;border-left:1px solid var(--rule);font-family:var(--mono);font-size:11px;font-weight:600;min-width:0}
.trio span:first-child{border-left:0}
.trio small{display:block;font-size:8.5px;font-weight:400;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
.trio span.verdict{background:var(--ink);color:var(--paper)}
.trio span.verdict small{color:rgba(250,249,245,.6)}
.trio span.verdict.pending{background:var(--accent-soft);color:var(--accent)}
.trio span.verdict.pending small{color:rgba(140,43,24,.62)}
.trio span.verdict.none{background:var(--inset);color:var(--muted)}
.empty{padding:48px 22px;color:var(--muted);text-align:center;font-size:13px}

.inspector{overflow:auto;min-height:0;overscroll-behavior:contain;scrollbar-color:var(--rule) transparent}
.insp{animation:rise .3s cubic-bezier(.22,.7,.3,1) backwards}
.insp-head{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:22px 26px 16px}
.insp-head h2{font-family:var(--serif);font-weight:400;font-size:1.45rem;line-height:1.3;letter-spacing:-.025em}
.insp-head .meta{margin-top:5px;font-family:var(--mono);font-size:11px;color:var(--muted)}
.insp-head .pdp{flex:0 0 auto;font-family:var(--mono);font-size:11px;padding:6px 10px;border:1px solid var(--rule)}
.insp-head .pdp:hover{border-color:var(--ink)}
.band{padding:16px 26px 18px;border-top:1px solid var(--rule);border-bottom:1px solid var(--ink);background:var(--inset)}
.labels{display:flex;align-items:stretch;border:1px solid var(--rule);width:fit-content;max-width:100%;background:var(--paper)}
.labels > div{padding:8px 16px 9px;border-left:1px solid var(--rule)}
.labels > div:first-child{border-left:0}
.labels dt{font-family:var(--mono);font-size:9px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint)}
.labels dd{margin-top:1px;font-family:var(--mono);font-size:15px;font-weight:600;letter-spacing:-.01em}
.labels dd small{display:block;font-size:9.5px;font-weight:400;color:var(--muted);letter-spacing:.02em;margin-top:1px}
.labels .verdict{background:var(--ink);color:var(--paper)}
.labels .verdict dt{color:rgba(250,249,245,.6)}
.labels .verdict.pending{background:var(--accent-soft);color:var(--accent)}
.labels .verdict.pending dt{color:rgba(140,43,24,.62)}
.labels .verdict.none{background:var(--paper);color:var(--muted)}
.why{margin-top:14px;font-size:13.5px;max-width:76ch}
.why b{font-weight:500}
.why .note{margin-top:4px;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border:1px solid var(--rule);font-family:var(--mono);font-size:10.5px;letter-spacing:.04em;background:var(--paper)}
.chip.open{border-color:var(--accent);color:var(--accent)}
.chip.dual{border-color:var(--accent);color:var(--accent)}
.chip a{border:0}
.insp-grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr)}
.col{padding:20px 26px 28px;min-width:0}
.col + .col{border-left:1px solid var(--rule)}
.col h3{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);padding-bottom:8px;border-bottom:1px solid var(--rule)}
.block{padding:14px 0 4px}
.block h4{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint);margin-bottom:6px}
.trail{display:flex;flex-wrap:wrap;border:1px solid var(--rule);width:fit-content;max-width:100%}
.trail div{padding:6px 13px 7px;border-left:1px solid var(--rule)}
.trail div:first-child{border-left:0}
.trail dt{font-family:var(--mono);font-size:8.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
.trail dd{font-family:var(--mono);font-size:12.5px;font-weight:600}
.trail div.final{background:var(--ink);color:var(--paper)}
.trail div.final dt{color:rgba(250,249,245,.6)}
.trail-src{margin-top:8px;font-family:var(--mono);font-size:10.5px;color:var(--muted)}
.quote{margin-top:6px;padding-left:14px;border-left:2px solid var(--ink);font-family:var(--serif);font-size:15.5px;font-weight:300;line-height:1.55}
.quote span{display:block;margin-top:5px;font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
.quote.absent{border-left-color:var(--rule);color:var(--muted);font-size:13.5px}
.main-image{position:relative;display:grid;place-items:center;width:100%;height:clamp(280px,40vh,440px);margin-top:10px;overflow:hidden;border:1px solid var(--rule);background:var(--inset)}
.main-image img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
.main-image .pos{position:absolute;right:8px;bottom:8px;padding:2px 7px;background:var(--ink);color:var(--paper);font-family:var(--mono);font-size:10px}
.thumbs{display:flex;gap:6px;margin-top:8px;overflow-x:auto;padding-bottom:4px}
.thumbs button{flex:0 0 64px;width:64px;height:56px;padding:0;overflow:hidden;border:1px solid var(--rule);background:#fff;cursor:pointer}
.thumbs button[aria-current=true]{border-color:var(--ink);box-shadow:0 0 0 1px var(--ink)}
.thumbs img{display:block;width:100%;height:100%;object-fit:cover}
.image-link{display:inline-block;margin-top:8px;font-family:var(--mono);font-size:10.5px;color:var(--muted)}
.kv{font-family:var(--mono);font-size:11px;color:var(--muted);line-height:1.85}
.kv b{color:var(--ink);font-weight:500}
.sigrow{padding:9px 0;border-top:1px solid var(--rule)}
.sigrow:first-of-type{border-top:0}
.sigrow strong{display:block;font-weight:500;font-size:13px}
.sigrow p{font-size:12.5px;color:var(--muted);line-height:1.5;margin-top:2px}
.sentence{font-size:13px;line-height:1.55;padding:6px 0}
.sentence + .sentence{border-top:1px dashed var(--rule)}
.recovery{margin-top:8px;padding:8px 10px;background:var(--accent-soft);color:#75401F;font-size:12px;line-height:1.5}

/* 부록 */
.map{width:100%;border-collapse:collapse;margin-top:6px}
.map th,.map td{text-align:left;padding:9px 16px 9px 0;border-bottom:1px solid var(--rule);vertical-align:top;font-size:13px}
.map th{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);font-weight:500;padding-top:0}
.map td.id{font-family:var(--mono);font-size:10.5px;color:var(--muted);word-break:break-all;width:24%}
.map td.desc{color:var(--muted);width:40%}
.map td.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.map td.where{font-family:var(--mono);font-size:10.5px;color:var(--muted)}

footer{margin-top:64px;padding:22px 0 70px;border-top:1.5px solid var(--ink);color:var(--muted);font-size:12px;line-height:1.75}
footer .mono{color:var(--ink)}

@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@media (max-width:1060px){
  .bench{grid-template-columns:320px minmax(0,1fr)}
  .insp-grid{grid-template-columns:1fr}
  .col + .col{border-left:0;border-top:1px solid var(--rule)}
}
@media (max-width:820px){
  .wrap{width:min(1320px,calc(100% - 28px))}
  .legend,.lanes,.aside-strip{grid-template-columns:1fr}
  .legend div,.aside-strip div{border-left:0;border-top:1px solid var(--rule)}
  .legend div:first-child,.aside-strip div:first-child{border-top:0}
  .aside-strip div{padding-left:0}
  .lane{padding:0 0 22px}
  .lane + .lane{border-left:0;border-top:1px solid var(--rule);padding:22px 0}
  .sec-head{flex-direction:column;align-items:flex-start}
  .sec-head p{text-align:left}
  .q{grid-template-columns:1fr;gap:10px}
  .bench{grid-template-columns:1fr;height:auto;min-height:0}
  .list{border-right:0;border-bottom:1px solid var(--ink)}
  .list-body{max-height:420px}
  .filters .search{margin-left:0;width:100%}
  .filters input{min-width:0;flex:1}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media print{
  body{font-size:10pt}
  .filters,.thumbs,.image-link{display:none}
  .bench{height:auto;border:0}
  .list{display:none}
  a{border:0}
}
"""


SCRIPT = r"""
const packed=JSON.parse(document.getElementById('audit-data').textContent);
const thaw=v=>Array.isArray(v)?v.map(thaw):(v&&typeof v==='object')?(('$' in v&&Object.keys(v).length===1)?packed.strings[v.$]:Object.fromEntries(Object.entries(v).map(([k,x])=>[k,thaw(x)]))):v;
const data=thaw(packed.data);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>Number(n||0).toLocaleString('ko-KR');
const laneById=Object.fromEntries(data.lanes.map(l=>[l.id,l]));
const signalById=Object.fromEntries(data.signals.map(s=>[s.id,s]));
const precedentById=Object.fromEntries(data.precedents.map(p=>[p.id,p]));
const inLane=(row,laneId)=>laneId==='ALL'||row.lane===laneId||(laneId==='POLICY'&&row.dual);
const laneRows=laneId=>data.rows.filter(row=>inLane(row,laneId));
const dualRows=data.rows.filter(row=>row.dual);
const blockedCount=pid=>data.rows.filter(row=>row.verdict&&row.verdict.blockedBy.includes(pid)).length;

let lane=data.defaultLane, query='', selectedKey=null, imageIndex=0;

/* ── 상단 두 목록: 건수와 사유별 분포 ── */
function renderLaneSummary(){
  for(const laneId of ['GT','POLICY']){
    const rows=laneRows(laneId);
    document.getElementById('count-'+laneId).textContent=fmt(rows.length);
    const groups=new Map();
    for(const row of rows){
      const head=row.verdict?row.verdict.ownerShort:laneById[row.lane].title;
      const why=row.verdict?row.verdict.reason:(row.signals[0]?signalById[row.signals[0].id]?.label||row.signals[0].id:'');
      const key=head+'\t'+why;
      groups.set(key,(groups.get(key)||0)+1);
    }
    const target=document.getElementById('groups-'+laneId);
    target.innerHTML=[...groups.entries()].sort((a,b)=>b[1]-a[1]).map(([key,n])=>{
      const [head,why]=key.split('\t');
      return `<div class="sig"><strong><i>${esc(head)}</i>${esc(why)}</strong><span class="num">${fmt(n)}</span></div>`;
    }).join('')||'<div class="sig"><strong>없음</strong><span class="num">0</span></div>';
  }
  document.getElementById('count-RUNTIME').textContent=fmt(laneRows('RUNTIME').length);
  document.getElementById('count-NONE').textContent=fmt(laneRows('NONE').length);
  document.getElementById('count-DUAL').textContent=fmt(dualRows.length);
  document.getElementById('count-OPEN-wrap').hidden=laneRows('OPEN').length===0;
  document.getElementById('count-OPEN').textContent=fmt(laneRows('OPEN').length);
}

/* ── 정책 질문: 군집 단위 ── */
function renderQuestions(){
  const target=document.getElementById('question-list');
  target.innerHTML=data.questions.map(q=>{
    const precedents=(q.precedents||[]).map(p=>`<u>${p.href?`<a href="${esc(p.href)}">${esc(p.id)}</a>`:esc(p.id)}</u><br><span class="status ${esc(p.status)}">${esc(p.status||'—')}</span>`).join('<br>');
    const words=k=>String(k).replace(/([a-z0-9])([A-Z])/g,'$1 $2').replace(/[_-]+/g,' ');
    const impact=Object.entries(q.impact||{}).map(([k,v])=>`<div><dt>${esc(words(k))}</dt><dd>${esc(typeof v==='number'?fmt(v):v)}</dd></div>`).join('');
    const blocked=(q.precedents||[]).map(p=>({id:p.id,n:blockedCount(p.id)})).filter(x=>x.n>0);
    return `<article class="q"><div class="q-rail"><p class="qid">${esc(q.id)}</p><p class="prec">${precedents||'<span class="status">판례 없음</span>'}</p></div><div class="q-body"><h3>${esc(q.question)}</h3>${impact?`<dl class="q-impact">${impact}</dl>`:''}${q.recommendation?`<p class="q-rec"><b>권고</b>${esc(q.recommendation)}</p>`:''}${blocked.length?`<p class="q-blocked"><b>이 답을 기다리는 사례</b>${blocked.map(x=>`${esc(x.id)} · ${fmt(x.n)}건`).join(' · ')}</p>`:''}</div></article>`;
  }).join('')||'<p class="empty">정책 질문 없음. 군집으로 접힐 만큼 모인 정책 결함이 없다.</p>';
}

/* ── 작업대 ── */
function visibleRows(){
  const q=query.trim().toLowerCase();
  return laneRows(lane).filter(row=>!q||[row.productKey,row.productName,row.brand,row.category,row.referenceLabel,row.observedLabel,row.verdict?.policyAnswer,row.verdict?.reason,row.evidence.text,...row.signals.map(s=>s.reason),...row.signals.map(s=>signalById[s.id]?.label||s.id)].join(' ').toLowerCase().includes(q));
}

function renderTabs(){
  const tabs=document.getElementById('lane-tabs');
  tabs.innerHTML=data.lanes.filter(l=>laneRows(l.id).length||l.id===lane).map(l=>`<button type="button" data-lane="${esc(l.id)}" aria-pressed="${l.id===lane}"><span class="mark ${esc(l.id)}" aria-hidden="true"></span>${esc(l.title)} ${fmt(laneRows(l.id).length)}</button>`).join('')+`<button type="button" data-lane="ALL" aria-pressed="${lane==='ALL'}">전체 ${fmt(data.rows.length)}</button>`;
  tabs.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{lane=b.dataset.lane;selectedKey=null;imageIndex=0;renderTabs();renderList();}));
}

function trio(row,compact){
  const v=row.verdict;
  const verdictClass=v?(v.owner==='PENDING_PRECEDENT'?'verdict pending':v.owner==='NONE'?'verdict none':'verdict'):'verdict pending';
  const verdictText=v?v.ownerShort:'미확정';
  return `<span><small>GT</small>${esc(row.referenceLabel)||'—'}</span><span><small>실행</small>${esc(row.observedLabel)||'—'}</span>${v?`<span><small>정책 답</small>${esc(v.policyAnswer)||'—'}</span>`:''}<span class="${verdictClass}"><small>귀책</small>${esc(verdictText)}</span>`;
}

function renderList(){
  const rows=visibleRows();
  if(!rows.some(row=>row.productKey===selectedKey)){selectedKey=rows[0]?.productKey||null;imageIndex=0;}
  document.getElementById('shown').textContent=`${fmt(rows.length)} / ${fmt(laneRows(lane).length)}`;
  document.getElementById('list-body').innerHTML=rows.length?rows.map((row,i)=>`<button type="button" class="item" role="option" aria-selected="${row.productKey===selectedKey}" data-key="${esc(row.productKey)}"><span class="idx"><span class="mark ${esc(row.lane)}" aria-hidden="true"></span>${String(i+1).padStart(2,'0')} · ${esc(laneById[row.lane].title)}${row.dual?'<span class="dual">양쪽 계류</span>':''}</span><h4>${esc(row.productName)}</h4><span class="key">${esc(row.productKey)}${row.category?' · '+esc(row.category):''}</span><span class="trio">${trio(row,true)}</span></button>`).join(''):'<p class="empty">조건에 맞는 상품이 없다.</p>';
  document.querySelectorAll('.item').forEach(el=>el.addEventListener('click',()=>{selectedKey=el.dataset.key;imageIndex=0;renderList();}));
  renderInspector();
}

function precedentChip(pid){
  const p=precedentById[pid];
  const label=p?`${esc(pid)} · ${esc(p.status)}`:esc(pid);
  return `<span class="chip ${p&&p.status==='OPEN'?'open':''}">${p&&p.href?`<a href="${esc(p.href)}">${label}</a>`:label}</span>`;
}

function renderInspector(){
  const row=data.rows.find(r=>r.productKey===selectedKey);
  const target=document.getElementById('inspector');
  if(!row){target.innerHTML='<p class="empty">왼쪽에서 사례를 고른다.</p>';return;}
  const v=row.verdict, j=row.judge, e=row.evidence, inp=row.input;

  /* 라벨 셋 + 귀책 */
  const verdictClass=v?(v.owner==='PENDING_PRECEDENT'?'verdict pending':v.owner==='NONE'?'verdict none':'verdict'):'verdict pending';
  const labels=`<dl class="labels"><div><dt>GT · 사람 정답</dt><dd>${esc(row.referenceLabel)||'—'}${row.goldSource?`<small>${esc(row.goldSource)}${row.gtReviewStatus?' · '+esc(row.gtReviewStatus):''}</small>`:''}</dd></div><div><dt>실행 · 판단기 출력</dt><dd>${esc(row.observedLabel)||'—'}${j.decisionSource?`<small>근거 출처 ${esc(j.decisionSource)}</small>`:''}</dd></div>${v?`<div><dt>정책 답 · 심판</dt><dd>${esc(v.policyAnswer)||'—'}<small>${esc(v.ruleId)}${v.strength?' · '+esc(v.strength):''}</small></dd></div>`:''}<div class="${verdictClass}"><dt>귀책</dt><dd>${v?esc(v.ownerShort):'미확정'}${v?`<small>${esc(v.action)}</small>`:''}</dd></div></dl>`;
  const why=v?`<div class="why"><b>${esc(v.reason)}</b>${v.note?`<p class="note">${esc(v.note)}</p>`:''}</div>`:'<div class="why"><p class="note">심판 결과가 없다. 큐 신호만으로 올라온 사례다.</p></div>';
  const chips=[...(v?v.blockedBy.map(precedentChip):[]),row.dual?'<span class="chip dual">양쪽 계류 — 실행 결함이자 정책 공백</span>':''].filter(Boolean).join('');

  /* 판단기가 본 것 */
  const hasTrail=j.firstStage||j.detailStage||row.observedLabel;
  const trail=hasTrail?`<dl class="trail">${j.firstStage?`<div><dt>1차 · 대표 이미지</dt><dd>${esc(j.firstStage)}</dd></div>`:''}${j.detailStage?`<div><dt>2차 · 상세 이미지</dt><dd>${esc(j.detailStage)}</dd></div>`:''}<div class="final"><dt>최종 출력</dt><dd>${esc(row.observedLabel)||'—'}</dd></div></dl>`:'';
  const quote=e.text?`<blockquote class="quote">${esc(e.text)}<span>${[e.type?'근거 유형 '+e.type:'',e.sceneIds.length?'장면 '+e.sceneIds.join(', '):''].filter(Boolean).map(esc).join(' · ')||'판단기 기록'}</span></blockquote>`:`<blockquote class="quote absent">판단기가 남긴 근거 문장이 없다.${inp.coverage?' 수집한 이미지는 전부 처리했지만 채택된 장면이 없다.':''}</blockquote>`;
  const images=e.images.slice(0,8);
  imageIndex=Math.min(imageIndex,Math.max(images.length-1,0));
  const url=images[imageIndex];
  const gallery=url?`<div class="main-image"><img src="${esc(url)}" referrerpolicy="no-referrer" alt="${esc(row.productName)} 근거 이미지 ${imageIndex+1}"><span class="pos">${imageIndex+1} / ${images.length}</span></div>${images.length>1?`<div class="thumbs">${images.map((u,i)=>`<button type="button" data-i="${i}" aria-current="${i===imageIndex}" aria-label="근거 이미지 ${i+1}"><img loading="lazy" src="${esc(u)}" referrerpolicy="no-referrer" alt=""></button>`).join('')}</div>`:''}<a class="image-link" href="${esc(url)}" target="_blank" rel="noreferrer">원본 이미지 ↗</a>`:'';
  const classification=j.classification?`<div class="block"><h4>감사 분류</h4><p class="kv"><b>${esc(j.classification)}</b>${j.basis?' — '+esc(j.basis):''}</p>${j.reviewRecommendation?`<p class="kv">검토 권고 · ${esc(j.reviewRecommendation)}</p>`:''}</div>`:'';
  const prompt=j.promptVersion?`<p class="kv">프롬프트 ${esc(j.promptVersion)}</p>`:'';

  /* 왜 큐에 올랐나 */
  const signals=row.signals.map(s=>`<div class="sigrow"><strong>${esc(signalById[s.id]?.label||s.id)}</strong><p>${esc(s.reason||signalById[s.id]?.description||'')}</p></div>`).join('');
  const sentences=row.policySentences.length?row.policySentences.map(s=>`<p class="sentence">${esc(s)}</p>`).join(''):'<p class="kv">직접 연결된 정책 문장이 없다.</p>';
  const hasInput=inp.preparedTiles!=null||inp.allTiles!=null||inp.selectedImages!=null||inp.sources.length;
  const inputLine=hasInput?`<p class="kv">${[inp.allTiles!=null||inp.preparedTiles!=null?`타일 <b>${esc(inp.allTiles??'—')}</b> / ${esc(inp.preparedTiles??'—')}`:'',inp.selectedImages!=null?`선택 <b>${esc(inp.selectedImages)}</b>${inp.omittedImages!=null?' · 생략 '+esc(inp.omittedImages):''}`:'',inp.coverage?`커버리지 ${esc(inp.coverage)}`:'',inp.sources.length?`수집 ${esc(inp.sources.join(', '))}`:''].filter(Boolean).join(' &nbsp;·&nbsp; ')}</p>`:'<p class="kv">상세 입력 기록 없음</p>';
  const recovery=[inp.collectionRecovered&&inp.previousCollectionError?`이전 실패 · ${esc(inp.previousCollectionError)} → 이번 실행에서 복구`:'',inp.retryReason?`재시도 · ${esc(inp.retryReason)}`:''].filter(Boolean).map(t=>`<p class="recovery">${t}</p>`).join('');
  const conflict=row.sourceConflict?`<div class="block"><h4>GT 소스 충돌 · ${esc(row.sourceConflict.kind)}</h4><p class="kv">정본 <b>${esc(row.sourceConflict.canonical)||'—'}</b> ${esc(row.sourceConflict.canonicalSource)}${row.sourceConflict.canonicalVersion?' · '+esc(row.sourceConflict.canonicalVersion):''}<br>평가 <b>${esc(row.sourceConflict.evaluation)||'—'}</b> ${esc(row.sourceConflict.evaluationSource)}</p></div>`:'';

  target.innerHTML=`<div class="insp"><header class="insp-head"><div><h2>${esc(row.productName)}</h2><p class="meta">${[row.productKey,row.brand,row.category].filter(Boolean).map(esc).join(' · ')}</p></div>${row.url?`<a class="pdp" href="${esc(row.url)}" target="_blank" rel="noreferrer">상품 페이지 ↗</a>`:''}</header><div class="band">${labels}${why}${chips?`<div class="chips">${chips}</div>`:''}</div><div class="insp-grid"><section class="col" aria-label="판단기가 본 것"><h3>판단기가 본 것</h3>${trail?`<div class="block">${trail}</div>`:''}<div class="block"><h4>근거 문장</h4>${quote}${gallery}</div>${classification}${prompt}</section><section class="col" aria-label="왜 큐에 올랐나"><h3>왜 큐에 올랐나</h3><div class="block">${signals||'<p class="kv">신호 없음</p>'}</div><div class="block"><h4>적용된 정책 문장</h4>${sentences}</div>${conflict}<div class="block"><h4>상세 입력</h4>${inputLine}${recovery}</div></section></div></div>`;
  target.querySelectorAll('.thumbs button').forEach(b=>b.addEventListener('click',()=>{imageIndex=Number(b.dataset.i);renderInspector();}));
}

/* ── 부록: 신호가 어느 목록으로 갔나 ── */
function renderSignalMap(){
  const target=document.getElementById('signal-map');
  target.innerHTML=data.signals.map(s=>{
    const rows=data.rows.filter(row=>row.signals.some(x=>x.id===s.id));
    const where=data.lanes.map(l=>({l,n:rows.filter(r=>r.lane===l.id).length})).filter(x=>x.n).map(x=>`${esc(x.l.title)} ${fmt(x.n)}`).join(' · ');
    return `<tr><td>${esc(s.label)}</td><td class="id">${esc(s.id)}</td><td class="desc">${esc(s.description)}</td><td class="num">${fmt(s.count)}</td><td class="where">${where||'—'}</td></tr>`;
  }).join('');
}

document.getElementById('search').addEventListener('input',ev=>{query=ev.target.value;selectedKey=null;imageIndex=0;renderList();});
renderLaneSummary();renderQuestions();renderTabs();renderList();renderSignalMap();
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    profile = load_profile(args.profile or default_profile())
    root = args.output_root.resolve() if args.output_root else output_root(profile)
    output = args.output.resolve() if args.output else root / "reports" / "catalog-audit.html"
    summary = read_json(root / "run-summary.json", {})
    status = read_json(root / "review" / "status.json", {})
    manifest = read_json(root / "manifest.json", {})
    questions = read_json(root / "reports" / "policy-questions.json", [])
    policy_index = read_json(root / "policy" / "policy-index.json", {})
    verdicts = {
        text(row.get("productKey")): row
        for row in read_jsonl(root / "review" / "verdicts.jsonl")
        if row.get("productKey")
    }
    signal_catalog = profile.get("signals", {}) if isinstance(profile.get("signals"), dict) else {}
    raw_rows = read_queues(root / "queue")
    rows = normalize_rows(raw_rows, verdicts, signal_catalog)
    counts = Counter(text(row.get("signal")) for row in raw_rows if row.get("signal"))

    signal_meta = [
        {
            "id": signal,
            "label": text(signal_catalog.get(signal, {}).get("label")) or signal,
            "description": text(signal_catalog.get(signal, {}).get("description")),
            "priority": int(signal_catalog.get(signal, {}).get("priority", 999)),
            "count": count,
        }
        for signal, count in counts.items()
    ]
    signal_meta.sort(key=lambda item: (item["priority"], -item["count"], item["id"]))

    lane_counts = Counter(row["lane"] for row in rows)
    default_lane = next((lane_id for lane_id in LANE_IDS if lane_counts.get(lane_id)), "ALL")

    precedent_status = {
        text(item.get("id")): text(item.get("status"))
        for item in (policy_index.get("precedents") or [])
        if isinstance(item, dict)
    }
    precedents = [
        {
            "id": text(item.get("id")),
            "status": text(item.get("status")),
            "href": link_from(output, text(item.get("path"))),
            "answers": item.get("answers") or [],
        }
        for item in (policy_index.get("precedents") or [])
        if isinstance(item, dict)
    ]
    precedent_href = {item["id"]: item["href"] for item in precedents}
    question_precedents = (
        policy_index.get("questionPrecedents", {}) if isinstance(policy_index, dict) else {}
    )
    linked_questions = [
        {
            **item,
            "precedents": [
                {"id": pid, "status": precedent_status.get(pid, ""), "href": precedent_href.get(pid, "")}
                for pid in question_precedents.get(text(item.get("id")), [])
            ],
        }
        for item in questions
        if isinstance(item, dict)
    ]

    generated = text(summary.get("generatedAt") or manifest.get("generatedAt"))
    source_dirty = bool(manifest.get("sourceDirty"))
    source_commit = text(manifest.get("sourceCommit"))[:8]
    products = int(summary.get("products", 0) or 0)
    accuracy = float(summary.get("surfaceAccuracy", 0) or 0)
    queued = int(status.get("queuedProducts", status.get("pendingProducts", len(rows))) or 0)
    adjudicated = int(status.get("adjudicatedProducts", 0) or 0)
    policy_counts = policy_index.get("counts", {}) if isinstance(policy_index, dict) else {}
    policy_owned = policy_index.get("owned", {}) if isinstance(policy_index, dict) else {}
    policy_version = text(policy_owned.get("version")) or "—"
    precedent_total = int(policy_counts.get("precedents", 0) or 0)
    precedent_decided = int(policy_counts.get("decided", 0) or 0)
    untracked = int(policy_counts.get("untrackedReviewViolations", 0) or 0)

    title = html.escape(text(profile["displayName"]))
    attribute = html.escape(text(profile["attributeName"]))
    subject = html.escape(text(profile["subjectName"]))
    goal_path = html.escape(text(profile.get("goal") or ""))
    verdict_path = relative_or_absolute(root / "review" / "verdicts.jsonl")
    decision_path = relative_or_absolute(root / "review" / "decisions.json")

    payload = {
        "profile": {"id": profile["id"], "attributeName": profile["attributeName"], "subjectName": profile["subjectName"]},
        "lanes": LANES,
        "defaultLane": default_lane,
        "signals": signal_meta,
        "rows": rows,
        "questions": linked_questions,
        "precedents": precedents,
        "hasVerdicts": bool(verdicts),
    }

    lane_by_id = {lane["id"]: lane for lane in LANES}
    lane_column = lambda lane_id: (  # noqa: E731
        f'<div class="lane"><div class="lane-head"><span class="mark {lane_id}" aria-hidden="true"></span>'
        f'<h2>{html.escape(lane_by_id[lane_id]["title"])}</h2><small>{html.escape(lane_by_id[lane_id]["unit"])}</small></div>'
        f'<p class="lane-count"><span class="num" id="count-{lane_id}">0</span><span>상품</span></p>'
        f'<p class="lane-note">{html.escape(lane_by_id[lane_id]["note"])}</p>'
        f'<div id="groups-{lane_id}"></div>'
    )
    body = f"""<div class="wrap">
  <header class="masthead">
    <div class="masthead-top">
      <p class="kicker">Catalog OS · {html.escape(text(profile['id']))} · 의심 원장</p>
      <p>생성 {html.escape(generated or '알 수 없음')} · 원본 {html.escape(source_commit or 'no commit')}{' <span class="dirty">미커밋 변경 있음</span>' if source_dirty else ''}</p>
    </div>
    <h1>{title}</h1>
    <p class="standfirst">이 보고서는 정확도를 보고하지 않는다. <b>{subject}</b>의 <b>{attribute}</b>이(가) 어긋난 지점에서
      GT와 정책 중 <b>어느 쪽을 고칠지</b>를 근거와 함께 지목하고, 사람이 한 번 답하면 닫히는 질문으로 넘긴다.{f' 귀책 순서는 <span class="mono">{goal_path}</span>가 정한다.' if goal_path else ''}</p>
    <dl class="runbar">
      <div><dt>평가 상품</dt><dd>{products:,}</dd></div>
      <div><dt>표면 정확도</dt><dd>{accuracy:.1%}</dd></div>
      <div><dt>사람 판정</dt><dd>{adjudicated:,} / {queued:,}</dd></div>
      <div><dt>정책</dt><dd>v{html.escape(policy_version)}</dd></div>
      <div><dt>판례</dt><dd>{precedent_total:,} · 확정 {precedent_decided:,}</dd></div>
      <div><dt>미추적 정책 공백</dt><dd>{untracked:,}</dd></div>
    </dl>
  </header>

  <dl class="legend" aria-label="세 라벨의 뜻">
    <div><dt>GT <small>사람 정답</small></dt><dd>골든셋 스냅샷에서 사람이 붙인 라벨. 검사 대상이지 기준이 아니다.</dd></div>
    <div><dt>실행 <small>판단기 출력</small></dt><dd>프롬프트·코드가 이미지와 문구를 보고 실제로 낸 라벨. 도구이지 판정 대상이 아니다.</dd></div>
    <div><dt>정책 답 <small>심판</small></dt><dd>소유 정책 문장만 기계적으로 적용해 낸 라벨. GT도 실행도 보지 않는다. 셋이 다를 때 어느 쪽을 고칠지가 <b>귀책</b>이다.</dd></div>
  </dl>

  <section class="lanes" aria-label="의심 대상 두 갈래">
    {lane_column('GT')}<p class="lane-more">건마다 아래 원장에서 근거를 본다 ↓</p></div>
    {lane_column('POLICY')}<p class="lane-more">군집은 <a href="#questions">정책 질문</a>으로 접혀 있다 ↓</p></div>
  </section>
  <div class="aside-strip">
    <div><span class="num" id="count-RUNTIME">0</span><p><b><span class="mark RUNTIME" aria-hidden="true"></span>실행 결함</b>정책은 답을 내는데 실행이 다른 값을 만들었다. 판정 대상이 아니라 버그로 분리해 넘긴다.</p></div>
    <div><span class="num" id="count-DUAL">0</span><p><b>양쪽 계류</b>실행이 값을 지어냈지만 정책도 그 상품에 답을 낼 근거가 없다. 실행을 고쳐도 정책 공백은 남는다.</p></div>
    <div><span class="num" id="count-NONE">0</span><p><b><span class="mark NONE" aria-hidden="true"></span>충돌 없음</b>정책·GT·실행이 같다. 근거를 어떻게 모았는지만 기록으로 남는다.</p></div>
  </div>
  <div class="aside-strip" id="count-OPEN-wrap" hidden style="border-top:0">
    <div><span class="num" id="count-OPEN">0</span><p><b><span class="mark OPEN" aria-hidden="true"></span>미확정</b>심판 결과가 없어 귀책을 정하지 못했다.</p></div>
  </div>

  <section class="sec" id="questions" aria-labelledby="q-title">
    <div class="sec-head"><h2 id="q-title"><small>의심되는 정책 · 군집 단위</small>한 번 답하면 닫히는 질문</h2><p>사람은 사례마다 라벨을 달지 않는다. 사례를 가르는 질문 하나에 답하고, 그 답이 판례가 되어 정책에 들어간다.</p></div>
    <div id="question-list"></div>
  </section>

  <section class="sec" aria-labelledby="cases-title">
    <div class="sec-head"><h2 id="cases-title"><small>건 단위</small>지목된 사례</h2><p>GT · 실행 · 정책 답을 나란히 놓고, 판단기가 이미지에서 무엇을 봤는지와 적용된 정책 문장을 함께 싣는다. 근거 없이 지목하지 않는다.</p></div>
    <div class="filters" role="group" aria-label="목록 선택"><div id="lane-tabs" style="display:contents"></div><label class="search"><input id="search" type="search" placeholder="상품명 · 키 · 라벨 · 사유 검색" aria-label="검색"><span class="shown" id="shown"></span></label></div>
    <div class="bench">
      <aside class="list" aria-label="사례 목록"><div class="list-body" id="list-body" role="listbox"></div></aside>
      <article class="inspector" id="inspector" aria-live="polite"></article>
    </div>
    <noscript><p class="empty">사례 원장을 보려면 JavaScript를 켠다.</p></noscript>
  </section>

  <section class="sec" aria-labelledby="map-title">
    <div class="sec-head"><h2 id="map-title"><small>부록</small>신호가 어느 목록으로 갔나</h2><p>큐는 신호로 쌓이고 보고서는 귀책으로 읽는다. 같은 신호라도 심판이 다른 곳을 지목할 수 있다.</p></div>
    <table class="map"><thead><tr><th>신호</th><th>ID</th><th>뜻</th><th>큐 건수</th><th>귀책 분포 (상품)</th></tr></thead><tbody id="signal-map"></tbody></table>
  </section>

  <footer>
    산출물 <span class="mono">{html.escape(relative_or_absolute(output))}</span><br>
    심판 추천 <span class="mono">{html.escape(verdict_path)}</span> · 사람 판정 원장 <span class="mono">{html.escape(decision_path)}</span> — 추천은 원장에 자동으로 들어가지 않는다.
  </footer>
</div>
"""
    document = (
        '<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{title}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Hahmlet:wght@300;400;500&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+KR:wght@400;500;600&display=swap" rel="stylesheet">\n'
        f"<style>{STYLE}</style>\n</head>\n<body>\n{body}"
        f'<script id="audit-data" type="application/json">{js_data(intern_strings(compact(payload)))}</script>\n'
        f"<script>{SCRIPT}</script>\n</body>\n</html>\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")

    if isinstance(summary, dict):
        summary.setdefault("artifacts", {})["htmlReport"] = relative_or_absolute(output)
        if "HTML 보고서" not in summary.setdefault("cycle", []):
            summary["cycle"].append("HTML 보고서")
        (root / "run-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
