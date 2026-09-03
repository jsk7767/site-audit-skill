# -*- coding: utf-8 -*-
"""site-audit 공용 유틸 — HTTP fetch, HTML 파서, JSON-LD 평탄화, 경로/슬러그.

표준 라이브러리만 사용한다 (playwright 는 site_render/site_qa 에서만).
모든 스크립트는 이 모듈을 import 하고, stdout 은 UTF-8 + line buffering 으로 맞춘다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36")

_CTX = ssl.create_default_context()

# AI/검색 크롤러 목록 — amazing-seo-skill 20종 + 네이버 Yeti + 구글/빙 기본.
# group: ai_train(학습), ai_search(검색·인용), search(전통 검색), kr(국내)
CRAWLERS = [
    ("GPTBot", "ai_train"), ("OAI-SearchBot", "ai_search"), ("ChatGPT-User", "ai_search"),
    ("ClaudeBot", "ai_train"), ("Claude-User", "ai_search"), ("Claude-SearchBot", "ai_search"),
    ("anthropic-ai", "ai_train"),
    ("PerplexityBot", "ai_search"), ("Perplexity-User", "ai_search"),
    ("Google-Extended", "ai_train"), ("Googlebot", "search"), ("Googlebot-Image", "search"),
    ("bingbot", "search"), ("msnbot", "search"),
    ("Applebot", "search"), ("Applebot-Extended", "ai_train"),
    ("meta-externalagent", "ai_train"), ("FacebookBot", "ai_train"),
    ("Bytespider", "ai_train"), ("CCBot", "ai_train"), ("Amazonbot", "ai_search"),
    ("DuckAssistBot", "ai_search"), ("DuckDuckBot", "search"), ("YouBot", "ai_search"),
    ("cohere-ai", "ai_train"), ("MistralAI-User", "ai_search"),
    ("Yeti", "kr"), ("NaverBot", "kr"), ("Daum", "kr"), ("kakaotalk-scrap", "kr"),
]


# ---------------------------------------------------------------- HTTP
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401
        return None


def fetch(url: str, *, method: str = "GET", follow: bool = True, timeout: int = 30,
          ua: str = UA_DESKTOP, headers: dict | None = None, max_bytes: int = 8_000_000, _retried: bool = False) -> dict:
    """URL 을 가져와 status/headers/body/ttfb_ms/url 을 dict 로 돌려준다. 예외를 삼키지 않고 필드로 남긴다."""
    req_headers = {"User-Agent": ua, "Accept": "*/*", "Accept-Language": "ko,en;q=0.8"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, method=method, headers=req_headers)
    handlers = [urllib.request.HTTPSHandler(context=_CTX)]
    if not follow:
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    t0 = time.time()
    try:
        r = opener.open(req, timeout=timeout)
        ttfb = round((time.time() - t0) * 1000, 1)
        body = r.read(max_bytes) if method == "GET" else b""
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        if (hdrs.get("content-encoding") or "").lower() == "gzip" or url.split("?")[0].endswith(".gz"):
            try:
                import gzip
                body = gzip.decompress(body)
            except Exception:
                pass
        return {"status": r.status, "headers": hdrs,
                "body": body, "ttfb_ms": ttfb, "total_ms": round((time.time() - t0) * 1000, 1),
                "url": r.url, "error": None}
    except urllib.error.HTTPError as e:
        # 429/503 은 한 번 쉬었다 재시도 (claude-seo 오류 처리 표: 속도 제한 시 backoff)
        if e.code in (429, 503) and not _retried:
            wait = 5
            try:
                wait = min(30, int(e.headers.get("Retry-After") or 5))
            except Exception:
                pass
            time.sleep(wait)
            return fetch(url, method=method, follow=follow, timeout=timeout, ua=ua,
                         headers=headers, max_bytes=max_bytes, _retried=True)
        body = b""
        try:
            body = e.read(max_bytes)
        except Exception:
            pass
        return {"status": e.code, "headers": {k.lower(): v for k, v in e.headers.items()},
                "body": body, "ttfb_ms": round((time.time() - t0) * 1000, 1),
                "total_ms": round((time.time() - t0) * 1000, 1), "url": url, "error": None}
    except Exception as e:  # DNS, timeout, SSL ...
        return {"status": None, "headers": {}, "body": b"", "ttfb_ms": None, "total_ms": None,
                "url": url, "error": f"{type(e).__name__}: {e}"}


def decode(body: bytes, content_type: str = "") -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    enc = (m.group(1) if m else "utf-8").lower()
    try:
        return body.decode(enc, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


# ---------------------------------------------------------------- URL helpers
def normalize_origin(url: str) -> str:
    """입력 URL 을 scheme://host 형태의 origin 으로. scheme 없으면 https."""
    u = url.strip()
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urllib.parse.urlsplit(u)
    host = p.netloc
    # 한글 도메인 → punycode (urllib 이 IDNA 를 자동 처리하지 않음)
    try:
        host = host.encode("idna").decode("ascii")
    except Exception:
        pass
    return f"{p.scheme.lower()}://{host}"


def display_host(origin: str) -> str:
    """punycode → 사람이 읽는 도메인."""
    host = urllib.parse.urlsplit(origin).netloc
    try:
        return host.encode("ascii").decode("idna")
    except Exception:
        return host


def slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9가-힣]+", "-", text).strip("-").lower()
    return s or "site"


def same_host(origin: str, href: str) -> bool:
    try:
        a = re.sub(r"^www\.", "", urllib.parse.urlsplit(origin).netloc.lower())
        b = re.sub(r"^www\.", "", urllib.parse.urlsplit(href).netloc.lower())
        return a == b
    except Exception:
        return False


def path_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    path = p.path or "/"
    if p.query:
        path += "?" + p.query
    return path


def path_to_filename(path: str) -> str:
    s = path.strip("/")
    if not s:
        return "home"
    s = re.sub(r"[^A-Za-z0-9가-힣._-]+", "_", s)
    return s[:80]


# ---------------------------------------------------------------- HTML parser
class Doc(HTMLParser):
    """한 페이지의 SEO/GEO 관련 신호를 한 번의 스캔으로 뽑는다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.metas: list[dict] = []
        self.links_rel: list[dict] = []
        self.anchors: list[dict] = []
        self.imgs: list[dict] = []
        self.headings: list[dict] = []
        self.jsonld_raw: list[str] = []
        self.lang = None
        self.scripts = 0
        self.inline_styles = 0
        self.style_blocks = 0
        self.tables = 0
        self.lists = 0
        self.paragraphs: list[str] = []
        self.iframes = 0
        self.videos = 0
        self.forms = 0
        self.buttons = 0
        self.tel_links = 0
        self.details = 0
        self.time_tags: list[str] = []
        self._text: list[str] = []
        self._h = None                 # 헤딩 캡처 (tag/cls)
        self._hbuf: list[str] = []
        self._a_open = False           # 앵커 텍스트 캡처
        self._abuf: list[str] = []
        self._ld = False               # JSON-LD 캡처
        self._ldbuf: list[str] = []
        self._tbuf: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_p = False
        self._pbuf: list[str] = []
        self._in_body = False

    BLOCK_TAGS = {"br", "p", "div", "li", "tr", "td", "th", "section", "article", "header", "footer", "nav",
                  "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "figcaption",
                  "summary", "details", "address", "hr", "span"}

    # -- tags
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        # 태그 경계에서 단어가 붙지 않도록 공백 삽입 (헤딩·앵커·문단·본문 버퍼 모두)
        if tag in self.BLOCK_TAGS:
            if self._h is not None:
                self._hbuf.append(" ")
            if self._a_open:
                self._abuf.append(" ")
            if self._in_p:
                self._pbuf.append(" ")
            if self._in_body and not self._skip:
                self._text.append(" ")
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "body":
            self._in_body = True
        elif tag == "title":
            self._in_title = True
            self._tbuf = []
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links_rel.append(a)
        elif tag == "a":
            self.anchors.append({"href": a.get("href", ""), "rel": a.get("rel", ""),
                                 "text": "", "aria": a.get("aria-label", "")})
            self._a_open = True
            self._abuf = []
            if a.get("href", "").startswith("tel:"):
                self.tel_links += 1
        elif tag == "img":
            self.imgs.append({"src": a.get("src") or a.get("data-src") or "", "alt": a.get("alt"),
                              "loading": a.get("loading"), "width": a.get("width"), "height": a.get("height"),
                              "decorative": a.get("aria-hidden") == "true" or a.get("role") == "presentation",
                              "fetchpriority": a.get("fetchpriority")})
        elif tag in ("h1", "h2", "h3", "h4"):
            if self._h is None:          # 헤딩 안의 헤딩은 무시 (잘못된 마크업)
                self._h = {"tag": tag, "cls": a.get("class", "")}
                self._hbuf = []
        elif tag == "script":
            if (a.get("type") or "").lower().strip() == "application/ld+json":
                self._ld = True
                self._ldbuf = []
            else:
                self.scripts += 1
                self._skip += 1
        elif tag == "style":
            self.style_blocks += 1
            self._skip += 1
        elif tag == "table":
            self.tables += 1
        elif tag in ("ul", "ol"):
            self.lists += 1
        elif tag == "p":
            self._in_p = True
            self._pbuf = []
        elif tag == "iframe":
            self.iframes += 1
        elif tag == "video":
            self.videos += 1
        elif tag == "form":
            self.forms += 1
        elif tag == "button":
            self.buttons += 1
        elif tag == "details":
            self.details += 1
        elif tag == "time":
            self.time_tags.append(a.get("datetime", ""))
        if "style" in a and tag not in ("html", "head"):
            self.inline_styles += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.title = "".join(self._tbuf).strip()
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4") and self._h and self._h["tag"] == tag:
            text = re.sub(r"\s+", " ", "".join(self._hbuf)).strip()
            self.headings.append({"tag": tag, "text": text, "cls": self._h.get("cls", "")})
            self._h = None
        elif tag == "a" and self._a_open:
            if self.anchors:
                self.anchors[-1]["text"] = re.sub(r"\s+", " ", "".join(self._abuf)).strip()[:120]
            self._a_open = False
        elif tag == "script":
            if self._ld:
                self.jsonld_raw.append("".join(self._ldbuf))
                self._ld = False
            elif self._skip:
                self._skip -= 1
        elif tag == "style" and self._skip:
            self._skip -= 1
        elif tag == "p" and self._in_p:
            t = re.sub(r"\s+", " ", "".join(self._pbuf)).strip()
            if t:
                self.paragraphs.append(t)
            self._in_p = False

    def handle_data(self, d):
        if self._in_title:
            self._tbuf.append(d)
            return
        if self._ld:
            self._ldbuf.append(d)
            return
        if self._h is not None:
            self._hbuf.append(d)
        if self._a_open:
            self._abuf.append(d)
        if self._in_p:
            self._pbuf.append(d)
        if not self._skip and self._in_body:
            self._text.append(d)

    # -- derived
    def body_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._text)).strip()

    def meta(self, name: str) -> str | None:
        name = name.lower()
        for m in self.metas:
            if (m.get("name") or "").lower() == name or (m.get("property") or "").lower() == name:
                return m.get("content")
        return None

    def rel(self, rel: str) -> list[dict]:
        return [l for l in self.links_rel if rel in (l.get("rel") or "").lower().split()]

    def jsonld(self) -> list:
        out = []
        for raw in self.jsonld_raw:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except Exception:
                # 흔한 오류: 뒤에 붙은 세미콜론/주석 → raw_decode 로 첫 객체만
                try:
                    obj, _ = json.JSONDecoder().raw_decode(raw)
                    out.append(obj)
                except Exception:
                    out.append({"__parse_error__": raw[:200]})
        return out


def parse_html(html: str) -> Doc:
    d = Doc()
    try:
        d.feed(html)
        d.close()
    except Exception:
        pass
    return d


# ---------------------------------------------------------------- JSON-LD
def flatten_ld(nodes) -> list[dict]:
    """@graph 와 중첩 객체를 모두 펼쳐 @type 이 있는 노드 목록으로."""
    out: list[dict] = []

    def walk(n):
        if isinstance(n, dict):
            if "@graph" in n and isinstance(n["@graph"], list):
                for x in n["@graph"]:
                    walk(x)
                # @graph 밖의 필드도 노드일 수 있음
                rest = {k: v for k, v in n.items() if k != "@graph"}
                if "@type" in rest:
                    out.append(rest)
                return
            if "@type" in n:
                out.append(n)
            for v in n.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(nodes)
    return out


def types_of(node: dict) -> list[str]:
    t = node.get("@type")
    if isinstance(t, list):
        return [str(x) for x in t]
    return [str(t)] if t else []


LOCAL_BUSINESS_TYPES = {
    "LocalBusiness", "Restaurant", "CafeOrCoffeeShop", "BarOrPub", "Bakery", "FoodEstablishment",
    "Store", "HealthAndBeautyBusiness", "BeautySalon", "NailSalon", "HairSalon", "DaySpa",
    "MedicalBusiness", "Dentist", "Physician", "MedicalClinic", "HealthClub", "ExerciseGym",
    "SportsActivityLocation", "LodgingBusiness", "Hotel", "AutoRepair", "RealEstateAgent",
    "LegalService", "Attorney", "AccountingService", "FinancialService", "TravelAgency",
    "ProfessionalService", "HomeAndConstructionBusiness", "EducationalOrganization", "School",
    "ChildCare", "PetStore", "VeterinaryCare", "Florist", "JewelryStore", "ClothingStore",
    "Organization", "Corporation",
}


def is_local_business(node: dict) -> bool:
    ts = set(types_of(node))
    return bool(ts & LOCAL_BUSINESS_TYPES)


# ---------------------------------------------------------------- misc
def fix_path(p: str | None) -> str | None:
    """Git Bash 경로(/c/Users/…)를 Windows 경로(C:/Users/…)로. MSYS_NO_PATHCONV=1 상태에서 넘어온 인자 대비."""
    if not p:
        return p
    m = re.match(r"^/([A-Za-z])/(.*)$", p)
    if m and os.name == "nt":
        return f"{m.group(1).upper()}:/{m.group(2)}"
    return p


_PATH_LEAK = re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\s\"'<>)]+|/c/Users/[^\s\"'<>)]+")


def scrub(text) -> str:
    """산출물에 로컬 사용자 경로가 남지 않도록 지운다 (QA 가 검사한다)."""
    return _PATH_LEAK.sub("<local-path>", str(text))


CLASS_WEIGHT = {"P1": 2.0, "P2": 0.5}


def _design_sev(new_cls: dict) -> str:
    act, rev = new_cls.get("조치", 0), new_cls.get("검토", 0)
    if act >= 3:
        return "P1"
    if act or rev:
        return "P2"
    if new_cls.get("참고") or new_cls.get("오탐"):
        return "INFO"
    return "OK"


def apply_narrative(findings: list[dict], design: dict | None, nar: dict | None) -> tuple[list[dict], dict]:
    """narrative.json 의 사람 판정(design_overrides · finding_status_overrides)을 findings 에 적용한다.

    site_score.py 와 site_report.py 가 같은 함수를 쓰므로 점수·시뮬레이션·보고서가 한 기준으로 맞는다.
    반환: (판정 적용된 findings 사본, 디자인 분류 증감 {class: ±hits})
    """
    fs = [dict(f) for f in findings]
    nar = nar or {}
    delta: dict[str, int] = {}
    do = nar.get("design_overrides") or {}
    if design and not design.get("error") and do:
        for rule, spec in do.items():
            if not isinstance(spec, dict):
                continue
            orig: dict[str, dict[str, int]] = {}   # lang → class → hits
            for pg in (design.get("pages") or {}).values():
                for i in pg.get("issues", []):
                    if i.get("rule") != rule:
                        continue
                    lang = (pg.get("lang") or "?")
                    orig.setdefault(lang, {})
                    orig[lang][i.get("class", "검토")] = orig[lang].get(i.get("class", "검토"), 0) + int(i.get("hits", 0))
            if not orig:
                continue
            new_cls: dict[str, int] = {}
            for lang, classes in orig.items():
                hits = sum(classes.values())
                key = lang.lower().split("-")[0]
                if "class" in spec:
                    cls = spec["class"]
                elif spec.get(lang) or spec.get(key):
                    cls = spec.get(lang) or spec.get(key)
                else:
                    for c, n in classes.items():   # 지정 없는 언어판은 원래 분류 유지
                        new_cls[c] = new_cls.get(c, 0) + n
                    continue
                new_cls[cls] = new_cls.get(cls, 0) + hits
            for classes in orig.values():
                for c, n in classes.items():
                    delta[c] = delta.get(c, 0) - n
            for c, n in new_cls.items():
                delta[c] = delta.get(c, 0) + n
            f = next((x for x in fs if x["id"] == f"D-{rule}"), None)
            if f:
                sev = _design_sev(new_cls)
                f["severity"] = sev
                f["status"] = "PASS" if sev in ("OK", "INFO") else "FAIL"
                f["weight"] = CLASS_WEIGHT.get(sev, 0)
                total = sum(new_cls.values())
                f["title"] = f"{rule} {total}건 · " + " · ".join(f"{c} {n}" for c, n in sorted(new_cls.items(), key=lambda kv: -kv[1])) + " (진단자 판정)"
                if spec.get("note"):
                    f["detail"] = ((f.get("detail") or "") + " 판정 메모: " + str(spec["note"])).strip()
    so = nar.get("finding_status_overrides") or {}
    for fid, st in so.items():
        f = next((x for x in fs if x["id"] == fid), None)
        if not f or st not in ("PASS", "FAIL", "HOLD"):
            continue
        f["status"] = st
        if st == "HOLD":
            f["severity"] = "INFO"
            f["weight"] = 0
        elif st == "PASS":
            if f["severity"] in ("P0", "P1", "P2"):
                f["severity"] = "OK"
            f["weight"] = 0
        elif st == "FAIL" and f["severity"] in ("OK", "INFO"):
            # 기계가 양호로 본 것을 사람이 결함으로 뒤집을 때: 제목에 표시하고 P2 기본 가중치를 준다
            f["severity"] = "P2"
            f["weight"] = 1.5
            if "(진단자 판정" not in f["title"]:
                f["title"] = f["title"] + " (진단자 판정: 결함)"
        elif st == "PASS" and "(진단자 판정" not in f["title"] and f["id"] not in {k for k in (nar.get("design_overrides") or {})}:
            pass
    return fs, delta


def md5(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8", "replace")
    return hashlib.md5(s).hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def log(msg: str) -> None:
    print(msg, flush=True)


# 한국 전화번호 / 주소 휴리스틱 (NAP 내부 일관성용)
PHONE_RE = re.compile(r"(?<![\d-])(0\d{1,3}[-.\s]?\d{3,4}[-.\s]?\d{4})(?![\d-])")
INTL_PHONE_RE = re.compile(r"\+82[-.\s]?(\d{1,3})[-.\s]?(\d{3,4})[-.\s]?(\d{4})")
_REGION = r"(?:서울특별시|서울시|서울|부산광역시|부산|대구광역시|대구|인천광역시|인천|광주광역시|광주|대전광역시|대전|울산광역시|울산|세종특별자치시|세종|경기도|경기|강원특별자치도|강원도|강원|충청북도|충북|충청남도|충남|전북특별자치도|전라북도|전북|전라남도|전남|경상북도|경북|경상남도|경남|제주특별자치도|제주도|제주)"
# 도로명: … 예시로4길 10, 1층 / 달구벌대로 2100 / 테헤란로 152
ADDR_RE = re.compile(_REGION + r"[^\n<>\"]{2,50}?[가-힣A-Za-z0-9]+(?:로|길|대로)\s?\d*(?:번?길)?\s?\d+(?:-\d+)?(?:,?\s?(?:지하\s?)?\d+층)?(?:\s?\d+호)?")
# 지번: … ○○구 ○○동 12-3 / 중구 남산동 4가 1
JIBUN_RE = re.compile(_REGION + r"[^\n<>\"]{2,40}?[가-힣]+(?:동|읍|면|리|가)\s?\d+(?:-\d+)?")


def find_phones(text: str) -> list[str]:
    out = []
    for m in PHONE_RE.findall(text):
        n = re.sub(r"[^\d]", "", m)
        if 9 <= len(n) <= 12 and n not in out:
            out.append(n)
    for a, b, c in INTL_PHONE_RE.findall(text):
        n = "0" + a + b + c
        if n not in out:
            out.append(n)
    return out


def find_addresses(text: str) -> list[str]:
    out = []
    for rx in (ADDR_RE, JIBUN_RE):
        for m in rx.finditer(text):
            s = re.sub(r"\s+", " ", m.group(0)).strip()
            if s not in out:
                out.append(s)
    return out[:10]


QUESTION_RE = re.compile(r"(\?|？|나요|까요|인가요|할까|어떻게|왜 |무엇|어디|언제|얼마|how |what |why |where |when |which )", re.I)


def is_question(text: str) -> bool:
    return bool(QUESTION_RE.search(text or ""))
