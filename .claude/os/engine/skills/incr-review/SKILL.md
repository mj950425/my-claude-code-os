---
name: incr-review
description: 새로 들어온 증분 데이터(라벨 없음)를 AI가 먼저 추론하게 하고, 데이터 운영팀이 화면에서 클릭만으로 검수하게 한다. 골든셋 검수와 같은 과제 선언·정책·판독자를 쓴다. "증분 넣어줘", "증분 밀어넣을게", "이 파일 증분 검수", "증분 검수 열어줘", "증분 얼마나 남았어", "증분 결과 파일 만들어줘", "증분 AI 다시 돌려줘" 요청에서 사용한다.
---

# 증분 검수 — 밀어넣으면 AI가 먼저 읽고, 사람은 누르기만

속성을 모른다. 증분을 받을 수 있는 과제는 골든셋 검수 과제(프로필 `gtTask`) 전부다 — 과제를 따로 선언하지 않는다.
계약: [incremental-review.md](../../contracts/incremental-review.md). 명령은 전부 **프로젝트 루트에서** 돈다.

## 1. 과제를 정한다

```bash
python3 .claude/os/engine/scripts/incr_review.py tasks
```

요청이 과제를 가리키지 않고 과제가 둘 이상이면 한 번 묻는다(AskUserQuestion, 선택지는 `name`).

## 2. 밀어넣는다 — «증분 넣어줘»

파일은 JSONL, 한 줄이 한 건이고 과제의 키 열(`keyField`)이 있어야 한다. 사진이 과제의 사진 색인(`gtTask.images`)에 아직 없으면
같은 모양의 증분 전용 색인을 `--images`로 함께 넣는다.

```bash
python3 .claude/os/engine/scripts/incr_review.py push --task <과제> --file <증분.jsonl> [--images <사진 색인.jsonl>] [--name <이름>]
```

- 거절(`ok: false`)이면 `error` 문장을 그대로 전하고 멈춘다 — 키 없음·키 겹침은 파일을 가진 쪽이 고친다.
- 성공하면 AI 추론이 뒤에서 이미 떠 있다(`aiStarted`). `aiStarted`가 거짓이면 `aiNote`를 전하고, 다른 AI 작업이 끝난 뒤 화면의 «AI 추론 시작»을 누르라고 안내한다.
- `answerColumnsAlreadyFilled`가 비어 있지 않으면 «결과 파일이 그 열을 덮는다»고 알린다. `rowsWithPhotos`가 `rows`보다 작으면 «사진을 못 찾은 건이 있다»고 알린다.
- 서버를 켜고(`./serve.sh start`) `http://127.0.0.1:7391` + 결과의 `screen`을 크롬으로 연다.

## 3. 검수는 사람이 화면에서 한다

«이름을 적고, AI 제안(파란 테두리)이 맞으면 «AI 값으로 모두 확정»(키보드 A), 다르면 맞는 값을 누르시면 됩니다»라고만 안내한다.
**AI 제안을 대신 확정하지 않는다** — 원장에는 사람이 누르거나 말로 명시한 판정만 간다(CLAUDE.md 규칙 5와 같은 선).
말로 판정을 받으면(«N1은 빨강이야») 누가 말했는지(`--reviewer`)와 **그 사람이 본 AI 제안**(`--expect-ai`, AI 제안이 없던 칸은
`--expect-ai-empty`)을 함께 적는다 — 먼저 `status`나 화면으로 그 칸의 AI 제안을 확인한다. 보류는 `--value` 대신 `--hold`:

```bash
python3 .claude/os/engine/scripts/incr_review.py record --task <과제> --batch <묶음> --key <키> --field <필드> --value <코드> --reviewer <이름> --expect-ai <본 AI 값>
```

## 4. 남은 것·다시 읽기·결과

```bash
python3 .claude/os/engine/scripts/incr_review.py status --task <과제> [--batch <묶음>]
python3 .claude/os/engine/scripts/incr_review.py run --task <과제> --batch <묶음> --reread   # 못 읽은 건만 (화면의 «못 읽은 것 다시»와 같다)
python3 .claude/os/engine/scripts/incr_review.py run --task <과제> --batch <묶음>            # 정책을 고친 뒤 — 확정하지 않은 건을 처음부터 다시 읽는다
python3 .claude/os/engine/scripts/incr_review.py export --task <과제> --batch <묶음>          # 모든 칸을 확정한 줄만 라벨을 채워 쓴다
```

수는 `status`가 센 그대로 전한다(규칙 8 — 다시 세지 않는다). «이미 AI가 검수중»이면 골든셋 검수의 AI가 도는 중이다 — 워크플로우는 한 번에 하나만 돈다.
