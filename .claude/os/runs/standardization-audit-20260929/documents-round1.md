# 문서·프로필 표준화 감사 — Round 1

- 기준일: 2026-09-29
- 범위: `.claude/os/attributes/*`의 현재 프로필·정책 정의 문서와 공통 정책 계약/로더만 읽음. 구현 파일은 수정하지 않음.
- 발견한 작업: 5개 attribute pack (`accessories-category-gender`, `bag-category-gender`, `clothing-category-gender`, `clothing-thumbnail-model-gender`, `clothing-thumbnail-observation`).
- 점수는 실제 추론 정확도가 아니라 요청된 문서/프로필 계약 점수다. 항목 순서는 `정책 정본 / 목적·값·규칙 / 업무-기술 분리 / 공유 로더·프롬프트 왕복 / 선례 추적성`이며 가중치는 `25/25/20/20/10`이다.

## 요약 점수

| 작업 | 점수 | 세부 점수 | 90 미만 사유 |
|---|---:|---:|---|
| accessories-category-gender | 88 | 25 / 24 / 19 / 14 / 6 | 공유 렌더링은 가능하지만 `promptDelivery`가 없어 원본 운영 프롬프트까지 왕복하는 계약이 프로필에서 확인되지 않음. 규칙과 선례 링크도 없음. |
| bag-category-gender | 89 | 25 / 24 / 19 / 15 / 6 | 위와 같이 운영 프롬프트 연결이 프로필에 없음. 정책 규칙은 명료하나 규칙별 선례가 모두 `없음`이고, 실제 선례 파일과 정의 규칙 간 링크가 보이지 않음. |
| clothing-category-gender | 94 | 25 / 24 / 20 / 20 / 5 | 완전한 promptDelivery 연결이 확인됨. 선례/외부 근거 연결은 별도 제시되지 않음. |
| clothing-thumbnail-model-gender | 92 | 25 / 25 / 20 / 20 / 2 | 완전한 promptDelivery 연결이 확인됨. 결정 사례나 출처 연결이 없음. |
| clothing-thumbnail-observation | 92 | 25 / 24 / 19 / 18 / 6 | 다섯 필드가 자세하고 코드로 연결됨. 다만 일부 규칙의 선례는 결정 ID를 정의 문장 안에 직접 나열해 링크/검증 가능한 선례 연결로 관리되지 않음. |

현재 점수만으로 모든 과제가 90점 이상이라고 판정하지 않는다. Accessory와 bag은 우선 프롬프트 전달 범위를 명시하고 실제 연결 여부를 확인해야 한다. 점수는 문서 근거가 추가되면 재평가한다.

## 공통 계약과 로더 확인

- 저장소 지침은 `policy-document-v2`를 새 정책 표준으로 지정하고, 정책 페이지와 에이전트가 `policy_prompt.render_agent_policy`의 같은 결과를 쓰도록 한다. 속성별 판단 문장을 엔진/역할 프롬프트에 넣지 않도록 경계를 둔다. (`CLAUDE.md:14-20, 38-52`)
- 정책 문서 계약은 목적·허용값·규칙을 정책 문서에, 응답 코드를 프로필에 둔다. 다중 필드는 각 필드 표시명과 정확히 맞는 H2 섹션을 요구한다. (`.claude/os/engine/contracts/policy-document.md:9-31`)
- 현재 소스는 정책 H2 중복과 공통/필드 규칙 ID 중복을 거부한다. root가 Round 1 도중 반영한 검사로 확인되어, 이전 코드 상태에 대한 별도 baseline 결함으로 점수화하지 않는다. 관련 자동 검증은 root가 추가 중이며 이 보고서에서는 실행하지 않았다. (`.claude/os/engine/scripts/policy_document.py:197-212, 233-244`)
- 공유 계약은 `policyTask`를 GT 유무와 무관하게 사용하고, GT 작업이 있을 때 `gtTask`를 별도 설정하며 두 필드 목록을 맞추도록 한다. (`.claude/os/engine/contracts/gt-task.md:586-620`)

## 작업별 근거와 제안

### accessories-category-gender — 88/100

**근거.** 정책 목적과 네 허용값, 5개 판단 규칙이 `definitions.md`에 한 곳에서 관리된다. (`.claude/os/attributes/accessories-category-gender/definitions.md:1-43`) 프로필의 `policyTask`와 `gtTask`가 같은 정의 파일을 가리키고 정책 표시값을 응답 코드에 연결한다. (`profile.json:11-26, 31-38, 90-98`) 패키지 문서도 정의 파일이 허용값과 판독 기준의 정본이며 정책 어댑터가 없다고 설명한다. (`package.md:3-15`)

**점수 판단.** 값/규칙은 구체적이고 역할 분리도 양호하다. 하지만 `policyTask`에는 정책 문서 경로와 값 코드만 있으며 `promptDelivery`가 없다(`profile.json:11-26`). 공통 renderer가 정책을 표시/에이전트에 전달하는 흐름은 저장소 지침에 있지만(`CLAUDE.md:14-20`), 이 프로필로부터 운영 프롬프트 리소스와 버전 소유자를 왕복 동기화하는 구성이 확인되지 않는다. `A1`~`A5` 모두 판례가 없어서 새 경계가 정의를 바꾼 과정과 적용 사례를 따라갈 링크도 없다(`definitions.md:20-43`).

**제안.** 작업의 공식 전달 목적을 먼저 문서화한다. 운영 추론 프롬프트까지 동기화하는 작업이면 실제 리소스/어댑터/마커를 `policyTask.promptDelivery`에 선언한다. 내부 GT 개선만 하는 작업이면 운영 프롬프트 동기화를 범위 밖으로 두는 이유와 shared-renderer 경로를 패키지 문서에 명시한다. 규칙별 경계가 결정된 뒤에만 선례 ID/출처를 붙인다.

### bag-category-gender — 89/100

**근거.** 정의 문서는 목적, 네 값, 식별 가능한 우선순위 규칙을 가진다. (`.claude/os/attributes/bag-category-gender/definitions.md:1-47`) 정책 프로필은 정의 문서와 값 코드 매핑을 선언하고, 분리된 `gtTask`도 같은 정본 문서를 사용한다. (`profile.json:11-25, 166-202`) 패키지 문서는 정의 문서를 값/자연어 기준으로 설명하고 이전 정책 문서 경로의 안내 성격을 분리한다. (`package.md:11-18, 45-46`)

**점수 판단.** 정책과 기술 설정의 분리는 좋다. `policyTask.promptDelivery`가 없어 실제 제품 프롬프트 리소스로 돌아가는 동기화 연결은 확인할 수 없다(`profile.json:11-25`). 정책에는 명명된 규칙이 있지만 각각의 `판례`는 모두 `없음`이며(`definitions.md:20-47`), 같은 팩에 선례 파일이 있어도 정책 규칙의 선례 필드가 연결하지 않는다(예: `.claude/os/attributes/bag-category-gender/policy/precedents/` 및 `definitions.md:20-47`). 또한 프로필의 응답 코드 `UNDETERMINED`와 레거시 `labels`의 `UNCLASSIFIED`가 함께 있어 (`profile.json:19-24, 67-72`) 서로 다른 값인 이유가 신규 독자에게 즉시 드러나지 않는다. GT 필드는 다시 `unknownLabel: UNDETERMINED`를 선언한다(`profile.json:193-202`). 이는 의도된 옛 코드 호환일 수 있으므로 정합성 오류라고 단정하지 않고 설명 부족으로 감점했다.

**제안.** 운영 프롬프트 동기화 여부를 액세서리와 같은 기준으로 결정한다. 선례 파일과 정책 규칙을 규칙 ID 기준으로 서로 연결한다. `UNCLASSIFIED`가 원천 GT 전용 별칭이라면 프로필/패키지 문서에 그 변환 방향을 한 문장으로 명시한다.

### clothing-category-gender — 94/100

**근거.** 목적, 네 허용값, 입력/제외 정보/착용자 확인/우선순위 규칙을 업무 기준 문서에 둔다. (`.claude/os/attributes/clothing-category-gender/definitions.md:1-71`) 프로필의 필드 값 코드가 정책값과 맞고, 운영 프롬프트 리소스·어댑터·필드·마커·해시/버전 템플릿이 `policyTask.promptDelivery` 아래 선언돼 있다. (`profile.json:11-52`) `gtTask`는 정책과 별도 선언이며 같은 정의 경로를 사용한다. (`profile.json:53-60, 112-120`)

**점수 판단.** 정본과 코드 바인딩이 분명하고 운영 프롬프트 연결도 선언되어 있어 가장 완결된 표준 사례다. 규칙 문서는 실제 관찰 조건과 판정 순서를 제공한다. (`definitions.md:16-71`) 다만 각 기준이 어떤 승인된 경계 결정에서 나왔는지 보여 주는 규칙별 선례 링크/출처는 현재 정의에서 확인되지 않는다(`definitions.md:16-71`).

**제안.** 이 프로필의 `promptDelivery`를 표준 예제로 삼아 각 task skeleton의 필수/선택 키와 문서 예제를 통일한다. 규칙 기원이 확인 가능한 경우만 해당 규칙에 결정 ID 또는 출처를 추가한다.

### clothing-thumbnail-model-gender — 92/100

**근거.** 목적은 썸네일 한 장의 중심 착용자 표현으로 범위를 제한하고 실제 정체성 추측을 제외한다. 값 4개와 중심 착용자 선택/표현 판정/모호함 처리 규칙이 명시돼 있다. (`.claude/os/attributes/clothing-thumbnail-model-gender/definitions.md:1-32`) 프로필은 필드와 값 코드, legacy projection, 실제 프롬프트 리소스·어댑터·필드·해시/버전 연결을 선언한다. (`profile.json:11-42`)

**점수 판단.** 프로필 기술 설정과 업무 기준이 분리되고 왕복 전달의 선언도 완전하다. 값 구성이 프롬프트 설정과 일치한다. (`profile.json:11-42`) 다만 정의 규칙은 모두 `판례: 없음`이고, 실제 경계 결정을 추적할 외부 사례/출처 연결이 없다(`definitions.md:20-32`). 이 단순한 분류가 의도적으로 precedent-free인지 문서에서 판단할 수 없다.

**제안.** 정책 편집 화면/생성 스켈레톤에 `선례 없음`을 유효한 완료 상태로 남기되, 경계 확정 사례가 생기면 연결할 선택 필드를 공통 UI에서 제공한다. 정책 출처가 이미 존재한다면 규칙마다 그 문서/결정 ID를 연결한다.

### clothing-thumbnail-observation — 92/100

**근거.** 정의 문서는 썸네일 한 장 관찰이라는 목적을 두고 다섯 필드 각각의 허용값과 우선순위 규칙을 설명한다. (`.claude/os/attributes/clothing-thumbnail-observation/definitions.md:1-182`) 정책 프로필은 다섯 필드의 표시명/코드를 선언하고 `gtTask`도 같은 필드 집합과 정의를 사용한다. (`profile.json:11-60, 61-90`) 패키지 문서는 이것이 GT 개선 task이고 허용값 정본이 definitions임을 밝힌다. (`package.md:1-20`)

**점수 판단.** 이 태스크는 생산성 높은 기준/예외가 구체적이다. 특히 전체 노출 필드의 가림 예외와 일반 경계 규칙이 분리돼 있다(`definitions.md:135-182`). 선례는 일부 `GTD-*` 번호와 적용 상품 키로 서술되지만, 정의 문서 한 줄 안에 들어 있어 별도 선례 원장/링크로 확인하거나 변경을 추적할 계약이 아니다(`definitions.md:135-177`). 다른 필드와 공통 규칙에는 선례가 없는 것으로 표시돼 있어 모든 규칙이 같은 방식으로 추적되는 것도 아니다(`definitions.md:18-68, 179-182`). 프로필도 생산 프롬프트와의 `promptDelivery`를 선언하지 않는데, 패키지 문서는 운영 프롬프트 v106과 코드 열거형을 이전 출처라고만 적는다(`package.md:17-20`). 관찰 GT 작업이므로 이 공백이 의도적일 수 있지만, 범위 설명이 필요하다.

**제안.** `GTD-*` 판정 출처를 클릭 가능한/조회 가능한 선례 자료로 연결하고, 규칙 본문에는 결정 원칙만 남긴다. 운영 prompt와 동기화하지 않는다면 그 이유(운영 모델의 관찰값 계약인지, 내부 GT 전용인지)를 `package.md`에서 구분해 둔다.

## 다음 라운드의 문서 작업 후보

1. 다섯 프로필 모두에서 `promptDelivery`가 필수인지 선택인지 명확히 하는 짧은 task skeleton 계약을 정한다. 값은 `정의/렌더만` 또는 `운영 프롬프트까지 동기화`처럼 독자가 이해하는 말로 보여 주고, 기계 경로는 별도 기술 설정으로 유지한다.
2. 정책 규칙에 출처/선례를 적는 공통 방법을 정한다. `선례가 없음`도 명시적 상태로 유지하고, 결정이 있는 규칙은 추적 가능한 ID/위치로 연결한다.
3. Bag의 `UNCLASSIFIED`와 `UNDETERMINED` 관계를 문서로 확정한다. 원천 GT 코드, 정책 이름, 응답 코드가 서로 다른 층이라는 설명을 profile/package에 보탠다.
4. `clothing-category-gender` 프로필의 `promptDelivery`를 왕복 전달 표준 예제로 활용하되, 썸네일 GT 전용 과제에 생산 프롬프트 동기화를 억지로 추가하지 않는다.
