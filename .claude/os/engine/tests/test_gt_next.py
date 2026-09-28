"""«다음 후보 받기» 러너 — 한 번에 하나만 돈다.

잠금은 파일에 적은 상태가 아니라 `flock`이다. 다른 프로세스가 쥐고 있으면 러너도 `prepare`도 곧장 거절하고,
쥔 프로세스가 죽으면 풀린다. 여기서는 진짜 다른 프로세스로 잠금을 쥐고 확인한다 — 같은 프로세스 안의 흉내로는
flock이 무엇을 막는지 알 수 없다.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import time
import urllib.request
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

SCRIPTS = next(parent for parent in Path(__file__).resolve().parents if (parent / ".claude").is_dir()) / ".claude/os/engine/scripts"
sys.path.insert(0, str(SCRIPTS))

import gt_agent  # noqa: E402
import gt_next  # noqa: E402


class LockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = (gt_next.RUN_DIR, gt_next.lock_dir)
        gt_next.RUN_DIR = Path(self.temp.name)
        gt_next.lock_dir = lambda profile: gt_next.RUN_DIR  # 러너도 임시 폴더에서 잠근다 — 진짜 러너와 부딪히지 않게
        self.lock = gt_next.RUN_DIR / "lock"
        self.state = gt_next.RUN_DIR / "state.json"
        self.holder: subprocess.Popen[str] | None = None

    def tearDown(self) -> None:
        if self.holder:
            self.holder.kill()
            self.holder.wait()
        gt_next.RUN_DIR, gt_next.lock_dir = self.saved
        self.temp.cleanup()

    def hold(self) -> None:
        """다른 프로세스가 잠금을 쥔다 — 도는 러너 흉내. 쥐었다고 알린 뒤에 돌려준다."""
        self.state.write_text(json.dumps({"task": "other", "taskName": "다른 과제", "phase": "workflow"}), encoding="utf-8")
        code = textwrap.dedent(f"""
            import fcntl, sys, time
            handle = open({str(self.lock)!r}, "a+")
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
        self.assertEqual(str(refused.exception), "이미 AI가 검수중입니다.")
        # 거절한 러너는 남의 상태를 덮어쓰지 않는다
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["task"], "other")

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
        gt_next._held[gt_next.RUN_DIR] = handle
        try:
            with gt_next.gate():
                pass
        finally:
            gt_next._held.pop(gt_next.RUN_DIR, None)
            handle.close()

    def test_each_runs_folder_has_its_own_lock(self) -> None:
        """테스트의 임시 과제가 진짜 러너(runs/.gt-next)와 부딪히지 않는다 — 잠금은 과제의 산출물 폴더를 따른다."""
        self.hold()
        with tempfile.TemporaryDirectory() as other:
            with gt_next.gate(Path(other)):
                pass


class HeadlessReplyTest(unittest.TestCase):
    """SDK 자식(gt_agent)의 답을 읽는 자리. 가짜 자식으로 돌린다 — 진짜는 구독 로그인과 몇십 분이 든다."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = gt_next.RUN_DIR
        gt_next.RUN_DIR = Path(self.temp.name)
        self.patches = [unittest.mock.patch.object(gt_next, "sdk_missing", return_value=False)]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        gt_next.RUN_DIR = self.saved
        self.temp.cleanup()

    def fake(self, reply: dict, seen: Path | None = None) -> None:
        """자식 자리에 답 한 줄을 내는 셸 스크립트를 끼운다. `seen`이 있으면 받은 환경을 적는다."""
        script = Path(self.temp.name) / "agent"
        record = f"env > {seen}\n" if seen else ""
        script.write_text(f"#!/bin/sh\n{record}cat <<'EOF'\n{json.dumps(reply)}\nEOF\n", encoding="utf-8")
        script.chmod(0o755)
        patch = unittest.mock.patch.object(gt_next, "headless_command", lambda path: [str(script)])
        patch.start()
        self.patches.append(patch)

    def test_the_child_is_the_sdk_agent_with_the_pinned_model(self) -> None:
        """판독·반론 에이전트는 헤드리스 세션의 모델을 물려받는다 — 그 모델을 러너가 못박는다. 자식은 같은 인터프리터의 gt_agent다."""
        with unittest.mock.patch.dict(os.environ, {"GT_NEXT_AGENT": ""}):
            argv = gt_next.headless_command(Path("/tmp/w.js"))
        self.assertEqual(argv[:3], [sys.executable, str(gt_next.AGENT), "call"])
        self.assertEqual(argv[argv.index("--model") + 1], gt_next.MODEL)

    def test_an_old_sdk_becomes_a_sentence(self) -> None:
        self.fake({"type": "result", "is_error": True, "result": "API Error: 400 Claude Code 2.1.197 does not support this model"})
        with self.assertRaises(RuntimeError) as stopped:
            gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log")
        self.assertIn("pip install -U claude-agent-sdk", str(stopped.exception))

    def test_a_missing_sdk_becomes_a_sentence(self) -> None:
        with unittest.mock.patch.object(gt_next, "sdk_missing", return_value=True):
            with self.assertRaises(RuntimeError) as stopped:
                gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log")
        self.assertIn("requirements.txt", str(stopped.exception))

    def test_an_api_key_never_reaches_the_child(self) -> None:
        """구독으로만 돈다 — API 키가 환경에 있으면 CLI가 그것을 먼저 써서 조용히 API 과금이 된다. 구독 토큰은 넘어간다."""
        seen = Path(self.temp.name) / "env"
        output = Path(self.temp.name) / "out.json"
        output.write_text("{}", encoding="utf-8")
        self.fake({"type": "result", "is_error": False, "result": "", "structured_output": {"outputFile": str(output)}}, seen)
        with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test"}):
            gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log")
        env = seen.read_text(encoding="utf-8")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN=oauth-test", env)

    def test_a_session_on_an_api_key_is_refused(self) -> None:
        self.fake({"type": "result", "is_error": True, "apiKeySource": "ANTHROPIC_API_KEY", "result": "구독이 아니라 API 키로"})
        with self.assertRaises(RuntimeError) as stopped:
            gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log")
        self.assertIn("API 키", str(stopped.exception))

    def test_a_bad_oauth_token_becomes_a_sentence(self) -> None:
        """클라우드 서버의 토큰이 틀렸거나 만료됐다 — CLI는 401로 답한다. 사람이 할 일(토큰 다시 받기)로 바꾼다."""
        self.fake({"type": "result", "is_error": True, "result": "ResultError: Failed to authenticate. API Error: 401 Invalid bearer token"})
        with self.assertRaises(RuntimeError) as stopped:
            gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log")
        self.assertIn("claude setup-token", str(stopped.exception))

    def test_not_logged_in_becomes_a_sentence(self) -> None:
        self.fake({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"})
        with self.assertRaises(RuntimeError) as stopped:
            gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log")
        self.assertIn("claude auth login", str(stopped.exception))
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", str(stopped.exception))

    def test_the_output_file_comes_back(self) -> None:
        output = Path(self.temp.name) / "out.json"
        output.write_text("{}", encoding="utf-8")
        self.fake({"type": "result", "is_error": False, "result": "", "structured_output": {"outputFile": str(output)}})
        self.assertEqual(gt_next._run_workflow("t", {"items": []}, Path(self.temp.name) / "log"), output)

    def test_the_read_stage_carries_no_gt(self) -> None:
        """판독자가 도는 동안 판독 파일 이름과 GT를 잇는 파일이 없어야 한다 — 판독 단계의 인자에는 GT가 없다."""
        args = {"task": "t", "batchId": "b", "items": [{"id": "GI-01", "view": "reader/x.json"}],
                "gt": {"GI-01": {"f": "SECRET"}}, "alternatives": {"GI-01": {"f": ["ALT"]}}}
        read = gt_next.stage_args(args, "read")
        self.assertEqual(read["stage"], "read")
        self.assertNotIn("gt", read)
        self.assertNotIn("alternatives", read)
        self.assertNotIn("SECRET", json.dumps(read))
        defend = gt_next.stage_args(args, "defend", {"GI-01": {"readings": []}})
        self.assertEqual((defend["stage"], defend["gt"], defend["readings"]), ("defend", args["gt"], {"GI-01": {"readings": []}}))

    def test_the_headless_session_gets_only_the_workflow_and_read_tools(self) -> None:
        """allowed_tools는 «묻지 않고 허용»일 뿐 제한이 아니다. 도구 자체를 줄이고, 쓰기·셸·웹을 막고, 권한 모드를 못박는다."""
        try:
            opts = gt_agent.options(gt_next.MODEL)
        except ImportError:
            self.skipTest("claude-agent-sdk가 이 인터프리터에 없다 — .venv/bin/python으로 돌린다")
        self.assertEqual(set(opts.tools), {"Workflow", "ToolSearch", "Read", "Grep", "Glob"})
        self.assertEqual(opts.tools, opts.allowed_tools)
        self.assertTrue({"Bash", "Write", "Edit", "WebFetch"} <= set(opts.disallowed_tools))
        self.assertEqual(opts.permission_mode, "dontAsk")
        self.assertTrue(opts.strict_mcp_config)
        self.assertEqual(opts.setting_sources, ["project"])
        self.assertEqual(opts.model, gt_next.MODEL)

    def test_the_args_are_embedded_not_retyped(self) -> None:
        """헤드리스 AI가 큰 JSON을 args로 옮겨 적다가 빈 인자로 돌았다(건 0). 그래서 인자는 스크립트에 박고 경로만 넘긴다."""
        script = gt_next.embedded_workflow({"task": "t", "batchId": "b", "items": [{"id": "GI-01"}]}, Path(self.temp.name) / "w.js")
        body = script.read_text(encoding="utf-8")
        self.assertNotIn(gt_next.EMBED_LINE, body)
        self.assertEqual(body.count("const INPUT = "), 1)
        self.assertIn("Workflow({ scriptPath:", gt_agent.workflow_prompt(script))
        self.assertNotIn("args:", gt_agent.workflow_prompt(script))
        node = shutil.which("node")
        if node:
            checked = subprocess.run([node, "--check", str(script)],
                                     capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_a_result_from_another_batch_is_refused(self) -> None:
        output = Path(self.temp.name) / "out.json"
        output.write_text(json.dumps({"result": {"schemaVersion": "gt-review-sweep-v2", "items": []}}), encoding="utf-8")
        self.fake({"type": "result", "is_error": False, "result": "", "structured_output": {"outputFile": str(output)}})
        with self.assertRaises(RuntimeError) as stopped:
            gt_next._run_workflow("t", {"batchId": "b1", "items": [{"id": "GI-01"}]}, Path(self.temp.name) / "log")
        self.assertIn("시작되지 못했습니다", str(stopped.exception))


class ScriptRunTest(unittest.TestCase):
    """서버는 러너를 `python3 gt_next.py run`으로 띄운다. 그렇게 띄운 러너가 자기 잠금에 막히지 않는지 본다 —
    스크립트로 띄우면 이 파일이 두 벌(__main__·gt_next)로 올라와 «쥔 잠금» 기록이 갈릴 수 있다. 실제로 버튼이 그렇게 멈췄다."""

    def test_the_runner_started_as_a_script_gets_past_its_own_lock(self) -> None:
        from test_gt_review import Fixture

        with tempfile.TemporaryDirectory() as temp:
            fx = Fixture(Path(temp))
            fx.save()
            claude = Path(temp) / "claude"
            claude.write_text('#!/bin/sh\necho \'{"type":"result","is_error":true,"result":"x"}\'\n', encoding="utf-8")
            claude.chmod(0o755)
            done = subprocess.run([sys.executable, str(SCRIPTS / "gt_next.py"), "run", "--task", str(fx.profile_path)],
                                  capture_output=True, text=True, env={**fx.env, "GT_NEXT_AGENT": str(claude)}, timeout=120)
            state = json.loads(done.stdout)
            self.assertEqual(state["phase"], "failed", done.stderr)
            # 준비는 지났고(판독 단계까지 갔다) 멈춘 까닭은 가짜 claude다 — 자기 잠금이 아니다
            self.assertNotEqual(state["message"], "이미 AI가 검수중입니다.")
            self.assertTrue(state.get("items"))

    def test_a_status_probe_does_not_starve_a_starting_runner(self) -> None:
        """상태 조회(running)는 잠금을 아주 잠깐 쥔다. 막 뜬 러너가 그 순간과 겹쳐도 «바쁨»으로 끝나지 않게 짧게 다시 해 본다."""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            code = (f"import fcntl,time; h=open({str(folder / 'lock')!r},'a+'); fcntl.flock(h,fcntl.LOCK_EX); "
                    "print('held',flush=True); time.sleep(0.3)")
            holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
            self.assertEqual(holder.stdout.readline().strip(), "held")
            self.assertIsNone(gt_next._try_lock(folder), "기다리지 않는 조회는 곧바로 None이어야 합니다")
            handle = gt_next._try_lock(folder, patience=2.0)
            self.assertIsNotNone(handle, "잠깐 쥔 잠금이 풀리면 러너는 얻어야 합니다")
            handle.close()
            holder.wait()

    def test_killing_the_runner_leaves_the_lock_with_the_workflow(self) -> None:
        """러너가 kill -9로 죽어도 헤드리스 자식(워크플로우)은 판독을 계속한다. 그동안 잠금이 풀리면 다음 버튼이 그 배치를
        죽은 배치로 보고 지우고 둘째 워크플로우를 띄운다. 그래서 잠금을 자식에게 물려준다 — 자식이 끝날 때까지 잠금이 남는다."""
        from test_gt_review import Fixture

        with tempfile.TemporaryDirectory() as temp:
            fx = Fixture(Path(temp))
            fx.save()
            child_pid = Path(temp) / "child.pid"
            claude = Path(temp) / "claude"
            claude.write_text(f"#!/bin/sh\necho $$ > {child_pid}\nsleep 30\n", encoding="utf-8")
            claude.chmod(0o755)
            runner = subprocess.Popen([sys.executable, str(SCRIPTS / "gt_next.py"), "run", "--task", str(fx.profile_path)],
                                      env={**fx.env, "GT_NEXT_AGENT": str(claude)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(200):
                    if child_pid.is_file() and child_pid.read_text().strip():
                        break
                    time.sleep(0.1)
                self.assertTrue(child_pid.is_file(), "러너가 헤드리스 자식을 띄우지 않았습니다")
                runner.kill()
                runner.wait()
                folder = Path(temp) / ".gt-next"
                self.assertIsNotNone(gt_next.running(folder), "러너가 죽자 잠금이 풀렸습니다 — 자식은 아직 판독 중입니다")
            finally:
                if child_pid.is_file():
                    with contextlib.suppress(ProcessLookupError, ValueError):
                        os.killpg(int(child_pid.read_text().strip()), signal.SIGKILL)
                if runner.poll() is None:
                    runner.kill()
            time.sleep(0.2)
            self.assertIsNone(gt_next.running(Path(temp) / ".gt-next"), "자식이 끝나면 잠금이 풀려야 합니다")

    def test_the_server_answers_with_the_runner_it_started(self) -> None:
        """서버가 러너를 띄우자마자 답하면 화면의 첫 조회가 지난 실행의 상태를 이번 결과로 읽는다. 서버는 러너가 제 상태를 쓸
        때까지 기다린 뒤 그 러너의 pid를 싣고, 상태 조회도 그 과제의 잠금 자리를 본다."""
        from test_gt_review import Fixture

        with tempfile.TemporaryDirectory() as temp:
            fx = Fixture(Path(temp))
            fx.save()
            folder = Path(temp) / ".gt-next"
            folder.mkdir()
            # 지난 실행이 남긴 상태 — 이것을 이번 결과로 읽으면 안 된다
            (folder / "state.json").write_text(json.dumps({"task": "fixture-color-sheen", "pid": 1, "phase": "failed",
                                                           "message": "지난번 실패"}), encoding="utf-8")
            claude = Path(temp) / "claude"
            claude.write_text('#!/bin/sh\nsleep 2\necho \'{"type":"result","is_error":true,"result":"x"}\'\n', encoding="utf-8")
            claude.chmod(0o755)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            server = subprocess.Popen([sys.executable, str(SCRIPTS / "serve_reports.py"), "--port", str(port)],
                                      env={**fx.env, "GT_NEXT_AGENT": str(claude)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    try:
                        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                        break
                    except OSError:
                        time.sleep(0.1)
                request = urllib.request.Request(f"http://127.0.0.1:{port}/gt-next", method="POST",
                                                 data=json.dumps({"task": "fixture-color-sheen"}).encode(),
                                                 headers={"Content-Type": "application/json"})
                answer = json.loads(urllib.request.urlopen(request, timeout=60).read())
                self.assertTrue(answer["ok"], answer)
                self.assertNotEqual(answer["pid"], 1)
                state = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/gt-next?task=fixture-color-sheen", timeout=10).read())
                self.assertEqual(state["pid"], answer["pid"], "상태 조회가 이번 러너가 아닌 것을 보여 줍니다")
                for _ in range(300):
                    state = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/gt-next?task=fixture-color-sheen", timeout=10).read())
                    if not state["running"]:
                        break
                    time.sleep(0.2)
                self.assertEqual(state["phase"], "failed")
                self.assertNotEqual(state["message"], "지난번 실패")
            finally:
                server.terminate()
                server.wait()

    def test_the_runner_reads_blind_first_and_defends_after(self) -> None:
        """러너는 판독(GT 없음)을 끝낸 뒤에야 반론(GT 있음)을 부르고, 단계마다 사본을 지운다. 리뷰에서 실제로 뚫린 눈가림을 막는다."""
        from test_gt_review import Fixture

        with tempfile.TemporaryDirectory() as temp:
            fx = Fixture(Path(temp))
            fx.save()
            seen = Path(temp) / "seen"
            seen.mkdir()
            claude = Path(temp) / "claude"
            # 가짜 자식(gt_agent 자리) — 받은 사본을 단계 이름으로 베껴 두고, 사본에 박힌 배치·건으로 워크플로우 반환값을 흉내 낸다.
            claude.write_text(f"""#!{sys.executable}
import json, sys, pathlib
path = pathlib.Path(sys.argv[sys.argv.index('call') + 1])
body = path.read_text()
args = json.loads(body.split('const INPUT = ', 1)[1].split('\\n', 1)[0])
(pathlib.Path({str(seen)!r}) / (args['stage'] + '.js')).write_text(body)
(pathlib.Path({str(seen)!r}) / (args['stage'] + '.path')).write_text(str(path))
items = [{{'id': item['id'], 'reading': {{'readings': []}}, 'defense': None}} for item in args['items']]
out = pathlib.Path({str(seen)!r}) / (args['stage'] + '.out')
out.write_text(json.dumps({{'result': {{'schemaVersion': 'gt-review-sweep-v2', 'task': args['task'], 'batchId': args['batchId'],
                                     'stage': args['stage'], 'needsRestart': None, 'items': items}}}}))
print(json.dumps({{'type': 'result', 'is_error': False, 'result': '', 'structured_output': {{'outputFile': str(out)}}}}))
""", encoding="utf-8")
            claude.chmod(0o755)
            done = subprocess.run([sys.executable, str(SCRIPTS / "gt_next.py"), "run", "--task", str(fx.profile_path)],
                                  capture_output=True, text=True, env={**fx.env, "GT_NEXT_AGENT": str(claude)}, timeout=120)
            state = json.loads(done.stdout)
            self.assertEqual(state["phase"], "done", state.get("message") or done.stderr)
            read, defend = (seen / "read.js").read_text(), (seen / "defend.js").read_text()
            read_args = json.loads(read.split("const INPUT = ", 1)[1].split("\n", 1)[0])
            defend_args = json.loads(defend.split("const INPUT = ", 1)[1].split("\n", 1)[0])
            self.assertNotIn("gt", read_args, "판독 단계 사본에 GT가 박혔습니다")
            self.assertTrue(defend_args["gt"], "반론 단계에는 GT가 있어야 합니다")
            self.assertEqual(set(defend_args["readings"]), {item["id"] for item in read_args["items"]})
            for stage in ("read", "defend"):
                self.assertFalse(Path((seen / f"{stage}.path").read_text()).exists(), f"{stage} 단계 사본이 남아 있습니다")
