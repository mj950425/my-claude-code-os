# 속성 작업 런타임·구조 감사 — Round 1

- 기준일: 2026-09-29
- 평가 범위: 실제 GT 개선 진입점, `다음 후보 받기` 런너, GT 워크플로우, 별도 표준 정책 SDK 평가기, 작업 프로필/입력 공급 경로
- 동작 제한: 읽기 전용. 운영 DB·운영 API 호출, 실제 SDK 추론, 사람 판정 기록은 하지 않았다.
- 점수 항목(100): 공통 진입점/설정 25, 동적 정책/숨은 기준 없음 25, 판독·검수·판례·조율 역할 계약 20, 스크립트 검증·비교·재시도 20, 호환성·조용한 실패 방지 10.
- 주의: 모델 품질·실제 SDK가 정책대로 답하는지는 이 정적/로컬 감사에서 입증하지 않았다. 표의 점수는 코드·프로필·로컬 상태로 확인 가능한 런타임 계약 점수다.

## 작업 종류와 실제 문

| 프로필 | 기능 종류 | `gt_review.py tasks` | 현재 입력/준비 상태 | 런타임 점수 |
|---|---|---:|---|---:|
| `accessories-category-gender` | 정책 + GT 개선 + 증분 | 등록됨 | 프로필·GT 원본·사진 색인 존재, 과제 로더 검증 통과; 화면상 준비된 배치 | 91 |
| `bag-category-gender` | 정책 + 감사 사이클 + GT 개선 + 증분 | 등록됨 | 프로필·GT·사진 색인 존재, 과제 로더 검증 통과; 사이클은 GT 원장을 GT 개선 과제에 넘김 | 92 |
| `clothing-category-gender` | 정책 + GT 개선 + 증분 | 등록됨 | 프로필·GT 원본은 있으나 GT 사진 색인 경로가 아직 없음, `prerequisite.adapter`도 없음; 화면상 미준비 | 82 (상한 미적용) |
| `clothing-thumbnail-observation` | 정책 + 다중 필드 GT 개선 + 증분 | 등록됨 | 프로필·GT·사진 색인 존재, 과제 로더 검증 통과; 사진 한 장의 다섯 필드 작업 | 92 |
| `clothing-thumbnail-model-gender` | 정책/운영 프롬프트 설정 전용 | 등록 안 됨(의도된 대로 `gtTask` 없음) | 독립 `policyTask`와 promptDelivery만 있음. GT 화면/증분 진입점 없음 | 90 (GT 런타임 비대상) |

모든 네 `gtTask`가 `.venv` 인터프리터의 `gt_task.load_task(profile)` 검증을 통과했고 `gt_review.py tasks`는 `brokenProfiles: []`를 반환했다. 이 검증은 프로필 규격을 확인한 것이다. 실제로 AI SDK가 네 작업의 이미지를 판독하는 실험은 하지 않았다. 의류 썸네일 모델 성별은 정책 설정 프로필로만 존재하며 GT 과제가 아닌 것을 누락으로 세지 않았다.

## 실제로 이어지는 두 런타임

### GT 개선 화면의 `다음 후보 받기`

버튼은 `gt_review_render.py`에서 `POST /gt-next`를 보낸다. `serve_reports.py`는 해당 과제를 확인하고 `gt_next.py run --task …`를 자식 러너로 띄운다. 러너는 과제를 읽고 `gt_review.cmd_prepare`를 호출해 `workflowArgs`를 만들며, `gt-review.js`를 `read` 단계로 실행하고, 나온 판독과 GT가 다르면 별도 `defend` 단계를 실행한 뒤 `cmd_finish`로 화면 파일을 갱신한다. 잠금·단계 상태·시간 제한·출력 JSON/파일·배치 ID 검사·실패 화면 복구가 러너에 있다. `read` 단계에는 GT/대체 답을 제외한다. GT 개선 과제는 공통 blind reader와 defender를 쓰고, task별로 정의·허용값·증거 입력을 제공한다.

이 버튼의 실행기는 Claude Agent SDK를 사용하는 `gt_agent.py`지만, 내부 호출 대상은 기존 `workflows/gt-review.js`다. 즉 SDK는 운반층이고, 판독/반론 프롬프트와 결과 합치기는 GT 개선 워크플로우가 소유한다.

### 표준 정책 SDK 평가 파이프라인

`policy_batch_review.py`는 고정 manifest를 해시로 묶고 케이스별로 `policy_multi_agent_eval.run()`을 호출한다. 여기는 표준 정책 문서(v2)를 렌더하고 JSON schema, 허용 규칙/판례/이미지 ID 참조를 검증한다. 판독 불일치면 독립 검수, 검수가 `EXTRACT_ERROR`일 때만 한 번 재판독한다. `policy_sdk_runtime.py`는 Read/Agent 도구만 허용하고 쓰기·셸·웹 호출을 막으며 API 키 환경변수를 지우고 subscription auth를 확인한다. batch는 케이스 시간 제한, 제한 동시성, 입력/정책 해시 기반 재개, 원자적 결과 저장, HTML 결과물을 갖는다.

현재 이 파이프라인은 **GT 개선 화면의 버튼에 연결되어 있지 않다.** 따라서 `policy_multi_agent_eval.py`에 보이는 오케스트레이터·판독자·검수자·판례자 구조나 그 재시도 게이트가 화면 버튼 실행의 보증이 되지 않는다. 두 진입점의 분리가 `engine/package.md` 및 `CLAUDE.md`에도 명시되어 있으므로 이 감사는 이를 버그라고 부르지는 않지만, “한 작업이 어떤 계약으로 실제 실행되는가”를 표시하지 않으면 문서에서 예상한 표준 경로를 버튼이 실행한다고 오해하기 쉽다. `clothing-thumbnail-model-gender`는 정책 문서/프롬프트 바인딩은 있으나 이 배치 실행의 검증 사례나 GT 화면은 현재 확인되지 않았다.

## 점수 근거와 주요 결함

### `accessories-category-gender` — 91/100

- 공통 GT 화면과 `gtTask` 로더를 통과하며 정책·GT 정의 경로를 같은 파일에 바인딩한다. 원본은 외부 core 레포의 GT이고, 가져오기 어댑터/사진 색인은 과제 설정으로 제공된다.
- `gt_review.py tasks`에서 준비된 화면이 있고, 상태 요약은 사람 대기 칸을 반환했다. 실제 SDK 추론은 실행하지 않았다.
- 깎은 점: 이 화면 버튼은 별도 표준 정책 SDK evaluator의 오케스트레이터/판례/스키마·추출 오류 재시도 계약을 호출하지 않는다. `policyTask-v2`를 선언했다고 해서 버튼이 그 SDK 경로로 실행되는 것은 아니다.

### `bag-category-gender` — 92/100

- 표준 정책 task + 기존 감사 사이클 + GT 개선 task를 같은 profile 안에서 운영한다. GT 파일은 `.claude/gt/...` 하나이고 사이클 ledger가 `gtTask`에 넘겨져 한 원장 규칙을 지킨다. 사진 색인과 가져오기/감사/심판 어댑터가 있다.
- 과제 로더 통과, 현재 UI 배치 존재. 버튼은 여전히 `gt-review.js` read/defend이며, 정책 SDK 별도 파이프라인의 reviewer/precedent/orchestrator 재시도 게이트를 호출하지 않는다.
- 기존 사이클 자체의 속성별 어댑터는 의도된 책임 분리라 감점하지 않았다. 단, 정책 문서 두 형식(자연어 v2 정책 task와 레거시 사이클 정책 표현)이 실제 각 진입점에 어떻게 투영되는지는 작업 화면에서 더 분명히 구분할 필요가 있다.

### `clothing-category-gender` — 82/100

- 프로필·GT 원본과 작업 규격은 유효하지만, `gtTask.images.path`는 `.claude/os/runs/clothing-category-gender/golden/clothing-image-index.jsonl`이고 현재 파일이 없다. 프로필에는 이를 다시 만드는 `prerequisite.adapter`가 없다. 홈 화면은 미준비라고 표시하지만, 코드상 이 업무의 사진 입력을 만드는 지정된 선행 명령이 확인되지 않았다. 따라서 과제는 등록되어도 현재 GT 증거를 볼 준비가 안 됐고, 조용한 fallback으로 이를 보충하지 않는다.
- 의류의 증분 수집은 `incremental` 프로필을 사용하지만, 이것이 GT 이미지 색인 파일을 생성한다는 코드는 확인되지 않았다. 같은 입력이라고 간주하면 안 된다.
- 이는 실제 작업 경로의 미완성이라 90 미만으로 평가했다. 필요한 조치: 이미지 색인을 생성할 어댑터를 profile에 선언해 홈/오류 안내가 재현 가능한 명령을 알려 주거나, 이미 존재하는 수집물을 이 경로로 명시적으로 연결하고 해당 출처/갱신 계약을 검증한다.

### `clothing-thumbnail-observation` — 92/100

- 정책 task와 GT task에 같은 다섯 필드/정의 경로가 연결되어 있다. 프로필이 필드별 제약, 옛 라벨 변환, 단일 이미지 입력을 선언하며 기존 GT 원장/사진 색인이 존재한다. task loader는 같은 정의 문서를 읽고 다중 필드 값 범위·관계 제약을 검증한다.
- 화면 버튼은 generic read/defend 워크플로우다. 표준 정책 SDK 평가기와 별개이며, 관찰값 테이블/계산은 기존 형식 경로(`gt_derive`)와 연동된다. 이 구분은 하위 호환을 위해 존재하지만 v2 policyTask가 버튼 단계에서 어떻게 소비되는지 테스트/상태 화면만으로 추적하는 보강이 필요하다.
- 깎은 점: 별도 SDK 역할 계약이 버튼 런타임에 없고, policy v2 ↔ GT legacy-definition의 동등성은 로더가 필드/값 목록을 대조해도 모든 판단 문장의 의미 동등성을 증명하지 않는다.

### `clothing-thumbnail-model-gender` — 90/100

- 이 설정은 단독 정책/운영 프롬프트 연결 task다. `policyTask`의 필드·코드와 v2 정의, prompt resource·Java adapter·마커·버전 바인딩이 profile에 선언되어 있다. `gtTask`가 없으므로 GT 개선이나 증분 검수 버튼에 나타나지 않는 것은 정상이다.
- `policy_batch_review.py`의 일반 SDK 진입점은 있으나, 이 profile을 위한 manifest와 고정 케이스가 있는지는 이번 검사로 입증되지 않았다. 그래서 “구현된 정책 연결”은 인정하되 “모델 실행 검증” 점수로 계산하지 않았다.
- 보강: task 목록/정책 페이지에서 “정책 및 운영 프롬프트 설정 전용”임을 일관되게 보이고, 로컬 안전 평가를 위한 manifest/예제 상태를 명시한다. GT 화면 항목을 인위적으로 추가할 필요는 없다.

## 구현자가 우선 처리할 항목

1. **의류 GT 사진 입력 공급 계약** — `clothing-category-gender`의 누락된 이미지 색인 경로에 선행 어댑터가 없다. 다른 UI 결함과 별개로 90점 미만을 만드는 직접 런타임 결함이다.
2. **UI 버튼과 SDK 파이프라인 차이를 상태/계약으로 드러내기** — 버튼은 `gt-review.js`의 blind read + 조건부 defend이고, 표준 정책 SDK evaluator는 별도 경로다. 화면에 SDK의 판례 에이전트·JSON schema·추출 오류 재시도를 그대로 쓴다고 표시하지 않도록 하고, 두 구현의 공통/차이 계약을 회귀 테스트로 고정한다.
3. **프로필 형식이 어느 실행 경로에서 소비되는지 표시** — `policyTask`, `gtTask`, `incremental` 각 블록이 어떤 진입점과 정책 표현에 적용되는지 profile validation 또는 UI에서 안내한다.
4. **AI 실행 증거의 범위 분리** — 로컬 설정 검사/단위 테스트 통과와 실제 SDK 구독 세션의 성공/정책 준수는 다른 근거다. 점수·리포트에 그 둘을 나란히 섞지 않는다.

## 확인 명령과 결과

- `.venv/bin/python .claude/os/engine/scripts/gt_review.py tasks`: GT 과제 네 개를 표시, `brokenProfiles: []`. 의류 GT 과제는 아직 준비 전으로 표시된다.
- `.venv/bin/python`에서 각 `gtTask` 프로필을 `gt_task.load_task()`로 검사: 네 GT 과제 모두 프로필 및 policyTask/gtTask 일치 검증 통과. `source` GT와 사진 경로 존재 여부도 별도 확인했다.
- 시스템 `python3 -m pytest ...`: SDK 테스트 수집 단계에서 `anyio` 모듈을 찾지 못해 종료했다.
- 프로젝트 `.venv` 전체 pytest를 시도했으나 부모 에이전트의 전체 테스트와 중복 실행을 피하기 위해 중단했다(약 60% 진행, 실패가 보였으나 완전한 결과로 취급하지 않음). 부모 에이전트가 공용 SDK venv에서 전체 테스트를 실행 중이다.
- 실제 Claude Agent SDK 평가/구독 호출: **실행 안 함**. 이 감사는 호출 설정과 파일 흐름을 확인했을 뿐 모델이 실제로 지침을 지키는 결과를 입증하지 않는다.
