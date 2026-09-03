# -*- coding: utf-8 -*-
"""1단계 — 수집. 라이브 URL 또는 로컬 정적 사이트 폴더에서 SEO/GEO 신호를 전수 수집해 collect.json 으로.

라이브:  py -3 site_collect.py https://example.kr --out ./example-audit [--max-pages 30] [--extra /menu /faq]
로컬:    py -3 site_collect.py --local ./site --base https://example.kr --out ./example-audit

읽기 전용. 라이브 사이트를 측정만 한다. 페이지 사이 0.4초 간격, 요청당 30초 타임아웃.
"""
from __future__ import annotations

import argparse
import os
import re
import time
import urllib.parse

from _common import (CRAWLERS, LOCAL_BUSINESS_TYPES, Doc, decode, display_host, fetch, find_addresses, find_phones, fix_path,
                     flatten_ld, is_local_business, is_question, log, md5, normalize_origin, now_iso, parse_html,
                     path_of, path_to_filename, same_host, save_json, types_of)

SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|css|js|mjs|pdf|zip|mp4|webm|woff2?|ttf|otf|xml|json|txt)(\?|$)", re.I)


# ---------------------------------------------------------------- robots
def parse_robots(text: str) -> dict:
    """User-agent 그룹별 Allow/Disallow 를 읽어 크롤러별 판정을 만든다."""
    groups: list[dict] = []
    cur = None
    sitemaps = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, val = [x.strip() for x in line.split(":", 1)]
        k = key.lower()
        if k == "user-agent":
            if cur is None or cur["rules"]:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
            cur["agents"].append(val.lower())
        elif k in ("allow", "disallow") and cur is not None:
            cur["rules"].append((k, val))
        elif k == "sitemap":
            sitemaps.append(val)

    def verdict_for(agent: str) -> str:
        a = agent.lower()
        matched = [g for g in groups if a in g["agents"]]
        if not matched:
            matched = [g for g in groups if "*" in g["agents"]]
            if not matched:
                return "unspecified"
        rules = [r for g in matched for r in g["rules"]]
        # 루트 전체 차단 여부만 본다 ("Disallow: /" 가 있고 Allow: / 가 없으면 차단)
        dis_root = any(k == "disallow" and v.strip() == "/" for k, v in rules)
        allow_root = any(k == "allow" and v.strip() == "/" for k, v in rules)
        if dis_root and not allow_root:
            return "disallow"
        partial = [v for k, v in rules if k == "disallow" and v.strip() not in ("", "/")]
        return "partial" if partial else "allow"

    bots = {name: {"group": grp, "verdict": verdict_for(name)} for name, grp in CRAWLERS}
    star = verdict_for("*")
    return {"groups": len(groups), "sitemaps": sitemaps, "bots": bots, "star": star,
            "cloudflare_managed": "Cloudflare Managed" in text or "cdn-cgi" in text,
            "ai_train_meta": bool(re.search(r"ai-train|Content-Signal", text, re.I))}


# ---------------------------------------------------------------- sitemap
def read_sitemap(url: str, depth: int = 0) -> list[dict]:
    r = fetch(url, timeout=30)
    body = decode(r["body"], r["headers"].get("content-type", ""))
    entry = {"url": url, "status": r["status"], "bytes": len(r["body"]), "is_index": False,
             "locs": [], "lastmods": [], "children": [], "error": r["error"]}
    if r["status"] != 200 or not body.strip():
        return [entry]
    if "<sitemapindex" in body:
        entry["is_index"] = True
        out = [entry]
        if depth < 2:
            for child in re.findall(r"<loc>\s*(.*?)\s*</loc>", body, re.S)[:20]:
                entry["children"].append(child.strip())
                out.extend(read_sitemap(child.strip(), depth + 1))
        return out
    entry["locs"] = [x.strip() for x in re.findall(r"<loc>\s*(.*?)\s*</loc>", body, re.S)]
    entry["lastmods"] = [x.strip() for x in re.findall(r"<lastmod>\s*(.*?)\s*</lastmod>", body, re.S)]
    entry["hreflang_links"] = len(re.findall(r"xhtml:link", body))
    entry["is_xml"] = "xml" in (r["headers"].get("content-type") or "")
    return [entry]


# ---------------------------------------------------------------- page analysis
def analyze_page(html: str, url: str, origin: str, headers: dict | None = None) -> dict:
    d: Doc = parse_html(html)
    headers = headers or {}
    text = d.body_text()
    title = d.title or ""
    desc = d.meta("description") or ""
    canon = [l.get("href") for l in d.rel("canonical")]
    hreflang = {}
    for l in d.rel("alternate"):
        if l.get("hreflang"):
            hreflang[l["hreflang"]] = l.get("href")
    alternates_llms = [l.get("href") for l in d.rel("alternate") if "llms" in (l.get("href") or "")]
    og = {k: d.meta("og:" + k) for k in ("title", "description", "image", "url", "site_name", "locale", "type")}
    twitter = {k: d.meta("twitter:" + k) for k in ("card", "title", "description", "image")}
    verification = {
        "naver": d.meta("naver-site-verification") is not None,
        "google": d.meta("google-site-verification") is not None,
        "bing": d.meta("msvalidate.01") is not None,
    }
    h1s = [h["text"] for h in d.headings if h["tag"] == "h1"]
    visible_h1s = [h["text"] for h in d.headings if h["tag"] == "h1" and "sr-only" not in (h["cls"] or "")]
    question_headings = [h["text"] for h in d.headings if h["tag"] in ("h2", "h3") and is_question(h["text"])]
    ld_nodes = flatten_ld(d.jsonld())
    ld_types = sorted({t for n in ld_nodes for t in types_of(n)})
    # 업체 노드: 구체 타입(Restaurant·NailSalon…) 우선. Organization 만 있는 노드(후기 출처 '네이버 블로그' 같은 publisher)는 구체 타입이 없을 때만 대체로 쓴다
    _generic = {"Organization", "Corporation"}
    biz_specific = [n for n in ld_nodes if set(types_of(n)) & (LOCAL_BUSINESS_TYPES - _generic)]
    biz = biz_specific or [n for n in ld_nodes if is_local_business(n)]
    faq_qs = []
    for n in ld_nodes:
        if "FAQPage" in types_of(n):
            for q in (n.get("mainEntity") or []):
                if isinstance(q, dict) and q.get("name"):
                    faq_qs.append(q["name"])
    # 사람: 직함·소속·자격·전문분야가 있는 Person, 또는 업체 노드의 founder/employee 가 가리키는 Person.
    # 후기(Review.author) 의 작성자 Person 은 대표 프로필이 아니므로 제외된다.
    persons = [n.get("name") for n in ld_nodes if "Person" in types_of(n) and n.get("name")
               and any(n.get(k) for k in ("jobTitle", "worksFor", "hasCredential", "alumniOf", "knowsAbout", "hasOccupation", "description"))]
    for b in biz:
        for key in ("founder", "employee", "employees", "member"):
            v = b.get(key)
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, dict) and x.get("name") and x["name"] not in persons:
                    persons.append(x["name"])
    rating_hits = len(re.findall(r'"(?:ratingValue|aggregateRating)"', html))
    date_modified = None
    date_published = None
    for n in ld_nodes:
        date_modified = date_modified or n.get("dateModified")
        date_published = date_published or n.get("datePublished")
    imgs_missing = [i for i in d.imgs if (i["alt"] is None or i["alt"].strip() == "") and not i["decorative"]]
    internal, external, naver_place, instagram, kakao, google_maps = [], [], [], [], [], []
    for a in d.anchors:
        href = (a["href"] or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "sms:")):
            continue
        absu = urllib.parse.urljoin(url, href)
        if same_host(origin, absu):
            internal.append(absu)
        else:
            external.append(absu)
            h = absu.lower()
            if "place.naver.com" in h or "naver.me" in h or "map.naver.com" in h:
                naver_place.append(absu)
            elif "instagram.com" in h:
                instagram.append(absu)
            elif "kakao" in h:
                kakao.append(absu)
            elif "google.com/maps" in h or "maps.app.goo.gl" in h or "g.page" in h:
                google_maps.append(absu)
    first_para = d.paragraphs[0] if d.paragraphs else ""
    para_lens = [len(p) for p in d.paragraphs]
    # 본문 문자 구성으로 실제 언어를 추정 (lang 속성과 다를 수 있다: lang=ko 인데 영문 사이트)
    letters = [ch for ch in text if ch.isalpha()]
    n_letters = len(letters) or 1
    hangul = sum(1 for ch in letters if "가" <= ch <= "힣")
    kana = sum(1 for ch in letters if "぀" <= ch <= "ヿ")
    han = sum(1 for ch in letters if "一" <= ch <= "鿿")
    latin = sum(1 for ch in letters if ch.isascii())
    script_ratio = {"hangul": round(hangul / n_letters, 2), "kana": round(kana / n_letters, 2), "han": round(han / n_letters, 2), "latin": round(latin / n_letters, 2)}
    if n_letters < 40:
        text_lang = None
    elif script_ratio["hangul"] >= 0.3:
        text_lang = "ko"
    elif script_ratio["kana"] >= 0.1:
        text_lang = "ja"
    elif script_ratio["han"] >= 0.3:
        text_lang = "zh"
    elif script_ratio["latin"] >= 0.7:
        text_lang = "en"
    else:
        text_lang = None
    css_links = [l.get("href") for l in d.rel("stylesheet") if l.get("href")]
    x_robots = headers.get("x-robots-tag")
    # 원페이지 사이트의 앵커 섹션(id="about" 등)도 '면' 으로 인정하기 위해 id 목록을 남긴다
    anchor_ids = sorted({m.lower() for m in re.findall(r'\sid="([A-Za-z][\w-]{1,40})"', html)})[:80]
    # 관리자·로그인·내부 페이지 추정: 제목/H1 에 로그인·관리자·admin 이 있으면 일반 페이지 판정에서 뺀다
    internal_page = bool(re.search(r"로그인|관리자|admin|login|dashboard|비밀번호", (title + " " + " ".join(h1s)), re.I))   # 아래 links 의 internal 리스트와 이름이 겹치면 안 된다
    return {
        "url": url, "lang": d.lang, "text_lang": text_lang, "script_ratio": script_ratio, "internal_page": internal_page, "anchor_ids": anchor_ids,
        "title": title, "title_len": len(title),
        "description": desc, "desc_len": len(desc),
        "canonical": canon[0] if canon else None, "canonical_count": len(canon),
        "robots_meta": d.meta("robots"), "x_robots_tag": x_robots,
        "viewport": d.meta("viewport") is not None,
        "charset": any("charset" in m for m in d.metas),
        "favicon": bool(d.rel("icon")) or bool(d.rel("shortcut")),
        "og": og, "twitter": twitter, "hreflang": hreflang, "rel_alternate_llms": alternates_llms,
        "verification": verification,
        "h1": h1s, "h1_visible": visible_h1s,
        "headings": [{"tag": h["tag"], "text": h["text"][:120]} for h in d.headings][:80],
        "heading_counts": {t: sum(1 for h in d.headings if h["tag"] == t) for t in ("h1", "h2", "h3", "h4")},
        "question_headings": question_headings[:30],
        "first_paragraph": first_para[:300], "first_paragraph_len": len(first_para),
        "paragraphs": len(d.paragraphs), "paragraph_avg_len": (round(sum(para_lens) / len(para_lens)) if para_lens else 0),
        "long_paragraphs": sum(1 for x in para_lens if x > 400),
        "body_chars": len(text), "body_words": len(text.split()),
        "tables": d.tables, "lists": d.lists, "details": d.details, "forms": d.forms, "buttons": d.buttons,
        "iframes": d.iframes, "videos": d.videos, "tel_links": d.tel_links,
        "imgs": {"total": len(d.imgs), "missing_alt": len(imgs_missing), "decorative": sum(1 for i in d.imgs if i["decorative"]),
                 "lazy": sum(1 for i in d.imgs if (i["loading"] or "") == "lazy"),
                 "with_dims": sum(1 for i in d.imgs if i["width"] and i["height"]),
                 "fetchpriority_high": sum(1 for i in d.imgs if (i["fetchpriority"] or "") == "high"),
                 # Next/Image 처럼 /_next/image?url=…hero.webp&w=… 형태도 잡는다
                 "modern_format": sum(1 for i in d.imgs if re.search(r"\.(webp|avif)(\?|&|$)|(webp|avif)(%3F|&|$)", i["src"] or "", re.I)),
                 "samples_missing_alt": [i["src"][:100] for i in imgs_missing[:5]]},
        "links": {"internal": sorted(set(internal)), "internal_count": len(internal), "external_count": len(external),
                  "naver_place": sorted(set(naver_place)), "instagram": sorted(set(instagram)),
                  "kakao": sorted(set(kakao)), "google_maps": sorted(set(google_maps)),
                  "nofollow": sum(1 for a in d.anchors if "nofollow" in (a["rel"] or ""))},
        "jsonld": {"blocks": len(d.jsonld_raw), "types": ld_types, "md5": md5("".join(d.jsonld_raw)),
                   "parse_errors": sum(1 for x in d.jsonld() if isinstance(x, dict) and "__parse_error__" in x),
                   "business": biz[:3], "faq_questions": faq_qs, "persons": persons,
                   "rating_hits": rating_hits, "menu_nodes": sum(1 for n in ld_nodes if "Menu" in types_of(n)),
                   "menu_items": sum(1 for n in ld_nodes if "MenuItem" in types_of(n)),
                   "breadcrumb": any("BreadcrumbList" in types_of(n) for n in ld_nodes),
                   "webpage": any("WebPage" in types_of(n) or "WebSite" in types_of(n) for n in ld_nodes),
                   "ids": sorted({n.get("@id") for n in biz if n.get("@id")}),
                   "same_as": sorted({s for n in biz for s in (n.get("sameAs") or []) if isinstance(s, str)})},
        "dates": {"dateModified": date_modified, "datePublished": date_published,
                  "time_tags": [t for t in d.time_tags if t][:5],
                  "meta_modified": d.meta("article:modified_time") or d.meta("last-modified")},
        "phones": find_phones(text), "addresses": find_addresses(text),
        "scripts": d.scripts, "inline_styles": d.inline_styles, "style_blocks": d.style_blocks,
        "css_links": css_links, "html_bytes": len(html.encode("utf-8", "replace")),
        "faq_visible_match": None,  # check 단계에서 채움
    }


# ---------------------------------------------------------------- live crawl
def collect_live(origin: str, out: str, max_pages: int, extra: list[str], delay: float = 0.4) -> dict:
    os.makedirs(os.path.join(out, "raw"), exist_ok=True)
    host = display_host(origin)
    log(f"[collect] origin={origin} ({host})")
    res: dict = {"mode": "live", "origin": origin, "host": host, "collected_at": now_iso(), "pages": {}}

    # robots / sitemap / llms / 404 probe
    r = fetch(origin + "/robots.txt")
    rb = decode(r["body"], r["headers"].get("content-type", "")) if r["status"] == 200 else ""
    res["robots"] = {"status": r["status"], "bytes": len(r["body"]), "text": rb[:6000], **parse_robots(rb)}
    with open(os.path.join(out, "raw", "robots.txt"), "w", encoding="utf-8") as f:
        f.write(rb)

    sm_urls = list(dict.fromkeys(res["robots"]["sitemaps"] + [origin + "/sitemap.xml"]))
    res["sitemaps"] = []
    for u in sm_urls[:5]:
        res["sitemaps"].extend(read_sitemap(u))
    all_locs = [loc for s in res["sitemaps"] for loc in s.get("locs", [])]
    res["sitemap_locs"] = list(dict.fromkeys(all_locs))
    res["sitemap_lastmods"] = [lm for s in res["sitemaps"] for lm in s.get("lastmods", [])]

    res["llms"] = {}
    for p in ("/llms.txt", "/llms-full.txt"):
        r = fetch(origin + p)
        txt = decode(r["body"], r["headers"].get("content-type", "")) if r["status"] == 200 else ""
        looks_html = "<html" in txt[:500].lower()
        res["llms"][p] = {"status": r["status"], "bytes": len(r["body"]), "is_html": looks_html,
                          "head": txt[:1500] if not looks_html else "", "has_prices": bool(re.search(r"\d[\d,]{2,}\s*원|₩\s?\d", txt))}
        if r["status"] == 200 and not looks_html:
            with open(os.path.join(out, "raw", p.strip("/")), "w", encoding="utf-8") as f:
                f.write(txt)

    r404 = fetch(origin + "/__site_audit_404_probe__")
    res["probe_404"] = {"status": r404["status"], "soft_404": r404["status"] == 200}

    # http → https, www 변형
    p = urllib.parse.urlsplit(origin)
    variants = {}
    for v in (f"http://{p.netloc}", f"https://www.{p.netloc}" if not p.netloc.startswith("www.") else f"https://{p.netloc[4:]}"):
        rv = fetch(v + "/", follow=False, timeout=20)
        variants[v] = {"status": rv["status"], "location": rv["headers"].get("location"), "error": rv["error"]}
    res["variants"] = variants

    # home headers + TTFB x3
    ttfbs, cfs, home = [], [], None
    for i in range(3):
        rh = fetch(origin + "/")
        if home is None:
            home = rh
        ttfbs.append(rh["ttfb_ms"])
        cfs.append(rh["headers"].get("cf-cache-status"))
        time.sleep(0.5)
    hh = home["headers"] if home else {}
    res["home_headers"] = {k: hh.get(k) for k in (
        "content-type", "content-encoding", "cache-control", "cf-cache-status", "server", "x-powered-by",
        "strict-transport-security", "content-security-policy", "x-content-type-options", "x-frame-options",
        "referrer-policy", "permissions-policy", "x-robots-tag", "last-modified", "etag", "vary", "age")}
    # 압축 여부는 Accept-Encoding 을 보내야 알 수 있다 (기본 fetch 는 보내지 않아 서버가 원문을 준다)
    rc = fetch(origin + "/", headers={"Accept-Encoding": "gzip, br"}, timeout=30)
    res["home_encoding_when_requested"] = (rc["headers"].get("content-encoding") or "") if rc["status"] else None
    res["ttfb_ms"] = ttfbs
    valid = [t for t in ttfbs if t]
    res["ttfb_avg"] = round(sum(valid) / len(valid), 1) if valid else None
    res["cf_cache_seq"] = cfs
    res["home_status"] = home["status"] if home else None
    res["home_final_url"] = home["url"] if home else None
    res["home_error"] = home["error"] if home else None

    # 페이지 목록: 홈 + extra + 사이트맵 + 홈 내부 링크 (캡)
    queue: list[str] = [origin + "/"]
    for e in extra:
        queue.append(urllib.parse.urljoin(origin + "/", e))
    queue += [u for u in res["sitemap_locs"] if same_host(origin, u)]
    seen: set[str] = set()
    discovered: list[str] = []
    n = 0
    while queue and n < max_pages:
        u = queue.pop(0)
        u = u.split("#", 1)[0]
        if not u or u in seen or SKIP_EXT.search(u) or not same_host(origin, u):
            continue
        seen.add(u)
        r = fetch(u)
        ct = r["headers"].get("content-type", "")
        html = decode(r["body"], ct) if r["body"] else ""
        path = path_of(u)
        fname = path_to_filename(path) + ".html"
        with open(os.path.join(out, "raw", fname), "w", encoding="utf-8") as f:
            f.write(html)
        if r["status"] == 200 and "html" in ct:
            page = analyze_page(html, r["url"], origin, r["headers"])
        else:
            page = {"url": u, "title": None}
        page.update({"status": r["status"], "final_url": r["url"], "redirected": (r["url"].rstrip("/") != u.rstrip("/")),
                     "bytes": len(r["body"]), "content_type": ct, "raw_path": f"raw/{fname}", "ttfb_ms": r["ttfb_ms"],
                     "error": r["error"], "in_sitemap": u in res["sitemap_locs"] or u.rstrip("/") in {x.rstrip("/") for x in res["sitemap_locs"]}})
        res["pages"][path] = page
        n += 1
        log(f"  {r['status']}  {path}  ({len(r['body'])//1024} KB)")
        # 홈·상위 페이지의 내부 링크를 큐에 (사이트맵 없는 사이트 대비)
        for l in page.get("links", {}).get("internal", [])[:60]:
            if l not in seen and l not in queue:
                queue.append(l)
                if l not in res["sitemap_locs"]:
                    discovered.append(l)
        time.sleep(delay)
    res["pages_total"] = n
    res["pages_capped"] = bool(queue) and n >= max_pages
    res["not_in_sitemap"] = sorted({d for d in discovered if d in seen and d not in res["sitemap_locs"]})[:30]
    try:
        res["redirect_probe"] = probe_redirects(res["pages"])
    except Exception as e:
        res["redirect_probe"] = {"error": str(e)[:160]}
    try:
        res["tls"] = probe_tls(origin)
    except Exception as e:
        res["tls"] = {"error": str(e)[:160]}
    log("[collect] AI 봇 UA 로 홈 재요청 (CDN·WAF 차단 확인)")
    try:
        res["ai_bot_probe"] = probe_ai_bots(origin, home or {})
    except Exception as e:
        res["ai_bot_probe"] = {"error": str(e)}
    log("[collect] 내부 링크 도달 확인")
    try:
        res["link_probe"] = probe_internal_links(origin, res["pages"], set(seen))
    except Exception as e:
        res["link_probe"] = {"error": str(e)}
    _post(res, origin)
    save_json(os.path.join(out, "collect.json"), res)
    log(f"[collect] pages={n} sitemap_locs={len(res['sitemap_locs'])} → {os.path.join(out, 'collect.json')}")
    return res


# ---------------------------------------------------------------- local static folder
def collect_local(site_dir: str, base: str, out: str) -> dict:
    """정적 사이트 폴더(index.html 트리)를 라이브와 같은 스키마로. 배포 전 게이트용."""
    os.makedirs(os.path.join(out, "raw"), exist_ok=True)
    origin = normalize_origin(base)
    res: dict = {"mode": "local", "origin": origin, "host": display_host(origin), "site_dir": os.path.abspath(site_dir),
                 "collected_at": now_iso(), "pages": {}}

    def rd(rel):
        p = os.path.join(site_dir, rel)
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()
        return None

    rb = rd("robots.txt") or ""
    res["robots"] = {"status": 200 if rb else 404, "bytes": len(rb), "text": rb[:6000], **parse_robots(rb)}
    sm = rd("sitemap.xml") or ""
    locs = [x.strip() for x in re.findall(r"<loc>\s*(.*?)\s*</loc>", sm, re.S)]
    res["sitemaps"] = [{"url": origin + "/sitemap.xml", "status": 200 if sm else 404, "bytes": len(sm), "is_index": "<sitemapindex" in sm,
                        "locs": locs, "lastmods": re.findall(r"<lastmod>\s*(.*?)\s*</lastmod>", sm, re.S), "children": [], "error": None,
                        "is_xml": True}]
    res["sitemap_locs"] = locs
    res["sitemap_lastmods"] = res["sitemaps"][0]["lastmods"]
    res["llms"] = {}
    for p in ("/llms.txt", "/llms-full.txt"):
        t = rd(p.strip("/"))
        res["llms"][p] = {"status": 200 if t is not None else 404, "bytes": len(t or ""), "is_html": False,
                          "head": (t or "")[:1500], "has_prices": bool(re.search(r"\d[\d,]{2,}\s*원|₩\s?\d", t or ""))}
    res["probe_404"] = {"status": None, "soft_404": None}
    res["variants"] = {}
    res["home_headers"] = {}
    res["ttfb_ms"] = []
    res["ttfb_avg"] = None
    res["cf_cache_seq"] = []
    res["home_status"] = 200 if rd("index.html") is not None else None

    n = 0
    error_pages = []
    for root, _dirs, files in os.walk(site_dir):
        for fn in files:
            if not fn.lower().endswith(".html"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, site_dir).replace(os.sep, "/")
            # 오류 페이지(404.html 등)는 색인 대상이 아니므로 페이지 집합에서 뺀다 (noindex·canonical 없음이 정상)
            if re.match(r"^(\d{3}|error|offline|maintenance)\.html$", fn.lower()) or rel.startswith(("_", ".")):
                error_pages.append(rel)
                continue
            path = "/" + (rel[:-len("index.html")] if rel.endswith("index.html") else rel)
            if path != "/" and not path.endswith("/") and path.endswith("index.html"):
                path = path[: -len("index.html")]
            html = rd(rel) or ""
            url = origin + path
            page = analyze_page(html, url, origin, {})
            page.update({"status": 200, "final_url": url, "redirected": False, "bytes": len(html.encode("utf-8", "replace")),
                         "content_type": "text/html", "raw_path": rel, "ttfb_ms": None, "error": None,
                         "in_sitemap": url in locs or url.rstrip("/") in {x.rstrip("/") for x in locs}})
            res["pages"][path] = page
            n += 1
            log(f"  local  {path}")
    res["pages_total"] = n
    res["pages_capped"] = False
    res["not_in_sitemap"] = []
    res["error_pages"] = error_pages
    _post(res, origin)
    save_json(os.path.join(out, "collect.json"), res)
    log(f"[collect] local pages={n} → {os.path.join(out, 'collect.json')}")
    return res


# ---------------------------------------------------------------- cross-page
def _post(res: dict, origin: str) -> None:
    pages = res["pages"]
    # 내부 링크 그래프 → 유입 수, 고아
    inbound: dict[str, int] = {p: 0 for p in pages}
    for p, pg in pages.items():
        for l in pg.get("links", {}).get("internal", []):
            lp = path_of(l)
            for cand in (lp, lp.rstrip("/") or "/", lp + "/" if not lp.endswith("/") else lp):
                if cand in inbound and cand != p:
                    inbound[cand] += 1
                    break
    res["link_graph"] = {"inbound": inbound,
                         "orphans": [p for p, c in inbound.items() if c == 0 and p != "/"]}
    # 제목 중복: canonical 이 같은 페이지(?view=site 같은 변형)는 한 페이지로 본다.
    # noindex 를 걸어 둔 페이지는 운영자가 이미 정리한 것이므로 중복 묶음에서 뺀다
    # (이미 고친 것을 고치라고 하는 지적이 된다).
    titles: dict[str, dict[str, str]] = {}
    for p, pg in pages.items():
        t = (pg.get("title") or "").strip()
        robots = ((pg.get("robots_meta") or "") + " " + (pg.get("x_robots_tag") or "")).lower()
        if t and "noindex" not in robots:
            key = (pg.get("canonical") or pg.get("final_url") or p).rstrip("/")
            titles.setdefault(t, {})[key] = p
    res["duplicate_titles"] = {t: sorted(ps.values()) for t, ps in titles.items() if len(ps) > 1}
    # JSON-LD 동일 블록 복제
    lds: dict[str, list[str]] = {}
    for p, pg in pages.items():
        m = (pg.get("jsonld") or {}).get("md5")
        if m and (pg.get("jsonld") or {}).get("blocks"):
            lds.setdefault(m, []).append(p)
    res["duplicate_jsonld"] = {m: ps for m, ps in lds.items() if len(ps) > 1}
    # 엔티티 정합
    ids, types, names, phones, addrs, same_as = set(), set(), set(), set(), set(), set()
    for pg in pages.values():
        j = pg.get("jsonld") or {}
        for i in j.get("ids", []):
            ids.add(i)
        for b in j.get("business", []):
            for t in types_of(b):
                types.add(t)
            if b.get("name"):
                names.add(str(b["name"]).strip())
            if b.get("telephone"):
                phones.add(re.sub(r"[^\d]", "", str(b["telephone"])))
            a = b.get("address")
            if isinstance(a, dict) and a.get("streetAddress"):
                addrs.add(str(a["streetAddress"]).strip())
            elif isinstance(a, str):
                addrs.add(a.strip())
        for s in j.get("same_as", []):
            same_as.add(s)
    text_phones = {ph for pg in pages.values() for ph in pg.get("phones", [])}
    text_addrs = {ad for pg in pages.values() for ad in pg.get("addresses", [])}
    res["entity"] = {"ids": sorted(ids), "id_consistent": len(ids) <= 1, "types": sorted(types),
                     "names": sorted(names), "schema_phones": sorted(phones), "schema_addresses": sorted(addrs),
                     "text_phones": sorted(text_phones), "text_addresses": sorted(text_addrs)[:8],
                     "same_as": sorted(same_as),
                     "punycode_mixed": any("xn--" in i for i in ids) and any(re.search(r"[가-힣]", i) for i in ids)}
    # 사이트 수준 집계
    langs = {pg.get("lang") for pg in pages.values() if pg.get("lang")}
    res["languages"] = sorted(l for l in langs if l)
    res["hreflang_pages"] = sum(1 for pg in pages.values() if pg.get("hreflang"))
    res["verification_any"] = {k: any((pg.get("verification") or {}).get(k) for pg in pages.values()) for k in ("naver", "google", "bing")}
    res["body_chars_total"] = sum(pg.get("body_chars", 0) or 0 for pg in pages.values())
    res["persons"] = sorted({p for pg in pages.values() for p in ((pg.get("jsonld") or {}).get("persons") or []) if p})


# ---------------------------------------------------------------- main

# AI 검색 봇 UA. robots.txt 가 허용해도 CDN·WAF 가 엣지에서 막는 경우가 있어 실제로 한 번 요청해 본다.
# OpenAI 공식 문서도 "호스팅·CDN 이 OpenAI 공개 IP 트래픽을 막지 않아야 한다" 고 적고 있다.
AI_BOT_UAS = [
    ("OAI-SearchBot", "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)"),
    ("ChatGPT-User", "Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)"),
    ("GPTBot", "Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)"),
    ("ClaudeBot", "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)"),
    ("PerplexityBot", "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"),
    ("Google-Extended", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    ("Yeti", "Mozilla/5.0 (compatible; Yeti/1.1; +http://naver.me/spd)"),
]




def probe_redirects(pages: dict, limit: int = 20, max_hops: int = 6) -> dict:
    """리다이렉트한 페이지만 골라 홉을 하나씩 따라간다.

    follow=True 로 받은 응답은 최종 URL 만 알려 주고 몇 번 튕겼는지는 모른다.
    두 번 이상 튕기면 매 요청이 왕복을 더하고, 크롤러는 홉 예산을 쓴다. 순환이면 페이지가 아예 안 열린다.
    """
    out = {"checked": 0, "chains": [], "loops": []}
    todo = [(path, pg.get("url")) for path, pg in pages.items()
            if pg.get("redirected") and pg.get("url")][:limit]
    for path, start in todo:
        hops, seen, cur = [], set(), start
        for _ in range(max_hops):
            r = fetch(cur, follow=False, timeout=15)
            st = r.get("status")
            loc = (r.get("headers") or {}).get("location")
            if st is None or not (300 <= st < 400) or not loc:
                break
            nxt = urllib.parse.urljoin(cur, loc)
            hops.append({"from": cur, "to": nxt, "status": st})
            if nxt in seen or nxt == cur:
                out["loops"].append({"path": path, "hops": hops})
                break
            seen.add(cur)
            cur = nxt
            time.sleep(0.15)
        else:
            out["loops"].append({"path": path, "hops": hops})   # max_hops 를 다 써도 안 끝남
        out["checked"] += 1
        if len(hops) >= 2 and not any(l["path"] == path for l in out["loops"]):
            out["chains"].append({"path": path, "n": len(hops),
                                  "trail": [h["from"] for h in hops] + [cur]})
    out["chains"] = out["chains"][:10]
    out["loops"] = out["loops"][:10]
    return out

def probe_tls(origin: str) -> dict:
    """인증서 만료일을 본다. 만료되면 브라우저가 경고 화면을 띄워 사이트 전체가 멈춘다."""
    import socket, ssl as _ssl, datetime as _dt
    host = urllib.parse.urlsplit(origin).hostname
    if not host:
        return {"error": "host 없음"}
    try:
        ctx = _ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
        not_after = cert.get("notAfter")
        exp = _dt.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=_dt.timezone.utc)
        days = (exp - _dt.datetime.now(_dt.timezone.utc)).days
        issuer = dict(x[0] for x in cert.get("issuer", ()) if x)
        return {"not_after": not_after, "days_left": days,
                "issuer": issuer.get("organizationName") or issuer.get("commonName"),
                "subject_alt_count": len(cert.get("subjectAltName") or ())}
    except Exception as e:
        return {"error": str(e)[:160]}

def probe_ai_bots(origin: str, baseline: dict) -> dict:
    """홈을 AI 봇 UA 로 다시 받아 본다. 사람 UA 응답과 상태·본문 크기를 비교한다.

    robots.txt 는 '허락한다는 선언' 일 뿐이고, 실제 차단은 CDN·WAF 에서 일어난다.
    같은 200 이어도 본문이 크게 줄면 봇에게 다른 것을 보여 주는 것이다.
    """
    base_len = len(baseline.get("body") or b"")
    base_status = baseline.get("status")
    out = {"baseline": {"status": base_status, "bytes": base_len}, "bots": {}, "blocked": [], "thin": []}
    for name, ua in AI_BOT_UAS:
        r = fetch(origin + "/", ua=ua, timeout=20)
        blen = len(r.get("body") or b"")
        row = {"status": r.get("status"), "bytes": blen, "error": r.get("error")}
        if r.get("status") in (401, 403, 429, 503) or (r.get("status") is None and r.get("error")):
            out["blocked"].append(name)
        elif base_len and blen and blen * 2 < base_len:
            row["ratio"] = round(blen / base_len, 3)
            out["thin"].append(name)
        out["bots"][name] = row
        time.sleep(0.4)
    return out


def probe_internal_links(origin: str, pages: dict, seen: set, limit: int = 40) -> dict:
    """페이지 안의 내부 링크 중 아직 안 받아 본 것을 확인한다.

    links.internal 은 전체 URL 이고 seen 도 전체 URL 이다. pages 는 경로로 키가 잡혀 있어
    앵커 검사만 path_of 로 바꿔 대조한다. 앵커(#id)는 네트워크로 확인할 수 없으므로
    대상 페이지가 실제로 그 id 를 가지고 있는지 본다.
    """
    todo, anchors, qset = [], [], set()
    for path, pg in pages.items():
        for l in ((pg.get("links") or {}).get("internal") or []):
            tgt = l.split("#")[0]
            frag = l.split("#")[1] if "#" in l else ""
            if frag:
                anchors.append((path, tgt, frag))
            if tgt and tgt not in seen and tgt not in qset and same_host(origin, tgt):
                qset.add(tgt)
                todo.append((path, tgt))
    broken, checked = [], 0
    for src, tgt in todo[:limit]:
        r = fetch(tgt, method="HEAD", timeout=15)
        st = r.get("status")
        if st is None or st >= 400:
            r2 = fetch(tgt, timeout=15)          # HEAD 를 막는 서버가 있어 GET 으로 한 번 더
            st = r2.get("status")
            if st is None or st >= 400:
                broken.append({"from": src, "to": path_of(tgt), "status": st, "error": r2.get("error")})
        checked += 1
        time.sleep(0.2)
    bad_anchor = []
    for src, tgt, frag in anchors:
        pg = pages.get(path_of(tgt))
        if pg is None:
            continue
        ids = set(pg.get("anchor_ids") or [])
        if ids and frag.lower() not in ids:
            bad_anchor.append({"from": src, "to": f"{path_of(tgt)}#{frag}"})
    return {"checked": checked, "queued": len(todo), "broken": broken[:15],
            "bad_anchor": bad_anchor[:15], "anchor_links": len(anchors)}


def main():
    ap = argparse.ArgumentParser(description="site-audit 1단계 수집")
    ap.add_argument("url", nargs="?", help="라이브 사이트 URL")
    ap.add_argument("--local", help="정적 사이트 폴더 (배포 전 게이트)")
    ap.add_argument("--base", help="--local 일 때 공개 예정 origin (https://...)")
    ap.add_argument("--out", required=True, help="작업 폴더")
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--extra", nargs="*", default=[], help="사이트맵에 없어도 볼 경로 (/menu /faq ...)")
    a = ap.parse_args()
    a.out = fix_path(a.out)
    a.local = fix_path(a.local)
    if a.local:
        if not a.base:
            ap.error("--local 에는 --base 가 필요합니다")
        if not os.path.isdir(a.local):
            ap.error(f"--local 폴더 없음: {a.local}")
        collect_local(a.local, a.base, a.out)
    elif a.url:
        collect_live(normalize_origin(a.url), a.out, a.max_pages, a.extra)
    else:
        ap.error("url 또는 --local 을 지정하세요")


if __name__ == "__main__":
    main()
