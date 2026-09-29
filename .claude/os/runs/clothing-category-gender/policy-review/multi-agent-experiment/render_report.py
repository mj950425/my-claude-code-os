#!/usr/bin/env python3
"""Render the current implementation and saved experiment traces. Run with SDK venv."""
import ast
import importlib.util
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path('/Users/mj/musinsa/dev/my-claude-code-os')
SCRIPTS=ROOT/'.claude/os/engine/scripts'
DEST=ROOT/'.claude/os/runs/clothing-category-gender/policy-review'
SOURCE=SCRIPTS/'policy_multi_agent_eval.py'
sys.path.insert(0,str(SCRIPTS))
import policy_multi_agent_eval as engine

# Read exact system prompt expressions without invoking an SDK session.
tree=ast.parse(SOURCE.read_text())
system_node=next(k.value for n in ast.walk(tree) if isinstance(n,ast.Call) for k in n.keywords if k.arg=='system_prompt')
systems=[ast.literal_eval(system_node.body),ast.literal_eval(system_node.orelse)] if isinstance(system_node,ast.IfExp) else [ast.literal_eval(system_node)]*2
roles=[{'id':'orchestrator','name':'오케스트레이터 에이전트','job':'판독을 위임하고 필요한 분업·판례 검색·독립 검수를 선택합니다.','prompt':systems[0],'tools':['Read','Agent'],'source':str(SOURCE),'extra':'하위 에이전트 호출은 실행당 최대 6회. 검사·정답 비교·재시도 분기는 Python 스크립트가 관리합니다.'}]
for key,definition in engine.agent_definitions(Path('${작업 폴더}'),engine.DEFAULT_MODEL).items():
 name={'reader':'판독 에이전트','reviewer':'검수 에이전트','precedent':'판례 에이전트'}[key]
 roles.append({'id':key,'name':name,'job':definition.description.split(': ',1)[-1],'prompt':definition.prompt,'tools':definition.tools,'source':str(SOURCE),'extra':('정답 불일치 후 별도 컨텍스트로 호출되는 검수의 시스템 지침:\n'+systems[1]) if key=='reviewer' else ''})
manifest=DEST/'multi-agent-experiment/manifest.json'
raw=json.loads(manifest.read_text()) if manifest.exists() else json.loads(Path('/tmp/policy-hard-cases.json').read_text())
if isinstance(raw,dict): raw=raw.get('cases',raw.get('entries',[]))
if not isinstance(raw,list) or not raw: raw=json.loads(Path('/tmp/policy-hard-cases.json').read_text())
# The source selection manifest remains authoritative for lineage and descriptions.
selected=json.loads(Path('/tmp/policy-hard-cases.json').read_text()) if Path('/tmp/policy-hard-cases.json').exists() else raw
cases=[]
for item in selected:
 identity=str(item['id']); path=DEST/f'multi-agent-experiment/{identity}.json'
 record=json.loads(path.read_text()) if path.exists() else None
 entry={**item,'resultPath':str(path),'record':record}
 if record:
  all_traces=[t for step in record.get('steps',[]) for t in step.get('response',{}).get('trace',[])]
  completed=[t for t in all_traces if t.get('event')=='tool_completed']
  reads={t.get('input',{}).get('file_path','') for t in completed if t.get('tool')=='Read'}
  image_reads={p for p in reads if Path(p).name.startswith('evidence-')}
  requested_reads={t.get('input',{}).get('file_path','') for t in all_traces if t.get('event')=='tool_request' and t.get('tool')=='Read'}
  requested_reads.update(b.get('input',{}).get('file_path','') for t in all_traces for b in t.get('tools',[]) if isinstance(b,dict) and b.get('name')=='Read')
  requested_images={Path(p).name for p in requested_reads if Path(p).name.startswith('evidence-')}
  child_observed=any(t.get('parentToolUseId') for t in all_traces)
  delegation_requests={t.get('id') for t in all_traces if t.get('event')=='tool_request' and t.get('tool') in ['Agent','Task']}
  delegation_requests.update(b.get('id') for t in all_traces for b in t.get('tools',[]) if isinstance(b,dict) and b.get('name') in ['Agent','Task'])
  entry['traceSummary']={'delegationRequests':len(delegation_requests),'delegationCompletions':sum(t.get('tool') in ['Agent','Task'] for t in completed),'readCompletions':sum(t.get('tool')=='Read' for t in completed),'imageReadFiles':len(image_reads),'requestedImageFiles':len(requested_images),'childObserved':child_observed,'models':sorted({t['model'] for t in all_traces if t.get('model')})}
 cases.append(entry)
data={'roles':roles,'cases':cases,'model':engine.DEFAULT_MODEL,'generated':datetime.now().astimezone().isoformat(timespec='seconds'),'source':str(SOURCE)}
html='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>정책 판독 · 에이전트와 실험</title>
<style>
:root{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;color:#202b37;background:#f5f6f8;font-size:14px}*{box-sizing:border-box}body{margin:0}header{padding:24px 32px 18px;border-bottom:1px solid #dce1e7;background:#fff}h1{font-size:23px;margin:0 0 8px}h2{font-size:19px;margin:0 0 15px}p{line-height:1.7;margin:8px 0;color:#53606e}main{max-width:1420px;padding:24px 32px 60px;margin:auto}section{margin:0 0 30px}.flow{display:flex;align-items:stretch;gap:0;overflow:auto;background:white;border-block:1px solid #dce1e7}.flow div{padding:15px 20px;min-width:165px}.flow div+div:before{content:'→';float:left;margin-left:-20px;color:#74839a}.flow strong{display:block;margin:4px 0}.type{font-size:11px;letter-spacing:.08em;color:#516e9f}.muted{color:#65717e;font-size:12px}.branches{padding:10px 0}.workspace{display:grid;grid-template-columns:240px minmax(0,1fr);background:#fff;border:1px solid #dce1e7}nav{padding:10px;border-right:1px solid #dce1e7}button{font:inherit;cursor:pointer;border:0;background:transparent;text-align:left;color:inherit}nav button{display:block;width:100%;padding:14px 12px;border-radius:5px}nav button[aria-selected=true]{background:#eaf0fa;color:#214e95;font-weight:700}button:focus-visible{outline:2px solid #3569b3;outline-offset:2px}article{padding:24px;min-width:0}pre{font:13px/1.85 ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f8fa;padding:18px;color:#263647;margin:16px 0;border-left:3px solid #cbd7e9}.pill{font-size:12px;background:#edf1f7;display:inline-block;padding:4px 8px;margin:0 5px 5px 0;border-radius:3px}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;background:#fff;text-align:left}th,td{padding:14px 12px;border-bottom:1px solid #e0e4e9;vertical-align:top;line-height:1.6}th{font-size:12px;color:#637081;background:#f0f3f7}td:first-child{min-width:190px}.status{font-weight:650;color:#315f98}details{background:white;border-bottom:1px solid #dce1e7;padding:16px 18px}summary{cursor:pointer;font-weight:650}.metrics{display:flex;gap:22px;flex-wrap:wrap;margin:15px 0}.metrics b{display:block;font-size:18px}.note{padding:12px 16px;background:#edf1f6;border-left:3px solid #8094b1}.trace{font-size:12px;max-height:360px;overflow:auto}a{color:#315f98}footer{font-size:12px;color:#65717e;overflow-wrap:anywhere}.stage{border-top:1px solid #dce1e7;margin-top:15px;padding-top:12px}.stage h3{font-size:14px}.toolbar{display:flex;gap:20px;align-items:baseline;justify-content:space-between} @media(max-width:720px){header{padding:20px}main{padding:20px}.workspace{grid-template-columns:1fr}nav{border-right:0;border-bottom:1px solid #dce1e7;display:flex;overflow:auto}nav button{min-width:150px}article{padding:18px}.toolbar{display:block}h1{font-size:21px}}
</style>
<header><h1>정책 판독 · 에이전트와 실험</h1><p>정책을 동적으로 전달하고, 오케스트레이터가 필요한 분업을 선택하는 SDK 실험입니다.</p></header>
<main><section><div class="toolbar"><h2>실행 흐름</h2><span id="model" class="muted"></span></div><div class="flow"><div><span class="type">AGENT</span><strong>오케스트레이터</strong><small>판독 위임 · 필요하면 분업</small></div><div><span class="type">SCRIPT</span><strong>응답 검사</strong><small>스키마 · 허용값 · 참조 ID</small></div><div><span class="type">SCRIPT</span><strong>기존 정답 비교</strong><small>일치하면 종료</small></div><div><span class="type">AGENT</span><strong>불일치 검수</strong><small>원본과 정책으로 독립 검토</small></div><div><span class="type">SCRIPT</span><strong>검수 응답 검사</strong><small>검수 결과에 따라 분기</small></div></div><p class="branches">판독 오류 → 최대 1회 재판독 · 기존 정답 오류 의심 / 정책 모호 → 사람 검수 · 정답 없음 → 비교 없이 결과 보관</p><p class="muted">판례 에이전트는 오케스트레이터가 필요할 때 호출합니다. 이 페이지는 별도 SDK 실험 경로이며 기존 ‘다음 후보 받기’ 버튼의 연결 완료를 의미하지 않습니다.</p></section>
<section><h2>에이전트별 실제 지침</h2><div class="workspace"><nav id="roles" role="tablist" aria-label="에이전트"></nav><article id="role-panel" role="tabpanel"><h2 id="role-name"></h2><p id="role-job"></p><div id="role-tools"></div><pre id="role-prompt"></pre><p id="role-extra"></p><p class="muted">${작업 폴더}는 실행마다 만든 독립 입력 폴더입니다. 목적·규칙·허용값은 policy.txt에서 동적으로 읽습니다.</p></article></div></section>
<section><h2>어려웠던 상품 3건</h2><p class="note">비교 기준은 9/3 역사적 검수 GT, 입력 이미지는 9/21 저장본입니다. 2건은 ORIGINAL_H_FALLBACK, 1건은 FINAL_AQ_20260903입니다. 당시 실험과 이미지 바이트가 같은지는 확인하지 않았습니다. 이번 실험에서 정책·GT를 자동 수정하지 않습니다.</p><div class="table-scroll"><table><thead><tr><th>상품 / 어려웠던 점</th><th>과거 GT</th><th>이번 판독</th><th>상태</th><th>위임 / 이미지 읽기</th></tr></thead><tbody id="results"></tbody></table></div><div id="case-details"></div></section><footer id="footer"></footer></main>
<script id="report-data" type="application/json">__DATA__</script><script>
const data=JSON.parse(document.getElementById('report-data').textContent), el=id=>document.getElementById(id);
const labels={MATCH:'과거 GT 일치',MATCH_AFTER_RETRY:'재판독 후 일치',MISMATCH:'불일치',EXTRACT_ERROR:'판독 오류',GT_SUSPECT:'기존 정답 오류 의심',POLICY_GAP:'정책 모호',HUMAN_REVIEW:'사람 검수',INVALID_RESPONSE:'응답 오류',NO_GOLD:'정답 없음'};
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
el('model').textContent='요청 모델 · '+data.model;
function selectRole(role){for(const b of el('roles').children)b.setAttribute('aria-selected',String(b.dataset.id===role.id));el('role-name').textContent=role.name;el('role-job').textContent=role.job;el('role-prompt').textContent=role.prompt;el('role-extra').textContent=role.extra;el('role-tools').replaceChildren(...role.tools.map(t=>node('span',t,'pill')))}
for(const r of data.roles){const b=node('button',r.name);b.dataset.id=r.id;b.setAttribute('role','tab');b.setAttribute('aria-controls','role-panel');b.onclick=()=>selectRole(r);el('roles').append(b)}selectRole(data.roles[0]);
for(const c of data.cases){const r=c.record,s=c.traceSummary||{},row=node('tr');let first=node('td');first.append(node('strong',c.id+' · '+c.productName),node('div',c.difficulty,'muted'));row.append(first,node('td',c.expected),node('td',r?.output?.value||'—'),node('td',r?(labels[r.status]||r.status):'결과 대기','status'));const coverage=r?.imageReadCoverage;row.append(node('td',r?`위임 요청 ${s.delegationRequests} · 완료 기록 ${s.delegationCompletions||'미관측'}\n이미지 Read 요청 ${s.requestedImageFiles} / ${c.imageCount}장`:'—'));el('results').append(row);
const detail=node('details'),summary=node('summary',c.id+' · 실행 근거와 결과');detail.append(summary);detail.append(node('p','검수 출처: '+(c.expectedSource?.labelSource||'확인 필요')+' · '+(c.expectedSource?.path||'')+':'+(c.expectedSource?.line||''),'muted'));if(!r){detail.append(node('p','저장된 실행 결과가 아직 없습니다. 결과 생성 후 보고서 생성기를 다시 실행하면 반영됩니다.'));el('case-details').append(detail);continue}
const m=node('div',undefined,'metrics');for(const [k,v]of [['단계 수',r.steps.length],['요청 모델',r.modelRequested],['관측 모델',(s.models||[]).join(', ')||'기록 없음'],['판례 후보',r.precedents?.candidateCount??'—']]){const x=node('div');x.append(node('span',k,'muted'),node('b',String(v)));m.append(x)}detail.append(m);detail.append(node('p','이미지 수는 SDK에서 관측한 Read 요청 기준입니다. 하위 에이전트의 완료 훅이 전달되지 않을 수 있어 완료 기록이 없으면 확인 여부를 알 수 없습니다. 요청 수는 내용 이해·판정 품질을 보장하지 않습니다.','muted'));if(r.error)detail.append(node('pre',r.error));if(r.output)detail.append(node('pre',JSON.stringify(r.output,null,2)));
for(const step of r.steps){const area=node('div',undefined,'stage'),resp=step.response||{};area.append(node('h3',step.stage+' · '+(resp.is_error?'실행 오류':'응답 수신')));const trace=resp.trace||[];let reqs=trace.filter(t=>t.event==='tool_request'&&['Agent','Task'].includes(t.tool));if(!reqs.length)reqs=trace.flatMap(t=>(t.tools||[]).filter(b=>['Agent','Task'].includes(b.name)).map(b=>({input:b.input})));for(const t of reqs){area.append(node('p','위임: '+(t.input.subagent_type||'역할 미기록')+' · '+(t.input.description||'')));if(t.input.prompt)area.append(node('pre',t.input.prompt,'trace'))}if(step.stage!=='READ')area.append(node('pre',JSON.stringify(resp.structured_output||resp.result,null,2)));const logs=node('details');logs.append(node('summary','도구 실행 기록 ('+trace.length+'개)'),node('pre',JSON.stringify(trace.filter(t=>t.event!=='assistant'),null,2),'trace'));area.append(logs);detail.append(area)}el('case-details').append(detail)}
el('footer').textContent='생성 시각 '+data.generated+' · 정의 출처 '+data.source+' · 실제 저장 결과만 표시하며 전체 정확도를 추정하지 않습니다.';
</script></html>'''
output=DEST/'agent-prompt-structure.html'
backup=DEST/'agent-prompt-structure.previous.html'
if output.exists() and not backup.exists():shutil.copyfile(output,backup)
output.write_text(html.replace('__DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c')))
print(json.dumps({'path':str(output),'bytes':output.stat().st_size,'cases':len(cases),'completed':sum(c['record'] is not None for c in cases)},ensure_ascii=False))
