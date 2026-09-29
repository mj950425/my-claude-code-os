"""Audit successful Read events from saved SDK traces, including macOS /var aliases."""
from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parent
state=json.loads((ROOT/"review/results.json").read_text())
manifest=json.loads((ROOT/"manifest.json").read_text())
cases={case["id"]:case for case in manifest["cases"]}
audit={}
for key,result in state["results"].items():
    total=len(cases[key]["images"]); observed=set()
    for step in result.get("steps",[]):
        trace=step["response"].get("trace",[])
        roots={Path(t["input"]["file_path"]).parent.resolve() for t in trace if t.get("event")=="tool_completed" and t.get("tool")=="Read" and Path(t.get("input",{}).get("file_path","")).name in {"input.json","policy.txt"}}
        for t in trace:
            if t.get("event")!="tool_completed" or t.get("tool")!="Read":continue
            raw=t.get("input",{}).get("file_path","")
            path=Path(raw);match=re.fullmatch(r"evidence-(\d+)\.[A-Za-z0-9]+",path.name)
            if not match:continue
            if path.is_absolute() and path.parent.resolve() not in roots:continue
            if not path.is_absolute() and not roots:continue
            number=int(match[1])
            if 1<=number<=total:observed.add(f"image-{number:02d}")
    all_ids={f"image-{i:02d}" for i in range(1,total+1)}
    audit[key]={"total":total,"observedReadIds":sorted(observed),"unobservedReadIds":sorted(all_ids-observed),"source":"Successful SDK PostToolUse Read events. Path aliases normalized; reads do not prove reasoning quality."}
(ROOT/"review/image-read-audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2))
print(json.dumps({"cases":len(audit),"fullyObserved":sum(not a["unobservedReadIds"] for a in audit.values()),"imageReads":sum(len(a["observedReadIds"]) for a in audit.values())}))
