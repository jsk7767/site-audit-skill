# -*- coding: utf-8 -*-
"""5단계 · 점수. findings.json 의 weight 를 범주별로 합산해 scores.json.

py -3 site_score.py ./example-audit

SEO  0~100 : claude-seo v2.2.5 가중치 유지 (기존 10개 보고서와 비교 가능)
             기술 22 · 콘텐츠 23 · 온페이지 20 · 구조화 10 · 성능 10 · AI검색 10 · 이미지 5
GEO  0~100 : 크롤러접근 20 · 기계가독/엔티티 25 · 인용가능성 20 · 신뢰/권위 15 · 최신성 10 · NEO 네이버 10
디자인    : 점수 대신 판정 + 조치/검토 건수 (검출 다수가 정당/오탐이라 점수화는 오해를 만든다)

범주 점수 = 100 - Σ(finding.weight × 배율). 배율 P0 3, P1 2, P2 1. 바닥 0. HOLD 는 감점하지 않는다.
"이 숫자는 사이트 자체의 최적화 상태를 이 도구의 가중치로 환산한 값이지 검색 순위 예측이 아니다" · 보고서에 반드시 명시.
"""
from __future__ import annotations

import argparse
import os

from _common import apply_narrative, fix_path, load_json, log, save_json

SEO_WEIGHTS = {"technical": 22, "content": 23, "onpage": 20, "schema": 10, "performance": 10, "ai": 10, "images": 5}
GEO_WEIGHTS = {"access": 20, "entity": 25, "citability": 20, "trust": 15, "freshness": 10, "neo": 10}
MULT = {"P0": 3.0, "P1": 2.0, "P2": 1.0}
# SEO 'AI 검색' 범주는 GEO 레인의 access+citability 판정을 그대로 가져와 계산 (claude-seo 의 AI Search Readiness 10%)
SEO_AI_FROM_GEO = ("access", "citability")


def category_score(findings: list[dict], lane: str, category: str | tuple) -> tuple[float, list[dict]]:
    cats = category if isinstance(category, tuple) else (category,)
    ded = []
    total = 0.0
    for f in findings:
        if f["lane"] != lane or f["category"] not in cats or f["status"] != "FAIL":
            continue
        d = f.get("weight", 0) * MULT.get(f["severity"], 0)
        if d > 0:
            ded.append({"id": f["id"], "title": f["title"], "severity": f["severity"], "deduction": round(d, 1)})
            total += d
    return max(0.0, round(100 - total * 2.2, 1)), ded  # 2.2 배율: P0 weight 10 → -66, P1 weight 3 → -13


def main():
    ap = argparse.ArgumentParser(description="site-audit 5단계 점수")
    ap.add_argument("out")
    a = ap.parse_args()
    a.out = fix_path(a.out)
    fj = load_json(os.path.join(a.out, "findings.json"))
    if not fj:
        raise SystemExit("findings.json 없음")
    d = load_json(os.path.join(a.out, "design.json")) or {}
    nar = load_json(os.path.join(a.out, "narrative.json")) or {}
    # 사람 판정(narrative.json)이 있으면 점수에도 반영 — 보고서(site_report.py)와 같은 함수
    fs, cls_delta = apply_narrative(fj["findings"], d, nar)
    if fj.get("unreachable"):
        res = {"seo": {"total": None, "categories": {}, "weights_source": "claude-seo v2.2.5"}, "geo": {"total": None, "categories": {}},
               "design": {"verdict": "미측정", "total_hits": None, "pages": 0, "by_class": {}, "render_fails": 0, "action_items": 0, "review_items": 0},
               "self_diagnosis": {"scored": {}, "hold": [], "subtotal": 0, "max_scored": 0, "note": "접근 불가"},
               "disclaimer": "사이트에 접근하지 못해 점수를 내지 않았습니다.", "unreachable": True, "narrative_applied": bool(nar),
               "findings_summary": fj.get("summary")}
        save_json(os.path.join(a.out, "scores.json"), res)
        log("[score] 접근 불가 · 점수 없음")
        return
    seo = {}
    for cat, w in SEO_WEIGHTS.items():
        if cat == "ai":
            sc, ded = category_score(fs, "G", SEO_AI_FROM_GEO)
        else:
            sc, ded = category_score(fs, "S", cat)
        seo[cat] = {"score": sc, "weight": w, "deductions": ded}
    seo_total = round(sum(v["score"] * v["weight"] for v in seo.values()) / 100)
    geo = {}
    for cat, w in GEO_WEIGHTS.items():
        sc, ded = category_score(fs, "G", cat)
        geo[cat] = {"score": sc, "weight": w, "deductions": ded}
    geo_total = round(sum(v["score"] * v["weight"] for v in geo.values()) / 100)
    # 디자인: 판정 (narrative 의 design_overrides 로 분류 증감 반영)
    ds = d.get("summary") or {}
    by_class = dict(ds.get("by_class") or {})
    for c, n in cls_delta.items():
        by_class[c] = by_class.get(c, 0) + n
    by_class = {c: n for c, n in by_class.items() if n > 0}
    render_fails = [f for f in fs if f["lane"] == "D" and f["category"] == "render" and f["status"] == "FAIL"]
    act = by_class.get("조치", 0)
    rev = by_class.get("검토", 0)
    if not d or d.get("error"):
        verdict = "미측정"
    elif act == 0 and not render_fails:
        verdict = "깨끗함" if rev == 0 else "양호 (검토 항목만)"
    elif act <= 10 and not any(f["severity"] == "P1" for f in render_fails):
        verdict = "소폭 손질"
    else:
        verdict = "손볼 것 있음"
    design = {"verdict": verdict, "total_hits": ds.get("total_hits"), "pages": ds.get("pages"), "by_class": by_class,
              "max_severity": ds.get("max_severity"), "render_fails": len(render_fails),
              "action_items": act, "review_items": rev}
    # 자가진단표(가이드북 E) 15항목 중 사이트에서 판정 가능한 항목 자동 채점 (0~5), 나머지 HOLD
    def has_ok(prefix):
        return any(f["id"].startswith(prefix) and f["status"] == "PASS" and f["severity"] == "OK" for f in fs)
    def has_fail(prefix):
        return any(f["id"].startswith(prefix) and f["status"] == "FAIL" for f in fs)
    self_diag = {
        "NAP-상호 일치": 5 if not has_fail("G-E-name") and has_ok("G-E-phone") else (2 if has_fail("G-E-name") else None),
        "NAP-주소·전화 동일": 5 if has_ok("G-E-phone") and has_ok("G-E-addr") else (1 if has_fail("G-E-phone") else 3),
        "NAP-영업시간 구조화": 5 if has_ok("G-E-hours") else 0,
        "정보-메뉴·서비스 상세": 5 if has_ok("S-S-menu") and has_ok("S-C-price") else (2 if has_ok("S-C-price") else 0),
        "정보-소개 엔티티 정의": 4 if has_ok("G-C-lead") else 1,
        "콘텐츠-FAQ 구조화": 5 if has_ok("S-C-faq") else 0,
        "엔티티-대표 프로필": 5 if has_ok("G-T-person") else 0,
        "엔티티-스키마 마크업": 5 if has_ok("S-S-biz") else (2 if not has_fail("S-S-biz") else 0),
        "정보-사진 30장+": None, "리뷰-50개+": None, "리뷰-평점·분포": None, "리뷰-24시간 답글": None,
        "콘텐츠-주2회 발행": None, "콘텐츠-업종 전문 글": None, "엔티티-언론·블로거 노출": None,
    }
    scored = {k: v for k, v in self_diag.items() if v is not None}
    res = {"seo": {"total": seo_total, "categories": seo, "weights_source": "claude-seo v2.2.5"},
           "geo": {"total": geo_total, "categories": geo},
           "design": design,
           "self_diagnosis": {"scored": scored, "hold": [k for k, v in self_diag.items() if v is None],
                              "subtotal": sum(scored.values()), "max_scored": 5 * len(scored), "note": "가이드북 자가진단표 15항목 중 사이트에서 판정 가능한 8항목만 자동. 나머지 7항목은 플레이스·리뷰·발행 이력 확인 필요(HOLD)."},
           "disclaimer": "이 숫자는 사이트 자체의 최적화 상태를 이 도구의 가중치로 환산한 값이지, 검색 순위 예측이 아닙니다.",
           "narrative_applied": bool(nar),
           "findings_summary": {"by_severity": {k: sum(1 for x in fs if x["severity"] == k) for k in ("P0", "P1", "P2", "OK", "INFO")},
                                "by_status": {k: sum(1 for x in fs if x["status"] == k) for k in ("PASS", "FAIL", "HOLD")}}}
    save_json(os.path.join(a.out, "scores.json"), res)
    log(f"[score] SEO {seo_total} · GEO {geo_total} · 디자인 {verdict} (조치 {act} · 검토 {rev} · 렌더 FAIL {len(render_fails)})")
    log("   SEO " + " · ".join(f"{k} {v['score']}" for k, v in seo.items()))
    log("   GEO " + " · ".join(f"{k} {v['score']}" for k, v in geo.items()))


if __name__ == "__main__":
    main()
