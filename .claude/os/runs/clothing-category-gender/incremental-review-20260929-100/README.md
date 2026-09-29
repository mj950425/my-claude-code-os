# 운영 증분 의류 100개 정책 검수

## 최종 결과

100개 모두 유효한 응답으로 완료했다. 최초 판독 일치 87개, 재판독 후 일치 4개, 최종 불일치 9개다. 스크립트가 불일치 13개를 독립 검수로 넘겼고, 그중 판독 오류 의견이 나온 5개를 한 번씩 재판독했다. 최종 불일치의 검수 의견은 정책 경계 3개, 운영 값 재확인 후보 5개, 재판독 뒤에도 의견이 해결되지 않은 상품 1개다.

`review/summary.json`에 집계, `review/mismatches.csv`에 불일치 9개와 검수 의견, `review/review.html`에 이미지와 전체 근거를 저장했다. **91개 일치는 운영 예측과의 일치이며 정답률 91%라는 뜻이 아니다.**

실행 오류 6개는 동일 입력으로 복구했다. 5개는 SDK의 1 MiB 응답 버퍼를 초과해 32 MiB로 조정 후 재실행했고, 1개는 하위 판독 완료 알림 뒤 최종 응답이 진행되지 않아 해당 CLI만 종료하고 새 세션으로 재실행했다. 초기 결과와 복구 결과는 별도 파일로 남겼으며 `merge_retries.py`가 정책·모델·입력 해시를 확인한 뒤 합쳤다. 최종 실행 오류는 0개다.

성공한 SDK Read 기록에서 100개 상품의 제공 이미지 1,678개 모두를 확인했다. macOS 경로 별칭(`/var`, `/private/var`) 때문에 초기 집계가 0으로 나오던 기록은 같은 원본 실행 기록에서 다시 계산했고, 수정 전 집계도 보존했다. 원본 다운로드 실패 7개 상품·11개 이미지는 이 수치와 별개로 여전히 누락 상태다.

## 대상 선정

운영 MongoDB `gender_target`에서 아래 조건을 만족하는 최근 100개를 `ut` 내림차순, `seller_product_id` 내림차순으로 고정했다.

```json
{"type":"GENDER","inference_origin":"INCREMENTAL","category_depth1":"의류","status":"EVENT_PUBLISHED","dt":null}
```

- 플랫폼: MUSINSA 100개.
- 완료 시각 범위(UTC): 2026-09-28 08:46:00.484 ~ 14:35:28.576.
- 대상 버전과 `seller_product_gender.gender_target_version`이 같은 상품만 비교했다. 고정된 100개 모두 일치했다.
- `seller_product_id`는 내부 상품 키, `platform_product_id`는 무신사 상품 번호다. 보고서의 `MUSINSA:...`는 후자다.
- 운영 값 분포: 남성 80, 공용 15, 여성 4, 판단 불가 1. 최근 완료 표본이며 무작위·균형 표본이 아니다.

## 실행과 해석

Python Claude Agent SDK를 이용해 오케스트레이터가 판독을 위임한다. 최초 판독에는 운영 예측값을 전달하지 않는다. 스크립트가 응답 형식과 참조를 검사하고 운영 예측값과 비교한다. 불일치일 때 별도 검수 세션을 실행하며, 판독 오류로 판단한 경우에만 한 번 재판독한다. 정책 경계나 운영 값 오류 의심은 검토 대상으로 남긴다.

네 역할의 지정 모델은 `claude-opus-5-5`다. SDK 내부 보조 호출은 원본 `modelUsage`에 별도 기록된다. 구독 인증으로 실행했다.

운영 값은 **비교용 예측**이다. 일치율을 정답률로 해석하거나, 에이전트 의견을 사람이 확정한 판례로 취급하지 않는다. 운영 DB와 정답 원장에 쓰지 않았다.

## 이미지 범위

현재 카탈로그 이미지와 상세 설명을 내려받고 Pillow로 공통 타일 규칙을 적용했다. 총 1,678개 이미지/타일을 준비했다. 과거 운영 추론 당시와 파일 내용까지 동일한 재생 실험은 아니다.

7개 상품에서 11개 원본 이미지 다운로드가 실패했다. 실패 원인과 URL은 `manifest.json`의 각 상품 `metadata.imageFailures`에 보존했다. 누락 상품을 다른 상품으로 교체하지 않았고, 판독 입력과 보고서에 누락을 표시했다.

## 파일

- `targets.json`: 고정한 target 목록과 조회 조건.
- `seller_product_gender.json`, `seller_product_images.json`: 비교값과 이미지 원장 스냅샷.
- `products.json`, `descriptions.json`, 대응 SQL: 현재 상품 정보와 상세 HTML의 읽기 전용 조회 결과.
- `manifest.json`: 고정한 100개, 이미지 경로, 버전, 운영 비교값, 누락 기록.
- `policy-snapshot/`: 이번 실행 정책 문서와 프로필 사본.
- `prepare_cases.py`: 저장된 조회 결과에서 입력과 이미지/타일을 준비한 코드.
- `review/results.json`: 상품별 최초 판독·독립 검수·재판독 및 SDK 실행 기록. 진행 중에는 완료된 상품부터 저장된다.
- `review/review.html`: 상품 이미지와 근거, 비교 결과. 별도 화면 생성만으로 갱신할 수 있다.
- `progress.html`: 진행 상황을 10초마다 읽는 화면.
- `audit_image_reads.py`, `review/image-read-audit.json`: 성공한 SDK 이미지 읽기 이벤트를 집계한다. 읽기 기록은 판단의 정확성을 보장하지 않는다.

## 재개

```sh
rtk proxy /tmp/policy-claude-sdk-venv/bin/python -u \
  .claude/os/engine/scripts/policy_batch_review.py \
  --manifest .claude/os/runs/clothing-category-gender/incremental-review-20260929-100/manifest.json \
  --output-dir .claude/os/runs/clothing-category-gender/incremental-review-20260929-100/review \
  --concurrency 4 --case-timeout 900
```

동일한 입력·정책 해시의 완료 건은 건너뛰고 실행 오류만 재개한다. 진행 중인 프로세스와 동시에 같은 출력 폴더에 재개하지 않는다. 정책이나 입력이 달라졌으면 새 실행 폴더를 사용한다.
