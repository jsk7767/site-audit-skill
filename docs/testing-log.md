# site-audit 테스트 기록 (writing-skills RED → GREEN → REFACTOR)

## RED — 스킬 없이 (2026-09-03, Sonnet, 도구 없이 계획만)

과제: example-j.kr 을 SEO·GEO/AEO·디자인으로 2시간 안에 진단하는 계획.

관찰된 기준선 (스킬이 채워야 할 빈칸):
- 측정 방법이 **수동**이다: "페이지 소스 보기(Ctrl+U)", "PageSpeed 웹 UI", "크롬 반응형 모드 육안". 재현 불가, 같은 잣대로 두 사이트 비교 불가.
- **증거 원장 없음.** 항목별 판정 나열만 있고 finding 마다 근거·페이지·조치가 붙지 않는다.
- 빠진 검사: AI 크롤러별 robots 판정(20종), llms.txt, SSR(원본 HTML vs 렌더), 엔티티 `@id` 통일·퓨니코드 혼재, FAQ 스키마 ↔ 화면 일치, 동일 JSON-LD 복제, priceRange 형식, hreflang/x-default/lang 불일치, 사이트맵 lastmod, soft 404, 보안 헤더, 캐시/TTFB, 폰트 바이트, 콘솔 오류, 가로 넘침, 11px 미만 글자, 44px 탭 타깃, keep-all, anti-slop 73룰, CSS/마크업 유래 분리, 언어별 줄표 판정, 가격 오탐 처리.
- 점수: "가중평균은 시간상 생략" → 기존 10개 보고서와 비교 불가. 시뮬레이션·지렛대 없음.
- 네이버: 서치어드바이저·플레이스 링크·블로그 언급 정도. Yeti·소유확인 메타·접힘 위 정보·AI 브리핑 인용 조건 없음.
- 좋았던 점(유지): PASS/HOLD/FAIL 언어와 P0/P1/P2 를 스스로 채택(thejsk 학습 효과), "AI 응답은 시점 의존" 고지, 미확인 목록을 정직하게 씀.

→ 스킬 형태 결정: 규율 스킬이 아니라 **기술·참조 스킬**. 금지 목록보다 "산출물의 모양"(파일·스키마·순서)을 고정하는 레시피가 맞다.

## GREEN — 파이프라인 실측 (2026-09-03)

| 사이트 | 페이지 | SEO | GEO | 디자인 | 소요 | QA |
|---|---|---|---|---|---|---|
| example-a.kr (4개 국어, 퓨니코드) | 9 | 93 | 96 | 소폭 손질 (499건: 오탐 245·검토 117·참고 111·정당 20·조치 6) | collect 25s · render 150s · design 60s | 판단 미작성만 FAIL (의도) |
| example-j.kr (단일 페이지) | 1 | 91 | 96 | 소폭 손질 (62건) | render 90s | 동일 |

발견·수정한 결함:
1. 헤딩·본문 텍스트가 태그 경계에서 붙음("사이트 A일곱 낮") → 파서 블록 태그에 공백, 렌더는 innerText.
2. 0507 안심번호(4자리 국번)·"○○로4길 10" 도로명 미추출 → 정규식 확장.
3. Git Bash 가 `/ko/` 를 `C:/Program Files/Git/ko/` 로 변환 → 스크립트에서 복원 + `MSYS_NO_PATHCONV=1`.
4. 루트가 언어 선택 페이지(128자)라 SSR P0 오판 → 본문 최장 페이지를 대표로.
5. em-dash 38건이 언어별 분류를 합쳐 P1 → 룰별 조치/검토/정당 내역으로 표시, 조치 ≥3 일 때만 P1.
6. priceRange "₩15,000~₩80,000" 를 문장으로 오판 → 24자·한글/영단어 기준.
7. anti-slop 0.4.2 JSON 에 gating/advisory 키 없음 → 룰 집합으로 advisory 판정, 예시(`e.g. …`) 파싱.
8. 모바일 스크린샷 DPR 3 로 보고서 2.5MB → `scale="css"`.

9. 로컬 게이트: Git Bash 경로(`/c/Users/…`)가 `MSYS_NO_PATHCONV=1` 상태로 넘어오면 Python 이 못 읽어 페이지 0 → `fix_path()` 로 정규화. 실패 메시지의 절대 경로가 보고서에 새어 QA 가 잡음 → `scrub()`.
10. 로컬 게이트가 `404.html` 을 일반 페이지로 세어 noindex P0·canonical/OG/hreflang/고아 P1 오탐 → 오류 페이지 제외.
11. 보고서 자기검사(anti-slop): 줄표 40·nested-cards 34·edge-stripe·underlined·tiny → 템플릿 수정 후 남은 것은 oversized-number(인용 가격)·overstuffed/multiline(표 밀도)·row-kicker 2 = 참고 등급만. 인용 예시의 긴 줄표는 "[긴 줄표]" 로 표기.

로컬 게이트 실측(사이트 A `homepage/site`, 2026-09-03): 페이지 9(404 제외), 디자인 검출 499(라이브와 동일), GEO 96(라이브와 동일). SEO 는 렌더·헤더·TTFB 항목이 로컬에서 HOLD 라 라이브보다 낮게 나온다(설계대로).

## GREEN — 서브에이전트 (Sonnet, SKILL.md 만 주고 site-f-site.vercel.app)

결과: 0~7단계 전부 수행, 12분, 도구 호출 60회. SEO 56 · GEO 91 · 디자인 손볼 것 있음(65건). P0 3(canonical 8/8·OG 8/8·영업시간 누락), narrative.json 작성(지렛대 3·판단 문단·notes·design_overrides), QA 11/11 PASS, 보고 형식 준수. 스크린샷을 직접 열어 findings 에 없는 것(쉼표 뒤 공백 누락으로 보이는 단어 꺾임)을 잡음.

서브에이전트가 짚은 결함(원문 요지):
1. `design_overrides` 를 세 문서가 안내하지만 `site_report.py` 가 읽지 않는 죽은 필드 → hero-kicker-eyebrow 를 "정당" 으로 써도 카드가 "검토 1" 그대로.
2. `finding_status_overrides` 가 목록·카운트엔 반영되지만 점수 시뮬레이션(`simulate()`)은 원본 findings 기준.
3. `run_all.py` 마지막 요약 print 의 긴 줄표가 cp949 콘솔에서 `UnicodeEncodeError` → 산출물은 다 만들어졌는데 파이프라인이 죽은 것처럼 보임.
4. 같은 사실(소유확인 메타 없음)이 `G-N-verify` 는 HOLD, `S-T-verify` 는 P1 FAIL 로 레인마다 다른 심각도.
5. `S-S-menu` 가 "구조화 없음" 이라고만 써서 백지처럼 읽히는데 실제로는 Restaurant 안에 MenuItem 8개가 인라인으로 있고 Menu(@id) 승격·가격만 남은 상태. 근거 한 줄이 아쉬움.
6. 잘 된 것: doctor→run_all 이 안내한 시간 안에 한 번에 끝남, MSYS_NO_PATHCONV·py -3 지침 유효, 스크린샷 강제가 유효.

## REFACTOR (2026-09-03, 위 6건 반영)

- `_common.apply_narrative()` 신설: `finding_status_overrides` + `design_overrides`(단일 class 또는 언어판별) 를 findings 에 적용해 제목·심각도·weight 를 바꾸고 디자인 분류 증감을 돌려준다. `site_score.py` 와 `site_report.py` 가 같은 함수를 써서 점수·시뮬레이션·카드·판정이 한 기준. `run_all.py --report-only` 가 score 를 다시 돌린다.
- `run_all.py` stdout UTF-8 reconfigure + 요약 문장의 줄표 제거. QA FAIL 문구에 "산출물은 전부 생성됨" 명시.
- `S-T-verify` 를 `G-N-verify` 와 같은 HOLD(INFO, 감점 0)로. 미등록 확인 시 에이전트가 FAIL 로 올린다.
- `S-S-menu` 근거에 "Menu 노드 n · MenuItem m · 업체 노드 안 hasMenuSection p페이지 · 가격 있는 항목 q" 를 항상 넣고, 인라인만 있는 경우를 "승격만 남음" 으로 따로 문장화.
- SKILL.md 6단계 1·7 항목, narrative-schema.md 규칙 갱신.
- 재검증: site-j(narrative 있음) `--report-only` → design_overrides 가 카드·판정 카드에 반영, QA PASS. site-f(에이전트 narrative) 재렌더 → QA PASS.

## 철저 검토 (2026-09-03, 사용자 요청 "철저하게 검토하자")

정적 검토
- 지침 문서의 룰 id 69개 ↔ `site_check.py` 127개 id 교차 확인: 미해결 0 (`G-entity` 는 근거 태그).
- 『필살기 100』 매핑 집계가 문서(28/22/50)와 실제(23/21/56) 불일치 → 수정. 번호 1~100 누락 없음.
- SKILL.md frontmatter 713자(한도 1024 이내).
- `lstrip("www.")` 접두어 오용(문자 집합 제거) → 정규식으로. `python -W error` 컴파일 경고 0.

코드 결함 (정독으로 발견·수정)
1. **HTML 파서**: `<h1><a>브랜드</a></h1>` 처럼 링크가 헤딩을 감싸면 캡처 버퍼가 앵커로 교체돼 헤딩이 사라짐 → 헤딩·앵커·JSON-LD 버퍼를 분리. 단위 테스트 통과.
2. `oversized-number` 분류가 룰 설명문의 "$1,842,000" 에 항상 매치해 **무조건 오탐** 처리 → 실제 검출 예시(samples)로만 판정.
3. fetch: 429/503 한 번 재시도(Retry-After 존중), gzip 사이트맵 해제. 처음 넣은 재시도 조건의 연산자 우선순위 오류를 즉시 교정.
4. render: `load` 이벤트가 안 끝나는 사이트 → `domcontentloaded` 후퇴.
5. doctor·compare 출력의 긴 줄표 제거(cp949 콘솔).

극단 사례 실측
| 사례 | 결과 | 조치 |
|---|---|---|
| 없는 도메인 | 크래시 없음. 그러나 SEO 62·"FAQ 없음" 등 **허위 판정** 생성 | 접근 불가 시 `S-T-unreachable` P0 + 전 레인 HOLD, 점수 None, exit 2 |
| example-c.kr (인트로형, 영문 헤딩) | 줄표 42건 전부 '조치'. 처음엔 "영문 사이트를 ko 로 오판" 이라 의심했으나 실측 본문은 한글 57% → 판정이 맞았음. 다만 lang≠본문 사례 일반 대비로 본문 문자 언어 추정(`text_lang`)을 추가해 디자인 분류·`S-T-lang-text` 에 반영. 렌더 후 본문이 원본 20% 미만(2,860자 → 75자)이면 `G-A-hidden` P2 | 실측 후 판단 정정 |
| example-b.com (17페이지, 다국어, 22MB) | 32초, 정상. OG 12/16 없음·JSON-LD 12/16 없음(기존 보고서와 일치) | 없음 |
| www.example-d.kr (6페이지, www 정규화) | SEO 96·GEO 98, P0/P1 0, 308 리다이렉트·Person 감지 정상 | 없음 |
| 사이트 A·사이트 J 회귀 | 사이트 A 84건 동일. 사이트 J는 Person 2건이 PASS 로 바뀜 → **라이브 사이트가 당일 갱신**된 것(구 raw Person 0 → 신 raw 1, Last-Modified 05:13Z). 파서 회귀 아님 | 재수집 시 결과 변동 가능성을 SKILL 함정에 명시 |

## 실전 투입 (2026-09-03, 사이트 A·사이트 D)

- 사이트 A: 대표 페이지 선택이 "본문 최장" 이라 메뉴 페이지가 뽑혀 첫 문단 판정 오류 → 언어 루트(/ko/ 우선) 선택으로 수정.
- 사이트 D 에서 잡은 오탐 4건 수정: ① `S-T-cache` 가 Cloudflare 아닌 사이트(cf-cache-status 전부 None)에서 `all([])==True` 로 FAIL → None 제외. ② `S-T-gzip` 이 Accept-Encoding 을 안 보내고 원문을 받아 "압축 없음" 오판 → collect 가 `Accept-Encoding: gzip, br` 로 별도 요청해 `home_encoding_when_requested` 저장(실측 'br'). ③ Next/Image `/_next/image?url=…webp&w=` 를 WebP 로 못 셈 → 정규식 확장(18/19). ④ 렌더 실패 요청에 `net::ERR_ABORTED`(브라우저 자체 취소) 7건 포함 → 집계 제외.
- 사람 판정으로 바뀐 것: 사이트 D 조치 18 → 30(조인 자간 12 를 사람이 조치로 올림), 밑줄 24·가격 24 정당/오탐. QA PASS.

## 아홉 사이트 재점검 · 재귀 개선 (2026-09-03, 사용자 요청)

수집 스캔에서 잡은 오탐과 수정:
1. 후기 출처 `Organization "네이버 블로그"` 가 업체 노드로 잡혀 `상호 표기 불일치` P1 오탐(사이트 H) → 구체 타입(Restaurant·NailSalon…) 노드 우선, Organization 은 대체용.
2. `Review.author` 의 Person(리뷰어 닉네임)이 대표 프로필로 잡혀 `G-T-person` PASS 오탐 → 직함·소속·자격·전문분야가 있거나 업체 founder/employee 가 가리키는 Person 만.
3. `?view=site` 처럼 canonical 이 같은 변형 페이지가 제목 중복으로 잡힘(사이트 C) → canonical 기준으로 묶음.
4. 다른 URL 을 가리키는 canonical(/studio → 홈)을 P1 결함으로 잡음 → 의도된 정규화일 수 있어 참고(INFO)로.
5. 서비스업(네일)의 `hasOfferCatalog` 를 두고 "Menu 노드 없음" P1 → 음식업 아닌 경우 OfferCatalog/makesOffer 를 정답으로 인정.
6. 권장 필드 1개(hasMap) 누락이 P1 → 3개 이상일 때만 P1. description 짧은 페이지 1개가 P1 → 절반 이상·2페이지 이상일 때만 P1. 3페이지 사이트에서 OG 1페이지 누락이 P0 → 2페이지 이상이면서 절반 이상일 때만 P0(canonical 동일).
7. `G-N-verify` 가 HOLD 인데 severity P1 로 집계 → INFO. `G-N-fold` 는 상호가 로고 이미지·업종 동의어인 경우가 많아 P2.
8. 사이트맵 누락 URL 이 `S-T-sitemap-miss` 와 `S-O-discover` 에 이중 집계 → 후자는 확인하지 못한 URL 만.
9. **전송량**: br 청크 응답은 content-length 가 없어 0 MB 로 나옴(사이트 I) → Playwright `request.sizes()` 로 대체. 스크롤 사웁으로 lazy 영상까지 합산돼 37.7 MB 가 나오는 문제 → 초기 로드(`total_bytes`)와 스크롤 후(`total_bytes_after_scroll`)를 분리, 점수는 초기 로드 기준, 스크롤 추가 5 MB+ 는 `S-P-scroll` P2.
10. 보고서에서 외부 자료 참조(필살기 100·가이드북 코드)와 자가진단 채점 제거(사용자 지시).

렌더는 열 사이트를 재측정했으나 시간 때문에 2세션을 동시에 돌렸다. 그래서 TBT 는 단일 실행 대비 ±30% 흔들림(예: 휘트니스 868→1,166, 안국 614→488). 전송량·LCP·구조 지표는 안정적. 정확한 TBT 비교가 필요하면 순차 1세션으로 다시 잰다(SKILL 함정 절에 명시).

### 서브에이전트 6명 소감(narrative 단계만 수행) → 반영 (2026-09-03)

| 사이트 | 지적 | 반영 |
|---|---|---|
| 사이트 G | 콘솔 요약이 narrative 반영 전 값 · 공용 CSS 반복 안내 없음 · 관리자 페이지가 일반 페이지로 | run_all 요약을 scores.json 기준으로 · `repeated_across_pages`/`unique_hits_estimate` · `internal_page` 자동 제외 |
| 사이트 E | CSS 유래 = 규칙 정의 수(Tailwind 미사용 유틸리티) · FAQ 누락 vs 표현 차이 · Next/Image fill + CLS 0 · 렌더 3페이지뿐 | `applied_in_markup`(transition-all·text-justify 는 0건이면 오탐, 나머지는 검토) · `S-S-faq-reword` 분리 · S-I-dims 자동 PASS · `--pages all` |
| 사이트 I | design_overrides 키(룰명) vs finding_notes 키(id) 혼동 · markup/inlined grep 표준화 · 렌더 페이지 선택 문서 불일치 · cp949 콘솔 | 스키마 문서·SKILL 함정 절 갱신, 선택 기준 문서 수정 |
| 사이트 B | exit 1 을 QA 실패로 오인 · 'Preview' 가 review 매치 · hasMenu 래퍼 미해제 · PASS→FAIL 덮어쓰기 제목 그대로 | 종료코드 의미 문서화 · 단어 경계 · 래퍼 해제 · "(진단자 판정: 결함)" 접미사 + weight 1.5 |
| 사이트 H | samples 가 룰 설명 재인용이라 추상적 · design_overrides 만으로 status 자동 PASS 인데 중복 지정 · 렌더 비대칭 · cp949 | 스키마 문서에 자동 PASS 규칙 명시, 나머지는 위와 동일 |
| 사이트 C | 로컬 매장 룰이 서비스 지역형 B2B 에 안 맞음(주소·영업시간·플레이스) · 원페이지 앵커 섹션 · aria-hidden 장식 줄표 · 모바일/데스크톱 콘텐츠 분기 | `areaServed` 감지 시 HOLD · `anchor_ids` + `has_section()` · 장식 줄표 자동 정당 · `D-viewport-split` 신규 |

최종: 열 사이트 전부 narrative 포함·QA PASS·아티팩트 발행. 남은 알려진 한계: TBT 단일 측정 ±30%(2세션 동시 렌더), anti-slop samples 추상성(탐지기 한계), 커스텀 클래스로 적용된 CSS 룰은 사람이 markup 확인.

## 2026-09-03 라운드 — 카페24 메타 대통일 (실사례 유래)

| 단계 | 결과 |
|---|---|
| RED | 합성 검체(8페이지 메타 전부 홈 고정) → 수정 전 엔진은 `S-T-canonical-mis` INFO/PASS 로 통과시킴 |
| GREEN | `S-T-canonical-collapse` P0 · `S-O-title-dup` P0 승격 · `S-O-meta-hardcoded` P1 · `S-T-platform` INFO 발화 |
| REFACTOR | 조치문에 플랫폼별 수정 위치 부착 (카페24 지문 검체로 확인) |
| 회귀 | 실사이트 10건 신규 FAIL 0건, P0 총계 불변, 빌더 7/10 식별 |

자체 실수 2건 기록:
1. 회귀를 `site_check.py --out <dir>` 로 돌렸으나 `out` 은 위치 인자 → 10건 전부 미실행 상태로 "오탐 0" 을 볼 뻔했다. **exit code 를 안 본 게 원인.**
2. `detect_platform` 의 `io.open` → `io` 미임포트 NameError 가 `except Exception` 에 삼켜져 조용히 None 반환. 예외를 `OSError` 로 좁히고 실패 로그를 남김.

## 2026-09-03 스킬 업데이트 — 1군 룰 14종 반영

| 검증 | 결과 |
|---|---|
| 열 사이트 회귀 | exit 0 10/10 · 신규 오탐 0 · **SEO/GEO 점수 10곳 전부 불변** |
| 합성 검체 (링크) | 404 링크·빈 앵커 양쪽 검출 |
| 실사이트 (봇) | 사이트 A 7종 봇 전부 200 · 사람 UA 와 바이트 동일 |
| 유령 발화 | 프로브 데이터 없는 기존 폴더에서 신규 3룰 미발화 확인 |

버그 4건: ``→0x08 파일 손상(경고 없음, 틀린 PASS 유발) · `hasMenuSection` dict TypeError · `internal` 변수 충돌 · with 없는 write 의 미반영.
**교훈: exit code 를 보지 않고 산출 파일을 읽으면 이전 실행 결과를 현재 결과로 착각한다.** site-i 을 두 번 잘못 보고했다.

## 2026-09-03 렌더 단계 — a11y·에이전트·SSL·스키마대조 (룰 166)

| 검증 | 결과 |
|---|---|
| axe 실측 | 사이트 H 모바일 대비 위반 28건 — 조사 프로토타입과 일치 |
| 스키마 대조 RED | 3/10 오탐 (`+82-507` vs `0507` 표기 차) → `phone_variants()` 로 오탐 0 |
| 스키마 대조 GREEN | 합성 검체(화면에 없는 번호·주소) 2건 정상 검출 |
| 열 사이트 회귀 | exit 0 10/10 · 신규 오탐 0 · 점수 10곳 전부 불변 · 디자인 판정 불변 |
| 전체 파이프라인 | 사이트 H 완주. TLS 31일 · 봇 7종 정상 · 앵커 8건 정상 · 대비 55건 |

버그 2건: JS 문자열의 `
` 이 실제 개행으로 치환돼 JS 문법 오류 · `S-C-fresh` 앵커가 사이트맵 룰과 중복.

## 2026-09-04 OpenSEO 대조 — 룰 6개 + 보고서 구조 (172룰)

| 검증 | 결과 |
|---|---|
| 열 사이트 회귀 | exit 0 10/10 · 신규 오탐 0 · **점수 드리프트 0** |
| `S-O-no-outlink` | P2/w1 로는 site-g 가 1점 하락 → 참고(w0)로 내려 드리프트 제거 |
| `S-O-heading-order` | 2/10 발화, 전부 실제 (h2→h4, h1→h3) |
| noindex 중복 오탐 | OpenSEO `isDuplicateCandidate` 참고해 수집에서 제외 |
| 보고서 재생성 | 10/10 "이번 주에 할 일 하나" 렌더 · QA PASS |

버그 1건: `
` 이 실제 개행으로 치환돼 `site_report.py` 문법 오류. §13-4 와 같은 사고 재발. 패치 문자열은 `chr(92)+"n"` 또는 raw 로.

## 2026-09-04 다른 사람 환경에서 처음 실행 — 사전점검 개선

다른 사용자가 이 저장소를 쓰려다 두 가지에 걸렸다.

| 증상 | 원인 | 대응 |
|---|---|---|
| `naverai` 없음 | SKILL.md 가 관련 스킬로 지목하는데 그 PC 에 없음 | 어떤 스크립트도 호출하지 않는 것을 확인(grep). doctor 가 **선택** 항목으로 분리해 표시하고, 없을 때 무엇을 못 하는지 한 줄로 알려 준다 |
| `py -3` 이 다른 파이썬 | doctor 가 통과한 것은 Hermes venv 의 3.11 인데 `py -3` 은 시스템 3.14 를 가리킴 | doctor 가 `py -3`·`python`·`python3` 가 각각 무엇을 가리키는지 표로 찍고, 통과한 것과 다르면 `← 다릅니다` 를 붙인다. 마지막 줄에 복사해 쓸 전체 경로 명령을 출력 |

**틀린 요구사항도 하나 잡았다.** README 가 Python 3.12+ 를 요구했는데, 전 파일이 `from __future__ import annotations` 를 쓰고 런타임 union 이 없어 **3.10 이상이면 된다.** 3.11.15 로 전 모듈 임포트 성공을 확인했다. 잘못된 요구사항이 다른 사람을 막고 있었다.

doctor 재작성: 필수(python·playwright·chromium·node·anti-slop·axe)와 선택(naverai·thejsk·curl_cffi)을 나누고, 문제마다 **그 인터프리터 전체 경로가 박힌 해결 명령**을 준다. `--json` 은 ready 여부를 exit code 로도 낸다.
