# clothing-thumbnail-observation

의류 썸네일 **한 장**에 붙는 관찰 칸(`profile.json`의 `gtTask.fields`)의 GT만 아는 속성 팩. 성별이 아니라 메타데이터다 —
사람이 있는가 · 몸이 어디까지 보이나 · 얼굴이 보이나 · 상품이 어느 쪽을 보이나 · 상품이 통째로 보이나.

감사 사이클(정책·판례·심판)이 없다. **GT 개선 과제(`gtTask`)로만 선다.** 이 팩이 있다는 것이
하네스가 상품 성별에 묶이지 않았다는 증거다. 이 팩 때문에 엔진이 넓힌 것은 과제를 모르는 일반 선언뿐이다
(맥락 열 `contextFields` · 열 이름 `columnNames` · 한 줄에 사진 한 장인 색인). 이 팩의 어휘는
엔진에 없다 — `test_package_boundary`가 프로필과 정책(`### 허용값`)에서 어휘를 모아 확인한다.

## 소유

| 종류 | 파일 |
|---|---|
| 선언 | `profile.json` — `gtTask` 하나. 필드·옛 값 대응·제약·사진 색인·판독 맥락. 값 목록은 없다 |
| 정책 | `definitions.md` — 허용값과 이름(필드마다 `### 허용값`, 정본)·판독자와 반론자가 읽는 유일한 기준. 운영 프롬프트 v106과 코드 열거형에서 옮겼다 |

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
