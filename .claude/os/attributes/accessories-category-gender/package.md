# accessories-category-gender

잡화(표준 카테고리 대분류 `잡화`) 상품의 대상 고객 성별 골든셋만 아는 속성 팩.
가져오기와 **GT 개선 과제(`gtTask`)**가 있다 — 정책·감사·심판 어댑터는 없다. 이 폴더를 지우면
잡화 스냅샷이 사라지고, 엔진과 다른 속성은 그대로 돈다.

## 소유

| 종류 | 파일 |
|---|---|
| 선언 | `profile.json` — 라벨·가져오기 어댑터 경로·`gtTask`. 정책 블록과 신호 정의는 아직 없다 |
| 정의 | `definitions.md` — GT 개선 판독자·반론자가 읽는 기준. 가방 정책의 근거 순위를 옮긴 출발점이다 |
| 어댑터 | `adapters/import_accessories_category_gender_sources.py` |
| 테스트 | `tests/test_import_paths.py` — GT 경로가 프로필 한 곳에서 오고, GT 사본을 쓰지 않는다 |
| 산출물 | `.claude/os/runs/accessories-category-gender/` (재생성 가능, 소유가 아니라 출력) |

## 왜 이미지 바이트까지 복사하는가

`bag-category-gender`의 가져오기는 이미지를 복사하지 않는다. 가방 감사는 정책 문장과
GT 라벨, 근거 장면 ID만으로 돌기 때문이다. 잡화는 다르다 — 판정 근거가 상세 타일 이미지
자체에 있어서, 원본 저장소가 `work/`를 비우면 왜 그렇게 판정했는지 다시 볼 수 없다.
그래서 여기서는 참조된 파일을 실제로 가져온다.

이미지 바이트는 `runs/accessories-category-gender/asset/`에 들어가고, 크기가 커서(얼마인지는 `manifest.json`) git이
추적하지 않는다(`.gitignore`). 텍스트 색인·스냅샷인 `golden/`과 무거운 바이너리인 `asset/`을 나눈
이유가 이것이다 — 추적하는 것과 추적하지 않는 것이 한 폴더에 섞이지 않는다.

대신 **어떤 파일이 어느 상품의 몇 번째 근거였는지**는 `golden/accessories-image-index.jsonl`이
추적한다. 바이트는 재현물이고, 색인이 기록이다.

색인에는 **asset으로 실제 들어온 파일만** 적는다. 원본에 파일이 없어 가져오지 못한 참조는
색인에서 빠지고 `manifest.json`의 `droppedImageReferences`에 남는다. 없는 근거를 있는 것처럼
세지 않으면서도, 무엇이 빠졌는지는 보이게 하기 위해서다.

## 실행

```bash
python3 .claude/os/attributes/accessories-category-gender/adapters/import_accessories_category_gender_sources.py
```

같은 파일이 이미 같은 크기로 있으면 다시 복사하지 않는다. `--skip-asset`을 주면 이미지 색인과
실행 스냅샷만 갱신한다(GT는 사본을 두지 않고 `gtTask.gt`가 원본을 직접 읽는다 — 경로도 프로필에만 적는다). GT 판만 바뀌었으면
`--gt-only`가 manifest의 GT 항목(경로·해시·건수)만 다시 적는다 — 실행 결과 원본이 없어도 되고, 손으로 고치지 않는다. 건수·용량은 실행이 끝나며 찍히는 `manifest.json`을 본다.


## GT 출처의 등급

`gtTask.authority`가 출처 이름으로 등급을 가른다. `FINAL_…`(D/O 최종 검수)·`USER_…`(하네스 사용자 정정)·`…_MANUAL_REVIEW_…`
(사람이 직접 한 수동 검수)는 **사람 확정** — 화면이 «뒤집으려면 증거를 꼭 직접 보세요»를 붙인다. `…FALLBACK…`(검수 전 원래 라벨)은
**참고 등급**이다. 새 출처 이름이 생기면 여기와 프로필에 함께 더한다 — 안 더하면 준비 경고 `sourcesUnclassified`가 알린다.

## GT 개선 — 원본을 직접 가리킨다

`gtTask.gt`는 `runs/`의 스냅샷이 아니라 외부 레포의 원본(`root: source`)을 가리킨다 — 어느 판인지는 프로필의
`gtTask.gt.path` 한 곳에만 적는다. 새 판이 옛 판을 바탕으로 삼았다고 밝히면(`supersededBy`) 옛 판을 가리킬 때 로더가 멈춘다. 그리고 이 파일은 D/O 검수 시트에서
다시 만들어지는 파생물이라 `gt.upstream`으로 그 사실을 밝힌다 — 원본에 직접 넣지 않고 시트에 붙여 넣을 목록을 낸다. `runs/`는 지워도 되는
산출물이라 정답의 원본이 될 수 없고, 로더가 거절한다. 사람 판정은 `.claude/gt/accessories-category-gender/gt-review/`에
남는다. 원본에 넣는 일은 이 파일이 아니라 D/O 검수 시트에서 한다 — `export`가 낸 `upstream-patch.csv`(시트·행·칸 포함)를 시트에 옮기고, 개발자가 시트 새로 고침을 돌린다. 시트는 빈칸을 «이전 값 유지»로 읽으므로 «비워야 한다»는 이 과제에서 쓰지 않고, 맞는 값이 없으면 `UNDETERMINED`를 고른다.

사진은 `runs/.../asset/`의 복사본을 쓴다(재현물). 상세 조각은 운영 하네스가 2026-08-31 평가에서 **이미 잘라 둔**
것이라 여기서 다시 자르지 않는다. 그때의 규칙은 고정 절단(`v0-fixed`)이었고 프로필의 `preTiledRule`이 그렇게
밝힌다 — 같은 `D02T03`이 현재 운영의 `D02T03`과 같은 사진이 아닐 수 있다는 사실을 화면이 함께 적는다.
