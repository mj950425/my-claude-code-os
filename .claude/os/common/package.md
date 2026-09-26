# common

어느 패키지도 모르는 순수 함수만 둔다. 엔진과 심사가 **같은 계산**을 해야 하지만 서로를 import하면
안 될 때 여기로 온다.

| 파일 | 한 문장 |
|---|---|
| `tile_rule.py` | 긴 상세 이미지를 타일로 가르는 규칙. core-catalog-platform `BatchImageComposer.tileRanges`의 이식 |
| `tests/test_tile_rule.py` | 운영 레포의 Java·파이썬 테스트와 같은 픽스처·같은 기대값 |

쓰는 곳: 엔진(GT 개선 사진 준비 `gt_images.py` · 프로필의 판·디코더 이름 검증 `gt_task.py` · 보고서의 조각 표시), 심사(`fetch_review_scenes.py`), 그리고 가방 속성의 가져오기
어댑터(갤러리 줄마다 번호를 매긴 판·디코더를 찍는다).

## 왜 따로 두는가

타일 번호(`D01T03`)는 운영 모델이 본 조각의 이름이다. 엔진(GT 개선 하네스의 사진 준비)도, 심사(판독 근거
재확인)도 그 이름을 **같은 픽셀**로 되돌려야 한다. 한쪽에 두면 다른 쪽이 그 패키지를 import해야 하는데,
심사는 엔진을 import하지 않는다(엔진의 오해가 심사로 번지지 않게). 그래서 둘 다 아는 자리가 아니라
**둘 다 모르는 자리**에 둔다.

## 규칙

- 저장소의 어느 패키지도 import하지 않는다. 모듈 머리의 import는 표준 라이브러리뿐이고, Pillow는
  사진을 여는 함수(`decode`·`decode_for_display`·`_exif_only_transpose`) 안에서만 늦게 들여온다(`test_common_imports_nothing_from_the_repo`가 확인한다).
- 운영 규칙이 바뀌기 전에 숫자를 바꾸지 않는다. 바꾸면 이 레포의 `D01T03`과 운영의 `D01T03`이 다른 사진이 된다.
- 규칙 이름(`RULE`)을 산출물에 함께 적는다. 규칙이 바뀌면 이름도 바꿔 옛 타일과 섞이지 않게 한다.

```bash
python3 -m pytest .claude/os/common/tests -q
```

## 판과 디코더

타일 번호는 **그 번호를 매긴 실행의 규칙**으로 되짚어야 같은 사진이다. 그래서 `tile_rule`은 판(`v0-fixed`·
`v1-background-band`·`v2-band-then-seam`)과 디코더(`java-imageio` 운영 서버 · `harness-pillow` 평가 하네스)를 함께 받는다.
판은 산출물이 직접 선언하는 것이 먼저고, 날짜로 정하는 `rule_for_date`는 대체 수단이다(판이 바뀐 날은 거절한다).
