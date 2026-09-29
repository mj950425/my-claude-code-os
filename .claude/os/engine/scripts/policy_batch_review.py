#!/usr/bin/env python3
"""Run resumable, blind-first policy reviews for a frozen case manifest."""
from __future__ import annotations

import argparse
import anyio
import hashlib
import html
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import policy_multi_agent_eval as evaluator
from policy_prompt import allowed_values, render_agent_policy
from policy_sdk_runtime import DEFAULT_MODEL, force_subscription_authentication

_META_KEYS = ("sellerProductId", "targetId", "targetVersion", "completedAt")


def _metadata(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
    return {key: case[key] if key in case else nested[key]
            for key in _META_KEYS if key in case or key in nested}


def _image_failures(case: dict[str, Any]) -> list[dict[str, str]]:
    metadata = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
    rows = metadata.get("imageFailures", case.get("imageErrors", []))
    if not isinstance(rows, list):
        rows = [rows]
    failures = []
    for row in rows:
        if isinstance(row, dict):
            failures.append({key: str(row.get(key) or "") for key in ("role", "url", "error")})
        else:
            failures.append({"role": "unknown", "url": "", "error": str(row)})
    return failures


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_path(raw: str, manifest_dir: Path) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else manifest_dir / path).resolve()


def load_manifest(path: Path) -> tuple[dict[str, Any], Path, Path | None, dict[str, list[str]]]:
    manifest_path = path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("manifest.version은 1이어야 합니다")
    if not isinstance(manifest.get("field"), str) or not manifest["field"]:
        raise ValueError("manifest.field가 필요합니다")
    if not isinstance(manifest.get("cases"), list) or not manifest["cases"]:
        raise ValueError("manifest.cases는 비어 있지 않은 목록이어야 합니다")
    definitions = _resolve_path(manifest.get("definitions", ""), manifest_path.parent)
    if not definitions.is_file():
        raise ValueError(f"definitions 파일이 없습니다: {definitions}")
    profile_raw = manifest.get("profile")
    profile = _resolve_path(profile_raw, manifest_path.parent) if profile_raw else None
    if profile is not None and not profile.is_file():
        raise ValueError(f"profile 파일이 없습니다: {profile}")

    seen: set[str] = set()
    image_hashes: dict[str, list[str]] = {}
    for case in manifest["cases"]:
        if not isinstance(case, dict):
            raise ValueError("각 case는 객체여야 합니다")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("case.id는 비어 있지 않은 고유 문자열이어야 합니다")
        seen.add(case_id)
        if "expected" in case:
            raise ValueError(f"{case_id}: manifest에는 gold 필드 expected를 넣지 않습니다")
        if not isinstance(case.get("data"), dict):
            raise ValueError(f"{case_id}: data 객체가 필요합니다")
        if any(key in case["data"] for key in ("expected", "productionValue", "production_value")):
            raise ValueError(f"{case_id}: expected/production 값은 data가 아닌 manifest 최상위 comparison 필드로 둡니다")
        images = case.get("images", [])
        if not isinstance(images, list) or any(not isinstance(item, str) for item in images):
            raise ValueError(f"{case_id}: images는 경로 문자열 목록이어야 합니다")
        resolved: list[str] = []
        hashes: list[str] = []
        for image in images:
            image_path = _resolve_path(image, manifest_path.parent)
            if not image_path.is_file():
                raise ValueError(f"{case_id}: 이미지 파일이 없습니다: {image_path}")
            resolved.append(str(image_path))
            hashes.append(_file_hash(image_path))
        case["images"] = resolved
        image_hashes[case_id] = hashes
        production_value = case.get("productionValue", case.get("production_value"))
        if production_value is not None and not isinstance(production_value, str):
            raise ValueError(f"{case_id}: productionValue는 문자열이어야 합니다")
        case["productionValue"] = production_value
    return manifest, definitions, profile, image_hashes


def input_hash(case: dict[str, Any], image_hashes: list[str]) -> str:
    return _sha256(_canonical({"case": case, "imageHashes": image_hashes}))


def policy_hash(manifest: dict[str, Any], definitions: Path, profile: Path | None) -> str:
    payload = {
        "manifest": manifest,
        "definitionsHash": _file_hash(definitions),
        "profileHash": _file_hash(profile) if profile else None,
        "field": manifest["field"],
        "model": manifest.get("model", DEFAULT_MODEL),
    }
    return _sha256(_canonical(payload))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
        json.dump(value, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_path = Path(tmp.name)
    temp_path.replace(path)


def _safe_result(case: dict[str, Any], raw_result: dict[str, Any]) -> dict[str, Any]:
    """Keep the model trace and outcome while removing the internal comparison-as-expected alias."""
    result = dict(raw_result)
    result.pop("policy", None)
    result.pop("scored", None)
    result.pop("correct", None)
    result_case = dict(case)
    result_case.pop("expected", None)
    result_case.pop("comparisonLabel", None)
    result["case"] = result_case
    extracted = (result.get("output") or {}).get("value")
    production = case.get("productionValue")
    result["productionComparison"] = {
        "source": "productionValue",
        "isGold": False,
        "productionValue": production,
        "extractedValue": extracted,
        "matches": None if production is None or extracted is None else production == extracted,
    }
    return result


async def _run_case(case: dict[str, Any], definitions: Path, profile: Path | None,
                    field: str, model: str, subject_key: str | None) -> dict[str, Any]:
    evaluation_case: dict[str, Any] = {
        "id": case["id"], "data": dict(case["data"]), "images": case["images"],
    }
    production_value = case.get("productionValue")
    failures = _image_failures(case)
    if failures:
        data = evaluation_case["data"]
        data["evidenceAvailabilityNote"] = (
            "이미지 다운로드 실패로 일부 증거 역할이 입력에 없습니다. 누락된 이미지가 실제로 "
            "없다고 추론하지 말고, 남은 근거가 부족하면 불확실하다고 보고하세요. 누락 역할: "
            + ", ".join(sorted({failure["role"] or "unknown" for failure in failures}))
        )
    if production_value is not None:
        # policy_multi_agent_eval keeps this out of READ input/prompt and only gives it to REVIEW.
        evaluation_case["expected"] = production_value
        evaluation_case["comparisonLabel"] = "생산 시스템 예측값(비교용, 골드 아님)"
    evaluation_case.update(_metadata(case))
    product_key = subject_key or case["id"]
    raw = await evaluator.run(evaluation_case, definitions, field, model, profile, str(product_key))
    result = _safe_result(case, raw)
    if failures:
        result["imageAvailability"] = {"complete": False, "failures": failures}
    else:
        result["imageAvailability"] = {"complete": True, "failures": []}
    return result


def build_html(manifest: dict[str, Any], results: dict[str, dict[str, Any]],
               report_dir: Path | None = None,
               value_names: dict[str, str] | None = None) -> str:
    cards: list[str] = []
    total_image_failures = sum(len(_image_failures(case)) for case in manifest["cases"])
    value_names = value_names or {}
    status_names = {"MATCH": "운영 값과 일치", "MISMATCH": "운영 값과 불일치",
                    "NO_GOLD": "비교값 없음", "MATCH_AFTER_RETRY": "재판독 후 일치",
                    "HUMAN_REVIEW": "사람 검토 필요", "GT_SUSPECT": "운영 값 재확인 후보",
                    "POLICY_GAP": "정책 경계 검토 필요", "EXTRACT_ERROR": "판독 오류 의견",
                    "ERROR": "실행 오류", "INVALID_RESPONSE": "응답 형식 오류",
                    "MISSING_RESULT": "진행 또는 대기 중"}

    def readable(value: Any) -> str:
        raw = "없음" if value is None else str(value)
        return f"{value_names[raw]} ({raw})" if raw in value_names else raw

    counts = {"total": len(manifest["cases"]), "attempted": 0, "completed": 0,
              "match": 0, "mismatch": 0, "missingEvidence": 0, "error": 0, "pending": 0}
    for case in manifest["cases"]:
        result = results.get(case["id"])
        if result is None or result.get("status") == "MISSING_RESULT":
            counts["pending"] += 1
            continue
        counts["attempted"] += 1
        comparison = result.get("productionComparison", {})
        if comparison.get("matches") is False:
            counts["mismatch"] += 1
        elif comparison.get("matches") is True:
            counts["match"] += 1
        if result.get("status") in ("ERROR", "INVALID_RESPONSE"):
            counts["error"] += 1
        elif (result.get("output") or {}).get("value") is not None:
            counts["completed"] += 1
        if (_image_failures(case)
                or any(not Path(path).is_file() for path in case.get("images", []))):
            counts["missingEvidence"] += 1
    for case in manifest["cases"]:
        result = results.get(case["id"])
        if not result:
            result = {"status": "MISSING_RESULT", "steps": [], "output": None,
                      "productionComparison": {"source": "productionValue", "isGold": False,
                                               "productionValue": case.get("productionValue"),
                                               "extractedValue": None, "matches": None}}
        comparison = result.get("productionComparison", {})
        output = result.get("output") or {}
        extracted = output.get("value", "미산출")
        production = comparison.get("productionValue")
        matches = comparison.get("matches")
        status = result.get("status", "UNKNOWN")
        steps = result.get("steps", [])
        read = next((step.get("response", {}).get("structured_output") for step in steps
                     if step.get("stage") == "READ"), None)
        first_value = read.get("value") if isinstance(read, dict) else None
        retry = next((step.get("response", {}).get("structured_output") for step in steps
                      if step.get("stage") == "RETRY"), None)
        retry_value = retry.get("value") if isinstance(retry, dict) else None
        verdict = next(((step.get("response", {}).get("structured_output") or {}).get("verdict")
                        for step in result.get("steps", [])
                        if step.get("stage") == "REVIEW"), None)
        review_reason = next(((step.get("response", {}).get("structured_output") or {}).get("reason")
                              for step in result.get("steps", [])
                              if step.get("stage") == "REVIEW"), None)
        reason = output.get("reason", result.get("error", ""))
        meta = _metadata(case)
        meta_html = " · ".join(f"{html.escape(key)}: {html.escape(str(value))}"
                               for key, value in meta.items())
        snapshot_note = ((case.get("metadata") or {}).get("evidenceSnapshot")
                         if isinstance(case.get("metadata"), dict) else None)
        if snapshot_note == "Current catalog/images downloaded for review, not a byte-identical replay of historical inference":
            snapshot_note = "현재 카탈로그에서 이미지를 받아 검수합니다. 과거 추론 당시 이미지와 바이트 단위로 같은 자료인지는 보장되지 않습니다."
        snapshot_html = (f'<p class="snapshot-note">증거 시점 주의: {html.escape(str(snapshot_note))}</p>'
                         if snapshot_note else '<p class="snapshot-note">과거 추론 당시와 동일한 이미지 스냅샷인지는 확인되지 않았습니다.</p>')
        image_nodes = []
        evidence_set = set(output.get("evidenceImageIds", []))
        image_roles = {item.get("path"): item.get("role")
                       for item in (case.get("data") or {}).get("imageRoles", [])
                       if isinstance(item, dict)}
        for index, path in enumerate(case.get("images", []), start=1):
            image_id = f"image-{index:02d}"
            role = image_roles.get(path, "")
            role_name = {"THUMBNAIL": "썸네일", "THUMBNAIL_EXTRA": "추가 썸네일",
                         "DETAIL": "상세", "DETAIL_TILE": "상세 조각"}.get(role, role)
            evidence_class = " cited" if image_id in evidence_set else ""
            image_href = os.path.relpath(path, report_dir) if report_dir else str(path)
            try:
                image_nodes.append(
                    f'<figure class="evidence{evidence_class}"><a href="{html.escape(str(image_href), quote=True)}" target="_blank" rel="noreferrer">'
                    f'<img loading="lazy" src="{html.escape(str(image_href), quote=True)}" alt="{html.escape(case["id"])} {image_id}"></a>'
                    f'<figcaption>{image_id} · {html.escape(str(role_name))}{" · 인용 근거" if image_id in evidence_set else ""}</figcaption></figure>'
                )
            except (OSError, ValueError):
                image_nodes.append(f'<span class="image-error">이미지 미리보기 실패: {html.escape(Path(path).name)}</span>')
        images = "".join(image_nodes)
        evidence_ids = output.get("evidenceImageIds", [])
        evidence = ", ".join(html.escape(str(value)) for value in evidence_ids) or "명시된 이미지 근거 없음"
        failures = _image_failures(case)
        missing_local_images = [path for path in case.get("images", []) if not Path(path).is_file()]
        failure_html = ""
        if failures or missing_local_images:
            rows = "".join(
                f'<li><b>{html.escape(item["role"] or "unknown")}</b>: '
                f'{html.escape(item["error"] or "다운로드 실패")}'
                + (f' <a href="{html.escape(item["url"], quote=True)}">원본 URL</a>'
                   if item["url"].startswith(("https://", "http://")) else "")
                + "</li>"
                for item in failures
            )
            missing_rows = "".join(f'<li>로컬 이미지 파일 없음: {html.escape(Path(path).name)}</li>'
                                    for path in missing_local_images)
            failure_html = (f'<div class="warning"><b>이미지 증거 누락</b><ul>{rows}{missing_rows}</ul>'
                            '판정이 완전한 이미지 검토를 포함하지 않습니다.</div>')
        match_label = "일치" if matches is True else "불일치" if matches is False else "비교값 없음"
        filters = []
        if matches is False:
            filters.append("mismatch")
        if failures or missing_local_images:
            filters.append("missing")
        if status in ("ERROR", "INVALID_RESPONSE"):
            filters.append("error")
        if status == "MISSING_RESULT":
            filters.append("pending")
        product_name = (case.get("data") or {}).get("productName") or "상품명 없음"
        retry_label = readable(retry_value) if retry_value is not None else "재시도 없음"
        first_reason = str(read.get("reason", "")) if isinstance(read, dict) else ""
        first_reason_html = (f'<details><summary>첫 판독 근거</summary><p class="reason">{html.escape(first_reason)}</p></details>'
                             if first_reason and (verdict or retry_value is not None) else "")
        cards.append(f"""<article id="case-{html.escape(case['id'], quote=True)}" class="case" data-filter="{' '.join(filters)}">
  <header><div><h2>{html.escape(case['id'])}</h2><p class="product-name">{html.escape(str(product_name))}</p></div><span class="status">{html.escape(status_names.get(str(status), str(status)))}</span></header>
  <p class="meta">{meta_html}</p>
  {snapshot_html}
  {failure_html}
  <div class="images">{images or '<span>등록 이미지 없음</span>'}</div>
  <dl><dt>첫 판독 (READ)</dt><dd>{html.escape(readable(first_value))}</dd>
      <dt>최종 정책 추출값</dt><dd>{html.escape(readable(extracted))}</dd>
      <dt>재시도 결과</dt><dd>{html.escape(retry_label)}</dd>
      <dt>운영 시스템 예측값</dt><dd>{html.escape(readable(production))} · 정답 아님</dd>
      <dt>비교</dt><dd>{match_label}</dd>
      <dt>독립 검수</dt><dd>{html.escape(status_names.get(str(verdict), str(verdict or '수행 안 함')))}</dd>
      <dt>추출 이미지 근거</dt><dd>{evidence}</dd></dl>
  <p><b>독립 검수 이유</b>: {html.escape(str(review_reason or '없음'))}</p>
  {first_reason_html}
  <p class="reason"><b>최종 판정 이유</b>: {html.escape(str(reason))}</p>
</article>""")
    title = html.escape(str(manifest.get("policyId") or manifest["field"]))
    summary_html = (f'<section class="summary"><b>시도 {counts["attempted"]}/{counts["total"]}</b>'
                    f'<span>유효 결과 완료 {counts["completed"]}</span>'
                    f'<span>오류 {counts["error"]}</span><span>대기 중 {counts["pending"]}</span>'
                    f'<span>운영 예측과 일치 {counts["match"]}</span>'
                    f'<span>운영 예측과 불일치 {counts["mismatch"]}</span>'
                    f'<span>이미지 증거 누락 {counts["missingEvidence"]}</span></section>')
    filters_html = ('<nav class="filters" aria-label="결과 필터">'
                    '<button data-show="all">전체</button><button data-show="mismatch">예측 불일치</button>'
                    '<button data-show="missing">이미지 증거 누락</button><button data-show="error">오류</button>'
                    '<button data-show="pending">대기 중</button></nav>')
    return f"""<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>정책 배치 검수 · {title}</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;background:#f3f4f6;color:#1f2937;margin:0}}
main{{max-width:1100px;margin:auto;padding:28px 20px}}h1{{margin:0 0 6px}}.intro{{color:#4b5563;margin:0 0 22px}}
.case{{background:#fff;border:1px solid #d1d5db;border-radius:10px;margin:14px 0;padding:16px}}.case[hidden]{{display:none}}
.product-name{{margin:4px 0;color:#4b5563;font-weight:500}}.summary{{display:flex;gap:18px;flex-wrap:wrap;background:#fff;padding:14px;border-radius:8px;margin:12px 0}}
.snapshot-note{{background:#f8fafc;border-left:3px solid #64748b;padding:8px 10px;color:#475569;font-size:12px}}
.filters{{display:flex;gap:8px;margin:12px 0}}.filters button{{border:1px solid #cbd5e1;background:white;border-radius:5px;padding:7px 12px;cursor:pointer}}
.filters button[aria-pressed="true"]{{background:#174ea6;color:white;border-color:#174ea6}}
.warning{{background:#fff2cc;border:1px solid #d99b00;padding:10px;border-radius:6px;margin:10px 0}}.warning ul{{margin:4px 0}}
header{{display:flex;justify-content:space-between;align-items:center}}h2{{font-size:19px;margin:0}}.status{{font-weight:700;color:#174ea6}}
.meta{{font-size:12px;color:#6b7280}}.images{{display:flex;gap:8px;overflow:auto;margin:12px 0}}
.images figure{{flex:0 0 220px;box-sizing:border-box;margin:0;padding:5px;border:1px solid #e5e7eb;border-radius:6px}}.images figure.cited{{border:3px solid #16803c}}
.images img{{width:100%;height:250px;object-fit:contain;border-radius:4px}}figcaption{{font-size:11px;color:#4b5563}}
dl{{display:grid;grid-template-columns:150px 1fr;gap:4px 12px;margin:10px 0}}dt{{font-weight:700}}dd{{margin:0}}.reason{{white-space:pre-wrap}}
</style><main><h1>정책 배치 검수 · {title}</h1>
<p class="intro">운영 시스템 값은 비교용 예측이며 정답(Gold)으로 취급하지 않습니다. 검수 의견은 사람이 확정한 정답이 아닙니다. 기록된 케이스: {len(results)} · 이미지 다운로드 실패: {total_image_failures}건.</p>
{summary_html}{filters_html}{''.join(cards)}
<script>const filterButtons = Array.from(document.querySelectorAll('.filters button'));
function showFilter(filter) {{
 filterButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.show === filter)));
 document.querySelectorAll('.case').forEach(card => {{ card.hidden = filter !== 'all' && !card.dataset.filter.split(' ').includes(filter); }});
}}
filterButtons.forEach(button => button.addEventListener('click', () => showFilter(button.dataset.show)));
const initialFilter = new URLSearchParams(location.search).get('filter');
showFilter(filterButtons.some(button => button.dataset.show === initialFilter) ? initialFilter : 'all');
</script></main></html>"""


async def run_batch(manifest: dict[str, Any], definitions: Path, profile: Path | None,
                    output_dir: Path, concurrency: int, model: str,
                    subject_key: str | None, case_timeout: float = 900) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "results.json"
    report_path = output_dir / "review.html"
    hashes = {case["id"]: input_hash(case, manifest["_imageHashes"][case["id"]])
              for case in manifest["cases"]}
    manifest_digest = _sha256(_canonical({
        "manifest": {key: value for key, value in manifest.items() if key != "_imageHashes"},
        "policyHash": policy_hash(manifest, definitions, profile),
        "inputHashes": hashes,
    }))
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("manifestHash") != manifest_digest:
            raise ValueError("저장된 결과의 매니페스트/정책/입력 해시가 달라 재개할 수 없습니다. 다른 output-dir을 지정하세요")
        results = state.get("results", {})
    else:
        results = {}
    manifest_for_report = {key: value for key, value in manifest.items() if not key.startswith("_")}
    lock = anyio.Lock()
    semaphore = anyio.Semaphore(concurrency)

    async def worker(case: dict[str, Any]) -> None:
        case_id = case["id"]
        previous = results.get(case_id)
        if previous and previous.get("inputHash") == hashes[case_id] and previous.get("status") not in ("ERROR", "INVALID_RESPONSE"):
            print(f"[resume] {case_id}", flush=True)
            return
        async with semaphore:
            try:
                with anyio.fail_after(case_timeout):
                    result = await _run_case(case, definitions, profile, manifest["field"], model, subject_key)
            except TimeoutError:
                result = {
                    "status": "ERROR", "errorCategory": "CASE_TIMEOUT",
                    "error": f"Case exceeded {case_timeout:g} seconds.",
                    "case": {"id": case.get("id"), "metadata": _metadata(case)},
                    "productionComparison": {"source": "productionValue", "isGold": False,
                                             "productionValue": case.get("productionValue"),
                                             "extractedValue": None, "matches": None},
                    "imageAvailability": {"complete": not bool(_image_failures(case)),
                                          "failures": _image_failures(case)},
                }
            except Exception as exc:
                result = {
                    "status": "ERROR", "errorCategory": "RUNNER_ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                    "case": {"id": case.get("id"), "metadata": _metadata(case)},
                    "productionComparison": {
                        "source": "productionValue", "isGold": False,
                        "productionValue": case.get("productionValue"),
                        "extractedValue": None, "matches": None,
                    },
                    "imageAvailability": {"complete": not bool(_image_failures(case)),
                                          "failures": _image_failures(case)},
                }
            result["inputHash"] = hashes[case_id]
            async with lock:
                results[case_id] = result
                _atomic_json(state_path, {"version": 1, "manifestHash": manifest_digest,
                                          "results": results})
            print(f"[done] {case_id}: {result.get('status')}", flush=True)

    async with anyio.create_task_group() as group:
        for case in manifest["cases"]:
            group.start_soon(worker, case)
    _atomic_json(state_path, {"version": 1, "manifestHash": manifest_digest, "results": results})
    names = {row["code"]: row["name"] for row in allowed_values(definitions, manifest["field"])}
    report_path.write_text(build_html(manifest_for_report, results, report_path.parent, names), encoding="utf-8")
    completed = sum(result.get("status") not in ("ERROR", "INVALID_RESPONSE")
                    for result in results.values())
    return {"state": state_path, "report": report_path, "manifestHash": manifest_digest,
            "completed": completed, "cases": len(manifest["cases"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--model")
    parser.add_argument("--case-timeout", type=float, default=900,
                        help="1케이스 전체(READ/REVIEW/재시도)의 제한 초 (기본 900)")
    parser.add_argument("--subject-key", help="모든 케이스에 적용할 선택적 판례 자기참조 제외 키")
    args = parser.parse_args()
    if args.concurrency < 1 or args.concurrency > 8:
        parser.error("--concurrency는 1~8이어야 합니다")
    if args.case_timeout <= 0:
        parser.error("--case-timeout은 양수여야 합니다")
    try:
        manifest, definitions, profile, image_hashes = load_manifest(args.manifest)
        _, allowed = render_agent_policy(definitions, manifest["field"])
        for case in manifest["cases"]:
            if case["productionValue"] is not None and case["productionValue"] not in allowed:
                raise ValueError(f"{case['id']}: productionValue가 정책 허용값에 없습니다")
        manifest["_imageHashes"] = image_hashes
        model = args.model or manifest.get("model") or DEFAULT_MODEL
        manifest["model"] = model
        force_subscription_authentication()
        summary = anyio.run(run_batch, manifest, definitions, profile, args.output_dir,
                            args.concurrency, model, args.subject_key, args.case_timeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"state": str(summary["state"]), "report": str(summary["report"]),
                      "manifestHash": summary["manifestHash"], "completed": summary["completed"],
                      "cases": summary["cases"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
