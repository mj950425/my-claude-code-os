"""GT 개선 과제의 결과(고친 GT 파일 · 판정 원장)를 GitHub에 브랜치 + PR로 올린다.

GT를 GitHub에서 관리한다 — 정답이 바뀐 이력은 커밋이 되고, 바뀐 줄은 PR에서 사람이 한 번 더 본다.

왜 작업 트리를 건드리지 않는가: 개발자가 같은 폴더에서 다른 일을 하고 있을 수 있다(커밋 안 한 변경, 다른 브랜치).
그래서 브랜치를 바꾸거나 stash하지 않고, **임시 인덱스**로 «기준 브랜치 + 이 과제의 파일»만 담은 커밋을 만들어
원격에 새 브랜치로 민다. 지금 브랜치·작업 트리·인덱스는 그대로다.

올리는 파일은 둘뿐이다.
- GT 원본 — 프로필 `gtTask.gt`가 이 레포 안을 가리킬 때만(밖이면 그 레포 몫이라 올리지 않는다).
- 판정 원장 폴더 `.claude/gt/<과제>/gt-review/` — 잠금 파일은 빼고.

원본에 아직 넣지 않은 정정이 있으면 멈춘다 — PR의 GT와 원장이 서로 다른 말을 하게 된다(«넣어줘»가 먼저다).
이 함수를 부르는 쪽(스킬)은 반드시 먼저 묻는다. 푸시와 PR은 밖으로 나가는 일이다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gt_decisions import DecisionRejected, apply, gt_dir
from gt_task import TaskError, load_task, resolve

REMOTE = "origin"
SKIP = {"decisions.lock"}


def _git(top: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    completed = subprocess.run(["git", "-C", str(top), *args], capture_output=True, text=True,
                               env={**os.environ, **(env or {})})
    if check and completed.returncode != 0:
        raise TaskError(f"git {' '.join(args[:2])} 실패: {completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout.strip()


def _toplevel(path: Path) -> Path:
    folder = path if path.is_dir() else path.parent
    while not folder.exists():
        folder = folder.parent
    return Path(_git(folder, "rev-parse", "--show-toplevel"))


def _inside(path: Path, top: Path) -> bool:
    try:
        path.resolve().relative_to(top.resolve())
        return True
    except ValueError:
        return False


def _base_branch(top: Path, base: str | None) -> str:
    """기준 브랜치 이름(원격 이름 없이). 없으면 지금 브랜치가 따라가는 원격 브랜치, 그것도 없으면 원격의 기본 브랜치."""
    if base:
        return base.split("/", 1)[1] if base.startswith(f"{REMOTE}/") else base
    tracked = _git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if tracked.startswith(f"{REMOTE}/"):
        return tracked.split("/", 1)[1]
    head = _git(top, "symbolic-ref", "--short", f"refs/remotes/{REMOTE}/HEAD", check=False)
    if head.startswith(f"{REMOTE}/"):
        return head.split("/", 1)[1]
    raise TaskError("올릴 기준 브랜치를 모릅니다 — 지금 브랜치를 원격에 한 번 올리거나 --base로 적어 주세요.")


def _files(profile: dict[str, Any], task: dict[str, Any], top: Path) -> tuple[list[Path], bool]:
    ledger = gt_dir(profile)
    files = sorted(path for path in ledger.rglob("*") if path.is_file() and path.name not in SKIP) if ledger.is_dir() else []
    source = resolve(profile, task["gt"])
    in_repo = _inside(source, top) and source.is_file()
    return ([source] if in_repo else []) + files, in_repo


def plan(profile: dict[str, Any], base: str | None = None) -> dict[str, Any]:
    """무엇을 어디에 올릴지. 아무것도 쓰지 않는다."""
    task = load_task(profile)
    ledger = gt_dir(profile)
    top = _toplevel(ledger)
    files, in_repo = _files(profile, task, top)
    if not files:
        raise DecisionRejected("올릴 것이 없습니다 — 아직 기록된 판정이 없습니다.")
    unapplied = 0
    if in_repo and not task["gt"].get("upstream"):
        try:
            unapplied = int(apply(profile, confirm=False).get("linesToChange") or 0)
        except DecisionRejected:
            unapplied = 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return {"task": profile["id"], "base": _base_branch(top, base), "branch": f"gt/{profile['id']}-{stamp}",
            "files": [str(path.resolve().relative_to(top.resolve())) for path in files],
            "gtInRepo": in_repo, "unapplied": unapplied}


def _counts(profile: dict[str, Any]) -> dict[str, Any]:
    path = gt_dir(profile) / "export.json"
    try:
        summary = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        summary = {}
    return {name: summary.get(name) for name in ("corrections", "alreadyApplied", "confirmations")}


def _repo_slug(top: Path) -> str | None:
    url = _git(top, "remote", "get-url", REMOTE, check=False)
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def publish(profile: dict[str, Any], base: str | None = None, confirm: bool = False, open_pr: bool = True) -> dict[str, Any]:
    """`confirm`이 아니면 계획만 낸다. `confirm`이면 기준 브랜치 위에 이 과제의 파일만 얹은 커밋을 새 브랜치로 밀고 PR을 연다."""
    result = plan(profile, base)
    if not confirm:
        return {**result, "published": False}
    if result["unapplied"]:
        raise DecisionRejected(f"원본 GT에 아직 넣지 않은 정정이 {result['unapplied']}줄 있습니다 — «넣어줘»로 먼저 넣은 뒤 올려 주세요.")
    top = _toplevel(gt_dir(profile))
    _git(top, "fetch", "--quiet", REMOTE, result["base"])
    base_commit = _git(top, "rev-parse", f"{REMOTE}/{result['base']}")
    base_tree = _git(top, "rev-parse", f"{base_commit}^{{tree}}")
    # 임시 인덱스 — 작업 트리·지금 인덱스·지금 브랜치는 건드리지 않는다.
    with tempfile.TemporaryDirectory() as folder:
        env = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        _git(top, "read-tree", base_commit, env=env)
        for relative in result["files"]:
            blob = _git(top, "hash-object", "-w", "--", relative)
            _git(top, "update-index", "--add", "--cacheinfo", f"100644,{blob},{relative}", env=env)
        tree = _git(top, "write-tree", env=env)
    if tree == base_tree:
        return {**result, "published": False, "note": "기준 브랜치와 같습니다 — 올릴 변경이 없습니다."}
    counts = _counts(profile)
    title = f"GT {profile.get('displayName') or profile['id']} — 판정 반영"
    lines = [f"- 정정 {counts['corrections'] or 0}칸(원본에 들어간 것 {counts['alreadyApplied'] or 0}) · 유지 확인 {counts['confirmations'] or 0}칸",
             *(f"- `{name}`" for name in result["files"])]
    message = (f"{title}\n\n" + "\n".join(lines)
               + "\n\n사람이 GT 개선 화면에서 누른 판정과, 그 판정을 넣은 GT 원본이다.\n\n"
               + "Co-authored-by: Claude <noreply@anthropic.com>\n")
    commit = _git(top, "commit-tree", tree, "-p", base_commit, "-m", message)
    _git(top, "push", "--quiet", REMOTE, f"{commit}:refs/heads/{result['branch']}")
    pr = None
    if open_pr:
        slug = _repo_slug(top)
        body = ("\n".join(lines) + "\n\n판정 원장(`decisions.json`)이 정본이다 — 이 PR의 GT 변경은 그 원장에서 만들어졌다. "
                "머지하면 GT가 바뀐다.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")
        args = ["gh", "pr", "create", "--base", result["base"], "--head", result["branch"], "--title", title, "--body", body]
        if slug:
            args[3:3] = ["--repo", slug]
        completed = subprocess.run(args, capture_output=True, text=True, cwd=str(top))
        if completed.returncode != 0:
            raise TaskError(f"브랜치 {result['branch']}는 올렸지만 PR을 열지 못했습니다: {completed.stderr.strip()}")
        pr = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else None
    return {**result, "published": True, "commit": commit, "pr": pr}
