# 골든셋 원장 계약

정답은 **한 곳에만 있다** — `.claude/gt/<프로필ID>/gt.jsonl`. 상품 하나에 라벨 하나다.

| 구분 | 위치 | 누가 쓰는가 | 재생성 |
|---|---|---|---|
| **GT 원장** | `.claude/gt/<프로필ID>/gt.jsonl` | `build_gt.py`가 계보를 합쳐 쓴다 | 됨. 다만 커밋한다 |
| **계보 색인** | `.claude/gt/<프로필ID>/lineage.json` | 같음 | 됨 |
| **가져온 스냅샷** | `runs/<프로필ID>/golden/` | import 어댑터 | 매 사이클 덮어씀 |

## 왜 합치는가

정답이 두 파일에 있으면 「지금 정답이 무엇인가」에 답이 둘이다. 그러면 화면과 큐가
서로 다른 쪽을 집어도 아무도 모른다. 실제로 그랬다 — GT 정정 조서가 한 상품의
`MALE → UNISEX` 정정을 「GT 유지」로 그렸다. 조서는 낮은 순위 계보의 라벨을 현재 GT로
집었고, 심판은 높은 순위 계보를 봤기 때문이다. 카드가 자기 자신과 모순이었다.

합치는 자리를 하나 만들면 그 사고가 구조적으로 안 난다. 아래 규칙 8과 같은 이유다 —
**두 번 세면 조용히 어긋난다.**

## 합치는 규칙

`engine/scripts/build_gt.py`가 한다. 속성을 모른다 — 계보의 위치와 순위는 인자로 받는다.

1. **순위가 낮은 숫자가 이긴다.** 프로필 `gt.lineages[].rank`가 정한다.
2. **진 계보는 지워지지 않는다.** `otherLineages[]`에 라벨과 출처가 남는다.
   왜 이 라벨이 이겼는지 나중에 되짚어야 하기 때문이다.
3. **사람 정정이 마지막에 얹힌다.** `corrections`는 순위와 무관하게 이긴다.
4. 진 계보가 다른 확정 라벨을 갖고 있었으면 `conflict: true`, `resolvedBy`에 근거를 적는다.

## 행 계약

| 필드 | 필수 | 뜻 |
|---|---|---|
| `productKey` | 예 | 상품 식별자. 계보 사이의 조인 키 |
| `goldLabel` | 예 | **이 상품의 정답 하나** |
| `goldSource` | 예 | 그 라벨을 적은 검수 이름 |
| `goldLineage` | 예 | 이긴 계보 id, 또는 `corrections` |
| `otherLineages[]` | 예 | 진 계보가 적었던 `{lineage, goldLabel, goldSource}` |
| `conflict` | 예 | 진 계보가 **다른** 확정 라벨을 갖고 있었나 |
| `resolvedBy` | 예 | `LINEAGE_RANK` · `CORRECTION` |

## 읽는 쪽의 의무

- **`goldLabel` 말고 다른 필드를 정답으로 쓰지 않는다.** `otherLineages`는 이력이지 후보가 아니다.
- 큐 행의 `referenceLabel`·`observedLabel`은 **모든 큐에서 같은 뜻이어야 한다** — GT와 실행이다.
  여기에 두 GT 계보를 담았던 것이 위의 사고였다.
- 계보가 갈렸다는 사실 자체는 `conflict` 플래그로만 읽는다. 파일 둘을 다시 비교하지 않는다.

## 새 속성에 붙이기

1. 프로필에 `gt` 블록(`path`, `lineageIndex`, `lineages[]`, `corrections`)을 넣는다.
2. `build_gt.py`를 계보 수만큼 `--lineage`로 돌려 원장을 만든다.
3. import 어댑터가 그 원장 하나만 읽게 한다.

`gt` 블록이 없는 프로필은 이 단계를 건너뛴다. 계보가 하나뿐이면 합칠 것이 없다.
