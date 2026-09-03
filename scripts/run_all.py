# -*- coding: utf-8 -*-
"""전체 파이프라인 — collect → render → design → check → score → report → qa (narrative 는 에이전트가 사이 단계에서 작성).

라이브:  py -3 run_all.py https://example.kr --name "매장명" --out ./example-audit [--brand 상호 --region 지역 --category 업종]
                 [--facts facts.json] [--pages / /menu] [--extra /faq] [--max-pages 30] [--skip-render] [--skip-design] [--compare DIR ...]
로컬:    py -3 run_all.py --local ./site --base https://example.kr --name "매장명" --out ./example-audit   (렌더는 건너뜀, 배포 전 게이트)

종료코드: 0 = P0/P1 없음(게이트 통과) · 1 = P0/P1 있음 · 2 = 파이프라인 오류
--report-only 로 narrative.json 작성 후 보고서·QA 만 다시 돌릴 수 있다.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(step: str, args: list[str], timeout: int = 900) -> int:
    print(f"\n===== {step} =====", flush=True)
    t0 = time.time()
    env = dict(os.environ, MSYS_NO_PATHCONV="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run([PY, "-u", os.path.join(HERE, args[0])] + args[1:], timeout=timeout, env=env)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        print(f"   TIMEOUT {timeout}s", flush=True)
        rc = 124
    print(f"   ({round(time.time() - t0)}s, exit {rc})", flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser(description="site-audit 전체 실행")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--local")
    ap.add_argument("--base")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--brand")
    ap.add_argument("--region")
    ap.add_argument("--category")
    ap.add_argument("--facts")
    ap.add_argument("--pages", nargs="*")
    ap.add_argument("--extra", nargs="*", default=[])
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--skip-design", action="store_true")
    ap.add_argument("--compare", nargs="*", default=[])
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--pdf", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    from _common import fix_path
    a.out, a.local, a.facts = fix_path(a.out), fix_path(a.local), fix_path(a.facts)
    a.compare = [fix_path(x) for x in a.compare]
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    if not a.report_only:
        if a.local:
            if not a.base:
                ap.error("--local 에는 --base 필요")
            if run("1/6 collect (local)", ["site_collect.py", "--local", a.local, "--base", a.base, "--out", out]):
                sys.exit(2)
        else:
            if not a.url:
                ap.error("url 또는 --local")
            if run("1/6 collect", ["site_collect.py", a.url, "--out", out, "--max-pages", str(a.max_pages)] + (["--extra"] + a.extra if a.extra else [])):
                sys.exit(2)
            if not a.skip_render:
                run("2/6 render", ["site_render.py", a.url, "--out", out] + (["--pages"] + a.pages if a.pages else []), timeout=1200)
        if not a.skip_design:
            run("3/6 design", ["site_design.py", out], timeout=900)
        chk = ["site_check.py", out]
        for k in ("brand", "region", "category", "facts"):
            v = getattr(a, k)
            if v:
                chk += [f"--{k}", v]
        if run("4/6 check", chk):
            sys.exit(2)
        if run("5/6 score", ["site_score.py", out]):
            sys.exit(2)
    if a.report_only:
        # narrative.json 의 판정을 점수에도 반영해야 하므로 score 를 다시 돌린다
        if run("5/6 score (narrative 반영)", ["site_score.py", out]):
            sys.exit(2)
    rep = ["site_report.py", out, "--name", a.name]
    if a.compare:
        rep += ["--compare"] + a.compare
    if run("6/6 report", rep):
        sys.exit(2)
    qa_rc = run("QA", ["site_qa.py", out] + (["--pdf"] if a.pdf else []), timeout=600)
    import json
    with open(os.path.join(out, "scores.json"), encoding="utf-8") as f:
        s = json.load(f)
    with open(os.path.join(out, "findings.json"), encoding="utf-8") as f:
        fj = json.load(f)
    # narrative.json 이 적용된 뒤의 집계는 scores.json 에 있다 (findings.json 은 기계 판정 원본)
    fs_sum = s.get("findings_summary") or fj["summary"]
    bs = fs_sum["by_severity"]
    print("\n================ 결과 ================")
    if fj.get("unreachable"):
        print("사이트에 접근하지 못했습니다. 점수·판정 없음. findings.json 의 S-T-unreachable 근거를 보고 URL/DNS/차단을 확인하세요.")
        sys.exit(2)
    print(f"SEO {s['seo']['total']} · GEO {s['geo']['total']} · 디자인 {s['design']['verdict']} (검출 {s['design'].get('total_hits')})")
    print(f"P0 {bs['P0']} · P1 {bs['P1']} · P2 {bs['P2']} · HOLD {fs_sum['by_status']['HOLD']}" + ("  (narrative 반영 후)" if s.get("narrative_applied") else "  (기계 판정)"))
    print(f"보고서: {os.path.join(out, 'report.html')}  QA: {'PASS' if qa_rc == 0 else 'FAIL (narrative.json 작성 후 --report-only 재실행. 산출물은 전부 생성됨)'}")
    if not os.path.exists(os.path.join(out, "narrative.json")):
        print("다음 단계: findings.json·design.json·screenshots/ 를 읽고 narrative.json (levers·judgment·finding_notes·design_overrides) 작성 → run_all.py --report-only")
    sys.exit(1 if (bs["P0"] or bs["P1"]) else 0)


if __name__ == "__main__":
    main()
