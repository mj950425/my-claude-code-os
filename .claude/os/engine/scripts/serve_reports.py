#!/usr/bin/env python3
"""산출물을 로컬 웹으로 띄운다. 홈은 실행할 때마다 원본에서 다시 센다.

왜 서버인가 — 보고서는 파일이라 열 때마다 경로를 찾아야 하고, 어느 것이 최신인지
파일 이름으로는 알 수 없다. 상시 뜬 프로세스 하나가 그 둘을 없앤다. 주소는 고정이고,
홈은 요청이 올 때마다 `run-summary.json`·`run-review.json`·`policy-index.json`을
다시 읽는다. 사이클을 다시 돌리면 새로고침만으로 바뀐다.

**여기서 숫자를 만들지 않는다.** 화면에 뜨는 건수는 전부 위 세 파일이 스스로 이름 붙여
기록한 값이다(`primaryFinding.count`, `counts.precedents` …). 서버가 큐를 다시 세면
보고서와 조용히 어긋나므로, 세는 일은 보고서에 맡기고 여기서는 "어디로 가면 그 숫자가
있는지"만 가리킨다. 프로젝트 규칙 8이 문서에 요구하는 것과 같다.

**이름 붙어 있다고 다 싣지는 않는다.** 심사의 큐 잔여 지표는 이름이 세는 것과 다르다 —
뺄셈이 걷어내는 것은 무충돌과 판례뿐이라, 라벨이 이미 일치하는 건과 심판이 "근거만으로
확정할 수 없다"고 적은 건이 그대로 남는다. "지금 가를 수 있는"으로 읽히면 그 자체가 틀린
판단 근거가 되므로 홈에서 뺐다. 심사 쪽이 이름대로 세게 되면 그때 다시 싣는다.

속성을 모른다. 어떤 속성이 있는지는 프로필을 훑어서 알고, 어느 파일이 어느 화면에
걸리는지는 `run-summary.json`의 `artifacts`가 선언한 키로만 안다.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re

import sys
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from catalog_profile import (
    PROJECT_ROOT,
    discover_profiles,
    load_profile,
    output_root,
    project_path,
)

OS_ROOT = PROJECT_ROOT / ".claude" / "os"
DEFAULT_PORT = 7391

# 화면 하나가 어느 산출물을 여는지. 키는 `run-summary.json`의 `artifacts`가 선언한 이름이다 —
# 파일 이름을 여기 적으면 렌더러가 이름을 바꿀 때 조용히 끊긴다.
MENU_ARTIFACTS = {
    "gt": ("gtFixesReport", "suspectGtReport"),
    "gaps": ("policyGapReport",),
    "index": ("htmlReport",),
}
MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".jsonl": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
}


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def words(key: str) -> str:
    """camelCase 키를 읽을 수 있게 띄운다. 보고서의 `words()`와 같은 규칙이다 —
    키 이름을 정하는 것은 속성이므로 엔진은 번역하지 않고 띄우기만 한다."""
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key)).replace("_", " ")


def num(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


# ────────────────────────────── 한 속성의 지금 상태 ──────────────────────────────

class Attribute:
    """프로필 하나와 그 산출물. 읽기만 한다 — 서버는 아무것도 쓰지 않는다."""

    def __init__(self, profile_path: Path) -> None:
        self.profile = load_profile(profile_path)
        self.id = str(self.profile["id"])
        self.root = output_root(self.profile)
        self.summary = read_json(self.root / "run-summary.json", {})
        self.review = read_json(self.root / "run-review" / "run-review.json", {})
        self.policy = read_json(self.root / "policy" / "policy-index.json", {})
        self.status = read_json(self.root / "review" / "status.json", {})
        self.questions = read_json(self.root / "reports" / "policy-questions.json", [])

    @property
    def has_run(self) -> bool:
        return bool(self.summary)

    def artifact(self, key: str) -> Path | None:
        """선언된 산출물만 연다. 선언에 없으면 화면에서도 없는 것으로 둔다."""
        declared = self.summary.get("artifacts")
        if not isinstance(declared, dict) or not declared.get(key):
            return None
        path = project_path(str(declared[key]))
        return path if path.is_file() else None

    def counts(self) -> dict[str, Any]:
        return self.policy.get("counts", {}) if isinstance(self.policy.get("counts"), dict) else {}

    def load(self) -> dict[str, Any]:
        """심사가 낸 사람 몫. 미판정 전체보다 이쪽이 먼저다."""
        value = self.review.get("reviewLoad")
        return value if isinstance(value, dict) else {}

    def owned_policy(self) -> Path | None:
        value = self.policy.get("owned")
        if isinstance(value, dict) and value.get("path"):
            path = project_path(str(value["path"]))
            if path.is_file():
                return path
        return None

    def rel(self, path: Path) -> str | None:
        """OS 폴더 안이면 `/doc`이 열 수 있는 상대 경로로 바꾼다. 밖이면 열지 않는다."""
        try:
            return str(path.resolve().relative_to(OS_ROOT))
        except ValueError:
            return None


def scan() -> list[Attribute]:
    found = []
    for path in discover_profiles():
        try:
            found.append(Attribute(path))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda item: (not item.has_run, item.id))


# ────────────────────────────── 마크다운 ──────────────────────────────
# 정책과 판례는 손으로 쓴 마크다운이다. 원문이 진실이므로 변환은 최소로만 한다 —
# 문서를 예쁘게 만드는 것이 목적이 아니라, 파일을 열지 않고 읽게 하는 것이 목적이다.

FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def front_matter(body: str) -> tuple[dict[str, str], str]:
    match = FRONT_MATTER.match(body)
    if not match:
        return {}, body
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body[match.end():]


def inline(text: str, link: Any) -> str:
    out = esc(text)
    out = INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", out)

    def anchor(match: re.Match[str]) -> str:
        href = link(html.unescape(match.group(2)))
        if href is None:
            return match.group(1)
        return f'<a class="lnk" href="{esc(href)}">{match.group(1)}</a>'

    return LINK.sub(anchor, out)


def render_markdown(body: str, link: Any) -> str:
    """`link(target) -> href|None`이 문서 간 이동을 정한다. None이면 글자로만 남긴다."""
    lines = body.splitlines()
    out: list[str] = []
    index, mode = 0, ""

    def close() -> None:
        nonlocal mode
        if mode:
            out.append(f"</{mode}>")
            mode = ""

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            close()
            index += 1
            block = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            out.append(f"<pre><code>{esc(chr(10).join(block))}</code></pre>")
            index += 1
            continue
        if not stripped:
            close()
            index += 1
            continue
        if stripped.startswith("#"):
            close()
            level = min(len(stripped) - len(stripped.lstrip("#")), 4)
            out.append(f"<h{level + 1}>{inline(stripped.lstrip('# '), link)}</h{level + 1}>")
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and set(lines[index + 1].strip()) <= set("|-: "):
            close()
            cells = lambda row: [c.strip() for c in row.strip().strip("|").split("|")]  # noqa: E731
            head = "".join(f"<th>{inline(c, link)}</th>" for c in cells(line))
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>")
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                row = "".join(f"<td>{inline(c, link)}</td>" for c in cells(lines[index]))
                out.append(f"<tr>{row}</tr>")
                index += 1
            out.append("</tbody></table>")
            continue
        if stripped.startswith(("- ", "* ")):
            if mode != "ul":
                close()
                out.append("<ul>")
                mode = "ul"
            out.append(f"<li>{inline(stripped[2:], link)}</li>")
            index += 1
            continue
        ordered = re.match(r"(\d+)\.\s+(.*)", stripped)
        if ordered:
            if mode != "ol":
                close()
                out.append("<ol>")
                mode = "ol"
            out.append(f"<li>{inline(ordered.group(2), link)}</li>")
            index += 1
            continue
        if stripped.startswith(">"):
            close()
            out.append(f"<blockquote>{inline(stripped.lstrip('> '), link)}</blockquote>")
            index += 1
            continue
        close()
        para = [stripped]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(r"[-*>#|]|\d+\.", lines[index].strip()):
            para.append(lines[index].strip())
            index += 1
        out.append(f"<p>{inline(' '.join(para), link)}</p>")
    close()
    return "\n".join(out)


# ────────────────────────────── 화면 ──────────────────────────────
# 보고서 CSS와 같은 뼈대를 쓰되 톤만 스토어프론트로 옮겼다 — 순흑백, 라운딩 0, 액센트 하나.
# 귀책을 색이 아니라 형태로 구분하는 `.mk`는 보고서의 `.mark`를 그대로 가져온 것이다.

STYLE = r"""
:root{color-scheme:light;
  --ink:#000;--paper:#fff;--muted:#767676;--faint:#A0A0A0;--rule:#E4E4E4;--inset:#F4F4F4;--accent:#D62300;
  --mono:'IBM Plex Mono',ui-monospace,SFMono-Regular,monospace;
  --sans:'Archivo','Gothic A1',-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:14px;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,p,ul,ol,dl,dd,figure{margin:0}
a{color:inherit;text-decoration:none}
.lnk{border-bottom:1px solid #D8D8D8;transition:border-color .16s}
.lnk:hover{border-color:var(--ink)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.mk{display:inline-block;width:11px;height:11px;border:1.5px solid currentColor;flex:0 0 auto}
.mk.gt{background:currentColor}
.mk.op{background:linear-gradient(135deg,currentColor 0 50%,transparent 50% 100%)}
.bar{display:flex;align-items:center;justify-content:space-between;gap:24px;min-height:52px;padding:8px 40px;border-bottom:1px solid var(--ink);flex-wrap:wrap}
.bar b{font-size:15px;font-weight:800;letter-spacing:-.02em}
.bar .where{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.18em;color:var(--faint)}
.bar nav{display:flex;align-items:center;gap:22px;font-family:var(--mono);font-size:11px;letter-spacing:.06em;color:var(--muted);flex-wrap:wrap}
.dot{display:inline-flex;align-items:center;gap:7px;font-weight:600}
.dot i{width:7px;height:7px;display:inline-block;background:currentColor}
.v-WARN{color:var(--accent)}.v-FAIL{color:var(--accent)}.v-PASS{color:var(--ink)}
.hero{padding:46px 40px 40px;border-bottom:1px solid var(--ink)}
.hero .kick{font-family:var(--mono);font-size:10.5px;font-weight:500;letter-spacing:.20em;color:var(--faint);margin-bottom:18px}
.hero .big{margin:0;font-size:56px;font-weight:800;line-height:1.02;letter-spacing:-.05em}
.strip{display:flex;border:1px solid var(--rule);flex-wrap:wrap}
.strip div{padding:14px 22px 15px;border-left:1px solid var(--rule)}
.strip div:first-child{border-left:0}
.strip dt{font-family:var(--mono);font-size:10.5px;letter-spacing:.10em;color:var(--faint)}
.strip dd{margin:5px 0 0;font-family:var(--mono);font-size:21px;font-weight:600;font-variant-numeric:tabular-nums}
.strip dd.sm{font-size:13px;padding-top:5px}
.strip .off dd{color:var(--muted)}
.menus{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-bottom:1px solid var(--ink)}
.menu{display:flex;flex-direction:column;min-width:0;padding:34px 32px 28px;border-right:1px solid var(--rule);transition:background .16s,color .16s}
.menu:last-child{border-right:0}
.menu:hover{background:var(--ink);color:var(--paper)}
.menu:hover .dim{color:rgba(255,255,255,.60)}
.menu:hover .row{border-color:rgba(255,255,255,.22)}
.menu:hover .go{background:var(--paper);color:var(--ink);border-color:var(--paper)}
.menu:hover .hot{color:var(--paper)}
.menu .no{display:inline-flex;align-items:center;gap:11px;font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.16em}
.menu .tag{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;color:var(--faint)}
.menu h2{margin:26px 0 0;font-size:40px;font-weight:800;letter-spacing:-.045em;line-height:1.05}
.menu>p{margin:9px 0 0;font-size:13.5px;line-height:1.6;color:var(--muted)}
.menu .rows{margin-top:26px;margin-bottom:26px}
.row{display:flex;align-items:baseline;justify-content:space-between;gap:14px;padding:11px 0;border-top:1px solid var(--rule)}
.row span{font-size:13px;font-weight:500}
.row b{font-family:var(--mono);font-size:17px;font-weight:600;font-variant-numeric:tabular-nums}
.row b.sm{font-size:11px;letter-spacing:.08em}
.go{align-self:flex-start;display:inline-flex;align-items:center;gap:10px;height:38px;padding:0 17px;margin-top:auto;border:1px solid currentColor;font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.09em;transition:background .16s,color .16s,border-color .16s}
.foot{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:20px;padding:20px 40px;border-bottom:1px solid var(--rule);font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.foot .set{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 28px}
.foot b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.foot .lbl{letter-spacing:.14em;font-size:9.5px;color:var(--faint)}
.end{padding:26px 40px 40px;font-size:12.5px;line-height:1.8;color:var(--muted)}
.end b{font-family:var(--mono);font-size:11.5px}

.head{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:40px;padding:44px 40px 30px;border-bottom:1px solid var(--ink)}
.head .no{display:inline-flex;align-items:center;gap:11px;font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.16em}
.head h1{margin:20px 0 0;font-size:62px;font-weight:800;letter-spacing:-.05em;line-height:.98}
.head p{margin:14px 0 0;font-size:14px;line-height:1.7;color:var(--muted);max-width:60ch}
.chips{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:20px 40px;border-bottom:1px solid var(--rule)}
.chips .lbl{font-family:var(--mono);font-size:9.5px;letter-spacing:.16em;color:var(--faint);margin-right:10px}
.chip{display:inline-flex;align-items:center;height:32px;padding:0 15px;border:1px solid var(--ink);font-family:var(--mono);font-size:11.5px;font-weight:600;letter-spacing:.08em}
.two{display:grid;grid-template-columns:minmax(0,1fr) 400px}
.two>.a{min-width:0;padding:36px 40px 48px;border-right:1px solid var(--rule)}
.two>.b{min-width:0;padding:36px 40px 48px}
.sech{display:flex;align-items:baseline;justify-content:space-between;gap:20px;padding-bottom:12px;border-bottom:1.5px solid var(--ink);margin-bottom:24px}
.sech h2{margin:0;font-size:22px;font-weight:800;letter-spacing:-.035em}
.sech span{font-family:var(--mono);font-size:10px;letter-spacing:.09em;color:var(--faint);word-break:break-all}
.sech b{color:var(--ink);font-weight:600}
.card{display:block;padding:17px 18px 18px;border:1px solid var(--rule);margin-bottom:11px;transition:border-color .16s,background .16s}
a.card:hover{border-color:var(--ink);background:#FAFAFA}
.card .top{display:flex;align-items:center;justify-content:space-between;gap:14px}
.card .top b{font-family:var(--mono);font-size:13px;font-weight:600;letter-spacing:.06em}
.card p{margin:11px 0 0;font-size:13px;line-height:1.62}
.card .meta{margin-top:10px;font-family:var(--mono);font-size:10px;letter-spacing:.06em;color:var(--faint)}
.st{display:inline-flex;align-items:center;gap:7px;font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;padding:3px 8px 2px;border:1px solid currentColor}
.st.OPEN{color:var(--accent)}
.st.DECIDED{color:var(--ink)}
.note{margin-top:22px;padding-top:16px;border-top:1px solid var(--rule);font-size:12.5px;line-height:1.7;color:var(--muted)}
.note b{color:var(--ink);font-weight:600}
.snap{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-top:34px;padding:15px 18px;background:var(--inset);flex-wrap:wrap}
.snap .lbl{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;color:var(--faint)}
.snap .p{margin:6px 0 0;font-family:var(--mono);font-size:12px;color:#2B2B2B;word-break:break-all}
.doc h2{margin:32px 0 10px;font-size:19px;font-weight:800;letter-spacing:-.03em}
.doc h2:first-child,.doc h3:first-child{margin-top:0}
.doc h3{margin:22px 0 8px;font-size:14.5px;font-weight:700;letter-spacing:-.01em}
.doc h4,.doc h5{margin:18px 0 6px;font-size:13px;font-weight:700}
.doc p{margin:0 0 11px;font-size:14px;line-height:1.78;color:#2B2B2B}
.doc ul,.doc ol{margin:0 0 13px;padding-left:19px}
.doc li{font-size:14px;line-height:1.75;color:#2B2B2B;margin-bottom:4px}
.doc code{font-family:var(--mono);font-size:12px;background:var(--inset);padding:1px 5px}
.doc pre{margin:0 0 13px;padding:13px 15px;background:var(--inset);overflow-x:auto}
.doc pre code{background:none;padding:0;font-size:12px;line-height:1.6}
.doc table{width:100%;border-collapse:collapse;margin:0 0 14px}
.doc th,.doc td{text-align:left;padding:9px 14px 9px 0;border-bottom:1px solid var(--rule);font-size:13px;vertical-align:top}
.doc th{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;color:var(--faint);font-weight:500}
.doc blockquote{margin:0 0 13px;padding-left:15px;border-left:2px solid var(--ink);font-size:13.5px;line-height:1.7;color:var(--muted)}
.empty{padding:60px 40px;color:var(--muted);font-size:14px;line-height:1.8}
.empty b{color:var(--ink);font-weight:600}
.empty code{font-family:var(--mono);font-size:12.5px;background:var(--inset);padding:2px 6px}
@media(max-width:1080px){
  .menus{grid-template-columns:1fr}
  .menu{border-right:0;border-bottom:1px solid var(--rule)}
  .two{grid-template-columns:1fr}
  .two>.a{border-right:0;border-bottom:1px solid var(--rule)}
  .hero,.head{grid-template-columns:1fr;gap:26px}
  .hero .big{font-size:38px}
  .head h1{font-size:44px}
  .bar,.hero,.head,.chips,.foot,.two>.a,.two>.b,.end{padding-left:22px;padding-right:22px}
}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;700;800'
    "&family=Gothic+A1:wght@400;500;700;800&family=IBM+Plex+Mono:wght@400;500;600;700"
    '&display=swap" rel="stylesheet">'
)

def shell(title: str, bar: str, body: str) -> bytes:
    return (
        "<!doctype html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{esc(title)}</title>\n{FONTS}\n<style>{STYLE}</style>\n</head>\n<body>\n"
        f"{bar}{body}\n</body>\n</html>\n"
    ).encode("utf-8")


def q(path: str, attribute: Attribute, **extra: str) -> str:
    params = {"a": attribute.id, **extra}
    return f"{path}?{urllib.parse.urlencode(params)}"


def top_bar(attribute: Attribute, others: list[Attribute], crumb: str, right: str = "") -> str:
    switch = ""
    if len(others) > 1:
        links = "".join(
            f'<a class="lnk" href="{esc(q("/", item))}">{esc(item.profile["displayName"])}</a>'
            if item.id != attribute.id
            else f'<span style="color:#000;font-weight:600">{esc(item.profile["displayName"])}</span>'
            for item in others
        )
        switch = f'<span style="display:flex;gap:16px;flex-wrap:wrap">{links}</span>'
    else:
        switch = f'<span>{esc(attribute.profile["displayName"])}</span>'
    return (
        '<div class="bar">'
        f'<div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap">'
        f'<a href="{esc(q("/", attribute))}"><b>CATALOG OS</b></a>'
        f'<span class="where">{esc(crumb)}</span></div>'
        f"<nav>{switch}{right}</nav></div>"
    )


def verdict_dot(attribute: Attribute) -> str:
    verdict = str(attribute.review.get("verdict") or "")
    if not verdict:
        return ""
    return f'<span class="dot v-{esc(verdict)}"><i></i>{esc(verdict)}</span>'


def stamp(attribute: Attribute) -> str:
    generated = str(attribute.summary.get("generatedAt") or "")
    if not generated:
        return ""
    try:
        when = datetime.fromisoformat(generated).astimezone()
        return f"RUN {when:%Y-%m-%d %H:%M}"
    except ValueError:
        return f"RUN {generated[:16]}"


def blank(message: str) -> str:
    return f'<p class="note">{esc(message)}</p>'


def no_run(attribute: Attribute, others: list[Attribute]) -> bytes:
    """사이클을 아직 안 돌린 속성. 화면을 비워 두는 대신 다음 한 걸음을 적는다."""
    return shell(
        f"{attribute.profile['displayName']} — Catalog OS",
        top_bar(attribute, others, "홈"),
        '<div class="empty"><p><b>아직 실행 결과가 없습니다.</b> '
        f'<code>{esc(attribute.rel(attribute.root) or attribute.root)}</code> 아래에 '
        "<code>run-summary.json</code>이 없습니다.</p>"
        "<p style=\"margin-top:14px\">이 속성의 <code>run.sh</code>를 한 번 돌리면 이 화면이 채워집니다.</p></div>",
    )


# ── 홈 ──────────────────────────────────────────────────────────────
def page_home(attribute: Attribute, others: list[Attribute]) -> bytes:
    if not attribute.has_run:
        return no_run(attribute, others)
    load, counts = attribute.load(), attribute.counts()
    finding = attribute.summary.get("primaryFinding")
    finding = finding if isinstance(finding, dict) else {}

    def rows(items: list[tuple[str, str, bool]]) -> str:
        return "".join(
            f'<div class="row"><span>{esc(label)}</span>'
            f'<b class="{"hot" if hot else ""}{" sm" if not value.replace(",", "").isdigit() else ""}">{value}</b></div>'
            for label, value, hot in items
        )

    menus = [
        ("gt", "01", "gt", "GT", "GT 개선", "골든셋이 틀렸다고 볼 근거가 있는 건. 사진과 함께 한 건씩 본다.", rows([
            (str(finding.get("label") or "GT 오류 후보"), num(finding.get("count")), True),
        ])),
        ("policy", "02", "", "POLICY", "정책 보기", "지금 무엇이라 정해져 있나. 소유 정책과 판례, 그리고 가져온 스냅샷.", rows([
            ("소유 정책", "v" + esc(str((attribute.policy.get("owned") or {}).get("version") or "—")), False),
            ("판례", num(counts.get("precedents")), False),
            ("확정된 판례", num(counts.get("decided")), False),
        ])),
        ("gaps", "03", "op", "GAP", "정책 개선", "정책이 답을 못 내는 자리. 군집 하나가 질문 하나가 된다.", rows([
            ("답을 기다리는 질문", num((counts.get("questions") or 0) - (counts.get("questionsResolved") or 0)), True),
            ("정책 질문 전체", num(counts.get("questions")), False),
            ("추적되지 않은 공백", num(counts.get("untrackedReviewViolations")), False),
        ])),
    ]
    tiles = "".join(
        f'<a class="menu" href="{esc(q("/" + route, attribute))}">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;gap:16px">'
        f'<span class="no"><i class="mk {mark}"></i>{no}</span><span class="tag dim">{tag}</span></div>'
        f"<h2>{esc(name)}</h2><p class=\"dim\">{esc(blurb)}</p>"
        f'<div class="rows">{body}</div>'
        '<span class="go">열기 <span style="font-size:13px">&rarr;</span></span></a>'
        for route, no, mark, tag, name, blurb, body in menus
    )

    health = "".join(
        f"<span>{esc(label)} <b>{value}</b></span>"
        for label, value in (
            ("평가", num(attribute.summary.get("products"))),
            ("표면 정확도", f"{float(attribute.summary.get('surfaceAccuracy') or 0):.1%}"),
            ("사람 판정", f"{num(attribute.status.get('adjudicatedProducts'))} / {num(attribute.status.get('queuedProducts'))}"),
        )
    )
    index_link = ""
    if attribute.artifact("htmlReport"):
        index_link = f'<a class="lnk" href="{esc(q("/r", attribute, k="htmlReport"))}">표지 전체 보기 &rarr;</a>'

    body = (
        '<div class="hero"><div>'
        f'<p class="kick">{esc(attribute.profile["subjectName"])} · '
        f'{esc(attribute.profile["attributeName"])}</p>'
        f'<h1 class="big">{esc(attribute.profile["displayName"])}</h1>'
        '</div></div>'
        f'<div class="menus">{tiles}</div>'
        f'<div class="foot"><div class="set"><span class="lbl">실행</span>{health}'
        f'<span>심사 {verdict_dot(attribute) or "—"}</span></div>{index_link}</div>'
        '<div class="end">사람 판정 원장 <b>'
        f'{esc((attribute.summary.get("artifacts") or {}).get("decisionLedger") or "—")}'
        "</b> — 사용자가 확정한 것만 들어간다</div>"
    )
    # 구분자를 글자로 두지 않는다 — 좁은 화면에서 줄이 바뀌면 줄 끝에 홀로 매달린다.
    right = f'<span>{esc(stamp(attribute))}</span>{verdict_dot(attribute)}'
    return shell(f"{attribute.profile['displayName']} — Catalog OS", top_bar(attribute, others, "LOCAL", right), body)


def doc_link(attribute: Attribute, base: Path) -> Any:
    """문서 안의 상대 링크를 이 서버가 열 수 있는 주소로 바꾼다.
    OS 폴더 밖을 가리키면 링크를 걸지 않는다 — 서버가 프로젝트 밖을 열어 주지 않는다."""

    def resolve(target: str) -> str | None:
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return target
        try:
            path = (base.parent / target.split("#", 1)[0]).resolve()
        except OSError:
            return None
        if not path.is_file():
            return None
        try:
            path.relative_to(OS_ROOT)
        except ValueError:
            return None
        if path.suffix == ".md":
            return q("/doc", attribute, p=str(path.relative_to(OS_ROOT)))
        try:
            return f"/f/{urllib.parse.quote(attribute.id)}/{path.relative_to(attribute.root).as_posix()}"
        except ValueError:
            return None

    return resolve


def page_doc(attribute: Attribute, others: list[Attribute], rel: str) -> bytes | None:
    path = (OS_ROOT / rel).resolve()
    try:
        path.relative_to(OS_ROOT)
    except ValueError:
        return None
    if not path.is_file() or path.suffix != ".md":
        return None
    meta, body = front_matter(path.read_text(encoding="utf-8"))
    facts = "".join(
        f"<div><dt>{esc(key)}</dt><dd class=\"sm\">{esc(value)}</dd></div>"
        for key, value in list(meta.items())[:4]
        if value
    )
    facts_block = f'<dl class="strip">{facts}</dl>' if facts else ""
    return shell(
        f"{path.name} — Catalog OS",
        top_bar(attribute, others, f"홈 / {esc(rel)}"),
        '<div class="head"><div>'
        f'<span class="no"><i class="mk"></i>{esc(path.stem)}</span>'
        f"<h1>{esc(meta.get('id') or path.stem)}</h1></div>"
        f'{facts_block}</div>'
        f'<div class="two"><div class="a doc">{render_markdown(body, doc_link(attribute, path))}</div>'
        f'<div class="b"><div class="sech"><h2>원본</h2></div>'
        f'<p style="font-family:var(--mono);font-size:11.5px;line-height:1.8;color:#767676;word-break:break-all">'
        f'.claude/os/{esc(rel)}</p>'
        '<p class="note">이 화면은 읽기만 한다. 고치려면 파일을 직접 연다.</p></div></div>',
    )


# ── GT 개선 ─────────────────────────────────────────────────────────
def page_gt(attribute: Attribute, others: list[Attribute]) -> bytes:
    if not attribute.has_run:
        return no_run(attribute, others)
    load = attribute.load()
    finding = attribute.summary.get("primaryFinding")
    finding = finding if isinstance(finding, dict) else {}
    titles = {
        "gtFixesReport": ("GT 정정 후보", "제안 단위 — 한 제안이 한 장의 조서다. 판독기가 인용한 사진이 함께 실린다."),
        "suspectGtReport": ("의심되는 GT 찾기", "건 단위 — 판독기가 본 이미지를 사람이 다시 보고 GT를 고칠지 정한다."),
    }
    cards = "".join(
        f'<a class="card" href="/f/{esc(attribute.id)}/{esc(path.relative_to(attribute.root).as_posix())}">'
        f'<div class="top"><b>{esc(titles[key][0])}</b>'
        '<span class="st DECIDED">열기 &rarr;</span></div>'
        f"<p>{esc(titles[key][1])}</p>"
        f'<p class="meta">{esc(path.name)}</p></a>'
        for key in MENU_ARTIFACTS["gt"]
        for path in [attribute.artifact(key)]
        if path is not None
    )
    return shell(
        f"GT 개선 — {attribute.profile['displayName']}",
        top_bar(attribute, others, "홈 / GT 개선",
                f'<a class="lnk" href="{esc(q("/policy", attribute))}">정책 보기 &rarr;</a>'),
        '<div class="head"><div><span class="no"><i class="mk gt"></i>01 &nbsp;GT</span>'
        "<h1>GT 개선</h1>"
        f"<p>{esc(finding.get('description') or '골든셋이 틀렸다고 볼 근거가 있는 건을 한 건씩 본다.')}</p></div>"
        '<dl class="strip">'
        f'<div><dt>{esc(finding.get("label") or "주요 신호")}</dt><dd>{num(finding.get("count"))}</dd></div>'
        "</dl></div>"
        f'<div class="two"><div class="a"><div class="sech"><h2>보고서</h2>'
        '<span>건수는 보고서가 임베드된 데이터에서 직접 센다</span></div>'
        f'{cards or blank("아직 만들어진 GT 보고서가 없습니다.")}</div>'
        '<div class="b"><div class="sech"><h2>이 화면이 묻는 것</h2></div>'
        '<p class="note" style="margin-top:0;padding-top:0;border-top:0">'
        "«이 GT가 틀렸나» 하나다. 표면 정확도·처리 건수·정책 버전은 그 답에 기여하지 않으면서 "
        "옆에 있으면 판단에 섞이므로 싣지 않는다.</p>"
        '<p class="note">큐의 잔여 건수도 여기 없다 — 귀책이 GT가 아닌 건이 섞여 있어 '
        f'이 화면의 몫이 아니다. <a class="lnk" href="{esc(q("/", attribute))}">홈에서 보기 &rarr;</a></p>'
        "</div></div>",
    )


# ── 정책 보기 ───────────────────────────────────────────────────────
def page_policy(attribute: Attribute, others: list[Attribute]) -> bytes:
    if not attribute.policy:
        return no_run(attribute, others)
    owned = attribute.policy.get("owned") if isinstance(attribute.policy.get("owned"), dict) else {}
    counts = attribute.counts()
    load = attribute.load()
    blocked = load.get("blockedByPrecedent") or {}
    by_question = {str(item.get("id")): item for item in attribute.questions if isinstance(item, dict)}

    facts = "".join(
        f'<div><dt>{esc(label)}</dt><dd class="{cls}">{esc(value)}</dd></div>'
        for label, value, cls in (
            ("버전", "v" + str(owned.get("version") or "—"), ""),
            ("소유자", owned.get("owner") or "—", "sm"),
            ("갱신", owned.get("updatedAt") or "—", "sm"),
            ("해시", str(owned.get("sha256") or "")[:7] or "—", "sm"),
        )
    )
    labels = owned.get("labels") or attribute.profile.get("labels") or []
    chips = "".join(f'<span class="chip">{esc(label)}</span>' for label in labels)

    path = attribute.owned_policy()
    if path is not None:
        _, text = front_matter(path.read_text(encoding="utf-8"))
        doc = render_markdown(text, doc_link(attribute, path))
        where = attribute.rel(path) or ""
    else:
        doc, where = '<p>소유 정책 파일을 찾지 못했습니다.</p>', ""

    cards = []
    for item in attribute.policy.get("precedents") or []:
        if not isinstance(item, dict):
            continue
        pid, status = str(item.get("id") or ""), str(item.get("status") or "OPEN")
        answered = [by_question.get(a) for a in (item.get("answers") or [])]
        asks = " ".join(str(a.get("question")) for a in answered if a)
        meta = " · ".join(
            part for part in (
                "답하는 질문 " + ", ".join(item.get("answers") or []) if item.get("answers") else "",
                f"걸린 건 {num(blocked[pid])}" if pid in blocked else "",
                "확정 " + str(item.get("decidedBy")) if item.get("decidedBy") else "",
            ) if part
        )
        target = project_path(str(item.get("path"))) if item.get("path") else None
        rel = attribute.rel(target) if target and target.is_file() else None
        open_tag = f'href="{esc(q("/doc", attribute, p=rel))}"' if rel else ""
        mark = '<i class="mk op"></i>' if status == "OPEN" else '<i class="mk gt"></i>'
        cards.append(
            f'<a class="card" {open_tag}><div class="top"><b>{esc(pid)}</b>'
            f'<span class="st {esc(status)}">{mark}{esc(status)}</span></div>'
            f"<p>{esc(asks) or '이 판례가 답하는 질문이 아직 연결되지 않았습니다.'}</p>"
            f'<p class="meta">{esc(meta)}</p></a>'
        )

    imported = attribute.policy.get("imported") if isinstance(attribute.policy.get("imported"), dict) else {}
    snap = ""
    if imported.get("path"):
        target = project_path(str(imported["path"]))
        rel = attribute.rel(target) if target.is_file() else None
        link = f'<a class="lnk" href="{esc(q("/doc", attribute, p=rel))}">스냅샷 열기 &rarr;</a>' if rel else ""
        snap = (
            '<div class="snap"><div><p class="lbl">가져온 스냅샷 · 읽기 전용</p>'
            f'<p class="p">{esc(Path(str(imported["path"])).name)} '
            f'<span style="color:#A0A0A0">· sha {esc(str(imported.get("sha256") or "")[:7])}</span></p></div>'
            f"{link}</div>"
        )

    return shell(
        f"정책 보기 — {attribute.profile['displayName']}",
        top_bar(attribute, others, "홈 / 정책 보기",
                f'<a class="lnk" href="{esc(q("/gaps", attribute))}">정책 개선 &rarr;</a>'),
        '<div class="head"><div><span class="no"><i class="mk"></i>02 &nbsp;POLICY</span>'
        "<h1>정책 보기</h1></div>"
        f'<dl class="strip">{facts}</dl></div>'
        f'<div class="chips"><span class="lbl">허용값</span>{chips}</div>'
        f'<div class="two"><div class="a"><div class="sech"><h2>소유 정책</h2>'
        f"<span>{esc(where)}</span></div>"
        f'<div class="doc">{doc}</div>{snap}</div>'
        f'<div class="b"><div class="sech"><h2>판례</h2>'
        f'<span>확정 <b>{num(counts.get("decided"))}</b> / {num(counts.get("precedents"))}</span></div>'
        f'{"".join(cards) or blank("등록된 판례가 없습니다.")}'
        f'<p class="note">열린 판례에 막혀 <b>{num(load.get("blockedProducts"))}건</b>이 판정 보류다. '
        "하나를 닫으면 그만큼이 큐로 돌아온다.</p></div></div>"
        '<div class="end">손으로 쓴 정책과 판례는 소유 정책 폴더에만 있다. 이 화면은 읽기만 한다.</div>',
    )


# ── 정책 개선 ───────────────────────────────────────────────────────
def page_gaps(attribute: Attribute, others: list[Attribute]) -> bytes:
    if not attribute.has_run:
        return no_run(attribute, others)
    counts = attribute.counts()
    load = attribute.load()
    blocked = load.get("blockedByPrecedent") or {}
    blocked_rows = "".join(
        f'<div class="row"><span>{esc(pid)}</span><b>{num(count)}</b></div>'
        for pid, count in sorted(blocked.items())
    )
    # 판례별 건수를 더하면 총계보다 크다 — 한 상품이 판례 둘 이상에 걸리기 때문이다.
    # 합계를 적어 두지 않으면 읽는 사람이 직접 더해 보고 없는 숫자를 얻는다.
    overlap = sum(int(v or 0) for v in blocked.values()) - int(load.get("blockedProducts") or 0)
    if blocked_rows:
        blocked_rows += (
            f'<div class="row" style="border-top-width:1.5px;border-top-color:#000">'
            f'<span style="font-weight:600">막힌 상품</span><b>{num(load.get("blockedProducts"))}</b></div>'
        )
        if overlap > 0:
            blocked_rows += (
                '<p class="note" style="margin-top:10px;padding-top:0;border-top:0">'
                f'판례별 건수를 더하면 {num(sum(int(v or 0) for v in blocked.values()))}이라 총계보다 '
                f'<b>{num(overlap)}</b> 많다. 한 상품이 판례 둘 이상에 걸려서다 — 더하지 말 것.</p>'
            )
    linked = attribute.policy.get("questionPrecedents") or {}
    status = {
        str(item.get("id")): str(item.get("status") or "")
        for item in (attribute.policy.get("precedents") or []) if isinstance(item, dict)
    }

    cards = []
    for item in attribute.questions:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "")
        impact = item.get("impact") if isinstance(item.get("impact"), dict) else {}
        chips = "".join(
            f'<div class="row"><span>{esc(words(key))}</span><b>{num(value)}</b></div>'
            for key, value in impact.items()
        )
        marks = "".join(
            f'<span class="st {esc(status.get(pid, ""))}">{esc(pid)} {esc(status.get(pid, ""))}</span> '
            for pid in linked.get(qid, [])
        )
        cards.append(
            f'<div class="card"><div class="top"><b>{esc(qid)}</b><span>{marks}</span></div>'
            f'<p style="font-size:14px;line-height:1.6">{esc(item.get("question"))}</p>'
            f'<div style="margin-top:10px">{chips}</div>'
            f'<p class="meta" style="margin-top:12px;color:#767676;letter-spacing:0;font-size:12px;line-height:1.6">'
            f'<b style="font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;color:#A0A0A0;'
            'display:block;margin-bottom:3px">권고안</b>'
            f'{esc(item.get("recommendation"))}</p></div>'
        )

    report = attribute.artifact("policyGapReport")
    report_card = ""
    if report is not None:
        report_card = (
            f'<a class="card" href="/f/{esc(attribute.id)}/{esc(report.relative_to(attribute.root).as_posix())}">'
            '<div class="top"><b>빈 정책 찾기</b><span class="st DECIDED">열기 &rarr;</span></div>'
            "<p>군집 단위 — 판례 하나가 닫는 사례들을 그 질문 아래 모아 둔다.</p>"
            f'<p class="meta">{esc(report.name)}</p></a>'
        )

    return shell(
        f"정책 개선 — {attribute.profile['displayName']}",
        top_bar(attribute, others, "홈 / 정책 개선",
                f'<a class="lnk" href="{esc(q("/policy", attribute))}">정책 보기 &rarr;</a>'),
        '<div class="head"><div><span class="no"><i class="mk op"></i>03 &nbsp;GAP</span>'
        "<h1>정책 개선</h1>"
        "<p>정책이 답을 못 내는 자리. 사람이 한 번 답하면 닫히도록 군집 하나가 질문 하나가 된다.</p></div>"
        '<dl class="strip">'
        f'<div><dt>답을 기다리는 질문</dt><dd>{num((counts.get("questions") or 0) - (counts.get("questionsResolved") or 0))}</dd></div>'
        f'<div class="off"><dt>정책 질문 전체</dt><dd>{num(counts.get("questions"))}</dd></div>'
        f'<div class="off"><dt>추적되지 않은 공백</dt><dd>{num(counts.get("untrackedReviewViolations"))}</dd></div>'
        "</dl></div>"
        f'<div class="two"><div class="a"><div class="sech"><h2>질문</h2>'
        '<span>영향 건수는 policy-questions.json이 사이클마다 다시 센 값이다</span></div>'
        f'{"".join(cards) or blank("열린 정책 질문이 없습니다.")}</div>'
        f'<div class="b"><div class="sech"><h2>보고서</h2></div>{report_card}'
        '<div class="sech" style="margin-top:34px"><h2>막고 있는 판례</h2></div>'
        f'{blocked_rows or blank("판례에 막힌 건이 없습니다.")}'
        '<p class="note">판례 하나를 닫으면 그만큼이 판정 큐로 돌아온다.</p>'
        '<p class="note">추적되지 않은 공백은 아무도 모르는 정책 공백이다. '
        "판례를 만들어 코드를 적으면 «알고 남겨둔 것»으로 바뀐다.</p></div></div>",
    )


# ────────────────────────────── 라우팅 ──────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "CatalogOS"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"{datetime.now():%H:%M:%S} {self.address_string()} {fmt % args}\n")

    def send(self, body: bytes, kind: str = "text/html; charset=utf-8", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        # 산출물은 사이클마다 갈린다. 새로고침이 항상 지금 파일을 보게 한다.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def fail(self, status: int, message: str) -> None:
        self.send(
            shell("찾지 못했습니다 — Catalog OS", "",
                  f'<div class="empty"><p><b>{status}</b> {esc(message)}</p>'
                  '<p style="margin-top:14px"><a class="lnk" href="/">홈으로 &rarr;</a></p></div>'),
            status=status,
        )

    def pick(self, found: list[Attribute], query: dict[str, list[str]]) -> Attribute | None:
        wanted = (query.get("a") or [""])[0]
        for item in found:
            if item.id == wanted:
                return item
        return found[0] if found else None

    def serve_file(self, attribute: Attribute, rel: str) -> None:
        """산출물 폴더 안에서만 연다. 상대 링크가 살아 있도록 폴더 구조를 그대로 노출한다."""
        try:
            path = (attribute.root / urllib.parse.unquote(rel)).resolve()
            path.relative_to(attribute.root.resolve())
        except (ValueError, OSError):
            return self.fail(HTTPStatus.FORBIDDEN, "산출물 폴더 밖은 열지 않습니다.")
        if not path.is_file():
            return self.fail(HTTPStatus.NOT_FOUND, f"파일이 없습니다: {rel}")
        kind = MIME.get(path.suffix.lower(), "application/octet-stream")
        try:
            self.send(path.read_bytes(), kind)
        except OSError as error:
            self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if route == "/healthz":
            return self.send(b"ok\n", "text/plain; charset=utf-8")

        # 매 요청마다 다시 읽는다. 사이클을 다시 돌리면 새로고침만으로 바뀐다.
        found = scan()
        if not found:
            return self.fail(HTTPStatus.NOT_FOUND, "프로필을 찾지 못했습니다.")

        if route.startswith("/f/"):
            _, _, rest = route[3:].partition("/")
            wanted = route[3:].split("/", 1)[0]
            for item in found:
                if item.id == urllib.parse.unquote(wanted):
                    return self.serve_file(item, rest)
            return self.fail(HTTPStatus.NOT_FOUND, "속성을 찾지 못했습니다.")

        attribute = self.pick(found, query)
        if attribute is None:
            return self.fail(HTTPStatus.NOT_FOUND, "속성을 찾지 못했습니다.")

        if route == "/r":
            key = (query.get("k") or [""])[0]
            path = attribute.artifact(key)
            if path is None:
                return self.fail(HTTPStatus.NOT_FOUND, f"선언된 산출물이 없습니다: {key}")
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"/f/{urllib.parse.quote(attribute.id)}/"
                                         f"{path.relative_to(attribute.root).as_posix()}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if route == "/doc":
            body = page_doc(attribute, found, (query.get("p") or [""])[0])
            return self.send(body) if body else self.fail(HTTPStatus.NOT_FOUND, "문서를 찾지 못했습니다.")

        pages = {"/": page_home, "/gt": page_gt, "/policy": page_policy, "/gaps": page_gaps}
        if route not in pages:
            return self.fail(HTTPStatus.NOT_FOUND, f"그런 화면은 없습니다: {route}")
        try:
            self.send(pages[route](attribute, found))
        except Exception as error:  # noqa: BLE001 — 화면 하나가 죽어도 서버는 살아 있어야 한다
            self.log_message("render failed: %s", error)
            self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"화면을 그리지 못했습니다: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="카탈로그 OS 산출물을 로컬 웹으로 띄운다.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CATALOG_OS_PORT", DEFAULT_PORT)))
    parser.add_argument("--host", default=os.environ.get("CATALOG_OS_HOST", "127.0.0.1"))
    args = parser.parse_args()

    found = scan()
    if not found:
        print("프로필을 찾지 못했습니다. 속성 패키지에 profile.json이 필요합니다.", file=sys.stderr)
        return 1

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as error:
        print(f"{args.host}:{args.port}를 열지 못했습니다 — {error}", file=sys.stderr)
        return 1
    server.daemon_threads = True

    print(f"Catalog OS → http://{args.host}:{args.port}", flush=True)
    for item in found:
        mark = "실행 있음" if item.has_run else "실행 전"
        print(f"  · {item.id} ({item.profile['displayName']}) — {mark}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("종료합니다.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
