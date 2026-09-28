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
.keys{font-size:12.5px;color:var(--faint);white-space:nowrap}
.keys kbd{font:inherit;font-size:11.5px;font-weight:600;padding:1px 6px;border-radius:6px;background:#F1F5F9;color:var(--muted);box-shadow:inset 0 -1px 0 var(--rule)}
@media (max-width:640px){.item-all button{width:100%}.keys{display:none}}
.item.focus{box-shadow:0 0 0 2px var(--accent-soft),var(--shadow)}  /* 키보드 A가 확정할 건 — 골든셋 검수의 카드처럼 조용히 */
.toast{position:fixed;left:50%;bottom:72px;transform:translateX(-50%);z-index:50;padding:10px 16px;border-radius:10px;background:var(--ink);color:#fff;font-size:13.5px;max-width:80vw;box-shadow:var(--shadow)}
.toast.bad{background:var(--bad)}
.loading{color:var(--faint);padding:26px 0}
.table td.go{text-align:right}
.once.all-done{margin-top:24px}
"""

BODY = """<div class="shell">{sidebar}<div class="wrap">
<header class="masthead">
  <div class="masthead-top"><p class="kicker crumbs"><a href="/">홈</a> / <a href="/incr">증분 검수</a><span id="crumb-task"></span></p><p id="made"></p></div>
  <div class="title-row"><h1 id="title">증분 검수</h1><div class="title-side" id="title-side" hidden><nav class="pager" aria-label="페이지"><button type="button" id="page-prev" aria-label="이전 페이지" hidden>‹</button><span id="page-now" hidden></span><button type="button" id="page-next" hidden>다음 페이지 →</button></nav><select id="batch-pick" aria-label="증분 묶음"></select><label class="who">판정하는 사람 <input id="reviewer" placeholder="이름"></label></div></div>
  <p class="lead" id="lead" hidden>새로 들어온 데이터를 AI가 먼저 읽어 두었습니다. 묶음을 열어 AI 제안이 맞으면 그대로 누르고, 다르면 맞는 값을 누르세요. 누른 판정만 기록됩니다 — AI 제안은 사람이 누르기 전에는 기록되지 않습니다.</p>
  <div id="runner" hidden aria-live="polite"></div>
  <div class="statusline" id="statusline" hidden>
    <div class="progress-block">
      <div id="progress-short"></div>
      <div class="pbar" aria-hidden="true"><i id="pbar"></i></div>
      <div class="tally" id="tally"></div>
    </div>
    <div class="status-side">
      <div class="views" role="group" aria-label="보기">
        <button type="button" data-view="all" aria-pressed="false">전체</button>
        <button type="button" data-view="left" aria-pressed="true">남은 건 <span class="count" id="count-left"></span></button>
      </div>
      <button type="button" id="do-run">AI 추론 시작</button>
      <button type="button" id="do-export" title="모든 칸을 확정한 건을 라벨 붙은 파일로 씁니다">결과 파일 만들기</button>
    </div>
  </div>
</header>
<div id="list"><p class="loading">증분 검수를 읽는 중…</p></div>
<div id="progress-bottom" hidden aria-live="polite"><span id="progress-bottom-text"></span>
  <span class="keys"><kbd>A</kbd> 제안대로 확정 · <kbd>J</kbd> 다음 건</span>
  <span class="bottom-actions"><button type="button" id="next-open">다음 안 한 건 ↓</button></span></div>
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
    .then((r) => r.json().catch(() => ({ok: false, error: '서버 응답을 읽지 못했습니다.'})))
    .catch(() => ({ok: false, error: '서버에 닿지 못했습니다 — 서버가 켜져 있는지 확인해 주세요(Claude에게 «서버 다시 켜줘»).'}));

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
        ['묶음', '넣은 시각', '진행', 'AI', ''].forEach((h) => headRow.appendChild(make('th', null, h)));
        const thead = make('thead'); thead.appendChild(headRow); table.appendChild(thead);
        const tbody = make('tbody');
        task.batches.forEach((b) => {
          const row = make('tr');
          const [tone, word] = batchState(b);  // 정책·골든셋 표와 같은 알약(.pill ok·to·out)
          row.appendChild(make('td', 'key', b.batch));
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
  let DATA = null, ONLY_LEFT = true, FROM = 0, pollTimer = null, WATCH = null;
  const PENDING = [];  // 이름 없이 누른 판정들 — 이름을 적으면 누른 차례대로 기록한다
  const INFLIGHT = new Map();  // 보내는 중인 판정(칸·값) — 같은 판정이 겹쳐 오면 한 번만 보낸다
  let LAST_AT = -1;
  let PAGE_NO = 1;  // 몇 번째 페이지인가 — 넘긴 횟수로 센다(«남은 건» 보기에서는 끝낸 건이 빠져 자리로 셀 수 없다)  // 지금 페이지에 그린 마지막 건의 자리 — 다음 페이지는 그 뒤에서 시작한다
  // 페이지는 번호가 아니라 «묶음 안의 몇 번째 건부터»(FROM)로 센다 — «남은 건» 보기에서 한 페이지를 끝내면 목록이 줄어들어,
  // 번호로 넘기면 끝낸 수만큼 건을 건너뛴다.
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
  const labeled = (d) => !!d && d.decision === 'LABEL' && !d.outOfPolicy;
  const itemDone = (item) => DATA.fields.every((f) => labeled(item.decisions[f.id]));
  const open = (item) => DATA.fields.filter((f) => { const d = item.decisions[f.id]; return !d || d.outOfPolicy; });
  const acceptable = (item) => { const todo = open(item); return todo.length > 0 && todo.every((f) => aiUsable(item, f)); };

  function decide(item, field, decision, value, bulk) {
    // 이미 기록된 것과 같은 판정은 다시 보내지 않는다 — 이름을 적는 순간 대기열이 기록되고 같은 값을 또 누르면 원장에 같은 줄이 둘 생긴다.
    const last = item.decisions[field.id];
    if (last && last.decision === decision && String(last.value ?? '') === String(value ?? '')) return Promise.resolve(true);
    const flight = [item.key, field.id, decision, value ?? ''].join('\u0000');
    if (INFLIGHT.has(flight)) return INFLIGHT.get(flight);
    const sent = send(item, field, decision, value, bulk).finally(() => INFLIGHT.delete(flight));
    if (reviewer()) INFLIGHT.set(flight, sent);
    return sent;
  }
  function send(item, field, decision, value, bulk) {
    if (!reviewer()) {
      // 누른 값을 버리지 않는다 — 이름을 적으면 그 판정을 그대로 이어서 기록한다.
      toast('맨 위에 이름을 적으면 방금 누른 값이 기록됩니다.', true); $('reviewer').focus();
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
    return post('/incr-decide', {task: TASK, batch: DATA.batch, key: item.key, field: field.id, decision, value,
                                 reviewer: reviewer(), bulk: !!bulk, expectedAi: aiValue(item, field)})
      .then((reply) => {
        if (!reply.ok) { toast(reply.error || '기록하지 못했습니다.', true); return false; }
        item.decisions[field.id] = reply.decision;
        // 값 여럿 칸의 누른 값은 기록된 값에서 다시 시작한다 — 한 번에 확정·보류로 칸이 바뀌면 옛 선택이 남지 않게.
        if (PICKS[item.key]) delete PICKS[item.key][field.id];
        if (reply.status) { DATA.status = reply.status; drawStatus(); }
        return true;
      });
  }

  function acceptAll(item, button) {
    // 보류한 칸은 사람이 일부러 멈춘 칸이다 — 한 번에 확정에서 뺀다. 확정된 칸도 건드리지 않는다.
    if (!acceptable(item)) return Promise.resolve();
    const wasDone = itemDone(item);
    if (button) button.disabled = true;
    let chain = Promise.resolve(true);
    open(item).forEach((f) => { chain = chain.then((ok) => ok && decide(item, f, 'LABEL', aiValue(item, f), true)); });
    return chain.then((ok) => { if (ok) toast('AI 제안대로 확정했습니다.'); }).finally(() => {
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
    const cited = new Set();
    DATA.fields.forEach((f) => ((aiCell(item, f) || {}).evidenceImageIds || []).forEach((id) => cited.add(String(id))));
    if (!item.images.length) {
      rail.appendChild(make('p', 'why', item.notPrepared ? '사진 준비 전 — «AI 추론 시작»을 누르면 사진이 나옵니다. 그동안에도 직접 고를 수 있습니다.'
        : item.noEvidence ? '사진도 글도 없는 건입니다 — AI가 읽지 않았습니다. 직접 골라 주세요.' : '사진이 없습니다.'));
    }
    item.images.forEach((image, index) => {
      const fig = make('figure', 'shot' + (cited.has(image.viewId) ? ' cited' : ''));
      const img = make('img'); img.loading = 'lazy'; img.src = image.url; img.alt = image.viewId + (image.role ? ' · ' + image.role : '');
      img.title = '누르면 크게 봅니다 — ← → 넘기기 · Esc 닫기';
      img.addEventListener('click', () => viewer(item, index));
      fig.appendChild(img);
      const cap = make('figcaption');
      cap.appendChild(make('b', null, image.viewId));
      // 골든셋 검수의 caption()처럼 번호 뒤에 « · 종류». 종류가 없는 사진도 표시가 번호 옆 한 줄에 오게 « · »로 잇는다.
      cap.appendChild(document.createTextNode(image.role ? ' · ' + image.role : (cited.has(image.viewId) || (index === 0 && cited.size) ? ' · ' : '')));
      if (cited.has(image.viewId)) cap.appendChild(make('em', null, 'AI 근거'));
      // 골든셋 검수처럼 — AI가 다른 사진을 짚었으면 첫 장에 «대표 사진»을 붙여 견줄 자리를 알린다
      else if (index === 0 && cited.size) cap.appendChild(make('em', 'rep', '대표 사진'));
      fig.appendChild(cap);
      box.appendChild(fig);
    });
    rail.appendChild(box);
    item.text.forEach((row) => { const t = make('div', 'textbox'); t.appendChild(make('b', null, row.name)); t.appendChild(document.createTextNode('\n' + row.value)); rail.appendChild(t); });
    if (item.context.length) rail.appendChild(make('p', 'why', item.context.map((row) => String(row.value)).join(' · ')));
    if (item.missing) rail.appendChild(make('p', 'why', '못 받은 사진 ' + item.missing + '장'));
    return [rail, one];
  }

  // 칸 — 골든셋 검수의 칸과 같은 모양: 이름 · AI가 본 것 · 이어 붙은 값 선택기(AI 제안에 파란 점) · 보류 · 답한 칸은 한 줄
  function cell(item, field) {
    const decided = item.decisions[field.id];
    const done = labeled(decided);
    const held = decided && decided.decision === 'HOLD';
    const ai = aiCell(item, field);
    const box = make('div', 'cell' + (done ? ' done' : '') + (held ? ' held' : '') + (ai && ai.askHuman && ai.askHuman.question ? ' asking' : ''));
    box.dataset.field = field.id;
    const head = make('div', 'c-head');
    head.appendChild(make('h4', null, field.name));
    head.appendChild(make('span', 'badge', done ? '답함' : held ? '보류' : ai && ai.value ? (ai.confidence === 'LOW' ? 'AI 확신 낮음' : 'AI 제안 있음') : '직접 골라 주세요'));
    box.appendChild(head);
    if (decided && decided.outOfPolicy) box.appendChild(make('p', 'ask', '정책이 바뀌어 지난 답(' + decided.value + ')이 허용값 밖입니다 — 다시 골라 주세요.'));
    if (ai && ai.askHuman && ai.askHuman.question) {
      const ask = make('p', 'ask'); ask.appendChild(make('b', null, 'AI가 묻는 것'));
      ask.appendChild(document.createTextNode(' — ' + ai.askHuman.question));
      if (ai.askHuman.here) ask.appendChild(make('span', 'ask-here', ai.askHuman.here));
      box.appendChild(ask);
    }
    const aiCodes = ai && ai.value ? String(ai.value).split('|') : [];
    const chosen = done ? String(decided.value).split('|') : [];
    let picks = null;
    if (field.many) {
      // 값 여럿 칸 — 사람이 누른 값만 채운다. AI 값은 «AI 제안» 글자로만(확정 전의 값이 기록된 값처럼 보이지 않게).
      PICKS[item.key] = PICKS[item.key] || {};
      if (!PICKS[item.key][field.id]) { PICKS[item.key][field.id] = {}; chosen.forEach((c) => { PICKS[item.key][field.id][c] = true; }); }
      picks = PICKS[item.key][field.id];
    }
    // 값 선택기 — 골든셋 검수와 같은 모양(이어 붙은 칸, AI가 낸 값 옆에 «AI 제안», 고른 값은 파랑 채움)
    const chips = make('div', 'act chips');
    field.labels.forEach((label) => {
      const b = make('button', 'chip' + (aiCodes.includes(label.code) ? ' is-ai' : '')); b.type = 'button'; b.dataset.value = label.code;
      b.appendChild(document.createTextNode(label.name));
      if (aiCodes.includes(label.code)) b.appendChild(make('span', 'vmark ai', 'AI 제안'));
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
    // 그 아래 한 줄 — 골든셋 검수처럼 왼쪽에 «보류»(값 여럿 칸은 «고른 값들로 확정»도)
    const row = make('div', 'act');
    if (field.many) {
      const confirm = make('button', null, '고른 값들로 확정'); confirm.type = 'button';
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
    box.appendChild(row);
    // AI가 본 것 — 골든셋 검수의 «AI가 사진에서 본 것 — …» 한 줄
    const gist = make('p', 'gist');
    if (!item.reading) gist.appendChild(document.createTextNode(item.noEvidence ? 'AI가 읽을 사진·글이 없습니다 — 직접 골라 주세요.' : 'AI가 아직 읽지 않았습니다.'));
    else if (!ai || !ai.value) { gist.appendChild(make('b', null, 'AI가 가르지 못함')); gist.appendChild(document.createTextNode(' — ' + (ai && ai.observation || '직접 골라 주세요.'))); }
    else { gist.appendChild(make('b', null, 'AI가 사진에서 본 것')); gist.appendChild(document.createTextNode(' — ' + (ai.confidence === 'LOW' ? '(확신 낮음) ' : '') + (ai.observation || ''))); }
    box.appendChild(gist);
    if (done) {
      const result = make('p', 'result');
      result.appendChild(make('b', null, labelName(field, decided.value)));
      result.appendChild(document.createTextNode(' · ' + decided.reviewer + (decided.agreedWithAi ? ' · AI와 같음' : '')));
      box.appendChild(result);
      const change = make('button', 'change', '바꾸기'); change.type = 'button';
      change.addEventListener('click', () => box.classList.toggle('open'));
      box.appendChild(change);
    }
    return box;
  }

  function card(item, number) {
    const done = itemDone(item);
    // 보류한 칸만 남은 건(rested)은 끝난 건처럼 건너뛴다 — 키보드 A·J와 «다음 안 한 건»이 거기서 멈추지 않게.
    const box = make('section', 'item' + (done ? ' finished' : !open(item).length ? ' rested' : ''));
    box.dataset.key = item.key;
    const top = make('p', 'item-top');
    top.appendChild(make('span', 'kicker', number + ' / ' + DATA.items.length));
    // 오른쪽 위 한 줄 — 골든셋 검수의 «수정 제안 1개»처럼 이 건에서 할 일
    const asking = DATA.fields.filter((f) => !labeled(item.decisions[f.id]) && !aiUsable(item, f)).length;
    top.appendChild(make('span', 'item-state', done ? '모두 확정' : !item.reading ? 'AI 추론 전'
      : asking ? '직접 볼 칸 ' + asking + '개' : 'AI 제안 ' + open(item).length + '칸 확인'));
    box.appendChild(top);
    const h3 = make('h3', null, item.key);
    const sub = [item.title, item.group].filter(Boolean).join(' · ');
    // 골든셋 검수처럼 키 뒤에 작은 글씨(.item h3 small)로 이름·묶음, 상품 페이지도 그 안에
    if (sub || item.link) {
      const small = make('small', null, sub ? ' ' + sub : '');
      if (item.link) { const a = make('a', null, (sub ? ' · ' : ' ') + '상품 페이지 ↗'); a.href = item.link; a.target = '_blank'; a.rel = 'noopener'; small.appendChild(a); }
      h3.appendChild(small);
    }
    box.appendChild(h3);
    const [rail, one] = shots(item);
    const body = make('div', 'item-body' + (one ? ' one' : ''));
    body.appendChild(rail);
    const decideBox = make('div', 'decide');
    const grid = make('div', 'gt-cell-grid');
    DATA.fields.forEach((f) => grid.appendChild(cell(item, f)));
    decideBox.appendChild(grid);
    const all = make('div', 'item-all');
    const ready = acceptable(item);
    all.appendChild(make('span', 'hint', done ? '모든 칸을 확정했습니다. 값을 바꾸려면 그 칸의 «바꾸기»를 누르세요.'
      : ready ? 'AI 제안이 모두 맞으면 한 번에 확정하세요. 보류한 칸은 건드리지 않습니다.'
      : 'AI가 가르지 못했거나 보류한 칸은 직접 골라 주세요.'));
    const button = make('button', null, 'AI 제안대로 모두 확정'); button.type = 'button'; button.disabled = !ready;
    button.addEventListener('click', () => acceptAll(item, button));
    all.appendChild(button);
    decideBox.appendChild(all);
    body.appendChild(decideBox);
    box.appendChild(body);
    return box;
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

  // 수는 서버(incr_review.status)가 센 그대로 — 화면이 원장을 다시 세지 않는다.
  function drawStatus() {
    const s = DATA.status;
    $('statusline').hidden = false;
    $('progress-short').textContent = '끝난 건 ' + s.itemsDone + ' / ' + s.items;
    $('pbar').style.width = (s.items ? Math.round(s.itemsDone * 100 / s.items) : 0) + '%';
    const tally = $('tally'); tally.textContent = '';
    [['확정한 칸', s.cellsLabeled + ' / ' + s.cells], ['그중 AI와 같음', s.cellsLabeled ? Math.round(s.cellsAgreedWithAi * 100 / s.cellsLabeled) + '%' : '–'],
     ['보류', String(s.cellsHeld)], ['AI가 읽은 건', s.itemsRead + ' / ' + s.items]].forEach(([k, v], i) => {
      if (i) tally.appendChild(document.createTextNode(' · '));
      tally.appendChild(document.createTextNode(k + ' ')); tally.appendChild(make('b', null, v));
    });
    $('count-left').textContent = s.itemsLeft ? String(s.itemsLeft) : '';
    $('progress-bottom-text').textContent = '끝난 건 ' + s.itemsDone + ' / ' + s.items + (s.itemsLeft ? ' · 남은 건 ' + s.itemsLeft : ' · 모두 확정했습니다 — «결과 파일 만들기»');
  }

  // AI 추론의 상태 — 골든셋 검수의 «다음 후보 받기»와 같은 부품: 도는 중이면 스피너 한 줄(.next-live .spin), 멈췄으면 경고(.notice),
  // 할 일 안내는 흰 한 줄(.once). 색을 여기서 칠하지 않는다.
  function drawRunner() {
    const status = DATA.status, runner = status.runner || {};
    const box = $('runner'), run = $('do-run');
    const readable = status.items - num(status.itemsNoEvidence);
    const fresh = !status.itemsRead;
    run.textContent = fresh ? 'AI 추론 시작' : '못 읽은 것 다시';
    box.textContent = ''; box.hidden = false;
    const say = (cls, text, spin) => {
      const p = make('p', cls);
      if (spin) { p.appendChild(make('span', 'spin')); const b = make('b', null, text); p.appendChild(b); }
      else p.textContent = text;
      if (spin && runner.startedAt) p.appendChild(make('span', 'muted', '(' + Math.max(0, Math.round((Date.now() - Date.parse(runner.startedAt)) / 60000)) + '분째)'));
      box.appendChild(p);
    };
    if (runner.running) { say('once next-live', runner.message || 'AI가 읽는 중입니다.', true); run.disabled = true; return true; }
    if (runner.lockBusy) { say('once next-live', '다른 AI 작업(골든셋 검수나 다른 증분)이 도는 중입니다 — 끝나면 «' + run.textContent + '»를 눌러 주세요.', true); run.disabled = true; return true; }
    run.disabled = false;
    if (runner.phase === 'failed') say('notice', runner.message || 'AI 추론이 멈췄습니다.');
    else if (fresh) say('once', 'AI가 아직 읽지 않았습니다 — «AI 추론 시작»을 눌러 주세요. 그동안에도 직접 고를 수 있습니다.');
    else if (status.itemsReadComplete < readable) say('once', 'AI가 못 읽은 건이 있습니다 — «못 읽은 것 다시»가 확정하지 않은 그 건만 다시 읽습니다.');
    else { box.hidden = true; run.disabled = true; }
    return false;
  }

  function shown() { return DATA.items.filter((item) => !ONLY_LEFT || !itemDone(item)); }
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
    const paged = all.length > PAGE_SIZE;
    ['page-prev', 'page-now', 'page-next'].forEach((id) => { $(id).hidden = !paged; });
    $('page-now').parentNode.hidden = !paged;  // 한 페이지뿐이면 넘김 자리째 숨긴다 — 빈 자리가 머리 줄을 밀지 않게
    $('page-now').textContent = page + ' / ' + pages + '페이지';
    $('page-prev').disabled = !all.some((item) => at(item) < FROM); $('page-next').disabled = !nextStart();
    if (!all.length) {
      // 골든셋 검수의 «이번에 볼 칸이 없습니다»와 같은 자리 — 흰 한 줄
      const note = make('p', 'once all-done', DATA.items.length ? '이 묶음의 모든 건을 확정했습니다. «결과 파일 만들기»를 눌러 주세요.' : '이 묶음에 건이 없습니다.');
      list.appendChild(note);
    }
    rows.forEach((item) => list.appendChild(card(item, at(item) + 1)));
    $('progress-bottom').hidden = !all.length;
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
    if (next) { focusNext(current, false); const f = document.querySelector('#list .item.focus'); if (f) f.scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
    if (gotoNextPage()) return;
    // 남은 건이 보류한 칸뿐이면 앞 페이지로 가도 할 일이 없다 — 그렇게 말한다.
    const anyOpen = DATA.items.some((item) => !itemDone(item) && open(item).length);
    toast(!DATA.status.itemsLeft ? '남은 건이 없습니다 — «결과 파일 만들기»를 눌러 주세요.'
      : !anyOpen ? '남은 건은 보류한 칸뿐입니다 — 보류를 풀려면 그 칸의 값을 골라 주세요(«전체» 보기).'
      : '이 페이지에는 남은 건이 없습니다 — 앞 페이지를 봐 주세요.', !!DATA.status.itemsLeft);
  }

  function drawReview(data) {
    DATA = data;
    DATA.items.forEach((item, index) => { item.at = index; });
    document.title = '데이터 운영 · ' + data.name + ' 증분 검수';
    $('title').textContent = data.name + ' · 증분 검수';
    $('crumb-task').textContent = ' / ' + data.name + (data.unit ? ' · ' + data.unit + ' 단위' : '');
    // 골든셋 검수의 «만든 시각»과 같은 모양 — 여기서는 증분을 밀어넣은 시각
    $('made').textContent = (data.status.meta || {}).pushedAt ? '넣은 시각 ' + new Date(data.status.meta.pushedAt).toLocaleString('ko-KR') : '';
    $('title-side').hidden = false;
    const pick = $('batch-pick'); pick.textContent = '';
    data.batches.forEach((b) => { const o = make('option', null, b); o.value = b; o.selected = b === data.batch; pick.appendChild(o); });
    drawItems();
    drawStatus();
    if (drawRunner()) schedule(8000);
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
    box.querySelector('p').textContent = item.key + ' · ' + image.viewId + (image.role ? ' · ' + image.role : '') + ' (' + (index + 1) + ' / ' + item.images.length + ')';
    box.hidden = false;
  }
  function go(step) { if (VIEW && VIEW.item.images.length) viewer(VIEW.item, (VIEW.index + step + VIEW.item.images.length) % VIEW.item.images.length); }
  $('viewer').addEventListener('click', (event) => {
    if (event.target.closest('.go')) { go(Number(event.target.closest('.go').dataset.go)); return; }
    if (event.target.tagName === 'IMG') { event.target.classList.toggle('full'); return; }
    $('viewer').hidden = true; VIEW = null;
  });

  const inView = (node) => { const box = node.getBoundingClientRect(); return box.bottom > 0 && box.top < innerHeight / (parseFloat(getComputedStyle(document.documentElement).zoom) || 1); };
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
      if (!node || !inView(node)) { toast('확정할 건이 화면에 없습니다 — J로 다음 건으로 가세요.', true); return; }
      const item = DATA.items.find((it) => it.key === node.dataset.key);
      const button = node.querySelector('.item-all button');
      if (item && button && !button.disabled) acceptAll(item, button);
      else if (item) toast(itemDone(item) ? '이 건은 이미 확정했습니다 — J로 다음 건으로 가세요.' : 'AI가 가르지 못했거나 보류한 칸이 있어 한 번에 확정할 수 없습니다 — 그 칸을 직접 골라 주세요.', true);
    }
    if (event.code === 'KeyJ') nextOpen();
  });

  // 머리의 버튼들 — 한 번만 건다.
  try { $('reviewer').value = localStorage.getItem(REVIEWER_KEY) || ''; } catch (e) {}
  $('reviewer').addEventListener('change', () => {
    try { localStorage.setItem(REVIEWER_KEY, reviewer()); } catch (e) {}
    if (reviewer() && PENDING.length) {
      const queued = PENDING.splice(0);
      queued.reduce((chain, run) => chain.then(run), Promise.resolve());
    }
  });
  $('batch-pick').addEventListener('change', (event) => {
    location.href = '/incr?task=' + encodeURIComponent(TASK) + '&batch=' + encodeURIComponent(event.target.value);
  });
  document.querySelectorAll('.views button').forEach((button) => button.addEventListener('click', () => {
    ONLY_LEFT = button.dataset.view === 'left';
    document.querySelectorAll('.views button').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    FROM = 0; PAGE_NO = 1; drawItems();
  }));
  $('page-prev').addEventListener('click', gotoPrevPage);
  $('page-next').addEventListener('click', gotoNextPage);
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
      if (!reply.ok) { toast(reply.error || '만들지 못했습니다.', true); return; }
      toast('확정한 ' + reply.labeled + '건을 ' + reply.file + '에 썼습니다' + (reply.pending ? ' (남은 ' + reply.pending + '건은 빠짐)' : '') + '.');
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
