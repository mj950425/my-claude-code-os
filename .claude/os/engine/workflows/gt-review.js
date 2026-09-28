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
    options: {
      type: 'array',
      description: '답마다 이 칸이 어느 값이 되는지. 둘 이상',
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
          rulesApplied: { type: 'array', items: { type: 'string' }, description: '정책 파일 «### 규칙»에서 이 값을 정하는 데 따른 규칙의 ID(R1 같은). 따른 규칙이 없으면 빈 배열' },
          casesOpened: { type: 'array', items: { type: 'string' }, description: '사례 목록에서 열어 본 사례의 ID(C01 같은). 열지 않았으면 빈 배열' },
          casesApplied: { type: 'array', items: { type: 'string' }, description: '열어 본 사례 가운데 이 값을 정하는 근거로 따른 사례의 ID. 열었지만 사정이 달라 따르지 않은 사례는 넣지 않는다' },
          askHuman: ASK_SCHEMA,
        },
        required: ['field', 'value', 'confidence', 'evidenceImageIds', 'observation', 'rulesApplied', 'casesApplied'],
      },
    },
    note: { type: 'string', description: '건 전체에 대한 한국어 요약. 증거가 부족했다면 무엇이 없었는지. 사람에게 묻는 말은 여기 쓰지 않고 그 칸의 askHuman에 쓴다' },
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
          rulesApplied: { type: 'array', items: { type: 'string' }, description: '판정의 근거로 든 정책 파일 «### 규칙»의 ID. 없으면 빈 배열' },
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
정책 파일: 판독 파일의 policy가 가리키는 파일 하나 — 이 상품에 걸리는 정의·허용값·규칙만 잘라 둔 것이다.
필드마다 «## <필드ID>» 절이 있다. 네가 답할 필드의 절을 먼저 전부 읽는다. 원래 정의 문서는 열지 않는다.
사진 경로는 프로젝트 루트(지금 작업 폴더) 기준이다.

Read·Grep·Glob만 쓴다. 어떤 파일도 만들거나 고치지 않는다. 사진을 잘라 저장하지 않는다 —
사람이 같은 사진을 보고 네 문장을 되짚을 수 있어야 한다. 건수를 세지 않는다.
observation·why·note는 **한국어로** 쓴다. 읽는 사람은 코드를 모르는 데이터 운영팀이다.
필드는 판독 파일 fields의 name(한국어 이름)으로 부르고, 필드 ID나 파일의 열 이름(text·context·images, 그 안의 열 이름)은
쓰지 않는다 — 맥락·글 열은 판독 파일 columnNames의 이름으로 부른다.
문장 안에서 값은 labelNames의 한국어 이름을 **글자 그대로** 부른다(바꿔 말하지 않는다 — 화면의 버튼과 같은 이름이어야 한다).
코드(영문 허용값)는 value 칸에만 쓴다.`

// 사례 목록 — 조회층. 목록(제목)은 작고, 자세한 것은 사례 파일에 있다. 판독자가 필요할 때만 연다.
const CASES = `
- 판독 파일에 cases가 있으면 그것은 **사례 목록**이다 — 사람이 검수에서 AI의 물음에 답한 기록의 제목(물음)과 답이다.
  정책이 아니라 사례다. 규칙이 된 사례(rule이 있는 것)는 정책 파일의 그 규칙을 따른다.
  askHuman을 남기기 **전에** 이 목록에서 같은 경계를 찾는다. 있으면 그 사례 파일을 열어 보고 그 답을 따르며 다시 묻지 않는다 —
  사진의 사정이 다르면 이 사진에 보이는 대로 판단한다(답을 이 상품의 값으로 옮겨 오지 않는다).
  연 사례의 ID를 casesOpened에, 그 가운데 값을 정하는 근거로 따른 사례를 casesApplied에 적는다 — 판례도 근거다.
  사례를 열었지만 사진의 사정이 달라 따르지 않았다면 casesApplied에 넣지 않고 observation(반론이면 why)에 그 사례와 다른 점을 적는다.
  목록에 없는 사례 파일은 열지 않는다.`

function readerPrompt(item) {
  return `판독 파일 ${item.view} 하나를 읽고, 그 안의 건 하나를 판독한다.

그 파일의 images에 있는 사진을 **전부** Read로 연다. text가 있으면 그 글도 증거다.
context가 있으면 함께 읽는다 — 답이 아니라 «무엇을 파는 상품인가» 같은 맥락이다.
fields에 적힌 필드마다, 정책 파일의 기준으로 증거에서 보이는 값을 허용값으로 답한다.

- 이 건의 정답은 모른다. 찾아 읽지도 않는다. 판독 파일·그 파일이 가리키는 사진·
  정책 파일·사례 목록과 그 목록의 사례 파일 말고는 아무것도 열지 않는다 — 판독 파일이 있는 폴더의 다른 파일, 작업 목록, GT 파일,
  지난 화면(review.json·review.html·sweep-raw.json)은 열지 않는다.
- 사진 여러 장이 같은 칸에 다른 답을 주면, 정책 파일이 그 경우를 어떻게 다루는지 따르고
  정의가 말하지 않으면 confidence를 LOW로 두고 observation에 갈린 사진을 적는다.
- 증거로 가를 수 없으면 value를 빈 문자열로, confidence를 LOW로 둔다. 추측으로 채우지 않는다.
- evidenceImageIds에는 네가 실제로 연 사진의 imageId(P01 같은)만 적는다.
- 사진과 정책 파일로 가를 수 없는 경계에 걸리면, 그 칸에 askHuman을 남기고 confidence를 LOW로 둔다.
  question은 사진 번호도 이 상품 이야기도 없는 경계 물음이다(사람의 답이 정의 문서의 규칙이 된다).
  이 사진의 사정은 here에, 답마다 될 값은 options에 적는다. 정책 파일 칸 절의 «### 규칙»이 이미 가른 경계는 다시 묻지 않고 그 규칙을 따른다(«범위»가 적힌 규칙은 그 카테고리 상품에만).
- 값을 정하는 데 따른 규칙이 있으면 그 ID를 rulesApplied에 적는다. 걸리는 규칙을 따르지 않았다면 observation에 왜인지 적는다.
- 사람에게 묻는 말을 note에 쓰지 않는다 — 화면은 askHuman만 사람에게 보인다.${CASES}${COMMON}`
}

// 값을 «이름 (코드)»로. 반론 문장은 이 모양을 따라 쓰므로, 여기서 코드만 주면 화면에 코드가 샌다.
function named(item, field, value) {
  if (value === null || value === undefined || value === '') return '(빈칸)'
  const names = (item.labelNames || {})[field] || {}
  return String(value).split(SEP).map((code) => (names[code] ? `${names[code]} (${code})` : code)).join(' + ')
}

function defensePrompt(item, disputed) {
  const lines = disputed
    .map((d) => `- ${d.field}: 지금 GT ${named(item, d.field, d.gt)} · 판독 ${named(item, d.field, d.value)} — 판독 근거 [${(d.evidenceImageIds || []).join(', ')}] ${d.observation || ''}`)
    .join('\n')
  return `판독 파일 ${item.view}의 건(${item.id})을 본다. 사진과 글은 그 파일이 가리킨다.

GT를 모르는 판독자가 증거만 보고 아래 칸에서 지금 GT와 다른 답을 냈다.
${lines}

네 일은 판독을 확인해 주는 것이 아니라 **지금 GT를 지키는 것**이다. 그 건의 사진을 전부 열고,
정책 파일의 기준으로 GT 값이 맞다고 볼 근거를 찾는다. 판독자가 놓친 사진, 정의의 예외 조항, 정책 파일의 «### 규칙»,
판독자가 기준을 잘못 적용한 자리를 본다. GT가 빈칸인 칸은 판독 값보다 나은 값이 있는지 본다.
판독 파일에 사례 목록(cases)이 있으면 사람이 같은 경계에 답한 판례가 있는지도 찾는다 — 판례가 GT 편이면 GT_STANDS의 근거이고,
판독 편이면 억지로 반박하지 않고 READER_RIGHT다. 근거로 든 규칙은 rulesApplied, 판례는 casesApplied에 적는다.

- 근거를 찾으면 GT_STANDS. 찾지 못하면 READER_RIGHT — 억지로 반박하지 않는다.
- 증거로 어느 쪽도 설 수 없으면 CANT_TELL. 사람이 무엇을 가르면 되는지 askHuman에 적는다(question은 상품을 떠난 경계 물음, here는 이 사진의 자리).
- 위 목록의 칸에만 답한다. GT 파일과 작업 목록은 열지 않는다 — 네가 볼 GT 값은 위에 다 있다.${CASES}
- why 문장에서 값은 위 목록의 이름(괄호 앞 글자)으로만 부른다. 괄호 안 코드는 옮겨 쓰지 않는다.${COMMON}`
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
  return reading
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
