# engine

속성이 무엇인지 모른 채 사이클을 돌리는 공통 코어. 속성이 늘어도 이 패키지는 바뀌지 않는다.

## 소유

| 종류 | 파일 |
|---|---|
| 계약 | `contracts/customization-boundary.md` · `contracts/policy-layer.md` · `contracts/gt-layer.md`(사이클 GT 계보·판정 원장) · `contracts/gt-task.md`(GT 개선 과제) · `contracts/declared-leaks.json` |
| 목표 | `goal.md` — 프로세스 전체가 무엇을 성공으로 보는가 |
| 오케스트레이터 | `scripts/run_catalog_cycle.py` |
| 프로필 해석 | `scripts/catalog_profile.py` |
| 심판 | `scripts/arbitrate.py` |
| 정책 인덱스 | `scripts/build_policy_index.py` |
| 진행률 | `scripts/build_review_progress.py` |
| 판정 원장 | `scripts/record_review_decision.py` |
| GT 계보 합치기 | `scripts/build_gt.py` — 계보 여럿을 순위로 합쳐 `.claude/gt/<id>/gt.jsonl` 하나로, 진 라벨은 이력으로 |
| 원장 파생 | `scripts/build_gt_decisions.py` — 판정 원장에서 정정·확인·판례 대기·판례별 사례집을 다시 만든다 |
| 규칙 브리프 | `scripts/build_precedent_brief.py` — 정책 규칙마다 원문 통째 + 확정 근거 판례 + 적용 사례 한 장 |
| 로컬 서버 | `scripts/serve_reports.py` — 화면을 그리지 않고 리다이렉트만. 쓰는 곳은 두 판정 원장(`/decide`·`/gt-decide`)뿐 |
| 페이지 머리 | `scripts/page_style.py` — 보고서와 GT 개선 화면이 함께 쓰는 글꼴·색. 하네스가 감사 보고서 모듈을 import하지 않게 떼어 냈다 |
| 리포트 | `scripts/render_catalog_report.py` |
| 개선 포인트 작업 목록 | `scripts/build_improvement_worklist.py` — 귀책으로 갈라 건·군집을 고르고 **센다** |
| 개선 포인트 보고서 | `scripts/render_improvements.py` — 판단과 반증을 합쳐 `improvements/`에 남긴다 |
| 보고서 형태 점검 | `scripts/check_report_shape.py` — 훅이 부른다. 진입점 링크는 `.claude/hooks/check-report-shape.py` |
| 뼈대 | `templates/goal.md` · `templates/policy.md` · `templates/precedent.md` |
| 테스트 | `tests/` — 계약 회귀와 패키지 경계 |
| 워크플로우 | `workflows/improvement-sweep.js` — 건·군집을 나눠 돌리는 스윕. `Workflow`가 `scriptPath`로 부른다 |
| 스킬 | `skills/` — `catalog-data-os` · `catalog-policy-golden-audit` · `catalog-review-decision` · `catalog-audit-report` · `catalog-improvement-sweep` |
| 에이전트 | `agents/catalog-golden-adjudicator.md` — 큐의 한 건이 정책 공백인가 GT 오류인가 실행 오류인가 |
| 에이전트 | `agents/catalog-policy-cluster-scout.md` — 같은 이유로 막힌 군집 하나가 어떤 정책 결함인가 |
| GT 개선 하네스 | `scripts/gt_review.py`(진입점) · `gt_task.py`(고르기) · `gt_images.py`(사진) · `gt_review_render.py`(화면) · `gt_decisions.py`(원장) · `gt_publish.py`(GitHub에 브랜치+PR) — 계약 `contracts/gt-task.md` |
| GT 개선 워크플로우 | `workflows/gt-review.js` — 과제를 모른다. 판독자·반론자 유형도 인자로 받는다 |
| 에이전트 | `agents/gt-blind-reader.md` — GT를 모른 채 사진과 정의만으로 칸을 읽는다 |
| 에이전트 | `agents/gt-defender.md` — 판독이 GT와 갈린 칸에서 GT를 지킬 근거를 찾는다 |
| 스킬 | `skills/gt-improve` — 데이터 운영팀의 단일 진입점 «GT 개선해줘» |
| 진입점 링크 | `.claude/skills/<이름>` · `.claude/agents/engine/<이름>.md` → 여기. 실체는 이 패키지가 소유한다 |

## 의존

```
engine  ──▶  common   (타일 규칙 — GT 개선 사진 준비와 보고서의 조각 표시)
engine  ──✗  review · interview · attributes
```

## 규칙

- **속성 이름을 코드에 쓰지 않는다.** 기본 프로필도 `attributes/*/profile.json`을 찾아서 정하고,
  둘 이상이면 `--profile`을 요구한다. 하드코딩된 기본값은 그 자체로 도메인 누수다.
- **프로젝트 루트는 폴더 깊이로 세지 않는다.** `.claude`를 가진 상위 폴더를 찾는다.
  깊이를 세면 패키지를 옮길 때마다 인덱스가 조용히 틀린다.
- **만든 것은 `run-summary.json`의 `artifacts`에 선언한다.** 하류가 경로를 관습으로 추측하기
  시작하면 그것은 계약이 아니다. 선언되지 않은 산출물은 심사에서 `SKIPPED`로 남는다.
- 도메인 어휘가 불가피하게 남으면 `contracts/declared-leaks.json`에 이유와 후속 조치를 적는다.
- **보고서 형태 점검은 어떤 run이든 같은 계약으로 본다.** 기준선은 언제나 `run-summary.json`이다.
  점검이 속성별 예외를 갖기 시작하면 그것은 이미 엔진이 아니다.

## 실행 순서

```
import 어댑터 → audit 어댑터 → arbitrate → build_policy_index → build_review_progress → render_catalog_report
```

앞의 셋은 속성이 제공하고, 뒤의 셋은 엔진이 제공한다.

사이클이 끝난 **뒤에** 개선 포인트 스윕이 이어진다. 사이클 안에 넣지 않은 이유는 둘이다 —
스윕은 사람이 다음에 손댈 것을 고르는 일이라 매 실행마다 돌 이유가 없고, 에이전트를 여럿 띄우므로
비용이 사이클과 다르다. 사이클은 데이터를 만들고, 스윕은 그 데이터에 대고 묻는다.

```
build_improvement_worklist → (워크플로우: 판정 · 반증 · 군집 질문) → render_improvements
```

## GT 개선 하네스 — 감사 사이클과 따로 서는 문

감사 사이클은 「상품 하나에 라벨 하나」와 정책·판례·심판을 전제한다. GT 개선 하네스는 그 전제를
하나도 요구하지 않는다 — 프로필의 `gtTask` 블록만 있으면 돈다. 그래서 정책 문서가 아직 없는
메타데이터 GT(이미지 한 장에 관찰 칸 여럿)와 가져오기만 있는 속성도 같은 문으로 들어온다.

```
gt_review.py prepare → (워크플로우 gt-review: 사진 판독 · GT 편 반론) → gt_review.py finish → 화면에서 사람이 답 → gt_review.py export
```

둘을 합치지 않은 이유 — 사이클에 넣으면 정책이 없는 과제는 영영 못 들어온다. 반대로 사이클을
하네스로 옮기면 판례·심판이 만든 경계가 사라진다. 대신 **한 GT에 판정 원장은 하나**라는 선을
로더가 지킨다: 사이클의 원장(`gt` 블록)을 가진 프로필은 `gtTask`를 선언할 수 없다.

## 보고서 형태 점검

`PostToolUse` 훅이 보고서가 갱신된 직후 자동으로 돈다. 네 가지를 본다 — 선언한 산출물이
실제로 있는가, 큐 건수와 요약 숫자가 같은가, 보고서의 `N건`·`N%`가 요약에서 나올 수 있는
값인가, HTML이 이번 요약의 신호를 담고 있는가.

세 번째가 핵심이다. 이 프로젝트의 규칙은 **다시 세어야 하는 숫자를 문서에 적지 않는다**이고,
복사된 숫자는 조용히 틀린 채로 판단 근거가 된다. 훅은 사람이 기억하지 않아도 그 순간에 돈다.

최근에 갱신된 run만 본다. 훅은 모든 Bash·Write·Edit 뒤에 붙으므로, 무관한 명령에서
조용하지 않으면 그 자체가 소음이 된다. 손으로 돌릴 때는 창을 무시하게 할 수 있다.

```bash
.claude/hooks/check-report-shape.py --all
```

## 검증

```bash
python3 -m pytest .claude/os/engine/tests -q
```
