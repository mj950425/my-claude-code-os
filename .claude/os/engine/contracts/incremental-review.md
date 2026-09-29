# 증분 검수 계약

새로 들어온 데이터(아직 라벨이 없는 건)를 **AI가 먼저 읽고, 데이터 운영팀은 클릭으로 확정**한다. 진입점은 `scripts/incr_review.py`,
화면은 `/incr`(서버가 `templates/incr.html`을 그대로 보낸다), 스킬은 `incr-review`.

**화면은 골든셋 검수와 한 벌이다.** `templates/incr.html`은 `scripts/incr_render.py`가 골든셋 검수를 그리는 스타일 상수
(`page_style.head`·`EXTRA_STYLE`·`THEME_STYLE`·`SIDEBAR_STYLE`, 목록 표는 `PAGE_STYLE`)와 같은 마크업 클래스로 **생성한 파일**이다 —
손으로 고치지 않는다. 골든셋 쪽 모양이 바뀌면 테스트가 «파일 ≠ 생성기 출력»으로 멈추고, `python3 incr_render.py`로 다시 쓴다.
사이드바도 같은 코드(`sidebar_html`·`SIDEBAR_SCRIPT`)라 «증분 검수»에 마우스를 올리면 골든셋 검수처럼 과제 목록(과제마다 남은 건)이 펼쳐진다. 과제 이름은 두 메뉴가 같은 이름이고(`/incr-tasks`의 `menuName`), 부모 줄의 수는 자식 수의 합이다. 과제가 없는 증분 화면에서 «골든셋 검수»·«정책»·«골든셋»을 누르면 첫 화면처럼 지금(또는 첫) 과제의 화면으로 간다 — 펼침이 없는 터치 화면에서도 누르면 어딘가에 닿는다.

## 골든셋 검수와 나눠 가진 것 — 선언·정책·판독자·잠금

| | 골든셋 검수(`gtTask`) | 증분 검수 |
|---|---|---|
| 과제 선언 | 프로필 `gtTask` | **같은 블록**. 따로 선언하지 않는다 |
| 정책 | 정의 문서(허용값·규칙·검수 문답) | **같은 문서**. 화면 버튼도 그 허용값이다 |
| 판독자 | `gt-blind-reader`(워크플로우 `gt-review.js` `read`) | **같은 판독자·같은 판독 파일 모양**(`gt_review.emit_workflow_args`) |
| 반론 | `gt-defender` | 없다 — 지킬 GT가 없다 |
| 후보 | 신호(모순·범위 밖·참고 등급…)로 고른 칸 | 밀어넣은 줄의 **모든 칸** |
| 원장 | `.claude/gt/<id>/gt-review/decisions.json` | `.claude/incr/<id>/decisions.jsonl` |
| 결과 | 정정 → 원본 GT | 라벨을 채운 줄 → `.claude/incr/<id>/labeled/<묶음>.jsonl` |
| 잠금 | `runs/.gt-next/lock` | **같은 잠금** — 워크플로우는 한 번에 하나 |

왜 선언을 나눠 갖나 — 증분의 AI 판독과 골든셋의 AI 판독이 다른 기준을 따르면, 골든셋 검수에서 사람이 고친 정책이 증분에는 닿지 않는다.
선언·정의 문서가 하나면 «정책에 넣어줘» 한 번이 두 문에 다 간다.

왜 원장을 나누나 — 증분의 답은 GT를 고치는 판정이 아니다. 한 원장에 섞으면 «이 GT 칸을 사람이 확인했다»와 «새 건에 라벨을 붙였다»가
구별되지 않는다(규칙 5 «한 GT에 원장은 하나»도 그대로 지킨다). `runs/`가 아닌 `.claude/incr/`에 두는 까닭은 GT 원장과 같다 — 지워도 되는 자리에 사람의 답을 두지 않는다.

## 새 데이터 모으기 — 운영에서

과제마다 운영에서 무엇을 어떻게 모으는지는 프로필의 `incremental` 블록이 말한다(플랫폼 · 표준 카테고리 앞머리 · 키 틀 · 사진 주소 ·
상세 사진이 필요한가). 코드는 엔진의 `incr_collect.py` 하나다 — 과제를 모른다. 순서는 셋이다.

1. `incr_collect.py sql --task <id>` → 읽기 전용 `mysql-query` 스킬로 돌린다(`--json`). 최근에 표준 카테고리가 정해진 상품만, 플랫폼마다 묶어서.
   `productStatuses`가 있으면 그 상태의 상품만(`seller_product.platform_product_status` — 예: `ONSALE`). 판매 전(`PENDING` 등) 상품은 무신사·29CM
   상품 페이지가 열리지 않아 사람이 확인할 수 없다. 링크의 번호는 `seller_product.platform_product_id`(상품 번호)이고 `spid`(`seller_product.id`)는 조회에만 쓴다.
2. (상세 사진이 필요한 과제) `incr_collect.py detail-sql --task <id> --rows <1의 결과>` → 같은 스킬로. **고른 건의** CUVE 상세 설명만 —
   후보 전체에 설명(HTML)을 붙이면 조회가 도구의 제한 시간(30초)을 넘는다. 고르는 규칙이 같아 두 조회가 같은 건을 보되, 고를 수의 `--spare`배(기본 3)를
   넉넉히 읽는다 — 3이 상세 사진 있는 건을 먼저 고르기 때문이다. 제한 시간을 넘으면 플랫폼마다 따로 돌린다.
2′. (`ledgerFallbackPlatforms`나 `thumbnailSource: "ledger"`가 있는 과제) `incr_collect.py detail-mongo --task <id> --rows <1의 결과>` → 읽기 전용 `mongo-query` 스킬의 `aggregate`로.
   **썸네일**(`thumbnailSource: "ledger"`)은 운영처럼 Mongo `seller_product_images`의 `THUMBNAIL` **전부**다(`MongoGenderImageInventoryAdapter.allThumbnails` —
   지워지지 않은 것, 빈 주소 빼고, `position` 오름차순, 겹친 주소 한 번). 주소는 `UrlGeneratorUtil.getThumbnailImageUrl`과 같게 `hosts`(운영 썸네일 CDN)를 잇는다.
   첫 장이 대표(판정의 `TARGET_REFERENCE`), 둘째부터는 `extraThumbnailRole`(«추가 썸네일»)이다. 원장에 썸네일이 없으면 `seller_product.image` 한 장.
   사진 색인의 `alwaysKeepRoles`에 둔 역할(썸네일)은 판독자에게 주는 사진 상한(`maxImages`)에서 빼지 않고, 남은 자리만 조각에서 고르게 뽑는다.
   상세는:
   운영이 상세 사진을 고르는 자리(`SellerProductDetailImageSourceAdapter`)와 같게 — **상세 설명(CUVE) HTML의 사진이 먼저**이고, 거기 사진이 없을 때만
   프로필이 적은 플랫폼에 한해 Mongo `seller_product_images`의 `DETAIL`(지워지지 않은 것, `position` 순)로 채운다. 다른 플랫폼의 그 원장은 상세가 아니라
   갤러리 썸네일이라 쓰지 않는다. 그 뒤 대표 썸네일과 같은 사진·겹친 주소를 뺀다(`effectiveDetailUrls`). HTML 추출도 운영(`ProductContentsImageUrlExtractor`)과
   같다 — `data-src` → `srcset` 첫 주소 → `src`, 탭·줄바꿈 지움, «https:/x» 고침, 호스트 없는 상대 경로는 버린다.
3. `incr_collect.py build --task <id> --rows … [--details …] --name <이름> --push [--split K]` — GT·사진 색인·지난 묶음에 이미 있는 키와 시험 등록 상품(상품명의 «테스트 상품»·«구매금지» 등, `incr_review.NOT_FOR_SALE`)은 빼고 뺀 목록(`skippedNotForSale`)을 돌려주며 — 파일로 밀어넣을 때는 빼지 않고 `rowsLookLikeTests`로 알린다 —
   표준 카테고리 둘째 마디마다 돌아가며 고른다. 상세 사진이 필요한 과제는 **설명에 사진이 있는 건을 먼저** 고르고 모자랄 때만 나머지로 채운다 —
   가장 새 상품은 설명이 «상세정보 참고» 같은 글뿐인 경우가 많아, 그대로 고르면 대표 사진 한 장짜리 건(판단 근거도 화면도 빈약하다)이 된다. 그런 뒤 대표 사진(EXIF 회전 적용)과 상세 설명의 사진(운영 추출과 같은 순서: `data-src` → `srcset` →
   `src`)을 하네스와 같은 함수(`gt_images.fetch`)로 받고, 상세는 **운영이 지금 자르는 규칙**으로 `common/tile_rule`이 잘라 `DxxTyy`로 둔다 —
   `incremental.tileRule`(판·디코더), 없으면 운영 기본값(`tile_rule.CURRENT` · `java-imageio`). 골든셋 사진 색인의 `preTiledRule`은 그 색인의 번호를 매긴
   옛 실행(평가 하네스·옛 판)의 선언이라 새 상품에 쓰지 않는다 — 쓰면 운영과 다른 조각이 된다. 상세 사진 수는 `maxDetailImages`(없거나 0이면 전부 — 운영처럼).
   운영 디코드와 맞춰 보지 않은 파일(WebP·맞춰 보지 않은 ICC·CMYK)은 자르지 않고 `failed`에 이유와 함께 남긴다.
   입력 두 장은 과제의 사진 색인과 같은 모양이다(목록형이면 건마다 한 줄, 아니면 사진마다 한 줄). 받은 사진은 `runs/<id>/incr/_inbox/`(지워도 되는 자리).

엔진·팩에 운영 접속 정보를 두지 않는다 — 조회는 스킬이 하고, 무엇을 모을지는 사람이 그때 정한다.

## 흐름

1. **밀어넣기** `push --task <id> --file <jsonl> [--images <색인>]` — 한 줄 한 건, 키 열 필수. 키 없음·키 겹침·사진 색인 오류(없는 파일,
   목록 색인의 키 겹침, 사진을 선언하지 않은 과제에 `--images`)는 **쓰기 전에** 거절한다 — 반쯤 만든 묶음이 «가장 최근 묶음»이 되지 않게.
   원본은 `.claude/incr/<id>/batches/<묶음>/input.jsonl`로 그대로 복사한다(무엇을 검수했는지의 기록 — 지우지 않는다).
   사진이 과제의 사진 색인에 없으면 같은 모양의 색인을 `--images`로 함께 넣는다(묶음 폴더의 `images.jsonl`). 색인 안의 **상대** 파일 경로는
   색인 자리가 아니라 과제 선언의 `fileRoot`·`fileBase` 기준이다 — 다른 곳의 사진이면 절대 경로로 적는다.
   밀어넣은 줄에 라벨 열(또는 `<열>Source`)이 이미 차 있으면 `answerColumnsAlreadyFilled`로 알린다 — 결과 파일이 그 칸을 덮는다.
   끝나면 AI 추론 러너를 떼어 띄운다. 다른 AI 작업이 잠금을 쥐고 있으면 띄우지 않고 `aiStarted: false`로 말한다(`--no-run`이면 띄우지 않는다).
2. **AI 추론** `run` — 잠금을 쥐고, 사진을 준비하고(`runs/<id>/incr/<묶음>/images`), 판독자 파일을 쓰고(`reader/`, 무작위 이름·키 없음),
   워크플로우 `read` 단계만 헤드리스로 부르고(`gt_next._run_workflow`), 판독을 `readings.json`에 합친다.
   - `--reread` 없이: 사진을 새로 준비하고 **확정하지 않은 건의 지난 판독은 버리고** 다시 읽는다(정책·사진 색인이 바뀐 뒤, 또는 처음).
     사진을 다시 만들면 지난 판독의 근거 사진 번호가 다른 사진을 가리킬 수 있기 때문이다. 확정한 건의 판독은 남긴다.
   - `--reread`: 지금 사진을 두고, 판독이 없거나 칸이 빠진 건만(화면의 «다시 읽기»).
   - 준비마다 새 `runId`를 매기고 합칠 때 그 값이 맞는 결과만 받는다 — 지난 실행의 출력이 붙지 않게. 칸이 하나도 없는 판독은 받지 않는다.
   - 잠금을 못 얻은 러너는 묶음의 상태 파일에 «바쁨»을 남기고 3으로 끝난다(서버는 409로 답한다). 도는 동안 러너의 공용 상태에 «증분이 돈다»를 적어,
     골든셋 화면이 지난 실행의 문장을 보이지 않게 한다.
   밀어넣은 줄에 답 열이 섞여 와도 판독자에게 가지 않는다 — 사진 색인이 답 열을 막는 규칙(`ImageIndex.blocked`)이 같다.
   눈가림의 한계도 골든셋 검수와 같다(gt-task.md «배치와 눈가림»): 판독자 자기 입력의 실마리는 구조로 없앴지만, 같은 저장소의
   작업 목록(`worklist.json`)·원장에는 판독자의 Grep이 닿을 수 있다 — 그 선은 역할 파일의 금지와 읽기 전용 도구, 뒤따르는 사람 판정으로 지킨다.
3. **클릭 검수** `/incr?task=<id>&batch=<묶음>` — 칸마다 허용값 버튼, AI 제안은 파란 테두리, 건마다 «AI 제안 N칸 확정»(키보드 `A`, 다음 건 `J`).
   한 번에 확정하는 칸은 **AI가 확신한 칸뿐이다** — 확신하지 못했거나(`aiConfidence`가 낮다) 사람에게 물은(`ask`) 칸은 서버가 `screen-bulk`로 받지 않고
   사람이 값을 직접 고르게 한다(화면이 막는 것과 별개로 `record()`가 거절한다 — 문이 둘이어도 선은 하나).
   «모두 확정»은 아직 답하지 않은 칸만 채운다 — 보류한 칸은 사람이 일부러 멈춘 칸이라 건드리지 않는다. 값 여럿 칸은 사람이 고른 값만 초록이고
   AI 값은 테두리로만 보인다(확정 전의 값이 기록된 값처럼 보이지 않게). 사진 준비 전 묶음도 건은 보이고 손으로 고를 수 있다.
   버튼은 `POST /incr-decide` → `incr_review.record()`(CLI `record`와 같은 함수). 쓰는 문은 묶음(`batch`)을 **반드시** 받는다 — 암묵으로 고르지 않는다.
   화면의 수는 `status`가 센 그대로다(판정 응답에 실려 온다 — 규칙 8). `status`는 한 번에 확정한 칸(`cellsBulk`)과 하나씩 고른 칸(`cellsIndividual`,
   그중 AI와 같은 값 `cellsIndividualAgreed`), 눈가림으로 다시 본 칸(`cellsRechecked`)을 따로 센다 — «AI와 맞은 비율»이 한 번 누르기나 AI를 가린 답으로 흔들리지 않게.
   - **먼저 누른 답이 남는다.** 화면은 그 칸의 마지막 판정 `decisionId`를 `expectedLatest`로 보내고, 그사이 다른 사람이 답했으면 거절하고
     그 답을 화면에 들인다(30초마다도 들인다). 조용히 덮어쓰지 않는다.
   - **표본 다시 보기(눈가림).** 한 번에 확정한 건 가운데 일부(`RECHECK_SHARE`, 적어도 한 건 — 키의 해시 순이라 누가 봐도 같은 건)를
     «표본 다시 보기» 보기에 올린다. 그 보기는 앞 답·앞 사람 이름·AI 제안·AI 관찰을 가리고 칸 이름·허용값·정책만 보인다 — 도장 찍기가 되지 않게.
     그 칸을 한 번에 확정한 사람(과 마지막으로 답한 사람)이 아닌 사람만 고를 수 있고(`screen-recheck`, 칸마다 한 번), 고른 값이 앞 답과 같으면
     `recheckAgreed: true`, 다르면 **갈림**(`disputed`)이다 — 한 사람 대 한 사람에서 뒤에 누른 쪽이 이기지 않게, 확정으로 세지 않고(`cellsDisputed`)
     두 사람이 아닌 세 번째 사람이 고를 때까지 기다린다(두 사람이 다시 누르면 거절). 갈린 칸이 있는 줄만 결과에서 빠지고 묶음은 막히지 않는다 —
     `--skip-recheck`는 표본의 미완료만 넘기고 갈림은 풀지 않는다. 눈가림은 보기 모드가 아니라 **사람**에 걸린다 — 다시 볼 칸이 남은
     표본 건은 확정한 사람이 아니면 어느 보기에서든 가려 보이고, 사진의 «AI 근거» 표시도 뺀다(화면 데이터(JSON)에는 남아 있다 — 화면의 선이다). «바꾸기»로 고친 것은 다시 본 것으로 세지 않는다(`recheck_cells`가 눈가림 줄만 본다).
     한 건이라도 다르게 나오면 그 묶음의 표본을 `RECHECK_SHARE_AFTER_MISS`로 키우고, 다시 본 건의 `RECHECK_FULL_AT` 넘게(두 건 이상) 어긋나면
     한 번에 확정한 건을 **모두** 다시 본다.
     **한계:** «다른 사람»은 화면 맨 위에 적은 이름으로 가른다 — 로그인이 없어 이름을 바꿔 적으면 막지 못한다. 원장에 이름이 남아 뒤에 되짚을 수 있을 뿐이다.
   - **AI가 물은 칸의 답은 두 사람을 거쳐 사례가 된다.** 판독이 `ask`를 남긴 칸에 사람이 답하면 그 물음이 판정 줄의 `basedOn.ask`로 남고,
     그 칸은 표본이 아니라 **전부** 눈가림 다시 보기에 오른다(`_needs_second`). 다시 본 값이 같거나 갈림을 세 번째 사람이 가른 답만
     골든셋 검수의 «사람이 답한 AI의 물음»(`gt_review.answered_questions`)에 모여 다음 판독의 사례 목록에 간다 — 한 사람의 판단이 곧 사례가 되지 않게.
   - 시험 등록 건으로 보이는 줄(`NOT_FOR_SALE`)은 한 번에 확정을 받지 않는다(화면·기록기 둘 다). `export`는 갈림으로 빠진 줄 수(`disputedRows`)를 함께 돌려준다.
4. **결과** `export`(화면 «반영하기» = `POST /incr-export`) — **표본 다시 보기가 끝나야** 쓴다(아니면 `recheckPending`과 함께 거절 — 한 번에
   확정한 칸이 검사 전에 밖으로 나가지 않게. 다시 볼 사람이 없는 팀은 CLI `--skip-recheck`로만 넘기고, 결과 줄에 `recheckSkipped`가 남는다).
   **모든 칸을 지금 정책 안의 값으로 확정한 줄만** 밀어넣은 줄 그대로에 라벨을 채운다.
   값은 원래 형으로 되돌린다(`gt_task.export_value` — 골든셋 «반영해줘»와 같은 함수). 칸마다 `<열>Source`는 `INCR_REVIEW_AI_BULK`
   (AI 제안을 한 번에 확정), `INCR_REVIEW_AI_AGREED`(하나씩 보고 AI와 같은 값을 확정) 또는 `INCR_REVIEW_HUMAN`(사람이 다른 값)이다. 결과 파일은 원장의 파생물이라 추적하지 않는다(`.gitignore`) —
   이 저장소에 라벨의 사본을 두 벌 두지 않는다(규칙 4). 받는 쪽이 가져간다.

## 원장 한 줄

`decisionId`(`INC-<순번>-<짧은 난수>` — 두 사람이 동시에 써도 겹치지 않게) · `batch` · `key` · `field` · `decision`(`LABEL`·`HOLD`) · `value`(허용값 코드, 값 여럿이면 정렬해 `|`) ·
`aiValue`·`aiConfidence`·`aiRunId`(사람이 **본** AI 판독) · `agreedWithAi` · `reviewer`(필수) · `channel`(`screen`·`screen-bulk`·`screen-recheck`·`spoken`) · `basedOn.ask`(AI가 물은 칸이면 그 물음) ·
`decidedAt` · `basis`(`definitions#<필드>`) · `supersedes`(같은 칸의 앞 판정).

- **AI 추천만으로는 원장에 한 줄도 생기지 않는다.** 사람 판정 줄에 그 사람이 본 AI 값을 곁들일 뿐이다. 화면은 보여 준 AI 값을 `expectedAi`로,
  말로 받은 판정은 `--expect-ai`(없던 칸은 `--expect-ai-empty`)로 보낸다 — 지금 판독과 다르면 거절한다(골든셋 원장의 `expectedBefore`와 같은 선).
  «AI 값으로 모두 확정»도 사람이 누른 판정이다(`screen-bulk`) — 원장만 보고 한 번에 누른 것을 가려 되짚을 수 있다.
- 덧붙이기만 한다. 고치면 새 줄이 `supersedes`로 옛 줄을 가리킨다.
- 원장은 **지금 정책으로 읽는다** — 옛 코드는 과제의 `legacy`로 옮기고(파일은 그대로), 정책에서 빠진 값의 판정은 확정으로 세지 않고
  화면에 «정책이 바뀌어 다시 골라 주세요»로 다시 올린다.
- 허용값 밖의 값, 이름 없는 판정, 묶음에 없는 키, 사람이 본 AI 값이 없는 판정은 거절한다.

## 아직 하지 않는 것

- 확정한 증분을 골든셋(GT)에 **넣지 않는다.** 결과 파일을 어디로 보낼지(운영 적재·GT 편입)는 그 파일을 받는 쪽이 정한다.
  GT로 보내려면 GT를 가진 쪽이 결과 파일을 합친다 — 넣으면 그 줄은 골든셋 검수의 후보가 된다.
- 서버 화면으로 파일을 올리는 문은 없다 — 밀어넣기는 `push`(개발자·Claude)다. 서버는 원장·결과 파일 말고 쓰지 않는다.
