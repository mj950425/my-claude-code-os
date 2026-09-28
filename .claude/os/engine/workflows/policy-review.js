export const meta = {
  name: 'policy-review',
  description: 'GT 개선 과제의 정책 규칙과 사례를 칸마다 읽어 표준이 아닌 물음을 다듬고, 중복·충돌을 가르고, 합칠·올릴·뺄 것을 제안한다. 과제·속성을 모른다',
  whenToUse: 'gt_review.py policy review가 찍은 workflowArgs를 그대로 넣는다. 결과는 policy finish --from으로 제안 목록이 된다',
  phases: [
    { title: '사례 다듬기', detail: '표준이 아닌 물음이 있는 칸마다 case-normalizer 하나. 답은 바꾸지 않는다' },
    { title: '중복·충돌', detail: '규칙이나 사례가 있는 칸마다 policy-auditor 하나. 후보 쌍 판정과 정리 제안' },
  ],
}

// 이 스크립트는 과제를 모른다. 규칙·사례·후보 쌍은 전부 입력 파일(input.json)에 있고 에이전트가 직접 읽는다.
// args는 입력 파일 경로와 볼 칸 목록만 나른다 — 큰 JSON을 AI가 옮겨 적으면 조용히 깨진다(gt-review.js와 같은 이유).
// 에이전트는 판정과 제안만 한다. 정책을 고치는 것은 사람이 제안 목록의 명령으로 한다.
const INPUT = typeof args === 'string' ? JSON.parse(args) : (args || {})
if (!INPUT.task || !INPUT.input || !Array.isArray(INPUT.fields)) {
  throw new Error('policy-review: 인자가 비었습니다(task·input·fields). policy review가 낸 workflowArgs를 그대로 넣어야 합니다')
}

const DRAFT_SCHEMA = {
  type: 'object',
  properties: {
    drafts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          decisionId: { type: 'string', description: '입력 cases의 id 그대로' },
          question: { type: 'string', description: '경계 물음 하나. «~인가?»로 끝나고 사진·상품 이야기 없이. 맞게 다듬을 수 없으면 빈 문자열' },
          here: { type: 'string', description: '이 사진의 사정 한 문장(사진 번호는 여기에만)' },
          why: { type: 'string', description: '무엇을 떼고 무엇을 남겼는지 한국어 한 문장' },
        },
        required: ['decisionId', 'question', 'here', 'why'],
      },
    },
  },
  required: ['drafts'],
}

const PROPOSAL = {
  type: 'object',
  properties: {
    rules: { type: 'array', items: { type: 'string' }, description: '입력 rules의 id' },
    cases: { type: 'array', items: { type: 'string' }, description: '입력 cases의 id' },
    text: { type: 'string', description: '규칙 문장 — «~이면» 하나에 값 하나' },
    condition: { type: 'string', description: '문장의 «~이면» 부분' },
    value: { type: 'string', description: '허용값 코드' },
    scope: { type: 'string', description: '범위(카테고리 이름, 쉼표로 여럿). 없으면 빈 문자열' },
    sentence: { type: 'string', description: 'definition 제안 — 정의 본문에 넣을 문장' },
    why: { type: 'string', description: '한국어 한두 문장' },
  },
  required: ['why'],
}

const AUDIT_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          pair: { type: 'string', description: '입력 pairs의 pair(A01 같은) 그대로' },
          verdict: { type: 'string', enum: ['DUPLICATE', 'CONFLICT', 'NARROWS', 'UNRELATED'] },
          why: { type: 'string', description: '두 조건을 나란히 놓고 같은/다른 자리를 짚는 한국어 한두 문장' },
        },
        required: ['pair', 'verdict', 'why'],
      },
    },
    merge: { type: 'array', items: PROPOSAL },
    promote: { type: 'array', items: PROPOSAL },
    retire: { type: 'array', items: PROPOSAL },
    definition: { type: 'array', items: PROPOSAL },
  },
  required: ['verdicts'],
}

function normalizePrompt(field) {
  return `정리 입력 파일 ${INPUT.input}를 Read로 연다. fields 가운데 id가 «${field}»인 칸 하나만 본다.
그 칸의 cases 가운데 problems가 비어 있지 않은 사례마다 물음을 표준 모양으로 다듬은 초안을 낸다.
problems가 빈 사례는 건드리지 않는다. 입력 파일 말고는 아무것도 열지 않는다.
question·here·why는 한국어로 쓴다. 답(valueName)은 바꾸지 않는다 — 다듬은 물음에 원래 답이 그대로 맞아야 한다.`
}

function auditPrompt(field) {
  return `정리 입력 파일 ${INPUT.input}를 Read로 연다. fields 가운데 id가 «${field}»인 칸 하나만 본다.
그 칸의 pairs마다 판정(DUPLICATE·CONFLICT·NARROWS·UNRELATED)을 내고, rules와 cases 전체를 보고 정리 제안(merge·promote·retire·definition)을 낸다.
- 제안은 입력에 있는 id만 가리킨다. 없는 id를 만들지 않는다.
- 사례의 물음(question)이 표준이 아니어도(problems) 뜻을 읽어 판정한다.
- 규칙 문장·조건은 경계 하나, 사진·상품 이야기 없이. value는 그 칸의 labelNames에 있는 코드만.
- 칸의 overLimit이 참이면 definition 제안을 먼저 본다.
입력 파일 말고는 아무것도 열지 않는다. why는 한국어로 쓴다.`
}

log(`과제 ${INPUT.task} · 칸 ${INPUT.fields.length} · 다듬을 칸 ${(INPUT.normalize || []).length}`)

phase('사례 다듬기')
const drafted = await parallel((INPUT.normalize || []).map((field) => () =>
  agent(normalizePrompt(field), { agentType: 'case-normalizer', label: `다듬기 ${field}`, phase: '사례 다듬기', schema: DRAFT_SCHEMA })
    .then((out) => (out && out.drafts) || [])
    .catch(() => [])))

phase('중복·충돌')
const audits = await parallel(INPUT.fields.map((field) => () =>
  agent(auditPrompt(field), { agentType: 'policy-auditor', label: `판정 ${field}`, phase: '중복·충돌', schema: AUDIT_SCHEMA })
    .then((out) => Object.assign({ field }, out || { verdicts: [] }))
    .catch(() => ({ field, verdicts: [], failed: true }))))

return {
  schemaVersion: 'gt-policy-review-v1',
  task: INPUT.task,
  input: INPUT.input,
  drafts: drafted.flat(),
  audits,
}
