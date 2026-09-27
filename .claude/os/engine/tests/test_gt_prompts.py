"""판단을 바꾸는 자리는 정의 문서 하나다 — 판독 프롬프트는 모든 과제에 고정이다.

과제는 계속 늘고, 판단을 고치는 사람은 데이터 운영팀이다. 그래서 과제마다 달라지는 것은 정의 문서(무엇이 답인가)와
프로필(무엇을 읽는가)뿐이고, 판독자·반론자에게 가는 지시문은 과제를 모른다. 여기서 두 가지를 지킨다.

1. 과제는 판독자·반론자를 고르지 못한다(`gtTask.agents`) — 과제별 프롬프트로 돌아가는 뒷문이다.
2. 공통 지시문(워크플로우·두 에이전트)에 어느 과제의 이름·필드·허용값도 적히지 않는다 — 적히면 그 과제의 기준이
   정의 문서와 프롬프트 두 곳에 갈린다.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".claude").is_dir())
ENGINE = PROJECT_ROOT / ".claude/os/engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "tests"))

from catalog_profile import discover_profiles, load_profile  # noqa: E402
from gt_task import AGENTS, load_task  # noqa: E402

SHARED_PROMPTS = [ENGINE / "workflows/gt-review.js", *(ENGINE / f"agents/{name}.md" for name in AGENTS.values())]


def task_vocabulary() -> dict[str, set[str]]:
    """과제마다 그 과제만의 낱말 — 과제 이름, 필드 ID·이름, 허용값 코드·이름. 정의 문서에서 읽은 그대로다."""
    found: dict[str, set[str]] = {}
    for path in discover_profiles():
        profile = load_profile(path)
        if not profile.get("gtTask"):
            continue
        task = load_task(profile)
        words = {str(profile["id"]), str(profile.get("displayName") or "")}
        for field in task["fields"]:
            words |= {str(field["id"]), str(field.get("name") or "")}
            words |= set(map(str, field.get("labels") or []))
            words |= {str(name) for name in (field.get("labelNames") or {}).values()}
        found[str(profile["id"])] = {word for word in words if len(word) >= 2}
    return found


class FixedPromptTest(unittest.TestCase):
    def test_the_shared_prompts_know_no_task(self) -> None:
        vocabulary = task_vocabulary()
        self.assertTrue(vocabulary, "GT 개선 과제를 하나도 찾지 못했습니다 — 이 검사가 아무것도 보지 않습니다")
        leaks = []
        for prompt in SHARED_PROMPTS:
            text = prompt.read_text(encoding="utf-8")
            for task_id, words in vocabulary.items():
                for word in sorted(words):
                    # 영문 코드는 낱말 경계로(`FRONT`가 `FRONTIER`에 걸리지 않게), 한국어 이름은 글자 그대로 찾는다.
                    pattern = rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])" if word.isascii() else re.escape(word)
                    if re.search(pattern, text):
                        leaks.append(f"{prompt.name}: {task_id}의 «{word}»")
        self.assertEqual(leaks, [], "공통 판독 프롬프트에 과제의 낱말이 있습니다 — 판단 기준은 정의 문서에 적으세요")

    def test_a_task_cannot_swap_the_prompts(self) -> None:
        from test_gt_review import Fixture

        with tempfile.TemporaryDirectory() as temp:
            fx = Fixture(Path(temp))
            fx.profile["gtTask"]["agents"] = {"reader": "gt-blind-reader"}  # 같은 이름이어도 칸 자체를 받지 않는다
            fx.save()
            stopped = fx.task("prepare", check=False)
            self.assertNotEqual(stopped.returncode, 0)
            self.assertIn("gtTask.agents는 받지 않습니다", stopped.stderr)
            self.assertIn("정의 문서", stopped.stderr)

    def test_every_task_uses_the_same_read_only_pair(self) -> None:
        self.assertEqual(AGENTS, {"reader": "gt-blind-reader", "defender": "gt-defender"})
        for prompt in SHARED_PROMPTS[1:]:
            head = prompt.read_text(encoding="utf-8").split("---")[1]
            self.assertRegex(head, r"(?m)^tools:\s*Read, Grep, Glob\s*$", f"{prompt.name}는 읽기 도구만 가져야 합니다")


if __name__ == "__main__":
    unittest.main()
