#!/usr/bin/env python3
"""증분 검수 화면(`templates/incr.html`)을 만든다 — 골든셋 검수 화면과 **같은 스타일 상수·같은 마크업**으로.

    python3 incr_render.py            # templates/incr.html을 다시 쓴다
    python3 incr_render.py --check    # 파일이 지금 생성기의 출력과 같은지만 본다(다르면 1)

## 왜 생성하나 — CSS는 한 벌이어야 한다

증분 화면을 손으로 쓴 CSS로 두었더니 골든셋 검수 화면과 글꼴·카드·버튼·간격이 조금씩 달랐다. 같은 운영팀이 두 화면을 오가며
누르는데 모양이 다르면 «다른 도구»로 읽힌다. 그래서 골든셋 검수를 그리는 상수(`page_style.head` · `EXTRA_STYLE` · `THEME_STYLE` ·
`SIDEBAR_STYLE`, 목록 카드·표는 정책·골든셋 화면과 같은 `LIST_STYLE`)와 사이드바(`sidebar_html`·`SIDEBAR_SCRIPT`)를 **그대로 가져다** 쓴다.
증분에만 있는 것(묶음 표·AI 추론 버튼)만 `INCR_STYLE`에 둔다.

## 왜 서버가 부르지 않고 파일로 쓰나

서버는 화면을 그리지 않는다(`serve_reports.py` 머리말) — 고정 파일과 JSON만 보낸다. 이 화면에는 데이터가 없고(건·수는 브라우저가
`/incr-tasks`·`/incr-data`에서 읽는다) 코드가 바뀔 때만 바뀌므로, 생성해 둔 파일 한 장이면 된다. 대신 골든셋 쪽 상수가 바뀌면
파일이 뒤처진다 — `test_incr_review`가 «파일 = 생성기 출력»을 확인해 그 어긋남을 잡는다(고칠 때는 이 스크립트를 다시 돌린다).
"""

from __future__ import annotations

import sys
from pathlib import Path

from gt_review_render import EXTRA_STYLE, LIST_STYLE, SIDEBAR_SCRIPT, SIDEBAR_STYLE, THEME_STYLE, sidebar_html
from page_style import head

TARGET = Path(__file__).resolve().parent.parent / "templates" / "incr.html"
FONT = '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap" rel="stylesheet">\n'

# 증분에만 있는 것. 나머지 모양은 전부 골든셋 검수의 상수에서 온다 — 여기에 카드·버튼·글꼴을 다시 적지 않는다.
INCR_STYLE = """
/* 증분 검수 — 골든셋 검수의 모양 위에 증분에만 있는 것만 */
.status-side #do-run{font:inherit;font-size:14px;font-weight:600;min-height:40px;padding:0 16px;border:1px solid var(--rule);border-radius:10px;background:#fff;color:var(--ink);cursor:pointer}
.status-side #do-run:hover{background:#F8FAFC;border-color:#CBD5E1}
.status-side #do-run:disabled{opacity:.6;cursor:default}
.title-side select{font:inherit;font-size:13.5px;min-height:42px;padding:0 12px;border:0;border-radius:10px;background:#F1F5F9;color:var(--ink);max-width:280px}
.incr-go{display:inline-flex;align-items:center;min-height:36px;padding:0 14px;border-radius:10px;background:var(--accent);color:#fff!important;font-size:13.5px;font-weight:600;white-space:nowrap}
.incr-go:hover{background:var(--accent-ink)}
.incr-go.quiet{background:#fff;color:var(--ink)!important;box-shadow:inset 0 0 0 1px var(--rule)}
.table .pbar{width:120px;height:6px;margin-top:6px}
.table .pbar i{background:var(--ok)}
.howto{margin-top:12px;font-size:13.5px;color:var(--muted)}
.howto code{display:block;margin-top:8px;padding:10px 12px;border-radius:10px;background:var(--paper);color:var(--ink);font-size:12.5px;overflow-x:auto;white-space:pre}
.item-all{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:10px;margin-top:6px;padding-top:14px;border-top:1px solid var(--rule-soft)}
.item-all .hint{margin-right:auto;font-size:12.5px;color:var(--faint)}
.item-all button{font:inherit;font-size:14px;font-weight:600;min-height:40px;padding:0 16px;border:0;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer}
.item-all button:hover{background:var(--accent-ink)}
.item-all button:disabled{opacity:.45;cursor:default}
@media (max-width:640px){.item-all button{width:100%}}
.item.focus{box-shadow:0 0 0 3px var(--accent),var(--shadow)}  /* 키보드 A·J의 대상 — 어느 건을 확정하는지 한눈에 */
.item-all.recheck{border-top-style:dashed}
.item-all.recheck button{background:var(--ok)}
.decide .cell .act:not(.chips):not(.notes) button.confirm-many{min-height:40px;padding:0 16px;border:0;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;font-weight:600}
.decide .cell .act:not(.chips):not(.notes) button.confirm-many:hover{background:var(--accent-ink);color:#fff}
.decide .cell .act:not(.chips):not(.notes) button.confirm-many:disabled{opacity:.45;cursor:default}
.decide .cell .result.disputed{color:var(--warn)}
.recheck-hint{grid-column:1/-1;margin:0 0 4px;font-size:13px;color:var(--muted)}
.toast{position:fixed;left:50%;bottom:72px;transform:translateX(-50%);z-index:50;padding:10px 16px;border-radius:10px;background:var(--ink);color:#fff;font-size:13.5px;max-width:80vw;box-shadow:var(--shadow)}
.toast.bad{background:var(--bad)}
.loading{color:var(--faint);padding:26px 0}
.table td.go{text-align:right}
.once.all-done{margin-top:24px}
"""

BODY = """<div class="shell">{sidebar}<div class="wrap">
<header class="masthead">
  <div class="masthead-top"><p class="kicker crumbs"><a href="/">홈</a> / <a href="/incr">증분 검수</a><span id="crumb-task"></span></p><p id="made"></p></div>
  <div class="title-row"><h1 id="title">증분 검수</h1><div class="title-side" id="title-side" hidden><nav class="pager" aria-label="페이지"><button type="button" id="page-prev" aria-label="이전 페이지">‹</button><span id="page-now"></span><button type="button" id="page-next" class="page-next">다음 페이지 →</button></nav><select id="batch-pick" aria-label="증분 묶음"></select><label class="who">판정하는 사람 <input id="reviewer" placeholder="이름"></label></div></div>
  <p class="lead" id="lead" hidden>새로 들어온 데이터를 AI가 먼저 읽어 두었습니다. 묶음을 열어 AI 제안이 맞으면 그대로 누르고, 다르면 맞는 값을 누르세요. 누른 판정만 기록됩니다 — AI 제안은 사람이 누르기 전에는 기록되지 않습니다.</p>
  <div class="actions"><div id="export-out" hidden aria-live="polite"></div></div>
  <div id="runner" hidden aria-live="polite"></div>
  <p id="name-hint" class="notice" hidden></p>
  <div class="statusline" id="statusline" hidden>
    <div class="progress-block">
      <div id="progress-short"></div>
      <div class="pbar" aria-hidden="true"><i id="pbar"></i></div>
      <div class="tally" id="tally"></div>
    </div>
    <div class="status-side">
      <div class="views" role="group" aria-label="보기">
        <button type="button" data-view="all" aria-pressed="true">전체</button>
        <button type="button" data-view="left" aria-pressed="false">답할 칸 <span class="count" id="count-left"></span></button>
        <button type="button" data-view="recheck" aria-pressed="false" id="view-recheck" hidden>표본 다시 보기 <span class="count" id="count-recheck"></span></button>
      </div>
      <button type="button" id="do-run" hidden>AI 추론 시작</button>
      <!-- 골든셋 검수와 같은 자리·이름 — 증분에서는 모든 칸을 확정한 건을 라벨 붙은 결과 파일로 쓴다 -->
      <button type="button" id="do-export" title="모든 칸을 확정한 건을 라벨 붙은 결과 파일로 씁니다">반영하기</button>
    </div>
  </div>
</header>
<div id="list"><p class="loading">증분 검수를 읽는 중…</p></div>
<div id="progress-bottom" hidden aria-live="polite"><span id="progress-bottom-text"></span>
  <span class="bottom-actions"><button type="button" id="next-open" title="키보드 J — 다음 건 · A — 이 건을 AI 제안대로 확정">다음 안 한 칸 ↓</button><button type="button" class="page-next" id="page-next-bottom">다음 페이지 →</button></span></div>
<div id="viewer" hidden><button type="button" class="go prev" data-go="-1" aria-label="이전 사진">‹</button><img alt=""><button type="button" class="go next" data-go="1" aria-label="다음 사진">›</button><p></p></div>
</div></div>
"""

# 화면의 동작. 건·칸·수는 전부 서버의 JSON(incr_review.home_rows · screen_data · status)에서 온다 — 여기서 원장을 세지 않는다.
# 버튼은 /incr-decide 하나로 간다(CLI record와 같은 함수). 사람이 본 AI 제안(expectedAi)을 늘 함께 보낸다.
SCRIPT = r"""
(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  const TASK = params.get('task'), BATCH = params.get('batch');
  const REVIEWER_KEY = 'gt-review-reviewer';  // 골든셋 검수 화면과 같은 이름 칸 — 한 번 적으면 두 화면이 같이 쓴다
  const PAGE_SIZE = 20;
  const $ = (id) => document.getElementById(id);
  const make = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const num = (v) => typeof v === 'number' ? v : 0;
  const when = (stamp) => {
    const at = stamp ? new Date(stamp) : null;
    return !at || isNaN(at) ? '' : at.toLocaleString('ko-KR', {month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  };
  const toast = (text, bad) => {
    const box = make('div', 'toast' + (bad ? ' bad' : ''), text);
    document.body.appendChild(box);
    setTimeout(() => box.remove(), bad ? 5000 : 1800);
  };
  const post = (path, body) => fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
    .then((r) => r.json().catch(() => ({ok: false, error: '서버 응답을 읽지 못했습니다 — 화면을 새로고침하고, 그래도 안 되면 Claude에게 «서버 다시 켜줘»라고 말해 주세요.'})))
    .catch(() => ({ok: false, error: '서버에 닿지 못했습니다 — 서버가 켜져 있는지 확인해 주세요(Claude에게 «서버 다시 켜줘»).'}));

  // 묶음 이름 — 기계 이름(«20260928-075902-prod-…») 대신 «9월 28일 오후 4:59 · prod-…»
  const batchLabel = (id) => {
    const m = String(id).match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-(.*)$/);
    if (!m) return String(id);
    const at = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
    return at.toLocaleString('ko-KR', {month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit'}) + ' · ' + m[7];
  };

  // ---------------------------------------------------------------- 목록 — 과제마다 묶음 표
  function batchState(b) {
    const runner = b.runner || {};
    if (runner.running) return ['to', 'AI가 읽는 중'];
    if (runner.phase === 'failed') return ['out', 'AI 추론 멈춤'];
    if (!b.itemsRead) return ['to', 'AI 추론 전'];
    if (!b.itemsLeft) return ['ok', '검수 끝'];
    return ['to', '검수 중'];
  }
  function drawList(data) {
    $('lead').hidden = false;
    const list = $('list');
    list.textContent = '';
    (data.tasks || []).forEach((task) => {
      const card = make('section', 'card');
      card.id = 'task-' + task.task;
      card.appendChild(make('h2', null, task.name));
      card.appendChild(make('p', 'sub', '한 건(' + (task.unit || '') + ')마다 ' + task.fields.join(' · ')
        + (task.itemsLeft ? ' — 남은 건 ' + task.itemsLeft : '')));
      if (task.problem) card.appendChild(make('p', 'notice', '설정에 문제가 있습니다 — Claude에게 «' + task.name + ' 증분 설정 봐줘»라고 전해 주세요.'));
      if (!(task.batches || []).length) {
        const how = make('div', 'howto', '아직 밀어넣은 증분이 없습니다. Claude에게 파일과 함께 «' + task.name + ' 증분 넣어줘»라고 말하면 AI가 곧바로 읽기 시작합니다.');
        // 명령은 개발자용 — 운영팀 화면에서는 접어 둔다
        const dev = make('details', 'src'); dev.appendChild(make('summary', null, '개발자용 명령'));
        dev.appendChild(make('code', null, 'python3 .claude/os/engine/scripts/incr_review.py push --task ' + task.task + ' --file <증분.jsonl>'));
        how.appendChild(dev);
        card.appendChild(how);
      } else {
        const wrap = make('div', 'table');
        const table = make('table');
        const headRow = make('tr');
        ['묶음', '넣은 시각', '진행', '상태', ''].forEach((h) => headRow.appendChild(make('th', null, h)));
        const thead = make('thead'); thead.appendChild(headRow); table.appendChild(thead);
        const tbody = make('tbody');
        task.batches.forEach((b) => {
          const row = make('tr');
          const [tone, word] = batchState(b);  // 정책·골든셋 표와 같은 알약(.pill ok·to·out)
          row.appendChild(make('td', 'key', batchLabel(b.batch)));
          row.appendChild(make('td', 'src', when((b.meta || {}).pushedAt)));
          const prog = make('td', 'prog');
          prog.appendChild(make('div', null, '끝난 건 ' + b.itemsDone + ' / ' + b.items));
          const bar = make('div', 'pbar'); const fill = make('i'); fill.style.width = (b.items ? Math.round(b.itemsDone * 100 / b.items) : 0) + '%';
          bar.appendChild(fill); prog.appendChild(bar); row.appendChild(prog);
          const ai = make('td'); ai.appendChild(make('span', 'pill ' + tone, word)); row.appendChild(ai);
          const go = make('td', 'go');
          const a = make('a', 'incr-go' + (tone === 'ok' ? ' quiet' : ''), tone === 'ok' ? '다시 보기' : '검수하기 →');
          a.href = '/incr?task=' + encodeURIComponent(task.task) + '&batch=' + encodeURIComponent(b.batch);
          go.appendChild(a); row.appendChild(go);
          tbody.appendChild(row);
        });
        table.appendChild(tbody); wrap.appendChild(table); card.appendChild(wrap);
        if (task.moreBatches) card.appendChild(make('p', 'sub', '더 오래된 묶음 ' + task.moreBatches + '개는 표에서 뺐습니다(남은 건 수에는 들어 있습니다).'));
      }
      list.appendChild(card);
    });
    if (!(data.tasks || []).length) list.appendChild(make('p', 'loading', '증분을 받을 과제가 없습니다. 개발자에게 설정을 부탁해 주세요.'));
  }

  // ---------------------------------------------------------------- 검수 화면
  let DATA = null, ONLY_LEFT = false, FROM = 0, pollTimer = null, WATCH = null;
  const PENDING = [];  // 이름 없이 누른 판정들 — 이름을 적으면 누른 차례대로 기록한다
  const INFLIGHT = new Map();  // 보내는 중인 판정(칸·값) — 같은 판정이 겹쳐 오면 한 번만 보낸다
  let LAST_AT = -1;
  let PAGE_NO = 1;  // 몇 번째 페이지인가 — 넘긴 횟수로 센다(«남은 건» 보기에서는 끝낸 건이 빠져 자리로 셀 수 없다)  // 지금 페이지에 그린 마지막 건의 자리 — 다음 페이지는 그 뒤에서 시작한다
  // 페이지는 번호가 아니라 «묶음 안의 몇 번째 건부터»(FROM)로 센다 — «남은 건» 보기에서 한 페이지를 끝내면 목록이 줄어들어,
  // 번호로 넘기면 끝낸 수만큼 건을 건너뛴다.
  let SYNCING = false;
  let RECHECK = new Set();  // 표본 다시 보기 대상 건(서버 recheck_keys)
  let RECHECK_CELLS = {};  // 건마다 아직 다시 보지 않은 칸(서버 recheck_cells) — 다 봤으면 빈 목록
  const BLIND_PICKS = {};  // 눈가림 다시 보기에서 고른 값(기록 전) — 건 · 칸마다
  let RECHECK_BY = {};  // 건마다 한 번에 확정한 사람 — 그 사람에게는 다시 보기 자리를 보이지 않는다
  let VIEW_MODE = 'all';  // all · left(답할 칸) · recheck(표본 다시 보기)
  const FOLDED = new Set();  // 접힌 건 — 열 때 이미 다 답한 건만 접는다(골든셋 검수처럼). 답하는 도중에는 접지 않는다
  const PICKS = {};  // 값 여럿 칸에서 누른 값들 — 카드를 다시 그려도 남는다(키 → 칸 → {코드: 참})
  const reviewer = () => ($('reviewer').value || '').trim();
  const labelName = (field, code) => code == null || code === '' ? '(빈칸)'
    : String(code).split('|').map((part) => (field.labels.find((l) => l.code === part) || {}).name || part).join(' + ');
  const aiCell = (item, field) => ((item.reading || {}).cells || {})[field.id] || null;
  const aiValue = (item, field) => { const c = aiCell(item, field); return c && c.value ? c.value : null; };
  const aiUsable = (item, field) => {
    const v = aiValue(item, field);
    return !!v && String(v).split('|').every((p) => field.labels.some((l) => l.code === p));
  };
  const labeled = (d) => !!d && d.decision === 'LABEL' && !d.outOfPolicy && !d.disputed;
  const itemDone = (item) => DATA.fields.every((f) => labeled(item.decisions[f.id]));
  const open = (item) => DATA.fields.filter((f) => { const d = item.decisions[f.id]; return !d || d.outOfPolicy || d.disputed; });
  // AI가 확신 있게 낸 값 — 허용값이고, 확신 높음이고, 사람에게 묻지 않았다. 한 번에 확정은 이 칸만 한다(서버도 같은 선으로 막는다).
  const aiSure = (item, field) => { const c = aiCell(item, field); return aiUsable(item, field) && c.confidence !== 'LOW' && !(c.askHuman && c.askHuman.question); };
  // 눈가림 — 다시 볼 칸이 남은 표본 건을 확정한 사람이 아닌 사람이 보면, 어느 보기에서든 앞 답·AI 제안·AI 근거 표시를 가린다
  const blindFor = (item) => (RECHECK_CELLS[item.key] || []).length > 0 && !(RECHECK_BY[item.key] || []).includes(reviewer());
  // 갈린 칸은 한 번에 확정에서 뺀다 — 세 번째 사람은 AI 편이 아니라 사진을 보고 가른다(서버도 거절한다)
  const sureOpen = (item) => open(item).filter((f) => aiSure(item, f) && !(item.decisions[f.id] || {}).disputed);
  const acceptable = (item) => !item.looksLikeTest && sureOpen(item).length > 0;  // 시험 등록 건은 한 번에 확정하지 않는다(서버도 막는다)

  function decide(item, field, decision, value, bulk) {
    // 이미 기록된 것과 같은 판정은 다시 보내지 않는다 — 이름을 적는 순간 대기열이 기록되고 같은 값을 또 누르면 원장에 같은 줄이 둘 생긴다.
    const last = item.decisions[field.id];
    // 갈린 칸은 예외 — 마지막 판정이 두 번째 사람의 답이라, 세 번째 사람이 같은 값을 골라도 그것이 가르는 답이다
    if (bulk !== 'recheck' && last && !last.disputed && last.decision === decision && String(last.value ?? '') === String(value ?? '')) return Promise.resolve(true);
    const flight = [item.key, field.id, decision, value ?? ''].join('\u0000');
    if (INFLIGHT.has(flight)) return INFLIGHT.get(flight);
    const sent = send(item, field, decision, value, bulk).finally(() => INFLIGHT.delete(flight));
    if (reviewer()) INFLIGHT.set(flight, sent);
    return sent;
  }
  function send(item, field, decision, value, bulk) {
    if (!reviewer()) {
      // 누른 값을 버리지 않는다 — 이름을 적으면 그 판정을 그대로 이어서 기록한다.
      // 골든셋 검수와 같은 자리에 말한다 — 그 칸의 한 줄과 머리의 안내
      const out = document.querySelector('.item[data-key="' + CSS.escape(item.key) + '"] .cell[data-field="' + CSS.escape(field.id) + '"] .result');
      if (out) out.textContent = '맨 위에 이름을 먼저 적어 주세요. 적고 나면 이 칸이 기록됩니다.';
      $('name-hint').textContent = '이름을 적고 Enter를 누르거나 이름 칸 밖을 누르면 방금 누른 값이 기록됩니다.'; $('name-hint').hidden = false;
      $('reviewer').focus();
      // 같은 칸을 다시 누르면 앞의 대기는 버린다(마지막 값만 기록) — 앞 대기의 약속은 false로 끝내 그 버튼이 풀리게.
      const cell = item.key + '\u0000' + field.id;
      PENDING.filter((entry) => entry.cell === cell).forEach((entry) => entry.cancel());
      for (let i = PENDING.length - 1; i >= 0; i -= 1) if (PENDING[i].cell === cell) PENDING.splice(i, 1);
      return new Promise((resolve) => {
        const run = () => decide(item, field, decision, value, bulk).then(resolve);
        run.cell = cell; run.cancel = () => resolve(false);
        PENDING.push(run);
      });
    }
    // 메모(이유·정책 밖 표시)는 그 칸의 «메모 추가»에서 — 골든셋 검수와 같다
    const box = document.querySelector('.item[data-key="' + CSS.escape(item.key) + '"] .cell[data-field="' + CSS.escape(field.id) + '"]');
    const reason = box ? (box.querySelector('input.reason') || {}).value || '' : '';
    const gap = box ? !!(box.querySelector('input.gap') || {}).checked : false;
    const out = box && box.querySelector('.result');
    const earlier = out && (box.classList.contains('done') || box.classList.contains('held')) ? out.textContent.trim() : '';
    if (out) out.textContent = '기록하는 중…';
    return post('/incr-decide', {task: TASK, batch: DATA.batch, key: item.key, field: field.id, decision, value, reason, gap,
                                 reviewer: reviewer(), bulk: bulk === true, recheck: bulk === 'recheck', expectedAi: aiValue(item, field),
                                 // 화면이 본 그 칸의 마지막 판정 — 그사이 다른 사람이 답했으면 서버가 거절한다(조용히 덮어쓰지 않게)
                                 expectedLatest: (item.decisions[field.id] || {}).decisionId || null})
      .then((reply) => {
        if (!reply.ok) {
          const text = '기록하지 못했습니다 — ' + (reply.error || '다시 눌러 주세요.') + (earlier ? ' — 전의 판정은 그대로 남아 있습니다: ' + earlier : '');
          if (out) out.textContent = text; else toast(text, true);
          if (/먼저 이 칸을 답했습니다|이미 다른 사람이 다시 봤습니다/.test(reply.error || '')) {
            // 다른 사람의 답을 곧바로 화면에 들인다 — 카드를 다시 그리면 칸의 문장이 지워지므로 알림은 떠 있는 줄로도 남긴다
            toast(field.name + ': ' + reply.error, true);
            syncDecisions(true);
          }
          return false;
        }
        $('name-hint').hidden = true;
        item.decisions[field.id] = reply.decision;
        // 값 여럿 칸의 누른 값은 기록된 값에서 다시 시작한다 — 한 번에 확정·보류로 칸이 바뀌면 옛 선택이 남지 않게.
        if (PICKS[item.key]) delete PICKS[item.key][field.id];
        if (reply.status) { DATA.status = reply.status; drawStatus(); }
        return true;
      });
  }

  function acceptAll(item, button) {
    // 보류한 칸은 사람이 일부러 멈춘 칸이다 — 한 번에 확정에서 뺀다. 확정된 칸도 건드리지 않는다.
    // AI가 확신한 칸만 — 확신 낮음·AI가 물은 칸은 사진을 보고 직접 고른다(«직접 봐 주세요» 칸이 한 번에 확정되지 않게)
    if (!acceptable(item)) return Promise.resolve();
    const wasDone = itemDone(item);
    if (button) button.disabled = true;
    let chain = Promise.resolve(true);
    sureOpen(item).forEach((f) => { chain = chain.then((ok) => ok && decide(item, f, 'LABEL', aiValue(item, f), true)); });
    return chain.finally(() => {
      refresh(item, wasDone);
      // 보류한 칸만 남았으면 이 건에서 더 할 일이 없다 — 다음 건으로(끝난 건처럼).
      if (!itemDone(item) && !open(item).length) { const card = cardOf(item); if (card) focusNext(card, true); }
    });
  }

  // 사진 — 골든셋 검수와 같은 틀(.rail .shots figure). AI가 근거로 든 사진에 «AI 근거».
  function shots(item) {
    const rail = make('aside', 'rail');
    const one = item.images.length === 1;
    const box = make('div', 'shots' + (one ? ' one' : ''));
    let cited = new Set();
    DATA.fields.forEach((f) => ((aiCell(item, f) || {}).evidenceImageIds || []).forEach((id) => cited.add(String(id))));
    // 골든셋 검수와 같은 규칙 — 전부(또는 여섯 장 넘게) 짚었으면 짚은 것이 아니다. 표시하면 모든 사진에 «AI 근거»가 붙는다.
    // 사진을 두루(여섯 장 넘게·전부) 근거로 들었으면 짚은 사진이 없는 것과 같다 — 그 사실을 말하고 사진을 모두 펼쳐 둔다(사람이 직접 훑게)
    const broad = !blindFor(item) && cited.size > 0 && (cited.size > 6 || cited.size >= item.images.length) && item.images.length > 1;
    if (cited.size > 6 || cited.size >= item.images.length || blindFor(item)) cited = new Set();
    if (broad) rail.appendChild(make('p', 'why', 'AI가 사진 여러 장을 두루 근거로 들었습니다 — 짚은 사진이 따로 없으니 사진을 모두 훑어 주세요.'));
    if (!item.images.length) {
      rail.appendChild(make('p', 'why', item.notPrepared ? '사진 준비 전 — «AI 추론 시작»을 누르면 사진이 나옵니다. 그동안에도 직접 고를 수 있습니다.'
        : item.noEvidence ? '사진도 글도 없는 건입니다 — AI가 읽지 않았습니다. 직접 골라 주세요.' : '사진이 없습니다.'));
    }
    // 골든셋 검수처럼 — AI가 근거로 든 사진을 앞에, 대표 사진은 늘 곁에, 나머지는 «나머지 사진 N장 보기»로 접는다(16장을 같은 무게로 늘어놓지 않게)
    const order = item.images.map((image, index) => [image, index]).sort((a, b) => (cited.has(b[0].viewId) - cited.has(a[0].viewId)) || (a[1] - b[1]));
    const keep = item.images.length > 6 ? order.filter(([image, index]) => cited.has(image.viewId) || index === 0) : order;
    const kept = keep.length ? keep : order.slice(0, 4);
    const rest = order.filter((pair) => !kept.includes(pair));
    const figure = ([image, index]) => {
      const fig = make('figure', 'shot' + (cited.has(image.viewId) ? ' cited' : ''));
      const img = make('img'); img.loading = 'lazy'; img.src = image.url; img.alt = image.viewId + (image.role ? ' · ' + image.role : '');
      img.title = '누르면 크게 봅니다 — ← → 넘기기 · Esc 닫기';
      img.addEventListener('click', () => viewer(item, index));
      fig.appendChild(img);
      const cap = make('figcaption');
      cap.appendChild(make('b', null, image.viewId));
      // 골든셋 검수의 caption()처럼 번호 뒤에 « · 종류». 종류가 없는 사진도 표시가 번호 옆 한 줄에 오게 « · »로 잇는다.
      if (image.role) cap.appendChild(document.createTextNode(' · ' + image.role));
      if (cited.has(image.viewId)) cap.appendChild(make('em', null, 'AI 근거'));
      // 골든셋 검수처럼 — AI가 다른 사진을 짚었으면 첫 장에 «대표 사진»을 붙여 견줄 자리를 알린다
      else if (index === 0 && cited.size) cap.appendChild(make('em', 'rep', '대표 사진'));
      fig.appendChild(cap);
      return fig;
    };
    kept.forEach((pair) => box.appendChild(figure(pair)));
    rail.appendChild(box);
    if (rest.length) {
      const more = make('details', 'more-shots'); more.appendChild(make('summary', null, '나머지 사진 ' + rest.length + '장 보기')); more.open = broad;
      const shelf = make('div', 'shots'); rest.forEach((pair) => shelf.appendChild(figure(pair))); more.appendChild(shelf);
      rail.appendChild(more);
    }
    item.text.forEach((row) => { const t = make('div', 'textbox'); t.appendChild(make('b', null, row.name)); t.appendChild(document.createTextNode('\n' + row.value)); rail.appendChild(t); });
    if (item.context.length) rail.appendChild(make('p', 'why', item.context.map((row) => String(row.value)).join(' · ')));
    if (item.missing) rail.appendChild(make('p', 'why', '못 받은 사진 ' + item.missing + '장'));
    return [rail, one];
  }

  // 사진 번호(P01)가 든 AI 문장 — 번호를 누르면 그 사진을 크게 본다(골든셋 검수의 a.pic)
  function said(item, text) {
    const frag = document.createDocumentFragment();
    String(text || '').split(/(P\d{2})/).forEach((part) => {
      const index = /^P\d{2}$/.test(part) ? item.images.findIndex((image) => image.viewId === part) : -1;
      if (index < 0) { frag.appendChild(document.createTextNode(part)); return; }
      const a = make('a', 'pic', part); a.href = '#'; a.addEventListener('click', (event) => { event.preventDefault(); viewer(item, index); });
      frag.appendChild(a);
    });
    return frag;
  }
  // 서버가 골든셋 검수와 같은 함수(_md·_inline)로 그린 정책 글 — 글자는 서버에서 이스케이프됐다. 문자열을 노드로 옮기기만 한다.
  function html(markup, tag = 'div', cls = null) {
    const box = make(tag, cls);
    const doc = new DOMParser().parseFromString('<body>' + (markup || '') + '</body>', 'text/html');
    [...doc.body.childNodes].forEach((node) => box.appendChild(document.importNode(node, true)));
    return box;
  }

  // 칸 — 골든셋 검수의 칸과 같은 모양: 이름 · (AI가 묻는 것) · 이어 붙은 값 선택기(AI 제안 표시) · [보류 … 근거 보기 · 메모 추가] ·
  // 접힌 «판단 근거»(값 설명 · AI가 사진에서 본 것 · 이 항목의 정책) · 답한 칸은 한 줄(값 · 사람 · 바꾸기)
  function cell(item, field) {
    const decided = item.decisions[field.id];
    const done = labeled(decided);
    const held = decided && decided.decision === 'HOLD';
    const ai = aiCell(item, field);
    const usable = aiUsable(item, field) && ai.confidence !== 'LOW' && !(ai.askHuman && ai.askHuman.question);
    const status = !item.reading ? 'NOT_READ' : usable ? 'FILL_PROPOSED' : 'NEEDS_HUMAN_LOOK';
    const box = make('div', 'cell ' + status + (done ? ' done' : '') + (held ? ' held' : '') + (ai && ai.askHuman && ai.askHuman.question ? ' asking' : ''));
    box.dataset.field = field.id;
    const head = make('div', 'c-head');
    head.appendChild(make('h4', null, field.name));
    head.appendChild(make('span', 'badge s-' + status, status === 'NOT_READ' ? 'AI가 못 읽음' : usable ? 'AI 제안' : '직접 봐 주세요'));
    box.appendChild(head);
    if (decided && decided.outOfPolicy) box.appendChild(make('p', 'ask', '정책이 바뀌어 지난 답(' + decided.value + ')이 허용값 밖입니다 — 다시 골라 주세요.'));
    if (ai && ai.askHuman && ai.askHuman.question) {
      const ask = make('p', 'ask'); ask.appendChild(make('b', null, 'AI가 묻는 것'));
      ask.appendChild(document.createTextNode(' — ' + ai.askHuman.question));
      if (ai.askHuman.here) { ask.appendChild(document.createElement('br')); const here = make('span', 'ask-here'); here.appendChild(said(item, ai.askHuman.here)); ask.appendChild(here); }
      box.appendChild(ask);
    }

    // 갈린 칸에서는 «AI 제안» 표시를 두지 않는다 — 세 번째 사람을 AI 쪽으로 기울이지 않게
    const aiCodes = ai && ai.value && !(decided && decided.disputed) ? String(ai.value).split('|') : [];
    const chosen = done ? String(decided.value).split('|') : [];
    let picks = null;
    if (field.many) {
      // 값 여럿 칸 — 사람이 누른 값만 채운다. AI 값은 «AI 제안» 글자로만(확정 전의 값이 기록된 값처럼 보이지 않게).
      PICKS[item.key] = PICKS[item.key] || {};
      if (!PICKS[item.key][field.id]) {
        // 기록된 값이 있으면 그것에서, 없고 AI가 확신한 칸이면 AI 제안을 미리 골라 둔다(아직 기록 전 — 확정 버튼을 눌러야 남는다)
        PICKS[item.key][field.id] = {};
        (done ? chosen : aiSure(item, field) ? aiCodes : []).forEach((c) => { PICKS[item.key][field.id][c] = true; });
      }
      picks = PICKS[item.key][field.id];
    }
    const chips = make('div', 'act chips'); chips.setAttribute('role', 'group'); chips.setAttribute('aria-label', '값 고르기');
    field.labels.forEach((label) => {
      const b = make('button', 'chip' + (aiCodes.includes(label.code) ? ' is-ai' + (usable ? ' primary' : '') : '')); b.type = 'button'; b.dataset.value = label.code;
      b.appendChild(document.createTextNode(label.name));
      // 골든셋 검수처럼 — 칠할 수 있는 제안이면 «AI 제안», AI가 확신 못 한 값이면 «AI 의견»
      if (aiCodes.includes(label.code)) b.appendChild(make('span', 'vmark ai', usable ? 'AI 제안' : 'AI 의견'));
      if (field.many ? picks[label.code] : chosen.includes(label.code)) b.classList.add(field.many && !done ? 'pending' : 'picked');
      b.addEventListener('click', () => {
        if (field.many) { picks[label.code] = !picks[label.code]; b.classList.toggle('pending', !!picks[label.code]); b.classList.remove('picked'); return; }
        const wasDone = itemDone(item);
        b.classList.add('pending');
        decide(item, field, 'LABEL', label.code).then((ok) => { if (ok) refresh(item, wasDone); else b.classList.remove('pending'); });
      });
      chips.appendChild(b);
    });
    box.appendChild(chips);
    const row = make('div', 'act');
    if (field.many) {
      const confirm = make('button', 'confirm-many', '고른 값으로 확정'); confirm.type = 'button';
      confirm.addEventListener('click', () => {
        const picked = Object.keys(picks).filter((k) => picks[k]).sort().join('|');
        if (!picked) { toast('값을 하나 이상 골라 주세요.', true); return; }
        const wasDone = itemDone(item);
        decide(item, field, 'LABEL', picked).then((ok) => { if (ok) refresh(item, wasDone); });
      });
      row.appendChild(confirm);
    }
    const hold = make('button', held ? 'picked' : null, '보류'); hold.type = 'button'; hold.dataset.decision = 'HOLD';
    hold.addEventListener('click', () => { const wasDone = itemDone(item); decide(item, field, 'HOLD', null).then((ok) => { if (ok) refresh(item, wasDone); }); });
    row.appendChild(hold);
    row.appendChild(make('span', 'grow'));
    const why = make('button', 'toggle t-why', '근거 보기'); why.type = 'button'; why.setAttribute('aria-expanded', 'false');
    const memoToggle = make('button', 'toggle t-memo', '메모 추가'); memoToggle.type = 'button'; memoToggle.setAttribute('aria-expanded', 'false');
    row.appendChild(why); row.appendChild(memoToggle);
    box.appendChild(row);
    // AI가 본 것 한 문장 — 골든셋 검수처럼 버튼 줄 아래, 직접 봐야 할 칸에만(제안이 있는 칸은 «근거 보기» 안에 있다)
    if (!usable) {
      const gist = make('p', 'gist');
      const first = String((ai && ai.observation) || '').split(/(?<=[.!?。])\s|(?<=다\.)\s/)[0];
      if (!item.reading) gist.appendChild(document.createTextNode(item.noEvidence ? 'AI가 읽을 사진·글이 없습니다 — 직접 골라 주세요.' : 'AI가 아직 읽지 않았습니다.'));
      else { gist.appendChild(make('b', null, 'AI가 사진에서 본 것')); gist.appendChild(document.createTextNode(' — ')); gist.appendChild(said(item, first || '직접 골라 주세요.')); }
      box.appendChild(gist);
    }

    // 접는 구역 — 메모(이유 · 정책 밖 표시)와 판단 근거. 골든셋 검수의 .more와 같은 모양·같은 글
    const more = make('div', 'more');
    const memo = make('details', 'memo-add'); memo.appendChild(make('summary', null, '이유 · 정책 밖 표시'));
    const notes = make('div', 'act notes');
    const reason = make('input', 'reason'); reason.placeholder = '이유 (선택) — 누르기 전에 적으면 다음 사람이 읽습니다';
    const gapLabel = make('label'); const gap = make('input', 'gap'); gap.type = 'checkbox';
    gapLabel.appendChild(gap); gapLabel.appendChild(document.createTextNode(' 정책이 다루지 않는 경우 — 정책을 고칠 목록에 오릅니다'));
    notes.appendChild(reason); notes.appendChild(gapLabel); memo.appendChild(notes); more.appendChild(memo);
    const grounds = make('details', 'grounds'); grounds.appendChild(make('summary', 'grounds-kicker', '판단 근거 · AI'));
    const noted = aiCodes.filter((code) => (field.valueNotesHtml || {})[code]);
    if (noted.length) {
      const ul = make('ul', 'value-notes');
      noted.forEach((code) => { const li = html(field.valueNotesHtml[code], 'li'); li.insertBefore(document.createTextNode(' — '), li.firstChild); li.insertBefore(make('b', null, labelName(field, code)), li.firstChild); ul.appendChild(li); });
      grounds.appendChild(ul);
    }
    const dl = make('dl');
    dl.appendChild(make('dt', null, 'AI가 사진에서 본 것'));
    const dd = make('dd');
    if (ai && ai.value) { dd.appendChild(make('b', null, labelName(field, ai.value) + (ai.confidence === 'LOW' ? ' (확신 낮음)' : ''))); dd.appendChild(document.createTextNode(' — ')); }
    dd.appendChild(said(item, (ai && ai.observation) || (item.reading ? '(설명 없음)' : 'AI가 아직 읽지 않았습니다.')));
    dl.appendChild(dd);
    grounds.appendChild(dl);
    if (field.definitionHtml) {
      const def = make('details', 'def'); def.appendChild(make('summary', null, '이 항목의 정책 보기')); def.appendChild(html(field.definitionHtml, 'div', 'deftext')); grounds.appendChild(def);
    }
    grounds.open = !!item.reading && !usable;  // 직접 봐야 할 칸은 근거를 펼쳐 둔다(골든셋 검수와 같다)
    more.appendChild(grounds);
    box.appendChild(more);
    [[why, grounds, '근거'], [memoToggle, memo, '메모']].forEach(([button, part, word]) => {
      const sync = () => { button.setAttribute('aria-expanded', String(part.open)); button.textContent = part.open ? word + ' 접기' : (word === '근거' ? '근거 보기' : '메모 추가'); };
      button.addEventListener('click', () => { part.open = !part.open; sync(); });
      part.addEventListener('toggle', sync);
    });

    const result = make('p', 'result');
    const change = make('button', 'change', '바꾸기'); change.type = 'button';
    if (decided && decided.disputed) {
      // 표본 다시 보기에서 두 사람의 답이 갈렸다 — 뒤에 누른 쪽이 이기지 않는다. 세 번째 사람이 고른다.
      const [a, b] = decided.disputed.answers;
      result.classList.add('disputed');
      result.appendChild(make('b', null, '두 분의 답이 갈렸습니다'));
      result.appendChild(document.createTextNode(' — ' + labelName(field, a.value) + '(' + a.reviewer + ') · ' + labelName(field, b.value) + '(' + b.reviewer
        + '). ' + (decided.disputed.parties.includes(reviewer()) ? '두 분이 아닌 다른 분께 부탁해 주세요.' : '사진을 보고 맞는 값을 골라 주세요 — 고른 값이 답이 됩니다.')));
    }
    if (held) {
      // 골든셋 검수와 같은 문장 — «보류 (사람, 날짜) — …»
      result.appendChild(make('b', null, '보류'));
      result.appendChild(document.createTextNode(' (' + decided.reviewer + ', ' + String(decided.decidedAt || '').slice(0, 10) + ') — 보류도 답으로 셉니다. 다시 답하려면 이 칸의 값을 누르세요.'));
    }
    if (done) {
      result.appendChild(make('b', null, labelName(field, decided.value)));
      result.appendChild(document.createTextNode(' · ' + decided.reviewer + (decided.agreedWithAi ? ' · AI와 같음' : '')));
      change.addEventListener('click', () => {
        const opened = box.classList.toggle('open');
        change.textContent = opened ? '접기' : '바꾸기'; change.setAttribute('aria-expanded', String(opened));
      });
    }
    box.appendChild(result);
    if (done) box.appendChild(change);
    // 갈린 두 사람에게는 누를 자리를 끈다 — 눌러도 기록되지 않는 버튼을 두지 않는다(서버도 거절한다)
    if (decided && decided.disputed && decided.disputed.parties.includes(reviewer())) box.querySelectorAll('.act button:not(.toggle)').forEach((b) => { b.disabled = true; });  // 근거 보기·메모 추가는 남긴다(세 번째 분께 이유를 남길 자리)
    return box;
  }

  // 건 — 골든셋 검수의 카드: 번호 · 상태 한 줄 · 키(작은 글씨로 묶음) · 왼쪽 사진, 오른쪽 칸들. 열 때 이미 다 답한 건은 접혀
  // 작은 대표 사진·키·«이 상품 다시 펼치기»만 남는다.
  function card(item, number) {
    const done = itemDone(item);
    const todo = open(item);
    // 보류만 남은 건도 «다 답했습니다» — 골든셋 검수는 보류를 답으로 센다(보류한 칸은 칸 안에 문장으로 남는다)
    const over = done || !todo.length;
    const box = make('section', 'item' + (over ? ' finished' : '') + (!done && over ? ' rested' : '') + (over && FOLDED.has(item.key) ? ' folded' : ''));
    box.dataset.key = item.key;
    // 누른 건이 키보드 A·J의 대상이 된다 — 어느 건을 확정하는지 사람이 고른다
    box.addEventListener('pointerdown', () => { document.querySelectorAll('#list .item.focus').forEach((n) => { if (n !== box) n.classList.remove('focus'); }); box.classList.add('focus'); });
    if (item.images.length) {
      const thumb = make('button', 'fold-thumb'); thumb.type = 'button'; thumb.setAttribute('aria-label', item.key + ' 펼치기');
      const img = make('img'); img.loading = 'lazy'; img.alt = ''; img.src = item.images[0].url; thumb.appendChild(img);
      thumb.addEventListener('click', () => unfold(item));
      box.appendChild(thumb);
    }
    const top = make('p', 'item-top');
    top.appendChild(make('span', 'kicker', number + ' / ' + DATA.items.length));
    top.appendChild(make('span', 'item-state', over ? '다 답했습니다' : !item.reading ? 'AI 추론 전' : '판정할 칸 ' + todo.length + '개'));
    box.appendChild(top);
    const h3 = make('h3', null, item.key + ' ');
    const sub = [item.title, item.group].filter(Boolean).join(' · ');
    if (sub || item.link) {
      const small = make('small', null, sub);
      small.style.cssText = 'color:var(--muted);font-weight:400';  // 골든셋 검수의 키 옆 글씨와 같은 색
      if (item.link) { const a = make('a', null, (sub ? ' · ' : '') + '상품 페이지 ↗'); a.href = item.link; a.target = '_blank'; a.rel = 'noopener'; small.appendChild(a); }
      h3.appendChild(small);
    }
    box.appendChild(h3);
    if (item.looksLikeTest) box.appendChild(make('p', 'notice', '시험 등록 건으로 보입니다(이름에 «테스트»·«구매금지» 등) — 검수할 데이터가 아니면 칸마다 «보류»를 눌러 결과에서 빼 주세요.'));
    const again = make('button', 'reopen', '이 상품 다시 펼치기'); again.type = 'button'; again.hidden = !box.classList.contains('folded');
    again.addEventListener('click', () => unfold(item));
    box.appendChild(again);
    const [rail, one] = shots(item);
    const body = make('div', 'item-body' + (one ? ' one' : ''));
    body.appendChild(rail);
    const decideBox = make('div', 'decide');
    const grid = make('div', 'gt-cell-grid');
    DATA.fields.forEach((f) => grid.appendChild(cell(item, f)));
    decideBox.appendChild(grid);
    if (!over && !blindFor(item) && !(VIEW_MODE === 'recheck' && (RECHECK_CELLS[item.key] || []).length)) {
      // 한 번에 확정 — 증분에서만. 눈가림 다시 보기 중에는 AI를 말하는 줄을 두지 않는다. AI가 확신한 칸만 확정하고, 직접 볼 칸은 몇 개인지 말한다(그 칸은 이 버튼으로 확정되지 않는다)
      const all = make('div', 'item-all');
      const sure = sureOpen(item).length, look = open(item).length - sure;
      all.appendChild(make('span', 'hint', !sure ? 'AI가 확신하지 못한 칸입니다 — 사진을 보고 직접 골라 주세요.'
        : look ? '사진을 보고 AI 제안이 맞으면 확정하세요. «직접 봐 주세요» 칸 ' + look + '개는 이 버튼으로 확정되지 않습니다 — 따로 골라 주세요.'
        : '사진을 보고 AI 제안이 맞으면 한 번에 확정하세요. 보류한 칸은 건드리지 않습니다.'));
      const button = make('button', null, !sure ? '남은 칸은 직접 골라 주세요' : look ? 'AI 제안 ' + sure + '칸 확정' : 'AI 제안대로 모두 확정'); button.type = 'button'; button.disabled = !sure; button.title = '키보드 A';
      button.addEventListener('click', () => acceptAll(item, button));
      all.appendChild(button);
      decideBox.appendChild(all);
    }
    const pending = DATA.fields.filter((f) => (RECHECK_CELLS[item.key] || []).includes(f.id));
    if (pending.length && (VIEW_MODE === 'recheck' || blindFor(item))) {
      // 표본 다시 보기 — 눈가림. 앞 사람의 값·이름과 AI 제안을 가리고 사진만 보고 새로 고른다. 같으면 확인, 다르면 그 값으로 바뀐다.
      grid.replaceWith(blind(item, pending));
    } else if (pending.length) {
      const note = make('div', 'item-all recheck');
      note.appendChild(make('span', 'hint', '다시 보기 대상입니다(한 번에 확정한 칸의 표본, 또는 AI가 사람에게 물은 칸) — 먼저 답한 분이 아닌 다른 분이 위의 «표본 다시 보기»에서 앞 답을 가린 채 다시 고릅니다.'));
      decideBox.appendChild(note);
    }
    body.appendChild(decideBox);
    box.appendChild(body);
    return box;
  }
  // 눈가림 다시 보기 — 칸 이름과 허용값만. AI 표시·앞 판정·근거(AI의 관찰)는 싣지 않는다. 정책은 볼 수 있다(사람이 따를 기준이라).
  function blind(item, pending) {
    const grid = make('div', 'gt-cell-grid');
    grid.appendChild(make('p', 'hint recheck-hint', '표본 다시 보기 — 앞 답과 AI 제안을 가렸습니다. 사진만 보고 맞는 값을 골라 «이 값으로 확인»을 눌러 주세요. 앞 답과 다르면 세 번째 분이 가를 때까지 결과에서 빠집니다.'));
    const confirmer = (RECHECK_BY[item.key] || []).includes(reviewer());
    if (confirmer) {
      // 확정한 본인에게는 고르는 자리를 보이지 않는다 — 눌러야 거절되는 버튼을 두지 않는다
      grid.appendChild(make('p', 'notice', '이 상품은 ' + reviewer() + '님이 먼저 답했습니다 — 다시 보기는 다른 분 차례입니다. 옆자리 분께 이 화면의 «표본 다시 보기»를 부탁해 주세요.'));
      return grid;
    }
    pending.forEach((field) => {
      const box = make('div', 'cell'); box.dataset.field = field.id;
      box.appendChild(make('h4', null, field.name));
      if (field.definitionHtml) {
        const def = make('details', 'def'); def.appendChild(make('summary', null, '이 항목의 정책 보기')); def.appendChild(html(field.definitionHtml, 'div', 'deftext')); box.appendChild(def);
      }
      // 고른 값은 카드를 다시 그려도 남긴다 — 한 칸을 기록하며 카드가 다시 그려질 때 다른 칸의 고름이 풀리지 않게
      BLIND_PICKS[item.key] = BLIND_PICKS[item.key] || {};
      const picks = BLIND_PICKS[item.key][field.id] = BLIND_PICKS[item.key][field.id] || {};
      const chips = make('div', 'act chips'); chips.setAttribute('role', 'group'); chips.setAttribute('aria-label', field.name + ' 다시 고르기');
      const send = (value) => decide(item, field, 'LABEL', value, 'recheck').then((ok) => {
        if (!ok) return;
        const d = item.decisions[field.id] || {};
        toast(d.recheckAgreed ? field.name + ': 앞 답과 같습니다 — 확인했습니다.'
          : field.name + ': 앞 답과 갈렸습니다 — 두 분이 아닌 세 번째 분이 고를 때까지 결과에서 빠집니다.', !d.recheckAgreed);
        RECHECK_CELLS[item.key] = (RECHECK_CELLS[item.key] || []).filter((id) => id !== field.id);
        refresh(item, itemDone(item));
        // 갈림은 원장 전체를 보고 정해진다(응답의 한 줄로는 모른다) — 서버가 센 지금 상태를 곧바로 들인다
        if (!d.recheckAgreed) syncDecisions(true);
      });
      // 고른 뒤 한 번 더 확정한다 — 눈가림 답은 앞 사람의 답을 바꿀 수 있어 한 번 누름으로 뒤집히지 않게
      const go = make('button', 'confirm-many', '이 값으로 확인'); go.type = 'button'; go.disabled = !Object.values(picks).some(Boolean);
      field.labels.forEach((label) => {
        const b = make('button', 'chip' + (picks[label.code] ? ' pending' : ''), label.name); b.type = 'button'; b.dataset.value = label.code;
        b.addEventListener('click', () => {
          if (!field.many) { Object.keys(picks).forEach((k) => { picks[k] = false; }); chips.querySelectorAll('.chip').forEach((c) => c.classList.remove('pending')); }
          picks[label.code] = !picks[label.code]; b.classList.toggle('pending', !!picks[label.code]);
          go.disabled = !Object.values(picks).some(Boolean);
        });
        chips.appendChild(b);
      });
      box.appendChild(chips);
      const row = make('div', 'act');
      go.addEventListener('click', () => {
        const picked = Object.keys(picks).filter((k) => picks[k]).sort().join('|');
        if (!picked) { toast('값을 하나 이상 골라 주세요.', true); return; }
        go.disabled = true; send(picked).finally(() => { go.disabled = false; });
      });
      row.appendChild(go); box.appendChild(row);
      box.appendChild(make('p', 'result'));
      grid.appendChild(box);
    });
    // 갈린 칸은 눈가림 칸 뒤에 숨기지 않는다 — 세 번째 사람이 가를 자리라 두 답과 함께 보인다
    DATA.fields.filter((f) => !pending.includes(f) && (item.decisions[f.id] || {}).disputed).forEach((f) => grid.appendChild(cell(item, f)));
    return grid;
  }
  function unfold(item) {
    FOLDED.delete(item.key);
    const node = cardOf(item);
    if (node) { node.classList.remove('folded'); const again = node.querySelector('.reopen'); if (again) again.hidden = true; }
  }

  const cardOf = (item) => document.querySelector('.item[data-key="' + CSS.escape(item.key) + '"]');
  function refresh(item, wasDone) {
    const old = cardOf(item);
    if (!old) return;
    const fresh = card(item, DATA.items.indexOf(item) + 1);
    if (old.classList.contains('focus')) fresh.classList.add('focus');
    old.replaceWith(fresh);
    // 방금 이 건을 끝냈을 때만 다음 건으로 — 끝난 건의 값을 고치는 중에는 화면을 옮기지 않는다.
    if (!wasDone && itemDone(item)) focusNext(fresh, true);
  }
  function visibleCards() { return [...document.querySelectorAll('#list .item')].filter((n) => n.offsetParent !== null); }
  function focusNext(from, scroll) {
    const list = visibleCards();
    const start = from ? list.indexOf(from) + 1 : 0;
    const next = [...list.slice(start), ...list.slice(0, start)].find((n) => !n.classList.contains('finished') && !n.classList.contains('rested'));
    document.querySelectorAll('#list .item.focus').forEach((n) => n.classList.remove('focus'));
    if (next) { next.classList.add('focus'); if (scroll) next.scrollIntoView({behavior: 'smooth', block: 'start'}); }
    return next;
  }

  // 수는 서버(incr_review.status)가 센 그대로 — 화면이 원장을 다시 세지 않는다. 모양은 골든셋 검수의 진행률 줄:
  // «판정할 칸 답한 / 전체 답함», 막대, «처음 올라온 칸 N — AI 제안 · 직접 봐야 할 …»
  function drawStatus() {
    const s = DATA.status;
    const answered = s.cellsLabeled + s.cellsHeld;
    $('statusline').hidden = false;
    const finished = s.cells > 0 && answered >= s.cells;
    $('progress-short').textContent = finished ? '다 답했습니다' : '판정할 칸 ' + answered + ' / ' + s.cells + ' 답함';
    $('pbar').style.width = (s.cells ? Math.round(answered * 100 / s.cells) : 0) + '%';
    const tally = $('tally'); tally.textContent = '';
    tally.appendChild(make('span', 'tally-kicker', '처음 올라온 칸 ' + s.cells + ' — '));
    const parts = [['AI 제안', s.cellsWithAi, true], ['직접 봐야 함', s.cellsAsking, true],
                   ['AI가 못 읽음', s.cells - s.cellsWithAi - s.cellsAsking, true],
                   // «AI와 같음»은 칸마다 보고 고른 칸에서만 — 한 번에 확정한 칸은 AI 값을 그대로 받은 것이라 따로 센다
                   ['칸마다 보고 AI와 같게', s.cellsIndividual ? Math.round(s.cellsIndividualAgreed * 100 / s.cellsIndividual) + '%' : null, false],
                   ['한 번에 확정', s.cellsBulk || null, false],
                   ['표본 다시 봄', s.recheckItems ? s.recheckedItems + ' / ' + s.recheckItems + '건' : null, false]].filter(([, v]) => v);
    parts.forEach(([k, v, strong], i) => {
      if (i) tally.appendChild(document.createTextNode(' · '));
      if (strong) tally.appendChild(make('b', null, k + ' ' + v)); else tally.appendChild(document.createTextNode(k + ' ' + v));
    });
    const left = s.cells - s.cellsLabeled - s.cellsHeld;  // «답함»과 같은 셈 — 보류는 답한 칸이다
    $('count-left').textContent = left ? String(left) : '';
    $('view-recheck').hidden = !s.recheckItems;
    $('count-recheck').textContent = s.recheckItems ? String(s.recheckItems - s.recheckedItems) : '';
    $('progress-bottom-text').textContent = '판정할 칸 ' + answered + ' / ' + s.cells + ' 답함';
    $('progress-bottom').hidden = finished;  // 다 했으면 떠 있는 막대를 내린다 — 할 일은 위의 «반영하기»가 말한다
  }

  // AI 추론의 상태 — 골든셋 검수의 «다음 후보 받기»와 같은 부품: 도는 중이면 스피너 한 줄(.next-live .spin), 멈췄으면 경고(.notice),
  // 할 일 안내는 흰 한 줄(.once). 색을 여기서 칠하지 않는다.
  function drawRunner() {
    const status = DATA.status, runner = status.runner || {};
    const box = $('runner'), run = $('do-run');
    const readable = status.items - num(status.itemsNoEvidence);
    const fresh = !status.itemsRead;
    run.textContent = fresh ? 'AI 추론 시작' : '다시 읽기';
    // 머리 줄의 버튼은 골든셋 검수처럼 «반영하기» 하나 — AI를 부르는 버튼은 한 번도 안 읽었거나 멈췄을 때만 곁에 둔다.
    // 못 읽은 건은 «다음 후보 받기 →»가 이어서 읽는다.
    run.hidden = !!runner.running || !(fresh || runner.phase === 'failed');
    box.textContent = ''; box.hidden = false;
    const say = (cls, text, spin) => {
      const p = make('p', cls);
      if (spin) { p.appendChild(make('span', 'spin')); const b = make('b', null, text); p.appendChild(b); }
      else p.textContent = text;
      if (spin && runner.running && runner.startedAt) p.appendChild(make('span', 'muted', '(' + Math.max(0, Math.round((Date.now() - Date.parse(runner.startedAt)) / 60000)) + '분째)'));
      box.appendChild(p);
    };
    NEXT_BUSY = !!runner.running;
    // AI가 도는 동안 잠그는 것은 «다음 후보 받기»뿐 — 페이지 넘김은 그대로 둔다(읽는 동안에도 다음 페이지를 볼 수 있게)
    nextButtons().forEach((b) => {
      if (runner.running && !nextStart()) { b.disabled = true; b.textContent = 'AI가 보는 중…'; }
      else if (b.textContent === 'AI가 보는 중…' || b.disabled) { b.disabled = false; b.textContent = nextStart() ? '다음 페이지 →' : NEXT_LABEL; }
    });
    if (runner.running) { say('once next-live', runner.message || 'AI가 읽는 중입니다.', true); run.disabled = true; return true; }
    if (runner.lockBusy) { say('once next-live', '다른 AI 작업(골든셋 검수나 다른 증분)이 도는 중입니다 — 끝나면 다시 눌러 주세요.', true); run.disabled = true; return true; }
    run.disabled = false;
    if (runner.phase === 'failed') say('notice', runner.message || 'AI 추론이 멈췄습니다.');
    else if (fresh) say('once', 'AI가 아직 읽지 않았습니다 — «AI 추론 시작»을 눌러 주세요. 그동안에도 직접 고를 수 있습니다.');
    else box.hidden = true;  // 골든셋 검수처럼 따로 알리지 않는다 — 못 읽은 건은 칸마다 «AI가 못 읽음»으로 보이고 «다음 후보 받기»가 다시 읽는다
    return false;
  }

  function shown() {
    if (VIEW_MODE === 'recheck') return DATA.items.filter((item) => RECHECK.has(item.key) && (RECHECK_CELLS[item.key] || []).length);
    return DATA.items.filter((item) => !ONLY_LEFT || !itemDone(item));
  }
  const at = (item) => item.at;  // 묶음 안의 자리 — drawReview가 한 번 매긴다(찾기를 되풀이하지 않게)
  function pageRows() { return shown().filter((item) => at(item) >= FROM).slice(0, PAGE_SIZE); }
  // 다음 페이지 — 지금 남은 목록이 아니라 **화면에 그린 마지막 건**(LAST_AT) 뒤에서 찾는다. 이 페이지를 끝내면 남은 목록이 줄어
  // 다시 셈하면 뒤쪽 건을 건너뛴다.
  const nextStart = () => shown().find((item) => at(item) > LAST_AT);
  function gotoNextPage() {
    const next = nextStart();
    if (!next) return false;
    FROM = at(next); PAGE_NO += 1; drawItems(); scrollTo({top: 0, behavior: 'smooth'});
    return true;
  }
  function gotoPrevPage() {
    const before = shown().filter((item) => at(item) < FROM);
    FROM = before.length ? at(before[Math.max(0, before.length - PAGE_SIZE)]) : 0;
    PAGE_NO = before.length > PAGE_SIZE ? Math.max(1, PAGE_NO - 1) : 1;
    drawItems(); scrollTo({top: 0, behavior: 'smooth'});
  }
  function drawItems() {
    const list = $('list');
    list.textContent = '';
    const all = shown();
    if (all.length && !all.some((item) => at(item) >= FROM)) FROM = at(all[Math.max(0, all.length - PAGE_SIZE)]);
    const rows = pageRows();
    LAST_AT = rows.length ? at(rows[rows.length - 1]) : FROM - 1;
    if (!all.some((item) => at(item) < FROM)) PAGE_NO = 1;
    const page = PAGE_NO;
    const pages = page - 1 + Math.max(1, Math.ceil(all.filter((item) => at(item) >= FROM).length / PAGE_SIZE));
    // 페이지 넘김 — 골든셋 검수처럼 머리 줄과 아래 알약에. 한 페이지뿐이면 앞 버튼·번호는 숨기고, 다음이 없으면 «다음 페이지»도 숨긴다.
    const paged = all.length > PAGE_SIZE;
    $('page-prev').hidden = !paged; $('page-now').hidden = !paged;
    $('page-now').textContent = page + ' / ' + pages + ' 페이지';
    $('page-prev').disabled = !all.some((item) => at(item) < FROM);
    // 골든셋 검수와 같다 — 마지막 페이지에서는 같은 버튼이 «다음 후보 받기 →»가 된다
    ['page-next', 'page-next-bottom'].forEach((id) => {
      if (NEXT_BUSY && !nextStart()) { $(id).disabled = true; $(id).textContent = 'AI가 보는 중…'; }
      else { $(id).disabled = false; $(id).textContent = nextStart() ? '다음 페이지 →' : NEXT_LABEL; }
    });
    if (!all.length) {
      // 골든셋 검수의 «이번에 볼 칸이 없습니다»와 같은 자리 — 흰 한 줄
      if (!DATA.items.length) list.appendChild(make('p', 'once all-done', '이 묶음에 건이 없습니다.'));
    }
    rows.forEach((item) => list.appendChild(card(item, at(item) + 1)));
    $('next-open').hidden = !DATA.items.some((item) => open(item).length);
    focusNext(null, false);
  }

  // 다음 안 한 건 — 이 페이지에 없으면 다음 페이지로. 둘 다 없으면 말한다(조용히 멈추지 않는다).
  function nextOpen() {
    const node = document.querySelector('#list .item.focus');
    const current = node && inView(node) ? node : null;
    const list = visibleCards();
    const after = current ? list.slice(list.indexOf(current) + 1) : list;
    const todo = (n) => !n.classList.contains('finished') && !n.classList.contains('rested');
    const next = after.find(todo) || (!current && list.find(todo));
    if (next) {
      focusNext(current, false);
      const f = document.querySelector('#list .item.focus');
      if (f) {
        // 골든셋 검수의 «다음 안 한 칸»처럼 — 그 건의 첫 안 한 칸을 화면 가운데로(접혀 있으면 편다)
        const item = DATA.items.find((it) => it.key === f.dataset.key);
        if (item && f.classList.contains('folded')) unfold(item);
        const cellBox = [...f.querySelectorAll('.cell')].find((c) => !c.classList.contains('done') && !c.classList.contains('held'));
        (cellBox || f).scrollIntoView({behavior: 'smooth', block: cellBox ? 'center' : 'start'});
      }
      return;
    }
    // 이 페이지에 안 한 건이 없으면 다음 페이지로(골든셋 검수의 «다음 안 한 칸»처럼 멈추지 않는다)
    if (gotoNextPage()) return;
    // 남은 건이 앞 페이지에 있으면 그 건으로 곧장 간다 — 사람이 페이지를 넘기며 찾지 않게
    const all = shown();
    const back = all.find((item) => !itemDone(item) && open(item).length);
    if (back) {
      const index = all.indexOf(back);
      FROM = at(all[index - (index % PAGE_SIZE)]); PAGE_NO = Math.floor(index / PAGE_SIZE) + 1; drawItems();
      const node = cardOf(back);
      if (node) {
        document.querySelectorAll('#list .item.focus').forEach((n) => n.classList.remove('focus')); node.classList.add('focus');
        const cellBox = [...node.querySelectorAll('.cell')].find((c) => !c.classList.contains('done') && !c.classList.contains('held'));
        (cellBox || node).scrollIntoView({behavior: 'smooth', block: 'center'});
      }
      return;
    }
    // 남은 건이 보류한 칸뿐이면 앞 페이지로 가도 할 일이 없다 — 그렇게 말한다.
    const anyOpen = DATA.items.some((item) => !itemDone(item) && open(item).length);
    toast(!DATA.status.itemsLeft ? '판정할 칸이 남지 않았습니다 — «반영하기»를 눌러 주세요.'
      : !anyOpen ? '남은 칸은 보류한 칸뿐입니다 — 보류를 풀려면 그 칸의 값을 골라 주세요.'
      : '이 페이지에는 남은 건이 없습니다 — 앞 페이지를 봐 주세요.', !!DATA.status.itemsLeft);
  }

  // «다음 후보 받기 →» — 골든셋 검수의 같은 버튼. 증분에서 «다음 후보»는 ① 이 과제에서 남은 건이 있는 다른 묶음, ② 없으면 이 묶음에서
  // AI가 아직 못 읽은 건(AI 추론을 띄운다), ③ 둘 다 없으면 «남은 후보 없음» — 새 데이터는 Claude가 넣는다(말할 문장을 알린다).
  const NEXT_LABEL = '다음 후보 받기 →';
  let NEXT_BUSY = false;
  const nextButtons = () => [$('page-next'), $('page-next-bottom')];
  const nextOut = () => { const out = $('export-out'); out.hidden = false; out.textContent = ''; return out; };
  async function goNext() {
    if (nextStart()) return gotoNextPage();
    const s = DATA.status;
    // ① 이 묶음에 AI가 아직 못 읽은 건이 있으면 그것부터 — 이 묶음의 다음 후보다(골든셋 검수의 «다음 후보 받기»가 판독을 띄우는 것과 같다)
    const readable = s.items - num(s.itemsNoEvidence);
    if (s.itemsReadComplete < readable && !(s.runner || {}).running) { $('do-run').click(); return; }
    // ② 남은 건이 있는 다른 묶음 — 이 묶음 다음 것부터(목록은 최근 것이 먼저)
    const left = s.cells - s.cellsLabeled - s.cellsHeld;
    let all = null;
    try { all = await (await fetch('/incr-tasks', {cache: 'no-store'})).json(); } catch (e) { all = null; }
    const mine = ((all && all.tasks) || []).find((task) => task.task === TASK) || {batches: []};
    const order = mine.batches || [];
    const here = order.findIndex((b) => b.batch === DATA.batch);
    const next = [...order.slice(here + 1), ...order.slice(0, Math.max(0, here))].find((b) => b.itemsLeft);
    if (next) {
      // 답하지 않은 칸은 이 묶음에 그대로 남지만, 모르고 넘어가지 않게 한 번 묻는다(골든셋 검수와 같은 물음)
      if (left && !confirm('이 화면에 답하지 않은 칸이 ' + left + '개 있습니다. 다음 묶음으로 넘어갈까요?\n(답하지 않은 칸은 이 묶음에 남습니다)')) return;
      location.href = '/incr?task=' + encodeURIComponent(TASK) + '&batch=' + encodeURIComponent(next.batch);
      return;
    }
    // ③ 더 없다 — 버튼을 잠그고 새 데이터를 넣는 말을 알린다
    nextButtons().forEach((b) => { b.disabled = true; b.textContent = '남은 후보 없음'; });
    const out = nextOut();
    const p = make('p'); p.appendChild(make('b', null, left ? '다른 묶음에 남은 후보가 없습니다.' : '이 과제의 증분 후보를 모두 봤습니다.'));
    p.appendChild(document.createTextNode((left ? ' 이 화면의 답하지 않은 칸 ' + left + '개를 마저 답하거나,' : '') + ' 새 데이터를 넣으려면 Claude에게 '));
    p.appendChild(make('code', 'phrase', DATA.name + ' 증분 넣어줘'));
    p.appendChild(document.createTextNode('라고 말해 주세요.'));
    out.appendChild(p);
    out.scrollIntoView({block: 'center', behavior: 'smooth'});
  }


  function drawReview(data) {
    DATA = data;
    DATA.items.forEach((item, index) => { item.at = index; });
    RECHECK = new Set(data.recheckKeys || []); RECHECK_CELLS = data.recheckCells || {}; RECHECK_BY = data.recheckBy || {};
    // 열 때 이미 다 답한 건은 접는다(골든셋 검수의 foldFinished) — 답하는 도중에 끝낸 건은 접지 않는다(누른 칸이 눈앞에서 사라지지 않게)
    FOLDED.clear();
    DATA.items.forEach((item) => { if (itemDone(item) || !open(item).length) FOLDED.add(item.key); });
    document.title = '데이터 운영 · ' + data.name + ' 증분 검수';
    $('title').textContent = data.name + ' · 증분 검수';
    $('crumb-task').textContent = ' / ' + data.name + (data.unit ? ' · ' + data.unit + ' 단위' : '');
    // 골든셋 검수의 «만든 시각»과 같은 모양 — 여기서는 증분을 밀어넣은 시각
    $('made').textContent = (data.status.meta || {}).pushedAt ? '넣은 시각 ' + new Date(data.status.meta.pushedAt).toLocaleString('ko-KR') : '';
    $('title-side').hidden = false;
    const pick = $('batch-pick'); pick.textContent = '';
    data.batches.forEach((b) => { const o = make('option', null, batchLabel(b)); o.value = b; o.selected = b === data.batch; pick.appendChild(o); });
    drawItems();
    drawStatus();
    if (drawRunner()) schedule(8000);
    if (!SYNCING) { SYNCING = true; setTimeout(() => syncDecisions(false), 30000); }
  }

  // AI가 끝나면 **바뀐 카드만** 다시 그린다 — 보던 자리·누르던 값 여럿 칸을 잃지 않게. 판정은 이 화면이 가진 것이 더 새것이다.
  function merge(data) {
    const byKey = new Map(DATA.items.map((item) => [item.key, item]));
    data.items.forEach((fresh) => {
      const item = byKey.get(fresh.key);
      if (!item) return;
      const before = JSON.stringify([item.reading, item.images, item.notPrepared]);
      Object.assign(item, {reading: fresh.reading, images: fresh.images, notPrepared: fresh.notPrepared, noEvidence: fresh.noEvidence,
                           context: fresh.context, text: fresh.text, missing: fresh.missing});
      if (before !== JSON.stringify([item.reading, item.images, item.notPrepared])) refresh(item, itemDone(item));
    });
    DATA.status = data.status;
    drawStatus();
  }
  // 다른 사람의 답을 들인다 — 30초마다(또는 부딪혔을 때 곧바로) 원장을 다시 읽어 바뀐 칸의 카드만 다시 그린다
  function syncDecisions(now) {
    if (!DATA || !TASK) return;
    fetch('/incr-data?task=' + encodeURIComponent(TASK) + '&batch=' + encodeURIComponent(DATA.batch), {cache: 'no-store'})
      .then((r) => r.json()).then((data) => {
        if (!data.ok) return;
        const byKey = new Map(data.items.map((item) => [item.key, item]));
        DATA.items.forEach((item) => {
          const fresh = byKey.get(item.key);
          if (fresh && JSON.stringify(fresh.decisions) !== JSON.stringify(item.decisions)) {
            item.decisions = fresh.decisions;
            if (PICKS[item.key]) delete PICKS[item.key];
            refresh(item, true);
          }
        });
        RECHECK = new Set(data.recheckKeys || []); RECHECK_CELLS = data.recheckCells || {}; RECHECK_BY = data.recheckBy || {};
        DATA.status = data.status; drawStatus();
      }).catch(() => {});
    if (!now) setTimeout(() => syncDecisions(false), 30000);
  }

  function schedule(ms) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(() => {
      fetch('/incr-data?task=' + encodeURIComponent(TASK) + '&batch=' + encodeURIComponent(DATA.batch), {cache: 'no-store'})
        .then((r) => r.json())
        .then((data) => {
          if (!data.ok) { schedule(15000); return; }
          merge(data);
          const busy = drawRunner();
          const runner = data.status.runner || {};
          // 막 띄운 러너가 아직 제 상태를 쓰기 전이면 조금 더 본다 — 한 번 «안 돈다»로 읽고 멈추지 않게.
          const waiting = WATCH && Date.now() < WATCH.until && runner.pid !== WATCH.pid;
          if (busy || waiting) schedule(busy ? 8000 : 2000); else WATCH = null;
        }).catch(() => { if (DATA) drawRunner(); schedule(15000); });
    }, ms);
  }

  // 크게 보기 — 골든셋 검수의 #viewer와 같은 틀: ← → 넘기기, Esc 닫기, 사진을 누르면 원래 크기
  let VIEW = null;
  function viewer(item, index) {
    VIEW = {item, index};
    const box = $('viewer'), img = box.querySelector('img');
    const image = item.images[index];
    img.src = image.url; img.classList.remove('full');
    // 사진 칸과 같은 규칙 — 여섯 장 넘게(또는 전부) 짚었으면 짚은 것이 아니다
    const citedAll = new Set(); DATA.fields.forEach((f) => ((aiCell(item, f) || {}).evidenceImageIds || []).forEach((id) => citedAll.add(String(id))));
    const citedHere = !blindFor(item) && citedAll.size <= 6 && citedAll.size < item.images.length && citedAll.has(image.viewId);
    box.querySelector('p').textContent = image.viewId + (image.role ? ' · ' + image.role : '') + (citedHere ? ' · AI 근거' : '') + ' (' + (index + 1) + ' / ' + item.images.length + ')';
    box.hidden = false;
  }
  function go(step) { if (VIEW && VIEW.item.images.length) viewer(VIEW.item, (VIEW.index + step + VIEW.item.images.length) % VIEW.item.images.length); }
  $('viewer').addEventListener('click', (event) => {
    if (event.target.closest('.go')) { go(Number(event.target.closest('.go').dataset.go)); return; }
    if (event.target.tagName === 'IMG') { event.target.classList.toggle('full'); return; }
    $('viewer').hidden = true; VIEW = null;
  });

  // 화면에 절반 넘게 보이는가 — 위에 조금 걸친 건을 A가 확정하지 않게(보이는 높이 ÷ min(건 높이, 화면 높이))
  const inView = (node) => {
    const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    const box = node.getBoundingClientRect(), height = innerHeight / zoom;
    const shown = Math.min(box.bottom, height) - Math.max(box.top, 0);
    return shown > 0 && shown >= Math.min(box.height, height) * 0.5;
  };
  document.addEventListener('keydown', (event) => {
    if (!$('viewer').hidden) {
      if (event.key === 'Escape') { $('viewer').hidden = true; VIEW = null; }
      if (event.key === 'ArrowLeft') go(-1);
      if (event.key === 'ArrowRight') go(1);
      return;
    }
    // 한글 입력 중에도 되도록 글쇠 자리(code)로 본다. 수정 글쇠(⌘A 전체 선택 같은)와 누르고 있는 반복은 무시한다.
    if (!DATA || event.metaKey || event.ctrlKey || event.altKey || event.repeat || event.isComposing) return;
    const tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || event.target.isContentEditable) return;
    const node = document.querySelector('#list .item.focus');
    if (event.code === 'KeyA') {
      if (!node || !inView(node)) { toast('확정할 건(파란 테두리)이 화면에 절반 넘게 보이지 않습니다 — J로 그 건으로 가거나 건을 눌러 고르세요.', true); return; }
      const item = DATA.items.find((it) => it.key === node.dataset.key);
      const button = node.querySelector('.item-all button');
      if (item && button && !button.disabled) acceptAll(item, button);
      else if (item) toast(itemDone(item) ? '이 건은 이미 확정했습니다 — J로 다음 건으로 가세요.' : 'AI가 확신한 칸이 없습니다 — 사진을 보고 칸마다 직접 골라 주세요.', true);
    }
    if (event.code === 'KeyJ') nextOpen();
  });

  // 머리의 버튼들 — 한 번만 건다.
  // 이름은 이 탭에서만 기억한다 — 컴퓨터를 같이 쓰면 앞사람 이름으로 기록되지 않게. 지난 이름들은 고르기 목록으로만 준다.
  try {
    $('reviewer').value = sessionStorage.getItem(REVIEWER_KEY) || '';
    const recent = JSON.parse(localStorage.getItem(REVIEWER_KEY + '-recent') || '[]');
    const list = make('datalist'); list.id = 'reviewer-recent';
    recent.forEach((name) => { const o = make('option'); o.value = name; list.appendChild(o); });
    document.body.appendChild(list); $('reviewer').setAttribute('list', 'reviewer-recent');
  } catch (e) {}
  $('reviewer').addEventListener('focus', () => $('reviewer').select());  // 다시 적으면 덮어쓴다(«숙련자숙련자»가 되지 않게)
  $('reviewer').addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === 'Escape') $('reviewer').blur(); });
  $('reviewer').addEventListener('change', () => {
    try {
      sessionStorage.setItem(REVIEWER_KEY, reviewer());
      const recent = JSON.parse(localStorage.getItem(REVIEWER_KEY + '-recent') || '[]').filter((name) => name !== reviewer());
      if (reviewer()) localStorage.setItem(REVIEWER_KEY + '-recent', JSON.stringify([reviewer(), ...recent].slice(0, 8)));
    } catch (e) {}
    if (reviewer() && PENDING.length) {
      const queued = PENDING.splice(0);
      queued.reduce((chain, run) => chain.then(run), Promise.resolve());
    }
    // 눈가림은 누가 보느냐에 달렸다 — 이름이 바뀌면 표본 건을 다시 그린다
    if (Object.values(RECHECK_CELLS).some((cells) => cells.length)) drawItems();
  });
  $('batch-pick').addEventListener('change', (event) => {
    location.href = '/incr?task=' + encodeURIComponent(TASK) + '&batch=' + encodeURIComponent(event.target.value);
  });
  document.querySelectorAll('.views button').forEach((button) => button.addEventListener('click', () => {
    ONLY_LEFT = button.dataset.view === 'left';
    VIEW_MODE = button.dataset.view;
    if (VIEW_MODE === 'recheck') DATA.items.forEach((item) => { if (RECHECK.has(item.key)) FOLDED.delete(item.key); });
    document.querySelectorAll('.views button').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    FROM = 0; PAGE_NO = 1; drawItems();
  }));
  $('page-prev').addEventListener('click', gotoPrevPage);
  $('page-next').addEventListener('click', goNext);
  $('page-next-bottom').addEventListener('click', goNext);
  $('next-open').addEventListener('click', nextOpen);
  $('do-run').addEventListener('click', () => {
    const run = $('do-run'); run.disabled = true;
    post('/incr-run', {task: TASK, batch: DATA.batch, reread: DATA.status.itemsRead > 0}).then((reply) => {
      if (!reply.ok) { toast(reply.error || '시작하지 못했습니다.', true); run.disabled = false; return; }
      toast('AI가 읽기 시작했습니다. 끝나면 이 화면에 제안이 채워집니다.');
      WATCH = {pid: reply.pid, until: Date.now() + 60000};
      schedule(2000);
    });
  });
  $('do-export').addEventListener('click', () => {
    const button = $('do-export'); button.disabled = true;
    post('/incr-export', {task: TASK, batch: DATA.batch}).then((reply) => {
      button.disabled = false;
      const out = $('export-out'); out.hidden = false; out.textContent = '';
      if (!reply.ok) {
        out.appendChild(make('p', null, '반영하지 못했습니다 — ' + (reply.error || '다시 눌러 주세요.')));
        if (reply.recheckPending) {
          const go = make('button', null, '표본 다시 보기로 가기'); go.type = 'button'; go.addEventListener('click', () => $('view-recheck').click()); out.appendChild(go);
          out.appendChild(make('p', 'muted', '다시 봐 줄 분이 없으면 Claude에게 «' + DATA.name + ' 표본 없이 반영해줘»라고 말해 주세요 — 표본을 건너뛴 사실이 결과 파일에 남습니다.'));
        }
        return;
      }
      if (!reply.labeled) {
        out.appendChild(make('p', null, '아직 모든 칸을 끝낸 상품이 없어 결과 파일에 쓴 것이 없습니다 — 상품마다 모든 칸을 답한 뒤 다시 눌러 주세요(보류한 칸이 있는 상품은 빠집니다).'));
        return;
      }
      const p1 = make('p'); p1.appendChild(make('b', null, '반영했습니다')); p1.appendChild(document.createTextNode(' — 모든 칸을 확정한 ' + reply.labeled + '건을 결과 파일에 썼습니다'
        + (reply.pending ? ' (아직 답하지 않은 ' + reply.pending + '건은 빠졌습니다)' : '') + '.'
        + (reply.disputedRows ? ' 두 분의 답이 갈려 세 번째 분을 기다리는 ' + reply.disputedRows + '건도 빠졌습니다.' : '')));
      out.appendChild(p1);
      out.appendChild(make('p', null, '결과 파일은 담당 개발자가 가져갑니다 — 바꾼 판정이 있으면 «반영하기»를 다시 눌러 주세요.'));
      const where = make('details', 'meta'); where.appendChild(make('summary', null, '기록용 정보')); where.appendChild(make('p', null, reply.file));
      out.appendChild(where);
      DATA.status.labeledFile = reply.file;
    });
  });

  if (location.protocol === 'file:') { $('list').textContent = '이 파일을 직접 열었습니다. 서버(./serve.sh start)를 켜고 http://127.0.0.1:7391/incr 로 열어 주세요.'; return; }
  const url = TASK ? '/incr-data?task=' + encodeURIComponent(TASK) + (BATCH ? '&batch=' + encodeURIComponent(BATCH) : '') : '/incr-tasks';
  fetch(url, {cache: 'no-store'})
    .then((r) => r.json())
    .then((data) => {
      if (data.ok === false && TASK) {
        // 증분이 아직 없는 과제 — 막다른 문장 대신 목록 화면의 그 과제 카드(넣는 법 안내)를 보인다.
        return fetch('/incr-tasks', {cache: 'no-store'}).then((r) => r.json()).then((all) => {
          const rows = (all.tasks || []).filter((task) => task.task === TASK);
          if (!rows.length) { $('list').textContent = ''; $('list').appendChild(make('p', 'notice', data.error || '읽지 못했습니다.')); return; }
          $('title').textContent = rows[0].name + ' · 증분 검수';
          $('crumb-task').textContent = ' / ' + rows[0].name;
          drawList({tasks: rows});
        });
      }
      if (data.ok === false) { $('list').textContent = ''; $('list').appendChild(make('p', 'notice', data.error || '읽지 못했습니다.')); return; }
      if (TASK) drawReview(data); else drawList(data);
    })
    .catch(() => { $('list').textContent = ''; $('list').appendChild(make('p', 'notice', '증분 검수를 읽지 못했습니다. 서버가 켜져 있는지 확인해 주세요.')); });
})();
"""

NOTE = ("<!--\n  생성된 파일 — 손으로 고치지 않는다. 고칠 자리는 engine/scripts/incr_render.py이고, 고친 뒤 «python3 incr_render.py»로 다시 쓴다.\n"
        "  모양은 골든셋 검수 화면과 같은 상수(page_style.head · EXTRA_STYLE · THEME_STYLE · SIDEBAR_STYLE · LIST_STYLE)에서 온다.\n"
        "  건·수는 전부 /incr-tasks · /incr-data(JSON)에서 읽는다 — 서버는 이 파일을 그대로 보낸다.\n-->\n")


def page() -> str:
    html = head("데이터 운영 · 증분 검수").replace("</head>", FONT + "</head>", 1)
    html = html.replace("<head>\n", "<head>\n" + NOTE, 1)
    html = html.replace("</style>", EXTRA_STYLE + THEME_STYLE + SIDEBAR_STYLE + LIST_STYLE + INCR_STYLE + "</style>", 1)
    return html + BODY.format(sidebar=sidebar_html("incr", None)) + f"<script>{SCRIPT}{SIDEBAR_SCRIPT}</script>\n</body></html>\n"


def main() -> int:
    text = page()
    if "--check" in sys.argv[1:]:
        same = TARGET.is_file() and TARGET.read_text(encoding="utf-8") == text
        print("같습니다" if same else f"다릅니다 — python3 {Path(__file__).name}로 다시 쓰세요")
        return 0 if same else 1
    TARGET.write_text(text, encoding="utf-8")
    print(TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
