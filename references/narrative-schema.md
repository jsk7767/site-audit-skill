# narrative.json 규격

에이전트가 6단계에서 쓰는 유일한 파일. `site_report.py` 가 읽어 보고서에 합친다. 모든 키는 선택이지만 QA 통과에는 `judgment` 가 필수다.

```json
{
  "title": "사이트 A 진단",
  "lede": "머리말 2~3문장. 무엇을 봤고 결론이 무엇인지. 숫자는 한두 개만.",
  "levers_lede": "여기서 상승폭 대부분이 나옵니다",
  "levers": [
    {"title": "사람이 없습니다", "body": "대표 소개 + Person 스키마 …", "gain": "GEO +6 · SEO +2 · 반나절", "ids": ["G-T-person", "S-C-person"]},
    {"title": "…", "body": "…", "gain": "…", "ids": ["…"]},
    {"title": "…", "body": "…", "gain": "…", "ids": ["…"]}
  ],
  "judgment": [
    "여기부터는 탐지기 결과가 아니라 제 의견입니다. …(사이트의 성격, 룰이 반대로 읽은 것)",
    "잘하고 있는 것 …",
    "다음에 볼 것 …"
  ],
  "finding_notes": {
    "S-T-verify": "구글·네이버 모두 HTML 파일 인증으로 등록돼 있음(서치콘솔 화면 확인 2026-09-03). 메타 부재는 결함이 아님.",
    "D-oversized-number": "245건 전부 원화 가격. 축약하지 않는 것이 표준."
  },
  "finding_status_overrides": {"S-T-verify": "HOLD", "S-C-price": "PASS"},
  "design_overrides": {
    "em-dash-copy": {"ko": "조치", "en": "검토", "ja": "정당", "zh": "정당", "note": "38건 전수 확인 — 전부 문장 중간"},
    "gradient-fill": {"class": "정당", "note": "사진 위 어둠 그라디언트 — 글자 가독성 장치"}
  },
  "plan": [
    {"priority": "P0", "title": "…", "todo": "…", "effort": "1시간"},
    {"priority": "P1", "title": "…", "todo": "…", "effort": "반나절"}
  ],
  "simulation_notes": {"P0 완료": "소유확인·canonical", "P1 완료": "사람·FAQ", "P2 완료": "폰트·헤더"},
  "compare_comment": "비교표 아래 한 문단.",
  "scope_measured": ["직접 측정한 것 목록 — 비우면 기계가 자동 작성"],
  "scope_not": ["확인하지 못한 것 — 비우면 자동 작성 + HOLD 항목"]
}
```

규칙

- **키 규칙**: `finding_notes`·`finding_status_overrides` 의 키는 finding id(`S-T-verify`, `D-em-dash-copy`, `D-tap-/ko/` 처럼 `D-` 접두 포함). `design_overrides` 의 키는 anti-slop **룰명**(`em-dash-copy`, `transition-all`, `D-` 없이). 둘을 섞으면 조용히 무시된다.
- `finding_status_overrides` 값은 `PASS | FAIL | HOLD`. HOLD 로 바꾸면 심각도는 INFO 로 내려가고 점수 시뮬레이션에서 빠진다. **바꾼 이유를 반드시 `finding_notes` 에 쓴다.**
- `design_overrides` 만으로 룰 전체가 오탐·정당·참고가 되면 그 `D-<룰>` finding 의 status 는 자동으로 PASS 가 된다. 같은 룰에 `finding_status_overrides` 를 또 쓸 필요 없다. `finding_status_overrides` 는 S/G 레인 finding 과 렌더 finding(`D-tap-*`, `D-keepall-*`) 에 쓴다.
- `design_overrides` 는 룰 단위 사람의 결정이다. 단일 `class` 또는 언어판별 값(`ko/en/ja/zh`, 지정 없는 언어판은 원래 분류 유지). 적용 결과: 해당 `D-<룰>` finding 의 제목이 "N건 · 조치 a · 정당 b (진단자 판정)" 으로 바뀌고 심각도(조치 ≥3 → P1, 조치·검토 있음 → P2, 참고·오탐만 → INFO, 그 외 OK)·점수·디자인 판정 카드(조치/검토 건수)가 함께 갱신된다. `note` 는 카드 본문에 "판정 메모" 로 붙는다.
- `finding_status_overrides` 와 `design_overrides` 는 `site_score.py` 와 `site_report.py` 가 같은 함수(`apply_narrative`)로 적용하므로 점수·시뮬레이션·목록이 한 기준이다. `--report-only` 가 score 를 다시 돌린다.
- `levers` 는 3개. `gain` 에는 시뮬레이션 근거(예상 점수)와 작업량을 함께.
- `judgment` 첫 문단은 "탐지기 결과가 아니라 의견" 임을 밝힌다.
- 모든 문장은 keep-all 로 렌더되므로 의미 단위로 문장을 끊는다. 긴 줄표(—) 대신 쉼표·가운뎃점.
