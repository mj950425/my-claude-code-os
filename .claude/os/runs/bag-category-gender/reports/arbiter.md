# 가방 성별 정책·골든셋 감사 · 심판 결과

- 목표 문서: `.claude/os/attributes/bag-category-gender/goal.md`
- 정책 문서: `.claude/os/attributes/bag-category-gender/policy/policy.md`
- 판정 대상: 320건 (큐 중복 제거)
- 미결 판례: BG-0001, BG-0002, BG-0003

이 파일의 모든 판정은 **추천**이다. 사람 판정 원장에 자동으로 들어가지 않는다.

## 귀책 분포

| 고칠 곳 | 뜻 | 건수 |
|---|---|---|
| `NONE` | 충돌 없음 | 201 |
| `GOAL` | 사람이 목표 기준으로 경계를 정한다 | 56 |
| `RUNTIME` | 실행을 고친다 | 51 |
| `PENDING_PRECEDENT` | 미결 판례가 답해야 정해진다 | 12 |

## 적용된 정책 규칙

| 규칙 | 건수 |
|---|---|
| `P3_WEARER` | 213 |
| `NO_APPLICABLE_RULE` | 54 |
| `P0_NO_EVIDENCE` | 48 |
| `P1_DIRECT_TEXT` | 3 |
| `P2_COMBINED_DESIGN` | 2 |

## 미결 판례에 걸린 건수

| 판례 | 건수 | 질문 |
|---|---|---|
| [BG-0002](.claude/os/attributes/bag-category-gender/policy/precedents/BG-0002.md) | 48 | GQ-RUN-001 |
| [BG-0003](.claude/os/attributes/bag-category-gender/policy/precedents/BG-0003.md) | 43 | GQ-SOURCE-001 |
| [BG-0001](.claude/os/attributes/bag-category-gender/policy/precedents/BG-0001.md) | 14 | GQ-GT-001 |

## 기존 신호가 어디로 갔나

| 큐 신호 | 새 귀책 | 건수 |
|---|---|---|
| `POLICY_RUNTIME_CONTRADICTION` | `RUNTIME` | 48 |
| `GOLDEN_SOURCE_CONFLICT` | `GOAL` | 30 |
| `POLICY_GOLDEN_GAP` | `RUNTIME` | 28 |
| `GOLDEN_UNSUPPORTED_AGREEMENT` | `RUNTIME` | 20 |
| `INTERACTION_POLICY_RECOVERED` | `GOAL` | 16 |
| `POLICY_GOLDEN_CONFLICT` | `GOAL` | 12 |
| `GOLDEN_POLICY_VIOLATION_CANDIDATE` | `PENDING_PRECEDENT` | 11 |
| `GOLDEN_SOURCE_CONFLICT` | `RUNTIME` | 11 |
| `INTERACTION_POLICY_RECOVERED` | `PENDING_PRECEDENT` | 9 |
| `GOLDEN_SOURCE_CONFLICT` | `PENDING_PRECEDENT` | 2 |
| `GOLDEN_POLICY_VIOLATION_CANDIDATE` | `RUNTIME` | 1 |
| `INTERACTION_POLICY_RECOVERED` | `RUNTIME` | 1 |
| `INVALID_TEXT_EVIDENCE_DROPPED` | `PENDING_PRECEDENT` | 1 |
| `INVALID_TEXT_EVIDENCE_DROPPED` | `RUNTIME` | 1 |
| `MODEL_POLICY_CONTRADICTION` | `RUNTIME` | 1 |
