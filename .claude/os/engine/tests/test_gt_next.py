"""«다음 후보 받기» 러너 — 한 번에 하나만 돈다.

잠금은 파일에 적은 상태가 아니라 `flock`이다. 다른 프로세스가 쥐고 있으면 러너도 `prepare`도 곧장 거절하고,
쥔 프로세스가 죽으면 풀린다. 여기서는 진짜 다른 프로세스로 잠금을 쥐고 확인한다 — 같은 프로세스 안의 흉내로는
flock이 무엇을 막는지 알 수 없다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = next(parent for parent in Path(__file__).resolve().parents if (parent / ".claude").is_dir()) / ".claude/os/engine/scripts"
sys.path.insert(0, str(SCRIPTS))

import gt_next  # noqa: E402


class LockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = (gt_next.RUN_DIR, gt_next.LOCK, gt_next.STATE)
        gt_next.RUN_DIR = Path(self.temp.name)
        gt_next.LOCK = gt_next.RUN_DIR / "lock"
        gt_next.STATE = gt_next.RUN_DIR / "state.json"
        self.holder: subprocess.Popen[str] | None = None

    def tearDown(self) -> None:
        if self.holder:
            self.holder.kill()
            self.holder.wait()
        gt_next.RUN_DIR, gt_next.LOCK, gt_next.STATE = self.saved
        self.temp.cleanup()

    def hold(self) -> None:
        """다른 프로세스가 잠금을 쥔다 — 도는 러너 흉내. 쥐었다고 알린 뒤에 돌려준다."""
        gt_next.STATE.write_text(json.dumps({"task": "other", "taskName": "다른 과제", "phase": "workflow"}), encoding="utf-8")
        code = textwrap.dedent(f"""
            import fcntl, sys, time
            handle = open({str(gt_next.LOCK)!r}, "a+")
            fcntl.flock(handle, fcntl.LOCK_EX)
            print("held", flush=True)
            time.sleep(60)
        """)
        self.holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.assertEqual(self.holder.stdout.readline().strip(), "held")

    def test_nothing_runs_when_the_lock_is_free(self) -> None:
        self.assertIsNone(gt_next.running())
        with gt_next.gate():
            pass  # 비어 있으면 prepare가 지난다

    def test_a_second_run_is_refused_even_for_another_task(self) -> None:
        self.hold()
        self.assertEqual(gt_next.running()["task"], "other")
        with self.assertRaises(gt_next.Busy) as refused:
            gt_next.run("clothing-thumbnail-observation")
        self.assertIn("다른 과제", str(refused.exception))
        # 거절한 러너는 남의 상태를 덮어쓰지 않는다
        self.assertEqual(json.loads(gt_next.STATE.read_text(encoding="utf-8"))["task"], "other")

    def test_prepare_is_refused_while_a_run_holds_the_lock(self) -> None:
        self.hold()
        with self.assertRaises(gt_next.Busy):
            with gt_next.gate():
                self.fail("잠금이 쥐어져 있는데 prepare가 지났습니다")

    def test_the_lock_frees_when_the_holder_dies(self) -> None:
        self.hold()
        self.holder.kill()
        self.holder.wait()
        self.holder = None
        self.assertIsNone(gt_next.running())
        # 끝을 적지 못한 상태 파일은 «멈춤»으로 읽는다 — «도는 중»이 아니다
        self.assertFalse(gt_next.status()["running"])
        self.assertEqual(gt_next.status()["phase"], "failed")

    def test_prepare_inside_the_runner_does_not_block_itself(self) -> None:
        handle = gt_next._try_lock()
        gt_next._held = handle
        try:
            with gt_next.gate():
                pass
        finally:
            gt_next._held = None
            handle.close()


class HeadlessReplyTest(unittest.TestCase):
    """헤드리스 Claude Code의 답을 읽는 자리. 가짜 claude로 돌린다 — 진짜는 로그인과 몇십 분이 든다."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = (gt_next.RUN_DIR, os.environ.get("GT_NEXT_CLAUDE"))
        gt_next.RUN_DIR = Path(self.temp.name)

    def tearDown(self) -> None:
        gt_next.RUN_DIR = self.saved[0]
        if self.saved[1] is None:
            os.environ.pop("GT_NEXT_CLAUDE", None)
        else:
            os.environ["GT_NEXT_CLAUDE"] = self.saved[1]
        self.temp.cleanup()

    def fake(self, reply: dict) -> None:
        script = Path(self.temp.name) / "claude"
        script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(reply)}\nEOF\n", encoding="utf-8")
        script.chmod(0o755)
        os.environ["GT_NEXT_CLAUDE"] = str(script)

    def test_not_logged_in_becomes_a_sentence(self) -> None:
        self.fake({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"})
        with self.assertRaises(RuntimeError) as stopped:
            gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log")
        self.assertIn("claude auth login", str(stopped.exception))

    def test_the_output_file_comes_back(self) -> None:
        output = Path(self.temp.name) / "out.json"
        output.write_text("{}", encoding="utf-8")
        self.fake({"type": "result", "is_error": False, "result": "", "structured_output": {"outputFile": str(output)}})
        self.assertEqual(gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log"), output)

    def test_the_args_file_keeps_gt_values_out_of_the_repo(self) -> None:
        self.fake({"type": "result", "is_error": True, "result": "x"})
        with self.assertRaises(RuntimeError):
            gt_next._run_workflow("t", {"items": [], "gt": {"a": 1}}, Path(self.temp.name) / "log")
        # 인자 파일은 runs/ 아래(레포에 안 올라가는 곳)에만 — 여기서는 임시 폴더로 옮겨 두었다
        self.assertTrue((gt_next.RUN_DIR / "workflow-args.t.json").is_file())


if __name__ == "__main__":
    unittest.main()
