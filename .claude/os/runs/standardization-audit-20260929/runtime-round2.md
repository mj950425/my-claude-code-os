# 속성 런타임·구조 감사 — Round 2

- 기준일: 2026-09-29
- 범위: 다섯 `profile.json` 작업 종류, GT `다음 후보 받기`의 실제 경로, 별도 policy SDK 평가 경로, 입력 어댑터/검증/테스트 경계
- 실제 Claude SDK 모델 호출·구독 추론: 실행하지 않음. DB, 운영 API, 원장 쓰기도 실행하지 않음.
- 점수(100): 공통 진입점/설정 25 · 동적 정책/숨은 기준 없음 25 · 판독/검수/판례/조율 계약 20 · 코드 검증·비교·재시도 20 · 호환성/실패 표시 10.

## 결과

| 작업 | 점수 | 판정 |
|---|---:|---|
| `accessories-category-gender` | **93** | 정책·GT·증분이 공통 프로필/엔진으로 연결된다. 버튼은 표준 정책 배치 평가기와 분리된 blind read/defend GT 경로다. |
| `bag-category-gender` | **94** | 정책·감사 사이클·GT가 한 프로필에 통합되고 GT ledger 소유도 검증된다. 같은 두 런타임 분리가 남는다. |
| `clothing-category-gender` | **91** | GT 사진 목록을 가져올 전용 로컬 adapter와 prerequisite가 추가됐다. 원본 증거가 제공되지 않아 준비 상태는 계속 `false`다. |
| `clothing-thumbnail-observation` | **94** | 다섯 필드·다중 값·상호 제약을 공통 loader/화면에 연결한다. 기존 관찰값 규칙 경로와 v2 정책 SDK는 다른 실행 경로로 남는다. |
| `clothing-thumbnail-model-gender` | **92** | `policyTask`/운영 프롬프트 설정 전용이다. `gtTask`가 없어 GT 검수·증분 목록에서 제외되는 것은 기능 차이다. SDK 모델 검수 증거는 없다. |

모두 90점 이상이며, 설계상 막히는 항목은 없다(`blockingIssues: []`). 다만 모델의 실제 판단 품질/정책 준수는 점수로 입증하지 않았고 실제 SDK 실행 확인 전이다.

## 실제 실행 경로를 확인한 결과

GT 버튼은 `serve_reports.py`의 `/gt-next`에서 `gt_next.py` 러너를 띄운다. 러너는 `prepare → gt-review.js read → (불일치가 있으면) gt-review.js defend → finish/render`로 진행한다. read 입력은 GT/대체 답을 뺀다. reader와 defender는 `.claude/os/engine/agents/`의 공통 읽기 전용 에이전트이며, 작업별 정책/필드/사진은 profile과 prepare 산출물에서 들어온다. 러너는 잠금·시간 제한·SDK 오류/권한/인증 검사·출력 파일/배치 ID 확인·실패 화면 처리를 담당한다.

표준 정책 SDK 배치 검수는 별도 `policy_batch_review.py → policy_multi_agent_eval.py → policy_sdk_runtime.py` 경로다. 여기에 오케스트레이터의 하위 판독/검수/판례 역할, JSON schema/ref 검사, 불일치 검수, `EXTRACT_ERROR`에만 한 번 재판독, 입력/정책 hash 재개, 시간 제한, API 키 제거/구독 인증 검사가 있다. 이 경로는 GT 화면 버튼이 호출하지 않는다. 두 진입점은 서로 다른 질문(기존 GT 정정 vs 고정 케이스의 정책 검수)을 담당하므로 분리는 유효하다. 다만 이 점을 UI/상태/리포트가 AI 실행 근거로 혼동하지 않게 유지해야 한다.

## Round 1에서 고친 계약 공백

- 표준 SDK 배치 HTML에 고정된 성별/썸네일 값 이름 목록이 있어 모든 과제 입력에도 특정 카탈로그 값이 나타났다. 표시 이름은 현재 정의 문서의 `allowed_values()` 결과로 만들도록 바꾸고, generic-value 회귀 테스트를 추가했다.
- parity 테스트 fake defender가 prompt 본문에서 `건(ID)` 문자열을 찾아야 한다고 가정했지만 실제 workflow는 식별자를 `opts.label`에 전달한다. 테스트 double을 실행 계약과 같게 수정했다.
- 두 에이전트 문서가 과거 정적 폴더 이름(`gt-review/reader/`)과 이전 text-column 계약을 가져야 한다고 요구하던 단언을 고쳤다. 이제 공통 동적 정책 및 `item.view` 입력 경계를 검증한다.
- 새 v2 `policyTask` 키(`documentFormat`, `valueCodes`)와 legacy projection을 테스트에서 명시적으로 검토한다. engine 소유표에 배치 평가기와 판례 검색 에이전트를 올렸다.

## 의류 GT 증거 공급 계약

첫 라운드에서 발견한 것이 “실물 이미지가 있다”는 뜻은 아니었다. 현재 `core-catalog-platfom` 체크아웃에는 이 GT 세트를 만들 원본 이미지/상세 실험 입력이 없다. Root가 제공한 보강은 로컬 JSONL만 받아 GT key를 기준으로 이미지 사본/색인을 만드는 `adapters/import_clothing_images.py`와 `gtTask.prerequisite` 선언이다. 어댑터는 현재 GT 키만 받으며 플랫폼 결합 `productKey`를 사용하고, 누락 파일·GT에 없는 키·아직 덮지 못한 GT 키를 manifest에 기록한다. GT 자체를 변경하지 않고 네트워크를 쓰지 않는다. 사진 입력 없이 억지로 준비 완료로 보이게 하지 않는다.

그래서 `clothing-category-gender`는 **실제 이미지 근거가 없는 상태**지만 **복구 계약이 없는 과제**는 아니다. `gt_review.py tasks`가 `prepared: false`로 둔 것은 올바른 차단 상태다. 증분 검수의 100건은 키/GT/이미지 범위가 다르므로 이 GT 화면의 대체 입력으로 사용하지 않는다. 로컬 이미지 원본이 나중에 제공되기 전까지 사진 검수는 준비되지 않는다.

## 검증 증거와 범위

- `gt_review.py tasks`: `brokenProfiles: []`; 실제 GT task 네 개 등록. 네 task loader 검증은 Round 1에서 통과했다. 의류 과제는 `prepared: false`로 유지됐다.
- 포커스 테스트: 공통 SDK 가상환경에서 `test_package_boundary.py`, `test_gt_review.py`, `test_policy_multi_agent_eval.py`, `test_policy_batch_review.py` 가운데 변경 구역 선택 테스트 **10 passed, 239 deselected**.
- 독립 실행: `rtk /tmp/policy-claude-sdk-venv/bin/python -m unittest discover -s .claude/os/attributes/clothing-category-gender/tests` → **Ran 6 tests, OK**(복사/GT 보존, 플랫폼 키 충돌, 기존 색인 보존, 누락 표시, source/GT 중복 및 빈 키 검사). 상대 이미지 경로가 source-index 폴더 기준으로 해석되는 코드를 확인했다. 추가 확인 과정에서 발견한 GT의 빈/중복 key overwrite 문제는 Root가 검증을 넣고 여섯 번째 테스트로 잠갔다. 새 실행에서 정상 통과해 차단 결함으로 남지 않았다.
- 단위 테스트는 모의 agent/runtime과 파일 경로 계약을 검증한다. 실제 SDK가 reader/defender/precedent 역할을 잘 수행하는지, 구독 인증으로 실제 결과를 내는지 **검증하지 않는다**.

## 남은 비차단 차이

1. GT UI의 두 단계 reader/defender와 정책 SDK 배치의 orchestrator/reader/reviewer/precedent는 역할 계약이 다르다. 따라서 SDK의 판례/JSON schema/재시도 성공 기록을 GT 버튼이 사용했다고 표현하지 않는다.
2. 의류 썸네일 모델 성별은 정책·prompt adapter 설정만 있고 현재 정책 batch manifest 기반 테스트 케이스가 확인되지 않았다. 운영 프롬프트 binding과 추론 결과 검증은 별개로 표시한다.
3. 의류 GT 검수 화면을 실제 준비하려면 GT에 대응하는 로컬 사진 색인 원본을 받아 adapter를 실행해야 한다. 지금은 적법하게 `준비 전`이다.
