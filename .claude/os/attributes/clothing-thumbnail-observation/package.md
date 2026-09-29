# clothing-thumbnail-observation

의류 썸네일 **한 장**에 붙는 관찰 칸(`profile.json`의 `gtTask.fields`)을 GT로 개선하는 속성 팩. 이 관찰값은 성별이 아니라 메타데이터다 —
사람이 있는가 · 몸이 어디까지 보이나 · 얼굴이 보이나 · 상품이 어느 쪽을 보이나 · 상품이 통째로 보이나.

별도 정책 감사·판례 승인·심판 사이클은 없다. 정책 정의(`policyTask`)는 공통 렌더러에 제공하고, **운영 범위는 GT 개선 과제(`gtTask`)다.** 이 팩이 있다는 것이
하네스가 상품 성별에 묶이지 않았다는 증거다. 이 팩 때문에 엔진이 넓힌 것은 과제를 모르는 일반 선언뿐이다
(맥락 열 `contextFields` · 열 이름 `columnNames` · 한 줄에 사진 한 장인 색인). 이 팩의 어휘는
엔진에 고정되지 않는다 — `test_package_boundary`가 프로필과 정책의 필드별 `### 허용값`에서 어휘를 모아 확인한다.

## 소유

| 종류 | 파일 |
|---|---|
| 선언 | `profile.json` — `policyTask`(정의·다섯 필드의 값 코드)와 `gtTask`(필드·옛 값 대응·제약·사진 색인·판독 맥락). 값 이름 목록은 정의 문서가 소유한다 |
| 증분 | `profile.json`의 `incremental` — 운영에서 증분을 모으는 법(의류 표준 카테고리 · 무신사 · 키 «상품번호-01» · 대표 썸네일만). 모으는 코드는 엔진 `incr_collect.py`다 |
| 정책 | `definitions.md` — 허용값과 이름(필드마다 `### 허용값`, 정본)·판독자와 반론자가 읽는 유일한 기준. 운영 프롬프트 v106과 코드 열거형에서 옮겼다 |

## 정책 전달과 운영 프롬프트

SDK/엔진은 다섯 필드 모두 `policyTask.definitions`를 `policy_prompt.render_agent_policy`가 매 실행 읽어 전달한다. 이 프로필에 `promptDelivery`가 없는 것은 SDK 정책 전달이 끊긴 상태라는 뜻이 아니다. `promptDelivery`는 외부 운영 프롬프트와 Java 어댑터의 해시/버전을 자동 동기화하려는 경우에만 선언한다. v106은 정책을 옮겨 온 출처이며, 이 프로필은 그 외부 파일과 현재 동기화 연결을 선언하지 않는다.

## 판례와 열린 질문

정의 문서의 `판례: GTD-…`는 사람 판정 원장의 확정 결정 ID다. 아래 링크 표에서 해당 ID의 원장 줄을 바로 열 수 있다. 확인한 결정은 모두 `productVisibility` 필드의 `WHOLE`이며 대체되지 않은 결정이다. 규칙 정의에는 짧은 ID만 둔다. 공통 SDK 프롬프트에 파일 경로를 반복 출력하지 않기 위해서다. `R5`가 좁혀 대체한 이전 `R2`는 [정책 이력의 보관 기록](../../runs/clothing-thumbnail-observation/policy-history/20260928T141030671594.md#L242-L252)에서 확인할 수 있다. `R6`의 `판례: 없음`은 일반 경계 원칙에 별도 승인 사례를 연결하지 않았다는 뜻이다. 새 질문이 생기면 `OPEN`으로 남기고, 답을 확정한 사람 결정만 규칙의 사례로 연결한다.

| 규칙 | 결정 ID → 원장 줄 |
|---|---|
| R2 | [GTD-00228](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L5227) · [GTD-00233](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L5342) · [GTD-00243](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L5572) · [GTD-00295](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L6768) · [GTD-00386](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L8873) · [GTD-00396](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L9131) · [GTD-00401](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L9262) · [GTD-00403](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L9316) · [GTD-00404](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L9343) · [GTD-00408](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L9443) · [GTD-00412](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L9547) |
| R3 | [GTD-00309](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L7090) · [GTD-00311](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L7136) · [GTD-00312](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L7159) |
| R4 | [GTD-00102](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L2329) · [GTD-00112](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L2559) · [GTD-00135](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L3088) · [GTD-00314](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L7205) |
| R5 | [GTD-00091](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L2076) · [GTD-00104](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L2375) · [GTD-00127](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L2904) · [GTD-00153](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L3502) · [GTD-00173](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L3962) · [GTD-00193](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L4422) · [GTD-00198](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L4537) · [GTD-00380](../../../gt/clothing-thumbnail-observation/gt-review/decisions.json#L8727) |

## 데이터는 외부 레포에 있다

썸네일 파일은 평가 하네스가 EXIF 회전을 적용해 저장한 것이라, 보기용 디코드가 회전을 따라도 모델이 본 방향과 같다.
원본 CDN 파일을 직접 가리키는 색인으로 바꾸면 그 전제가 깨진다(운영 썸네일 경로는 돌리지 않는다).

GT 원본은 이 레포의 `.claude/gt/clothing-thumbnail-observation/gt.jsonl`이다 — GitHub에서 관리한다. 판정은 같은 폴더의
`gt-review/`에 남고, «넣어줘»가 원본에 넣은 뒤 «올려줘»가 둘을 한 PR로 올린다. 이 파일은
`core-catalog-platform`의 `tool/image-gender/gt-harness/data/thumbnail-shot-face-gt-20260923.jsonl`에서 옮겨 왔다 —
그 레포의 평가 하네스가 GT를 읽는다면 이제 이 레포의 파일을 가리켜야 한다(그쪽 사본은 더 고쳐지지 않는다).
사진 색인과 사진은 그대로 `../core-catalog-platfom/tool/image-gender/gt-harness/`에 있다(`root: source`).

## 판독자 입력이 운영과 다른 곳 — 판독 맥락

운영 모델은 상품명·카테고리를 받지 않는다. 그런데 «상품이 통째로 보이나»는 **어느 옷이 파는 상품인가**를
알아야 답할 수 있다 — 후드와 쇼츠를 같이 입은 컷에서 첫 시험 판독이 실제로 멈췄다. GT는 운영 모델의
입력 조건을 흉내 내는 것이 아니라 정답이므로, 판독자에게 표준 카테고리를 맥락으로 준다(`images.contextFields`).
GT 열은 맥락으로 선언돼도 로더가 막는다.

## 돌리기

운영팀은 «썸네일 관찰 GT 개선해줘»라고만 요청한다. 개발자가 확인할 때는:

```bash
python3 .claude/os/engine/scripts/gt_review.py prepare --task clothing-thumbnail-observation --limit 1
```
