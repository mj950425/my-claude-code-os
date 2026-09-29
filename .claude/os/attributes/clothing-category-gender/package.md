# clothing-category-gender

의류(표준 카테고리 대분류 `의류`) 상품의 대상 고객 성별만 아는 속성 팩.
정책 정의(`policyTask`), **GT 개선 과제(`gtTask`)**, **증분 검수의 운영 수집(`incremental`)**, 로컬 GT 사진 가져오기 어댑터를 함께 선언한다. 별도 정책 감사 사이클과 심판 어댑터는 없다.
이 폴더를 지우면 메뉴에서 이 과제가 사라지고, 엔진과 다른 속성은 그대로 돈다.

## 소유

| 종류 | 파일 |
|---|---|
| 선언 | `profile.json` — `policyTask`(정의·필드별 응답 코드·외부 동기화 설정), `gtTask`(필드·GT 원본·등급·사진 색인 설정), `incremental`(새 상품 수집). 사람이 읽는 값 이름/뜻은 정책 문서에 둔다 |
| 정책 | `definitions.md` — 목적·허용값(`## 허용값`)·판독 기준의 정본. 운영 의류 성별 프롬프트 v1000 `clothing.txt`에서 옮겨 왔다. 문서 머리말에 원문 해시를 보관한다고 가정하지 않는다 |
| 산출물 | `.claude/os/runs/clothing-category-gender/` (재생성 가능, 소유가 아니라 출력) |

## 정책 전달과 운영 프롬프트

SDK/엔진은 `policyTask.definitions`를 `policy_prompt.render_agent_policy`가 매 실행 읽어 목적·규칙·허용값을 구성한다. 이 정책 전달은 모든 task의 공통 경로다. 이 프로필의 `policyTask.promptDelivery`는 별도 기능으로, 외부 운영 프롬프트 `clothing.txt`와 Java 어댑터의 해시/버전 선언을 정책 변경과 함께 동기화하도록 연결한다. `promptDelivery`가 없어도 SDK가 정책을 읽는 일은 계속 가능하다.

규칙의 `판례: 없음`은 그 규칙에 명시적으로 연결한 선례 ID가 없다는 뜻이다. GT 개선 이력이 있는 task는 SDK가 같은 필드의 사람 `CORRECT`/`CONFIRM` 질문 답을 별도 후보로 검색할 수 있지만, 규칙 ID가 명시되지 않은 후보는 일반 사례로 남는다. 미결 질문은 확정 답과 구분하며, 답이 정해지기 전에 사례 ID를 만들어 정책에 연결하지 않는다.

## 골든셋 사진 준비

골든셋 검수는 GT와 같은 `productKey`를 가진 원본 사진이 필요하다. `profile.json`은 사진 색인의 `keyField`와 GT 결합 키 `joinField`를 모두 `productKey`로 지정한다. MUSINSA와 EGOOCM에서 같은 상품 번호가 나와도 플랫폼 접두사가 포함된 GT 키로 결합하므로 서로 다른 상품의 사진을 섞지 않는다.

가져오기 어댑터는 있지만, **현재는 입력 원본 색인이 없어 골든셋 검수가 준비되지 않았다.** 예상 산출물 `runs/clothing-category-gender/golden/clothing-image-index.jsonl`은 아직 없고, 이 checkout에서 GT 상품에 연결할 로컬 이미지 `--source-index`도 찾지 못했다. 라벨 파일 `clothing-product-gt-20260903.jsonl`이 있다는 사실만으로 사진 근거가 준비된 것은 아니다.

원본 사진을 확보한 뒤 JSONL의 한 줄마다 GT에 있는 `productKey`와 이미지 배열을 적는다. `file`이 상대 경로면 색인 파일이 있는 폴더를 기준으로 찾는다. `role`은 프로필이 허용한 `THUMBNAIL`, `THUMBNAIL_EXTRA`, `DETAIL_TILE` 중 하나여야 한다.

```json
{"productKey":"<GT의 플랫폼 접두 상품 키>","images":[{"file":"images/example.jpg","imageId":"THUMBNAIL-001","role":"THUMBNAIL"}]}
```

그 다음 로컬에서 가져오기를 실행한다.

```bash
python3 .claude/os/attributes/clothing-category-gender/adapters/import_clothing_images.py \
  --source-index <로컬 JSONL 색인 경로>
```

어댑터는 입력 JSONL과 로컬 파일만 읽어 실제 GT 키에 연결되는 파일을 `runs/.../asset/`에 복사하고 사진 색인/manifest를 쓴다. GT 라벨 원본은 수정하지 않고 네트워크/API/DB도 사용하지 않는다. 없는 파일과 사진이 빠진 GT 키는 manifest에 기록한다. GT와 맞는 파일이 한 장도 없으면 실패하며 기존 사진 색인은 보존한다.

`.claude/os/runs/clothing-category-gender/incremental-review-20260929-100/`의 100개 사진은 운영 증분 검수의 별도 상품 모집단이다. 그 이미지는 위 GT 키와 매칭됐다고 확인된 자료가 아니므로 골든셋 사진색인에 대입하거나 빠진 GT 사진을 대신하지 않는다. 운영 증분 검수 결과도 골든셋 GT 검수의 준비를 증명하지 않는다.
