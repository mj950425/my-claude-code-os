"""Merge successful recovery runs after the original batch has stopped."""
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[2] / "engine/scripts"))
import policy_batch_review as batch


def load(name):
    manifest, definitions, profile, hashes = batch.load_manifest(ROOT / name)
    manifest["_imageHashes"] = hashes
    manifest["model"] = manifest.get("model") or batch.DEFAULT_MODEL
    return manifest, definitions, profile


def digest(manifest, definitions, profile):
    return batch._sha256(batch._canonical({
        "manifest": {k: v for k, v in manifest.items() if k != "_imageHashes"},
        "policyHash": batch.policy_hash(manifest, definitions, profile),
        "inputHashes": {case["id"]: batch.input_hash(case, manifest["_imageHashes"][case["id"]])
                        for case in manifest["cases"]},
    }))


manifest, definitions, profile = load("manifest.json")
cases = {case["id"]: case for case in manifest["cases"]}
state_path = ROOT / "review/results.json"
state = json.loads(state_path.read_text())
if state["manifestHash"] != digest(manifest, definitions, profile):
    raise ValueError("Original policy, model, or inputs changed; merge refused")
backup = ROOT / "review/results-initial-attempt.json"
if not backup.exists():
    shutil.copy2(state_path, backup)
merged = []
for directory, name in (("transport-retry", "transport-retry-manifest.json"),
                        ("stalled-retry", "stalled-retry-manifest.json")):
    retry_path = ROOT / directory / "results.json"
    if not retry_path.exists():
        continue
    retry_manifest, retry_definitions, retry_profile = load(name)
    retry_state = json.loads(retry_path.read_text())
    if retry_state["manifestHash"] != digest(retry_manifest, retry_definitions, retry_profile):
        raise ValueError(f"Recovery manifest changed: {directory}")
    if (retry_definitions, retry_profile, retry_manifest["field"], retry_manifest["model"]) != (
            definitions, profile, manifest["field"], manifest["model"]):
        raise ValueError(f"Recovery policy differs: {directory}")
    for key, result in retry_state["results"].items():
        old = state["results"][key]
        if result["status"] in {"ERROR", "INVALID_RESPONSE"} or old["status"] not in {"ERROR", "INVALID_RESPONSE"}:
            continue
        expected = batch.input_hash(cases[key], manifest["_imageHashes"][key])
        if result["inputHash"] != expected or old["inputHash"] != expected or result["case"] != cases[key]:
            raise ValueError(f"Recovery input differs: {key}")
        state["results"][key] = {**result, "executionRecovery": {
            "source": f"../{directory}/results.json", "previousStatus": old["status"],
            "previousError": old.get("error"),
        }}
        merged.append(key)
batch._atomic_json(state_path, state)
(ROOT / "review/review.html").write_text(batch.build_html(
    {k: v for k, v in manifest.items() if not k.startswith("_")}, state["results"], ROOT / "review"), encoding="utf-8")
print(json.dumps({"merged": merged, "valid": sum(r["status"] not in {"ERROR", "INVALID_RESPONSE"}
                                             for r in state["results"].values())}, ensure_ascii=False))
