# clothing-thumbnail-model-gender

의류 대표 썸네일 한 장에서 대상 상품을 입은 중심 착용자의 겉으로 보이는 성별 표현을 분류하는 정책 task다. 실제 성별 정체성은 추정하지 않는다.

## 소유

| 종류 | 파일 |
|---|---|
| 선언 | `profile.json` — `policyTask` 필드/값 코드와 선택적 외부 `promptDelivery` 연결 |
| 정책 | `definitions.md` — 목적·허용값·판정 규칙의 정본 |
| 산출물 | `.claude/os/runs/clothing-thumbnail-model-gender/` (재생성 가능한 평가 기록) |

## 정책 전달과 운영 프롬프트

SDK/엔진은 `policyTask.definitions`를 `policy_prompt.render_agent_policy`가 매 실행 읽어 목적·규칙·허용값을 만든다. 이는 운영 프롬프트 동기화 설정과 독립된 공통 경로다. 이 프로필의 `policyTask.promptDelivery`는 외부 운영 프롬프트 리소스와 어댑터의 해시/버전 표기를 정책 정의와 함께 갱신하는 선택 기능이다.

규칙의 `판례: 없음`은 아직 그 규칙에 명시적으로 연결한 선례가 없다는 뜻이다. 이 프로필은 `gtTask`와 사람 판정 원장을 선언하지 않으므로 SDK 판례 검색 후보도 없다. 이후 GT 과제와 경계 질문이 생기면 질문을 `OPEN`으로 보존하고, 사람이 답한 결정 사례만 해당 규칙에 연결한다.
