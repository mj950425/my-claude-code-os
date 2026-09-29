export const meta = {
  name: 'policy-readiness-review',
  description: '신규 과제의 자연어 정책 정의를 독립된 관점으로 검수하고 프롬프트 전달 준비도를 평가한다.',
  whenToUse: '새 과제 정책을 처음 만들거나 규칙을 바꾼 뒤 readiness를 검수할 때 사용한다. task와 definition, 허용값, 렌더링 프롬프트 경로를 제공한다.',
  phases: [
    { title: '계약 검수', detail: '정의·허용값·규칙 순서와 문장 경계를 검수한다' },
    { title: '반례 검수', detail: '경계 사례를 대입해 모호성, 충돌, 다음 규칙 이동을 검수한다' },
    { title: '프롬프트 검수', detail: '전달된 규칙과 블라인드 경로 분리를 확인한다' },
  ],
}

const INPUT = typeof args === 'string' ? JSON.parse(args) : (args || {})
if (!INPUT.task || !INPUT.definition || !INPUT.allowedValues) {
  throw new Error('policy-readiness-review: task·definition·allowedValues가 필요합니다')
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    scores: {
      type: 'array', items: {
        type: 'object', properties: {
          axis: { type: 'string' }, score: { type: 'number' }, reason: { type: 'string' },
        }, required: ['axis', 'score', 'reason'],
      },
    },
    findings: { type: 'array', items: { type: 'string' } },
    examples: { type: 'array', items: { type: 'string' } },
    empiricalAccuracy: { type: 'string', enum: ['NOT_EVALUATED', 'EVALUATED'] },
  },
  required: ['scores', 'findings', 'examples', 'empiricalAccuracy'],
}

const REQUIRED_AXES = [
  'allowed_values_and_scope',
  'rule_clarity_and_decision',
  'priority_and_fallback',
  'conflicts_and_missing_cases',
  'prompt_and_blind_separation',
]

const common = `과제: ${INPUT.task}\n정의 파일: ${INPUT.definition}\n허용값: ${JSON.stringify(INPUT.allowedValues)}\n추론 프롬프트(있으면): ${INPUT.inferencePrompt || '제공 안 됨'}\n블라인드 검수 프롬프트(있으면): ${INPUT.blindPrompt || '제공 안 됨'}\n\n정의 파일과 명시된 입력만 읽으세요. 실제 이미지/상품 정확도는 실행하지 않았다면 NOT_EVALUATED로 두세요. scores에는 다음 axis를 각각 정확히 한 번씩 반환하세요: ${REQUIRED_AXES.join(', ')}. 점수는 유한한 숫자 0~100 범위입니다. 누락·중복·범위 밖 점수는 검수 실패입니다. 해결되지 않은 구체적 모호성이 있으면 해당 축은 89점 이하입니다. 점수와 함께 근거를 적고 파일은 수정하지 마세요.`

const reviewers = [
  ['policy-contract-reviewer', '계약 검수', '정의·허용값 일치와 범위, 규칙의 명료성과 결정성, 우선순위와 다음 규칙 이동, 모순·빠진 상황, 프롬프트 연결과 블라인드 분리를 다섯 축 모두 평가합니다.'],
  ['policy-counterexample-reviewer', '반례 검수', '각 축을 모두 평가하되 반례 중심으로 판단합니다. 허용값·범위, 반례에 대한 규칙의 결정성, 우선순위·fallback, 충돌·누락, 프롬프트 본문·블라인드 분리를 빠짐없이 채점합니다.'],
  ['policy-prompt-reviewer', '프롬프트 검수', '각 축을 모두 평가하되 모델이 실제로 읽는 렌더 결과를 중심으로 판단합니다. 허용값·범위, 규칙 해석, 우선순위·fallback, 충돌·누락, 프롬프트 보존·블라인드 분리를 빠짐없이 채점합니다.'],
]

const results = await parallel(reviewers.map(([agentType, label, focus]) => () =>
  agent(`${common}\n\n${focus}`, { agentType, label, phase: label, schema: REVIEW_SCHEMA })
    .then((out) => ({ reviewer: agentType, ...(out || {}) }))
    .catch((error) => ({ reviewer: agentType, scores: [], findings: [String(error)], examples: [], empiricalAccuracy: 'NOT_EVALUATED', failed: true }))
))

const scores = results.flatMap((r) => r.scores || []).map((s) => Number(s.score)).filter(Number.isFinite)
const validReview = (result) => {
  const rows = result.scores || []
  const axes = rows.map((row) => row.axis)
  return !result.failed && REQUIRED_AXES.every((axis) => axes.filter((value) => value === axis).length === 1)
    && rows.length === REQUIRED_AXES.length
    && rows.every((row) => Number.isFinite(Number(row.score)) && Number(row.score) >= 0 && Number(row.score) <= 100)
}
return {
  schemaVersion: 'policy-readiness-v1',
  task: INPUT.task,
  definition: INPUT.definition,
  reviewers: results,
  minimumScore: scores.length ? Math.min(...scores) : null,
  requiredAxes: REQUIRED_AXES,
  ready: results.length === reviewers.length && results.every(validReview)
    && results.every((r) => r.scores.every((s) => Number(s.score) >= 90))
    && results.every((r) => r.empiricalAccuracy === 'NOT_EVALUATED' || r.empiricalAccuracy === 'EVALUATED'),
  readinessNote: 'ready는 문서·프롬프트 구조 검수 통과만 뜻합니다. 실제 정확도는 이미지/상품 GT 평가로 별도 확인해야 합니다.',
}
