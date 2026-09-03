# -*- coding: utf-8 -*-
"""4단계 · 룰 엔진. collect.json / render.json / design.json (+ facts.json) 을 읽어 findings.json 을 만든다.

py -3 site_check.py ./example-audit [--facts facts.json] [--brand "매장명"] [--region "○○역"] [--category "네일샵"]

finding 스키마:
  {id, lane(S|G|D), category, severity(P0|P1|P2|OK|INFO), status(PASS|FAIL|HOLD), title, detail,
   evidence[], pages[], fix, refs[], weight}
- 근거 없는 PASS 를 만들지 않는다: 측정값이 없으면 status=HOLD.
- 라이브 검색 결과·AI 브리핑은 여기서 판정하지 않는다 (에이전트가 naverai 로 별도 실측).
- refs: 『필살기 100』 번호(K##), 가이드북 CITE/자가진단(G-*), claude-seo 범주(CS-*), fire-your-seo-agency NEO(NEO-*)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re

from _common import CRAWLERS, fix_path, load_json, log, save_json, scrub, types_of

# ---------------------------------------------------------------- helpers
class Findings:
    def __init__(self):
        self.items: list[dict] = []
        self._ids: set[str] = set()

    def add(self, id_: str, lane: str, category: str, severity: str, status: str, title: str, *,
            detail: str = "", evidence=None, pages=None, fix: str = "", refs=None, weight: float = 1.0):
        if id_ in self._ids:
            id_ = f"{id_}#{sum(1 for x in self.items if x['id'].startswith(id_))}"
        self._ids.add(id_)
        self.items.append({"id": id_, "lane": lane, "category": category, "severity": severity, "status": status,
                           "title": scrub(title), "detail": scrub(detail), "evidence": [scrub(e) for e in list(evidence or [])[:12]],
                           "pages": list(pages or [])[:20], "fix": fix, "refs": list(refs or []), "weight": weight})

    def ok(self, id_, lane, category, title, **kw):
        self.add(id_, lane, category, "OK", "PASS", title, **kw)

    def hold(self, id_, lane, category, title, **kw):
        self.add(id_, lane, category, "INFO", "HOLD", title, **kw)


def pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M%z"):
        try:
            d = dt.datetime.strptime(s[:len(fmt) + 6] if "%z" in fmt else s[:19], fmt)
            return d.replace(tzinfo=None)
        except Exception:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def days_since(s):
    d = parse_date(s)
    return (dt.datetime.now() - d).days if d else None


def html_pages(c: dict) -> dict:
    """판정 대상 페이지: 200 HTML. 관리자·로그인 등 내부 페이지(internal_page)는 뺀다 (참고 finding 으로만 알린다)."""
    return {p: pg for p, pg in c["pages"].items() if pg.get("status") == 200 and pg.get("title") is not None and not pg.get("internal_page")}


def path_of_url(u: str) -> str:
    import urllib.parse
    p = urllib.parse.urlsplit(u)
    return (p.path or "/").rstrip("/")


# ---------------------------------------------------------------- S: SEO




def phone_variants(tel: str) -> list[str]:
    """한국 전화는 스키마가 +82-50-7…, 화면이 0507… 로 서로 다르게 적힌다.

    국가번호 표기와 국내 표기를 모두 만들어 하나라도 화면에 있으면 일치로 본다.
    이 정규화 없이 비교하면 정상 사이트가 전부 불일치로 잡힌다.
    """
    d = re.sub(r"[^0-9]", "", tel or "")
    if not d:
        return []
    out = {d}
    if d.startswith("82"):
        out.add("0" + d[2:])          # +82-2-… → 02-…
        out.add(d[2:])
    if d.startswith("0"):
        out.add("82" + d[1:])
    return [x for x in out if len(x) >= 8]

AXE_NAME_IDS = {"button-name", "link-name", "input-button-name", "image-alt", "input-image-alt",
                "label", "select-name", "aria-command-name", "aria-toggle-field-name", "frame-title"}
AXE_TREE_IDS = {"aria-required-parent", "aria-required-children", "aria-valid-attr", "aria-valid-attr-value",
                "aria-roles", "aria-hidden-focus", "aria-hidden-body", "duplicate-id-aria", "nested-interactive"}


def path_of_url(u: str) -> str:
    """전체 URL 에서 경로만. 링크 그래프를 pages 키(경로)와 맞추기 위해."""
    try:
        pr = urllib.parse.urlsplit(u)
        return (pr.path or "/") or "/"
    except Exception:
        return u

def _as_list(v):
    """JSON-LD 는 값이 하나면 배열을 생략한다. 항상 리스트로 맞춘다."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]

# --- raw HTML 접근 + 본문 지표 -------------------------------------------------
# 여러 룰이 같은 원문을 본다. 한 번만 읽어 캐시한다.
_RAW_CACHE: dict = {}
_TXT_CACHE: dict = {}

_TAG_STRIP = re.compile(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", re.S | re.I)


def raw_html(c: dict, pg: dict) -> str:
    rp = pg.get("raw_path")
    if not rp:
        return ""
    base = c.get("site_dir") if c.get("mode") == "local" else (c.get("_out") or ".")
    full = os.path.join(base, rp)
    if full in _RAW_CACHE:
        return _RAW_CACHE[full]
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            html = fh.read()
    except OSError as e:
        html = ""
    _RAW_CACHE[full] = html
    return html


def visible_text(html: str) -> str:
    if not html:
        return ""
    if html in _TXT_CACHE:
        return _TXT_CACHE[html]
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _TAG_STRIP.sub(" ", html)))
    _TXT_CACHE[html] = t
    return t


# 증거 장르 (arXiv 2604.25707: 코드 +77% · 통계 +62% · 정의 +57% · 비교 +55% · 절차 +41% · Q&A 포맷 단독 -5.74%)
STAT_RE = re.compile(r"\d[\d,\.]*\s*(?:%|명|건|개|년|개월|일|시간|분|초|원|kg|g|cm|mm|m|km|㎡|평|석|회|배|위|층|인분|잔|병)")
DEF_RE = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z0-9 ]{1,20}(?:은|는|이란|란|라는 것은)\s[^.]{5,80}?(?:입니다|이다|예요|말합니다|뜻합니다|의미합니다)")
STEP_RE = re.compile(r"(?:^|[\s>])(?:1단계|첫째|먼저|그다음|그 다음|마지막으로|Step\s*1)")

# 국내외 권위 출처로 볼 수 있는 도메인. 소셜·자사 채널은 제외한다.
AUTHORITY_RE = re.compile(r"(?:^|\.)(?:go|or|re|ac)\.kr$|\.gov$|\.gov\.|\.edu$|\.edu\.|\bwho\.int$|\bnih\.gov$|wikipedia\.org$|\.ac\.uk$", re.I)
SOCIAL_RE = re.compile(r"instagram|facebook|youtube|youtu\.be|blog\.naver|cafe\.naver|tiktok|twitter|x\.com|kakao|threads|pinterest|linkedin|naver\.me|smartstore|map\.naver|place\.naver", re.I)

# 구글이 리치결과를 내리는 스키마 타입. 마크업 자체는 유효하므로 제거를 권하지 않는다.
DEPRECATED_RICH = {
    "HowTo": "2023-08 리치결과 폐기",
    "FAQPage": "2026-05-07 리치결과 폐기 (타입 자체는 유효, 제거 불필요)",
    "SearchAction": "2023-10 사이트링크 검색창 폐기",
}

# 노출되면 안 되는 문자열. AIza 계열은 클라이언트 공개가 정상인 경우가 있어 등급을 나눈다.
SECRET_HARD = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{32,}|xox[baprs]-[A-Za-z0-9-]{10,}")
SECRET_SOFT = re.compile(r"AIza[0-9A-Za-z_\-]{35}")


def check_seo(F: Findings, c: dict, r: dict | None, facts: dict | None):
    pages = html_pages(c)
    origin = c["origin"]
    live = c.get("mode") == "live"
    n = len(pages)

    plat = detect_platform(c.get("_out") or ".", c.get("pages") or {})
    if plat:
        F.add("S-T-platform", "S", "technical", "INFO", "PASS", f"사이트 빌더: {plat['name']} ({plat['kind']})",
              detail="메타 하드코딩·중복 사고는 대부분 빌더의 공통 head 템플릿에서 생깁니다. 조치 위치를 이 플랫폼 기준으로 안내합니다.",
              evidence=[f"generator={plat.get('generator') or '표기 없음'}"], weight=0)

    internal = [p for p, pg in c["pages"].items() if pg.get("internal_page")]
    if internal:
        F.add("S-T-internal", "S", "technical", "INFO", "PASS", f"관리자·로그인으로 보이는 페이지 {len(internal)}개는 판정에서 뺐습니다",
              detail="공개 사이트맵에 들어 있거나 홈에서 링크되면 noindex 와 사이트맵 제외를 권장합니다.", evidence=internal[:5], weight=0)
    # --- 기술 (CS-technical)
    if live:
        if c.get("home_status") == 200:
            F.ok("S-T-home", "S", "technical", "홈이 200 으로 응답합니다", evidence=[f"TTFB 3회 {c.get('ttfb_ms')} 평균 {c.get('ttfb_avg')} ms"], weight=0)
        else:
            F.add("S-T-home", "S", "technical", "P0", "FAIL", "홈이 정상 응답하지 않습니다",
                  evidence=[f"status={c.get('home_status')} error={c.get('home_error')}"], weight=10)
        v_http = (c.get("variants") or {}).get(f"http://{origin.split('//')[1]}") or {}
        if v_http.get("status") in (301, 308) and (v_http.get("location") or "").startswith("https://"):
            F.ok("S-T-https", "S", "technical", "http → https 301 리다이렉트", evidence=[f"http → {v_http.get('location')}"], refs=["K27"])
        elif v_http.get("status") == 200:
            F.add("S-T-https", "S", "technical", "P1", "FAIL", "http 주소가 https 로 넘어가지 않고 그대로 열립니다",
                  evidence=[f"http://… → {v_http.get('status')}"], fix="http → https 301 전역 리다이렉트", refs=["K27"], weight=4)
        else:
            F.hold("S-T-https", "S", "technical", "http 변형 응답을 확인하지 못했습니다", evidence=[str(v_http)])
        www = [(k, v) for k, v in (c.get("variants") or {}).items() if k.startswith("https://")]
        for k, v in www:
            if v.get("status") in (301, 308):
                F.ok("S-T-www", "S", "technical", "www 변형이 정규 주소로 301", evidence=[f"{k} → {v.get('location')}"])
            elif v.get("status") == 200:
                F.add("S-T-www", "S", "technical", "P2", "FAIL", "www 변형이 별도 200 으로 열립니다 (중복 호스트)",
                      evidence=[f"{k} → 200"], fix="한 호스트로 301 통일", weight=2)
            elif v.get("error") or v.get("status") is None:
                F.hold("S-T-www", "S", "technical", "www 변형이 응답하지 않습니다 (DNS 미설정일 수 있음)", evidence=[f"{k}: {v.get('error') or v.get('status')}"])
        p404 = c.get("probe_404") or {}
        if p404.get("soft_404"):
            F.add("S-T-404", "S", "technical", "P1", "FAIL", "존재하지 않는 경로가 200 을 반환합니다 (soft 404)",
                  evidence=["/__site_audit_404_probe__ → 200"], fix="진짜 404 상태코드 반환", refs=["K79"], weight=4)
        elif p404.get("status") in (404, 410):
            F.ok("S-T-404", "S", "technical", "없는 경로에 진짜 404", evidence=[f"probe → {p404.get('status')}"])
        hh = c.get("home_headers") or {}
        sec_missing = [h for h in ("strict-transport-security", "x-content-type-options", "referrer-policy", "permissions-policy") if not hh.get(h)]
        if not sec_missing:
            F.ok("S-T-sec", "S", "technical", "보안 헤더 4종 존재", evidence=[f"HSTS={hh.get('strict-transport-security')}"])
        elif len(sec_missing) == 4:
            F.add("S-T-sec", "S", "technical", "P2", "FAIL", "보안 헤더가 하나도 없습니다", evidence=[", ".join(sec_missing)],
                  fix="HSTS · X-Content-Type-Options: nosniff · Referrer-Policy · Permissions-Policy 추가", weight=3)
        else:
            F.add("S-T-sec", "S", "technical", "P2", "FAIL", f"보안 헤더 {len(sec_missing)}종 누락", evidence=[", ".join(sec_missing)],
                  fix="누락 헤더 추가 (nginx add_header)", weight=1.5)
        if hh.get("x-frame-options") is None and "frame-ancestors" not in (hh.get("content-security-policy") or ""):
            F.add("S-T-frame", "S", "technical", "P2", "FAIL", "클릭재킹 방어 헤더 없음", evidence=["X-Frame-Options / CSP frame-ancestors 없음"],
                  fix="X-Frame-Options: SAMEORIGIN", weight=1)
        cc = hh.get("cache-control") or ""
        cf = [x for x in (c.get("cf_cache_seq") or []) if x]   # Cloudflare 가 아니면 전부 None → 판정하지 않는다
        if "no-store" in cc or (cf and all(x == "DYNAMIC" for x in cf)):
            F.add("S-T-cache", "S", "technical", "P2", "FAIL", "HTML 이 엣지 캐시를 타지 않습니다",
                  evidence=[f"Cache-Control: {cc or '없음'}", f"cf-cache-status: {cf}", f"TTFB 평균 {c.get('ttfb_avg')} ms"],
                  fix="정적 페이지면 s-maxage + 배포 시 퍼지. 예약 폼 등 동적 경로는 제외", weight=1.5)
        elif cf and any(x == "HIT" for x in cf):
            F.ok("S-T-cache", "S", "technical", "엣지 캐시 HIT", evidence=[f"cf-cache-status {cf}", f"TTFB 평균 {c.get('ttfb_avg')} ms"])
        if c.get("ttfb_avg") and c["ttfb_avg"] > 800:
            F.add("S-T-ttfb", "S", "technical", "P1", "FAIL", "TTFB 가 느립니다", evidence=[f"3회 {c.get('ttfb_ms')} 평균 {c['ttfb_avg']} ms (기준 ≤ 800, 권장 ≤ 200)"],
                  fix="캐시·서버 응답 개선", weight=3)
        enc = c.get("home_encoding_when_requested")   # Accept-Encoding 을 보내고 받은 응답 헤더 (collect 가 별도 요청)
        if enc is None:
            pass  # 구버전 collect.json: 판정 불가
        elif not enc and (pages.get("/") or {}).get("bytes", 0) > 30000:
            F.add("S-T-gzip", "S", "technical", "P2", "FAIL", "HTML 압축 전송 없음", evidence=[f"Accept-Encoding: gzip, br 요청에도 content-encoding 없음, 홈 {(pages.get('/') or {}).get('bytes', 0)//1024} KB"], fix="gzip/br 활성화", weight=1)
        elif enc:
            F.ok("S-T-gzip", "S", "technical", f"HTML 압축 전송 ({enc})", weight=0)

    # robots
    rb = c.get("robots") or {}
    if rb.get("status") != 200:
        F.add("S-T-robots", "S", "technical", "P1", "FAIL", "robots.txt 가 없습니다", evidence=[f"status={rb.get('status')}"],
              fix="robots.txt 생성: User-agent: * / Allow: / / Sitemap: …", refs=["K26"], weight=3)
    else:
        if rb.get("star") == "disallow":
            F.add("S-T-robots", "S", "technical", "P0", "FAIL", "robots.txt 가 모든 크롤러를 차단합니다", evidence=[rb.get("text", "")[:200]],
                  fix="Disallow: / 제거", weight=10)
        else:
            F.ok("S-T-robots", "S", "technical", "robots.txt 정상 (전체 허용)", evidence=[f"groups={rb.get('groups')} star={rb.get('star')}"])
        if not rb.get("sitemaps"):
            F.add("S-T-robots-sm", "S", "technical", "P2", "FAIL", "robots.txt 에 Sitemap 선언이 없습니다", fix="Sitemap: https://…/sitemap.xml 한 줄 추가", refs=["K26"], weight=1)
    # sitemap
    sms = [s for s in (c.get("sitemaps") or []) if s.get("status") == 200]
    locs = c.get("sitemap_locs") or []
    if not sms:
        F.add("S-T-sitemap", "S", "technical", "P1", "FAIL", "sitemap.xml 이 없습니다", evidence=[f"{[(s.get('url'), s.get('status')) for s in (c.get('sitemaps') or [])][:2]}"],
              fix="sitemap.xml 생성 후 robots 선언·서치어드바이저/서치콘솔 제출", refs=["K26"], weight=4)
    else:
        ev = [f"{len(locs)} URL, lastmod {len(c.get('sitemap_lastmods') or [])}건"]
        missing = [p for p, pg in pages.items() if not pg.get("in_sitemap") and not pg.get("redirected")]
        if missing:
            F.add("S-T-sitemap-miss", "S", "technical", "P2", "FAIL", f"확인한 페이지 {len(missing)}개가 사이트맵에 없습니다",
                  evidence=missing[:8], fix="누락 URL 추가", weight=1.5)
        lms = c.get("sitemap_lastmods") or []
        if not lms:
            F.add("S-T-sitemap-lastmod", "S", "technical", "P2", "FAIL", "사이트맵에 lastmod 가 없습니다", fix="빌드 시각 또는 실제 수정일을 lastmod 로", refs=["K78"], weight=1)
        else:
            ds = [days_since(x) for x in lms]
            ds = [d for d in ds if d is not None]
            if ds and min(ds) > 180:
                F.add("S-T-sitemap-stale", "S", "technical", "P2", "FAIL", "사이트맵 lastmod 가 6개월 이상 오래됐습니다", evidence=[f"가장 최근 {min(ds)}일 전"], refs=["K78"], weight=1)
            else:
                ev.append(f"가장 최근 lastmod {min(ds) if ds else '?'}일 전")
        F.ok("S-T-sitemap", "S", "technical", "sitemap.xml 정상", evidence=ev)
        bad = [l for l in locs if not l.startswith(origin)]
        if bad and len(bad) >= max(1, len(locs) // 2):
            F.add("S-T-sitemap-host", "S", "technical", "P1", "FAIL", "사이트맵 URL 호스트가 현재 도메인과 다릅니다", evidence=bad[:4],
                  fix="사이트맵 loc 를 정규 도메인으로", weight=3)
    # 색인 차단
    noindex = [p for p, pg in pages.items() if "noindex" in ((pg.get("robots_meta") or "") + (pg.get("x_robots_tag") or "")).lower()]
    if noindex:
        F.add("S-T-noindex", "S", "technical", "P0", "FAIL", f"noindex 가 걸린 페이지 {len(noindex)}개", evidence=noindex[:8],
              fix="운영 페이지에서 noindex 제거", weight=10)
    else:
        F.ok("S-T-noindex", "S", "technical", "noindex 없음 (전 페이지 색인 가능)", evidence=[f"{n}페이지 robots meta·X-Robots-Tag 확인"])
    # 소유확인
    va = c.get("verification_any") or {}
    if not va.get("naver") and not va.get("google"):
        # 메타 부재 ≠ 미등록 (파일·DNS 인증 가능). G-N-verify 와 같은 기준으로 HOLD, 감점 없음.
        F.add("S-T-verify", "S", "technical", "INFO", "HOLD", "네이버·구글 소유확인 메타가 없습니다 (등록 여부 확인 필요)",
              detail="파일/DNS 방식으로 이미 등록했을 수 있습니다. 메타가 없다는 사실만 확인된 것이며 미등록 확정은 아닙니다. 서치어드바이저·서치콘솔 화면으로 확인하고, 미등록이면 P1 로 올려 처리합니다.",
              evidence=["naver-site-verification 0건", "google-site-verification 0건"],
              fix="미등록이면: 서치어드바이저·서치콘솔 등록 → 사이트맵 제출 → 수집 요청", refs=["K26", "K71", "NEO-1"], weight=0)
    else:
        F.ok("S-T-verify", "S", "technical", "검색엔진 소유확인 메타 존재", evidence=[f"naver={va.get('naver')} google={va.get('google')}"], refs=["NEO-1"])
    # canonical
    no_canon = [p for p, pg in pages.items() if not pg.get("canonical")]
    if no_canon:
        sev = "P0" if (len(no_canon) >= 2 and len(no_canon) * 2 >= n) else "P1"   # 절반 이상이면서 2페이지 이상일 때만 P0
        F.add("S-T-canonical", "S", "technical", sev, "FAIL", f"canonical 없는 페이지 {len(no_canon)}/{n}", evidence=no_canon[:8],
              fix='<link rel="canonical" href="정규 URL"> 자기 참조', weight=4 if sev == "P0" else 2)
    else:
        mism = [p for p, pg in pages.items() if pg.get("canonical") and not pg["canonical"].rstrip("/").endswith(p.rstrip("/")) and p != "/" and pg["canonical"].rstrip("/") != (pg.get("final_url") or "").rstrip("/")]
        if mism:
            # 한 URL 로 몰린 정도를 본다. 소수의 별칭 페이지 정리는 정상, 다수가 한 곳으로 몰리면 색인 붕괴다.
            from collections import Counter as _Cnt
            tgt = _Cnt((pages[p].get("canonical") or "").rstrip("/") for p in mism)
            top_t, top_n = (tgt.most_common(1) or [("", 0)])[0]
            if top_n >= 3 and top_n * 2 >= n:
                F.add("S-T-canonical-collapse", "S", "technical", "P0", "FAIL",
                      f"서로 다른 {top_n}개 페이지의 canonical 이 한 URL 로 하드코딩돼 있습니다 (전체 {n})",
                      detail="canonical 은 '이 URL 이 정본' 이라는 선언입니다. 독립 콘텐츠 페이지들이 한 URL 을 가리키면 검색엔진은 그 페이지들을 정본의 사본으로 보고 색인에서 제외합니다. "
                             "'도메인 권위를 한곳에 모은다'는 이유로 전 페이지 메타를 통일하는 시공이 있는데, 권위는 그렇게 모이지 않고 개별 페이지의 색인 자격만 사라집니다.",
                      evidence=[f"{q} → {pages[q].get('canonical')}" for q in mism[:8]] + [f"집중 대상: {top_t}"],
                      fix="페이지마다 자기 자신을 가리키는 canonical (self-referencing) 로 되돌립니다. 별칭·정렬 파라미터 URL 만 정본을 가리킵니다."
                          + (f" [{plat['name']}] {plat['fix']}" if plat and plat.get("fix") else ""),
                      weight=5)
            else:
                F.add("S-T-canonical-mis", "S", "technical", "INFO", "PASS", f"canonical 이 다른 URL 을 가리키는 페이지 {len(mism)}개 (중복 처리 의도면 정상)",
                      detail="의도한 정규화(별칭·변형 페이지)면 그대로 두고, 독립 콘텐츠인데 홈으로 모아진 것이면 자기 참조로 바꾼다.",
                      evidence=[f"{q} → {pages[q].get('canonical')}" for q in mism[:6]], weight=0)
        F.ok("S-T-canonical", "S", "technical", "전 페이지 canonical 존재", evidence=[f"{n}/{n} · 자기 참조 {n - len(mism)}"])
    # 다국어
    langs = c.get("languages") or []
    if len(langs) > 1 or c.get("hreflang_pages"):
        no_hl = [p for p, pg in pages.items() if not pg.get("hreflang")]
        if no_hl and len(no_hl) < n:
            F.add("S-T-hreflang", "S", "technical", "P1", "FAIL", f"hreflang 없는 페이지 {len(no_hl)}/{n} (다국어 사이트)", evidence=no_hl[:6],
                  fix="언어판마다 ko/en/…/x-default 상호 참조", refs=["K63"], weight=2)
        elif no_hl and len(no_hl) == n and len(langs) > 1:
            F.add("S-T-hreflang", "S", "technical", "P1", "FAIL", "언어판이 있는데 hreflang 이 전혀 없습니다", evidence=[f"lang={langs}"],
                  fix="hreflang 클러스터 + x-default", refs=["K63"], weight=3)
        else:
            xd = sum(1 for pg in pages.values() if "x-default" in (pg.get("hreflang") or {}))
            F.ok("S-T-hreflang", "S", "technical", "hreflang 클러스터 존재", evidence=[f"{c.get('hreflang_pages')}페이지, x-default {xd}페이지, lang={langs}"], refs=["K63"])
    lang_mis = []
    for p, pg in pages.items():
        l = (pg.get("lang") or "").lower()
        if re.search(r"/(en|ja|zh|jp|cn)(/|$)", p) and l.startswith("ko"):
            lang_mis.append(f"{p} lang={l}")
    if lang_mis:
        F.add("S-T-lang", "S", "technical", "P1", "FAIL", "외국어 페이지의 <html lang> 이 ko 입니다", evidence=lang_mis[:5], fix="lang 속성을 실제 언어로", weight=2)
    no_lang = [p for p, pg in pages.items() if not pg.get("lang")]
    if no_lang:
        F.add("S-T-lang-none", "S", "technical", "P2", "FAIL", f"<html lang> 없는 페이지 {len(no_lang)}", evidence=no_lang[:5], weight=1)
    lang_vs_text = [f"{p}: lang={pg.get('lang')} 본문={pg.get('text_lang')}" for p, pg in pages.items()
                    if pg.get("lang") and pg.get("text_lang") and pg["lang"].lower().split("-")[0] != pg["text_lang"]]
    if lang_vs_text:
        F.add("S-T-lang-text", "S", "technical", "P2", "FAIL", f"<html lang> 과 본문 문자가 다른 페이지 {len(lang_vs_text)}",
              detail="본문 문자 구성으로 추정한 언어입니다. 번역기·스크린리더·검색엔진이 lang 을 믿습니다.", evidence=lang_vs_text[:5], fix="lang 을 실제 언어로", weight=1)
    no_vp = [p for p, pg in pages.items() if not pg.get("viewport")]
    if no_vp:
        F.add("S-T-viewport", "S", "technical", "P1", "FAIL", f"viewport 메타 없는 페이지 {len(no_vp)}", evidence=no_vp[:5], refs=["K21"], weight=3)


    # --- 스니펫 차단 (Google: AI 답변의 지원 링크는 "색인 + 스니펫 노출 자격" 이 전제) ---
    snip = []
    for q, pg in pages.items():
        rm = (pg.get("robots_meta") or "") + " " + (pg.get("x_robots_tag") or "")
        hit = re.findall(r"nosnippet|max-snippet\s*:\s*0|noarchive", rm, re.I)
        if hit:
            snip.append(f"{q}: {', '.join(sorted(set(x.lower() for x in hit)))}")
    if snip:
        sev = "P0" if len(snip) * 2 >= n else "P1"
        F.add("S-T-snippet-block", "S", "technical", sev, "FAIL", f"검색 스니펫을 막아 둔 페이지 {len(snip)}/{n}",
              detail="구글 공식 문서는 AI Overviews·AI Mode 에 지원 링크로 노출되려면 색인돼 있고 '스니펫과 함께 검색에 노출될 자격' 이 있어야 한다고 적고 있습니다. "
                     "nosnippet·max-snippet:0·noarchive 는 그 자격을 스스로 끕니다. 색인은 되지만 인용 후보에서 빠집니다.",
              evidence=snip[:6], fix="해당 메타/헤더를 지웁니다. 일부만 가리려면 data-nosnippet 속성으로 구간을 지정합니다.",
              weight=4 if sev == "P0" else 2)
    else:
        F.ok("S-T-snippet-block", "S", "technical", "스니펫 차단 지시 없음", evidence=[f"{n}/{n} 페이지 nosnippet·max-snippet:0·noarchive 없음"])

    # --- 혼합 콘텐츠 / http 링크 (서브리소스와 단순 링크를 나눈다) ---
    if (c.get("origin") or "").startswith("https"):
        sub, anchors = [], 0
        for q, pg in pages.items():
            h = raw_html(c, pg)
            if not h:
                continue
            for m in re.finditer(r"<(script|img|iframe|source|video|audio|embed)\b[^>]*\bsrc=[\"']http://(?!localhost|127\.)([^\"']+)", h, re.I):
                sub.append(f"{q}: <{m.group(1).lower()}> http://{m.group(2)[:50]}")
            for m in re.finditer(r"<link\b[^>]*\bhref=[\"']http://(?!localhost|127\.)([^\"']+)", h, re.I):
                sub.append(f"{q}: <link> http://{m.group(1)[:50]}")
            anchors += len(re.findall(r"<a\b[^>]*\bhref=[\"']http://(?!localhost|127\.)", h, re.I))
        if sub:
            F.add("S-T-mixed", "S", "technical", "P1", "FAIL", f"https 페이지가 http 리소스를 불러옵니다 {len(sub)}건",
                  detail="브라우저가 차단하거나 경고합니다. 이미지·스크립트·스타일이 안 뜨는 원인이 됩니다.",
                  evidence=sub[:6], fix="//  또는 https:// 로 바꿉니다.", weight=2)
        elif anchors:
            F.add("S-T-link-http", "S", "technical", "INFO", "FAIL", f"http:// 로 시작하는 바깥 링크 {anchors}건",
                  detail="서브리소스가 아니라 단순 링크라 브라우저가 막지는 않습니다. 상대 사이트가 https 로 리다이렉트하므로 왕복이 한 번 늘 뿐입니다. "
                         "점수에는 넣지 않았습니다.",
                  evidence=[f"{anchors}건 (script·img·link 같은 서브리소스는 0건)"], fix="링크 주소를 https:// 로", weight=0)
        else:
            F.ok("S-T-mixed", "S", "technical", "혼합 콘텐츠 없음", evidence=[f"{n}페이지 http 리소스 0"])

    # --- 자격증명 노출 ---
    hard, soft = [], []
    for q, pg in pages.items():
        h = raw_html(c, pg)
        if not h:
            continue
        if SECRET_HARD.search(h):
            hard.append(q)
        if SECRET_SOFT.search(h):
            soft.append(q)
    if hard:
        F.add("S-T-secret", "S", "technical", "P0", "FAIL", f"비밀키로 보이는 문자열이 HTML 에 노출됐습니다 ({len(hard)}페이지)",
              detail="개인키·AWS 액세스키·토큰은 클라이언트에 두면 안 됩니다. 값은 여기 적지 않습니다. 즉시 폐기·재발급하세요.",
              evidence=[f"{q} (값은 생략)" for q in hard[:5]], fix="해당 키를 폐기하고 서버 환경변수로 옮깁니다.", weight=6)
    elif soft:
        F.add("S-T-secret", "S", "technical", "INFO", "PASS", f"클라이언트 API 키가 HTML 에 있습니다 ({len(soft)}페이지)",
              detail="지도·폰트 등 브라우저에서 쓰는 키는 노출이 정상입니다. 다만 콘솔에서 HTTP 리퍼러·API 제한이 걸려 있는지 확인이 필요합니다.",
              evidence=[f"{q} (값은 생략)" for q in soft[:5]], fix="발급 콘솔에서 도메인 제한 확인", weight=0)

    # --- 법정 표기 (폼이 개인정보를 받는 경우) ---
    contact_pages, has_policy = [], False
    for q, pg in pages.items():
        h = raw_html(c, pg)
        if not h:
            continue
        if re.search(r"개인정보\s*(처리|취급)\s*방침|개인정보처리방침|privacy\s*policy", h, re.I):
            has_policy = True
        if re.search(r"<input[^>]+type=[\"'](?:email|tel)[\"']", h, re.I) or re.search(r"<textarea", h, re.I)            or re.search(r"<input[^>]+name=[\"'][^\"']*(?:name|phone|tel|email|mobile|이름|연락처)[^\"']*[\"']", h, re.I):
            contact_pages.append(q)
    if contact_pages and not has_policy:
        F.hold("S-T-legal", "S", "technical", f"연락처를 받는 입력폼이 있는데 개인정보처리방침이 안 보입니다 ({len(contact_pages)}페이지)",
               detail="이름·연락처·이메일을 받으면 개인정보보호법상 처리방침 공개와 동의 절차가 필요합니다. 다만 그 폼이 실제로 개인정보를 저장하는지, "
                      "외부 예약 서비스로 넘기기만 하는지에 따라 달라집니다. 운영자 확인이 필요합니다.",
               evidence=contact_pages[:5] + ["사이트 전체에서 '개인정보처리방침' 문자열 0건"],
               fix="수집한다면 처리방침 페이지를 만들고 푸터에서 링크합니다. 넘기기만 한다면 그 사실을 폼 옆에 적습니다.")
    elif has_policy:
        F.ok("S-T-legal", "S", "technical", "개인정보처리방침 표기 있음")


    # --- 스키마 값이 화면에도 있는가 ---
    # AI 와 검색엔진은 보이는 HTML 을 함께 읽는다. 구조화 데이터에만 있고 화면 어디에도 없는 값은
    # 확인할 수 없는 주장이 된다. 대표 페이지 한 장이 아니라 '전 페이지 어디에도 없을 때' 만 지적한다
    # (프로토타입에서 언어 선택 루트만 보고 오탐을 냈다).
    all_text = " ".join(visible_text(raw_html(c, pg)) for pg in pages.values())
    all_digits = re.sub(r"[^0-9]", "", all_text)
    all_squeezed = re.sub(r"\s+", "", all_text)
    drift, seen_claims = [], set()
    for pg in pages.values():
        for b in ((pg.get("jsonld") or {}).get("business") or []):
            tel = b.get("telephone")
            if isinstance(tel, str) and tel.strip():
                for dg in phone_variants(tel):
                    if len(dg) >= 8 and dg in all_digits:
                        break
                else:
                    key = re.sub(r"[^0-9]", "", tel)
                    if len(key) >= 8 and ("tel", key) not in seen_claims:
                        seen_claims.add(("tel", key))
                        drift.append(f"전화 {tel} — 화면 어디에도 없음")
            addr = b.get("address")
            if isinstance(addr, dict):
                st = addr.get("streetAddress")
                if isinstance(st, str) and len(st.strip()) >= 6:
                    core = re.sub(r"\s+", "", st)[:10]
                    if core and core not in all_squeezed and ("addr", core) not in seen_claims:
                        seen_claims.add(("addr", core))
                        drift.append(f"주소 {st[:40]} — 화면 어디에도 없음")
    if drift:
        F.add("S-S-drift", "S", "schema", "P1", "FAIL", f"구조화 데이터에만 있고 화면에는 없는 값 {len(drift)}건",
              detail="사람이 볼 수 없는 정보는 확인할 수 없는 주장입니다. 검색엔진도 화면과 마크업이 어긋나면 마크업 쪽을 신뢰하지 않습니다. "
                     "값이 바뀌었는데 한쪽만 고친 경우가 대부분입니다.",
              evidence=drift[:6], fix="같은 값을 화면에도 적습니다. 값이 바뀌었으면 양쪽을 함께 고칩니다.", weight=2)
    elif any((pg.get("jsonld") or {}).get("business") for pg in pages.values()):
        F.ok("S-S-drift", "S", "schema", "구조화 데이터의 전화·주소가 화면에도 있음")

    # --- 폐기된 리치결과 타입 ---
    dep = {}
    for q, pg in pages.items():
        for t in ((pg.get("jsonld") or {}).get("types") or []):
            if t in DEPRECATED_RICH:
                dep.setdefault(t, []).append(q)
    if dep:
        F.add("S-S-deprecated", "S", "schema", "INFO", "PASS", f"구글이 리치결과를 내린 스키마 타입 {len(dep)}종이 있습니다",
              detail="마크업 자체는 여전히 유효합니다. 지우라는 뜻이 아니라, 이 타입을 넣었다고 검색결과에 특별한 카드가 뜨지는 않는다는 뜻입니다. "
                     "FAQ 를 넣는 이유는 리치결과가 아니라 화면에 실제로 있는 질문·답변을 기계가 읽게 하는 것입니다.",
              evidence=[f"{t} — {DEPRECATED_RICH[t]} ({len(ps)}페이지)" for t, ps in dep.items()],
              fix="유지해도 됩니다. 다만 '리치결과가 나온다'는 이유로 정당화하지 않습니다.", weight=0)



    # --- 인증서 만료 ---
    tls = c.get("tls") or {}
    if tls and not tls.get("error") and tls.get("days_left") is not None:
        dl = tls["days_left"]
        if dl < 0:
            F.add("S-T-ssl", "S", "technical", "P0", "FAIL", "TLS 인증서가 만료됐습니다",
                  detail="브라우저가 경고 화면을 먼저 띄웁니다. 사이트가 살아 있어도 사실상 접속 불가입니다.",
                  evidence=[f"만료 {tls.get('not_after')} ({-dl}일 지남)", f"발급 {tls.get('issuer')}"],
                  fix="즉시 갱신하고 자동 갱신(certbot·ACME)이 도는지 확인합니다.", weight=8)
        elif dl < 14:
            F.add("S-T-ssl", "S", "technical", "P1", "FAIL", f"TLS 인증서가 {dl}일 뒤 만료됩니다",
                  detail="자동 갱신이 걸려 있다면 정상입니다. 수동 발급이면 지금 갱신하세요.",
                  evidence=[f"만료 {tls.get('not_after')}", f"발급 {tls.get('issuer')}"], weight=3)
        else:
            F.ok("S-T-ssl", "S", "technical", f"TLS 인증서 유효 (남은 {dl}일)",
                 evidence=[f"만료 {tls.get('not_after')} · 발급 {tls.get('issuer')}"])

    # --- 깨진 내부 링크 / 빈 앵커 ---
    lp = c.get("link_probe") or {}
    if lp and not lp.get("error"):
        brk = lp.get("broken") or []
        ba = lp.get("bad_anchor") or []
        if brk:
            F.add("S-L-broken", "S", "technical", "P1", "FAIL", f"눌러도 안 열리는 내부 링크 {len(brk)}건",
                  detail="방문자가 막다른 길을 만나고, 크롤러는 그 경로에서 멈춥니다. 사이트맵에 없던 링크까지 실제로 눌러 본 결과입니다.",
                  evidence=[f"{b['from']} → {b['to']} ({b.get('status') or b.get('error')})" for b in brk[:6]],
                  fix="주소를 고치거나 링크를 지웁니다. 옮긴 페이지면 301 로 연결합니다.", weight=2.5)
        else:
            F.ok("S-L-broken", "S", "technical", "내부 링크 도달 확인", evidence=[f"추가 확인 {lp.get('checked', 0)}건 전부 정상"])
        if ba:
            F.add("S-L-anchor", "S", "technical", "P2", "FAIL", f"대상이 없는 앵커 링크 {len(ba)}건",
                  detail="한 페이지 안에서 구간으로 이동하는 링크인데 그 id 가 문서에 없습니다. 눌러도 아무 일이 없습니다.",
                  evidence=[f"{b['from']} → {b['to']}" for b in ba[:6]], fix="대상 요소에 id 를 붙이거나 링크를 고칩니다.", weight=1)
        elif lp.get("anchor_links"):
            F.ok("S-L-anchor", "S", "technical", "앵커 링크 대상 확인", evidence=[f"앵커 {lp['anchor_links']}건 전부 대상 존재"])


    # --- 리다이렉트 체인 · 순환 ---
    rp = c.get("redirect_probe") or {}
    if rp and not rp.get("error") and rp.get("checked"):
        loops, chains = rp.get("loops") or [], rp.get("chains") or []
        if loops:
            F.add("S-T-redirect-loop", "S", "technical", "P0", "FAIL", f"리다이렉트가 제자리를 도는 주소 {len(loops)}건",
                  detail="브라우저도 크롤러도 그 페이지를 열지 못합니다. 색인에서 사라집니다.",
                  evidence=[f"{l['path']}: " + " → ".join(h["to"][:60] for h in l["hops"][:3]) for l in loops[:4]],
                  fix="리다이렉트 규칙이 서로를 가리키는 곳을 끊습니다.", weight=6)
        if chains:
            worst = max(x["n"] for x in chains)
            sev = "P1" if worst >= 3 else "P2"
            F.add("S-T-redirect-chain", "S", "technical", sev, "FAIL", f"두 번 이상 튕기는 주소 {len(chains)}건 (최대 {worst}홉)",
                  detail="매 요청이 왕복을 더하고 크롤러는 홉 예산을 씁니다. 중간 단계를 건너뛰고 처음부터 최종 주소로 보내면 됩니다.",
                  evidence=[f"{x['path']} — {x['n']}홉: " + " → ".join(u[:44] for u in x["trail"][:3]) for x in chains[:4]],
                  fix="첫 규칙이 곧바로 최종 URL 을 가리키게 고칩니다.", weight=2 if sev == "P1" else 1)
        if not loops and not chains:
            F.ok("S-T-redirect-chain", "S", "technical", "리다이렉트 체인 없음", evidence=[f"리다이렉트 {rp['checked']}건 전부 1홉"])

    # --- 본문이 사실상 같은 페이지 ---
    import hashlib as _hl
    sig: dict[str, list[str]] = {}
    for q, pg in pages.items():
        t = re.sub(r"\s+", "", visible_text(raw_html(c, pg)))
        if len(t) < 300:                       # 너무 짧으면 우연히 같을 수 있다
            continue
        robots = ((pg.get("robots_meta") or "") + " " + (pg.get("x_robots_tag") or "")).lower()
        if "noindex" in robots:                # 운영자가 이미 정리한 페이지는 뺀다
            continue
        canon = (pg.get("canonical") or "").rstrip("/")
        if canon and not canon.endswith(q.rstrip("/")) and q != "/":
            continue                           # 다른 URL 로 정규화해 둔 페이지도 뺀다
        sig.setdefault(_hl.sha1(t.encode("utf-8")).hexdigest(), []).append(q)
    dupc = [ps for ps in sig.values() if len(ps) > 1]
    if dupc:
        F.add("S-C-dup-content", "S", "content", "P1", "FAIL", f"본문이 사실상 같은 페이지 묶음 {len(dupc)}개",
              detail="검색엔진은 그중 하나만 고르고 나머지는 색인에서 뺍니다. 어느 쪽이 남을지는 우리가 정할 수 없습니다. "
                     "정말 같은 내용이면 하나로 합치거나 canonical 로 정본을 지정하고, 다른 내용이어야 한다면 실제로 달라야 합니다.",
              evidence=[" = ".join(g[:4]) for g in dupc[:5]],
              fix="합치기 · canonical 로 정본 지정 · 또는 내용을 실제로 다르게", weight=3)
    elif len(sig) >= 2:
        F.ok("S-C-dup-content", "S", "content", "페이지마다 본문이 다릅니다", evidence=[f"본문 300자 이상 {len(sig)}페이지 비교"])

    # --- 헤딩 단계 건너뛰기 ---
    skips = []
    for q, pg in pages.items():
        lv = [int(h["tag"][1]) for h in (pg.get("headings") or []) if re.fullmatch(r"h[1-6]", h.get("tag") or "")]
        prev = None
        for x in lv:
            if prev is not None and x > prev + 1:
                skips.append(f"{q}: h{prev} 다음에 h{x}")
                break
            prev = x
    if skips:
        F.add("S-O-heading-order", "S", "onpage", "P2", "FAIL", f"제목 단계를 건너뛴 페이지 {len(skips)}개",
              detail="h2 없이 h1 에서 h3 으로 넘어가면 문서의 층이 어긋납니다. 화면 낭독기와 본문을 덩어리로 끊어 읽는 기계가 구조를 잘못 잡습니다. "
                     "글자 크기 때문에 단계를 고른 경우가 대부분인데, 크기는 CSS 로 정하고 단계는 내용의 층으로 정합니다.",
              evidence=skips[:6], fix="단계를 순서대로 쓰고 크기는 CSS 로", weight=1)
    else:
        F.ok("S-O-heading-order", "S", "onpage", "제목 단계가 순서대로입니다")

    # --- 클릭 깊이 · 막다른 페이지 ---
    adj: dict[str, set[str]] = {}
    for q, pg in pages.items():
        outs = set()
        for l in ((pg.get("links") or {}).get("internal") or []):
            tgt = path_of_url(l)
            if tgt in pages and tgt != q:
                outs.add(tgt)
        adj[q] = outs
    if "/" in adj and len(pages) >= 4:
        depth = {"/": 0}
        frontier = ["/"]
        while frontier:
            nxt = []
            for q in frontier:
                for t in adj.get(q, ()):
                    if t not in depth:
                        depth[t] = depth[q] + 1
                        nxt.append(t)
            frontier = nxt
        deep = sorted(((d, q) for q, d in depth.items() if d >= 4), reverse=True)
        if deep:
            F.add("S-O-depth", "S", "onpage", "P2", "FAIL", f"홈에서 {deep[0][0]}번 이상 눌러야 닿는 페이지 {len(deep)}개",
                  detail="깊이 들어간 페이지는 사람도 크롤러도 늦게 닿습니다. 홈이나 주요 페이지에서 세 번 안에 닿게 두는 편이 안전합니다.",
                  evidence=[f"{q} — {d}단계" for d, q in deep[:6]],
                  fix="주요 페이지 목록이나 푸터에서 직접 링크합니다.", weight=1)
        else:
            F.ok("S-O-depth", "S", "onpage", "모든 페이지가 홈에서 3번 안에 닿습니다",
                 evidence=[f"최대 깊이 {max(depth.values())}단계 · {len(depth)}/{len(pages)}페이지 도달"])
    dead = [q for q, outs in adj.items() if not outs and (pg := pages.get(q)) and (pg.get("links") or {}).get("internal_count", 0) == 0]
    if dead and len(pages) >= 3:
        F.add("S-O-no-outlink", "S", "onpage", "INFO", "FAIL", f"원본 HTML 에 나가는 링크가 없는 페이지 {len(dead)}개",
              detail="들어온 사람이 다음으로 갈 곳이 없고, 크롤러도 그 페이지에서 멈춥니다. 다만 내비게이션을 자바스크립트로 그리는 사이트라면 "
                     "브라우저에서는 링크가 보입니다. 자바스크립트를 실행하지 않는 수집기 기준의 관측이라 점수에는 넣지 않았습니다.",
              evidence=dead[:6], fix="관련 페이지나 홈으로 가는 링크를 원본 HTML 에 둡니다.", weight=0)

    # --- 온페이지 (CS-onpage)
    no_title = [p for p, pg in pages.items() if not (pg.get("title") or "").strip()]
    if no_title:
        F.add("S-O-title", "S", "onpage", "P0", "FAIL", f"title 없는 페이지 {len(no_title)}", evidence=no_title[:6], weight=5)
    long_t = [f"{p} ({pg['title_len']}자)" for p, pg in pages.items() if pg.get("title_len", 0) > 60]
    short_t = [f"{p} ({pg['title_len']}자)" for p, pg in pages.items() if 0 < pg.get("title_len", 0) < 15]
    if long_t:
        F.add("S-O-title-len", "S", "onpage", "P2", "FAIL", f"title 60자 초과 {len(long_t)}페이지", evidence=long_t[:6], fix="핵심어를 앞으로, 60자 이내", weight=1)
    if short_t:
        F.add("S-O-title-short", "S", "onpage", "P2", "FAIL", f"title 15자 미만 {len(short_t)}페이지", evidence=short_t[:6], weight=1)
    dup = c.get("duplicate_titles") or {}
    if dup:
        big = max((len(ps) for ps in dup.values()), default=0)
        if big >= 3 and big * 2 >= n:
            F.add("S-O-title-dup", "S", "onpage", "P0", "FAIL",
                  f"{big}개 페이지가 같은 title 을 씁니다 (전체 {n}) — 페이지별 제목이 없습니다",
                  detail="AI 답변이든 검색 결과든 인용의 최소 단위는 사이트가 아니라 URL 입니다. 모든 페이지가 같은 제목이면 어느 페이지를 보여줄지 고를 근거가 사라지고, 클릭 대상 문구도 페이지 내용과 어긋납니다.",
                  evidence=[f"'{t[:40]}' ← {ps}" for t, ps in sorted(dup.items(), key=lambda kv: -len(kv[1]))[:4]],
                  fix="페이지마다 그 페이지가 답하는 질문을 제목으로 씁니다. 한 페이지 한 질문이면 고유 제목은 따라옵니다."
                      + (f" [{plat['name']}] {plat['fix']}" if plat and plat.get("fix") else ""), weight=4)
        else:
            F.add("S-O-title-dup", "S", "onpage", "P1", "FAIL", f"제목이 같은 페이지 묶음 {len(dup)}", evidence=[f"'{t[:40]}' ← {ps}" for t, ps in list(dup.items())[:4]], fix="페이지마다 고유 제목", weight=2)
    brand_twice = []
    for p, pg in pages.items():
        t = pg.get("title") or ""
        parts = [x.strip() for x in re.split(r"\s[|·\-–—]\s", t) if x.strip()]
        if len(parts) >= 2 and len(set(parts)) < len(parts):
            brand_twice.append(f"{p}: {t[:60]}")
    if brand_twice:
        F.add("S-O-title-repeat", "S", "onpage", "P1", "FAIL", "제목 안에 같은 구절(상호)이 두 번 들어갑니다", evidence=brand_twice[:5], fix="제목 템플릿 중복 제거", weight=2)
    no_desc = [p for p, pg in pages.items() if not (pg.get("description") or "").strip()]
    short_d = [f"{p} ({pg['desc_len']}자)" for p, pg in pages.items() if 0 < pg.get("desc_len", 0) < 50]
    long_d = [f"{p} ({pg['desc_len']}자)" for p, pg in pages.items() if pg.get("desc_len", 0) > 160]
    if no_desc:
        F.add("S-O-desc", "S", "onpage", "P1", "FAIL", f"meta description 없는 페이지 {len(no_desc)}/{n}", evidence=no_desc[:6], fix="80~120자, 지역·대표메뉴·결정 요인 포함", weight=3)
    if short_d:
        sev = "P1" if len(short_d) >= max(2, n // 2) else "P2"
        F.add("S-O-desc-short", "S", "onpage", sev, "FAIL", f"description 50자 미만 {len(short_d)}페이지", evidence=short_d[:6], fix="80~120자로 확장", weight=1.5 if sev == "P1" else 0.8)
    if long_d:
        F.add("S-O-desc-long", "S", "onpage", "P2", "FAIL", f"description 160자 초과 {len(long_d)}페이지", evidence=long_d[:6], weight=0.5)
    if not no_desc and not short_d:
        F.ok("S-O-desc", "S", "onpage", "description 전 페이지 적정 길이", evidence=[f"{n}페이지 50~160자"])
    h1_bad = [f"{p} (H1 {len(pg.get('h1') or [])}개)" for p, pg in pages.items() if len(pg.get("h1") or []) != 1]
    if h1_bad:
        F.add("S-O-h1", "S", "onpage", "P1", "FAIL", f"H1 이 정확히 1개가 아닌 페이지 {len(h1_bad)}", evidence=h1_bad[:6], fix="페이지당 H1 하나", weight=2)
    else:
        F.ok("S-O-h1", "S", "onpage", "전 페이지 H1 1개", evidence=[(pages.get('/') or {}).get('h1', [''])[0][:60] if pages.get('/') else ''])
    sr_only = [p for p, pg in pages.items() if pg.get("h1") and not pg.get("h1_visible")]
    if sr_only:
        F.add("S-O-h1-hidden", "S", "onpage", "P2", "FAIL", f"H1 이 화면에 안 보이는(sr-only) 페이지 {len(sr_only)}", evidence=sr_only[:5],
              detail="틀린 것은 아니지만 첫 가시 헤딩이 지역·업종·상호를 담는지 확인해야 합니다.", weight=0.5)
    no_og = [p for p, pg in pages.items() if not ((pg.get("og") or {}).get("title") and (pg.get("og") or {}).get("image"))]
    if no_og:
        sev = "P0" if (len(no_og) >= 2 and len(no_og) * 2 >= n) else "P1"
        F.add("S-O-og", "S", "onpage", sev, "FAIL", f"OG 제목/이미지 없는 페이지 {len(no_og)}/{n}", evidence=no_og[:6],
              fix="og:title·description·image(1200×630)·url·site_name·locale", detail="카카오톡·인스타 DM 공유 미리보기가 비어 보입니다.", weight=4 if sev == "P0" else 2)
    else:
        F.ok("S-O-og", "S", "onpage", "OG 태그 세트 존재", evidence=[f"{n}/{n}"])
    if n >= 3:
        from collections import Counter as _C2
        hard = []
        for key, label in (("og:url", "og:url"), ("og:title", "og:title")):
            vals = [(pg.get("og") or {}).get(key) for pg in pages.values()]
            vals = [v for v in vals if v]
            if len(vals) >= 3:
                v0, k0 = (_C2(vals).most_common(1) or [("", 0)])[0]
                if k0 >= 3 and k0 * 2 >= n:
                    hard.append(f"{label}: {k0}/{n}페이지가 '{str(v0)[:60]}' 동일")
        dd = [(pg.get("description") or "").strip() for pg in pages.values()]
        dd = [d for d in dd if d]
        if len(dd) >= 3:
            d0, dk = (_C2(dd).most_common(1) or [("", 0)])[0]
            if dk >= 3 and dk * 2 >= n:
                hard.append(f"meta description: {dk}/{n}페이지가 '{d0[:50]}' 동일")
        if hard:
            F.add("S-O-meta-hardcoded", "S", "onpage", "P1", "FAIL",
                  f"페이지가 달라도 바뀌지 않는 메타가 {len(hard)}종 있습니다",
                  detail="템플릿 머리말에 값을 고정해 두면 모든 페이지가 같은 값을 내보냅니다. 공유 카드는 어느 페이지를 공유해도 같은 그림이 뜨고, 검색 결과 문구도 페이지와 어긋납니다. "
                         "og:url 이 고정된 경우 특히 나쁩니다 — 공유·수집 경로가 전부 그 한 URL 로 돌아갑니다.",
                  evidence=hard, fix="메타를 페이지 변수로 뺍니다. og:url 은 그 페이지의 canonical 과 같은 값이어야 합니다."
                       + (f" [{plat['name']}] {plat['fix']}" if plat and plat.get("fix") else ""), weight=2.5)
    no_tw = [p for p, pg in pages.items() if not (pg.get("twitter") or {}).get("card")]
    if no_tw and len(no_tw) == n:
        F.add("S-O-twitter", "S", "onpage", "P2", "FAIL", "twitter:card 없음", fix='<meta name="twitter:card" content="summary_large_image">', weight=0.5)
    # 내부 링크
    lg = c.get("link_graph") or {}
    orphans = lg.get("orphans") or []
    if orphans:
        F.add("S-O-orphan", "S", "onpage", "P1", "FAIL", f"내부 링크를 하나도 못 받는 페이지 {len(orphans)}", evidence=[f"{p} (유입 0)" for p in orphans[:6]],
              fix="헤더·푸터·관련 섹션에서 링크", refs=["K19"], weight=2.5)
    elif n > 1:
        F.ok("S-O-links", "S", "onpage", "고아 페이지 없음", evidence=[f"유입 수 {dict(list((lg.get('inbound') or {}).items())[:6])}"], refs=["K19"])
    # 사이트맵 누락은 S-T-sitemap-miss 가 이미 다루므로, 여기서는 확인(크롤)하지 못한 URL 만 (중복 집계 방지)
    checked_paths = {p.rstrip("/") for p in pages}
    not_sm = [u for u in (c.get("not_in_sitemap") or []) if path_of_url(u) not in checked_paths]
    if not_sm:
        F.add("S-O-discover", "S", "onpage", "P2", "FAIL", f"홈에서 링크되지만 사이트맵에 없고 이번에 확인하지 못한 URL {len(not_sm)}", evidence=not_sm[:5], weight=0.5)
    no_fav = [p for p, pg in pages.items() if not pg.get("favicon")]
    if no_fav and len(no_fav) == n:
        F.add("S-O-favicon", "S", "onpage", "P2", "FAIL", "favicon 링크 없음", weight=0.5)

    # --- 콘텐츠 (CS-content)
    thin = [f"{p} ({pg.get('body_chars', 0):,}자)" for p, pg in pages.items() if 0 <= (pg.get("body_chars") or 0) < 600 and not p.startswith("/__")]
    if thin:
        sev = "P1" if len(thin) >= max(1, n // 2) else "P2"
        F.add("S-C-thin", "S", "content", sev, "FAIL", f"본문 600자 미만 페이지 {len(thin)}/{n}", evidence=thin[:8],
              fix="각 페이지 1,000자 이상: 실제 차별점·주문 흐름·가격·주차·동선", refs=["K05", "K18", "K34"], weight=4 if sev == "P1" else 2)
    total_chars = c.get("body_chars_total") or 0
    F.add("S-C-total", "S", "content", "INFO", "PASS", f"본문 합계 {total_chars:,}자 / {n}페이지", evidence=[f"{p}: {pg.get('body_chars', 0):,}자" for p, pg in sorted(pages.items(), key=lambda kv: -(kv[1].get('body_chars') or 0))[:8]], weight=0)
    persons = c.get("persons") or []
    if persons:
        F.ok("S-C-person", "S", "content", "매장을 대표하는 사람이 구조화돼 있습니다 (Person)", evidence=persons[:3], refs=["K15", "K58", "G-EEAT"])
    else:
        F.add("S-C-person", "S", "content", "P2", "FAIL", "대표·셰프·디자이너 등 사람의 이름이 구조화돼 있지 않습니다",
              detail="E-E-A-T 의 '경험·전문성' 신호. 소개 페이지 + Person 스키마 + 경력 한 줄.", fix="대표 소개 섹션 + Person(founder) 스키마", refs=["K15", "K58", "K86", "G-EEAT"], weight=2)
    faq_pages = [p for p, pg in pages.items() if (pg.get("jsonld") or {}).get("faq_questions")]
    faq_vis = [p for p, pg in pages.items() if pg.get("details") or any("faq" in (h.get("text") or "").lower() or "자주" in (h.get("text") or "") for h in pg.get("headings", []))]
    if faq_pages or faq_vis:
        F.ok("S-C-faq", "S", "content", "FAQ 콘텐츠 존재", evidence=[f"FAQ 스키마 {len(faq_pages)}페이지, 화면 FAQ 흔적 {len(faq_vis)}페이지"], refs=["K17", "G-FAQ"])
    else:
        F.add("S-C-faq", "S", "content", "P1", "FAIL", "FAQ 가 없습니다", fix="고객이 실제 묻는 질문 10개 이상 + FAQPage 스키마 (화면에 보이는 문답만)", refs=["K17", "K84", "G-FAQ"], weight=3)
    dates = [pg.get("dates", {}).get("dateModified") for pg in pages.values() if pg.get("dates", {}).get("dateModified")]
    if dates:
        ds = [days_since(d) for d in dates]
        ds = [d for d in ds if d is not None]
        _m = min(ds) if ds else None
        _band = None if _m is None else ("30일 이내" if _m <= 30 else "90일 이내" if _m <= 90 else "180일 이내" if _m <= 180 else "180일 초과")
        if ds and min(ds) > 180:
            F.add("S-C-fresh", "S", "content", "P2", "FAIL", "dateModified 가 6개월 이상 오래됐습니다", evidence=[f"가장 최근 {min(ds)}일 전"], refs=["K78"], weight=1)
        else:
            F.ok("S-C-fresh", "S", "content", f"최근 수정일이 구조화돼 있습니다 ({_band})",
                 evidence=[f"dateModified 최근 {_m}일 전 · 구간 {_band}",
                           "갱신이 잦을수록 AI 답변에 자주 인용된다는 관측이 있어 30/90/180일 구간으로 적습니다"], refs=["K78"])
    else:
        F.add("S-C-fresh", "S", "content", "P2", "FAIL", "수정일(dateModified) 신호가 없습니다", fix="WebPage/Restaurant 노드에 dateModified", refs=["K78"], weight=1)
    # 가격 공개 (음식·서비스 업종)
    price_txt = sum(1 for pg in pages.values() if re.search(r"\d{1,3}(,\d{3})+\s*원|₩\s?\d", " ".join([pg.get("first_paragraph", "")] + [h.get("text", "") for h in pg.get("headings", [])])) )
    raw_price = 0
    for p, pg in pages.items():
        rp = os.path.join(c.get("site_dir") if c.get("mode") == "local" else os.path.dirname(os.path.abspath(os.path.join(c.get("_out", "."), "collect.json"))), pg.get("raw_path", ""))
        try:
            with open(rp, encoding="utf-8", errors="replace") as f:
                if re.search(r"\d{1,3}(,\d{3})+\s*원|₩\s?\d{1,3}(,\d{3})*", f.read()):
                    raw_price += 1
        except Exception:
            pass
    menu_items = sum((pg.get("jsonld") or {}).get("menu_items", 0) for pg in pages.values())
    if raw_price or price_txt:
        F.ok("S-C-price", "S", "content", "가격이 사이트에 공개돼 있습니다", evidence=[f"가격 표기 페이지 {raw_price}개", f"MenuItem {menu_items}개"], refs=["K14"])
    else:
        F.add("S-C-price", "S", "content", "P1", "FAIL", "사이트에 가격 표기가 없습니다",
              detail="손님이 가격을 알려고 제3자 사이트(다이닝코드·식신)로 가게 됩니다. 업종상 가격 비공개가 정책이면 HOLD 로 바꿀 것.", fix="메뉴·서비스마다 금액 + offers.price", refs=["K14", "K31"], weight=3)
    # 리뷰 근거
    rating_hits = sum((pg.get("jsonld") or {}).get("rating_hits", 0) for pg in pages.values())
    if rating_hits:
        F.add("S-C-rating", "S", "content", "P1", "HOLD", "aggregateRating/ratingValue 가 마크업에 있습니다 · 화면에 같은 평점·리뷰 수가 보이는지 확인",
              detail="화면에 없는 평점 구조화는 가이드라인 위반. 출처·갱신 조건이 맞을 때만 유지.", evidence=[f"히트 {rating_hits}건"], refs=["W-review"], weight=0)

    # --- 구조화 데이터 (CS-schema)
    ent = c.get("entity") or {}
    biz_pages = [p for p, pg in pages.items() if (pg.get("jsonld") or {}).get("business")]
    no_ld = [p for p, pg in pages.items() if not (pg.get("jsonld") or {}).get("blocks")]
    if not biz_pages:
        F.add("S-S-biz", "S", "schema", "P0", "FAIL", "LocalBusiness 계열 JSON-LD 가 없습니다",
              fix="Restaurant/NailSalon/… 노드: name·address·telephone·openingHoursSpecification·geo·image·url·sameAs·priceRange·hasMap", refs=["K16", "G-schema-min"], weight=8)
    else:
        b = (pages[biz_pages[0]]["jsonld"]["business"] or [{}])[0]
        req = ["name", "address", "telephone", "openingHoursSpecification", "geo", "image", "url", "sameAs", "priceRange", "hasMap"]
        missing = [k for k in req if not b.get(k)]
        core_missing = [k for k in ("name", "address", "telephone", "openingHoursSpecification") if k in missing]
        # 서비스 지역형 사업(areaServed 만 있고 주소 없음: 출장·프로젝트형 B2B·스튜디오)은 주소·영업시간 미공개가 사업 선택일 수 있다
        service_area = bool(b.get("areaServed")) and not b.get("address")
        c["_service_area"] = service_area
        if core_missing and service_area:
            F.add("S-S-biz-core", "S", "schema", "INFO", "HOLD", f"서비스 지역형(areaServed) 사업으로 보여 주소·영업시간 요구를 보류: 누락 {', '.join(core_missing)}",
                  detail="워크인 매장이면 P0 로 올린다. 전화·이메일 등 연락 경로 하나는 구조화돼 있어야 한다.", evidence=[f"@type {types_of(b)} · areaServed {str(b.get('areaServed'))[:60]}"], refs=["G-schema-min"], weight=0)
        elif core_missing:
            F.add("S-S-biz-core", "S", "schema", "P0", "FAIL", f"LocalBusiness 최소 필수 필드 누락: {', '.join(core_missing)}",
                  detail="이름·주소·전화·영업시간은 AI 가 위치를 파악하는 최소 조건.", evidence=[f"@type {types_of(b)}"], refs=["G-schema-min"], weight=5)
        rec_missing = [k for k in missing if k not in core_missing]
        if rec_missing:
            sev = "P1" if len(rec_missing) >= 3 else "P2"
            F.add("S-S-biz-rec", "S", "schema", sev, "FAIL", f"LocalBusiness 권장 필드 누락 {len(rec_missing)}: {', '.join(rec_missing)}",
                  evidence=[f"@type {types_of(b)} · @id {b.get('@id')}"], fix="누락 필드 채우기 (image 는 절대 URL 3장 이상)", refs=["K16", "K25"], weight=2 if sev == "P1" else 0.8)
        if not missing:
            F.ok("S-S-biz", "S", "schema", "LocalBusiness 필수·권장 필드 완비", evidence=[f"@type {types_of(b)}", f"@id {b.get('@id')}"], refs=["K16"])
        pr = b.get("priceRange")
        if pr and (len(str(pr)) > 24 or re.search(r"[가-힣]{2,}|[A-Za-z]{4,}", str(pr))):
            F.add("S-S-pricerange", "S", "schema", "P1", "FAIL", "priceRange 에 문장이 들어 있습니다", evidence=[f"priceRange = {pr!r}"], fix="₩₩ 또는 ₩6,000-₩19,000 형식", weight=2)
        if len(biz_pages) < n and n > 1:
            F.add("S-S-biz-cover", "S", "schema", "P2", "FAIL", f"업체 노드가 없는 페이지 {n - len(biz_pages)}/{n}", evidence=[p for p in pages if p not in biz_pages][:6], weight=1)
    if no_ld:
        F.add("S-S-none", "S", "schema", "P1" if len(no_ld) >= max(1, n // 2) else "P2", "FAIL", f"JSON-LD 가 전혀 없는 페이지 {len(no_ld)}/{n}", evidence=no_ld[:6], weight=2)
    perr = sum((pg.get("jsonld") or {}).get("parse_errors", 0) for pg in pages.values())
    if perr:
        F.add("S-S-parse", "S", "schema", "P0", "FAIL", f"JSON-LD 파싱 오류 {perr}건", fix="JSON 문법 검증 (리치 결과 테스트)", refs=["G-schema-validate"], weight=5)
    if not ent.get("id_consistent"):
        F.add("S-S-id", "S", "schema", "P1", "FAIL", "업체 @id 가 페이지마다 다릅니다 (엔티티 분산)", evidence=ent.get("ids", [])[:5], fix="@id 를 퓨니코드 기준 한 값으로 통일", refs=["W-entity"], weight=3)
    elif ent.get("ids"):
        F.ok("S-S-id", "S", "schema", "업체 @id 통일", evidence=ent.get("ids"), refs=["W-entity"])
    if ent.get("punycode_mixed"):
        F.add("S-S-puny", "S", "schema", "P1", "FAIL", "@id 에 한글 도메인과 퓨니코드가 섞여 있습니다", evidence=ent.get("ids", [])[:4], weight=2)
    dj = c.get("duplicate_jsonld") or {}
    for m, ps in list(dj.items())[:1]:
        if len(ps) >= 3 and any((pages[p].get("jsonld") or {}).get("faq_questions") for p in ps):
            F.add("S-S-dup", "S", "schema", "P1", "FAIL", f"동일 JSON-LD 블록이 {len(ps)}페이지에 복제돼 있습니다 (FAQPage 포함)",
                  detail="FAQPage 는 그 페이지 화면에 실제로 보이는 문답에만 붙여야 합니다.", evidence=ps[:8], fix="페이지별 노드 분리, FAQ 는 /faq 에만", weight=2)
    # FAQ 화면 일치
    for p, pg in pages.items():
        qs = (pg.get("jsonld") or {}).get("faq_questions") or []
        if not qs:
            continue
        rp = os.path.join(c.get("site_dir") if c.get("mode") == "local" else c.get("_out", "."), pg.get("raw_path", ""))
        try:
            with open(rp, encoding="utf-8", errors="replace") as f:
                body = f.read().split("</head>")[-1]
            body_plain = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.S)
            body_flat = re.sub(r"\s+", "", body_plain)
            missing, reworded = [], []
            for q in qs:
                if q.strip()[:20] in body_plain or re.sub(r"\s+", "", q)[:16] in body_flat:
                    continue
                # 표현 차이(라벨 축약)인지 완전 누락인지: 질문의 단어(2자 이상) 60% 이상이 본문에 있으면 '표현 차이'
                toks = [t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", q) if t not in ("무엇", "어떻게", "있나요", "되나요", "인가요", "합니까")]
                hit = sum(1 for t in toks if t in body_plain)
                (reworded if toks and hit / len(toks) >= 0.6 else missing).append(q)
            pg["faq_visible_match"] = {"total": len(qs), "missing": len(missing), "reworded": len(reworded)}
            if missing:
                F.add("S-S-faq-vis", "S", "schema", "P1", "FAIL", f"{p}: FAQ 스키마 질문 {len(missing)}/{len(qs)}개가 화면에 없습니다", evidence=[q[:50] for q in missing[:4]],
                      fix="화면에 보이는 문답만 FAQPage 로", refs=["W-faq"], weight=2)
            if reworded:
                F.add("S-S-faq-reword", "S", "schema", "P2", "FAIL", f"{p}: FAQ 스키마 질문 {len(reworded)}/{len(qs)}개가 화면 라벨과 표현이 다릅니다",
                      detail="문답은 화면에 있지만 라벨 문구가 스키마와 다름(주제어 생략 등). 스키마 name 을 화면 문구와 똑같이 맞춘다.",
                      evidence=[q[:50] for q in reworded[:4]], fix="스키마 name = 화면 문구", refs=["W-faq"], weight=0.5)
        except Exception:
            pass
    menu_nodes = sum((pg.get("jsonld") or {}).get("menu_nodes", 0) for pg in pages.values())
    menu_like = [p for p in pages if re.search(r"menu|메뉴|price|가격|service|시술", p, re.I)]
    # 이미 있는 것도 근거로 남긴다 (백지인지, 승격만 남았는지 구분)
    inline_sections = sum(1 for pg in pages.values() for b in ((pg.get("jsonld") or {}).get("business") or []) if b.get("hasMenuSection") or b.get("hasMenu") or b.get("hasOfferCatalog"))
    items_with_price = 0
    for pg in pages.values():
        for b in ((pg.get("jsonld") or {}).get("business") or []):
            secs = b.get("hasMenuSection") or []
            # Restaurant.hasMenu 가 Menu 객체(래퍼)면 그 안의 hasMenuSection/hasMenuItem 도 센다
            hm = b.get("hasMenu")
            if isinstance(hm, dict):
                # hasMenuSection 은 배열이 원칙이지만 섹션이 하나면 객체로 쓰는 사이트가 있다. 양쪽 다 리스트로 맞춘다.
                inner = hm.get("hasMenuSection") or []
                secs = _as_list(secs) + _as_list(inner) + ([{"hasMenuItem": hm.get("hasMenuItem")}] if hm.get("hasMenuItem") else [])
            secs = _as_list(secs)
            for sec in secs:
                if not isinstance(sec, dict):   # 문자열·URL 로 잘못 넣은 경우도 있다
                    continue
                its = sec.get("hasMenuItem") or sec.get("itemListElement") or []
                its = its if isinstance(its, list) else [its]
                for it in its:
                    if isinstance(it, dict) and (it.get("offers") or it.get("price")):
                        items_with_price += 1
    menu_ev = [f"Menu 노드 {menu_nodes} · MenuItem {menu_items} · 업체 노드 안 hasMenuSection/hasMenu/hasOfferCatalog {inline_sections}페이지 · 가격 있는 항목 {items_with_price}"]
    food_types = {"Restaurant", "FoodEstablishment", "CafeOrCoffeeShop", "BarOrPub", "Bakery"}
    is_food = bool(set((c.get("entity") or {}).get("types") or []) & food_types)
    has_catalog = any(b.get("hasOfferCatalog") or b.get("makesOffer") for pg in pages.values() for b in ((pg.get("jsonld") or {}).get("business") or []))
    if menu_like and not is_food and has_catalog:
        # 서비스업(네일·헬스·미용)은 OfferCatalog/makesOffer 가 정답 구조. Menu 노드를 요구하지 않는다
        F.ok("S-S-menu", "S", "schema", "서비스 카탈로그 구조화 존재 (OfferCatalog/makesOffer)", evidence=menu_ev, refs=["W-menu"])
    elif menu_like and not menu_nodes and inline_sections:
        F.add("S-S-menu", "S", "schema", "P1", "FAIL", "메뉴 구조가 업체 노드 안에 인라인으로만 있고 독립 Menu 노드가 없습니다",
              detail="MenuItem 은 이미 있으므로 백지가 아닙니다. Menu(@id) 로 승격하고 Restaurant.hasMenu 가 그 @id 를 가리키게 하며 offers.price 를 채우는 일만 남았습니다.",
              evidence=menu_ev + menu_like[:3], fix="Menu(@id) → hasMenuSection → hasMenuItem(offers.price, priceCurrency KRW); Restaurant.hasMenu = {@id}", refs=["W-menu"], weight=1.5)
    elif menu_like and not menu_nodes:
        F.add("S-S-menu", "S", "schema", "P1", "FAIL", "메뉴/서비스 페이지가 있는데 Menu/OfferCatalog 구조화가 없습니다", evidence=menu_ev + menu_like[:3],
              fix="Menu(@id) → hasMenuSection → hasMenuItem(offers.price) / 서비스는 OfferCatalog", refs=["W-menu"], weight=2)
    elif menu_nodes:
        F.ok("S-S-menu", "S", "schema", "메뉴 구조화 존재", evidence=menu_ev, refs=["W-menu"])
    bc = sum(1 for pg in pages.values() if (pg.get("jsonld") or {}).get("breadcrumb"))
    if n > 3 and bc == 0:
        F.add("S-S-breadcrumb", "S", "schema", "P2", "FAIL", "BreadcrumbList 없음", weight=0.5)
    sa = ent.get("same_as") or []
    if sa:
        bad_sa = [s for s in sa if "search" in s or "빠른길찾기" in s or "?query=" in s]
        if bad_sa:
            F.add("S-S-sameas", "S", "schema", "P2", "FAIL", "sameAs 에 프로필이 아닌 검색 URL 이 있습니다", evidence=bad_sa[:3], fix="네이버 플레이스 프로필·인스타 등 공식 프로필 URL 로", weight=1)
        else:
            F.ok("S-S-sameas", "S", "schema", "sameAs 공식 프로필 연결", evidence=sa[:5], refs=["K22", "G-entity"])
    else:
        F.add("S-S-sameas", "S", "schema", "P1", "FAIL", "sameAs 가 없습니다 (플레이스·인스타·GBP 연결 없음)", fix="관리 가능한 공식 프로필만 sameAs 에", refs=["K22", "G-entity"], weight=2)

    # --- 이미지 (CS-images)
    tot = sum((pg.get("imgs") or {}).get("total", 0) for pg in pages.values())
    miss_alt = sum((pg.get("imgs") or {}).get("missing_alt", 0) for pg in pages.values())
    if tot:
        if miss_alt:
            sev = "P1" if pct(miss_alt, tot) > 20 else "P2"
            F.add("S-I-alt", "S", "images", sev, "FAIL", f"alt 없는 이미지 {miss_alt}/{tot} ({pct(miss_alt, tot)}%)",
                  evidence=[f"{p}: {(pg.get('imgs') or {}).get('missing_alt')}/{(pg.get('imgs') or {}).get('total')}" for p, pg in pages.items() if (pg.get('imgs') or {}).get('missing_alt')][:6],
                  fix="장식 이미지는 alt=\"\" + aria-hidden, 나머지는 내용 서술", refs=["K04", "K73"], weight=3 if sev == "P1" else 1)
        else:
            F.ok("S-I-alt", "S", "images", "이미지 alt 전수 입력", evidence=[f"{tot}장"], refs=["K73"])
        modern = sum((pg.get("imgs") or {}).get("modern_format", 0) for pg in pages.values())
        if tot >= 5 and pct(modern, tot) < 30:
            F.add("S-I-format", "S", "images", "P2", "FAIL", f"WebP/AVIF 비율 {pct(modern, tot)}%", detail="src 확장자 기준. picture/srcset 으로 제공하면 오탐일 수 있음.", fix="WebP 변환 + width/height 지정", refs=["K74"], weight=1)
        dims = sum((pg.get("imgs") or {}).get("with_dims", 0) for pg in pages.values())
        if tot >= 5 and pct(dims, tot) < 50:
            # 실측 CLS 가 0 에 가깝거나 Next/Image(fill) 를 쓰면 width/height 부재는 실효가 없다 → 참고
            home_cls = None
            if r and not r.get("error"):
                rp0 = (r.get("pages") or {}).get("/") or next(iter((r.get("pages") or {}).values()), {})
                home_cls = (rp0.get("mobile_4x") or {}).get("cls")
            uses_nimg = any("data-nimg" in (open(os.path.join(c.get("site_dir") if c.get("mode") == "local" else c.get("_out", "."), pg.get("raw_path", "")), encoding="utf-8", errors="replace").read(200_000) if os.path.exists(os.path.join(c.get("site_dir") if c.get("mode") == "local" else c.get("_out", "."), pg.get("raw_path", ""))) else "") for pg in list(pages.values())[:3])
            if (home_cls is not None and home_cls <= 0.02) or uses_nimg:
                F.add("S-I-dims", "S", "images", "INFO", "PASS", f"width/height 속성 있는 이미지 {pct(dims, tot)}% (실측 CLS {home_cls}{' · Next/Image' if uses_nimg else ''} 라 실효 없음)", weight=0)
            else:
                F.add("S-I-dims", "S", "images", "P2", "FAIL", f"width/height 속성 있는 이미지 {pct(dims, tot)}%", detail="CLS 예방. CSS aspect-ratio 로 잡았다면 오탐.", evidence=[f"실측 CLS {home_cls}"], weight=0.5)
    else:
        F.add("S-I-none", "S", "images", "P2", "FAIL", "이미지가 하나도 없습니다 (배경 CSS 만 사용?)", refs=["K74"], weight=1)

    # --- 성능 (CS-performance) · render.json
    if r and not r.get("error"):
        home = (r.get("pages") or {}).get("/") or next(iter((r.get("pages") or {}).values()), {})
        m = home.get("mobile_4x") or {}
        d = home.get("desktop") or {}
        if m and not m.get("error"):
            lcp, cls, tbt = m.get("lcp") or 0, m.get("cls") or 0, m.get("tbt") or 0
            mb = (m.get("total_bytes") or 0) / 1048576
            ev = [f"모바일 4× · LCP {round(lcp)} ms · CLS {cls} · TBT {round(tbt)} ms · 요청 {m.get('requests')} · {mb:.2f} MB · DOM {m.get('dom')}",
                  f"데스크톱 · LCP {round(d.get('lcp') or 0)} ms · CLS {d.get('cls')} · TBT {round(d.get('tbt') or 0)} ms · {((d.get('total_bytes') or 0)/1048576):.2f} MB"]
            if lcp > 4000:
                F.add("S-P-lcp", "S", "performance", "P0", "FAIL", f"모바일 LCP {round(lcp)} ms (기준 2,500)", evidence=ev, fix="히어로 이미지 경량화·fetchpriority=high·영상 지연 로드", refs=["K74"], weight=5)
            elif lcp > 2500:
                F.add("S-P-lcp", "S", "performance", "P1", "FAIL", f"모바일 LCP {round(lcp)} ms (기준 2,500)", evidence=ev, fix="히어로 자원 우선순위·이미지 경량화", refs=["K74"], weight=3)
            else:
                F.ok("S-P-lcp", "S", "performance", f"모바일 LCP {round(lcp)} ms 통과", evidence=ev)
            if cls > 0.25:
                F.add("S-P-cls", "S", "performance", "P1", "FAIL", f"CLS {cls} (기준 0.1)", fix="이미지 width/height, 폰트 swap, 광고/배너 자리 예약", weight=3)
            elif cls > 0.1:
                F.add("S-P-cls", "S", "performance", "P2", "FAIL", f"CLS {cls} (기준 0.1)", weight=1.5)
            if tbt > 600:
                F.add("S-P-tbt", "S", "performance", "P1", "FAIL", f"모바일 TBT {round(tbt)} ms (기준 300)", evidence=[f"롱태스크 {m.get('longtasks')}개", f"스크립트 {m.get('scripts_net', {}).get('n')}개 {round((m.get('scripts_net', {}).get('bytes') or 0)/1024)} KB"], fix="JS 분할·지연, 서드파티 제거", weight=3)
            elif tbt > 300:
                F.add("S-P-tbt", "S", "performance", "P2", "FAIL", f"모바일 TBT {round(tbt)} ms (기준 300)", weight=1.5)
            scroll_mb = (m.get("scroll_added_bytes") or 0) / 1048576
            scroll_types = {k: round(v / 1048576, 2) for k, v in (m.get("scroll_added_by_type") or {}).items()}
            ev_bytes = [f"초기 로드 {mb:.2f} MB · 스크롤 후 +{scroll_mb:.2f} MB (스크롤 추가분 유형별 {scroll_types})"]
            if mb > 5:
                F.add("S-P-bytes", "S", "performance", "P1", "FAIL", f"모바일 초기 전송량 {mb:.1f} MB (권장 < 2 MB)", evidence=ev_bytes, fix="영상·이미지 게이팅, 폰트 서브셋", refs=["K74"], weight=3)
            elif mb > 2:
                F.add("S-P-bytes", "S", "performance", "P2", "FAIL", f"모바일 초기 전송량 {mb:.1f} MB (권장 < 2 MB)", evidence=ev_bytes, refs=["K74"], weight=1.5)
            if scroll_mb > 5:
                vid = ((m.get("scroll_added_by_type") or {}).get("video") or 0) / 1048576
                F.add("S-P-scroll", "S", "performance", "P2", "FAIL", f"끝까지 스크롤하면 {scroll_mb:.1f} MB 가 더 내려옵니다" + (f" (영상 {vid:.1f} MB)" if vid > 1 else ""),
                      detail="첫 화면 지표에는 안 잡히지만 데이터 요금과 스크롤 중 버벅임에 영향. 영상은 클릭 시 재생, 이미지는 표시 크기로.", evidence=ev_bytes, fix="영상 클릭 시 마운트 · 이미지 srcset", refs=["K74"], weight=1)
            fo = m.get("fonts") or {}
            if (fo.get("bytes") or 0) > 400 * 1024:
                F.add("S-P-fonts", "S", "performance", "P1", "FAIL", f"폰트 {fo.get('n')}파일 {round(fo['bytes']/1024)} KB", evidence=[f"{x['kb']} KB {x['file']}" for x in fo.get("top", [])[:4]],
                      fix="실사용 글리프 서브셋(pyftsubset) · 굵기당 1파일 · font-display: swap", weight=2.5)
            elif (fo.get("n") or 0) > 8:
                F.add("S-P-fonts-n", "S", "performance", "P2", "FAIL", f"폰트 파일 {fo.get('n')}개", fix="굵기 통합", weight=1)
            if (m.get("duplicate_requests") or 0) > 3:
                F.add("S-P-dup", "S", "performance", "P2", "FAIL", f"같은 URL 중복 요청 {m['duplicate_requests']}건", weight=1)
            if (m.get("dom") or 0) > 1500:
                F.add("S-P-dom", "S", "performance", "P2", "FAIL", f"DOM 노드 {m['dom']} (기준 1,500)", weight=1)
            if m.get("console_errors"):
                F.add("S-P-console", "S", "performance", "P2", "FAIL", f"콘솔 오류 {m['console_errors']}건", evidence=[x["text"][:100] for x in (m.get("console") or []) if x["type"] in ("error", "pageerror")][:4], weight=1)
            if m.get("failed_requests"):
                F.add("S-P-failed", "S", "performance", "P2", "FAIL", f"실패 요청 {len(m['failed_requests'])}건", evidence=[f"{x['error']} {x['url'][:80]}" for x in m["failed_requests"][:4]], weight=1)
            if (m.get("oversizedImgs") or 0) >= 3:
                F.add("S-I-oversized", "S", "images", "P2", "FAIL", f"표시 크기의 2배 이상 원본 이미지 {m['oversizedImgs']}장", evidence=m.get("oversizedSamples", [])[:4], fix="srcset/sizes 로 뷰포트별 제공", refs=["K74"], weight=1)
            # 홈 외에 렌더한 페이지도 LCP·초기 전송량을 본다 (메뉴 페이지가 홈보다 무거운 사이트가 있다)
            for op, oe in (r.get("pages") or {}).items():
                om = oe.get("mobile_4x") or {}
                if op == "/" or not om or om.get("error"):
                    continue
                olcp = om.get("lcp") or 0
                omb = (om.get("total_bytes") or 0) / 1048576
                if olcp > 2500 or omb > 3:
                    F.add(f"S-P-page-{op}", "S", "performance", "P2", "FAIL", f"{op}: 모바일 LCP {round(olcp)} ms · 초기 전송량 {omb:.1f} MB",
                          evidence=[f"이미지 {(om.get('by_type') or {}).get('image', {}).get('n', 0)}장 {((om.get('by_type') or {}).get('image', {}).get('bytes', 0))//1024} KB", f"스크롤 후 +{(om.get('scroll_added_bytes') or 0)/1048576:.1f} MB"],
                          fix="첫 화면 이미지 1장만 즉시, 나머지 lazy·srcset", weight=1)
        else:
            F.hold("S-P-render", "S", "performance", "모바일 렌더 측정 실패", evidence=[str(m.get("error"))[:200]])
    else:
        F.hold("S-P-render", "S", "performance", "Chromium 실측이 없습니다 (render.json 없음/오류)", evidence=[str((r or {}).get("error"))[:200]])


# ---------------------------------------------------------------- G: GEO/AEO
def check_geo(F: Findings, c: dict, r: dict | None, facts: dict | None, brand: str | None, region: str | None, category: str | None):
    pages = html_pages(c)
    n = len(pages)
    rb = c.get("robots") or {}
    bots = rb.get("bots") or {}

    # --- 크롤러 접근 (G-access)
    if rb.get("status") == 200:
        blocked = [b for b, v in bots.items() if v.get("verdict") == "disallow"]
        ai_blocked = [b for b in blocked if bots[b]["group"] in ("ai_train", "ai_search")]
        search_blocked = [b for b in blocked if bots[b]["group"] in ("search", "kr")]
        if search_blocked:
            F.add("G-A-search", "G", "access", "P0", "FAIL", f"검색 크롤러 차단: {', '.join(search_blocked)}", fix="Disallow 제거", refs=["NEO-2"], weight=8)
        if ai_blocked:
            F.add("G-A-ai", "G", "access", "P1", "FAIL", f"AI 크롤러 {len(ai_blocked)}종 차단: {', '.join(ai_blocked[:8])}",
                  detail="학습용(GPTBot·ClaudeBot·Google-Extended·CCBot)과 검색·인용용(OAI-SearchBot·ChatGPT-User·Claude-User·PerplexityBot)을 구분해 정책을 정하되, GEO 목적이면 검색·인용용은 반드시 허용.",
                  evidence=[f"{b} ({bots[b]['group']})" for b in ai_blocked][:10], fix="AI 크롤러 Allow (Cloudflare AI Crawl Control 의 관리형 robots 도 확인)", refs=["K72"], weight=4)
        else:
            F.ok("G-A-ai", "G", "access", "AI 크롤러 차단 없음", evidence=[f"{len([b for b, v in bots.items() if v['group'] in ('ai_train', 'ai_search')])}종 확인 · 전부 allow/미지정"], refs=["K72"])
        if rb.get("cloudflare_managed"):
            F.add("G-A-cf", "G", "access", "P1", "FAIL", "Cloudflare 관리형 robots.txt 블록이 붙어 있습니다", detail="CF AI Crawl Control 이 AI 봇을 자동 차단하는 경우가 많음.", fix="CF 대시보드 → AI Crawl Control → robots.txt 관리 해제", weight=3)
        if bots.get("Yeti", {}).get("verdict") == "disallow":
            F.add("G-A-yeti", "G", "access", "P0", "FAIL", "네이버 Yeti 차단", refs=["NEO-2"], weight=8)
        else:
            F.ok("G-A-yeti", "G", "access", "네이버 Yeti 허용", refs=["NEO-2"])
    else:
        F.hold("G-A-robots", "G", "access", "robots.txt 없음 · 크롤러 정책 미정", refs=["K72"])
    # SSR: 원본 HTML 에 H1·본문이 있는가 (AI 크롤러는 JS 를 실행하지 않는다)
    home = pages.get("/") or (next(iter(pages.values()), {}) if pages else {})
    # 루트가 언어 선택·리다이렉트용 얇은 페이지면 대표 페이지를 고른다:
    # 1순위 언어 루트(/ko/, /en/ …) 중 본문 ≥300자 (한국어 우선), 2순위 본문이 가장 긴 페이지
    if (home.get("body_chars") or 0) < 300 and pages:
        lang_roots = [(p, pg) for p, pg in pages.items() if re.fullmatch(r"/[a-z]{2}(-[A-Za-z]+)?/?", p) and (pg.get("body_chars") or 0) >= 300]
        lang_roots.sort(key=lambda kv: (0 if kv[0].startswith("/ko") else 1, -(kv[1].get("body_chars") or 0)))
        if lang_roots:
            main_path, home = lang_roots[0]
        else:
            main_path, home = max(pages.items(), key=lambda kv: kv[1].get("body_chars") or 0)
        if (pages.get("/") or {}).get("h1"):
            F.add("G-A-root", "G", "access", "INFO", "PASS", f"루트(/)는 {(pages.get('/') or {}).get('body_chars', 0)}자 짜리 진입 페이지. 대표 페이지로 {main_path} 를 봅니다", weight=0)
    raw_chars = home.get("body_chars") or 0
    rend = None
    if r and not r.get("error"):
        rp = (r.get("pages") or {}).get("/") or next(iter((r.get("pages") or {}).values()), {})
        rend = ((rp.get("mobile_4x") or {}).get("renderedBodyChars"))
    if home.get("h1") and raw_chars >= 300:
        ev = [f"원본 HTML H1: {home['h1'][0][:60]}", f"원본 본문 {raw_chars:,}자" + (f" / 렌더 후 {rend:,}자" if rend else "")]
        if rend and rend > raw_chars * 3 and rend - raw_chars > 1500:
            F.add("G-A-ssr", "G", "access", "P1", "FAIL", "본문 대부분이 JS 렌더 후에만 나타납니다 (CSR 의존)", evidence=ev, fix="SSR/프리렌더로 핵심 본문을 원본 HTML 에", refs=["K18", "NEO-3"], weight=4)
        else:
            F.ok("G-A-ssr", "G", "access", "원본 HTML 에 H1·본문 존재 (크롤러 눈으로 읽힘)", evidence=ev, refs=["K18"])
            if rend is not None and raw_chars >= 1000 and rend < raw_chars * 0.2:
                F.add("G-A-hidden", "G", "access", "P2", "FAIL", "화면에 보이는 본문이 원본의 20% 미만입니다 (인트로·오버레이·접힘)",
                      detail="크롤러는 읽지만 방문자는 클릭·스크롤 전까지 못 보는 구조입니다. 첫 화면에 핵심 사실이 보이는지 스크린샷으로 확인.", evidence=ev, weight=1)
    elif pages:
        F.add("G-A-ssr", "G", "access", "P0", "FAIL", "원본 HTML 에 H1 또는 본문이 없습니다 (JS 없이 빈 페이지)", evidence=[f"H1 {home.get('h1')}, 본문 {raw_chars}자"], fix="SSR/프리렌더", refs=["K18", "NEO-3"], weight=8)
    llms = (c.get("llms") or {}).get("/llms.txt") or {}
    if llms.get("status") == 200 and not llms.get("is_html"):
        ev = [f"{llms.get('bytes')} B", "가격 포함" if llms.get("has_prices") else "가격 없음"]
        F.ok("G-A-llms", "G", "access", "llms.txt 존재", detail="구글은 llms.txt 를 랭킹에 쓰지 않는다고 밝혔고(2026-05), 주요 AI 크롤러도 아직 소비하지 않습니다. 있으면 좋은 정도이며 인용 지렛대는 아닙니다.", evidence=ev, refs=["CS-geo-llms"])
        if not llms.get("has_prices") and any(re.search(r"menu|메뉴|price", p) for p in pages):
            F.add("G-A-llms-price", "G", "access", "P2", "FAIL", "llms.txt 에 가격이 없습니다", fix="메뉴·가격을 llms.txt 에도", weight=0.5)
        alt = sum(1 for pg in pages.values() if pg.get("rel_alternate_llms"))
        if not alt:
            F.add("G-A-llms-link", "G", "access", "P2", "FAIL", "HTML 에 llms.txt rel=alternate 선언 없음", fix='<link rel="alternate" type="text/plain" href="/llms.txt">', weight=0.3)
    else:
        F.add("G-A-llms", "G", "access", "P2", "FAIL", "llms.txt 없음", detail="선택 항목. 매장 사실(주소·시간·메뉴·가격·FAQ)을 텍스트로 정리한 파일. 랭킹 효과는 입증되지 않았으나 비용이 낮음.", fix="/llms.txt 생성 (라이브 사실만)", refs=["CS-geo-llms"], weight=1)

    # --- 기계가독·엔티티 (G-entity)
    ent = c.get("entity") or {}
    names = ent.get("names") or []
    if brand:
        variants = [x for x in names if x and brand not in x and x not in brand]
        if variants and len(names) > 1 and not (c.get("languages") and len(c["languages"]) > 1):
            F.add("G-E-name", "G", "entity", "P1", "FAIL", "상호 표기가 하나로 통일돼 있지 않습니다", evidence=names[:5], fix="모든 매체 동일 표기 (약칭·변형 금지)", refs=["K01", "G-NAP", "W-name"], weight=3)
    sp, tp = set(ent.get("schema_phones") or []), set(ent.get("text_phones") or [])
    norm = lambda s: s[-8:]  # 국번 이후 8자리로 비교 (+82/0 차이 흡수)
    service_area = bool(c.get("_service_area"))
    if service_area and not sp and not tp:
        F.hold("G-E-phone", "G", "entity", "전화번호 미공개 · 서비스 지역형 사업이라 보류 (이메일·DM 등 연락 경로 확인)")
    elif sp and tp:
        if {norm(x) for x in sp} & {norm(x) for x in tp}:
            F.ok("G-E-phone", "G", "entity", "전화번호: 스키마와 화면 일치", evidence=[f"스키마 {sorted(sp)} / 화면 {sorted(tp)}"], refs=["K01", "G-NAP"])
        else:
            F.add("G-E-phone", "G", "entity", "P0", "FAIL", "전화번호가 스키마와 화면에서 다릅니다", evidence=[f"스키마 {sorted(sp)}", f"화면 {sorted(tp)}"], fix="대표번호 하나로 통일", refs=["K01", "G-NAP"], weight=5)
    elif not sp and not tp:
        F.add("G-E-phone", "G", "entity", "P1", "FAIL", "전화번호가 화면에도 스키마에도 없습니다", fix="tel: 링크 + telephone 필드", refs=["K01"], weight=3)
    elif not sp:
        F.add("G-E-phone", "G", "entity", "P1", "FAIL", "전화번호가 스키마에 없습니다", evidence=[f"화면 {sorted(tp)}"], refs=["K01"], weight=2)
    sa, ta = ent.get("schema_addresses") or [], ent.get("text_addresses") or []
    if sa and ta:
        key = lambda s: re.sub(r"\s", "", s)
        hit = any(key(a)[-6:] in key(t) or key(t)[-6:] in key(a) for a in sa for t in ta)
        if hit:
            F.ok("G-E-addr", "G", "entity", "주소: 스키마와 화면 일치", evidence=[f"스키마 {sa[:2]}", f"화면 {ta[:2]}"], refs=["K01", "G-NAP"])
        else:
            F.add("G-E-addr", "G", "entity", "P1", "HOLD", "주소 표기가 스키마와 화면에서 달라 보입니다 · 확인 필요", evidence=[f"스키마 {sa[:2]}", f"화면 {ta[:2]}"], refs=["K01"], weight=2)
    elif not sa and service_area:
        F.hold("G-E-addr", "G", "entity", "주소 미공개 · 서비스 지역형(areaServed) 사업이라 보류")
    elif not sa:
        F.add("G-E-addr", "G", "entity", "P1", "FAIL", "주소가 스키마에 없습니다", refs=["K01", "G-schema-min"], weight=3)
    if facts:
        fp = re.sub(r"[^\d]", "", str(facts.get("phone") or ""))
        fa = str(facts.get("address") or "")
        if fp and sp and fp[-8:] not in {norm(x) for x in sp}:
            F.add("G-E-facts-phone", "G", "entity", "P0", "FAIL", "사실 기준표의 전화와 사이트 스키마 전화가 다릅니다", evidence=[f"facts {fp} / schema {sorted(sp)}"], weight=5)
        if fa and sa and not any(re.sub(r"\s", "", fa)[-6:] in re.sub(r"\s", "", a) for a in sa):
            F.add("G-E-facts-addr", "G", "entity", "P0", "FAIL", "사실 기준표의 주소와 사이트 스키마 주소가 다릅니다", evidence=[f"facts {fa} / schema {sa[:2]}"], weight=5)
        if fp and fa and not any(x["id"].startswith("G-E-facts") for x in F.items):
            F.ok("G-E-facts", "G", "entity", "사실 기준표(NAP)와 사이트 스키마 일치", evidence=[f"phone·address 대조"], refs=["G-NAP"])
    else:
        F.hold("G-E-external", "G", "entity", "외부 플랫폼(네이버·구글·카카오) NAP 일치는 이번 범위에서 확인하지 않았습니다",
               detail="facts.json 을 주면 사이트 ↔ 기준표를 대조합니다. 플레이스 대조는 /thejsk 로.", refs=["K01", "G-NAP", "G-self-1"])
    if not names:
        F.add("G-E-entity", "G", "entity", "P1", "FAIL", "업체 엔티티 이름이 구조화돼 있지 않습니다", refs=["K22", "G-entity"], weight=3)
    hours = any(((pg.get("jsonld") or {}).get("business") or [{}])[0].get("openingHoursSpecification") for pg in pages.values() if (pg.get("jsonld") or {}).get("business"))
    if hours:
        F.ok("G-E-hours", "G", "entity", "영업시간 구조화", refs=["K08", "G-self-3"])
    elif service_area:
        F.hold("G-E-hours", "G", "entity", "영업시간 미공개 · 서비스 지역형 사업이라 보류")
    else:
        F.add("G-E-hours", "G", "entity", "P1", "FAIL", "영업시간이 구조화돼 있지 않습니다", fix="openingHoursSpecification (휴무·브레이크·라스트오더)", refs=["K08", "G-schema-min"], weight=3)
    geo = any(((pg.get("jsonld") or {}).get("business") or [{}])[0].get("geo") for pg in pages.values() if (pg.get("jsonld") or {}).get("business"))
    if not geo:
        F.add("G-E-geo", "G", "entity", "P2", "FAIL", "위도·경도(geo) 없음", fix="GeoCoordinates", refs=["K25"], weight=1)
    if c.get("hreflang_pages") and len(c.get("languages") or []) > 1:
        F.ok("G-E-multilang", "G", "entity", "언어별 인용 출처(다국어판) 존재", evidence=[f"lang={c.get('languages')}"], refs=["K63"])


    # --- AI 봇 실제 접근 (robots.txt 선언이 아니라 응답으로 확인) ---
    ab = c.get("ai_bot_probe") or {}
    if ab and not ab.get("error") and ab.get("bots"):
        blocked, thin = ab.get("blocked") or [], ab.get("thin") or []
        base = ab.get("baseline") or {}
        if blocked:
            F.add("G-A-cloak", "G", "access", "P0", "FAIL", f"AI 검색 봇 {len(blocked)}종이 홈에서 막혔습니다",
                  detail="robots.txt 가 허용해도 CDN·WAF 가 엣지에서 되돌려보내면 결과는 차단입니다. 실제로 그 봇의 User-Agent 로 홈을 요청해 본 값입니다. "
                         "OpenAI 는 공식 문서에서 호스팅·CDN 이 자사 공개 IP 트래픽을 막지 않아야 한다고 명시하고 있습니다.",
                  evidence=[f"{k}: {v.get('status') or v.get('error')}" for k, v in ab["bots"].items() if k in blocked][:6]
                           + [f"사람 UA 기준 {base.get('status')} / {base.get('bytes', 0):,} bytes"],
                  fix="Cloudflare 는 AI Crawl Control·봇 차단 규칙, 그 외 WAF 는 UA 규칙에서 해당 봇을 허용합니다.", weight=6)
        elif thin:
            F.add("G-A-cloak", "G", "access", "P1", "FAIL", f"AI 봇에게 본문이 절반 이하로 오는 페이지가 있습니다 ({len(thin)}종)",
                  detail="상태코드는 200 이지만 사람이 받는 것보다 본문이 크게 적습니다. 봇 전용 축약 페이지를 주고 있을 수 있습니다.",
                  evidence=[f"{k}: {ab['bots'][k].get('bytes', 0):,} bytes (사람 대비 {ab['bots'][k].get('ratio')})" for k in thin][:6],
                  fix="봇과 사람에게 같은 HTML 을 줍니다.", weight=3)
        else:
            F.ok("G-A-cloak", "G", "access", f"AI 검색 봇 {len(ab['bots'])}종 실제 접근 정상",
                 evidence=[f"{k} {v.get('status')} / {v.get('bytes', 0):,}B" for k, v in list(ab["bots"].items())[:4]]
                          + [f"사람 UA {base.get('status')} / {base.get('bytes', 0):,}B"])

    # --- 인용가능성 (G-cite)
    qh = sum(len(pg.get("question_headings") or []) for pg in pages.values())
    h2 = sum((pg.get("heading_counts") or {}).get("h2", 0) + (pg.get("heading_counts") or {}).get("h3", 0) for pg in pages.values())
    if qh == 0 and not any((pg.get("jsonld") or {}).get("faq_questions") for pg in pages.values()):
        F.add("G-C-question", "G", "citability", "P1", "FAIL", "질문형 소제목이 하나도 없습니다",
              detail="소제목은 기계가 본문을 덩어리로 끊어 읽는 경계입니다. 다만 질문·답변 꼴로 쓰는 것 자체가 인용을 만들지는 않습니다 "
                     "(통제 실험에서 Q&A 포맷 단독은 오히려 -5.74%). 효과는 그 소제목 아래에 숫자·정의·비교·절차 같은 증거가 있을 때 납니다. "
                     "아래 증거 밀도 항목과 함께 보세요.",
              evidence=[f"h2/h3 {h2}개 중 질문형 0"], fix='"어떻게 …?", "얼마 …?", "주차 되나요?" 형 소제목 + 첫 문장에 직접 답', refs=["K29", "K84", "G-QA"], weight=3)
    else:
        F.ok("G-C-question", "G", "citability", "질문형 소제목/FAQ 존재", evidence=[f"질문형 소제목 {qh}개, FAQ 스키마 {sum(len((pg.get('jsonld') or {}).get('faq_questions') or []) for pg in pages.values())}문항"], refs=["K29", "G-QA"])

    # --- 증거 밀도 (참고) --------------------------------------------------
    # 근거: KDD 2024 GEO (인용문 +41% · 통계 +32% · 출처 +28% · 키워드 도배는 역효과),
    #       arXiv 2604.25707 (코드 +77% · 통계 +62% · 정의 +57% · 비교 +55% · 절차 +41% · Q&A 포맷 단독 -5.74%,
    #       영향력 상위 사분위 페이지의 헤딩 밀도가 하위의 12.5배).
    # 도입 규율상 새 신호는 참고로 먼저 넣는다. 점수 가중치 0.
    chars = sum(pg.get("body_chars") or 0 for pg in pages.values())
    kilo = (chars / 1000) or 1
    n_stat = n_def = n_step = n_table = n_quote = 0
    auth_domains, quote_pages = set(), []
    for q, pg in pages.items():
        h = raw_html(c, pg)
        t = visible_text(h)
        n_stat += len(STAT_RE.findall(t))
        n_def += len(DEF_RE.findall(t))
        n_step += len(re.findall(r"<ol[\s>]", h, re.I)) + len(STEP_RE.findall(t))
        n_table += len(re.findall(r"<table[\s>]", h, re.I))
        bq = len(re.findall(r"<blockquote[\s>]", h, re.I))
        n_quote += bq
        if bq:
            quote_pages.append(f"{q} ({bq})")
        for href in set(re.findall(r"href=[\"'](https?://[^\"'#]+)", h, re.I)):
            m = re.match(r"https?://([^/]+)", href)
            if not m:
                continue
            dom = m.group(1).lower().split(":")[0]
            if c.get("host", "") in dom or SOCIAL_RE.search(href):
                continue
            if AUTHORITY_RE.search(dom):
                auth_domains.add(dom)

    heads = sum(sum((pg.get("heading_counts") or {}).get(x, 0) for x in ("h2", "h3", "h4")) for pg in pages.values())
    hd = heads / kilo
    sd = n_stat / kilo
    genres = [g for g, v in (("정의", n_def), ("비교표", n_table), ("절차", n_step), ("통계", n_stat), ("인용", n_quote)) if v]

    F.add("G-C-evidence", "G", "citability", "INFO", "PASS" if len(genres) >= 3 else "FAIL",
          f"증거 장르 {len(genres)}/5종 사용 ({', '.join(genres) if genres else '없음'})",
          detail="AI 답변에 문장이 실제로 흡수되는지를 가르는 것은 말투나 서식이 아니라 증거 밀도였습니다. "
                 "정의문·비교표·절차·수치·인용이 들어간 문단이 답변에 들어갑니다. 없는 사실을 지어 넣으라는 뜻이 아니라, "
                 "이미 알고 있는 것을 숫자와 순서로 적으라는 뜻입니다.",
          evidence=[f"정의문 {n_def} · 비교표 {n_table} · 절차 {n_step} · 수치 {n_stat} · 인용 {n_quote}",
                    f"본문 {chars:,}자 기준"],
          fix="가장 손쉬운 순서: 가격·시간·거리·인원을 숫자로 적기 → 비교가 필요한 것은 표로 → 방법은 번호 매긴 순서로.",
          weight=0)

    band = "낮음" if sd < 3 else ("보통" if sd < 6 else "높음")
    F.add("G-C-stats", "G", "citability", "INFO", "PASS" if sd >= 3 else "FAIL",
          f"본문 1,000자당 수치 {sd:.1f}개 ({band})",
          detail="통제 실험에서 통계·수치를 더한 문단은 답변 영향력이 뚜렷하게 올랐습니다. 이 값은 절대 기준이 아니라 같은 사이트의 전후 비교용입니다.",
          evidence=[f"수치 표현 {n_stat}개 / 본문 {chars:,}자", "열 개 사이트 실측 범위 2.0~9.9"], weight=0)

    F.add("G-C-heading", "G", "citability", "INFO", "PASS" if hd >= 3 else "FAIL",
          f"본문 1,000자당 소제목 {hd:.2f}개",
          detail="영향력 상위 사분위 페이지의 헤딩 밀도가 하위의 12.5배였습니다. 검색용이 아니라 기계가 본문을 끊어 읽게 하는 경계입니다. "
                 "긴 글일수록 소제목 없이는 통째로 건너뜁니다.",
          evidence=[f"h2~h4 {heads}개 / 본문 {chars:,}자", "열 개 사이트 실측 범위 1.00~8.40"],
          fix="한 화면에 소제목이 하나는 보이도록. 소제목 바로 아래 첫 문장이 그 소제목에 대한 답이 되게 씁니다.", weight=0)

    if n_quote:
        F.ok("G-C-quote", "G", "citability", f"인용 블록 {n_quote}개", evidence=quote_pages[:5])
    else:
        F.add("G-C-quote", "G", "citability", "INFO", "FAIL", "인용 블록(blockquote)이 없습니다",
              detail="GEO 연구에서 인용문 추가가 가장 큰 폭(+41%)의 개선을 냈습니다. 손님 후기, 원장·셰프의 말, 자료 문장을 인용 형식으로 두는 것만으로 해당합니다. "
                     "없는 후기를 지어내라는 뜻이 아닙니다.",
              evidence=["blockquote 0개"], fix="실제로 받은 후기·인터뷰 한두 개를 <blockquote> 로 표시", weight=0)

    informational = n_def >= 5 and chars >= 20000   # 정보성 본문이 실제로 있을 때만 출처 부재를 지적한다
    if auth_domains:
        F.ok("G-C-source", "G", "citability", f"외부 권위 출처 링크 {len(auth_domains)}곳", evidence=sorted(auth_domains)[:6])
    elif not informational:
        F.ok("G-C-source", "G", "citability", "외부 출처 인용이 필요한 유형이 아닙니다",
             evidence=[f"정의문 {n_def}개 · 본문 {chars:,}자 — 정보성 글이 적어 해당 없음으로 봅니다"])
    else:
        F.add("G-C-source", "G", "citability", "INFO", "FAIL", "외부 권위 출처로의 링크가 없습니다",
              detail="공공기관·학술·사전 같은 확인 가능한 출처를 인용하면 답변에 반영될 확률이 올랐습니다(+28%). "
                     "다만 이 신호는 정보성 글에서 나온 것이라, 매장 소개 위주 사이트에는 억지로 넣을 일이 아닙니다. 해당하는 내용이 있을 때만 답니다.",
              evidence=["소셜·자사 채널을 뺀 .go.kr/.or.kr/.ac.kr/.gov/.edu/위키 링크 0건"],
              fix="근거를 대는 문장이 있다면 그 출처로 링크합니다 (예: 식약처 고시, 표준 규격, 통계 자료).", weight=0)

    tables = sum(pg.get("tables", 0) for pg in pages.values())
    lists = sum(pg.get("lists", 0) for pg in pages.values())
    if tables == 0 and lists < 3:
        F.add("G-C-structure", "G", "citability", "P2", "FAIL", "표·리스트가 거의 없습니다 (문단 위주)", evidence=[f"table {tables}, ul/ol {lists}"], fix="가격표·비교표·체크리스트를 표/리스트로", refs=["K75", "G-table"], weight=2)
    else:
        F.ok("G-C-structure", "G", "citability", "표·리스트 구조 사용", evidence=[f"table {tables}, ul/ol {lists}"], refs=["K75"])
    longp = sum(pg.get("long_paragraphs", 0) for pg in pages.values())
    if longp >= 3:
        F.add("G-C-paragraph", "G", "citability", "P2", "FAIL", f"400자 넘는 긴 문단 {longp}개", fix="2~4문장 단위로 나누고 소제목", refs=["G-structure"], weight=1)
    fp = home.get("first_paragraph") or ""
    if fp and (brand or region or category):
        keys = [k for k in (brand, region, category) if k]
        hit = [k for k in keys if k and k.lower() in fp.lower()]
        if len(hit) >= 2:
            F.ok("G-C-lead", "G", "citability", "첫 문단에 상호·지역·업종이 들어 있습니다", evidence=[fp[:100]], refs=["K02", "K29"])
        else:
            F.add("G-C-lead", "G", "citability", "P2", "FAIL", "홈 첫 문단에 상호·지역·업종 조합이 없습니다", evidence=[fp[:100], f"확인 키워드 {keys}"], fix="첫 문단 = 지역 + 업종 + 핵심 가치 한 문장", refs=["K02", "K29"], weight=1.5)
    if r and not r.get("error"):
        rp = (r.get("pages") or {}).get("/") or next(iter((r.get("pages") or {}).values()), {})
        m = rp.get("mobile_4x") or {}
        fh = m.get("firstHeading") or {}
        fold = " ".join(m.get("foldText") or [])
        keys = [k for k in (brand, region, category) if k]
        if keys:
            hit = [k for k in keys if k.lower() in (fold + " " + (fh.get("text") or "")).lower()]
            if len(hit) >= 2:
                F.ok("G-N-fold", "G", "neo", "모바일 첫 화면에 상호·지역·업종이 보입니다", evidence=[f"첫 가시 헤딩: {fh.get('text')}", f"키워드 {hit}"], refs=["NEO-4", "G-self-6"])
            else:
                # 상호가 로고 이미지, 업종이 동의어('회식집'·'운동공간')로 표기된 경우가 많아 문구 보강 수준 → P2
                F.add("G-N-fold", "G", "neo", "P2", "FAIL", "모바일 첫 화면(접힘 위)에 지역·업종·상호 조합이 다 보이지는 않습니다",
                      detail="키워드 기준 판정입니다. 상호가 로고 이미지로만 있거나 업종을 다른 말로 썼으면 문구 한 줄 보강으로 충분합니다.",
                      evidence=[f"첫 가시 헤딩: {fh.get('text')}", f"첫 화면 텍스트: {fold[:160]}", f"확인 키워드 {keys} 중 보인 것 {hit}"], fix="첫 가시 헤딩/부제에 '지역 + 업종 + 상호'", refs=["NEO-4"], weight=1.5)
        else:
            F.hold("G-N-fold", "G", "neo", "첫 화면 키워드 판정 보류 · --brand/--region/--category 를 주면 판정", evidence=[f"첫 가시 헤딩: {fh.get('text')}"])

    # --- 신뢰·권위 (G-trust)
    persons = c.get("persons") or []
    if persons:
        F.ok("G-T-person", "G", "trust", "대표/전문가 Person 구조화", evidence=persons[:3], refs=["K15", "K58", "G-self-13"])
    else:
        F.add("G-T-person", "G", "trust", "P1", "FAIL", "사람(대표·셰프·디자이너) 프로필이 없습니다", detail="AI 는 '누가 만드는가'를 신뢰 신호로 봅니다.", fix="대표 소개 + 경력·자격 + Person 스키마", refs=["K15", "K58", "K86", "G-self-13"], weight=3)
    sa = ent.get("same_as") or []
    naver_link = any("naver" in s for s in sa) or any(pg.get("links", {}).get("naver_place") for pg in pages.values())
    insta = any("instagram" in s for s in sa) or any(pg.get("links", {}).get("instagram") for pg in pages.values())
    gmap = any("google" in s for s in sa) or any(pg.get("links", {}).get("google_maps") for pg in pages.values())
    ev = [f"네이버 플레이스 {'있음' if naver_link else '없음'}", f"인스타 {'있음' if insta else '없음'}", f"구글 지도/GBP {'있음' if gmap else '없음'}"]
    if naver_link and (insta or gmap):
        F.ok("G-T-profiles", "G", "trust", "외부 공식 프로필 연결", evidence=ev, refs=["K22", "K45", "G-entity"])
    elif naver_link or insta or gmap:
        F.add("G-T-profiles", "G", "trust", "P2", "FAIL", "외부 공식 프로필 연결이 일부만 있습니다", evidence=ev, fix="플레이스·인스타·GBP 를 sameAs + 화면 링크로", refs=["K22", "K45"], weight=1.5)
    else:
        F.add("G-T-profiles", "G", "trust", "P1", "FAIL", "네이버 플레이스·인스타·구글 프로필 연결이 없습니다", evidence=ev, refs=["K22", "K45"], weight=3)
    # 원페이지 사이트: 경로 대신 앵커 섹션(id="about" 등)·헤딩으로 '면' 의 존재를 본다
    def has_section(path_rx: str, id_rx: str, heading_rx: str) -> list[str]:
        out = []
        for p, pg in pages.items():
            if re.search(path_rx, p, re.I) or any(re.search(id_rx, a, re.I) for a in (pg.get("anchor_ids") or [])) \
               or any(re.search(heading_rx, h.get("text", ""), re.I) for h in pg.get("headings", [])):
                out.append(p)
        return out
    # 'Preview' 가 review 에 걸리지 않도록 영문은 단어 경계로
    rev_pages = has_section(r"\breviews?\b|후기|리뷰", r"^(reviews?|testimonials?|voice)$", r"후기|리뷰|\breviews?\b")
    if rev_pages:
        F.ok("G-T-review", "G", "trust", "후기 콘텐츠 존재", evidence=sorted(set(rev_pages))[:4], detail="원문 URL·작성자·날짜가 있는 출처 기반 후기인지는 사람이 확인.", refs=["K43", "G-self-7"])
    else:
        F.add("G-T-review", "G", "trust", "P2", "FAIL", "후기·리뷰 섹션이 없습니다", fix="네이버 블로그/플레이스 원문 URL 을 출처로 붙인 후기 섹션 (평점 조작 금지)", refs=["K43", "K53"], weight=2)
    tel = sum(pg.get("tel_links", 0) for pg in pages.values())
    if tel:
        F.ok("G-T-tel", "G", "trust", "전화 즉시 연결(tel:) 링크", evidence=[f"{tel}개"])
    else:
        F.add("G-T-tel", "G", "trust", "P2", "FAIL", "tel: 링크 없음 (모바일에서 한 번에 전화 불가)", weight=1)
    about = has_section(r"about|story|소개|이야기|brand", r"^(about|story|brand|intro|philosophy|history)$", r"^(소개|이야기|우리|브랜드 스토리|about|our story)")
    if not about and n > 2:
        F.add("G-T-about", "G", "trust", "P2", "FAIL", "소개/이야기 면이 없습니다 (경로·앵커 섹션·헤딩 기준)", fix="창업 배경·재료·조리법·철학을 사실로", refs=["K38", "K65"], weight=1.5)

    # --- 최신성 (G-fresh)
    lms = c.get("sitemap_lastmods") or []
    dm = [pg.get("dates", {}).get("dateModified") for pg in pages.values() if pg.get("dates", {}).get("dateModified")]
    ds = [d for d in ([days_since(x) for x in lms] + [days_since(x) for x in dm]) if d is not None]
    if ds and min(ds) <= 90:
        F.ok("G-F-recent", "G", "freshness", f"최근 갱신 신호 {min(ds)}일 전", evidence=[f"sitemap lastmod {len(lms)}건, dateModified {len(dm)}페이지"], refs=["K78", "K07"])
    elif ds:
        F.add("G-F-recent", "G", "freshness", "P1", "FAIL", f"마지막 갱신 신호가 {min(ds)}일 전입니다", detail="3개월 이내 콘텐츠가 인용 확률 약 3배(SE Ranking). 6개월 넘으면 인용 대상에서 밀림.", fix="소식/이벤트/계절 메뉴 월 1회 + dateModified 갱신", refs=["K78", "K07", "K97"], weight=3)
    else:
        F.add("G-F-recent", "G", "freshness", "P1", "FAIL", "갱신 시점을 알 수 있는 신호(lastmod·dateModified)가 없습니다", fix="sitemap lastmod + dateModified", refs=["K78"], weight=3)
    news = has_section(r"news|notice|소식|event|blog|journal", r"^(news|notice|events?|blog|journal|monthly)$", r"소식|이벤트|이달의|공지|\bnews\b|\bjournal\b")
    if news:
        F.ok("G-F-news", "G", "freshness", "소식/이벤트 면 존재 (경로·앵커·헤딩)", evidence=news[:3], refs=["K07"])
    else:
        F.add("G-F-news", "G", "freshness", "P2", "FAIL", "소식·이벤트·블로그 등 주기 갱신 면이 없습니다", refs=["K07", "K97"], weight=1.5)

    # --- NEO 네이버 (G-neo)
    va = c.get("verification_any") or {}
    if va.get("naver"):
        F.ok("G-N-verify", "G", "neo", "네이버 서치어드바이저 소유확인 메타", refs=["NEO-1"])
    else:
        F.add("G-N-verify", "G", "neo", "INFO", "HOLD", "네이버 소유확인 메타 없음 · 파일/DNS 인증 여부 확인 필요",
              detail="메타가 없다는 사실만 확인. 서치어드바이저 화면에서 미등록이 확인되면 P1 로 올린다.",
              fix="미등록이면: 서치어드바이저 등록 → robots·sitemap 제출 → 수집 요청 → 주간 노출/클릭 확인", refs=["NEO-1", "K26"], weight=0)
    if not naver_link and service_area:
        F.hold("G-N-place", "G", "neo", "네이버 플레이스 링크 없음 · 워크인 매장이 아니면 플레이스 계정이 없을 수 있어 보류")
    elif not naver_link:
        F.add("G-N-place", "G", "neo", "P1", "FAIL", "네이버 플레이스 링크가 사이트에 없습니다", fix="플레이스 프로필 URL 을 sameAs + 화면 버튼(예약·길찾기)", refs=["K06", "K24"], weight=2)
    kakao = any(pg.get("links", {}).get("kakao") for pg in pages.values())
    og_ok = bool(((pages.get("/") or {}).get("og") or {}).get("image"))
    if og_ok:
        F.ok("G-N-share", "G", "neo", "카카오톡 공유 미리보기(OG 이미지) 준비", refs=["K45"])
    F.hold("G-N-briefing", "G", "neo", "네이버 AI 브리핑·AiRS 순위 실측은 에이전트가 naverai 로 별도 수행", detail="py -3 ~/.claude/skills/naverai/naver_ai_overview.py \"<질문>\" --mention \"<상호>\" / naver_place_rank.py \"<지역 업종>\" --id <placeId>", refs=["NEO-5", "W-score", "G-self-D1"])
    F.hold("G-N-ai-know", "G", "neo", "시크릿창 AI 질문(\"<상호> 어떤 곳이야?\")으로 AI 가 매장을 아는지·틀리게 아는지 확인 (D1 진단)", detail="ChatGPT·Claude·Gemini·Perplexity·네이버 AI 브리핑에 같은 질문 5~10개 → 인용 O/X 기록, 14일 후 재측정.", refs=["G-self-D1", "W-secret", "FYS-measure"])


# ---------------------------------------------------------------- D: 디자인
def check_design(F: Findings, c: dict, r: dict | None, d: dict | None):

    # --- 접근성 · 에이전트 조작 가능성 (axe-core 실측) -----------------------
    # AI 에이전트는 스크린리더가 읽는 접근성 트리를 그대로 읽는다. 사람에게 안 읽히면 기계에도 안 읽힌다.
    # 라이트하우스의 '에이전틱 브라우징' 은 통과 비율이고 공식 문서가 개발 중이라고 밝히고 있어,
    # 그 점수를 재현하지 않고 같은 축에서 우리가 실제로 잰 값만 싣는다.
    axe_err, by_group, worst = None, {}, {}
    agent_max = {}
    for path, modes in ((r or {}).get("pages") or {}).items():
        if not isinstance(modes, dict):
            continue
        for mode, v in modes.items():
            if not isinstance(v, dict):
                continue
            ax = v.get("axe") or {}
            if ax.get("error"):
                axe_err = ax["error"]
                continue
            for grp, cnt in (ax.get("by_group") or {}).items():
                if cnt > by_group.get(grp, 0):
                    by_group[grp] = cnt
                    worst[grp] = (path or "/", mode, ax.get("violations") or [])
            ag = v.get("agent") or {}
            if not ag.get("error") and ag.get("interactive"):
                if ag["interactive"] > agent_max.get("interactive", 0):
                    agent_max = dict(ag, _page=(path or "/"), _mode=mode)

    if axe_err and not by_group:
        F.hold("D-a11y", "D", "a11y", "접근성 스캔을 하지 못했습니다", evidence=[axe_err[:200]],
               detail="vendor/axe.min.js 가 없거나 페이지에서 실행되지 않았습니다. 렌더 결과 없이 판정하지 않습니다.")
    elif by_group or (r or {}).get("pages"):
        n_con = by_group.get("contrast", 0)
        if n_con:
            pgp, mode, vio = worst.get("contrast", ("/", "", []))
            samples = []
            for x in vio:
                if x["id"] == "color-contrast":
                    for nd in x["nodes"][:5]:
                        ratio = re.search(r"contrast of ([\d.]+)", nd.get("summary") or "")
                        samples.append(f"{nd['target'][:56]}" + (f" — 대비 {ratio.group(1)}:1" if ratio else ""))
            sev = "P1" if n_con >= 10 else "P2"
            F.add("D-a11y-contrast", "D", "a11y", sev, "FAIL", f"글자와 배경 대비가 기준 미달인 요소 {n_con}개",
                  detail="WCAG AA 기준은 본문 4.5:1, 큰 글씨 3:1 입니다. 밝은 화면·야외·저시력에서 안 읽히고, "
                         "화면을 읽는 기계도 같은 트리를 봅니다. 브랜드 색을 바꾸라는 뜻이 아니라 그 색 위의 글자 명도를 올리라는 뜻입니다.",
                  evidence=[f"가장 많은 화면: {pgp} ({mode})"] + samples[:5],
                  fix="글자색을 진하게 하거나 배경을 어둡게 합니다. 사진 위 글자는 그늘막(어두운 반투명 층)이나 text-shadow 로 보강합니다.",
                  weight=0)
        else:
            F.ok("D-a11y-contrast", "D", "a11y", "색 대비 기준 통과", evidence=["axe color-contrast 위반 0"])

        n_name = by_group.get("name", 0)
        if n_name:
            pgp, mode, vio = worst.get("name", ("/", "", []))
            F.add("D-a11y-name", "D", "a11y", "P1", "FAIL", f"이름이 없는 버튼·링크·입력칸 {n_name}개",
                  detail="아이콘만 있는 버튼처럼 읽을 이름이 없는 조작 요소입니다. 스크린리더도, 페이지를 대신 조작하는 AI 에이전트도 "
                         "'무엇을 누르는 것인지' 알 수 없어 그 기능을 건너뜁니다.",
                  evidence=[f"{pgp} ({mode})"] + [f"{x['id']}: {x['n']}건 — {x['nodes'][0]['html'][:70] if x['nodes'] else ''}"
                                                  for x in vio if x["id"] in AXE_NAME_IDS][:5],
                  fix="아이콘 버튼에 aria-label, 이미지 링크에 alt, 입력칸에 label 을 붙입니다.", weight=0)
        else:
            F.ok("D-a11y-name", "D", "a11y", "조작 요소에 읽을 이름 있음")

        n_tree = by_group.get("tree", 0)
        if n_tree:
            pgp, mode, vio = worst.get("tree", ("/", "", []))
            F.add("D-a11y-aria", "D", "a11y", "P2", "FAIL", f"접근성 트리가 깨지는 마크업 {n_tree}개",
                  detail="role 이 잘못 짝지어졌거나, aria-hidden 안에 초점이 가는 요소가 있거나, 조작 요소가 중첩돼 있습니다. "
                         "트리를 따라 읽는 쪽에서는 요소가 사라지거나 두 번 읽힙니다.",
                  evidence=[f"{pgp} ({mode})"] + [f"{x['id']}: {x['n']}건" for x in vio if x["id"] in AXE_TREE_IDS][:5],
                  fix="해당 요소의 role·aria 속성을 표준 조합으로 고칩니다.", weight=0)
        else:
            F.ok("D-a11y-aria", "D", "a11y", "접근성 트리 정합")

    if agent_max:
        un = agent_max.get("unnamed", 0)
        it = agent_max.get("interactive", 0)
        cls_vals = [v.get("cls") for modes in ((r or {}).get("pages") or {}).values() if isinstance(modes, dict)
                    for v in modes.values() if isinstance(v, dict) and v.get("cls") is not None]
        cls = max(cls_vals) if cls_vals else None
        lines = [f"조작 가능한 요소 {it}개 중 이름 없는 것 {un}개 ({agent_max.get('_page')} {agent_max.get('_mode')})",
                 f"랜드마크(main·nav·header·footer) {agent_max.get('landmarks', 0)}개 · h1 {agent_max.get('h1', 0)}개"]
        if agent_max.get("fields"):
            lines.append(f"입력칸 {agent_max['fields']}개 중 라벨 {agent_max.get('fields_labeled', 0)}개 · autocomplete {agent_max.get('fields_autocomplete', 0)}개")
        if cls is not None:
            lines.append(f"레이아웃 이동(CLS) {cls}")
        ok = un == 0 and agent_max.get("landmarks", 0) >= 2 and (cls is None or cls < 0.1)
        F.add("D-agent-ready", "D", "a11y", "INFO", "PASS" if ok else "FAIL",
              "페이지를 대신 조작하는 AI 에이전트가 읽을 수 있는 상태" + ("입니다" if ok else "인지 확인이 필요합니다"),
              detail="구글이 라이트하우스에 '에이전틱 브라우징' 축을 새로 만들었습니다. 통과 비율로만 표시되고 공식 문서가 개발 중이라고 밝히고 있어, "
                     "그 점수를 그대로 옮기지 않고 같은 축에서 우리가 실제로 잰 값만 싣습니다. 에이전트는 사람이 보는 화면이 아니라 접근성 트리와 "
                     "요소 위치를 보고 움직이므로, 이름 없는 버튼과 흔들리는 레이아웃에서 실패합니다.",
              evidence=lines,
              fix="이름 없는 조작 요소에 aria-label · 입력칸에 label 과 autocomplete · 레이아웃 이동 0.1 미만 유지.", weight=0)

    if not d or d.get("error"):
        F.hold("D-slop", "D", "slop", "anti-slop 검출 없음", evidence=[str((d or {}).get("error"))[:200]])
    else:
        s = d.get("summary") or {}
        by_class = s.get("by_class") or {}
        act = by_class.get("조치", 0)
        rev = by_class.get("검토", 0)
        F.add("D-slop-summary", "D", "slop", "INFO", "PASS", f"디자인 결함 탐지 73룰 · 검출 {s.get('total_hits')}건 ({s.get('pages')}페이지)",
              detail="조치/검토/정당/참고/오탐 분류는 언어·문맥 규칙 초안이며 에이전트가 확정합니다. 룰셋 밖 판단은 별도 절에.",
              evidence=[f"{k} {v}" for k, v in by_class.items()] + [f"CSS 유래 {sum((s.get('css_origin_by_rule') or {}).values())}건"], weight=0)
        for rule, n in list((s.get("by_rule") or {}).items())[:16]:
            by_class: dict[str, int] = {}
            by_lang: dict[str, int] = {}
            excerpt = ""
            why = ""
            samples: list[str] = []
            pages_hit: list[str] = []
            for p, pg in (d.get("pages") or {}).items():
                for i in pg.get("issues", []):
                    if i.get("rule") != rule:
                        continue
                    by_class[i.get("class", "검토")] = by_class.get(i.get("class", "검토"), 0) + i.get("hits", 0)
                    lk = pg.get("lang") or "?"
                    by_lang[lk] = by_lang.get(lk, 0) + i.get("hits", 0)
                    excerpt = excerpt or i.get("excerpt", "")
                    why = why or i.get("why", "")
                    for smp in i.get("samples", []):
                        if smp not in samples and len(samples) < 6:
                            samples.append(smp)
                    pages_hit.append(p)
            act = by_class.get("조치", 0)
            rev = by_class.get("검토", 0)
            if act >= 3:
                sev = "P1"
            elif act or rev:
                sev = "P2"
            elif by_class.get("참고") or by_class.get("오탐"):
                sev = "INFO"
            else:
                sev = "OK"
            css_n = (s.get("css_origin_by_rule") or {}).get(rule, 0)
            extra_detail = ""
            # CSS 유래 100% + 빌드 번들 사이트 → 미사용 유틸리티 정의를 센 것일 수 있어 P1 로 올리지 않는다
            if css_n == n and s.get("build_tool_pages") and sev == "P1":
                sev = "P2"
                extra_detail = " (CSS 규칙 정의 수 기준이며 빌드 번들에선 미사용 정의일 수 있음. raw HTML 에서 클래스 적용 여부 확인)"
            status = "PASS" if sev in ("OK", "INFO") else "FAIL"
            breakdown = " · ".join(f"{k} {v}" for k, v in sorted(by_class.items(), key=lambda kv: -kv[1]))
            rep = (s.get("repeated_across_pages") or {}).get(rule)
            rep_ev = [f"공용 스타일시트 반복: 페이지당 {rep['hits_per_page']}건 × {rep['pages']}페이지 (고유 {rep['unique_estimate']}건)"] if rep else []
            F.add(f"D-{rule}", "D", "slop", sev, status, f"{rule} {n}건 · {breakdown}", detail=((why or excerpt)[:220] + extra_detail),
                  evidence=[f"CSS 유래(규칙 정의) {css_n} · 마크업 유래 {n - css_n}", "언어판별 " + ", ".join(f"{k} {v}" for k, v in by_lang.items())]
                           + rep_ev + ([f"예: {'; '.join(samples[:4])}"] if samples else []),
                  pages=sorted(set(pages_hit)), weight={"P1": 2, "P2": 0.5}.get(sev, 0))
        if s.get("external_css_unresolved"):
            F.hold("D-css-partial", "D", "slop", f"외부 CSS 를 못 읽은 페이지 {s['external_css_unresolved']} · 스타일 룰은 하한선", weight=0)
    if r and not r.get("error"):
        for p, e in (r.get("pages") or {}).items():
            m = e.get("mobile_4x") or {}
            if m.get("error"):
                continue
            if m.get("overflowX"):
                F.add(f"D-overflow-{p}", "D", "render", "P1", "FAIL", f"{p}: 모바일 390px 에서 가로 넘침", evidence=m.get("overflowSamples", [])[:4], fix="넘치는 요소 max-width:100% / 고정폭 제거", weight=3)
            tt, tn = m.get("tinyText") or 0, m.get("textNodes") or 0
            if tn and pct(tt, tn) > 10:
                F.add(f"D-tiny-{p}", "D", "render", "P2", "FAIL", f"{p}: 11px 미만 텍스트 {tt}/{tn} ({pct(tt, tn)}%)", evidence=[f"글자 크기 분포 {dict(sorted(((int(k), v) for k, v in (m.get('fontSizeHist') or {}).items()), key=lambda kv: kv[0])[:6])}"], fix="캡션·라벨도 11.5px 이상", weight=1)
            st, sn = m.get("smallTapTargets") or 0, m.get("tapTargets") or 0
            if sn and pct(st, sn) > 40:
                F.add(f"D-tap-{p}", "D", "render", "P2", "FAIL", f"{p}: 44px 미만 탭 타깃 {st}/{sn} ({pct(st, sn)}%)", evidence=m.get("smallTapSamples", [])[:4], fix="링크·버튼 최소 44×44 (padding)", weight=1)
            if m.get("bodyWordBreak") and m["bodyWordBreak"] != "keep-all" and (m.get("lang") or "").startswith("ko"):
                F.add(f"D-keepall-{p}", "D", "render", "P2", "FAIL", f"{p}: body word-break 가 keep-all 이 아닙니다 (한국어 단어 중간 꺾임)", evidence=[f"word-break: {m['bodyWordBreak']}"], fix="body{word-break:keep-all;overflow-wrap:break-word} + 문단 text-wrap:pretty", weight=1)
            if not m.get("darkModeRules"):
                F.add(f"D-dark-{p}", "D", "render", "INFO", "PASS", f"{p}: prefers-color-scheme 규칙 없음 (단일 테마)", detail="단일 테마는 결함이 아닙니다. 다크 배경 사이트면 OS 다크 모드에서도 일관됩니다.", weight=0)
            if m.get("h1") and m["h1"].get("srOnly"):
                fh = m.get("firstHeading") or {}
                F.add(f"D-h1-{p}", "D", "render", "INFO", "PASS", f"{p}: H1 은 sr-only, 첫 가시 헤딩 = {fh.get('tag')} \"{(fh.get('text') or '')[:50]}\"", weight=0)
        # 모바일·데스크톱 분기 감지: 같은 URL 인데 제목·본문이 크게 다르면 뷰포트별로 다른 콘텐츠를 준다는 뜻
        for p, e in (r.get("pages") or {}).items():
            mm, dd = e.get("mobile_4x") or {}, e.get("desktop") or {}
            if not mm or not dd or mm.get("error") or dd.get("error"):
                continue
            mt, dt = (mm.get("title") or "").strip(), (dd.get("title") or "").strip()
            mb, db = mm.get("renderedBodyChars") or 0, dd.get("renderedBodyChars") or 0
            if (mt and dt and mt != dt) or (db > 500 and mb < db * 0.3):
                F.add(f"D-viewport-split-{p}", "D", "render", "P2", "FAIL", f"{p}: 모바일과 데스크톱이 다른 콘텐츠를 보여줍니다",
                      detail="검색엔진은 모바일 우선 색인이라 모바일에서 사라진 본문은 없는 것과 같습니다.",
                      evidence=[f"제목 모바일 '{mt[:40]}' / 데스크톱 '{dt[:40]}'", f"렌더 본문 모바일 {mb:,}자 / 데스크톱 {db:,}자"], fix="모바일에도 같은 본문(접기 허용)", weight=2)
        # 접힘 위 스크린샷 존재 → 에이전트가 눈으로 볼 대상
        shots = [(p, (e.get("mobile_4x") or {}).get("screenshots")) for p, e in (r.get("pages") or {}).items()]
        F.add("D-screens", "D", "render", "INFO", "PASS", "스크린샷 확보 · 에이전트가 직접 봐야 할 것: 첫 화면 위계·CTA·사진 품질·색 대비",
              evidence=[f"{p}: {s}" for p, s in shots if s][:6], weight=0)
    else:
        F.hold("D-render", "D", "render", "렌더 실측 없음 · 가로 넘침·글자 크기·탭 타깃 미판정")


# ---------------------------------------------------------------- main

# --- 사이트 빌더 지문 --------------------------------------------------------
# 국내 SMB 사이트는 대부분 빌더/솔루션 위에 있고, head 하드코딩 사고는 거의 전부
# "템플릿 공통 head 에 값을 박아 둔" 형태다. 조치 문구를 플랫폼에 맞게 준다.
PLATFORM_SIG = [
    ("카페24",      r"cafe24|ec-?base|EC_GLOBAL_|/web/upload/", "쇼핑몰 솔루션",
     "디자인 관리 > HTML/CSS 편집에서 공통 head 를 열고, 박아 둔 title·canonical·og 를 지웁니다. "
     "카페24 기본 치환코드({$page_title} 등)를 쓰거나 상품/게시판 레이아웃별로 나눠 넣습니다."),
    ("메이크샵",    r"makeshop\.co\.kr|makeshop", "쇼핑몰 솔루션",
     "디자인 편집 > 공통 헤더에서 고정값을 제거하고 페이지 구분 치환자로 바꿉니다."),
    ("고도몰",      r"godomall|godo\.co\.kr", "쇼핑몰 솔루션",
     "디자인 스킨의 공통 head 파일에서 고정값을 제거하고 페이지별 변수로 바꿉니다."),
    ("아임웹",      r"imweb\.me|imweb\.co|/imweb/", "사이트 빌더",
     "사이트 설정 > SEO 에서 페이지별 제목·설명을 개별 입력합니다. 공통 head 스크립트에 박은 og 태그가 있으면 지웁니다."),
    ("식스샵",      r"sixshop", "사이트 빌더",
     "페이지 설정에서 페이지별 SEO 항목을 채우고, 공통 코드 삽입 영역의 고정 메타를 지웁니다."),
    ("Wix",        r"wixstatic|wix\.com", "사이트 빌더",
     "각 페이지의 SEO 기본 설정에서 제목·설명을 개별 지정합니다."),
    ("Squarespace", r"squarespace", "사이트 빌더", "페이지별 SEO 패널에서 제목·설명을 개별 지정합니다."),
    ("Shopify",    r"cdn\.shopify|Shopify\.theme", "쇼핑몰 솔루션",
     "theme.liquid 의 head 에서 고정 문자열을 지우고 {{ page_title }}·{{ canonical_url }} 로 되돌립니다."),
    ("WordPress",  r"wp-content|wp-includes", "CMS",
     "SEO 플러그인(Yoast·Rank Math)의 전역 템플릿이 페이지 값을 덮어쓰지 않는지 확인합니다."),
    ("Framer",     r"framerusercontent|framer\.com", "사이트 빌더", "페이지별 Metadata 패널에서 제목·설명·OG 를 개별 지정합니다."),
    ("Notion",     r"notion\.site|notion-static", "문서 공개", "노션 공개 페이지는 메타 제어가 제한적입니다. 도메인 연결 도구(oopy 등) 쪽 설정을 확인합니다."),
    ("Next.js",    r"/_next/", "프레임워크",
     "App Router 면 각 page 의 generateMetadata / metadata export 로 옮기고, 루트 layout 에 고정한 값은 지웁니다."),
]


def detect_platform(out, pages):
    """홈 raw HTML 에서 빌더를 추정한다. 못 찾으면 None."""
    import os as _os, re as _re
    html = ""
    for p, pg in pages.items():
        rp = _os.path.join(out, pg.get("raw_path") or "")
        if pg.get("status") == 200 and pg.get("raw_path") and _os.path.exists(rp):
            try:
                with open(rp, encoding="utf-8", errors="replace") as fh:
                    html = fh.read()
            except OSError as e:          # 읽기 실패만 삼킨다. NameError 류는 그대로 터뜨린다.
                log(f"platform: raw 읽기 실패 {rp} ({e})")
                html = ""
            break
    if not html:
        return None
    gen = _re.search(r"<meta[^>]+name=[\"']generator[\"'][^>]+content=[\"']([^\"']+)", html, _re.I)
    for name, rx, kind, fix in PLATFORM_SIG:
        if _re.search(rx, html, _re.I):
            return {"name": name, "kind": kind, "fix": fix, "generator": (gen.group(1)[:60] if gen else None)}
    if gen:
        return {"name": gen.group(1)[:40], "kind": "generator 메타", "fix": "", "generator": gen.group(1)[:60]}
    return None


def main():
    ap = argparse.ArgumentParser(description="site-audit 4단계 룰 엔진")
    ap.add_argument("out")
    ap.add_argument("--facts", help="사실 기준표 JSON (name/phone/address/hours …)")
    ap.add_argument("--brand")
    ap.add_argument("--region")
    ap.add_argument("--category")
    a = ap.parse_args()
    a.out = fix_path(a.out)
    a.facts = fix_path(a.facts)
    c = load_json(os.path.join(a.out, "collect.json"))
    if not c:
        raise SystemExit("collect.json 없음")
    c["_out"] = a.out
    r = load_json(os.path.join(a.out, "render.json"))
    d = load_json(os.path.join(a.out, "design.json"))
    facts = load_json(a.facts) if a.facts else None
    brand = a.brand or (facts or {}).get("name") or ((c.get("entity") or {}).get("names") or [None])[0]
    F = Findings()
    unreachable = not html_pages(c)
    if unreachable:
        # 접근 자체가 안 되면 나머지 룰은 판정 불가 → 전부 HOLD. (없는 도메인에 "FAQ 없음" 같은 판정을 내지 않는다)
        F.add("S-T-unreachable", "S", "technical", "P0", "FAIL", "사이트에 접근하지 못했습니다 (HTML 페이지 0)",
              detail="DNS·TLS·차단(403/WAF)·타임아웃 중 하나입니다. URL·DNS·방화벽을 확인한 뒤 다시 실행하세요. 아래 레인은 판정하지 않았습니다.",
              evidence=[f"홈 status={c.get('home_status')} error={c.get('home_error')}", f"robots status={(c.get('robots') or {}).get('status')}",
                        f"sitemap status={[s.get('status') for s in (c.get('sitemaps') or [])][:2]}"], weight=10)
        F.hold("G-unreachable", "G", "access", "접근 불가로 GEO/AEO 레인 미판정")
        F.hold("D-unreachable", "D", "slop", "접근 불가로 디자인 레인 미판정")
    else:
        check_seo(F, c, r, facts)
        check_geo(F, c, r, facts, brand, a.region or (facts or {}).get("region"), a.category or (facts or {}).get("category"))
        check_design(F, c, r, d)
    order = {"P0": 0, "P1": 1, "P2": 2, "OK": 3, "INFO": 4}
    items = sorted(F.items, key=lambda x: (x["lane"], order.get(x["severity"], 9)))
    summary = {
        "total": len(items),
        "by_severity": {k: sum(1 for x in items if x["severity"] == k) for k in order},
        "by_status": {k: sum(1 for x in items if x["status"] == k) for k in ("PASS", "FAIL", "HOLD")},
        "by_lane": {l: {k: sum(1 for x in items if x["lane"] == l and x["severity"] == k) for k in ("P0", "P1", "P2")} for l in ("S", "G", "D")},
    }
    res = {"origin": c["origin"], "host": c["host"], "mode": c.get("mode"), "checked_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
           "unreachable": unreachable,
           "inputs": {"brand": brand, "region": a.region, "category": a.category, "facts": bool(facts), "render": bool(r and not r.get("error")), "design": bool(d and not d.get("error"))},
           "summary": summary, "findings": items}
    save_json(os.path.join(a.out, "findings.json"), res)
    log(f"[check] findings={summary['total']} P0={summary['by_severity']['P0']} P1={summary['by_severity']['P1']} P2={summary['by_severity']['P2']} HOLD={summary['by_status']['HOLD']}")
    for x in items:
        if x["severity"] in ("P0", "P1"):
            log(f"   {x['severity']} [{x['lane']}/{x['category']}] {x['title']}")


if __name__ == "__main__":
    main()
