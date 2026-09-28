#!/usr/bin/env python3
"""워크플로우 한 단계를 Claude Agent SDK로 부른다 — **구독(OAuth)으로만.** 러너(`gt_next.py`)가 자식으로 띄운다.

    python gt_agent.py call <워크플로우 사본.js> --model <모델>   # 러너가 부른다. 답 JSON 한 줄을 표준 출력에
    python gt_agent.py check                                       # 이 컴퓨터가 구독으로 돌 수 있는지 한 번 물어 본다

## 왜 SDK인가

전에는 러너가 `claude -p`를 직접 띄웠다. 그러려면 이 컴퓨터에 Claude Code가 설치돼 있어야 했고, 러너는 데스크톱 앱 폴더
(macOS 전용 경로)를 뒤져 가장 새 판을 골랐다. SDK(`claude-agent-sdk`)는 CLI를 패키지 안에 싣고 온다 — `pip install` 한 번이면
리눅스 서버에서도 같은 판이 돈다. 판독 규칙은 그대로 워크플로우(`gt-review.js`)에 있다. 여기는 그것을 **부르기만** 한다 —
판독을 파이썬으로 다시 쓰면 같은 일을 하는 곳이 둘이 된다.

## 왜 자식 프로세스인가

러너는 잠금을 자식에게 물려주고(`pass_fds`), 종료 신호를 받으면 자식의 프로세스 묶음째 끝낸다(`gt_next` 머리말). SDK를 러너 안에서
부르면 SDK가 띄우는 CLI에는 잠금이 넘어가지 않고 프로세스 묶음도 갈린다. 그래서 SDK 호출만 이 작은 자식에 두고, 러너는 전과 같이
자식 하나를 띄워 답 JSON을 읽는다 — 답의 모양도 `claude -p --output-format json`과 같다(`result`·`is_error`·`structured_output`).

## 구독만 — API 키로 새지 않는다

인증은 둘 중 하나다. 이 컴퓨터에서 `claude auth login`을 했거나, `claude setup-token`으로 받은 토큰을 `CLAUDE_CODE_OAUTH_TOKEN`에
넣었거나(클라우드 서버). `ANTHROPIC_API_KEY`가 환경에 있으면 CLI는 그것을 먼저 쓴다 — 조용히 API 과금으로 넘어간다. 그래서 API 키
변수를 지우고 시작하고, 세션이 뜬 뒤에도 인증 출처(`apiKeySource`)가 구독이 아니면 판독을 시작하기 전에 멈춘다.

## 세션의 권한 — `claude -p` 때와 같다

도구를 워크플로우와 읽기 도구로 줄이고(`tools`), 쓰기·셸·웹을 막고(`disallowed_tools`), 권한 모드를 `dontAsk`로 못박고, MCP 서버를
싣지 않는다(`strict_mcp_config`). 설정은 이 프로젝트의 것만 읽는다(`setting_sources=["project"]`) — 판독·반론 에이전트
(`.claude/agents/`)는 거기서 오고, 사용자 전역의 자동 승인이나 훅은 서버마다 달라 싣지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".claude").is_dir())

TOOLS = ["Workflow", "ToolSearch", "Read", "Grep", "Glob"]
DENIED = ["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"]

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"outputFile": {"type": "string"}, "error": {"type": "string"}},
    "required": ["outputFile"],
}

# 이 변수가 있으면 CLI는 구독보다 먼저 쓴다. 러너가 한 번 지우고(gt_next._headless_env), 여기서도 지운다 — 혼자 불러도 같게.
API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# 세션이 알려 주는 인증 출처 가운데 구독인 것. 로그인(키체인)이나 CLAUDE_CODE_OAUTH_TOKEN이면 «API 키 없음»으로 뜬다.
SUBSCRIPTION_SOURCES = ("none", None)

NOT_SUBSCRIPTION = "구독이 아니라 API 키로 돌려 해서 멈췄습니다 — 서버 환경에서 ANTHROPIC_API_KEY를 빼 주세요."


def workflow_prompt(script: Path) -> str:
    return f"""데이터 운영 화면의 버튼(골든셋 검수의 «다음 후보 받기»·증분 검수의 AI 추론)으로 사용자가 이 워크플로우 실행을 명시적으로 요청했다.
다른 일은 하지 않는다. 파일을 읽거나 만들거나 고치지 않는다.

1. Workflow 도구가 목록에 없으면 ToolSearch로 `select:Workflow`를 불러온다.
2. Workflow({{ scriptPath: "{script}" }})를 한 번 부른다. args는 넣지 않는다 — 인자는 스크립트 안에 이미 있다.
3. 워크플로우가 끝날 때까지 기다린다. 끝나면 알림에 적힌 출력 파일(워크플로우 반환값이 든 파일)의 절대 경로를 outputFile로 답한다.
   실패했거나 출력 파일이 없으면 outputFile은 빈 문자열, error에 한 문장."""


def options(model: str, **extra: Any) -> Any:
    """SDK 세션의 설정. CLI를 바꿔 끼우려면 `GT_NEXT_CLAUDE`(기본은 SDK에 실린 CLI)."""
    from claude_agent_sdk import ClaudeAgentOptions

    settings: dict[str, Any] = {
        "model": model, "cwd": str(PROJECT_ROOT),
        "tools": list(TOOLS), "allowed_tools": list(TOOLS), "disallowed_tools": list(DENIED),
        "permission_mode": "dontAsk", "strict_mcp_config": True, "setting_sources": ["project"],
        "cli_path": os.environ.get("GT_NEXT_CLAUDE") or None,
        "stderr": lambda line: print(line, file=sys.stderr, flush=True),
    }
    return ClaudeAgentOptions(**{**settings, **extra})


async def ask(prompt: str, opts: Any) -> dict[str, Any]:
    """세션 하나를 끝까지 돌려 `claude -p --output-format json`과 같은 모양의 답을 만든다."""
    from claude_agent_sdk import ResultMessage, SystemMessage, query

    reply: dict[str, Any] = {"type": "result", "is_error": True, "result": "", "structured_output": None}
    async for message in query(prompt=prompt, options=opts):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            source = message.data.get("apiKeySource")
            reply["apiKeySource"] = source
            if source not in SUBSCRIPTION_SOURCES:
                # 판독을 한 건도 시작하기 전이다 — 여기서 끊으면 과금되는 요청이 없다.
                reply["result"] = NOT_SUBSCRIPTION
                return reply
        elif isinstance(message, ResultMessage):
            reply.update(is_error=bool(message.is_error), result=message.result or "",
                         structured_output=message.structured_output,
                         num_turns=message.num_turns, duration_ms=message.duration_ms)
    return reply


def _clean_env() -> None:
    for name in API_KEY_VARS:
        os.environ.pop(name, None)


def _run(prompt: str, opts: Any) -> dict[str, Any]:
    import anyio

    try:
        return anyio.run(ask, prompt, opts)
    except Exception as error:  # noqa: BLE001 — CLI가 뜨지 못한 것까지 답 한 줄로 돌려준다(러너가 사람의 문장으로 바꾼다)
        return {"type": "result", "is_error": True, "result": f"{type(error).__name__}: {error}", "structured_output": None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    call = sub.add_parser("call")
    call.add_argument("script", type=Path)
    call.add_argument("--model", required=True)
    check = sub.add_parser("check")
    check.add_argument("--model", default="claude-haiku-4-5-20251001", help="확인만 하므로 가장 가벼운 모델")
    args = parser.parse_args()
    _clean_env()
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        print(json.dumps({"type": "result", "is_error": True, "result": "claude-agent-sdk is not installed",
                          "structured_output": None}, ensure_ascii=False))
        return 1
    if args.command == "check":
        reply = _run("ok 한 단어만 답한다.", options(args.model, tools=[], allowed_tools=[], max_turns=1))
        ok = not reply.get("is_error") and reply.get("apiKeySource") in SUBSCRIPTION_SOURCES
        print(json.dumps({"ok": ok, "subscription": reply.get("apiKeySource") in SUBSCRIPTION_SOURCES,
                          "oauthToken": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), "reply": reply.get("result")},
                         ensure_ascii=False))
        return 0 if ok else 1
    reply = _run(workflow_prompt(args.script), options(args.model, output_format={"type": "json_schema", "schema": OUTPUT_SCHEMA}))
    print(json.dumps(reply, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
