# -*- coding: utf-8 -*-
"""2단계 — Chromium 실측. 데스크톱 1440 + 모바일 390(CPU 4× · 3G급) 로 성능·렌더·시각 신호를 render.json 에.

py -3 site_render.py https://example.kr --out ./example-audit [--pages / /menu] [--no-throttle]

측정값: LCP/CLS/TBT/FCP/TTFB, 요청·바이트(유형별), 폰트·이미지 상위, DOM 수, 콘솔 오류, 가로 넘침,
첫 가시 헤딩, 11px 미만 텍스트 비율, 44px 미만 탭 타깃 비율, SSR 대비 렌더 본문 길이, 스크린샷.
데이터/*-seo/_perf.py · <사이트H>-seo/seo_measure.py measure_render 를 일반화한 것.
"""
from __future__ import annotations

import argparse
import os
import re
import time
import urllib.parse
from collections import Counter

from _common import UA_MOBILE, fix_path, load_json, log, normalize_origin, now_iso, path_to_filename, save_json

INIT_JS = """
window.__lcp=0;window.__cls=0;window.__lt=[];window.__errs=[];
try{new PerformanceObserver(l=>{for(const e of l.getEntries())window.__lcp=e.startTime}).observe({type:'largest-contentful-paint',buffered:true});}catch(e){}
try{new PerformanceObserver(l=>{for(const e of l.getEntries())if(!e.hadRecentInput)window.__cls+=e.value}).observe({type:'layout-shift',buffered:true});}catch(e){}
try{new PerformanceObserver(l=>{for(const e of l.getEntries())window.__lt.push(e.duration)}).observe({type:'longtask',buffered:true});}catch(e){}
"""

PAGE_JS = r"""() => {
  const n = performance.getEntriesByType('navigation')[0] || {};
  const paint = performance.getEntriesByType('paint').find(p => p.name === 'first-contentful-paint') || {};
  const vw = window.innerWidth, vh = window.innerHeight;
  // 첫 가시 헤딩
  let firstHeading = null, h1 = null;
  const hs = document.querySelectorAll('h1,h2,h3');
  for (const h of hs) {
    const b = h.getBoundingClientRect(); const cs = getComputedStyle(h);
    const vis = b.width > 10 && b.height > 10 && cs.visibility !== 'hidden' && cs.opacity !== '0' && cs.clipPath !== 'inset(50%)';
    if (vis && !firstHeading) firstHeading = {tag: h.tagName, text: (h.innerText || h.textContent).replace(/\s+/g,' ').trim().slice(0,120), top: Math.round(b.top), inViewport: b.top < vh};
  }
  const h1el = document.querySelector('h1');
  if (h1el) { const b = h1el.getBoundingClientRect(); const cs = getComputedStyle(h1el);
    h1 = {text: (h1el.innerText || h1el.textContent).replace(/\s+/g,' ').trim().slice(0,120), w: Math.round(b.width), h: Math.round(b.height), top: Math.round(b.top),
          srOnly: (b.width <= 1 || b.height <= 1 || cs.clipPath === 'inset(50%)' || cs.position === 'absolute' && b.width <= 1), fontSize: cs.fontSize}; }
  // 텍스트 노드 글자 크기 표본 (가시 요소만, 최대 1500개)
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let tiny = 0, total = 0, sizes = {}, node;
  while ((node = walker.nextNode()) && total < 1500) {
    const t = node.textContent.trim(); if (t.length < 2) continue;
    const el = node.parentElement; if (!el) continue;
    const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    const fs = parseFloat(cs.fontSize); total++; sizes[Math.round(fs)] = (sizes[Math.round(fs)] || 0) + 1;
    if (fs < 11) tiny++;
  }
  // 탭 타깃 (모바일 기준으로만 의미)
  const targets = document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,[role=button]');
  let small = 0, tCount = 0; const smallSamples = [];
  for (const t of targets) { const r = t.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(t); if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    tCount++; if (r.width < 44 || r.height < 44) { small++; if (smallSamples.length < 5) smallSamples.push((t.textContent || t.getAttribute('aria-label') || t.tagName).replace(/\s+/g,' ').trim().slice(0,40) + ` ${Math.round(r.width)}x${Math.round(r.height)}`); } }
  // 가로 넘침 + 넘치는 요소 표본
  const de = document.documentElement;
  const overflow = de.scrollWidth > de.clientWidth + 1;
  const wide = []; if (overflow) { for (const el of document.body.querySelectorAll('*')) { const r = el.getBoundingClientRect(); if (r.right > vw + 1 && r.width > 40) { wide.push(el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '') + ` w=${Math.round(r.width)}`); if (wide.length >= 5) break; } } }
  // above-the-fold 내용
  const foldText = []; for (const el of document.body.querySelectorAll('h1,h2,h3,p,a,li,span')) { const r = el.getBoundingClientRect(); if (r.top >= 0 && r.top < vh && r.width > 0 && r.height > 0) { const t = (el.innerText || el.textContent).replace(/\s+/g,' ').trim(); if (t.length > 3 && foldText.length < 40 && !foldText.includes(t.slice(0,80))) foldText.push(t.slice(0,80)); } }
  // 이미지: 실제 표시 크기 대비 원본 과대 (2배 이상)
  let imgs = 0, oversized = 0, noDims = 0; const overs = [];
  for (const im of document.images) { if (!im.naturalWidth) continue; imgs++; const r = im.getBoundingClientRect(); if (r.width > 0 && im.naturalWidth > r.width * 2.2 && im.naturalWidth > 600) { oversized++; if (overs.length < 5) overs.push(`${(im.currentSrc||im.src).split('/').pop().slice(0,50)} ${im.naturalWidth}px→${Math.round(r.width)}px`); } if (!im.getAttribute('width') || !im.getAttribute('height')) noDims++; }
  // 스타일시트/미디어쿼리에서 다크모드 지원 여부
  let darkRules = 0; try { for (const ss of document.styleSheets) { try { for (const r of ss.cssRules) { if (r.media && /prefers-color-scheme/.test(r.media.mediaText)) darkRules++; } } catch(e){} } } catch(e){}
  const bodyCs = getComputedStyle(document.body);
  return {
    lcp: window.__lcp, cls: Math.round(window.__cls * 1000) / 1000,
    tbt: window.__lt.reduce((a, d) => a + Math.max(0, d - 50), 0), longtasks: window.__lt.length,
    fcp: paint.startTime || null, ttfb: n.responseStart || null, domcl: n.domContentLoadedEventEnd || null, load: n.loadEventEnd || null,
    transfer: n.transferSize || null, dom: document.getElementsByTagName('*').length,
    scrollHeight: de.scrollHeight, vw, vh,
    firstHeading, h1, textNodes: total, tinyText: tiny, fontSizeHist: sizes,
    tapTargets: tCount, smallTapTargets: small, smallTapSamples: smallSamples,
    overflowX: overflow, overflowSamples: wide, foldText,
    renderedBodyChars: (document.body.innerText || '').replace(/\s+/g, ' ').trim().length,
    imgs, oversizedImgs: oversized, oversizedSamples: overs, imgsNoDims: noDims,
    darkModeRules: darkRules, bodyFont: bodyCs.fontFamily.slice(0, 80), bodyWordBreak: bodyCs.wordBreak, bodyLineHeight: bodyCs.lineHeight,
    lang: document.documentElement.lang, title: document.title,
    hasViewportMeta: !!document.querySelector('meta[name=viewport]'),
    videos: document.querySelectorAll('video').length, iframes: document.querySelectorAll('iframe').length,
  };
}"""



# --- 접근성 스캔 (axe-core) ---------------------------------------------------
# CSP 가 <script> 주입을 막는 사이트가 있어 add_script_tag 대신 evaluate 로 넣는다.
# CDP Runtime.evaluate 는 페이지 CSP 의 제약을 받지 않는다.
AXE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "axe.min.js")

# 사람에게도 기계(AI 에이전트)에게도 같이 중요한 규칙만 고른다.
# 에이전트는 스크린리더가 읽는 접근성 트리를 그대로 읽는다 (Lighthouse 에이전틱 브라우징 축).
AXE_RULES = {
    "contrast": ["color-contrast"],
    "name": ["button-name", "link-name", "input-button-name", "image-alt", "input-image-alt",
             "label", "select-name", "aria-command-name", "aria-toggle-field-name", "frame-title"],
    "tree": ["aria-required-parent", "aria-required-children", "aria-valid-attr", "aria-valid-attr-value",
             "aria-roles", "aria-hidden-focus", "aria-hidden-body", "duplicate-id-aria", "nested-interactive"],
}
_AXE_SRC = None


def axe_source() -> str | None:
    global _AXE_SRC
    if _AXE_SRC is None:
        try:
            with open(AXE_PATH, encoding="utf-8") as f:
                _AXE_SRC = f.read()
        except OSError:
            _AXE_SRC = ""
    return _AXE_SRC or None


def run_axe(pg) -> dict:
    """페이지에 axe 를 주입해 우리가 고른 규칙만 돌린다. 실패하면 이유를 남기고 빈 결과."""
    src = axe_source()
    if not src:
        return {"error": f"axe-core 없음 ({AXE_PATH}). vendor/axe.min.js 를 내려받으세요."}
    want = [r for group in AXE_RULES.values() for r in group]
    try:
        pg.evaluate(src)
        res = pg.evaluate(
            r"""(rules) => axe.run(document, {
                    runOnly: {type: 'rule', values: rules},
                    resultTypes: ['violations'],
                    elementRef: false
               }).then(r => ({
                    violations: r.violations.map(v => ({
                        id: v.id, impact: v.impact, help: v.help, n: v.nodes.length,
                        nodes: v.nodes.slice(0, 6).map(nd => ({
                            target: (nd.target || []).join(' '),
                            summary: (nd.failureSummary || '').split(String.fromCharCode(10)).slice(0, 3).join(' / ').slice(0, 220),
                            html: (nd.html || '').slice(0, 160)
                        }))
                    })),
                    passes_checked: rules.length
               }))""",
            want,
        )
    except Exception as e:
        return {"error": str(e)[:200]}
    by = {}
    for v in res.get("violations", []):
        for grp, ids in AXE_RULES.items():
            if v["id"] in ids:
                by.setdefault(grp, 0)
                by[grp] += v["n"]
    res["by_group"] = by
    return res


def agent_signals(pg) -> dict:
    """AI 에이전트가 페이지를 조작할 수 있는지에 직결되는 값만 센다.

    라이트하우스의 '에이전틱 브라우징' 은 가중 점수가 아니라 통과 비율이고 아직 개발 중이라고
    공식 문서가 밝히고 있다. 그 점수를 재현하지 않고, 같은 축에서 우리가 실제로 잴 수 있는 것만 센다.
    """
    try:
        return pg.evaluate(r"""() => {
            const sel = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=textbox],[role=combobox],[onclick]';
            const els = Array.from(document.querySelectorAll(sel));
            const vis = els.filter(e => {
                const r = e.getBoundingClientRect();
                const st = getComputedStyle(e);
                return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
            });
            const named = vis.filter(e => {
                const t = (e.getAttribute('aria-label') || e.getAttribute('title') || e.innerText || e.value ||
                           (e.labels && e.labels.length ? e.labels[0].innerText : '') || '').trim();
                if (t) return true;
                const lb = e.getAttribute('aria-labelledby');
                if (lb) return lb.split(/\s+/).some(id => document.getElementById(id));
                const img = e.querySelector('img[alt]:not([alt=""])');
                return !!img;
            });
            const forms = Array.from(document.querySelectorAll('form'));
            const fields = Array.from(document.querySelectorAll('form input:not([type=hidden]),form select,form textarea'));
            const labeled = fields.filter(f => (f.labels && f.labels.length) || f.getAttribute('aria-label') || f.getAttribute('aria-labelledby'));
            const autoc = fields.filter(f => f.getAttribute('autocomplete'));
            return {
                interactive: vis.length,
                unnamed: vis.length - named.length,
                unnamed_samples: vis.filter(e => !named.includes(e)).slice(0, 6)
                    .map(e => (e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.split(/\s+/)[0] : '')).slice(0, 60)),
                forms: forms.length, fields: fields.length,
                fields_labeled: labeled.length, fields_autocomplete: autoc.length,
                landmarks: document.querySelectorAll('main,[role=main],nav,[role=navigation],header,footer').length,
                h1: document.querySelectorAll('h1').length
            };
        }""")
    except Exception as e:
        return {"error": str(e)[:160]}


def measure(pw, url: str, mobile: bool, throttle: bool, shots_dir: str, shot_prefix: str, wait_ms: int = 6000) -> dict:
    b = pw.chromium.launch(args=["--no-sandbox"])
    ctx = b.new_context(
        viewport={"width": 390, "height": 844} if mobile else {"width": 1440, "height": 900},
        device_scale_factor=3 if mobile else 1, is_mobile=mobile, has_touch=mobile,
        user_agent=UA_MOBILE if mobile else None, locale="ko-KR",
    )
    pg = ctx.new_page()
    responses, failed, console = [], [], []
    pg.on("response", lambda r: responses.append(r))
    # ERR_ABORTED 는 브라우저가 스스로 취소한 요청(lazy 이미지 교체·내비게이션)이라 실패로 세지 않는다
    pg.on("requestfailed", lambda r: failed.append({"url": r.url[:160], "error": (r.failure or "")[:80]}) if "ERR_ABORTED" not in (r.failure or "") else None)
    pg.on("console", lambda m: console.append({"type": m.type, "text": m.text[:200]}) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: console.append({"type": "pageerror", "text": str(e)[:200]}))
    cdp = ctx.new_cdp_session(pg)
    if mobile and throttle:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
        cdp.send("Network.enable", {})
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150,
                                                      "downloadThroughput": 1474560, "uploadThroughput": 675000})
    pg.add_init_script(INIT_JS)
    out: dict = {"url": url, "mode": "mobile_4x" if mobile else "desktop", "error": None}
    try:
        try:
            pg.goto(url, wait_until="load", timeout=90000)
        except Exception as e:  # load 이벤트가 끝나지 않는 사이트(롱폴링·무한 스트림) → DOMContentLoaded 로 후퇴
            if "Timeout" not in type(e).__name__ and "timeout" not in str(e).lower():
                raise
            out["load_fallback"] = "domcontentloaded"
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(wait_ms)
        m = pg.evaluate(PAGE_JS)
        out.update(m)
        n_initial = len(responses)   # 여기까지가 '초기 로드'. 이후 스크롤로 내려오는 lazy 자원은 따로 센다
        # 스크린샷: 접힘 위 + 전체(높이 캡)
        os.makedirs(shots_dir, exist_ok=True)
        # scale="css": 모바일 DPR 3 이어도 CSS 픽셀 크기로 저장 (보고서 inline 용량 1/9)
        pg.screenshot(path=os.path.join(shots_dir, f"{shot_prefix}_fold.png"), scale="css")
        try:
            pg.screenshot(path=os.path.join(shots_dir, f"{shot_prefix}_full.png"), full_page=True, scale="css")
        except Exception as e:  # 너무 긴 페이지
            out["full_shot_error"] = str(e)[:120]
        out["screenshots"] = {"fold": f"screenshots/{shot_prefix}_fold.png", "full": f"screenshots/{shot_prefix}_full.png"}
        out["axe"] = run_axe(pg)
        out["agent"] = agent_signals(pg)
        # 천천히 스크롤해 lazy 자원까지 집계
        h = min(int(m.get("scrollHeight") or 0), 12000)
        for y in range(0, h, 900):
            pg.evaluate(f"window.scrollTo(0,{y})")
            pg.wait_for_timeout(250)
        pg.wait_for_timeout(1500)
        after = pg.evaluate("() => ({cls: Math.round(window.__cls*1000)/1000, dom: document.getElementsByTagName('*').length, imgs: document.images.length})")
        out["afterScroll"] = after
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        n_initial = len(responses)
    # 네트워크 집계 — 초기 로드(initial) 와 스크롤 후(total) 를 구분한다
    agg: dict[str, dict] = {}
    total = 0
    initial_bytes = 0
    scroll_by_type: dict[str, int] = {}
    fonts, images, scripts, hosts = [], [], [], Counter()
    seen_urls = Counter()
    for idx, r in enumerate(responses):
        try:
            ct = (r.headers.get("content-type") or "").split(";")[0].strip()
            sz = int(r.headers.get("content-length") or 0)
            u = r.url
            st = r.status
        except Exception:
            continue
        if not sz:
            # br/gzip 청크 전송은 content-length 가 없다 → 실제 전송 바이트(sizes) 로 대체
            try:
                sizes = r.request.sizes()
                sz = int(sizes.get("responseBodySize") or 0) + int(sizes.get("responseHeadersSize") or 0)
            except Exception:
                sz = 0
        seen_urls[u] += 1
        kind = ("script" if "javascript" in ct or u.endswith(".js") else "css" if "css" in ct else "image" if "image" in ct
                else "font" if "font" in ct or u.split("?")[0].endswith((".woff2", ".woff", ".ttf", ".otf")) else "html" if "html" in ct
                else "video" if "video" in ct or ".m3u8" in u or ".ts" in u.split("?")[0][-3:] else "json" if "json" in ct else "other")
        a = agg.setdefault(kind, {"n": 0, "bytes": 0})
        a["n"] += 1
        a["bytes"] += sz
        total += sz
        if idx < n_initial:
            initial_bytes += sz
        else:
            scroll_by_type[kind] = scroll_by_type.get(kind, 0) + sz
        try:
            hosts[urllib.parse.urlsplit(u).netloc] += sz
        except Exception:
            pass
        if kind == "font":
            fonts.append((sz, u))
        elif kind == "image":
            images.append((sz, u))
        elif kind == "script":
            scripts.append((sz, u))
        if st >= 400:
            failed.append({"url": u[:160], "error": f"HTTP {st}"})
    fonts.sort(reverse=True)
    images.sort(reverse=True)
    scripts.sort(reverse=True)
    out.update({
        "requests": len(responses), "requests_initial": n_initial,
        "total_bytes": initial_bytes,            # 초기 로드 전송량 (기존 보고서·CWV 관행과 같은 기준)
        "total_bytes_after_scroll": total,       # 끝까지 스크롤한 뒤 누적 (lazy 이미지·영상 포함)
        "scroll_added_bytes": total - initial_bytes, "scroll_added_by_type": scroll_by_type,
        "by_type": agg,
        "fonts": {"n": len(fonts), "bytes": sum(s for s, _ in fonts), "top": [{"kb": round(s / 1024, 1), "file": u.split("/")[-1][:70]} for s, u in fonts[:8]]},
        "images_net": {"n": len(images), "bytes": sum(s for s, _ in images), "top": [{"kb": round(s / 1024, 1), "file": u.split("/")[-1][:70]} for s, u in images[:6]]},
        "scripts_net": {"n": len(scripts), "bytes": sum(s for s, _ in scripts), "top": [{"kb": round(s / 1024, 1), "file": u.split("/")[-1][:70]} for s, u in scripts[:5]]},
        "third_party_hosts": [{"host": h, "kb": round(b / 1024, 1)} for h, b in hosts.most_common(8) if h != urllib.parse.urlsplit(url).netloc],
        "duplicate_requests": sum(c - 1 for c in seen_urls.values() if c > 1),
        "failed_requests": failed[:15], "console": console[:20],
        "console_errors": sum(1 for c in console if c["type"] in ("error", "pageerror")),
    })
    ctx.close()
    b.close()
    return out


def main():
    ap = argparse.ArgumentParser(description="site-audit 2단계 Chromium 실측")
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", nargs="*", default=None, help="측정할 경로 (기본: / + collect.json 의 상위 2개). 'all' 이면 전 페이지(모바일, 캡 --max-render)")
    ap.add_argument("--max-render", type=int, default=12)
    ap.add_argument("--no-throttle", action="store_true")
    ap.add_argument("--wait", type=int, default=6000, help="load 후 대기 ms")
    a = ap.parse_args()
    a.out = fix_path(a.out)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        save_json(os.path.join(a.out, "render.json"), {"error": f"playwright 없음: {e}"})
        log(f"[render] SKIP — playwright 없음: {e}")
        return
    origin = normalize_origin(a.url)
    pages = a.pages
    if pages:
        # Git Bash(MSYS) 가 "/ko/" 를 "C:/Program Files/Git/ko/" 로 바꾸는 문제 → 되돌리고, 선행 슬래시 보장
        fixed = []
        for p in pages:
            for part in p.split(","):
                part = part.strip()
                if not part:
                    continue
                m = re.search(r"/Git(/.*)$", part)
                if m:
                    part = m.group(1)
                if not part.startswith("/"):
                    part = "/" + part
                fixed.append(part)
        pages = fixed
    if pages == ["all"]:
        # 전 페이지 렌더 (모바일만, 캡 12) — 비교 배치처럼 전 페이지 신뢰도가 필요할 때
        c = load_json(os.path.join(a.out, "collect.json")) or {}
        pages = ["/"] + [p for p, pg in (c.get("pages") or {}).items() if p != "/" and pg.get("status") == 200 and not pg.get("internal_page")][: a.max_render - 1]
    if pages is None:
        pages = ["/"]
        c = load_json(os.path.join(a.out, "collect.json"))
        if c:
            # 본문이 긴 순으로 2개 추가 (홈 제외, 메뉴/소개/FAQ 우선)
            cands = [(p, pg.get("body_chars", 0) or 0) for p, pg in c["pages"].items() if p != "/" and pg.get("status") == 200]
            pri = [p for p, _ in sorted(cands, key=lambda x: -x[1])]
            for key in ("menu", "faq", "about", "reserv", "story"):
                for p in pri:
                    if key in p and p not in pages and len(pages) < 3:
                        pages.append(p)
            for p in pri:
                if p not in pages and len(pages) < 3:
                    pages.append(p)
    shots = os.path.join(a.out, "screenshots")
    res = {"origin": origin, "measured_at": now_iso(), "throttle": not a.no_throttle, "pages": {}}
    with sync_playwright() as pw:
        for i, p in enumerate(pages):
            url = origin + p
            name = path_to_filename(p)
            log(f"[render] {p} — mobile 4x …")
            t0 = time.time()
            entry = {"mobile_4x": measure(pw, url, True, not a.no_throttle, shots, f"{name}_mobile", a.wait)}
            if i == 0:
                log(f"[render] {p} — desktop …")
                entry["desktop"] = measure(pw, url, False, False, shots, f"{name}_desktop", a.wait)
            entry["elapsed_s"] = round(time.time() - t0, 1)
            res["pages"][p] = entry
            m = entry["mobile_4x"]
            if m.get("error"):
                log(f"   ERROR {m['error']}")
            else:
                log(f"   LCP {round(m.get('lcp') or 0)}ms  CLS {m.get('cls')}  TBT {round(m.get('tbt') or 0)}ms  "
                    f"req {m.get('requests_initial')}/{m.get('requests')}  {round((m.get('total_bytes') or 0)/1048576,2)}MB(+{round((m.get('scroll_added_bytes') or 0)/1048576,2)} 스크롤)  overflow={m.get('overflowX')}  "
                    f"tiny {m.get('tinyText')}/{m.get('textNodes')}  tap<44 {m.get('smallTapTargets')}/{m.get('tapTargets')}  errs {m.get('console_errors')}")
    save_json(os.path.join(a.out, "render.json"), res)
    log(f"[render] → {os.path.join(a.out, 'render.json')}")


if __name__ == "__main__":
    main()
