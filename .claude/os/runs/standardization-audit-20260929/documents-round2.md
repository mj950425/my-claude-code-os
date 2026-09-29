# 문서·프로필 표준화 감사 — Round 2

- 기준일: 2026-09-29
- 범위: 5개 attribute package 설명, 공통 정책 문서 계약, 정책 정의의 사례 연결을 재검토했다. 정책 규칙·DB·실험 결과는 변경하지 않았다.
- 점수는 문서 계약의 완성도이며 실제 분류 정확도는 아니다. 고정 가중치: 정책 정본 25, 목적·허용값·규칙 25, 업무/기술 분리 20, 공유 로더·프롬프트 왕복 20, 판례 추적성 10.

## 점수

| 작업 | 합계 | 정본 / 규칙 / 분리 / 전달 / 판례 | 남은 감점 |
|---|---:|---:|---|
| accessories-category-gender | 94 | 25 / 24 / 20 / 20 / 5 | A1–A5에 직접 연결한 승인 판례가 없다. 이는 사람이 답한 GT가 전혀 없다는 뜻과는 구별해 문서화했다. |
| bag-category-gender | 95 | 25 / 24 / 20 / 20 / 6 | 현재 BG 질문은 OPEN이다. 규칙에 연결된 확정 판례가 없으며, OPEN 질문을 완료된 사례처럼 연결할 수 없다. |
| clothing-category-gender | 95 | 25 / 25 / 20 / 20 / 5 | 규칙별 승인 판례 연결은 확인되지 않는다. GT 사진 준비는 아직 입력 색인/이미지가 없어 미완료다. |
| clothing-thumbnail-model-gender | 93 | 25 / 25 / 20 / 20 / 3 | 이 프로필에는 GT task나 사람 판정 원장이 없어 판례 후보가 없다. 향후 확정 사례를 연결할 경로는 설명돼 있다. |
| clothing-thumbnail-observation | 98 | 25 / 25 / 20 / 20 / 8 | R6에는 별도 승인 사례가 연결되지 않았다. 다른 사례는 package의 별도 ID→원장 링크표에서 추적하며, 정책 문서 자체에는 ID만 둔다. |

점수 산정은 promptDelivery 유무를 SDK 정책 전달의 완성도 감점으로 보지 않는다. 현재 SDK는 모든 task에서 정책 정의를 공통 렌더러로 동적으로 읽는다. `promptDelivery`는 외부 Java 운영 리소스와 해시/버전 표기를 동기화하는 별도 선택 기능이다. 따라서 누락 자체는 결함이 아니며, 외부 동기화를 선언한 의류 정책은 선택 연결의 목적이 package에 설명되어 있다.

## 공통 계약 및 확인 범위

- v2 문서 계약은 목적·허용값·규칙을 정의 문서에, 응답 코드를 프로필에 둔다. 단일/복수 속성 문서 및 공통 규칙의 모양도 정한다. (`.claude/os/engine/contracts/policy-document.md:7-41`)
- SDK와 정책 페이지는 `policy_prompt.render_agent_policy` 결과를 공유하고 GT 판독도 같은 렌더러를 부른다. `promptDelivery`는 외부 프롬프트/Java 어댑터 버전 동기화 선택 기능이다. (`policy-document.md:43-49`; `.claude/os/engine/scripts/policy_prompt.py:208-225`; `.claude/os/engine/scripts/gt_task.py:672-680`; `.claude/os/engine/scripts/gt_review.py:185-196`)
- 규칙의 `판례`는 명시적으로 승인 연결한 결정이며, `판례: 없음`도 유효한 상태다. 미결 OPEN 질문과 사람의 CORRECT/CONFIRM 답변 후보는 별개이며, 후보에서 특정 rule ID를 주장하려면 정의 문서에 연결 ID가 있어야 한다. (`policy-document.md:50-54`; `.claude/os/engine/scripts/gt_review.py:1277-1295`; `.claude/os/engine/scripts/policy_precedents.py:26-59`)
- 중복 H2와 규칙 ID 중복 거부는 현재 로더 코드에 반영돼 있다. 이는 문서 점수의 미해결 결함으로 계산하지 않았다. root가 이 검증용 테스트 8개를 통과했다고 보고했다. (`.claude/os/engine/scripts/policy_document.py:197-212,233-244`; 테스트 결과는 root 보고)

## 작업별 판정과 잔여 결함

### accessories-category-gender — 94/100

`definitions.md`가 목적·값·A1–A5 규칙의 정본이고 package 소유표 및 profile 선언도 이를 따른다. (`.claude/os/attributes/accessories-category-gender/package.md:7-15`; `profile.json:11-38`; `definitions.md:1-43`) 현재 문구는 별도 정책 감사/심판 어댑터가 없다는 뜻이며, 정책 정의나 GT 개선 과제가 없다는 뜻은 아니다. package는 SDK/GT의 공통 동적 렌더 경로와 외부 동기화 설정이 이 task에는 없음을 명시한다. 사람의 CORRECT/CONFIRM 답변 후보와 명시적 rule precedent를 구별하고, 규칙에 연결되지 않은 후보는 빈 ruleIds로 남긴다. (`package.md:17-21`)

잔여 감점은 A1–A5의 `판례: 없음`이다. 규칙을 뒷받침하는 명시적 승인 결정 링크는 현재 없다. 후보 원장이 있을 수 있다는 것만으로 규칙 사례를 만들어내지 않았다.

### bag-category-gender — 95/100

정책 정본, profile의 policyTask/gtTask, 그리고 별도 업무 목표 문서가 구분돼 있다. (`.claude/os/attributes/bag-category-gender/package.md:11-26`; `profile.json:11-25,166-202`) package는 SDK 동적 렌더와 선택적 외부 프롬프트 동기화를 구별하고, BG 파일들이 OPEN 질문이지 확정 사례가 아니라고 설명한다. (`package.md:28-36`) `UNCLASSIFIED`는 GT 무정답 표식이고 `UNDETERMINED`는 정책 응답값이라는 층위 차이와, 전자를 후자로 일괄 변환하지 않는다는 점도 코드 근거에 맞춰 적었다. (`package.md:34-36`; `profile.json:19-24,67-72,193-202`; `.claude/os/engine/scripts/arbitrate.py:27-29`)

잔여 감점은 확정 precedent 링크가 아직 없다는 점이다. BG 문서가 OPEN 상태인 동안에는 정책에 연결 가능한 확정 사례가 없다.

### clothing-category-gender — 95/100

업무 정의와 값 코드, 외부 프롬프트 v1000/Java 어댑터 동기화 설정, GT 및 incremental 설정과 실제 가져오기 어댑터가 profile/패키지에서 확인된다. (`.claude/os/attributes/clothing-category-gender/profile.json:11-60,176-209`; `package.md:3-19`; `adapters/import_clothing_images.py`) 정의 문서의 허용값 절은 실제 형식대로 `## 허용값`이며, 운영 v1000 prompt에서 옮겨온 현 정본에는 머리말 해시가 있다고 가정하지 않는다. (`definitions.md:1-14`; `package.md:11-12`) 골든셋 사진 준비는 source index와 이미지 파일을 준비하는 절차, JSONL 입력 예시, productKey 조인, 복사/누락 manifest 동작을 안내한다. (`package.md:21-40`)

현재 상태는 **GT 사진 준비 전**이다. 예상 사진 index와 GT 연결용 로컬 `--source-index` 이미지가 이 checkout에서 확인되지 않았으며, label JSONL만으로는 이미지 근거가 준비되지 않는다. 별도 증분 검수 100개는 다른 상품 모집단이므로 이를 GT 사진 자료로 대체할 수 없다. (`package.md:23-27,40-42`) root 보고에 따르면 adapter 테스트 5개는 통과했으나, 본 문서 감사에서 재실행하지 않았다. 잔여 감점은 규칙별 승인 판례 연결의 부재이며, 사진 준비 상태 자체는 문서 설명의 결함이 아니라 아직 해결되지 않은 입력 상태다.

### clothing-thumbnail-model-gender — 93/100

정의 문서가 중심 착용자 표현 분류와 추정 금지, 허용값 및 규칙을 소유하고, profile은 값 코드/실행 설정을 둔다. (`.claude/os/attributes/clothing-thumbnail-model-gender/package.md:3-11`; `profile.json:11-42`; `definitions.md:1-32`) package는 동적 SDK 렌더와 외부 promptDelivery를 구별한다. 이 profile에는 gtTask/사람 판정 원장이 없으므로 판례 검색 후보도 없다고 과장 없이 명시했다. (`package.md:13-17`)

잔여 감점은 확정 precedent 또는 사람 결정 원장이 없는 점이다. 이는 현재 범위/설정상 유효한 상태로 보이며, 미래 사례 발생 시 OPEN 질문과 확정 사례를 구별해 연결하도록 설명했다.

### clothing-thumbnail-observation — 98/100

다섯 관찰 필드의 허용값/규칙을 단일 정의 문서가 소유하고 profile의 policyTask/gtTask와 구분된다. (`.claude/os/attributes/clothing-thumbnail-observation/package.md:11-21`; `profile.json:11-90`; `definitions.md:1-182`) package는 v106이 정책을 옮겨온 출처라는 점과 현재 외부 promptDelivery를 선언하지 않은 점, SDK 동적 전달을 분리한다. (`package.md:19-21`)

정의 문서에는 짧은 GTD ID만 두고, package의 규칙별 표에서 각 ID를 원장 줄로 연결한다. 이는 공통 SDK 프롬프트가 장황한 원장 경로를 반복 렌더하지 않게 한다. 26개 ID 모두 원장에 있고 `productVisibility=WHOLE`이며 대체되지 않은 결정임을 읽기 전용 확인했다. R2를 좁힌 R5 변경은 보관된 정책 이력으로 링크했다. (`definitions.md:150-177`; `package.md:23-32`; `.claude/os/gt/clothing-thumbnail-observation/gt-review/decisions.json`; `.claude/os/runs/clothing-thumbnail-observation/policy-history/20260928T141030671594.md#L242-L252`)

잔여 감점은 일반 경계 규칙 R6에 별도 연결된 승인 사례가 없다는 점이다. 이를 임의 사례로 채우지 않고 `판례: 없음`으로 유지했다.

## 수정 파일과 검증

- 수정한 문서: 다섯 `package.md`, `.claude/os/engine/contracts/policy-document.md`, observation 정의 문서의 기존 GTD 하이퍼링크 표기를 ID 텍스트로 되돌림. 판례별 원장 링크 표는 observation package에 둔다.
- 다섯 package 모두 현재 `profile.json` 선언과 문서 소유표/서술의 모순을 재검토했다. 특히 의류 카테고리는 policyTask, 이미지 가져오기 어댑터, `## 허용값`, profile의 valueCodes를 현 구조대로 설명하도록 바로잡았다.
- 의류 카테고리 package에 골든셋 이미지 준비 입력 형식/실행법/미준비 상태를 기록했다. 라벨이나 평가 결과는 수정하지 않았다.
- 원장 링크는 결정 ID 일치, 대상 필드/값, 대체 여부를 읽기 전용으로 확인했다. 링크 추가는 결정이나 정책 규칙을 바꾸지 않는다.
- root 보고: 정책 문서 검증 테스트 8개와 의류 사진 가져오기 테스트 5개 통과. 본 보고 범위에서는 문서 수정 뒤 테스트를 별도로 실행하지 않았다.
- 모든 다섯 작업이 90점 이상이다. 점수는 남은 판례 공백을 그대로 감점해 계산했으며, 90점에 맞추기 위해 rubric이나 기준을 바꾸지 않았다.
