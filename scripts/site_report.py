# -*- coding: utf-8 -*-
"""7단계 · 고객용 HTML/MD 보고서. findings.json + scores.json + (에이전트가 쓴) narrative.json → report.html, report.md

py -3 site_report.py ./example-audit --name "매장명" [--compare ./other-audit ...] [--no-inline-shots]

구조는 데이터 폴더의 SEO 진단서·디자인 감사서와 같다:
  머리말 → 점수카드(SEO·GEO·디자인) → 세 지렛대 → 레인별 판정(S/G/D) → 룰셋 밖의 판단 → 실행 계획
  → 점수 시뮬레이션 → (N사이트 비교) → 확인한 범위와 확인하지 못한 것 → 바닥글
narrative.json 이 없으면 기계 문장으로 채우고 QA 가 '판단 미작성' 을 경고한다.
"""
from __future__ import annotations

import argparse
import base64
import html
import os
import time

from _common import apply_narrative, fix_path, load_json, log

E = html.escape

CSS = r"""
:root{--ground:#F7F5F4;--surface:#FFFFFF;--surface-2:#ECE6E7;--ink:#1B1618;--ink-2:#443B3E;--muted:#766B6E;
--line:rgba(27,22,24,.12);--line-strong:rgba(27,22,24,.24);--accent:#6B2C46;--accent-ink:#552036;
--crit:#B3261E;--mid:#A06A20;--good:#2F7A52;--crit-soft:rgba(179,38,30,.10);--mid-soft:rgba(160,106,32,.13);--good-soft:rgba(47,122,82,.11);
--shadow:0 1px 2px rgba(27,22,24,.05),0 8px 24px -16px rgba(27,22,24,.3)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ground:#12100F;--surface:#1B1718;--surface-2:#241F21;--ink:#F1EAEC;--ink-2:#CDC1C5;--muted:#968A8E;
--line:rgba(241,230,235,.13);--line-strong:rgba(241,230,235,.26);--accent:#DE93AF;--accent-ink:#EBB1C6;--crit:#FF8A7A;--mid:#E3B25C;--good:#6FCB93;
--crit-soft:rgba(255,138,122,.14);--mid-soft:rgba(227,178,92,.14);--good-soft:rgba(111,203,147,.14);--shadow:0 1px 2px rgba(0,0,0,.45),0 10px 28px -18px rgba(0,0,0,.9)}}
:root[data-theme="dark"]{--ground:#12100F;--surface:#1B1718;--surface-2:#241F21;--ink:#F1EAEC;--ink-2:#CDC1C5;--muted:#968A8E;
--line:rgba(241,230,235,.13);--line-strong:rgba(241,230,235,.26);--accent:#DE93AF;--accent-ink:#EBB1C6;--crit:#FF8A7A;--mid:#E3B25C;--good:#6FCB93;
--crit-soft:rgba(255,138,122,.14);--mid-soft:rgba(227,178,92,.14);--good-soft:rgba(111,203,147,.14);--shadow:0 1px 2px rgba(0,0,0,.45),0 10px 28px -18px rgba(0,0,0,.9)}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:"IBM Plex Sans KR","Pretendard Variable","Malgun Gothic",system-ui,sans-serif;font-size:16px;line-height:1.75;word-break:keep-all;overflow-wrap:break-word;-webkit-font-smoothing:antialiased}
.wrap{max-width:940px;margin:0 auto;padding:0 24px 96px}
h1,h2,h3,h4{font-family:"Gowun Batang","Noto Serif KR","Batang",serif;font-weight:700;text-wrap:balance;line-height:1.3;margin:0}
p,li{text-wrap:pretty;margin:0}
a{color:var(--accent-ink);text-decoration:none;font-weight:500}a:hover{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
code,.mono{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-size:.86em;font-variant-numeric:tabular-nums}
code{background:var(--surface-2);padding:.12em .38em;border-radius:3px;word-break:break-all}
em{font-style:normal;font-weight:600}
.masthead{padding:64px 0 40px;border-bottom:1px solid var(--line-strong)}
.masthead h1{font-size:clamp(32px,6vw,50px);letter-spacing:-.015em;margin:14px 0 16px}
.masthead .sub{color:var(--ink-2);font-size:17px;max-width:64ch}
.eyebrow{font-size:11.5px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-family:"IBM Plex Mono",monospace}
.meta-row{display:flex;flex-wrap:wrap;gap:8px 28px;margin-top:28px;font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--muted)}
.meta-row b{color:var(--ink-2);font-weight:500}
.scores{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px;margin:44px 0 0}
.score-card{background:var(--surface);border:1px solid var(--line);border-radius:2px;padding:22px 22px 18px;box-shadow:var(--shadow)}
.score-card .lane{font-family:"IBM Plex Mono",monospace;font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.score-card .big{font-family:"Gowun Batang","Noto Serif KR",serif;font-size:60px;line-height:1;font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums;margin:8px 0 2px}
.score-card .big small{font-family:"IBM Plex Mono",monospace;font-size:13px;color:var(--muted);font-weight:400;margin-left:6px}
.score-card .verdict{font-family:"Gowun Batang","Noto Serif KR",serif;font-size:26px;font-weight:700;margin:12px 0 6px;line-height:1.25}
.bars{margin-top:12px}
.bar-row{display:grid;grid-template-columns:minmax(0,1fr) 44px;gap:12px;align-items:center;padding:7px 0;border-bottom:1px solid var(--line)}
.bar-row:first-child{border-top:1px solid var(--line)}
.bar-label{display:flex;align-items:baseline;gap:8px;min-width:0}
.bar-label .name{font-size:13.5px;font-weight:500}
.bar-label .wt{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted)}
.track{grid-column:1/-1;height:4px;background:var(--surface-2);overflow:hidden;margin-top:-2px}
.fill{height:100%;display:block}
.bar-val{font-family:"IBM Plex Mono",monospace;font-size:14px;font-weight:600;text-align:right;font-variant-numeric:tabular-nums}
.s-good{color:var(--good)}.s-mid{color:var(--mid)}.s-crit{color:var(--crit)}
.f-good{background:var(--good)}.f-mid{background:var(--mid)}.f-crit{background:var(--crit)}
.note{font-size:13px;color:var(--muted);margin-top:10px;line-height:1.6}
section{margin-top:68px}
.sec-head{border-bottom:2px solid var(--ink);padding-bottom:10px;margin-bottom:26px}
.sec-head h2{font-size:25px;letter-spacing:-.01em}
.sec-head .lede{color:var(--muted);font-size:14px;margin-top:6px;font-family:"IBM Plex Mono",monospace}
.prose{max-width:70ch}.prose p+p{margin-top:16px}
.thisweek{border:1px solid var(--accent);border-left-width:5px;border-radius:10px;padding:22px 24px;margin:0 0 26px;background:var(--surface)}
.thisweek .tag{font-size:12px;letter-spacing:.08em;color:var(--accent);font-weight:700;margin-bottom:8px}
.thisweek h2{margin:0 0 10px;font-size:22px;line-height:1.35;word-break:keep-all}
.thisweek p{margin:0 0 10px;line-height:1.75;word-break:keep-all}
.thisweek .how{padding:12px 14px;border-radius:8px;background:var(--ground);line-height:1.7;word-break:keep-all}
.thisweek .meta{margin-top:10px;font-size:13px;color:var(--muted)}
.levers{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}
.lever{background:var(--surface);border:1px solid var(--line);border-radius:2px;padding:22px 20px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:10px}
.lever .rank{font-family:"IBM Plex Mono",monospace;font-size:11.5px;font-weight:600;letter-spacing:.1em;color:var(--accent);text-transform:uppercase}
.lever h3{font-size:18px}.lever p{font-size:14px;color:var(--ink-2);line-height:1.65}
.lever .gain{margin-top:auto;padding-top:12px;border-top:1px solid var(--line);font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--muted)}
.lever .gain b{color:var(--good);font-weight:600}
h3.cat{font-size:17px;margin:34px 0 6px;font-family:"IBM Plex Sans KR",sans-serif;font-weight:600}
h3.cat small{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--muted);font-weight:400;margin-left:8px}
.finding{background:var(--surface);border:1px solid var(--line);border-radius:2px;padding:18px 20px;margin-top:12px}
.finding.p0{border-color:var(--crit)}.finding.p1{border-color:var(--mid)}
.f-top{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin-bottom:8px}
.f-top h4{font-size:16px;font-family:"IBM Plex Sans KR",sans-serif;font-weight:600}
.chip{font-family:"IBM Plex Mono",monospace;font-size:11.5px;font-weight:600;letter-spacing:.08em;padding:3px 8px;border-radius:2px;text-transform:uppercase;white-space:nowrap}
.chip.p0{background:var(--crit-soft);color:var(--crit)}.chip.p1{background:var(--mid-soft);color:var(--mid)}.chip.p2{background:var(--surface-2);color:var(--muted)}
.chip.ok{background:var(--good-soft);color:var(--good)}.chip.hold{background:var(--surface-2);color:var(--accent)}.chip.info{background:var(--surface-2);color:var(--muted)}
.finding p{font-size:14.5px;color:var(--ink-2);line-height:1.65}
.evidence{margin-top:10px;padding:8px 0 0;border-top:1px solid var(--line);font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--ink-2);line-height:1.6;word-break:break-all}
.evidence b{color:var(--muted);font-weight:500;margin-right:6px}
.fix{margin-top:8px;font-size:13.5px;color:var(--ink-2)}.fix b{color:var(--good);font-weight:600;margin-right:6px}
.refs{margin-top:6px;font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted)}
details.okgroup{margin-top:14px}details.okgroup summary{cursor:pointer;font-size:14px;color:var(--good);font-weight:500}
.tw{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:2px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:10px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
thead th{font-size:12px;font-weight:600;color:var(--muted);white-space:nowrap;background:var(--surface-2)}
tbody tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.hot td{background:var(--mid-soft)}
.pill{display:inline-block;padding:1px 8px;border-radius:3px;font-size:11.5px;font-weight:600;font-family:"IBM Plex Mono",monospace;white-space:nowrap}
.pill.act{background:var(--crit-soft);color:var(--crit)}.pill.rev{background:var(--mid-soft);color:var(--mid)}.pill.ok{background:var(--good-soft);color:var(--good)}.pill.ref{background:var(--surface-2);color:var(--muted)}.pill.fp{background:var(--surface-2);color:var(--muted)}
.proj{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.proj-step{background:var(--surface);border:1px solid var(--line);border-radius:2px;padding:18px 16px}
.proj-step.now{border-color:var(--accent)}
.proj-step .stage{font-family:"IBM Plex Mono",monospace;font-size:11.5px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase}
.proj-step .val{font-family:"Gowun Batang","Noto Serif KR",serif;font-size:38px;font-weight:700;color:var(--accent);line-height:1.1;margin-top:6px;font-variant-numeric:tabular-nums}
.proj-step .delta{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--good);font-weight:600}
.proj-step .note{font-size:12.5px;margin-top:6px}
.scope{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:22px}
.scope-col h4{font-size:15px;font-family:"IBM Plex Sans KR",sans-serif;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.scope .dot{width:9px;height:9px;border-radius:50%;display:inline-block}.dot.y{background:var(--good)}.dot.n{background:var(--muted)}
.scope ul{padding-left:1.1em;display:flex;flex-direction:column;gap:6px;font-size:14px;color:var(--ink-2)}
.shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-top:14px}
.shots figure{margin:0;border:1px solid var(--line);border-radius:2px;padding:8px}
.shots img{width:100%;height:auto;display:block}
.shots figcaption{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted);margin-top:6px}
footer{margin-top:80px;padding-top:20px;border-top:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.7;font-family:"IBM Plex Mono",monospace}
.judg{background:var(--surface);border:1px solid var(--line);border-radius:2px;padding:24px}
.judg p+p{margin-top:14px}.judg .warn{color:var(--crit);font-weight:600}
@media (max-width:640px){.wrap{padding:0 16px 72px}.masthead{padding:44px 0 30px}section{margin-top:52px}}
"""

LANE_NAMES = {"S": "SEO", "G": "GEO · AEO", "D": "디자인"}
CAT_NAMES = {
    "technical": "기술", "onpage": "온페이지", "content": "콘텐츠", "schema": "구조화 데이터", "performance": "성능", "images": "이미지", "ai": "AI 검색",
    "access": "크롤러 접근", "entity": "기계가독 · 엔티티", "citability": "인용 가능성", "trust": "신뢰 · 권위", "freshness": "최신성", "neo": "네이버(NEO)",
    "slop": "결함 탐지 73룰", "render": "렌더 실측", "a11y": "접근성 · 에이전트 조작",
}
SEV_CLASS = {"P0": "p0", "P1": "p1", "P2": "p2", "OK": "ok", "INFO": "info"}


def color(v: float) -> str:
    return "good" if v >= 80 else "mid" if v >= 60 else "crit"


def bars(cats: dict) -> str:
    out = ['<div class="bars">']
    for k, v in cats.items():
        sc = v["score"]
        out.append(f'<div class="bar-row"><div class="bar-label"><span class="name">{E(CAT_NAMES.get(k, k))}</span><span class="wt">{v["weight"]}%</span></div>'
                   f'<div class="bar-val s-{color(sc)}">{round(sc)}</div><div class="track"><span class="fill f-{color(sc)}" style="width:{sc}%"></span></div></div>')
    out.append("</div>")
    return "".join(out)


def finding_html(f: dict, notes: dict) -> str:
    sev = f["severity"]
    cls = "hold" if f["status"] == "HOLD" else SEV_CLASS.get(sev, "p2")
    chip = "HOLD" if f["status"] == "HOLD" else ("양호" if sev == "OK" else "참고" if sev == "INFO" else sev)
    chip_cls = "hold" if f["status"] == "HOLD" else ("ok" if sev == "OK" else "info" if sev == "INFO" else sev.lower())
    parts = [f'<div class="finding {cls}" id="{E(f["id"])}"><div class="f-top"><span class="chip {chip_cls}">{E(chip)}</span><h4>{E(f["title"])}</h4></div>']
    if f.get("detail"):
        parts.append(f'<p>{E(f["detail"])}</p>')
    if notes.get(f["id"]):
        parts.append(f'<p><em>검토 메모</em> {E(notes[f["id"]])}</p>')
    if f.get("evidence"):
        # 인용된 검출 예시의 긴 줄표는 보고서 자기검사(anti-slop)에 다시 걸리므로 글자 이름으로 바꿔 적는다
        parts.append('<div class="evidence"><b>근거</b>' + "<br>".join(E(str(x).replace("—", "[긴 줄표]")) for x in f["evidence"][:8]) + "</div>")
    if f.get("pages") and len(f["pages"]) <= 12 and f["lane"] == "D":
        parts.append('<div class="refs">페이지 ' + E(" · ".join(f["pages"])) + "</div>")
    if f.get("fix") and f["status"] != "PASS":
        parts.append(f'<div class="fix"><b>조치</b>{E(f["fix"])}</div>')
    # 참조 코드(K##·G-*·NEO-*)는 내부 원장(findings.json)에만 둔다. 고객용 보고서에는 싣지 않는다 (2026-09-03 결정).
    parts.append("</div>")
    return "".join(parts)


def lane_section(lane: str, fs: list[dict], notes: dict, title_extra: str = "") -> str:
    items = [dict(f) for f in fs if f["lane"] == lane]
    cats: dict[str, list] = {}
    for f in items:
        cats.setdefault(f["category"], []).append(f)
    order = {"P0": 0, "P1": 1, "P2": 2, "INFO": 3, "OK": 4}
    out = [f'<section id="lane-{lane}"><div class="sec-head"><h2>{E(LANE_NAMES[lane])}{E(title_extra)}</h2><div class="lede">P0 오늘 · P1 3일 · P2 30일 · HOLD 는 매장 확인 필요</div></div>']
    for cat, lst in cats.items():
        lst.sort(key=lambda x: (0 if x["status"] == "HOLD" and x["severity"] not in ("P0", "P1") else order.get(x["severity"], 9)))
        fails = [f for f in lst if f["status"] != "PASS"]
        oks = [f for f in lst if f["status"] == "PASS"]
        out.append(f'<h3 class="cat">{E(CAT_NAMES.get(cat, cat))}<small>{len(fails)} 항목 · 양호 {len(oks)}</small></h3>')
        for f in fails:
            out.append(finding_html(f, notes))
        if oks:
            out.append('<details class="okgroup"><summary>양호·참고 항목 ' + str(len(oks)) + '개 펼치기</summary>')
            for f in oks:
                out.append(finding_html(f, notes))
            out.append("</details>")
    out.append("</section>")
    return "".join(out)


def simulate(scores: dict, fs: list[dict]) -> list[dict]:
    """P0 → P1 → P2 순서로 감점을 되돌린 SEO/GEO 점수."""
    from site_score import GEO_WEIGHTS, MULT, SEO_AI_FROM_GEO, SEO_WEIGHTS

    def totals(exclude: set[str]):
        def cat_score(lane, cats):
            ded = 0.0
            for f in fs:
                if f["lane"] == lane and f["category"] in cats and f["status"] == "FAIL" and f["severity"] not in exclude:
                    ded += f.get("weight", 0) * MULT.get(f["severity"], 0)
            return max(0.0, 100 - ded * 2.2)
        seo = sum((cat_score("G", SEO_AI_FROM_GEO) if k == "ai" else cat_score("S", (k,))) * w for k, w in SEO_WEIGHTS.items()) / 100
        geo = sum(cat_score("G", (k,)) * w for k, w in GEO_WEIGHTS.items()) / 100
        return round(seo), round(geo)
    steps = []
    prev = (scores["seo"]["total"], scores["geo"]["total"])
    steps.append({"stage": "현재", "seo": prev[0], "geo": prev[1], "now": True})
    for label, ex in (("P0 완료", {"P0"}), ("P1 완료", {"P0", "P1"}), ("P2 완료", {"P0", "P1", "P2"})):
        s, g = totals(ex)
        steps.append({"stage": label, "seo": s, "geo": g, "d_seo": s - prev[0], "d_geo": g - prev[1]})
        prev = (s, g)
    return steps


def b64_img(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) > 900_000:
            return None
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def build(out_dir: str, name: str, compare: list[str], inline_shots: bool) -> tuple[str, str]:
    c = load_json(os.path.join(out_dir, "collect.json")) or {}
    fj = load_json(os.path.join(out_dir, "findings.json")) or {}
    sc = load_json(os.path.join(out_dir, "scores.json")) or {}
    r = load_json(os.path.join(out_dir, "render.json")) or {}
    d = load_json(os.path.join(out_dir, "design.json")) or {}
    nar = load_json(os.path.join(out_dir, "narrative.json")) or {}
    # 사람 판정 적용 — site_score.py 와 같은 함수라 점수카드·시뮬레이션·목록이 한 기준
    fs, _delta = apply_narrative(fj.get("findings", []), d, nar)
    notes = nar.get("finding_notes") or {}
    host = c.get("host") or fj.get("host") or "?"
    today = time.strftime("%Y-%m-%d")
    n_pages = len([p for p, pg in (c.get("pages") or {}).items() if pg.get("status") == 200])
    seo, geo, des = sc.get("seo", {}), sc.get("geo", {}), sc.get("design", {})
    p0 = [f for f in fs if f["severity"] == "P0" and f["status"] == "FAIL"]
    p1 = [f for f in fs if f["severity"] == "P1" and f["status"] == "FAIL"]
    p2 = [f for f in fs if f["severity"] == "P2" and f["status"] == "FAIL"]
    holds = [f for f in fs if f["status"] == "HOLD"]

    title = nar.get("title") or f"{name} 사이트 진단"
    lede = nar.get("lede") or (f"{host} 의 {n_pages}개 페이지를 SEO · GEO/AEO · 디자인 세 축으로 같은 날 같은 방식으로 측정했습니다. "
                               f"P0 {len(p0)}건, P1 {len(p1)}건, P2 {len(p2)}건이 나왔고 매장 확인이 필요한 항목 {len(holds)}건은 HOLD 로 두었습니다.")
    H = [f"<title>{E(title)}</title>",
         '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&family=IBM+Plex+Sans+KR:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">',
         f"<style>{CSS}</style>", '<div class="wrap">']
    # masthead
    H.append(f'<header class="masthead"><div class="eyebrow">사이트 진단 보고서 · site-audit</div><h1>{E(title)}</h1><p class="sub">{E(lede)}</p>'
             f'<div class="meta-row"><span><b>대상</b> {E(host)}</span><span><b>진단일</b> {today}</span><span><b>범위</b> {n_pages}페이지'
             f'{" (" + str(c.get("pages_total")) + "개 캡)" if c.get("pages_capped") else ""}</span>'
             f'<span><b>모드</b> {"라이브" if c.get("mode") == "live" else "로컬(배포 전)"}</span><span><b>도구</b> site-audit · anti-slop {E(str(d.get("tool_version") or "0.4.2"))} · Chromium</span></div></header>')
    # scores
    H.append('<div class="scores">')
    if sc.get("unreachable"):
        H.append('<div class="score-card"><div class="lane">접근 불가</div><div class="verdict">측정하지 못했습니다</div>'
                 f'<p class="note">{E(str((fs[0].get("evidence") or [""])[0]) if fs else "")}</p><p class="note">URL·DNS·방화벽(403/WAF)·타임아웃을 확인한 뒤 다시 실행하세요. 점수·판정은 내지 않았습니다.</p></div>')
        seo, geo, des = {}, {}, {}
    if seo:
        H.append(f'<div class="score-card"><div class="lane">SEO</div><div class="big">{seo["total"]}<small>/ 100</small></div>{bars(seo["categories"])}</div>')
    if geo:
        H.append(f'<div class="score-card"><div class="lane">GEO · AEO (AI 검색 대응)</div><div class="big">{geo["total"]}<small>/ 100</small></div>{bars(geo["categories"])}</div>')
    if des:
        bc = des.get("by_class") or {}
        H.append(f'<div class="score-card"><div class="lane">디자인</div><div class="verdict">{E(des.get("verdict", "미측정"))}</div>'
                 f'<p class="note">73룰 검출 {des.get("total_hits") or 0}건 / {des.get("pages") or 0}페이지 · '
                 + " · ".join(f"{k} {v}" for k, v in bc.items()) + f' · 렌더 결함 {des.get("render_fails", 0)}</p>'
                 f'<p class="note">디자인은 점수 대신 판정으로 씁니다. 검출 대부분이 정당한 선택이거나 오탐이라 숫자로 줄이면 오해가 생깁니다.</p></div>')
    H.append("</div>")
    if not sc.get("unreachable"):
        H.append(f'<p class="note">{E(sc.get("disclaimer", ""))}</p>')
    # 이번 주 할 일 하나 — 사장님이 첫 화면에서 무엇부터 할지 알게 한다.
    # narrative 에 없으면 첫 번째 지렛대를 그대로 쓴다 (지어내지 않고 이미 근거가 있는 것 중 가장 큰 것).
    tw = nar.get("this_week")
    _lv = nar.get("levers") or []
    if not tw and _lv:
        tw = {"title": _lv[0].get("title", ""), "why": _lv[0].get("body", ""), "how": "", "effort": _lv[0].get("gain", "")}
    if tw and tw.get("title"):
        H.append('<section class="thisweek"><div class="tag">이번 주에 할 일 하나</div>')
        H.append(f'<h2>{E(tw.get("title", ""))}</h2>')
        if tw.get("why"):
            H.append(f'<p>{E(tw["why"])}</p>')
        if tw.get("how"):
            H.append(f'<div class="how">{E(tw["how"])}</div>')
        bits = [x for x in (tw.get("effort"), tw.get("owner")) if x]
        if bits:
            H.append(f'<div class="meta">{E(" · ".join(bits))}</div>')
        H.append('<div class="meta">나머지 항목은 이것을 끝낸 뒤에 봐도 됩니다. 아래 목록은 전수 점검 결과이며, 순서는 급한 것부터입니다.</div>')
        H.append("</section>")

    # levers
    levers = nar.get("levers")
    if not levers:
        top = sorted([f for f in fs if f["status"] == "FAIL" and f["severity"] in ("P0", "P1")], key=lambda f: -(f.get("weight", 0) * {"P0": 3, "P1": 2}.get(f["severity"], 1)))[:3]
        levers = [{"title": f["title"], "body": f.get("fix") or f.get("detail") or "", "gain": f"{f['severity']} · 근거 {len(f.get('evidence') or [])}건", "ids": [f["id"]]} for f in top]
    if levers:
        H.append('<section><div class="sec-head"><h2>점수를 올리는 세 개의 지렛대</h2><div class="lede">' + E(nar.get("levers_lede") or "여기서 상승폭 대부분이 나옵니다") + '</div></div><div class="levers">')
        for i, l in enumerate(levers[:3], 1):
            H.append(f'<div class="lever"><div class="rank">지렛대 {i}</div><h3>{E(l.get("title", ""))}</h3><p>{E(l.get("body", ""))}</p><div class="gain">{E(l.get("gain", ""))}</div></div>')
        H.append("</div></section>")
    # lanes
    H.append(lane_section("S", fs, notes, f" · {seo.get('total', '?')}점"))
    H.append(lane_section("G", fs, notes, f" · {geo.get('total', '?')}점"))
    # design lane + screenshots
    H.append(lane_section("D", fs, notes, f" · {des.get('verdict', '')}"))
    shots = []
    for p, e in (r.get("pages") or {}).items():
        for mode in ("mobile_4x", "desktop"):
            s = (e.get(mode) or {}).get("screenshots") or {}
            if s.get("fold"):
                shots.append((p, mode, s["fold"]))
    if shots:
        H.append('<section><div class="sec-head"><h2>첫 화면 (접힘 위)</h2><div class="lede">모바일 390px · 데스크톱 1440px · 위계·CTA·사진은 사람이 봅니다</div></div><div class="shots">')
        for p, mode, rel in shots[:6]:
            src = rel
            if inline_shots:
                b = b64_img(os.path.join(out_dir, rel))
                src = b or rel
            H.append(f'<figure><img src="{E(src)}" alt="{E(p)} {E(mode)} 첫 화면" loading="lazy"><figcaption>{E(p)} · {E(mode)}</figcaption></figure>')
        H.append("</div></section>")
    # judgment
    H.append('<section><div class="sec-head"><h2>룰셋 밖의 판단</h2><div class="lede">탐지기 결과가 아니라 진단자의 의견입니다 · 근거의 무게가 다릅니다</div></div><div class="judg">')
    if nar.get("judgment"):
        for para in nar["judgment"]:
            H.append(f"<p>{E(para)}</p>")
    else:
        H.append('<p class="warn">판단 미작성 · 에이전트가 narrative.json 의 judgment 를 채워야 합니다. (스크린샷·검출 표·본문을 직접 읽고 쓴 의견)</p>')
    H.append("</div></section>")
    # action plan
    H.append('<section><div class="sec-head"><h2>실행 계획</h2><div class="lede">우선순위 · 할 일 · 근거 항목</div></div><div class="tw"><table><thead><tr><th>우선</th><th>작업</th><th>구체적으로 할 일</th><th>레인</th></tr></thead><tbody>')
    plan = nar.get("plan")
    if plan:
        for row in plan:
            H.append(f'<tr><td><span class="chip {row.get("priority", "p2").lower()}">{E(row.get("priority", "P2"))}</span></td><td><b>{E(row.get("title", ""))}</b></td><td>{E(row.get("todo", ""))}</td><td class="num">{E(row.get("effort", ""))}</td></tr>')
    else:
        for f in p0 + p1 + p2[:10]:
            H.append(f'<tr><td><span class="chip {f["severity"].lower()}">{f["severity"]}</span></td><td><b>{E(f["title"])}</b></td><td>{E(f.get("fix") or f.get("detail") or "")}</td><td class="num">{E(LANE_NAMES[f["lane"]])}</td></tr>')
    H.append("</tbody></table></div></section>")
    # simulation
    if seo and geo:
        steps = simulate(sc, fs)
        H.append('<section><div class="sec-head"><h2>점수 시뮬레이션</h2><div class="lede">각 단계 완료 시 예상 온사이트 점수 (SEO / GEO)</div></div><div class="proj">')
        for s in steps:
            dl = f'<div class="delta">SEO {s["d_seo"]:+d} · GEO {s["d_geo"]:+d}</div>' if not s.get("now") else ""
            H.append(f'<div class="proj-step{" now" if s.get("now") else ""}"><div class="stage">{E(s["stage"])}</div><div class="val">{s["seo"]} <span style="font-size:18px;color:var(--muted)">/</span> {s["geo"]}</div>{dl}<div class="note">{E((nar.get("simulation_notes") or {}).get(s["stage"], ""))}</div></div>')
        H.append(f'</div><p class="note">{E(sc.get("disclaimer", ""))}</p></section>')
    # compare
    if compare:
        rows = []
        for cd in [out_dir] + compare:
            s2 = load_json(os.path.join(cd, "scores.json")) or {}
            c2 = load_json(os.path.join(cd, "collect.json")) or {}
            r2 = load_json(os.path.join(cd, "render.json")) or {}
            m2 = ((r2.get("pages") or {}).get("/") or next(iter((r2.get("pages") or {}).values()), {})).get("mobile_4x") or {}
            rows.append((c2.get("host", os.path.basename(cd)), s2.get("seo", {}).get("total"), s2.get("geo", {}).get("total"), s2.get("design", {}).get("verdict"),
                         len(c2.get("pages") or {}), c2.get("body_chars_total"), (m2.get("total_bytes") or 0) / 1048576, m2.get("tbt"), cd == out_dir))
        rows.sort(key=lambda x: -(x[1] or 0))
        H.append(f'<section><div class="sec-head"><h2>{len(rows)}개 사이트 비교</h2><div class="lede">같은 도구 · 같은 가중치</div></div><div class="tw"><table><thead><tr><th>사이트</th><th class="num">SEO</th><th class="num">GEO</th><th>디자인</th><th class="num">페이지</th><th class="num">본문</th><th class="num">모바일 전송</th><th class="num">TBT</th></tr></thead><tbody>')
        for hst, s1, g1, dv, np_, bc_, mb, tbt, me in rows:
            H.append(f'<tr{" class=hot" if me else ""}><td><b>{E(str(hst))}</b></td><td class="num s-{color(s1 or 0)}">{s1}</td><td class="num s-{color(g1 or 0)}">{g1}</td><td>{E(str(dv))}</td><td class="num">{np_}</td><td class="num">{(bc_ or 0):,}자</td><td class="num">{mb:.2f} MB</td><td class="num">{round(tbt or 0)} ms</td></tr>')
        H.append("</tbody></table></div>")
        if nar.get("compare_comment"):
            H.append(f'<div class="prose" style="margin-top:18px"><p>{E(nar["compare_comment"])}</p></div>')
        H.append("</section>")
    # scope
    measured = nar.get("scope_measured") or [
        f"{n_pages}개 페이지 원본 HTML 전수 파싱 (메타·헤딩·링크·이미지·JSON-LD·전화·주소)",
        "robots.txt / sitemap.xml / llms.txt / 404 프로브 / http·www 변형 / 응답 헤더 / TTFB 3회",
        f"Chromium 실측 {len((r.get('pages') or {}))}페이지 (모바일 390px CPU 4× + 데스크톱 1440px): LCP·CLS·TBT·전송량·폰트·콘솔·가로 넘침·글자 크기·탭 타깃" if r and not r.get("error") else "Chromium 실측 없음",
        f"anti-slop 73룰 × {d.get('summary', {}).get('pages', 0)}페이지 (CSS 합친 사본 + 마크업 전용 사본)" if d and not d.get("error") else "anti-slop 미실행",
        f"AI·검색 크롤러 {len((c.get('robots') or {}).get('bots') or {})}종 robots 판정",
    ]
    not_measured = nar.get("scope_not") or [
        "네이버·구글 색인 여부의 확정 · 서치어드바이저·서치콘솔에서 직접 확인",
        "실사용자 CWV(CrUX) · PageSpeed 공식 점수 · 백링크 · 키워드별 순위",
        "네이버 플레이스·구글 비즈니스 프로필의 NAP 대조 (facts.json 또는 /thejsk 로 별도)",
        "AI 브리핑·ChatGPT 등에서의 실제 인용 여부 (naverai / 시크릿창 질문으로 별도 실측)",
        "번역 품질·문구 정확성 · 실제 메뉴 가격의 현행 여부",
    ] + [f"HOLD: {f['title']}" for f in holds[:6]]
    H.append('<section><div class="sec-head"><h2>확인한 범위와 확인하지 못한 것</h2><div class="lede">이 진단서가 무엇을 근거로 하는지</div></div><div class="scope">'
             '<div class="scope-col"><h4><span class="dot y"></span>직접 측정한 것</h4><ul>' + "".join(f"<li>{E(x)}</li>" for x in measured) + '</ul></div>'
             '<div class="scope-col"><h4><span class="dot n"></span>확인하지 못한 것</h4><ul>' + "".join(f"<li>{E(x)}</li>" for x in not_measured) + "</ul></div></div></section>")
    H.append(f'<footer>진단 도구: site-audit (SEO 가중치 claude-seo v2.2.5 · 디자인 탐지 gesso anti-slop 73룰 · Chromium 148{" 모바일 CPU 4× 스로틀" if r.get("throttle") else ""}) · '
             f'기계 판정과 사람 판단을 분리해 적었습니다. 검색 결과·AI 답변은 시점·위치·개인화에 따라 달라집니다.</footer></div>')
    html_out = "\n".join(H)
    # markdown
    M = [f"# {title}", "", lede, "", f"- 대상: {host} · 진단일 {today} · {n_pages}페이지",
         f"- **SEO {seo.get('total', '?')}** / **GEO {geo.get('total', '?')}** / 디자인 **{des.get('verdict', '?')}** (검출 {des.get('total_hits') or 0})", ""]
    if tw and tw.get("title"):
        M += ["## 이번 주에 할 일 하나", "", f"**{tw['title']}**", ""]
        if tw.get("why"):
            M += [tw["why"], ""]
        if tw.get("how"):
            M += ["> " + tw["how"].replace("\n", "\n> "), ""]
        _bits = [x for x in (tw.get("effort"), tw.get("owner")) if x]
        if _bits:
            M += [" · ".join(_bits), ""]
        M += ["나머지 항목은 이것을 끝낸 뒤에 봐도 됩니다.", ""]
    for lbl, lst in (("P0", p0), ("P1", p1), ("P2", p2[:12])):
        if lst:
            M.append(f"## {lbl}")
            for f in lst:
                M.append(f"- [{LANE_NAMES[f['lane']]}] {f['title']}" + (f" · {f['fix']}" if f.get("fix") else ""))
            M.append("")
    if holds:
        M.append("## HOLD (매장 확인 필요)")
        M += [f"- {f['title']}" for f in holds[:10]]
        M.append("")
    if nar.get("judgment"):
        M.append("## 룰셋 밖의 판단")
        M += nar["judgment"]
    return html_out, "\n".join(M)


def main():
    ap = argparse.ArgumentParser(description="site-audit 7단계 보고서")
    ap.add_argument("out")
    ap.add_argument("--name", required=True, help="매장/사이트 이름")
    ap.add_argument("--compare", nargs="*", default=[], help="비교할 다른 감사 폴더")
    ap.add_argument("--no-inline-shots", action="store_true")
    a = ap.parse_args()
    a.out = fix_path(a.out)
    a.compare = [fix_path(x) for x in a.compare]
    html_out, md = build(a.out, a.name, a.compare, not a.no_inline_shots)
    with open(os.path.join(a.out, "report.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    log(f"[report] → {os.path.join(a.out, 'report.html')} ({len(html_out)//1024} KB) + report.md")


if __name__ == "__main__":
    main()
