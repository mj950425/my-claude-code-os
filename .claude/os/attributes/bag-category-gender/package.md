# bag-category-gender

가방 상품의 대상 고객 성별만 아는 속성 팩. 이 폴더를 지우면 이 속성이 사라지고,
엔진은 그대로 돈다.

이름은 `<대상>-<속성>`이다 — **가방 카테고리**의 상품에 붙는 **대상 고객 성별** 라벨.
가방의 성별도, 사람의 성정체성도 아니다. 쇼핑에서 상품을 거르기 위한 분류 라벨이고
그 경계는 [goal.md](goal.md)가 정한다. `engine`·`interview`가 역할 이름인 것과 달리
이 폴더는 **인스턴스** 이름이라 `attributes/` 아래에 산다. 속성이 늘면 옆으로 늘어난다.

## 소유

| 종류 | 파일 |
|---|---|
| 선언 | `profile.json` — `policyTask`(정책 정의·응답 코드)·라벨·신호·어댑터 경로·`gtTask` |
| 목표 | `goal.md` — 정책과 골든셋이 충돌할 때의 귀책 원칙 |
| 소유 정책 | `definitions.md` — 값과 자연어 판정 규칙의 유일한 정본 |
| 미결 경계 | `policy/precedents/BG-*.md` — `OPEN` 상태의 질문. 확정 사례(`DECIDED`)가 아니므로 판독 기준으로 전달하지 않는다 |
| 골든셋 검수 | `definitions.md` — 골든셋 검수(gtTask)도 같은 정책을 읽는다. 검수 문답은 실행 기록에 쌓인다 |
| 어댑터 | `adapters/import_bag_category_gender_sources.py` · `adapters/audit_bag_category_gender.py` · `adapters/arbiter_bag_category_gender.py` · `adapters/import_bag_review_images.py`(골든셋 검수의 사진 색인·상세 조각 사본) |
| 진입점 | `run.sh` |
| 테스트 | `tests/test_bag_policy_evidence.py` · `tests/test_arbiter.py` |
| 스킬 | `skills/` — `bag-category-gender-os` · `bag-policy-import` · `bag-golden-import` · `bag-policy-golden-audit` · `bag-policy-question` · `bag-golden-decision` · `bag-review-progress` · `bag-ambiguity-review` · `bag-category-gender-interview` |
| 에이전트 | 없다. 상품 판정과 인터뷰는 공유 `catalog-*` 에이전트에 `profile.json`을 넘긴다 |
| 진입점 링크 | `.claude/skills/<이름>` → 여기. 실체는 이 패키지가 소유한다 |
| 산출물 | `.claude/os/runs/bag-category-gender/` (재생성 가능, 소유가 아니라 출력) |

## 정책 전달과 판례

SDK/엔진은 `policyTask.definitions`를 `policy_prompt.render_agent_policy`가 실행 시 읽어 정책 목적·규칙·허용값을 만든다. 골든셋 검수도 같은 공통 렌더러를 사용한다. `policyTask.promptDelivery`는 SDK 실행의 필수 설정이 아니다. 이 설정은 외부 운영 프롬프트 파일과 Java 어댑터의 해시/버전 표기를 동기화할 때만 쓴다. 현재 이 프로필은 그 외부 연결을 선언하지 않았다.

규칙의 `판례: 없음`은 그 규칙에 명시적으로 연결한 선례 ID가 없다는 뜻이다. `policy/precedents/BG-*.md`는 현재 모두 답을 기다리는 `OPEN` 질문이며, 이 파일을 `DECIDED`로 바꾸는 것만으로 SDK 사례 후보가 생기지는 않는다. 판례 검색은 별도로 같은 속성의 GT 검수 원장에서 사람이 `CORRECT` 또는 `CONFIRM`으로 답한 질문 사례를 찾는다(현재 상품은 제외). 그런 사례를 규칙에 승인·연결하려면 해당 `GTD-*`를 규칙의 `판례`에 적는다. 열린 질문을 확정 사례로 꾸며 넣지 않는다.

## 서로 다른 라벨: `UNCLASSIFIED`와 `UNDETERMINED`

두 값은 같은 답의 옛 이름이 아니다. 원천 GT의 `UNCLASSIFIED`는 아직 정답 라벨이 없다는 **빈 정답 표지**다. 감사기는 이 표지를 `NO_GOLD`로 분류해 GT와 정책 판정을 비교하지 않는다. 반면 정책의 `판단 불가`는 근거가 부족할 때 사람이 선택할 수 있는 정식 결과 `UNDETERMINED`다. 화면에서 새로 기록하는 판정에는 이 정책 값을 사용한다. 따라서 원천의 `UNCLASSIFIED`를 `UNDETERMINED`로 일괄 치환하지 않는다. (`profile.json`의 `policyTask.fields[].valueCodes`, `gtTask.fields[].unknownLabel`; `engine/scripts/arbitrate.py`의 `NO_GOLD`)

## 판정 원장 — 골든셋 검수로 옮겼다 (2026-09-28)

이 GT의 판정 원장은 **골든셋 검수 과제**(`profile.json`의 `gtTask`, 화면 `/gt/bag-category-gender`)가 갖는다.
`gt` 블록은 계보를 합쳐 GT를 **만들기만** 한다(`"ledger": "gtTask"`) — 사이클의 판정 문(`/decide`·`record_review_decision`)은 닫혀 있다.
옮길 때 사이클 원장(`runs/bag-category-gender/review/decisions.json`)의 판정은 0건이라 잃은 답이 없다.
GT를 계보에서 다시 합칠 때는 `.claude/gt/bag-category-gender/gt-review/corrections.jsonl`을 마지막 `--corrections`로 넘긴다
([gt-layer.md](../../engine/contracts/gt-layer.md) «원장을 골든셋 검수로 넘긴 속성»).

## 규칙

- 어댑터는 **통역사**다. 원본이 `productGender`든 무엇이든 큐로 나올 때는
  `referenceLabel`·`observedLabel`로 맞춘다. 원본 필드명이 엔진에 새어 나가면 경계가 무너진다.
- `adapters/arbiter_bag_category_gender.py`는 `definitions.md`의 근거 우선순위를 코드로 구현한다.
  **정책이 바뀌면 여기도 같이 바뀌어야 한다.** 새 판단을 어댑터에서 만들지 않는다.
- 가져오기 어댑터는 `common/tile_rule.py`에 기댄다. 갤러리 줄마다 **어느 타일 규칙 판·디코더로 번호를 매겼는지**
  (`tileRule`)를 찍는데, 그 판은 실행 결과 폴더의 날짜로 `tile_rule.rule_for_date`가 고른다. 장면을 되짚는
  `review/`가 같은 선언으로 잘라야 `D01T03`이 같은 조각을 가리키기 때문이다. `common`이 바뀌면 이 찍음도 바뀐다.
- `policy/policy.md`는 이전 문서 위치를 가리키는 안내문이다. 과거 본문은 `runs/bag-category-gender/policy-migrations/20260929/legacy-policy.md`에 보존했다.
- `runs/bag-category-gender/policy/`에 있는 것은 외부에서 가져온 읽기 전용 스냅샷이다. 구현 차이를 확인할 때 사용한다.

## 실행

```bash
.claude/os/attributes/bag-category-gender/run.sh
```

`run.sh`는 두 패키지를 잇는 자리다 — 엔진 사이클을 돌리고, 끝나면 `review`에 심사를 넘긴다.
넘기는 것은 프로필이 아니라 **산출물 폴더 하나**다. 엔진은 심사를 모르고 심사는 속성을 모르므로,
둘을 아는 유일한 곳이 이 파일이다.

## 검증

```bash
python3 -m pytest .claude/os/attributes/bag-category-gender/tests -q
```
