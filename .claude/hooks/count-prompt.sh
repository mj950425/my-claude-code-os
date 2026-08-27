#!/usr/bin/env bash
# UserPromptSubmit 훅: 사용자가 클로드에게 요청을 보낼 때마다 호출 횟수를 누적 기록한다.
#
# 설계 노트
# - 프로젝트 경로를 스크립트 위치에서 역산한다. 훅은 임의의 cwd에서 실행될 수 있어
#   상대 경로나 $(pwd)에 의존하면 엉뚱한 곳에 파일이 생긴다.
# - stdout을 비운다. UserPromptSubmit 훅의 stdout은 모델 컨텍스트로 주입되므로,
#   매 요청마다 출력하면 토큰만 축낸다.
# - 어떤 경우에도 exit 0. 카운터 실패가 사용자의 요청을 막아서는 안 된다.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
COUNT_FILE="$PROJECT_DIR/.claude/usage/prompt-count.json"

command -v jq >/dev/null 2>&1 || exit 0

mkdir -p "$(dirname "$COUNT_FILE")" || exit 0
[ -s "$COUNT_FILE" ] || printf '{"total":0,"by_date":{}}' > "$COUNT_FILE"

DATE="$(date +%F)"
TS="$(date +%FT%T%z)"

tmp="$(mktemp "${COUNT_FILE}.XXXXXX")" || exit 0
if jq --arg d "$DATE" --arg ts "$TS" '
      .total            = ((.total // 0) + 1)
    | .by_date[$d]      = ((.by_date[$d] // 0) + 1)
    | .last_prompt_at   = $ts
  ' "$COUNT_FILE" > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
  mv "$tmp" "$COUNT_FILE"
else
  rm -f "$tmp"
fi

exit 0
