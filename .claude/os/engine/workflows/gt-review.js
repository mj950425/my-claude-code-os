export const meta = {
  name: 'gt-review',
  description: 'GT 개선 과제의 건마다 증거만 보는 판독과 GT 편 반론을 따로 받는다. 과제·속성을 모른다',
  whenToUse: 'gt_review.py prepare가 찍은 workflowArgs를 그대로 넣는다. 어떤 gtTask 프로필이든 같다',
  phases: [
    { title: '증거 판독', detail: '건 하나에 판독자 하나. GT와 실행 값을 모른 채 증거와 정의만 본다' },
    { title: 'GT 편 반론', detail: '판독이 GT와 다른 칸만. 같은 증거에서 GT를 지킬 근거를 찾는다' },
  ],
}

// 이 스크립트는 과제를 모른다. 필드 이름·허용값·정의 문서·증거 경로는 전부 파일에 있고,
// 에이전트가 직접 읽는다. args는 포인터와 «판독 뒤 반론이 필요한가»를 가를 GT 값만 나른다.
// GT 값은 판독자 프롬프트에 절대 넣지 않는다 — 넣는 순간 판독자는 «무엇이 보이는가» 대신
// «무엇이 답인가»를 먼저 정한다.
// 인자는 둘 중 한 곳에서 온다. 스킬이 부르면 Workflow의 args, 러너(gt_next.py)가 부르면 아래 줄에 박힌 JSON이다 —
// 러너는 이 줄을 배치의 인자로 바꾼 사본을 부른다. 큰 JSON을 AI가 옮겨 적으면 조용히 깨진다(실제로 건 0으로 돌았다).
// 문자열로 감싸 온 인자도 받는다. 인자가 비었으면 판독자를 하나도 부르지 않고 빈 결과를 내는 대신 멈춘다.
const INPUT = typeof args === 'string' ? JSON.parse(args) : (args || {})
if (!INPUT.task || !INPUT.batchId || !Array.isArray(INPUT.items)) {
  throw new Error('gt-review: 인자가 비었습니다(task·batchId·items). prepare가 낸 workflowArgs를 그대로 넣어야 합니다')
}
const TASK = INPUT.task
const BATCH = INPUT.batchId
const WORKLIST = INPUT.worklist
// 판독자·반론자는 모든 과제에 공통이다(gt_task.AGENTS가 정하고 prepare가 넘긴다). 과제는 프롬프트를 바꾸지 못한다 —
// 판단을 바꾸는 자리는 정의 문서 하나다. 이 파일에도 과제·필드·허용값 이름을 적지 않는다(test_gt_review가 확인한다).
const AGENTS = Object.assign({ reader: 'gt-blind-reader', defender: 'gt-defender' }, INPUT.agents || {})
// 건마다 같은 필드 설명(허용값·이름표 등)은 INPUT.common에 한 번만 실어도 된다 — 건이 많으면 인자가 같은 목록을 수십 번 되풀이한다.
const ITEMS = (INPUT.items || []).map(item => Object.assign({}, INPUT.common || {}, item))
const GT = INPUT.gt || {}
const SEP = '|'

// 사람에게 묻는 말 — 칸마다 이 모양 하나로만 받는다. 메모(note)에 섞인 물음을 화면이 문장으로 골라내면 칸을 모르고, 빠지고, 엉뚱한 문장이 잡힌다.
// question은 이 상품을 떠나서도 통하는 경계 물음이다 — 사람의 답이 정책 규칙으로 옮겨 간다.
const ASK_SCHEMA = {
  type: 'object',
  description: '사진과 정책만으로 이 칸을 가를 수 없어 사람의 판단이 필요할 때만 쓴다. 쓰면 confidence는 LOW다',
  properties: {
    question: { type: 'string', description: '정책의 경계를 묻는 한국어 물음 하나. 사진 번호·이 상품 이야기 없이, 다른 상품에도 그대로 통하게. 예: «손이 상품의 일부를 가리면 가린 것으로 보는가?»' },
    here: { type: 'string', description: '이 사진에서 그 물음이 걸린 자리, 한국어 한 문장. 예: «P01에서 손이 상품 가장자리에 닿아 있다»' },
    imageIds: { type: 'array', items: { type: 'string' }, description: '그 자리가 보이는 사진의 imageId' },
    observe: { type: 'string', description: '관찰 칸에서만 — 이 물음이 가르는 관찰 항목 id. 그러면 options는 빈 배열로 둔다(답마다 될 값은 워크플로우가 채운다)' },
    options: {
      type: 'array',
      description: '답마다 이 칸이 어느 값이 되는지. 둘 이상(관찰 칸은 빈 배열)',
      items: {
        type: 'object',
        properties: {
          answer: { type: 'string', description: '짧은 한국어 답. 예: «가린 것이다»' },
          value: { type: 'string', description: '그 답이면 이 칸의 허용값(코드)' },
        },
        required: ['answer', 'value'],
      },
    },
  },
  required: ['question', 'here', 'imageIds', 'options'],
}

// 관찰 칸의 답 하나 — 판독자는 값을 모른 채 사실에만 답한다. 값은 아래 값 규칙 표(common.derive)가 정한다.
const OBSERVATION = {
  type: 'object',
  properties: {
    id: { type: 'string', description: '판독 파일 fields[].observe[].id 그대로' },
    v: { type: 'boolean', description: '그 항목이 사진에서 참인가 — 정책 파일에 적힌 항목의 뜻대로' },
    sure: { type: 'boolean', description: '확실하면 true. 경계에 걸려 어느 쪽인지 가르기 어려우면 false' },
    why: { type: 'string', description: '무엇을 보고 그렇게 답했는지 한국어 한 구절' },
  },
  required: ['id', 'v', 'sure', 'why'],
}

const READING_SCHEMA = {
  type: 'object',
  properties: {
    readings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          field: { type: 'string', description: '판독 파일 fields의 id 그대로(영문). 한국어 이름은 문장에서만 쓴다' },
          value: {
            type: 'string',
            description: `그 필드의 허용값 중 하나. 값 여럿 필드(cardinality many)는 허용값을 «${SEP}»로 잇는다. 증거로 가를 수 없으면 빈 문자열`,
          },
          confidence: { type: 'string', enum: ['HIGH', 'LOW'] },
          evidenceImageIds: { type: 'array', items: { type: 'string' }, description: '값을 정한 사진의 imageId(P01 같은). 글이 근거면 비운다' },
          observation: { type: 'string', description: '무엇이 보였는지 한국어로 한두 문장. 정의 문서의 어느 기준에 걸렸는지 포함' },
          definitionGap: { type: 'boolean', description: '정의 문서가 이 경우를 다루지 않아 기준 밖에서 판단해야 했으면 true' },
          rulesApplied: { type: 'array', items: { type: 'string' }, description: '판정에 실제 적용한 정책 규칙 ID. 따른 규칙이 없으면 빈 배열' },
          casesOpened: { type: 'array', items: { type: 'string' }, description: '사례 목록에서 열어 본 사례의 ID(C01 같은). 열지 않았으면 빈 배열' },
          casesApplied: { type: 'array', items: { type: 'string' }, description: '열어 본 사례 가운데 이 값을 정하는 근거로 따른 사례의 ID. 열었지만 사정이 달라 따르지 않은 사례는 넣지 않는다' },
          observations: { type: 'array', items: OBSERVATION, description: '관찰 칸(판독 파일 fields에 observe가 있는 칸)에서만 — 관찰 항목마다 답 하나. 값(value)은 빈 문자열로 둔다' },
          askHuman: ASK_SCHEMA,
        },
        required: ['field', 'value', 'confidence', 'evidenceImageIds', 'observation', 'rulesApplied', 'casesApplied'],
      },
    },
    unopened: { type: 'array', items: { type: 'string' }, description: '열지 못한 사진과 이유' },
  },
  required: ['readings'],
}

const DEFENSE_SCHEMA = {
  type: 'object',
  properties: {
    rebuttals: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          field: { type: 'string', description: '위 목록의 필드 id 그대로' },
          verdict: { type: 'string', enum: ['READER_RIGHT', 'GT_STANDS', 'CANT_TELL'] },
          why: { type: 'string', description: '정책 파일의 기준·규칙·판례와 증거를 들어 한국어로 한두 문장' },
          evidenceImageIds: { type: 'array', items: { type: 'string' } },
          rulesApplied: { type: 'array', items: { type: 'string' }, description: '판정의 근거로 든 정책 규칙 ID. 없으면 빈 배열' },
          casesOpened: { type: 'array', items: { type: 'string' }, description: '사례 목록에서 열어 본 사례의 ID. 없으면 빈 배열' },
          casesApplied: { type: 'array', items: { type: 'string' }, description: '판정의 근거로 든 사례의 ID. 없으면 빈 배열' },
          askHuman: Object.assign({}, ASK_SCHEMA, { description: 'CANT_TELL일 때, 사람이 무엇을 가르면 되는지. 다른 판정에는 쓰지 않는다' }),
        },
        required: ['field', 'verdict', 'why', 'evidenceImageIds', 'rulesApplied', 'casesApplied'],
      },
    },
  },
  required: ['rebuttals'],
}

// 정책은 건마다 따로 잘라 둔 파일 하나다(판독 파일의 policy) — 이 상품에 걸리는 정의·허용값·규칙만 있다. 원래 정의 문서는 열지 않는다.
// 사람이 검수에서 답한 기록은 프롬프트에 싣지 않는다. 규칙이 된 것은 정책 파일에, 나머지는 사례 목록(판독 파일의 cases)에 있고 필요할 때 연다.
const COMMON = `
판독 파일의 policy와 해당 필드의 기준을 읽고, images의 모든 사진과 text·context를 함께 확인한다.
자료는 판독 파일과 그 파일에서 연결한 사진·정책·사례를 사용한다. 상대 경로는 프로젝트 루트 기준이다.
설명은 한국어 합니다체로 쓰고, 필드와 값은 제공된 한국어 이름으로 부른다.`

const CASES = `
정책만으로 경계가 불명확하면 cases에서 관련 사례를 확인한다. 같은 조건일 때만 사례를 적용하며 정책을 우선한다.
그래도 판단할 수 없는 경계는 askHuman으로 묻는다.`

function readerPrompt(item) {
  return `[수행 지침]
판독 파일 ${item.view}의 카탈로그 정보와 정책을 바탕으로 각 필드를 판정한다.
허용값이 있는 필드는 정책에 따라 값을 선택한다. observe가 있는 필드는 관찰 항목에 답하고, 최종 값은 계산기에 맡긴다.
정책에 미결정 처리 기준이 있으면 그 기준을 따른다. 기준이 없고 증거도 부족하면 값을 비우고 확신을 낮춘다.
${COMMON}${CASES}`
}

// 값을 «이름 (코드)»로. 반론 문장은 이 모양을 따라 쓰므로, 여기서 코드만 주면 화면에 코드가 샌다.
function named(item, field, value) {
  if (value === null || value === undefined || value === '') return '(빈칸)'
  const names = (item.labelNames || {})[field] || {}
  return String(value).split(SEP).map((code) => (names[code] ? `${names[code]} (${code})` : code)).join(' + ')
}

function defensePrompt(item, disputed) {
  const lines = disputed
    .map((d) => `- ${d.field}: 지금 GT ${named(item, d.field, d.gt)} · 판독 ${named(item, d.field, d.value)} — 판독 근거 [${(d.evidenceImageIds || []).join(', ')}] ${d.observation || ''}`
      + (d.observations && d.observations.length ? `\n  (관찰 칸 — 판독 값은 관찰 답으로 정해졌다. GT를 지키려면 어느 관찰이 틀렸는지 사진으로 짚는다)` : ''))
    .join('\n')
  return `[검토 목적]
기존 정답을 유지할 근거가 있는지 정책과 증거로 검토한다.

[수행 지침]
판독 파일 ${item.view}를 읽고 아래 불일치 필드만 검토한다.
${lines}

기존 정답의 근거가 있으면 GT_STANDS, 새 판독이 정책에 맞으면 READER_RIGHT, 둘 다 확정할 수 없으면 CANT_TELL로 답한다.
관찰로 계산한 값이면 어느 관찰이 증거와 다른지 확인한다. 관찰은 맞지만 값 규칙이 문제면 CANT_TELL로 답한다.
기존 정답이 비어 있고 새 판독도 맞지 않으면 CANT_TELL로 답한다.
${COMMON}${CASES}`
}

// 판독자가 필드 칸에 한국어 이름을 적는 일이 실제로 있었다. 이름 → id로 되돌려, 칸이 «못 읽음»으로 떨어지지 않게 한다.
function fieldId(item, field) {
  // 이미 id면 옮기지 않는다 — 이름이 다른 필드의 id와 같아도(로더가 막지만) 판독이 다른 필드로 가지 않게.
  if ((item.fields || []).includes(field) || (item.quietFields || []).includes(field)) return field
  const names = item.fieldNames || {}
  return names[field] || field
}

// 허용값 안인가 — cell_status와 같은 규칙: 값 여럿 필드만 조각마다, 값 하나 필드는 값 통째로.
function inLabels(item, field, value) {
  const labels = (item.labels || {})[field]
  if (!labels) return true
  const parts = (item.many || {})[field] ? canonical(value).split(SEP) : [String(value)]
  return parts.every((part) => labels.includes(part))
}

function canonical(value) {
  // 빈 조각·겹친 조각을 빼고 정렬 — merge·cell_status의 sorted(set(...))와 같다.
  return [...new Set(String(value || '').split(SEP).filter(Boolean))].sort().join(SEP)
}

log(`배치 ${BATCH} · 건 ${ITEMS.length}. 판독자 ${AGENTS.reader} · 반론자 ${AGENTS.defender}`)

// 에이전트 유형이 이 세션에 없으면 대신 세우지 않는다. 기본 에이전트는 쓰기 도구를 갖고 있어,
// «읽기만 한다»가 지시로만 남는다. 멈추고 다시 시작하라고 말하는 편이 눈가림을 지킨다.
// 판독자가 없으면 멈추고(판독이 하나도 없다), 반론자만 없으면 판독은 계속 받는다 — 받은 판독을 버리지 않는다.
let missingAgent = null
let missingRole = null
// 에이전트 유형이 없다는 하네스의 오류만 가른다. 다른 «not found»(파일 등)를 유형 부재로 읽으면 배치 전체를 버린다.
const AGENT_MISSING = /agent type ['"]?[\w:-]+['"]? not found/i

// 값 규칙 표 — 파이썬(gt_derive.parse)이 만든 식 트리를 계산만 한다. 파서는 파이썬 하나다(둘이면 어긋난다).
function evalTree(tree, answers) {
  switch (tree.op) {
    case 'name': return !!answers[tree.name]
    case 'not': return !evalTree(tree.arg, answers)
    case 'and': return tree.args.every((a) => evalTree(a, answers))
    case 'or': return tree.args.some((a) => evalTree(a, answers))
    case 'count': {
      const n = Object.keys(answers).filter((k) => k.startsWith(tree.prefix) && answers[k]).length
      return { '>=': n >= tree.n, '>': n > tree.n, '<=': n <= tree.n, '<': n < tree.n, '==': n === tree.n }[tree.cmp]
    }
    default: throw new Error(`gt-review: 모르는 식 노드 ${tree.op}`)
  }
}

function deriveValue(table, answers) {
  for (const rule of table.rules) if (evalTree(rule.ast, answers)) return { value: rule.value, rule: rule.id }
  return { value: table.else, rule: null }
}

// 답 하나를 뒤집으면 값이 바뀌는 항목 — gt_derive.pivotal과 같은 규칙.
function pivotalItems(table, answers) {
  const base = deriveValue(table, answers).value
  return table.observe.filter((id) => deriveValue(table, Object.assign({}, answers, { [id]: !answers[id] })).value !== base)
}

// 다시 볼 항목 — 애매한 항목 가운데, 다른 애매한 항목이 어느 쪽이든 그 항목 하나를 뒤집으면 값이 바뀌는 조합이 있는 것.
// 하나씩만 뒤집으면 «둘이 함께 참이어야 값이 바뀌는»(COUNT >= 2 같은) 항목을 놓친다. 애매한 항목이 많으면 하나씩 뒤집기로 줄인다.
function unsureToAsk(table, answers, unsure) {
  if (unsure.length > 10) {
    const pivots = pivotalItems(table, answers)
    return unsure.filter((id) => pivots.includes(id))
  }
  return unsure.filter((id) => {
    const others = unsure.filter((o) => o !== id)
    for (let mask = 0; mask < (1 << others.length); mask++) {
      const trial = Object.assign({}, answers)
      others.forEach((o, n) => { trial[o] = !!(mask & (1 << n)) })
      const a = deriveValue(table, Object.assign({}, trial, { [id]: true })).value
      const b = deriveValue(table, Object.assign({}, trial, { [id]: false })).value
      if (a !== b) return true
    }
    return false
  })
}

const SECOND_SCHEMA = {
  type: 'object',
  properties: { observations: { type: 'array', items: Object.assign({}, OBSERVATION, {
    properties: Object.assign({ field: { type: 'string', description: '칸 id 그대로' } }, OBSERVATION.properties),
    required: ['field', 'id', 'v', 'sure', 'why'] }) } },
  required: ['observations'],
}

// 둘째 관찰자 — 같은 판독자 유형이 앞선 답을 모른 채 흔들리는 항목만 다시 본다. 값도 앞 답도 주지 않는다.
function secondPrompt(item, lines) {
  return `[수행 지침]
판독 파일 ${item.view}의 모든 사진과 정책을 확인하고 아래 관찰 항목에 답한다.
${lines}
${COMMON}`
}

// 관찰 칸의 값을 정하고, 흔들리는 자리만 둘째 관찰자에게 다시 묻는다. 갈리면 합치지 않고 확신을 낮춘다(사람이 본다).
// 관찰 칸의 물음 — 물음이 가르는 항목(observe)을 예/아니오로 뒤집어 표가 선택지의 값을 채운다. 판독자와 반론자가 같이 쓴다.
// 어느 항목인지 모르거나(답을 값으로 옮길 수 없다), 어느 쪽이든 값이 같으면(물을 까닭이 없다) null.
function fillObservedAsk(table, answers, ask) {
  const target = ask && ask.observe
  if (!target || !table.observe.includes(target)) return null
  const yes = deriveValue(table, Object.assign({}, answers, { [target]: true })).value
  const no = deriveValue(table, Object.assign({}, answers, { [target]: false })).value
  if (yes === no) return null
  return Object.assign({}, ask, { options: [{ answer: '예', value: yes }, { answer: '아니오', value: no }] })
}

async function applyDerive(item, reading) {
  const tables = item.derive || {}
  const fields = Object.keys(tables)
  if (!fields.length) return reading
  const byField = {}
  reading.readings.forEach((r) => { byField[r.field] = r })
  const ask = []   // [{field, id}] — 애매하면서 값을 뒤집는 항목
  const state = {}
  for (const field of fields) {
    const table = tables[field]
    const r = byField[field] || (byField[field] = { field, value: '', confidence: 'LOW', evidenceImageIds: [], observation: '관찰 답이 돌아오지 않았습니다.', rulesApplied: [], casesApplied: [] })
    const given = {}
    ;(r.observations || []).forEach((o) => { if (table.observe.includes(o.id)) given[o.id] = o })
    const answers = {}
    table.observe.forEach((id) => { answers[id] = !!(given[id] && given[id].v) })
    const missing = table.observe.filter((id) => !given[id])
    const unsure = table.observe.filter((id) => !given[id] || given[id].sure === false)
    state[field] = { table, r, given, answers, missing, split: [] }
    unsureToAsk(table, answers, unsure).forEach((id) => ask.push({ field, id }))
  }
  // 같은 관찰 ID를 두 칸이 쓰는데 답이 다르면 한 사진을 두 번 다르게 본 것이다 — 두 칸 모두 갈림으로 둔다.
  const seen = {}
  for (const field of fields) for (const id of state[field].table.observe) {
    if (!(id in seen)) { seen[id] = { field, v: state[field].answers[id] }; continue }
    if (seen[id].v !== state[field].answers[id]) { state[field].split.push(id); state[seen[id].field].split.push(id) }
  }
  if (ask.length) {
    const lines = ask.map((a) => {
      const name = ((item.observeNames || {})[a.field] || {})[a.id] || a.id
      return `- 칸 ${a.field} · 항목 ${a.id}(${name})`
    }).join('\n')
    let second = null
    try {
      second = await agent(secondPrompt(item, lines), {
        agentType: AGENTS.reader, label: `다시 보기 ${item.id}`, phase: '증거 판독', schema: SECOND_SCHEMA })
    } catch (error) { second = null }
    const again = {}
    ;((second && second.observations) || []).forEach((o) => { again[`${fieldId(item, o.field)}|${o.id}`] = o })
    for (const a of ask) {
      const s2 = again[`${a.field}|${a.id}`]
      const st = state[a.field]
      if (!s2) { st.split.push(a.id); continue }           // 다시 보기가 돌아오지 않았다 — 흔들리는 채로 둔다
      if (!st.given[a.id]) {                                  // 첫 눈이 답하지 않은 항목 — 다시 본 눈의 답을 쓴다(비교할 답이 없다)
        st.given[a.id] = { id: a.id, v: !!s2.v, sure: s2.sure !== false, why: s2.why, second: { v: !!s2.v, why: s2.why } }
        st.answers[a.id] = !!s2.v
        st.missing = st.missing.filter((id) => id !== a.id)
        ;(st.r.observations || (st.r.observations = [])).push(st.given[a.id])
        continue
      }
      if (!!s2.v !== st.answers[a.id]) st.split.push(a.id)  // 두 눈이 갈렸다 — 합치지 않는다
      else if (st.given[a.id]) st.given[a.id].sure = true    // 같게 봤다 — 확실해졌다
      ;(st.r.observations || []).forEach((o) => { if (o.id === a.id) o.second = { v: !!s2.v, why: s2.why } })
    }
  }
  for (const field of fields) {
    const st = state[field]
    const got = deriveValue(st.table, st.answers)
    const split = Array.from(new Set(st.split))
    const r = st.r
    // 관찰 답이 하나도 없으면 값을 만들지 않는다 — 모두 «아니오»로 친 값은 사진을 본 값이 아니다(화면에서는 «AI가 읽지 못함»).
    const none = st.missing.length === st.table.observe.length
    r.value = none ? '' : got.value
    r.rulesApplied = Array.from(new Set([...(got.rule && !none ? [got.rule] : []), ...(r.rulesApplied || []).filter((id) => !String(id).startsWith('V'))]))
    r.split = split
    r.observations = st.table.observe.map((id) => Object.assign({ id, v: st.answers[id], sure: false, why: '(답 없음)' }, st.given[id] || {}))
    // 관찰 칸의 물음 — 판독자는 값을 몰라 «답마다 될 값»을 못 적는다. 물음이 가르는 항목을 뒤집어 보고 표가 값을 채운다.
    // 어느 쪽으로 답해도 값이 같으면 물을 까닭이 없다(사람 시간만 쓴다) — 물음을 내린다.
    if (r.askHuman && r.askHuman.question) {
      const filled = fillObservedAsk(st.table, st.answers, r.askHuman)
      if (filled) r.askHuman = filled
      else delete r.askHuman
    }
    const low = split.length || st.missing.length || (r.askHuman && r.askHuman.question)
    r.confidence = low ? 'LOW' : 'HIGH'
    const names = (item.observeNames || {})[field] || {}
    const summary = r.observations.map((o) => `${names[o.id] || o.id} ${o.v ? '있음' : '없음'}`).join(' · ')
    r.observation = [r.observation, `관찰 — ${summary}`, split.length ? `다시 본 눈과 갈린 항목: ${split.map((id) => names[id] || id).join(', ')}` : '']
      .filter(Boolean).join(' ')
  }
  reading.readings = Object.values(byField)
  return reading
}

async function readStage(item) {
  if (missingRole === 'reader') return null
  let reading
  try {
    reading = await agent(readerPrompt(item), {
      agentType: AGENTS.reader, label: `판독 ${item.id}`, phase: '증거 판독', schema: READING_SCHEMA,
    })
  } catch (error) {
    if (AGENT_MISSING.test(String((error && error.message) || error))) { missingAgent = AGENTS.reader; missingRole = 'reader' }
    throw error
  }
  if (!reading) return null
  // 사람에게 묻는 칸은 확신 낮음이다 — 물으면서 확신 높음이면 반론만 불리고 물음은 묻힌다.
  reading.readings = (reading.readings || []).map((r) => Object.assign({}, r, { field: fieldId(item, r.field) },
    r.askHuman && r.askHuman.question ? { confidence: 'LOW' } : {}))
  return applyDerive(item, reading)
}

async function defendStage(reading, item) {
  if (!reading) return { id: item.id, reading: null, defense: null }
  const gt = GT[item.id] || {}
  const alts = (INPUT.alternatives || {})[item.id] || {}
  // 대체 정답(사람이 같이 맞다고 적어 둔 값)을 낸 판독은 어긋난 것이 아니다 — 반론을 부르지 않는다.
  // 한 칸에 서로 다른 두 값을 낸 판독은 화면에서 «사람이 볼 칸»이 된다(merge와 같은 규칙) — 반론할 거리가 아니다.
  const valuesByField = {}
  reading.readings.forEach((r) => { (valuesByField[r.field] = valuesByField[r.field] || new Set()).add(String(r.value)) })
  // 한 필드의 판독이 여럿이면 마지막 것 하나만 본다(merge와 같은 규칙) — 같은 값을 두 번 적어도 반론은 한 번.
  const lastByField = {}
  reading.readings.forEach((r) => { lastByField[r.field] = r })
  const disputed = Object.values(lastByField)
    // 후보 칸, 그리고 후보가 아닌 칸(quietFields) — 뒤엣것은 판독이 확신 있게 GT와 다를 때만(아래 조건 그대로) 반론한다.
    .filter((r) => item.fields.includes(r.field) || (item.quietFields || []).includes(r.field))
    .filter((r) => valuesByField[r.field].size === 1)
    .filter((r) => r.value && r.confidence === 'HIGH' && canonical(r.value) !== canonical(gt[r.field])
      // 대체 정답은 GT가 있을 때만 본다(빈 GT에는 대체할 정답이 없다) — cell_status와 같은 규칙.
      && !(gt[r.field] !== null && gt[r.field] !== undefined && (alts[r.field] || []).map(canonical).includes(canonical(r.value)))
      // 허용값 밖의 값은 화면에서 «사람이 볼 칸»이 된다(cell_status와 같은 규칙) — 반론할 거리가 아니다.
      && inLabels(item, r.field, r.value))
    .map((r) => Object.assign({}, r, { gt: gt[r.field] === undefined ? null : gt[r.field] }))
  // GT와 같은 답이면 반론을 부르지 않는다. GT를 모르는 눈이 같은 값을 냈다는 것이 이미 근거다.
  if (!disputed.length || missingRole === 'defender') return { id: item.id, reading, defense: null }
  try {
    const defense = await agent(defensePrompt(item, disputed), {
      agentType: AGENTS.defender, label: `반론 ${item.id}`, phase: 'GT 편 반론', schema: DEFENSE_SCHEMA,
    })
    if (defense) defense.rebuttals = (defense.rebuttals || []).map((r) => Object.assign({}, r, { field: fieldId(item, r.field) }))
    // 관찰 칸에서 반론자가 물으면 판독자의 관찰 답 위에서 같은 방식으로 선택지를 채운다 — 그래야 사람의 답이 다음 판독자의 사례가 된다.
    if (defense) defense.rebuttals.forEach((r) => {
      const table = (item.derive || {})[r.field]
      if (!table || !r.askHuman || !r.askHuman.question) return
      const answers = {}
      table.observe.forEach((id) => { answers[id] = !!((lastByField[r.field] || {}).observations || []).find((o) => o.id === id && o.v) })
      const filled = fillObservedAsk(table, answers, r.askHuman)
      if (filled) r.askHuman = filled
      else delete r.askHuman
    })
    return { id: item.id, reading, defense }
  } catch (error) {
    if (AGENT_MISSING.test(String((error && error.message) || error))) { missingAgent = AGENTS.defender; missingRole = missingRole || 'defender' }
    return { id: item.id, reading, defense: null }
  }
}

// 단계 — 러너(gt_next.py)는 판독(read)과 반론(defend)을 **따로** 부른다. 판독 단계의 인자에는 GT가 없고, 반론 단계는 판독이 모두
// 끝난 뒤에만 GT를 받는다. 판독자가 도는 동안 판독 파일 이름과 GT를 잇는 파일이 디스크 어디에도 없게 하려는 것이다(눈가림).
// 인자에 stage가 없으면 예전처럼 한 번에 둘 다 한다.
const STAGE = INPUT.stage || 'both'
const READINGS = INPUT.readings || {}
const results = STAGE === 'read'
  ? await pipeline(ITEMS, async (item) => {
    const reading = await readStage(item)
    return reading ? { id: item.id, reading, defense: null } : null
  })
  : STAGE === 'defend'
    ? await pipeline(ITEMS.filter((item) => READINGS[item.id]), (item) => defendStage(READINGS[item.id], item))
    : await pipeline(ITEMS, readStage, defendStage)

const kept = results.filter(Boolean)
if (missingAgent) {
  log(`에이전트 유형 ${missingAgent}가 이 세션에 없다 — 대신 세우지 않고 멈춘다. Claude Code를 다시 시작하면 등록된다`)
}
const unread = ITEMS.filter((item) => !kept.some((r) => r.id === item.id && r.reading)).map((item) => item.id)
if (unread.length && STAGE !== 'defend') log(`판독이 돌아오지 않은 건: ${unread.join(', ')} — 화면에 «AI가 못 읽음»으로 남는다`)

return {
  schemaVersion: 'gt-review-sweep-v2',
  task: TASK,
  batchId: BATCH,
  stage: STAGE,
  worklist: WORKLIST,
  needsRestart: missingAgent ? { agentType: missingAgent, role: missingRole } : null,
  items: kept,
}
