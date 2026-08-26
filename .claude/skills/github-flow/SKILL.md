---
name: github-flow
description: 여러 GitHub 계정(개인/회사)이 한 머신에 공존하는 환경에서 커밋 푸시와 fork→upstream PR을 계정 오염 없이 수행한다. "커밋 푸시", "PR 열어줘", "PR 만들어", "fork에서 PR", "푸시 403", "Permission denied to", "must be a collaborator", "gh 계정 바꿔줘", "브랜치 올려줘" 요청에서 사용한다.
---

# github-flow

한 머신에 GitHub 계정이 둘 이상 로그인돼 있을 때, **쓰기 작업 직전에 신원을 확인**하고 fork 기반 PR을 연다.

## 핵심 전제

이 환경에는 계정이 두 개다.

| 계정 | 용도 |
|---|---|
| `mj950425` | 개인 — NextStep 실습 등 개인 저장소 |
| `2minjoon` | 회사 |

`gh`의 **활성 계정은 예고 없이 되돌아간다.** 로그인 직후 확인했더라도, 나중 명령에서 다시 회사 계정이 잡힐 수 있다. 그래서 "한 번 확인했으니 괜찮다"는 가정은 쓰지 않는다.

## 규칙 1 — 쓰기 전에 항상 신원을 확인한다

push, PR 생성, 이슈 코멘트 등 **쓰기 작업 전에는 매번** 아래를 실행한다. 읽기 작업에는 불필요하다.

```bash
gh api user --jq .login
printf "protocol=https\nhost=github.com\n\n" | git credential fill 2>/dev/null | grep '^username='
gh api repos/<owner>/<repo> --jq '.permissions'
```

세 값이 모두 의도한 계정이고 `push: true`여야 진행한다. 하나라도 어긋나면 **작업하지 말고 먼저 계정을 정리한다.**

`gh api user`와 git credential은 **서로 다른 경로**다. gh는 활성 계정 토큰을, git은 credential helper(macOS는 osxkeychain)를 각각 본다. 둘이 다른 계정을 가리키는 상태가 실제로 발생하므로 반드시 둘 다 본다.

## 규칙 2 — 전역 전환보다 명령 단위 토큰을 먼저 쓴다

활성 계정을 바꾸면 사용자의 다른 작업(회사 저장소)까지 영향을 받는다. 한 번의 명령만 다른 계정으로 실행하면 될 때는 전역을 건드리지 않는다.

```bash
export GH_TOKEN=$(gh auth token --user mj950425)
gh pr create ...
```

이 프로젝트처럼 **앞으로 계속 개인 계정으로 작업하는 게 확정된 경우에만** 전역 전환한다. 전환했으면 되돌리는 법을 사용자에게 알린다.

```bash
gh auth switch --user mj950425   # 개인
gh auth switch --user 2minjoon   # 회사
```

## 규칙 3 — fork → upstream PR

이 저장소는 `next-step/my-claude-code-os`의 fork다. NextStep은 **수강생 GitHub 아이디와 같은 이름의 브랜치**를 upstream에 두고, 각자 fork에서 그 브랜치로 PR을 보낸다.

- base: `next-step/my-claude-code-os` 의 `mj950425` 브랜치
- head: `mj950425/my-claude-code-os` 의 작업 브랜치 (`step0`, `step1`, …)
- 제목 관례: `N주차 - 요약` 또는 `[stepN] 요약`

```bash
export GH_TOKEN=$(gh auth token --user mj950425)
gh pr create \
  --repo next-step/my-claude-code-os \
  --base mj950425 \
  --head mj950425:<작업브랜치> \
  --title "<N주차 - 요약>" \
  --body "<본문>"
```

head는 **단계 브랜치**를 쓴다. `main`을 head로 쓰면 이후 main에 쌓는 커밋이 열려 있는 PR에 그대로 딸려 들어간다.

관례가 헷갈리면 추측하지 말고 이미 머지된 PR의 실제 구조를 본다.

```bash
gh pr list --repo next-step/my-claude-code-os --state all --limit 10 \
  --json number,title,baseRefName,headRefName \
  --template '{{range .}}#{{.number}} [{{.baseRefName}} <- {{.headRefName}}] {{.title}}{{"\n"}}{{end}}'
```

## 에러 → 원인 표

권한 오류는 "자격증명이 부족한가"와 "대상/신원이 틀렸는가"를 먼저 가른다. 아래는 모두 **후자**였고, 토큰 재발급이나 스코프 확대로는 고쳐지지 않는다.

| 에러 | 실제 원인 | 조치 |
|---|---|---|
| `Permission to X denied to Y` (403) | 저장소 소유자와 인증 계정 불일치. git이 키체인의 다른 계정을 씀 | 규칙 1로 신원 확인 → 규칙 2로 계정 정리 |
| `must be a collaborator` (PR 생성) | fork PR에는 권한이 필요 없다. **활성 gh 계정이 fork 소유자가 아닌 것**이 원인 | `gh auth status`로 활성 계정 확인 → `GH_TOKEN`으로 올바른 계정 지정 |
| `Permission denied (publickey)` | SSH 키가 GitHub에 미등록 | HTTPS + gh 토큰 경로를 쓴다. SSH로 우회하려 하지 않는다 |

`gh auth status`는 **로그인된 모든 계정과 활성 계정**을 보여준다. `gh api user`만으로는 "다른 계정도 로그인돼 있다"는 사실이 안 보이므로, 계정 문제를 진단할 때는 `gh auth status`를 쓴다.

## 진단 원칙

되는 사례와 비교한다. PR/푸시가 실패하면 **이미 성공한 동일 구조의 사례를 조회해 내 명령과 대조**한다. 구조가 같으면 남은 변수는 신원뿐이다. 추측으로 재시도하지 않는다.
