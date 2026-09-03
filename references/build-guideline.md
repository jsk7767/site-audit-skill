# 홈페이지 설계·제작 지침 — 진단 룰과 1:1

새 홈페이지를 짓거나 리뉴얼할 때 **처음부터** 이 기준으로 만든다. 각 항목 끝의 `[룰 id]` 는 `site_check.py` 가 배포 전·후에 같은 것을 검사한다는 뜻이다. 진단기가 잡는 것을 설계 단계에서 미리 없애는 문서다.

에이전트가 "설계 지침" 요청을 받으면 이 문서를 그 사이트(업종·페이지 수·언어·스택)에 맞춰 **구체 명세**(페이지 목록, head 블록, JSON-LD, robots/sitemap/llms 초안, 성능 예산표, 디자인 규칙표)로 다시 써서 준다. 일반론을 복사하지 않는다.

---

### head 값은 페이지 변수로만 넣는다 (템플릿 하드코딩 금지)

빌더·프레임워크의 공통 head 에 `title`·`canonical`·`og:url`·`description` 을 문자열로 박으면 전 페이지가 같은 값을 내보낸다.
- `canonical` 은 **페이지마다 자기 자신**을 가리킨다(self-referencing). 별칭·정렬 파라미터 URL만 정본을 가리킨다.
- `og:url` 은 그 페이지의 canonical 과 **같은 값**이어야 한다.
- `title` 은 그 페이지가 답하는 질문이다. 인용·색인의 최소 단위가 URL 이라서, 한 페이지 한 질문이면 고유 제목은 따라온다.
- "도메인 신호를 한곳에 모으려고 메타를 통일한다"는 시공 요청은 거절한다. 권위는 그렇게 모이지 않고 개별 페이지의 색인 자격만 사라진다.

플랫폼별 위치: 카페24 = 디자인 관리 > HTML/CSS 편집의 공통 head · Next.js App Router = 각 page 의 `generateMetadata` (루트 layout 에 고정 금지) · 아임웹/식스샵 = 페이지별 SEO 패널 · Shopify = `theme.liquid` 의 `{{ page_title }}`·`{{ canonical_url }}`.


### 인용되는 문단은 형식이 아니라 증거로 만든다

질문형 소제목을 달았다는 사실만으로는 아무것도 보장되지 않는다(통제 실험에서 Q&A 포맷 단독은 -5.74%). 답변에 실제로 흡수되는 문단에는 다음이 들어 있다.

| 넣을 것 | 예 |
|---|---|
| 숫자 | 가격, 소요 시간, 역에서 도보 분, 좌석 수, 보관 기간 |
| 정의 | "OO는 ~입니다" 한 문장 |
| 비교 | 두 선택지의 차이를 표로 |
| 절차 | 번호를 매긴 순서 |
| 인용 | 실제로 받은 후기·인터뷰를 blockquote 로 |

없는 사실을 지어내라는 뜻이 아니다. 이미 아는 것을 숫자와 순서로 적으라는 뜻이다. 소제목은 기계가 본문을 끊어 읽는 경계이므로, 한 화면에 하나는 보이게 두고 바로 아래 첫 문장이 그 소제목의 답이 되게 쓴다.

### 배포 전에 봇으로 한 번 받아 본다

robots.txt 에 `Allow` 를 적어도 CDN·WAF 가 엣지에서 막으면 결과는 차단이다. 배포 후 홈을 OAI-SearchBot·GPTBot·ClaudeBot·PerplexityBot·Yeti UA 로 한 번씩 요청해 상태와 본문 크기가 사람과 같은지 본다(`site_collect.py` 가 자동으로 한다). 그리고 `nosnippet`·`max-snippet:0`·`noarchive` 를 넣지 않는다 — 색인은 되지만 AI 답변의 인용 후보에서 빠진다.


### 색 대비와 이름은 사람과 기계에 같이 걸린다

본문 4.5:1, 큰 글씨 3:1 (WCAG AA). 사진 위 글자는 어두운 반투명 층이나 `text-shadow: 0 1px 2px rgba(0,0,0,.55)` 로 보강한다. 아이콘만 있는 버튼에는 `aria-label`, 이미지 링크에는 `alt`, 입력칸에는 `label` 과 `autocomplete` 를 붙인다. 페이지를 대신 조작하는 AI 에이전트는 화면 그림이 아니라 접근성 트리와 요소 위치를 보고 움직이므로, 이름 없는 조작 요소와 흔들리는 레이아웃(CLS 0.1 이상)에서 실패한다. `main`·`nav`·`header`·`footer` 랜드마크를 두고 h1 은 하나만 둔다.

### 구조화 데이터와 화면은 같은 값이어야 한다

전화·주소·영업시간을 JSON-LD 에만 넣고 화면에 안 쓰면 확인할 수 없는 주장이 된다. 값이 바뀌면 양쪽을 함께 고친다. 전화는 화면 표기(`0507-…`)와 스키마 표기(`+82-50-7…`)가 달라도 되지만, **같은 번호여야 한다.**


## 0. 시작 전 — 사실 기준표 (facts.json)

사이트의 모든 사실은 한 파일에서 나온다. 페이지·스키마·llms.txt·플레이스가 이 표와 다르면 그게 결함이다. `[G-E-*]`

```json
{
  "name": "정확한 상호 (모든 매체 동일 표기)",
  "name_alt": {"en": "…", "ja": "…", "zh": "…"},
  "phone": "0507-0000-0000", "phone_display": "0507-0000-0000",
  "address": "○○시 ○○구 ○○로 00, 0층", "postal_code": "00000",
  "address_alt": {"en": "0F, 00 Example-ro, Example-gu, Seoul"},
  "geo": {"lat": 00.0000, "lng": 000.0000},
  "hours": [{"days": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"], "open": "11:00", "close": "22:00", "last_order": "21:20", "break": null}],
  "closed": "연중무휴 또는 매주 일요일",
  "category": "돼지갈비 전문점", "schema_type": "Restaurant",
  "region": "○○역 · ○○동", "landmarks": ["○○역 2번 출구 도보 1분"],
  "price_range": "₩18,000-₩58,000",
  "menu_source": "menu.json (라이브 플레이스에서 가져온 날짜 명시)",
  "person": {"name": "대표 이름", "role": "대표", "career": "한 줄 경력"},
  "profiles": {"naver_place": "https://m.place.naver.com/restaurant/…/home", "instagram": "…", "google_maps": "…", "kakao": "…"},
  "parking": "확인된 사실만", "reservation": "네이버 예약 / 전화", "group_max": "확인 후",
  "unverified": ["룸 인원", "원산지"]
}
```

- 확인 안 된 것은 `unverified` 에 두고 **사이트에 쓰지 않는다**. 나중에 채운다.
- 표기는 하나로. 띄어쓰기·한자·영문까지. `강남미용실 / 강남 美용실 / 강남 미용실` 은 AI 에게 세 업체다. `[G-E-name, K01]`

## 1. 정보 구조 — 페이지 최소 세트

| 페이지 | 역할 | 본문 | 룰 |
|---|---|---|---|
| `/` 홈 | 지역+업종+상호를 첫 문단·첫 가시 헤딩에. CTA(전화·예약·길찾기) 접힘 위 | ≥1,000자 | `[G-C-lead, G-N-fold, S-C-thin]` |
| `/menu` 또는 `/services` | 전 품목 + **가격** + 재료·소요시간·추천 대상. Menu/OfferCatalog 스키마 | ≥1,000자 | `[S-C-price, S-S-menu, K14, K05]` |
| `/about` 소개·이야기 | 창업 배경·재료·조리법·철학 + **사람**(대표·셰프) 경력 → Person 스키마 | ≥1,000자 | `[G-T-person, S-C-person, G-T-about, K15, K38, K58]` |
| `/location` 오시는 길 | 랜드마크 동선(출구·도보 분), 주차 **수치**, 대중교통, 지도 임베드 | ≥600자 | `[K06, K11, K25]` |
| `/faq` | 고객이 실제 묻는 질문 10개+ (질문형 소제목, 첫 문장이 답). FAQPage 는 **이 페이지에만** | ≥1,000자 | `[S-C-faq, S-S-faq-vis, S-S-dup, K17, K84]` |
| `/reviews` 후기 | 네이버 블로그·플레이스 **원문 URL·작성자·날짜** 를 출처로. 평점 구조화 금지 | — | `[G-T-review, S-C-rating, K43]` |
| `/reservation` 또는 `/contact` | 예약 방법 3가지+(전화·네이버·카카오), 인원·예약금·취소 조건 사실만 | ≥600자 | `[K24]` |
| `/news` 소식 | 월 1회 이상 갱신 면 (계절 메뉴·이벤트). dateModified 갱신 | — | `[G-F-news, G-F-recent, K07, K78]` |
| 언어판 `/en/` … | 진짜 번역 페이지(SSR). `<html lang>` 맞추고 hreflang 상호 참조 + x-default, 사이트맵 포함 | — | `[S-T-hreflang, S-T-lang, K63]` |

- 모든 페이지가 헤더·푸터에서 서로 링크된다. 고아 페이지 0. `[S-O-orphan, K19]`
- 단일 페이지(원페이지)로 갈 거면 위 역할을 섹션으로 다 넣고 앵커 링크. 단, 검색 노출 면이 1개라는 한계를 고객에게 미리 말한다.
- PDF 안내장 대신 텍스트 페이지. `[K23]`

## 2. `<head>` 표준 블록 (페이지마다)

```html
<html lang="ko">                                              <!-- 언어판마다 실제 언어 [S-T-lang] -->
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">   <!-- [S-T-viewport, K21] -->
<title>상호 | 지역 업종 핵심어 (15~60자, 상호 한 번만)</title>    <!-- [S-O-title-*] -->
<meta name="description" content="80~120자. 지역·대표메뉴·결정요인(주차·예약·가격대)">  <!-- [S-O-desc-*] -->
<link rel="canonical" href="https://도메인/경로/">              <!-- 자기 참조, 퓨니코드 기준 [S-T-canonical] -->
<meta property="og:title" content="…"><meta property="og:description" content="…">
<meta property="og:image" content="https://도메인/og/경로.jpg">  <!-- 1200×630 절대 URL [S-O-og] -->
<meta property="og:url" content="…"><meta property="og:site_name" content="상호"><meta property="og:locale" content="ko_KR"><meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">        <!-- [S-O-twitter] -->
<link rel="alternate" hreflang="ko" href="…/ko/"><link rel="alternate" hreflang="en" href="…/en/"><link rel="alternate" hreflang="x-default" href="…/">
<meta name="naver-site-verification" content="…">                <!-- 서치어드바이저 [S-T-verify, G-N-verify, NEO-1] -->
<meta name="google-site-verification" content="…">
<link rel="icon" href="/favicon.ico">                            <!-- [S-O-favicon] -->
<link rel="alternate" type="text/plain" href="/llms.txt">        <!-- 선택 [G-A-llms-link] -->
<link rel="preload" as="font" type="font/woff2" href="/fonts/…-ko.woff2" crossorigin>   <!-- 실제 첫 화면에 쓰는 폰트만 -->
```

- 페이지마다 제목이 다르고, 제목 템플릿이 상호를 두 번 붙이지 않는다. `[S-O-title-dup, S-O-title-repeat]`
- `noindex` 는 스테이징에만. 운영 배포 직전에 grep 으로 0건 확인. `[S-T-noindex]`

## 3. 본문 구조 — AI 가 인용하는 형태

- H1 은 페이지당 하나, 화면에 보이게. 첫 가시 헤딩(모바일 접힘 위)에 **지역 + 업종 + 상호**. `[S-O-h1, G-N-fold, K02]`
- 첫 문단 = 한 문장 정의: "○○시 ○○역 2번 출구 앞, 168시간 숙성 돼지갈비 전문 ○○집". 이 문장이 GBP 소개·플레이스 소개·llms.txt 에도 똑같이 들어간다. `[G-C-lead, K22]` (근거 태그 G-entity)
- 소제목은 **질문형**: "주차 되나요?", "단체 몇 명까지 되나요?", "얼마예요?" 다음 첫 문장이 직접 답(40~60자). `[G-C-question, K29, K84]`
- 문단은 2~4문장. 400자 넘는 문단 금지. `[G-C-paragraph]`
- 가격·비교·조건은 **표**, 절차·구성은 **리스트**. `[G-C-structure, K75]`
- 숫자를 낸다: 숙성 시간, 좌석 수, 주차 대수, 도보 분. "정성껏" 대신 "42~72시간". 사이트가 그 숫자의 **원천**이 되면 AI 가 인용한다. `[K34, K77]`
- 사람: 대표·셰프·디자이너 이름, 경력, 자격. 익명 사이트는 신뢰 신호가 없다. `[G-T-person, K15, K86]`
- 후기: 요약이 아니라 출처. 원문 URL·작성자·날짜. `[G-T-review]`
- 한국어 문장에 긴 줄표(—) 쓰지 않는다. 쉼표·가운뎃점·문장 분리. 영어판은 빈도 제한, 일·중은 자국 부호. `[D-em-dash-copy]`
- 이모지를 아이콘 대신 쓰지 않는다 (인라인 SVG). `[D-emoji-icon]`

## 4. JSON-LD — 엔티티 하나, 페이지마다 연결

```json
{"@context":"https://schema.org","@graph":[
 {"@type":["Restaurant","LocalBusiness"],"@id":"https://xn--…kr/#restaurant",
  "name":"상호","alternateName":["Example Store"],"url":"https://xn--…kr/","telephone":"+82-507-0000-0000",
  "address":{"@type":"PostalAddress","streetAddress":"○○로 00, 0층","addressLocality":"○○구","addressRegion":"○○시","postalCode":"00000","addressCountry":"KR"},
  "geo":{"@type":"GeoCoordinates","latitude":00.0000,"longitude":000.0000},
  "openingHoursSpecification":[{"@type":"OpeningHoursSpecification","dayOfWeek":["Monday","…"],"opens":"11:00","closes":"22:00"}],
  "image":["https://…/1.jpg","https://…/2.jpg","https://…/3.jpg"],"logo":"https://…/logo-512.png",
  "priceRange":"₩18,000-₩58,000","servesCuisine":"Korean BBQ","acceptsReservations":"True",
  "hasMenu":{"@id":"https://xn--…kr/menu#menu"},"hasMap":"https://map.naver.com/…",
  "sameAs":["https://m.place.naver.com/restaurant/…","https://www.instagram.com/…","https://maps.google.com/?cid=…"],
  "founder":{"@type":"Person","name":"대표","jobTitle":"대표","description":"한 줄 경력"},
  "dateModified":"2026-09-03"},
 {"@type":"WebPage","@id":"https://xn--…kr/#webpage","url":"…","name":"…","inLanguage":"ko","dateModified":"2026-09-03","about":{"@id":"https://xn--…kr/#restaurant"}},
 {"@type":"BreadcrumbList","itemListElement":[…]}
]}
```

규칙 `[S-S-*, G-E-*]`
- `@id` 는 **퓨니코드** 도메인 기준 한 값. 한글 도메인 문자열과 섞지 않는다.
- 필수 4: name·address·telephone·openingHoursSpecification. 권장: geo·image(절대 URL 3장+)·url·sameAs·priceRange·hasMap.
- `priceRange` 는 `₩₩` 또는 금액 범위. 문장 금지.
- `/menu` 에 `Menu(@id) → hasMenuSection → hasMenuItem(offers.price, priceCurrency KRW)`. `Restaurant.hasMenu` 가 그 @id 를 가리킨다. 서비스업은 `OfferCatalog`.
- `FAQPage`: **구글이 2026-05-07 FAQ 리치결과를 폐기했다**(6월 Search Console 리포트·리치결과 테스트 제거, 8월 API 제거). 스키마 타입 자체는 유효하고 남겨 둬도 문제없지만 **"리치결과가 나온다" 를 이유로 붙이지 않는다.** 붙인다면 화면에 그 문답이 보이는 페이지에만, 전 페이지 복제 금지. FAQ **콘텐츠 자체**는 AI 답변에서 여전히 유효하므로 화면의 문답을 잘 쓰는 쪽에 힘을 준다.
- `aggregateRating`·`Review` 평점은 화면에 같은 수치가 보이고 출처·약관이 맞을 때만. 기본은 넣지 않는다.
- `sameAs` 는 관리 가능한 공식 프로필만. 검색 URL·제3자 DB 는 넣지 않는다.
- 리치 결과 테스트로 검증 후 배포. `[S-S-parse]`

## 5. robots · sitemap · llms

```
# robots.txt                                   [S-T-robots, G-A-*, K72, NEO-2]
User-agent: *
Allow: /
Disallow: /admin/
Disallow: /api/
Sitemap: https://xn--…kr/sitemap.xml
```
- AI 크롤러(GPTBot·ClaudeBot·PerplexityBot·OAI-SearchBot·Google-Extended·CCBot 등)를 막지 않는다. 학습 거부가 정책이면 검색·인용용(OAI-SearchBot·ChatGPT-User·Claude-User·PerplexityBot)은 남긴다.
- Cloudflare 를 쓰면 AI Crawl Control 의 관리형 robots.txt 를 끈다. 서버 원본과 엣지 응답을 둘 다 curl 로 확인.
- sitemap.xml: 전 공개 URL + `lastmod`(실제 수정일 또는 빌드 시각) + 언어판. `[S-T-sitemap*]`
- llms.txt(선택): 사실 기준표 그대로(상호·주소·전화·시간·메뉴·가격·FAQ·페이지 링크). 라이브 데이터에서 생성한다. `[G-A-llms]`
- 없는 경로는 진짜 404. `[S-T-404]`

## 6. 성능 예산 (모바일 390px · CPU 4×)

| 지표 | 예산 | 방법 | 룰 |
|---|---|---|---|
| LCP | ≤ 2.5 s | 히어로 이미지 ≤ 200KB WebP, `fetchpriority="high"`, 영상은 포스터 먼저 | `[S-P-lcp]` |
| CLS | ≤ 0.1 | 모든 img width/height, 폰트 `font-display: swap` | `[S-P-cls, S-I-dims]` |
| TBT | ≤ 300 ms | 서드파티 스크립트 최소, 지연 로드 | `[S-P-tbt]` |
| 총 전송 | ≤ 2 MB | 영상 게이팅(클릭·뷰포트 진입 시), 이미지 srcset | `[S-P-bytes, K74]` |
| 폰트 | ≤ 4파일 · ≤ 300KB | 실사용 글리프 서브셋(pyftsubset), 굵기당 1파일, 언어별 unicode-range | `[S-P-fonts]` |
| 이미지 | alt 전수 · WebP · lazy(접힘 아래) | 장식은 `alt="" aria-hidden="true"` | `[S-I-alt, S-I-format, K73]` |
| 헤더 | HSTS · nosniff · Referrer-Policy · Permissions-Policy · X-Frame-Options | nginx add_header | `[S-T-sec, S-T-frame]` |
| 캐시 | 정적 HTML `s-maxage` + 배포 시 퍼지, 에셋 immutable + `?v=` | TTFB ≤ 200 ms 목표 | `[S-T-cache, S-T-ttfb]` |
| SSR | 원본 HTML 에 H1·본문·JSON-LD | `curl -s URL | grep '<h1'` 로 확인 | `[G-A-ssr, NEO-3]` |

## 7. 디자인 규칙 (anti-slop 73룰 중 설계 단계 필수)

- 기본 인디고/보라 액센트 금지. 브랜드 색 하나를 정한다. `[indigo-accent]`
- 그림자는 한 겹, 흐림 작게. 카드 안 카드 금지(안쪽은 여백+구분선). `[heavy-box-shadow, nested-cards]`
- 이모지 아이콘 금지 → 인라인 SVG. `[emoji-icon]`
- 본문 11.5px 이상, 캡션도. 행간 1.3 이상(한글 받침). `[tiny-body-text, tight-line-height]`
- 넓은 자간(0.08em+)은 라벨·구간 제목에만. 본문 금지. `[wide-body-tracking]`
- 그라디언트는 사진 위 가독성 오버레이만. 아이콘·버튼 채움 금지. `[gradient-fill]`
- 배경 있는 면에 테두리를 또 두르지 않는다(반투명 면 위 경계선은 예외). `[redundant-border]`
- 제목 위 대문자 키커, 가짜 잡지 마스트헤드, 색 띠 레일, 두 색 제목 금지. `[hero-kicker-eyebrow, fake-masthead, edge-stripe, two-tone-headline]`
- `transition: all` 금지(속성 지정). lorem ipsum·플레이스홀더 이미지 0. `[transition-all, lorem-ipsum, placeholder-image]`
- 한국어: `body{word-break:keep-all;overflow-wrap:break-word}` + 문단 `text-wrap:pretty` + 제목 `balance`. 여러 문장 소개문은 문장 단위 `<br>`. `[D-keepall]`
- 모바일 390px 가로 넘침 0. 탭 타깃 44×44 이상. `[D-overflow, D-tap]`
- 원화 가격은 축약하지 않는다(28,000원). 탐지기가 `oversized-number` 로 잡아도 오탐이다.
- 다크/라이트: 단일 테마여도 된다. 둘 다 지원하면 `prefers-color-scheme` + `data-theme` 토글 동시 처리.
- 배포 전 `npx -y @gessobuild/anti-slop check <site> --json` 으로 조치 등급 0.

## 8. 네이버 (NEO)

- 서치어드바이저 등록 → 소유확인(메타 또는 파일) → robots 검증 → sitemap 제출 → 수집 요청 → 주간 노출/클릭 확인. `[G-N-verify, NEO-1]`
- Yeti 허용. `[G-A-yeti]`
- 플레이스 프로필 URL 을 `sameAs` + 화면 버튼(예약·길찾기)로. 플레이스 관리자의 "홈페이지" 필드에 이 도메인 등록(로컬 API link 선점). `[G-N-place, K06, K24]`
- 카카오톡 공유 미리보기 = OG 이미지. `[G-N-share]`
- AI 브리핑 인용 조건: 구조화된 사실(표), 원출처 링크, 모바일 접힘 위 핵심 정보, 발행 속도. `[NEO-4, NEO-5]`
- 블로그는 "안(네이버 블로그: 신뢰·체류)" 과 "밖(자체 도메인: 구조화 사실 페이지)" 두 트랙. 서로 자연 링크.

## 9. 배포 전 게이트

```bash
py -3 ~/.claude/skills/site-audit/scripts/run_all.py --local ./site --base https://xn--…kr --name "상호" --out ./pre-deploy-audit --facts facts.json
# exit 0 (P0/P1 없음) 이어야 배포. 1 이면 findings.json 의 P0/P1 을 고친다.
```

배포 후 24시간 안에 라이브 모드로 한 번 더(헤더·캐시·TTFB·렌더는 라이브에서만 잰다).

## 10. 운영 루틴 (사이트는 짓고 끝이 아니다)

| 주기 | 할 일 | 룰 |
|---|---|---|
| 배포 직후 | 기준선 측정: 노출·클릭(서치어드바이저/서치콘솔), AI 질문 5~10개 O/X, `site:` 색인 수 | `[FYS-measure]` |
| +14일 | 재측정, `site_compare.py --delta` | |
| 월 1회 | 소식/계절 메뉴 1건 + dateModified + sitemap lastmod | `[G-F-*, K07, K78]` |
| 월 1회 | 시크릿창에서 AI 에게 "상호 어떤 곳?" / "지역 업종 추천" — 언급·오류·경쟁사 기록 | `[G-N-ai-know, W-secret, W-score]` |
| 분기 | 리뷰 키워드 모니터링, 깨진 링크, 가격 현행화(플레이스와 대조) | `[K49, K79]` |
| 메뉴·가격 변경 시 | facts/menu JSON → 페이지·스키마·llms 재생성 → 게이트 → 배포 → 플레이스 동기화 | `[G-E-*]` |

---

참조 표기: `K##` 『SEO·AEO·GEO 필살기 100』(김유진, 도서담, 2026) 규칙 번호 · `G-*` 『자영업자를 위한 GEO&AEO 완벽 가이드』 · `NEO-*`/`FYS-*` fire-your-seo-agency · `W-*` GEO/AEO 복습 워크북 · `CS-*` claude-seo v2.2.5 · anti-slop 룰 id 는 gesso 0.4.2.
