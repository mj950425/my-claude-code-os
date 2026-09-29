"""Prepare the frozen target cohort from read-only DB snapshots; no production writes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from datetime import datetime, timezone
import hashlib, io, json, sys, ssl
from PIL import Image, ImageOps
from bson import json_util
ROOT = Path(__file__).resolve().parent
PROJECT = next(p for p in ROOT.parents if (p / ".claude").is_dir())
sys.path.insert(0, str(PROJECT / ".claude/os/engine/scripts"))
sys.path.insert(0, str(PROJECT / ".claude/os/common"))
from incr_collect import detail_urls, ledger_thumbnails
import tile_rule
class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style"): self.hidden+=1
    def handle_endtag(self,tag):
        if tag in ("script","style"): self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip():self.parts.append(data.strip())
def load(name):return json_util.loads((ROOT/name).read_text())
products={r["sellerProductId"]:r for r in load("products.json")}
genders={r["seller_product_id"]:r for r in load("seller_product_gender.json")}
ledgers={r["seller_product_id"]:r for r in load("seller_product_images.json")}
descriptions={}
for r in load("descriptions.json"):descriptions.setdefault(r["sellerProductId"],[]).append(r.get("description") or "")
images_root=ROOT/"images";images_root.mkdir(exist_ok=True)
def prepare(t):
    sid=t["seller_product_id"];product=products[sid];gender=genders[sid]
    if gender.get("gender_target_version") != t["version"]:raise ValueError(f"Serving/target version mismatch: {sid}")
    desc="\n".join(descriptions.get(sid,[]));parsed=Text();parsed.feed(desc)
    thumbs=ledger_thumbnails(ledgers[sid],"https://image.msscdn.net/thumbnails")
    if not thumbs:raise ValueError(f"No thumbnail: {sid}")
    details=[u for u in detail_urls(desc,"") if u not in thumbs]
    paths=[];sources=[];failures=[]
    for role, urls in [("THUMBNAIL",thumbs),("DETAIL",details)]:
        for position,url in enumerate(urls,1):
            digest=hashlib.sha256(url.encode()).hexdigest();raw_path=images_root/(digest+".source")
            try:
                if not raw_path.exists():
                    request=Request(url,headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.musinsa.com/"})
                    try:
                        response=urlopen(request,timeout=30)
                    except Exception as exc:
                        if "Missing Authority Key Identifier" not in str(exc):raise
                        context=ssl.create_default_context()
                        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
                        response=urlopen(request,timeout=30,context=context)
                    with response:raw_path.write_bytes(response.read())
                with Image.open(raw_path) as opened:image=ImageOps.exif_transpose(opened).convert("RGB")
                ranges=tile_rule.tile_ranges(image,tile_rule.CURRENT) if role=="DETAIL" else [(0,image.height)]
                for piece,(top,bottom) in enumerate(ranges,1):
                    file=images_root/f"{digest}-{role}-{piece:03d}.jpg"
                    if not file.exists():
                        tile=image.crop((0,top,image.width,bottom));tile.thumbnail((1200,1600));tile.save(file,quality=92)
                    paths.append(str(file));sources.append({"path":str(file),"role":role,"sourceUrl":url,"sourceIndex":position,"top":top,"bottom":bottom,"sourceSha256":hashlib.sha256(raw_path.read_bytes()).hexdigest()})
            except Exception as exc:failures.append({"role":role,"url":url,"error":str(exc)})
    if not paths:raise ValueError(f"No images fetched: {sid}")
    key=f"{product['platform']}:{product['goodsNo']}"
    return {"id":key,"data":{"productName":product["productName"],"standardCategory":product["standardCategory"],"descriptionText":"\n".join(parsed.parts),"imageRoles":[{"path":s["path"],"role":s["role"],"sourceIndex":s["sourceIndex"]} for s in sources]},"images":paths,"productionValue":gender["gender"],"metadata":{"sellerProductId":sid,"platformProductId":product["goodsNo"],"targetId":str(t["_id"]),"targetVersion":t["version"],"completedAt":t["event_published_at"].isoformat(),"productionSource":gender.get("gender_source"),"productStatus":product["productStatus"],"pdpUrl":f"https://www.musinsa.com/products/{product['goodsNo']}","evidenceSnapshot":"Current catalog/images downloaded for review, not a byte-identical replay of historical inference","imageSources":sources,"imageFailures":failures}}
targets=load("targets.json")["documents"];results={};errors=[]
with ThreadPoolExecutor(max_workers=8) as pool:
    futures={pool.submit(prepare,t):t for t in targets}
    for f in as_completed(futures):
        t=futures[f]
        try:
            result=f.result();results[t["seller_product_id"]]=result
            print(f"Prepared {len(results)}/100: {result['id']} images={len(result['images'])} failures={len(result['metadata']['imageFailures'])}",flush=True)
        except Exception as exc:errors.append({"sellerProductId":t["seller_product_id"],"error":str(exc)});print(errors[-1],flush=True)
cases=[results[t["seller_product_id"]] for t in targets if t["seller_product_id"] in results]
manifest={"version":1,"policyId":"clothing-category-gender","definitions":str(PROJECT/".claude/os/attributes/clothing-category-gender/definitions.md"),"profile":str(PROJECT/".claude/os/attributes/clothing-category-gender/profile.json"),"field":"targetGender","cases":cases,"metadata":{"preparedAt":datetime.now(timezone.utc).isoformat(),"selection":load("targets.json")["filter"],"cohortSize":100,"errors":errors,"decoder":"Pillow","tileVersion":tile_rule.CURRENT,"comparisonSource":"PRODUCTION_PREDICTION"}}
(ROOT/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,default=str))
print("Manifest prepared",len(cases),"errors",len(errors),flush=True)
