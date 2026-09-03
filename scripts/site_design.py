# -*- coding: utf-8 -*-
"""3단계 — 디자인 결정적 검출. gesso anti-slop 73룰을 'CSS 합친 사본'과 '마크업 전용 사본' 두 벌에 돌려
CSS 유래/마크업 유래를 분리하고, 언어·문맥 규칙으로 1차 분류(조치/검토/정당/참고/오탐)를 붙여 design.json 에.

py -3 site_design.py ./example-audit [--pages / /menu ...]

방법론 출처: 데이터/*-design-audit.html (사이트 A·사이트 K·사이트 L 감사에서 정립).
- 외부 스타일시트를 각 페이지에 inline 해야 style 룰이 CSS 를 본다 (externalStylesheets=0 이어야 완전 판정).
- 마크업만 남긴 사본으로 한 번 더 돌려 CSS 유래 건수를 분리한다.
- 분류는 초안이다. 최종 판정은 에이전트가 narrative.json 의 design_overrides 로 확정한다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import urllib.parse

from _common import decode, fetch, fix_path, load_json, log, path_to_filename, save_json, scrub

# 룰별 기본 분류 (gesso 0.4.2 룰 id 기준). 없는 룰은 tier 로 추정.
RULE_CLASS = {
    # 조치 — 거의 항상 고칠 것
    "indigo-accent": "조치", "heavy-box-shadow": "조치", "emoji-icon": "조치", "nested-cards": "조치",
    "lorem-ipsum": "조치", "placeholder-image": "조치", "transition-all": "조치", "gradient-text": "조치",
    "hero-kicker-eyebrow": "검토", "fake-masthead": "조치", "edge-stripe": "조치", "two-tone-headline": "조치",
    "bare-hr": "검토", "boxless-body": "조치", "hero-badge": "조치", "dot-chart": "조치", "decorated-gauge": "조치",
    "justified-text": "조치", "unreachable-color": "검토",
    # 검토 — 문맥에 따라 다름
    "em-dash-copy": "검토", "tiny-body-text": "검토", "tight-line-height": "검토", "wide-body-tracking": "검토",
    "redundant-border": "검토", "gradient-fill": "검토", "decorative-divider": "검토", "hover-scale-image": "참고",
    "oversized-number": "검토", "row-kicker-eyebrow": "참고", "row-as-card": "참고",
    # 참고 — advisory
    "overstuffed-row": "참고", "multiline-row-meta": "참고", "single-font-page": "참고",
    "broken-image": "조치",
}
ADVISORY_RULES = {"overstuffed-row", "multiline-row-meta", "row-kicker-eyebrow", "row-as-card", "hover-scale-image", "single-font-page"}
GATING_DEFAULT = "검토"

ISSUE_RE = re.compile(r"^\[(?P<cat>[^/\]]+)/(?P<rule>[^\]]+)\]\s*(?P<n>\d+)x:\s*(?P<msg>.*)$", re.S)
PRICE_RE = re.compile(r"(₩|원|KRW|\d{1,3}(,\d{3})+)")
CJK_DASH_LANGS = {"ja", "zh", "zh-hant", "zh-hans", "zh-tw", "zh-cn"}


def npx_cmd() -> str | None:
    return shutil.which("npx.cmd") or shutil.which("npx")


def run_antislop(target: str) -> dict:
    cmd = npx_cmd()
    if not cmd:
        return {"error": "npx 없음 (Node.js 필요)"}
    try:
        p = subprocess.run([cmd, "-y", "@gessobuild/anti-slop", "check", target, "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = p.stdout.strip()
    # npx 가 앞에 설치 로그를 찍을 수 있어 첫 '{' 부터 파싱
    i = out.find("{")
    if i < 0:
        if p.returncode == 2:
            return {"error": "검사할 HTML 이 없음 (design/ 사본 0개 · collect 가 200 HTML 페이지를 못 얻었을 가능성)"}
        return {"error": scrub(f"anti-slop 출력 없음 (exit {p.returncode}) stderr={p.stderr[-300:]}")}
    try:
        data = json.loads(out[i:])
    except Exception as e:
        return {"error": f"JSON 파싱 실패: {e}; head={out[i:i+200]}"}
    data["exit_code"] = p.returncode
    return data


def inline_css(html: str, page_url: str, cache: dict) -> tuple[str, int, int]:
    """<link rel=stylesheet> 를 <style> 로 치환. (결과 html, 인라인 성공 수, 실패 수)"""
    ok = fail = 0

    def repl(m):
        nonlocal ok, fail
        tag = m.group(0)
        if not re.search(r'rel=["\']?[^"\'>]*stylesheet', tag, re.I):
            return tag
        href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
        if not href:
            return tag
        u = urllib.parse.urljoin(page_url, href.group(1))
        if "fonts.googleapis" in u or "fonts.gstatic" in u:
            return tag  # 폰트 서비스는 탐지기가 제외
        if u not in cache:
            r = fetch(u, timeout=30)
            cache[u] = decode(r["body"], r["headers"].get("content-type", "")) if r["status"] == 200 else None
        css = cache[u]
        if css is None:
            fail += 1
            return tag
        ok += 1
        return f"<style data-inlined-from=\"{u}\">\n{css}\n</style>"

    out = re.sub(r"<link\b[^>]*>", repl, html, flags=re.I)
    return out, ok, fail


def strip_styles(html: str) -> str:
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    html = re.sub(r"<link\b[^>]*stylesheet[^>]*>", "", html, flags=re.I)
    return html


def classify(rule: str, msg: str, lang: str | None, advisory: bool, samples: list[str] | None = None) -> tuple[str, str]:
    """(분류, 근거 한 줄). 초안 규칙이며 에이전트가 덮어쓸 수 있다. samples = 탐지기가 인용한 실제 검출 예시."""
    l = (lang or "").lower()
    samples = samples or []
    if rule == "em-dash-copy":
        if l.startswith("ko"):
            return "조치", "한국어에는 문장 중간 긴 줄표 관행이 없음 — 쉼표·가운뎃점·문장 분리"
        if l in CJK_DASH_LANGS or l.startswith(("ja", "zh")):
            return "정당", "일본어 두 배 대시·중국어 파절호는 표준 부호"
        if l.startswith("en"):
            return "검토", "영어에서 줄표는 정상 부호이나 빈도가 높으면 기계 작성 인상"
        return "검토", "언어 미상 — 페이지 lang 확인"
    if rule == "oversized-number":
        # 룰 설명문 자체에 "$1,842,000" 이 들어 있어 msg 로 판정하면 항상 오탐이 된다 → 실제 예시(samples)로만 판정
        if samples and all(PRICE_RE.search(x) for x in samples):
            return "오탐", "원화 가격·금액은 축약하지 않는 것이 표준"
        if samples and any(PRICE_RE.search(x) for x in samples):
            return "검토", "가격이 섞여 있음. 가격은 오탐, 통계·집계 수치는 조치"
        return "검토", "순서·호실·전화번호면 오탐, 통계 수치면 조치"
    if rule == "gradient-fill":
        return "검토", "사진 위 어둠 그라디언트(가독성 장치)면 정당, 장식이면 조치"
    if rule == "wide-body-tracking":
        return "검토", "브랜드 라벨·구간 제목의 넓은 자간이면 정당, 본문이면 조치"
    if rule == "redundant-border":
        return "검토", "어두운 바탕 위 반투명 면이면 테두리가 경계 역할 → 정당"
    if rule == "tiny-body-text":
        return "검토", "11px 미만은 읽기 하한선. 캡션·각주라도 11.5px 이상 권장"
    if advisory:
        return "참고", RULE_WHY.get(rule, "판정 점수에 반영되지 않는 등급")
    return RULE_CLASS.get(rule, GATING_DEFAULT), RULE_WHY.get(rule, "")


# 보고서에 들어갈 한 줄 설명 (anti-slop 영문 설명 대신)
RULE_WHY = {
    "broken-image": "src 가 비었거나 자리표시자인 img 는 깨진 이미지 상자로 보인다",
    "emoji-icon": "이모지가 아이콘 자리를 대신하면 OS 마다 다르게 그려지고 생성물 인상을 준다. 인라인 SVG 로",
    "nested-cards": "카드 안에 또 카드(면·테두리·둥근 모서리)를 두면 층이 구분되지 않는다. 안쪽은 여백·구분선만",
    "heavy-box-shadow": "여러 겹 큰 그림자는 '떠 있는 카드' 생성물 지문. 한 겹, 흐림 작게",
    "indigo-accent": "기본 인디고·보라 액센트는 가장 흔한 생성 UI 지문. 브랜드 색으로",
    "transition-all": "transition: all 은 의도치 않은 속성까지 애니메이션. 속성을 지정",
    "lorem-ipsum": "채움 문구가 남아 있다",
    "placeholder-image": "자리표시자 이미지가 남아 있다",
    "hero-kicker-eyebrow": "H1 위 대문자 작은 키커. 정보는 제목 아래 설명 문장으로 옮기면 형태만 달라진다",
    "row-kicker-eyebrow": "반복 항목마다 대문자 소제목. 라틴 대문자 기준이라 한국어판은 걸리지 않는다",
    "overstuffed-row": "한 줄에 슬롯이 많다(이름·설명·중량·가격). 메뉴 목록에서 흔한 참고 등급",
    "multiline-row-meta": "반복 항목의 설명이 두 줄 이상. 설명이 길어 생기는 참고 등급",
    "hover-scale-image": "이미지 hover 확대. 확대율 낮고 두어 곳이면 절제된 편",
    "decorative-divider": "장식용 선(대시 나열). 일본어 두 배 대시가 잇달아 쓰이면 오탐",
    "single-font-page": "한 글꼴로 전체를 끄는 구성. 제목·본문 대비가 없다는 참고",
    "gradient-text": "글자에 그라디언트 클리핑. 생성물 지문",
    "justified-text": "양끝맞춤은 한글에서 단어 사이가 벌어진다. 왼끝맞춤으로",
    "over-rounded-card": "지나치게 둥근 카드 모서리",
    "underlined-text": "본문 밑줄. 링크는 색·굵기로",
    "unreachable-color": "선언됐지만 화면에 닿지 않는 색(변수·미사용 규칙)",
    "bare-hr": "맨 hr 구분선",
    "boxless-body": "body 에 배경·여백이 없어 레이아웃이 무너진다",
    "wide-body-tracking": "본문 크기 글자에 0.08em 이상 자간",
    "tiny-body-text": "11px 미만 글자",
    "tight-line-height": "행간 1.2 미만. 한글 받침이 윗줄에 닿는다",
    "redundant-border": "배경 있는 면에 테두리를 또 둘렀다",
    "gradient-fill": "채움 그라디언트",
    "em-dash-copy": "문장 중간 긴 줄표",
    "oversized-number": "축약하지 않은 긴 숫자",
}


def main():
    ap = argparse.ArgumentParser(description="site-audit 3단계 디자인 검출 (anti-slop)")
    ap.add_argument("out")
    ap.add_argument("--pages", nargs="*", default=None, help="검사할 경로 (기본: 200 응답 HTML 전부, 최대 12)")
    ap.add_argument("--max-pages", type=int, default=12)
    a = ap.parse_args()
    a.out = fix_path(a.out)
    c = load_json(os.path.join(a.out, "collect.json"))
    if not c:
        raise SystemExit("collect.json 없음 · site_collect.py 먼저")
    pages = a.pages or [p for p, pg in c["pages"].items() if pg.get("status") == 200 and "html" in (pg.get("content_type") or "html")][: a.max_pages]
    d_inl = os.path.join(a.out, "design", "inlined")
    d_mk = os.path.join(a.out, "design", "markup")
    for d in (d_inl, d_mk):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    cache: dict = {}
    meta: dict = {}
    src_dir = c.get("site_dir") if c.get("mode") == "local" else a.out
    for p in pages:
        pg = c["pages"][p]
        raw = os.path.join(src_dir, pg["raw_path"])
        if not os.path.exists(raw):
            continue
        with open(raw, encoding="utf-8", errors="replace") as f:
            html = f.read()
        name = path_to_filename(p) + ".html"
        if c.get("mode") == "local":
            # 로컬: 상대 경로 CSS 를 파일에서 읽어 inline
            def repl_local(m, base=os.path.dirname(raw)):
                tag = m.group(0)
                if not re.search(r"stylesheet", tag, re.I):
                    return tag
                href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
                if not href or href.group(1).startswith("http"):
                    return tag
                fp = os.path.normpath(os.path.join(base, href.group(1).split("?")[0]))
                if os.path.exists(fp):
                    with open(fp, encoding="utf-8", errors="replace") as f2:
                        return f"<style data-inlined-from=\"{href.group(1)}\">\n{f2.read()}\n</style>"
                return tag
            inl = re.sub(r"<link\b[^>]*>", repl_local, html, flags=re.I)
            ok = inl.count("data-inlined-from")
            fail = 0
        else:
            inl, ok, fail = inline_css(html, pg.get("final_url") or pg["url"], cache)
        with open(os.path.join(d_inl, name), "w", encoding="utf-8") as f:
            f.write(inl)
        with open(os.path.join(d_mk, name), "w", encoding="utf-8") as f:
            f.write(strip_styles(html))
        # 분류에 쓰는 언어 = 본문 실제 문자(text_lang) 우선, 없으면 lang 속성. (lang=ko 인데 영문 사이트인 경우 대비)
        meta[name] = {"path": p, "lang": pg.get("text_lang") or pg.get("lang"), "lang_attr": pg.get("lang"), "css_inlined": ok, "css_failed": fail,
                      "css_links": pg.get("css_links", []), "inline_styles": pg.get("inline_styles"), "style_blocks": pg.get("style_blocks")}
    log(f"[design] anti-slop check — inlined ({len(meta)} pages) …")
    r_inl = run_antislop(d_inl)
    log("[design] anti-slop check — markup only …")
    r_mk = run_antislop(d_mk)
    res: dict = {"tool": "@gessobuild/anti-slop", "pages": {}, "error": r_inl.get("error") or r_mk.get("error")}
    if res["error"]:
        save_json(os.path.join(a.out, "design.json"), res)
        log(f"[design] ERROR {res['error']}")
        return
    res["tool_version"] = r_inl.get("version")
    mk_by_file = {os.path.basename(x["file"]): x for x in r_mk.get("results", [])}
    # CSS 유래 룰의 '마크업 적용 여부' 자동 확인: 흔한 유틸리티 클래스가 마크업에 실제로 쓰였는지 센다.
    # (탐지기는 스타일시트의 규칙 정의 수를 세므로 Tailwind 등 번들에 남은 미사용 정의가 '조치' 로 부풀 수 있다)
    APPLIED_PATTERNS = {
        "transition-all": r'class="[^"]*\btransition-all\b|transition:\s*all',
        "justified-text": r'class="[^"]*\btext-justify\b|text-align:\s*justify',
        "underlined-text": r'class="[^"]*\bunderline\b|text-decoration(?:-line)?:\s*underline',
        "wide-body-tracking": r'class="[^"]*\btracking-(?:wide|wider|widest)\b|letter-spacing:\s*0?\.(?:0[89]|[1-9])',
        "crushed-tracking": r'class="[^"]*\btracking-(?:tight|tighter)\b|letter-spacing:\s*-',
        "tiny-body-text": r'class="[^"]*\btext-\[?(?:9|10|10\.5)px|font-size:\s*(?:9|10|10\.5)px',
        "heavy-box-shadow": r'class="[^"]*\bshadow-(?:xl|2xl)\b|box-shadow:',
        "indigo-accent": r'class="[^"]*\b(?:bg|text|border)-(?:indigo|violet|purple)-\d|#(?:6366f1|4f46e5|7c3aed|8b5cf6)',
        "edge-stripe": r'class="[^"]*\bborder-l-(?:4|8)\b|border-left:\s*[3-9]px',
        "hover-scale-image": r'class="[^"]*\bhover:scale-\d|transform:\s*scale',
    }
    markup_cache: dict[str, str] = {}
    build_flag: dict[str, bool] = {}
    for name in meta:
        try:
            with open(os.path.join(d_mk, name), encoding="utf-8", errors="replace") as f:
                markup_cache[name] = f.read()
            with open(os.path.join(d_inl, name), encoding="utf-8", errors="replace") as f:
                build_flag[name] = bool(re.search(r"/_next/|data-nimg|tailwindcss|--tw-|/_nuxt/|astro-", f.read(400_000)))
        except Exception:
            markup_cache[name] = ""
            build_flag[name] = False
    total_by_rule: dict[str, int] = {}
    css_by_rule: dict[str, int] = {}
    cls_totals: dict[str, int] = {}
    grand = 0
    for x in r_inl.get("results", []):
        fname = os.path.basename(x["file"])
        m = meta.get(fname, {})
        mk = mk_by_file.get(fname, {})
        by_rule = (x.get("counts") or {}).get("byRule") or {}
        mk_rule = (mk.get("counts") or {}).get("byRule") or {}
        issues = []
        for s in x.get("issues", []):
            mm = ISSUE_RE.match(s.replace("[advisory]", "").strip())
            if not mm:
                issues.append({"raw": s[:300]})
                continue
            rule = mm.group("rule").strip()
            adv = "[advisory]" in s or rule in ADVISORY_RULES
            n = int(mm.group("n"))
            css_n = max(0, n - int(mk_rule.get(rule, 0)))
            msg = mm.group("msg").strip()
            # 설명 뒤에 "(e.g. 28,000; 58,000)" 형태로 실제 검출 예시가 붙는다 → 분리
            samples = []
            k = msg.rfind("(e.g. ")
            if k > 0 and msg.endswith(")"):
                samples = [x.strip() for x in msg[k + 6:-1].split(";") if x.strip()][:6]
                desc = msg[:k].strip()
            else:
                desc = msg
            klass, why = classify(rule, desc, m.get("lang"), adv, samples)
            decorative = None
            if rule == "em-dash-copy":
                # aria-hidden 장식 요소(불릿·구분 기호) 안의 줄표는 문장 줄표가 아니다
                mk_html = markup_cache.get(fname, "")
                decorative = len(re.findall(r'<(\w+)[^>]*aria-hidden="true"[^>]*>[^<]*[—–][^<]*</\1>', mk_html))
                if decorative and decorative >= n:
                    klass, why = "정당", f"줄표 {n}건이 전부 aria-hidden 장식 요소(불릿·구분 기호) 안"
                elif decorative:
                    why = (why + f" · 그중 {decorative}건은 aria-hidden 장식 요소").strip(" ·")
            applied = None
            if rule in APPLIED_PATTERNS and css_n == n and n > 0:
                applied = len(re.findall(APPLIED_PATTERNS[rule], markup_cache.get(fname, ""), re.I))
                # 유틸리티 클래스명이 곧 룰인 경우(transition-all·text-justify)만 '적용 0건 = 미사용 정의' 로 단정한다.
                # 자간·그림자·색 같은 룰은 커스텀 클래스로 적용됐을 수 있어 0건이어도 '검토' 에 그친다.
                if applied == 0 and build_flag.get(fname) and rule in ("transition-all", "justified-text"):
                    klass, why = "오탐", "스타일시트에 정의만 있고 마크업에 적용된 요소 0건 (빌드 번들의 미사용 유틸리티)"
                elif applied == 0 and klass == "조치":
                    klass, why = "검토", "유틸리티 클래스·인라인 적용을 마크업에서 찾지 못함. 커스텀 클래스로 적용됐을 수 있으니 design/markup 에서 확인"
            issues.append({"category": mm.group("cat"), "rule": rule, "hits": n, "css_origin": css_n, "markup_origin": n - css_n,
                           "applied_in_markup": applied, "decorative_hits": decorative, "advisory": adv, "class": klass, "why": why, "excerpt": desc[:240], "samples": samples})
            total_by_rule[rule] = total_by_rule.get(rule, 0) + n
            css_by_rule[rule] = css_by_rule.get(rule, 0) + css_n
            cls_totals[klass] = cls_totals.get(klass, 0) + n
            grand += n
        res["pages"][m.get("path", fname)] = {
            "file": fname, "lang": m.get("lang"), "pass": x.get("pass"), "severity": x.get("severity"),
            "counts": x.get("counts"), "externalStylesheets": x.get("externalStylesheets"),
            "css_inlined": m.get("css_inlined"), "css_failed": m.get("css_failed"),
            "markup_only_total": (mk.get("counts") or {}).get("total"), "issues": issues,
        }
    res["summary"] = {
        "pages": len(res["pages"]), "total_hits": grand, "by_rule": dict(sorted(total_by_rule.items(), key=lambda kv: -kv[1])),
        "css_origin_by_rule": {k: v for k, v in css_by_rule.items() if v},
        "by_class": cls_totals, "max_severity": max([p["severity"] or 0 for p in res["pages"].values()] or [0]),
        "pages_pass": sum(1 for p in res["pages"].values() if p["pass"]),
        "external_css_unresolved": sum(1 for p in res["pages"].values() if (p.get("externalStylesheets") or 0) > 0),
    }
    adv_total = sum(i.get("hits", 0) for p in res["pages"].values() for i in p["issues"] if i.get("advisory"))
    res["summary"]["advisory_total"] = adv_total
    res["summary"]["gating_total"] = grand - adv_total
    # 공용 CSS 반복: 같은 룰이 같은 건수로 3페이지 이상 반복되면 스타일시트 1곳 유래일 가능성이 높다 → 고유 건수 추정
    rep: dict[str, dict] = {}
    for p, pg in res["pages"].items():
        for i in pg["issues"]:
            if not i.get("rule"):
                continue
            k = i["rule"]
            rep.setdefault(k, {}).setdefault(i.get("hits", 0), []).append(p)
    repeated = {}
    unique_est = 0
    for rule, by_hits in rep.items():
        total_rule = sum(h * len(ps) for h, ps in by_hits.items())
        max_pages = max(len(ps) for ps in by_hits.values())
        if max_pages >= 3 and len(by_hits) == 1:
            h = next(iter(by_hits))
            repeated[rule] = {"hits_per_page": h, "pages": max_pages, "unique_estimate": h}
            unique_est += h
        else:
            unique_est += total_rule
    res["summary"]["repeated_across_pages"] = repeated
    res["summary"]["unique_hits_estimate"] = unique_est
    # 빌드 산출물(Tailwind/Next 등) 사이트는 CSS 번들에 미사용 유틸리티 정의가 남아 CSS 유래 룰이 부풀 수 있다.
    # 'CSS 유래' 건수는 규칙 정의 수이지 마크업 적용 수가 아니다 → 플래그를 남겨 체커·에이전트가 감안하게 한다
    build_hits = 0
    for name in meta:
        fp = os.path.join(d_inl, name)
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                head = f.read(400_000)
        except Exception:
            continue
        if re.search(r"/_next/|data-nimg|tailwindcss|--tw-|astro-|/_nuxt/|vite/", head):
            build_hits += 1
    res["summary"]["build_tool_pages"] = build_hits
    res["summary"]["css_origin_note"] = ("CSS 유래 건수는 스타일시트의 규칙 정의 수. 빌드 번들 사이트에서는 미사용 유틸리티일 수 있으니 raw HTML 에서 클래스 적용 여부를 확인"
                                        if build_hits else "CSS 유래 건수는 스타일시트의 규칙 정의 수 (마크업 적용 수가 아님)")
    save_json(os.path.join(a.out, "design.json"), res)
    s = res["summary"]
    log(f"[design] pages={s['pages']} hits={s['total_hits']} gating={s['gating_total']} advisory={s['advisory_total']} "
        f"class={s['by_class']} maxSev={s['max_severity']} unresolvedCSS={s['external_css_unresolved']}")
    for rule, n in list(s["by_rule"].items())[:12]:
        log(f"   {rule:24} {n:4}  (css {s['css_origin_by_rule'].get(rule, 0)})")


if __name__ == "__main__":
    main()
