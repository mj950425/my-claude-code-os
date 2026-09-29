"""Check independent scores against the registered tasks; retain source fingerprints."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AUDIT = Path(__file__).resolve().parent
TASKS = sorted(path.parent.name for path in (ROOT / ".claude/os/attributes").glob("*/profile.json"))


def main():
    reports = {}
    for dimension in ("documents", "runtime", "ui"):
        path = AUDIT / f"{dimension}-round2.json"
        report = json.loads(path.read_text())
        if report["dimension"] != dimension or set(report["scores"]) != set(TASKS):
            raise ValueError(f"{dimension}: registered task coverage differs")
        if report.get("blockingIssues"):
            raise ValueError(f"{dimension}: blocking issues remain")
        for task, score in report["scores"].items():
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 90 <= score <= 100:
                raise ValueError(f"{dimension}/{task}: score {score} fails threshold")
        reports[dimension] = report
    paths = list((ROOT / ".claude/os/attributes").glob("*/definitions.md"))
    paths += list((ROOT / ".claude/os/attributes").glob("*/profile.json"))
    paths += list((ROOT / ".claude/os/attributes").glob("*/package.md"))
    paths += list((ROOT / ".claude/os/attributes").glob("*/adapters/*.py"))
    paths += list((ROOT / ".claude/os/attributes").glob("*/tests/*.py"))
    paths += [ROOT / "CLAUDE.md"]
    for directory in ("scripts", "workflows", "templates", "agents", "contracts", "tests"):
        paths += [p for p in (ROOT / ".claude/os/engine" / directory).rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith(".")]
    result = {"tasks": TASKS, "assessments": reports,
              "sourceHashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(paths)}}
    (AUDIT / "verified-assessments.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"tasks": len(TASKS), "dimensions": len(reports), "allScoresAtLeast90": True}))


if __name__ == "__main__":
    main()
