#!/usr/bin/env python3
"""워크리스트와 스윕 판단을 합쳐 개선 포인트 두 장을 만든다. 확정하지 않는다.

왜 렌더러가 따로인가 — 워크플로우의 에이전트는 `Read`·`Grep`·`Glob`만 갖는다. 판단은 하되
기록하지 않는다는 이 OS의 경계 때문이다. 그래서 판단을 파일로 남기는 일은 스크립트가 맡고,
**숫자는 전부 여기서 다시 만든다.** 에이전트가 센 숫자를 문서에 옮기면 실행마다 달라지고,
달라진 채로 사람의 시간 계획이 된다(프로젝트 규칙 8).

이 스크립트가 쓰는 곳은 `<run>/improvements/`뿐이다. `run-summary.json`을 고치지 않는다 —
심사(review)가 그 요약을 기준선 삼아 다시 세기 때문에, 사이클이 끝난 뒤 요약을 손대면
심사가 무엇과 무엇을 비교했는지 아무도 알 수 없게 된다. 대신 이 산출물이 자기 `basedOn`으로
어느 실행 위에 섰는지 스스로 말한다.

사람 판정 원장(`review/decisions.json`)에는 **쓰지 않는다.** 여기 있는 것은 전부 제안이고,
확정은 `record_review_decision.py`가 사람의 답으로만 한다.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any

from catalog_profile import (
    default_profile,
    load_profile,
    output_root,
    project_path,
    relative_or_absolute,
)
# 화면의 겉모양은 보고서 세 장과 같은 것을 쓴다. 같은 엔진 안이라 import해도 경계를 넘지 않는다.
from render_catalog_report import INDEX_FILE, head

SCHEMA = "catalog-improvements-v1"
SWEEP_SCHEMA = "catalog-improvement-sweep-v1"

# 판정과 반증이 만나 무엇이 되는가. 이 표가 이 스크립트의 전부다.
STATUS_NOTE = {
    "GT_FIX_CANDIDATE": "반증이 GT를 지킬 근거를 못 찾았다. 사람 앞에 놓을 GT 정정 후보다",
    "GT_STANDS": "반증이 GT를 지킬 근거를 찾았다. 후보에서 내리고, 이 신호를 만든 규칙을 의심한다",
    "NEEDS_EVIDENCE": "사진·원장을 더 봐야 갈린다. 근거를 다시 받는 절차로 넘긴다",
    "MOVED_TO_POLICY": "GT 문제가 아니라 정책·실행 문제로 갈렸다. B의 군집으로 옮겨 본다",
    "LABEL_OUT_OF_RANGE": "허용 라벨 밖의 값을 제안했다. 판단을 쓰지 않고 되돌린다",
}
DEFECT_LABEL = {
    "GAP": "공백",
    "MISTRANSLATION": "오역",
    "WEAK_EVIDENCE": "근거 부족",
    "RUNTIME_MISMATCH": "실행 불일치",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def classify(claim: dict[str, Any], refutation: dict[str, Any] | None, labels: list[str]) -> str:
    proposed = str(claim.get("proposedLabel") or "")
    if labels and proposed and proposed not in labels:
        return "LABEL_OUT_OF_RANGE"
    verdict = str((refutation or {}).get("verdict") or "")
    if claim.get("classification") == "NEEDS_MORE_EVIDENCE" or verdict == "INCONCLUSIVE":
        return "NEEDS_EVIDENCE"
    if claim.get("classification") != "GOLDEN_SUSPECT":
        return "MOVED_TO_POLICY"
    if verdict == "GT_STANDS":
        return "GT_STANDS"
    return "GT_FIX_CANDIDATE"


def merge(worklist: dict[str, Any], sweep: dict[str, Any], worklist_path: Path) -> dict[str, Any]:
    labels = list(worklist.get("labels") or [])
    gt_rows = {row["id"]: row for row in worklist.get("gtCandidates") or []}
    cluster_rows = {row["id"]: row for row in worklist.get("policyClusters") or []}

    unknown = [
        item.get("id")
        for item in (sweep.get("gt") or [])
        if item.get("id") not in gt_rows
    ] + [
        item.get("id")
        for item in (sweep.get("clusters") or [])
        if item.get("id") not in cluster_rows
    ]
    if unknown:
        raise SystemExit(
            f"워크리스트에 없는 id가 스윕 결과에 있습니다: {unknown}. "
            "다른 실행의 결과를 합치면 어느 실행의 판단인지 알 수 없게 됩니다."
        )

    gt: list[dict[str, Any]] = []
    for item in sweep.get("gt") or []:
        source = gt_rows[item["id"]]
        claim = item.get("claim") or {}
        refutation = item.get("refutation")
        gt.append(
            {
                "id": item["id"],
                "productKey": source["productKey"],
                "productName": source.get("productName"),
                "currentGoldLabel": source.get("currentGoldLabel"),
                "runLabel": source.get("runLabel"),
                "proposedLabel": claim.get("proposedLabel"),
                "classification": claim.get("classification"),
                "policySentence": claim.get("policySentence") or "",
                "evidence": claim.get("evidence") or [],
                "missingEvidence": claim.get("missingEvidence") or [],
                "question": claim.get("question"),
                "confidence": claim.get("confidence"),
                "refutation": refutation,
                "status": classify(claim, refutation, labels),
                "citedSceneIds": source.get("citedSceneIds") or [],
                "queues": source.get("queues") or [],
            }
        )
    gt.sort(key=lambda row: (list(STATUS_NOTE).index(row["status"]), row["id"]))

    clusters: list[dict[str, Any]] = []
    for item in sweep.get("clusters") or []:
        source = cluster_rows[item["id"]]
        question = item.get("question") or {}
        clusters.append(
            {
                "id": item["id"],
                "clusterKey": source["clusterKey"],
                "owner": source.get("owner"),
                "ownerAction": source.get("ownerAction"),
                # 영향 건수는 워크리스트가 센 값을 그대로 가리킨다. 여기서 다시 세지 않는다.
                "products": source.get("products"),
                "sampleProductKeys": source.get("sampleProductKeys") or [],
                "blockedBy": source.get("blockedBy") or [],
                "defectType": question.get("defectType"),
                "defectReason": question.get("defectReason"),
                "boundary": question.get("boundary"),
                "question": question.get("question"),
                "options": question.get("options") or [],
                "recommendation": question.get("recommendation"),
                "rationale": question.get("rationale"),
                "counterExamples": question.get("counterExamples") or [],
                "duplicates": question.get("duplicates") or "",
                "unread": question.get("unread") or [],
            }
        )
    clusters.sort(key=lambda row: (-(row.get("products") or 0), row["id"]))

    answered_gt = {row["id"] for row in gt}
    answered_clusters = {row["id"] for row in clusters}
    unanswered = [
        {"id": key, "kind": "GT", "productKey": row["productKey"]}
        for key, row in gt_rows.items()
        if key not in answered_gt
    ] + [
        {"id": key, "kind": "CLUSTER", "clusterKey": row["clusterKey"]}
        for key, row in cluster_rows.items()
        if key not in answered_clusters
    ]

    return {
        "schemaVersion": SCHEMA,
        "profileId": worklist.get("profileId"),
        "basedOn": {
            **(worklist.get("basedOn") or {}),
            "worklist": relative_or_absolute(worklist_path),
        },
        "counts": {
            "gtAnswered": len(gt),
            "gtByStatus": {
                status: sum(1 for row in gt if row["status"] == status) for status in STATUS_NOTE
            },
            "clustersAnswered": len(clusters),
            "clusterProducts": sum(row.get("products") or 0 for row in clusters),
            "unanswered": len(unanswered),
        },
        "gt": gt,
        "clusters": clusters,
        "unanswered": unanswered,
        "excluded": worklist.get("excluded") or [],
        "handoff": worklist.get("handoff") or [],
    }


def render_markdown(profile: dict[str, Any], merged: dict[str, Any], worklist_path: Path, sweep_path: Path) -> str:
    based = merged.get("basedOn") or {}
    lines = [
        f"# 개선 포인트 — {profile.get('displayName') or merged.get('profileId')}",
        "",
        "> **여기 있는 것은 전부 제안이다.** 확정은 사람이 하고, 원장은 "
        "`review/decisions.json` 하나뿐이다 — 기록은 스킬 `catalog-review-decision`이 한다.",
        "",
        f"- 기준선: `{based.get('file')}` · 생성 `{based.get('generatedAt')}`",
        f"- 작업 목록: `{relative_or_absolute(worklist_path)}`",
        f"- 스윕 원본: `{relative_or_absolute(sweep_path)}`",
        "",
        "## A. 부족한 GT — 건 단위",
        "",
    ]
    if merged["gt"]:
        lines += [
            "| id | 상품 | 현재 GT → 제안 | 분류 | 반증 | 상태 |",
            "|---|---|---|---|---|---|",
        ]
        for row in merged["gt"]:
            refutation = row.get("refutation") or {}
            lines.append(
                f"| {row['id']} | `{row['productKey']}` {row.get('productName') or ''} "
                f"| {row.get('currentGoldLabel')} → {row.get('proposedLabel')} "
                f"| {row.get('classification')} | {refutation.get('verdict') or '—'} | **{row['status']}** |"
            )
        lines.append("")
        for row in merged["gt"]:
            refutation = row.get("refutation") or {}
            lines += [
                f"### {row['id']} · `{row['productKey']}`",
                "",
                f"- 상태: **{row['status']}** — {STATUS_NOTE.get(row['status'], '')}",
                f"- 정책 문장: {row.get('policySentence') or '**대지 못했다**'}",
                f"- 확신: {row.get('confidence')}",
                "- 근거:",
            ]
            lines += [f"  - {item}" for item in row.get("evidence") or ["(없음)"]]
            if row.get("missingEvidence"):
                lines.append("- 못 본 것:")
                lines += [f"  - {item}" for item in row["missingEvidence"]]
            if refutation:
                lines += [
                    f"- 반증: **{refutation.get('verdict')}** — 가장 약한 고리: {refutation.get('weakestLink')}",
                ]
                lines += [f"  - 유지 근거: {item}" for item in refutation.get("standingEvidence") or []]
            lines += [f"- 사람에게 물을 것: {row.get('question')}", ""]
    else:
        lines += ["돌아온 판정이 없다.", ""]

    lines += ["## B. 부족한 정책 — 군집 단위", ""]
    if merged["clusters"]:
        lines += ["| id | 결함 | 경계 | 영향 | 이미 물어본 것 |", "|---|---|---|---|---|"]
        for row in merged["clusters"]:
            defect = DEFECT_LABEL.get(str(row.get("defectType")), row.get("defectType"))
            lines.append(
                f"| {row['id']} | {defect} | {row.get('boundary')} | {row.get('products')}건 "
                f"| {row.get('duplicates') or '없음'} |"
            )
        lines.append("")
        for row in merged["clusters"]:
            lines += [
                f"### {row['id']} · {row['clusterKey']}",
                "",
                f"- 귀책: {row.get('owner')} — {row.get('ownerAction')}",
                f"- 결함: {DEFECT_LABEL.get(str(row.get('defectType')), row.get('defectType'))}"
                + (f" — {row['defectReason']}" if row.get("defectReason") else ""),
                f"- 영향: {row.get('products')}건 (표본 {', '.join(row.get('sampleProductKeys') or [])})",
                "",
                f"**질문 — {row.get('question')}**",
                "",
            ]
            for option in row.get("options") or []:
                lines.append(
                    f"- `{option.get('choice')}` — 바뀌는 것: {option.get('changes')}"
                    + (f" · 잃는 것: {option.get('cost')}" if option.get("cost") else "")
                )
            lines += [
                "",
                f"- 권고: {row.get('recommendation')}"
                + (f" — {row['rationale']}" if row.get("rationale") else ""),
            ]
            for example in row.get("counterExamples") or []:
                lines.append(
                    f"- 반례: `{example.get('productKey')}` "
                    f"(GT {example.get('currentGoldLabel')} · 실행 {example.get('runLabel')}) "
                    f"— {example.get('whyDifferent')}"
                )
            if row.get("unread"):
                lines += [f"- 못 읽은 것: {item}" for item in row["unread"]]
            lines.append("")
    else:
        lines += ["돌아온 질문이 없다.", ""]

    lines += ["## 이 스윕이 다루지 않은 것", ""]
    for item in merged.get("handoff") or []:
        lines.append(f"- 귀책 `{item['owner']}` {item['products']}건 — {item['note']}")
    for item in merged.get("excluded") or []:
        size = item.get("products") or item.get("clusters")
        lines.append(f"- {item['reason']} — {size}")
    if merged.get("unanswered"):
        lines.append("")
        lines.append("### 빈자리 — 물었는데 답이 돌아오지 않은 것")
        for item in merged["unanswered"]:
            lines.append(f"- {item['id']} ({item['kind']}) `{item.get('productKey') or item.get('clusterKey')}`")
    lines.append("")
    return "\n".join(lines)


def render_html(
    profile: dict[str, Any], merged: dict[str, Any], worklist_path: Path, sweep_path: Path, back_href: str
) -> str:
    """서버에서 읽는 자리. 내용은 마크다운과 같고, 숫자도 같은 `merged`에서 온다.

    표지가 이 파일을 선언으로 알지 않는다 — `run-summary.json`을 고치지 않기 때문이다.
    대신 표지가 열릴 때 `improvements.json`을 찾아 보고, 있으면 링크를 띄운다.
    """
    esc = lambda value: html.escape("" if value is None else str(value))  # noqa: E731
    based = merged.get("basedOn") or {}
    title = f"개선 포인트 · {profile.get('displayName') or merged.get('profileId')}"
    parts = [
        '<div class="wrap">',
        '<header class="masthead">',
        f'<div class="masthead-top"><p class="kicker">Catalog OS · {esc(merged.get("profileId"))} · 개선 포인트</p>'
        f'<nav><a href="{esc(back_href)}">표지 →</a></nav></div>',
        f"<h1><small>제안 · 확정 아님</small>{esc(profile.get('displayName') or merged.get('profileId'))}</h1>",
        '<p style="margin-top:14px;color:var(--muted)">여기 있는 것은 전부 제안이다. 확정은 사람이 하고, '
        "원장은 <span class=\"mono\">review/decisions.json</span> 하나뿐이다.</p>",
        '<p class="mono" style="margin-top:8px;font-size:11px;color:var(--faint)">'
        f"기준선 {esc(based.get('file'))} · 생성 {esc(based.get('generatedAt'))}<br>"
        f"작업 목록 {esc(relative_or_absolute(worklist_path))} · 스윕 원본 {esc(relative_or_absolute(sweep_path))}</p>",
        "</header>",
    ]

    parts += ['<section class="sec"><div class="sec-head"><h2><small>A · 건 단위</small>부족한 GT</h2></div>']
    if merged["gt"]:
        parts.append(
            '<table class="map"><thead><tr><th>id</th><th>상품</th><th>현재 GT → 제안</th>'
            "<th>분류</th><th>반증</th><th>상태</th></tr></thead><tbody>"
        )
        for row in merged["gt"]:
            refutation = row.get("refutation") or {}
            parts.append(
                f'<tr><td class="id">{esc(row["id"])}</td><td><span class="mono">{esc(row["productKey"])}</span> '
                f'{esc(row.get("productName"))}</td><td class="mono">{esc(row.get("currentGoldLabel"))} → '
                f'{esc(row.get("proposedLabel"))}</td><td>{esc(row.get("classification"))}</td>'
                f'<td>{esc(refutation.get("verdict") or "—")}</td><td><b>{esc(row["status"])}</b><br>'
                f'<span style="color:var(--muted)">{esc(STATUS_NOTE.get(row["status"], ""))}</span></td></tr>'
            )
        parts.append("</tbody></table>")
    else:
        parts.append('<p style="margin-top:14px;color:var(--muted)">돌아온 판정이 없다.</p>')
    parts.append("</section>")

    parts += ['<section class="sec"><div class="sec-head"><h2><small>B · 군집 단위</small>부족한 정책</h2></div>']
    if not merged["clusters"]:
        parts.append('<p style="margin-top:14px;color:var(--muted)">돌아온 질문이 없다.</p>')
    for row in merged["clusters"]:
        defect = DEFECT_LABEL.get(str(row.get("defectType")), row.get("defectType"))
        parts += [
            '<article style="margin-top:34px;padding-top:18px;border-top:1px solid var(--rule)">',
            f'<p class="kicker">{esc(row["id"])} · {esc(defect)} · 영향 {esc(row.get("products"))}건 · '
            f'귀책 {esc(row.get("owner"))}</p>',
            f'<h3 style="margin-top:8px;font-size:1.15rem;line-height:1.45">{esc(row.get("question"))}</h3>',
            f'<p class="mono" style="margin-top:6px;font-size:11px;color:var(--faint)">{esc(row["clusterKey"])}</p>',
            '<table class="map" style="margin-top:16px"><thead><tr><th>선택지</th><th>바뀌는 것</th>'
            "<th>잃는 것</th></tr></thead><tbody>",
        ]
        for option in row.get("options") or []:
            parts.append(
                f'<tr><td>{esc(option.get("choice"))}</td><td class="desc">{esc(option.get("changes"))}</td>'
                f'<td class="desc">{esc(option.get("cost"))}</td></tr>'
            )
        parts += [
            "</tbody></table>",
            f'<p style="margin-top:14px"><b>권고</b> — {esc(row.get("recommendation"))}</p>',
            f'<p style="margin-top:10px;color:var(--muted)"><b>결함</b> — {esc(row.get("defectReason"))}</p>',
            f'<p style="margin-top:10px;color:var(--muted)"><b>경계</b> — {esc(row.get("boundary"))}</p>',
            f'<p style="margin-top:10px;color:var(--muted)"><b>이미 물어본 것</b> — {esc(row.get("duplicates") or "없음")}</p>',
        ]
        if row.get("counterExamples"):
            parts.append('<ul style="margin-top:10px;padding-left:18px">')
            for example in row["counterExamples"]:
                parts.append(
                    f'<li><span class="mono">{esc(example.get("productKey"))}</span> '
                    f'(GT {esc(example.get("currentGoldLabel"))} · 실행 {esc(example.get("runLabel"))}) — '
                    f'{esc(example.get("whyDifferent"))}</li>'
                )
            parts.append("</ul>")
        parts.append(
            '<details style="margin-top:12px"><summary style="cursor:pointer;color:var(--muted)">근거와 못 읽은 것</summary>'
            f'<p style="margin-top:8px;color:var(--muted)">{esc(row.get("rationale"))}</p>'
            + (
                '<ul style="margin-top:8px;padding-left:18px;color:var(--muted)">'
                + "".join(f"<li>{esc(item)}</li>" for item in row.get("unread") or [])
                + "</ul>"
                if row.get("unread")
                else ""
            )
            + "</details></article>"
        )
    parts.append("</section>")

    parts += ['<section class="sec"><div class="sec-head"><h2><small>범위 밖</small>이 스윕이 다루지 않은 것</h2></div><ul style="margin-top:14px;padding-left:18px">']
    for item in merged.get("handoff") or []:
        parts.append(f'<li>귀책 <span class="mono">{esc(item["owner"])}</span> {esc(item["products"])}건 — {esc(item["note"])}</li>')
    for item in merged.get("excluded") or []:
        keys = item.get("nextClusterKeys") or item.get("nextProductKeys") or []
        parts.append(
            f'<li>{esc(item["reason"])} — {esc(item.get("products") or item.get("clusters"))}'
            + (f'<br><span class="mono" style="font-size:11px;color:var(--faint)">{esc(" · ".join(keys))}</span>' if keys else "")
            + "</li>"
        )
    for item in merged.get("unanswered") or []:
        parts.append(
            f'<li>답이 돌아오지 않았다 — {esc(item["id"])} <span class="mono">'
            f'{esc(item.get("productKey") or item.get("clusterKey"))}</span></li>'
        )
    parts += ["</ul></section>", '<footer style="margin:60px 0 40px"></footer>', "</div>"]
    return head(title) + "\n".join(parts) + "\n</body>\n</html>\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--sweep", type=Path, help="워크플로우가 돌려준 JSON. 기본은 improvements/sweep-raw.json")
    args = parser.parse_args()

    profile = load_profile((args.profile or default_profile()).resolve())
    root = args.output_root.resolve() if args.output_root else output_root(profile)
    folder = root / "improvements"
    worklist_path = folder / "worklist.json"
    sweep_path = args.sweep.resolve() if args.sweep else folder / "sweep-raw.json"

    if not worklist_path.is_file():
        raise SystemExit(f"작업 목록이 없습니다: {worklist_path}. build_improvement_worklist.py를 먼저 돌리세요.")
    if not sweep_path.is_file():
        raise SystemExit(
            f"스윕 결과가 없습니다: {sweep_path}. 워크플로우가 돌려준 JSON을 그대로 이 파일에 저장하세요."
        )

    worklist = read_json(worklist_path)
    sweep = read_json(sweep_path)
    if sweep.get("schemaVersion") != SWEEP_SCHEMA:
        raise SystemExit(f"{sweep_path}: {SWEEP_SCHEMA}가 아닙니다.")
    pointed_at = sweep.get("worklist")
    if pointed_at and project_path(str(pointed_at)) != worklist_path:
        raise SystemExit(
            f"스윕이 다른 작업 목록을 보고 판단했습니다: {pointed_at} != {relative_or_absolute(worklist_path)}. "
            "두 실행을 섞으면 어느 실행의 판단인지 알 수 없습니다."
        )

    merged = merge(worklist, sweep, worklist_path)
    (folder / "improvements.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (folder / "improvements.md").write_text(
        render_markdown(profile, merged, worklist_path, sweep_path), encoding="utf-8"
    )
    # 표지로 돌아가는 링크. 보고서 폴더는 사이클이 정하므로 기본 자리(`<run>/reports/`)를 가리킨다.
    back_href = os.path.relpath(root / "reports" / INDEX_FILE, folder)
    (folder / "improvements.html").write_text(
        render_html(profile, merged, worklist_path, sweep_path, back_href), encoding="utf-8"
    )
    print(relative_or_absolute(folder / "improvements.json"))
    print(relative_or_absolute(folder / "improvements.md"))
    print(relative_or_absolute(folder / "improvements.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
