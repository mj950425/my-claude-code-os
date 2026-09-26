"""보고서·GT 개선 화면이 함께 쓰는 페이지 머리(글꼴·색·기본 모양).

감사 보고서(`render_catalog_report`)에서 떼어 냈다. GT 개선 화면이 머리 하나 때문에 감사 보고서 모듈 전체를
import하면, 그 모듈의 도메인 분기(판독 메모의 낱말로 테두리 색을 고르는 것 같은)까지 하네스의 일부가 된다 —
하네스가 과제를 모른다는 검사(`test_package_boundary`)가 그 모듈까지 봐야 한다. 머리만 여기 둔다.
"""

from __future__ import annotations

import html

STYLE = r"""
:root{
  color-scheme:light;
  /* 순흑백에 액센트 하나. 그 하나는 "고치자는 방향"과 "판독기가 인용한 사진"에만 쓴다 —
     화면에서 빨간 것이 보이면 그 자리가 곧 조치할 자리라는 뜻이 되어야 한다.
     --faint와 --muted는 캔버스 시안(#A3A3A3·#B8B8B8)보다 어둡다. 시안 값은 흰 배경에서
     2.5:1·2.0:1이라 본문으로 읽히지 않는다. 톤은 지키되 읽히는 선까지 내렸다. */
  --paper:#FFFFFF; --inset:#F7F7F7; --ink:#000000; --muted:#5C5C5C; --faint:#767676;
  --rule:#E4E4E4; --rule-soft:#F0F0F0; --ghost:#8A8A8A;
  --accent:#D62300; --accent-soft:#FDEDEA;
  --display:"Archivo","Gothic A1","Apple SD Gothic Neo",sans-serif;
  --sans:"Archivo","Gothic A1","Apple SD Gothic Neo",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,monospace;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-weight:400;font-size:14.5px;line-height:1.6;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,p,ul,ol,dl,dd,figure{margin:0}
a{color:inherit;text-decoration:none;border-bottom:1px solid var(--rule);transition:border-color .18s}
a:hover{border-color:var(--accent)}
button{font:inherit;color:inherit}
button:focus-visible,input:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{width:min(1360px,calc(100% - 56px));margin:0 auto}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.kicker{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.18em;text-transform:uppercase;color:var(--faint)}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:500;letter-spacing:-.02em}

/* 귀책을 색이 아니라 형태로 구분한다. 인쇄해도 남는다. */
.mark{display:inline-block;width:9px;height:9px;border:1.25px solid var(--ink);flex:0 0 auto;translate:0 -1px}
.mark.GT{background:var(--ink)}
.mark.POLICY{background:transparent}
.mark.RUNTIME{border-radius:50%;background:transparent}
.mark.OPEN{background:linear-gradient(135deg,var(--ink) 0 50%,transparent 50% 100%)}
.mark.NONE{border-style:dotted}

/* masthead */
.masthead{padding:36px 0 0}
.masthead-top{display:flex;justify-content:space-between;align-items:baseline;gap:24px;padding-bottom:10px;font-family:var(--mono);font-size:10.5px;color:var(--faint)}
.masthead-top .dirty{color:var(--accent)}
.masthead-top nav a{margin-left:14px;border-bottom-color:var(--faint);color:var(--muted)}
.masthead h1{font-family:var(--display);font-weight:800;letter-spacing:-.045em;line-height:.98;font-size:clamp(2.4rem,5.4vw,4.6rem);padding:20px 0 10px;border-top:2px solid var(--ink)}
.masthead h1 small{display:block;font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);margin-bottom:10px}
.runbar{display:flex;flex-wrap:wrap;margin-top:18px;border-top:1px solid var(--ink);border-bottom:1px solid var(--ink)}
.runbar div{flex:1 1 130px;padding:9px 14px 10px;border-left:1px solid var(--rule)}
.runbar div:first-child{border-left:0;padding-left:0}
.runbar dt{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint)}
.runbar dd{margin:2px 0 0;font-family:var(--mono);font-size:14px;font-weight:500;font-variant-numeric:tabular-nums}

/* 표지: 두 목록 */
.lanes{display:grid;grid-template-columns:1fr 1fr;margin-top:44px}
.lane{padding:0 34px 22px 0}
.lane + .lane{border-left:1px solid var(--rule);padding:0 0 22px 34px}
.lane-head{display:flex;align-items:baseline;gap:10px}
.lane-head h2{font-family:var(--display);font-weight:700;font-size:1.5rem;letter-spacing:-.035em}
.lane-head small{font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
.lane-count{margin:6px 0 4px;display:flex;align-items:baseline;gap:10px}
.lane-count .num{font-size:3.2rem;font-weight:300;line-height:1}
.lane-count span{font-size:12.5px;color:var(--muted);font-family:var(--mono)}
.sig{display:grid;grid-template-columns:1fr auto;gap:2px 16px;padding:10px 0;border-top:1px solid var(--rule)}
.sig strong{font-weight:500;font-size:13px}
.sig strong i{font-style:normal;font-family:var(--mono);font-size:10px;letter-spacing:.08em;color:var(--accent);margin-right:8px}
.sig .num{font-size:14px}
.lane-open{display:inline-block;margin-top:16px;padding:9px 14px;border:1px solid var(--ink);font-family:var(--mono);font-size:11px;letter-spacing:.06em}
.lane-open:hover{background:var(--ink);color:var(--paper)}
.aside-strip{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--ink);border-bottom:1px solid var(--ink)}
.aside-strip div{display:grid;grid-template-columns:auto 1fr;gap:0 14px;align-items:center;padding:14px 18px;border-left:1px solid var(--rule)}
.aside-strip div:first-child{border-left:0;padding-left:0}
.aside-strip .num{font-size:1.9rem;font-weight:300;line-height:1}
.aside-strip p{display:flex;align-items:center;gap:7px;font-size:12.5px;font-weight:500}

.sec{margin-top:60px}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;gap:20px;padding-bottom:10px;border-bottom:1.5px solid var(--ink)}
.sec-head h2{font-family:var(--display);font-weight:700;font-size:1.55rem;letter-spacing:-.035em}
.sec-head h2 small{display:block;font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);margin-bottom:6px}
.map{width:100%;border-collapse:collapse;margin-top:6px}
.map th,.map td{text-align:left;padding:9px 16px 9px 0;border-bottom:1px solid var(--rule);vertical-align:top;font-size:13px}
.map th{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);font-weight:500;padding-top:0}
.map td.id{font-family:var(--mono);font-size:10.5px;color:var(--muted);word-break:break-all}
.map td.desc{color:var(--muted)}
.map td.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.map td.where,.map td.mono{font-family:var(--mono);font-size:10.5px;color:var(--muted)}
.map td.lbl{font-family:var(--mono);font-size:11px;font-weight:600;white-space:nowrap}

/* 사례 보고서: 툴바 */
.toolbar{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;align-items:center;margin-top:28px;background:var(--paper);border-top:1.5px solid var(--ink);border-bottom:1px solid var(--rule)}
.toolbar button{appearance:none;background:none;border:0;border-right:1px solid var(--rule);font-family:var(--mono);font-size:11px;letter-spacing:.04em;color:var(--muted);padding:10px 14px;cursor:pointer;display:flex;align-items:center;gap:7px;transition:color .16s,background .16s}
.toolbar button:hover{background:var(--inset);color:var(--ink)}
.toolbar button[aria-pressed=true]{background:var(--ink);color:var(--paper)}
.toolbar button[aria-pressed=true] .mark{border-color:var(--paper)}
.toolbar button[aria-pressed=true] .mark.GT{background:var(--paper)}
.toolbar button[aria-pressed=true] .mark.OPEN{background:linear-gradient(135deg,var(--paper) 0 50%,transparent 50% 100%)}
.toolbar .search{margin-left:auto;display:flex;align-items:center;gap:10px}
.toolbar input{border:0;border-left:1px solid var(--rule);background:transparent;padding:10px 12px;font-family:var(--mono);font-size:11.5px;min-width:280px}
.toolbar input::placeholder{color:var(--faint)}
.toolbar .shown{font-family:var(--mono);font-size:11px;color:var(--faint);padding-right:4px}

/* 군집 머리 */
.cluster{margin-top:44px}
.cluster-head{display:grid;grid-template-columns:184px 1fr;gap:0 34px;padding:16px 0 18px;border-top:1.5px solid var(--ink)}
.cluster-rail .cid{font-family:var(--mono);font-size:12px;font-weight:600;color:var(--accent);letter-spacing:.04em}
.cluster-rail .cid.plain{color:var(--ink)}
.cluster-rail .ccount{margin-top:6px;font-family:var(--mono);font-size:11px;color:var(--muted)}
.cluster-rail .ccount .num{font-size:1.6rem;font-weight:300;display:block;line-height:1;color:var(--ink);margin-bottom:2px}
.cluster-rail .status{display:inline-block;margin-top:8px;padding:2px 6px;border:1px solid var(--accent);color:var(--accent);font-family:var(--mono);font-size:9.5px;letter-spacing:.1em}
.cluster-rail .status.DECIDED{border-color:var(--ink);color:var(--ink)}
.cluster-body h2{font-family:var(--display);font-weight:700;font-size:1.24rem;line-height:1.42;letter-spacing:-.025em;max-width:64ch}
.cluster-body .sub{margin-top:4px;font-size:12.5px;color:var(--muted)}
.q-impact{display:flex;flex-wrap:wrap;margin-top:12px;border:1px solid var(--rule);width:fit-content;max-width:100%}
.q-impact div{padding:5px 13px 6px;border-left:1px solid var(--rule)}
.q-impact div:first-child{border-left:0}
.q-impact dt{font-family:var(--mono);font-size:9px;letter-spacing:.11em;text-transform:uppercase;color:var(--faint)}
.q-impact dd{font-family:var(--mono);font-size:13px;font-weight:600}
.q-rec{margin-top:10px;font-size:13px;max-width:72ch}
.q-rec b{font-family:var(--mono);font-size:10px;letter-spacing:.11em;text-transform:uppercase;color:var(--faint);display:block;margin-bottom:2px}
.q-rec b i{font-style:normal;color:var(--accent)}
.p-more h4 small{font-weight:400;letter-spacing:.06em;text-transform:none;color:var(--accent)}

/* 정정 후보: 한 제안이 한 장이다. 제목 다음이 바로 필터이고, 그 다음이 제안이다 —
   머리에 리포트 자신을 설명하는 말도, 리포트 자신을 세는 숫자도 두지 않는다.
   이 화면이 묻는 것은 "이 GT가 틀렸나" 하나뿐이고, 나머지는 그 답에 기여하지 않는다. */
.fx{display:grid;grid-template-columns:96px minmax(0,1fr);gap:0 32px;padding:60px 0 68px;
    border-top:1px solid var(--ink);animation:fx-rise .45s cubic-bezier(.2,.7,.3,1) both}
@keyframes fx-rise{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.fx{animation:none}}

/* 왼쪽 — 일련번호. 카드를 세는 유일한 자리다 */
.fx-rail{position:sticky;top:64px;align-self:start}
.fx-rail .ord{display:block;font-family:var(--mono);font-size:40px;font-weight:600;letter-spacing:-.03em;
              line-height:.85;color:var(--rule);font-variant-numeric:tabular-nums}
.fx-rail .chips{margin-top:20px}
.stamp{display:inline-block;margin-top:18px;padding:5px 10px 4px;font-family:var(--mono);font-size:10px;
       font-weight:700;letter-spacing:.14em;border:1px solid var(--rule);color:var(--muted)}
.stamp.SURE{border-color:var(--accent);color:var(--accent)}
.stamp.POLICY{border-color:var(--ink);color:var(--ink)}
.stamp.ASK,.stamp.OPEN{border-style:dashed}

.fx-body{min-width:0}
.fx-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin-bottom:14px;
         font-family:var(--mono);font-size:11px;letter-spacing:.07em;color:var(--ghost)}
.fx-meta a{font-weight:600;letter-spacing:.1em;color:var(--ink);border:0}
.fx-meta a:hover{color:var(--accent)}
.fx h3{font-family:var(--display);font-weight:700;font-size:26px;line-height:1.3;letter-spacing:-.02em;
       max-width:34ch;overflow-wrap:anywhere}

/* 주문 — 현재 GT는 그어지고, 제안만 색을 갖는다 */
.ruling{display:flex;flex-wrap:wrap;align-items:flex-end;gap:16px 44px;margin-top:32px;padding:26px 0;
        border-top:1px solid var(--ink);border-bottom:1px solid var(--rule)}
.ruling>div{min-width:0}
.ruling dt{font-family:var(--mono);font-size:10px;font-weight:600;letter-spacing:.15em;color:var(--ghost);margin-bottom:10px}
.ruling dd{font-family:var(--display);font-size:42px;font-weight:800;letter-spacing:-.03em;line-height:.95;
           font-variant-numeric:tabular-nums}
.ruling .was dd{color:#8A8A8A;text-decoration:line-through;text-decoration-thickness:2px}
.ruling .now dd{color:var(--accent)}
.ruling .keep dd{color:var(--ink);font-size:32px}
.ruling small{display:block;margin-top:12px;font-family:var(--mono);font-size:10.5px;letter-spacing:.05em;
              line-height:1.55;color:var(--muted);overflow-wrap:anywhere}
.ruling .to{align-self:center;margin-bottom:24px;color:var(--rule);line-height:0}
.ruling .to svg{display:block}

/* 판독기와 리뷰어를 섞지 않는다 */
.fx-say{display:grid;grid-template-columns:104px minmax(0,1fr);gap:20px;padding:19px 0;
        border-bottom:1px solid var(--rule-soft)}
.fx-say:last-of-type{border-bottom:0}
.fx-say b{font-family:var(--mono);font-size:10px;font-weight:600;letter-spacing:.13em;color:var(--ghost);padding-top:3px}
.fx-say p{font-size:15px;line-height:1.75;color:var(--ink);max-width:76ch;overflow-wrap:anywhere}
.fx-say.mut p{color:var(--muted)}
.fx-say .src{font-family:var(--mono);font-size:11px;letter-spacing:.04em;color:var(--ghost)}

/* 판정 — 읽는 자리에서 그대로 답한다.
   액센트는 «조치할 자리»에만 쓴다는 이 화면의 규칙 그대로, 승인 버튼 하나가 그 자리다. */
.act{margin-top:36px;padding-top:22px;border-top:1px solid var(--ink)}
.act-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin-bottom:16px;
          font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--ghost)}
.act-form{display:grid;gap:12px;max-width:760px}
.act-bind{display:grid;grid-template-columns:104px minmax(0,1fr);gap:12px;align-items:center}
.act-bind>span{font-family:var(--mono);font-size:10px;font-weight:600;letter-spacing:.13em;color:var(--ghost)}
.act select,.act input[type=text]{width:100%;padding:10px 12px;border:1px solid var(--rule);background:var(--paper);
  color:var(--ink);font-family:var(--sans);font-size:14px;border-radius:0}
.act select:focus,.act input[type=text]:focus{outline:0;border-color:var(--ink)}
.act select[disabled]{background:var(--inset);color:var(--muted)}
/* 규칙 칸은 판례를 고르면 잠긴다. 왜 잠겼는지가 옆에 적혀야 «고장」으로 안 읽힌다. */
.act-rule{display:block;min-width:0}
.act-rule small{display:block;margin-top:6px;font-family:var(--mono);font-size:10.5px;
  letter-spacing:.04em;color:var(--faint)}
.act-buttons{display:flex;flex-wrap:wrap;gap:10px;margin-top:2px}
.act button{padding:12px 18px;border:1px solid var(--ink);background:var(--paper);color:var(--ink);
  font-family:var(--sans);font-size:13.5px;font-weight:600;cursor:pointer;transition:background .15s,color .15s}
.act button:hover{background:var(--ink);color:var(--paper)}
.act button.go{background:var(--accent);border-color:var(--accent);color:#fff}
.act button.go:hover{background:#B01D00;border-color:#B01D00}
.act button[disabled]{opacity:.45;cursor:default}
.act button[disabled]:hover{background:var(--paper);color:var(--ink)}
.act .out{font-family:var(--mono);font-size:11.5px;line-height:1.6;letter-spacing:.02em;color:var(--muted);
  padding:12px 14px;background:var(--inset);overflow-wrap:anywhere}
.act .out.bad{background:var(--accent-soft);color:#8C1800}
/* 이미 답한 건. 폼을 지우고 무엇을 언제 답했는지만 남긴다 — 두 번 누를 자리를 없앤다. */
.act.settled{border-top-color:var(--rule)}
.act .stamp-done{display:grid;gap:6px;padding:14px 16px;background:var(--inset);
  font-family:var(--mono);font-size:11.5px;line-height:1.7;color:var(--ink);overflow-wrap:anywhere}
.act .stamp-done b{font-size:12px;letter-spacing:.06em}
.act .stamp-done .held{color:var(--accent)}
/* 서버 없이 파일로 열었을 때. 버튼을 그려 놓고 안 눌리는 것보다 왜 안 되는지 적는 편이 낫다. */
.act .offline{font-family:var(--mono);font-size:11.5px;line-height:1.7;color:var(--faint);
  padding:12px 14px;border:1px dashed var(--rule)}

/* 증거판 — 인용된 장면이 먼저 온다 */
.plate{margin-top:34px}
.plate-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin-bottom:14px;
            font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--ghost)}
.plate .shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:20px;overflow:visible;padding:0}
.plate .shots figure{margin:0;min-width:0}
.plate .frame{display:block;width:100%;padding:0;border:1px solid var(--rule);background:#fff;
              cursor:zoom-in;position:relative;transition:border-color .16s,transform .16s}
.plate .frame:hover{border-color:var(--ink);transform:translateY(-2px)}
/* 높이를 고정하면 세로로 긴 상세컷이 가운데 실오라기 한 줄로 줄어든다 — 증거를 못 읽는다.
   폭을 채우고 높이는 사진이 정한다. 격자가 들쭉날쭉해지지만, 보이는 편이 낫다. */
/* 긴 상세 원본에서 이 장면이 실제로 차지한 자리만 보여준다. 고정 절단 판(v0-fixed)에서는 타일 높이가 폭×1.5라,
   액자를 2:3으로 잡으면 액자 높이가 곧 타일 한 칸이다. 다른 판은 렌더러가 `tile`을 0으로 보내 자르지 않는다.
   그러면 `top`의 100%가 정확히 한 타일이라 원본 높이를 몰라도 잘라 낼 수 있다.
   **근사다** — 운영은 타일 높이를 half_up(폭×1.5)로 반올림하므로, 폭이 홀수면 k번째 타일이 원본 기준 최대 k×0.5px
   어긋난다. 보는 용도(어느 사진인가)에는 충분하고, 판독에 쓰는 조각은 이 CSS가 아니라 tile_rule로 잘라 낸다.
   자르지 않으면 한 원본에서 나온 열 장이 전부 같은 그림으로 보이고,
   「어느 사진이 근거인가」에 답할 수 없다. */
.plate .frame.tiled{position:relative;aspect-ratio:2/3;overflow:hidden;border-width:0;
  outline:1px solid var(--rule);outline-offset:-1px}
.plate figure.cited .frame.tiled{border-width:0;outline:2px solid var(--accent);outline-offset:-2px}
.plate .frame.tiled img{position:absolute;left:0;top:calc(var(--tile) * -100%);
  width:100%;height:auto;min-height:0;max-height:none}
.plate .frame img{display:block;width:100%;height:auto;min-height:200px;max-height:520px;
                  object-fit:contain;background:#fff}
.plate figure.cited .frame{border:2px solid var(--accent)}
.plate figure.cited .frame::after{content:"근거";position:absolute;top:0;left:0;background:var(--accent);color:#fff;
              font-family:var(--mono);font-size:9.5px;font-weight:700;letter-spacing:.14em;padding:5px 9px 4px}
.plate figcaption{margin-top:9px;font-family:var(--mono);font-size:10px;letter-spacing:.09em;color:var(--ghost)}
.plate figcaption .scene{display:block}
/* 판독기가 이 장면에서 본 것. 주장과 사진을 잇는 한 줄이라 캡션에서 가장 크게 읽혀야 한다 */
/* 판독 기록은 사진에 붙어야 한다. 캡션 한 줄로 떨어뜨리면 어느 사진의 말인지 흐려진다. */
.plate .seen{position:absolute;left:0;right:0;bottom:0;padding:7px 9px;
  font-family:var(--sans);font-size:12px;line-height:1.35;text-align:left;
  background:rgba(255,255,255,.94);border-top:1px solid var(--rule)}
.plate .seen b{font-weight:600}
.plate .seen.male b{color:#1449b8}
.plate .seen.female b{color:#c0134a}
.plate .seen.none{color:var(--ghost);font-style:italic}
/* 인용하지 않았는데 판독기가 무언가 적어 둔 장면. 근거로 채택된 것과 같은 무게로 보이면
   안 되지만, 숨기면 반증이 사라진다 — 실행의 주장과 어긋나는 기록이 여기 있었다. */
.plate .seen.aside{background:rgba(255,255,255,.9);border-top:1px dashed var(--rule)}
.plate figure:not(.cited) .frame{outline:1px dashed var(--rule);outline-offset:-1px}
.plate .frame.missing{position:relative;aspect-ratio:2/3;display:grid;place-items:center;
  border:1px dashed var(--rule);background:var(--inset);padding:14px;text-align:center}
.plate .frame.missing .seen{position:static;background:none;border:0;text-align:center}
.plate figcaption .derived{display:block;margin-top:3px;font-family:var(--sans);font-size:11.5px;color:var(--muted)}
.plate figure.cited figcaption .scene{color:var(--accent)}
.plate figcaption .fail{display:none;margin-top:4px;color:var(--accent)}
.plate figure.gone .frame{border-style:dashed;background:var(--inset);cursor:default}
.plate figure.gone .frame img{height:44px;opacity:0}
.plate figure.gone figcaption .fail{display:block}
.plate details{margin-top:16px;border-top:1px solid var(--rule-soft);padding-top:14px}
.plate summary{cursor:pointer;font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;color:var(--muted)}
.plate summary:hover{color:var(--accent)}
.plate details .shots{margin-top:14px}
.plate .noshot{padding:26px;border:1px dashed var(--rule);background:var(--inset);color:var(--ghost);
               font-family:var(--mono);font-size:11px;text-align:center}
.fx .chips{margin-top:8px}

/* 확대 뷰어 */
dialog.viewer{max-width:96vw;max-height:96vh;padding:0;border:1.5px solid var(--ink);background:var(--paper)}
dialog.viewer::backdrop{background:rgba(23,21,15,.86)}
dialog.viewer img{display:block;max-width:92vw;max-height:84vh;object-fit:contain;background:#fff}
dialog.viewer .bar{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:8px 12px;
                   border-top:1px solid var(--rule);font-family:var(--mono);font-size:11px;color:var(--muted)}
dialog.viewer button{appearance:none;background:none;border:1px solid var(--rule);font-family:var(--mono);
                     font-size:11px;padding:4px 10px;cursor:pointer;color:var(--ink)}
dialog.viewer button:hover{background:var(--ink);color:var(--paper)}

/* 상품 카드: 하네스 리포트처럼 상품 단위로 이미지를 밀집한다 */
.product{display:grid;grid-template-columns:184px 1fr;gap:0 34px;padding:22px 0 26px;border-top:1px solid var(--rule)}
.p-rail{font-family:var(--mono);font-size:11px;color:var(--muted);line-height:1.7}
.p-rail .idx{display:flex;align-items:center;gap:8px;color:var(--ink);font-weight:500;letter-spacing:.08em}
.p-rail .key{margin-top:8px;color:var(--ink);font-size:11.5px;word-break:break-all}
.p-rail .cat{font-size:10.5px;color:var(--faint);word-break:break-all}
.p-rail .chips{margin-top:10px}
.p-body{min-width:0}
.p-head{display:flex;justify-content:space-between;align-items:flex-start;gap:20px}
.p-head h3{font-family:var(--display);font-weight:700;font-size:1.18rem;line-height:1.34;letter-spacing:-.025em}
.p-head .pdp{flex:0 0 auto;font-family:var(--mono);font-size:10.5px;padding:5px 9px;border:1px solid var(--rule)}
.p-head .pdp:hover{border-color:var(--ink)}
.labels{display:flex;align-items:stretch;border:1px solid var(--rule);width:fit-content;max-width:100%;margin-top:12px;background:var(--paper)}
.labels > div{padding:7px 14px 8px;border-left:1px solid var(--rule)}
.labels > div:first-child{border-left:0}
.labels dt{font-family:var(--mono);font-size:8.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint)}
.labels dd{margin-top:1px;font-family:var(--mono);font-size:14px;font-weight:600;letter-spacing:-.01em}
.labels dd small{display:block;font-size:9px;font-weight:400;color:var(--muted);letter-spacing:.02em}
.labels .verdict{background:var(--ink);color:var(--paper)}
.labels .verdict dt{color:rgba(250,249,245,.6)}
.labels .verdict dd small{color:rgba(250,249,245,.7)}
.labels .verdict.pending{background:var(--accent-soft);color:var(--accent)}
.labels .verdict.pending dt,.labels .verdict.pending dd small{color:rgba(140,43,24,.7)}
/* 판독기가 쓴 것과 리뷰어가 쓴 것을 한 칸에 섞지 않는다. 누가 쓴 문장인지가 판정을 가른다. */
.p-split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0;margin-top:14px;border:1px solid var(--rule)}
.voice{padding:11px 14px 13px;min-width:0}
.voice + .voice{border-left:1px solid var(--rule)}
.voice.review{background:var(--inset)}
.voice > h4{display:flex;align-items:baseline;gap:8px;margin-bottom:8px;font-family:var(--mono);font-size:9.5px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--ink)}
.voice > h4 small{font-weight:400;letter-spacing:.06em;text-transform:none;color:var(--faint);font-size:9.5px}
.voice p{font-size:13px;line-height:1.55}
.voice p + p{margin-top:6px}
.voice .said{font-weight:500}
.voice .aside{color:var(--muted);font-size:12.5px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.voice .chips{margin-top:8px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:2px 7px;border:1px solid var(--rule);font-family:var(--mono);font-size:10px;letter-spacing:.04em;background:var(--paper)}
/* 액센트는 "고치자는 방향"과 "판독기가 인용한 사진"에만 쓴다. 미결 판례는 맥락이지
   주장이 아니다 — 빨강을 여기에 쓰면 화면에서 빨간 것을 찾는 눈이 흐려진다.
   대신 굵기로 가른다: 미결은 검은 테두리, 확정은 회색. */
.chip.open,.chip.dual{border-color:var(--ink);color:var(--ink)}
.chip a{border:0}
.trail{display:flex;flex-wrap:wrap;border:1px solid var(--rule);width:fit-content;background:var(--paper);margin-bottom:8px}
.trail div{padding:5px 12px 6px;border-left:1px solid var(--rule)}
.trail div:first-child{border-left:0}
.trail dt{font-family:var(--mono);font-size:8.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
.trail dd{font-family:var(--mono);font-size:12px;font-weight:600}
.trail div.final{background:var(--ink);color:var(--paper)}
.trail div.final dt{color:rgba(250,249,245,.6)}
.trail-src{font-family:var(--mono);font-size:10px;color:var(--muted);margin-top:6px}
.quote{padding-left:12px;border-left:2px solid var(--ink);font-family:var(--serif);font-size:14.5px;font-weight:300;line-height:1.5}
.quote span{display:block;margin-top:3px;font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
.quote.absent{border-left-color:var(--rule);color:var(--muted);font-size:13px;font-family:var(--sans)}
.shots-head{display:flex;align-items:baseline;gap:8px;margin:16px 0 6px;font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
.shots-head b{color:var(--ink);font-weight:500}
.shots-head i{font-style:normal;color:var(--accent)}
.shots{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px;scrollbar-color:var(--rule) transparent}
.shots.detail{background:var(--inset);padding:6px 6px 8px}
.shot{flex:0 0 124px;width:124px;margin:0;border:1px solid var(--rule);background:#fff;position:relative}
.shot a{display:block;border:0}
.shot img{display:block;width:100%;height:140px;object-fit:contain;background:#fff}
.shot figcaption{padding:4px 6px 5px;font-family:var(--mono);font-size:9px;line-height:1.4;color:var(--muted);border-top:1px solid var(--rule);min-height:30px;overflow-wrap:anywhere}
.shot figcaption b{display:block;color:var(--ink);font-weight:600;font-size:9.5px}
.shot.evidence{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.shot.evidence figcaption b{color:var(--accent)}
.shot.evidence::after{content:"근거";position:absolute;top:4px;left:4px;padding:1px 5px;background:var(--accent);color:var(--paper);font-family:var(--mono);font-size:8.5px;letter-spacing:.1em}
.shot.missing{display:grid;place-items:center;height:172px;color:var(--faint);font-family:var(--mono);font-size:10px;text-align:center;padding:8px;background:var(--inset)}
.p-more{margin-top:12px;font-size:12.5px}
.p-more summary{cursor:pointer;font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);list-style:none}
.p-more summary::before{content:"+ ";color:var(--ink)}
.p-more[open] summary::before{content:"− "}
.p-more .grid{display:grid;grid-template-columns:1fr 1fr;gap:0 28px;margin-top:8px;padding-top:8px;border-top:1px solid var(--rule)}
.p-more h4{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint);margin:10px 0 4px}
.sigrow{padding:6px 0;border-top:1px dashed var(--rule)}
.sigrow:first-of-type{border-top:0}
.sigrow strong{display:block;font-weight:500;font-size:12.5px}
.sigrow p{font-size:12px;color:var(--muted);line-height:1.5}
.sentence{font-size:12.5px;line-height:1.5;padding:4px 0}
.kv{font-family:var(--mono);font-size:10.5px;color:var(--muted);line-height:1.8}
.kv b{color:var(--ink);font-weight:500}
.recovery{margin-top:6px;padding:6px 9px;background:var(--accent-soft);color:#75401F;font-size:11.5px;line-height:1.5}
.empty{padding:48px 22px;color:var(--muted);text-align:center;font-size:13px}

footer{margin-top:64px;padding:22px 0 70px;border-top:1.5px solid var(--ink);color:var(--muted);font-size:12px;line-height:1.75}
footer .mono{color:var(--ink)}
footer nav a{margin-right:16px;border-bottom-color:var(--faint)}

@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@media (max-width:900px){
  .wrap{width:min(1360px,calc(100% - 28px))}
  .lanes,.aside-strip{grid-template-columns:1fr}
  .aside-strip div{border-left:0;border-top:1px solid var(--rule)}
  .aside-strip div:first-child{border-top:0}
  .aside-strip div{padding-left:0}
  .lane{padding:0 0 22px}
  .lane + .lane{border-left:0;border-top:1px solid var(--rule);padding:22px 0}
  .sec-head{flex-direction:column;align-items:flex-start}
  .sec-head p{text-align:left}
  .cluster-head,.product{grid-template-columns:1fr;gap:10px}
  .fx{grid-template-columns:1fr;gap:14px 0;padding:22px 0 28px}
  .fx-rail{position:static;display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px}
  .fx-rail .chips{margin-top:0}
  .stamp{transform:none}
  .plate .shots{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
  .plate .frame img{height:200px}
  .p-rail{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center}
  .p-rail .key,.p-rail .chips{margin-top:0}
  .p-split{grid-template-columns:1fr}
  .voice + .voice{border-left:0;border-top:1px solid var(--rule)}
  .p-more .grid{grid-template-columns:1fr}
  .toolbar{position:static}
  .toolbar .search{margin-left:0;width:100%}
  .toolbar input{min-width:0;flex:1}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media print{
  body{font-size:10pt}
  .toolbar,.p-head .pdp{display:none}
  .product,.cluster-head,.fx{break-inside:avoid}
  .plate figure{break-inside:avoid}
  .shots{flex-wrap:wrap;overflow:visible}
  a{border:0}
}
"""


def head(title: str) -> str:
    return (
        '<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=Gothic+A1:wght@400;500;700;800&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">\n'
        f"<style>{STYLE}</style>\n</head>\n<body>\n"
    )
