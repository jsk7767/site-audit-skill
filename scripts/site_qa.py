# -*- coding: utf-8 -*-
"""8단계 — 보고서 QA. report.html 을 Chromium 으로 열어 1000px/390px 가로 넘침·섹션·판단 미작성·콘솔 오류를 검사하고
라이트/다크 스크린샷을 qa/ 에 남긴다. --pdf 를 주면 thejsk export_pdf.py 로 A4 PDF 도 만든다.

py -3 site_qa.py ./example-audit [--pdf]
종료코드 0 = 통과, 1 = 실패 (완료 보고 전 반드시 0 확인)
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from _common import fix_path, log, save_json


def main():
    ap = argparse.ArgumentParser(description="site-audit 8단계 보고서 QA")
    ap.add_argument("out")
    ap.add_argument("--pdf", action="store_true")
    a = ap.parse_args()
    a.out = fix_path(a.out)
    html_path = os.path.join(a.out, "report.html")
    if not os.path.exists(html_path):
        log("[qa] report.html 없음")
        sys.exit(1)
    qa_dir = os.path.join(a.out, "qa")
    os.makedirs(qa_dir, exist_ok=True)
    from playwright.sync_api import sync_playwright
    url = pathlib.Path(html_path).resolve().as_uri()
    res = {"checks": [], "pass": True}

    def chk(name, ok, detail=""):
        res["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            res["pass"] = False
        log(f"   {'PASS' if ok else 'FAIL'}  {name}  {detail}")

    with sync_playwright() as p:
        b = p.chromium.launch()
        for label, w, scheme in (("desktop-light", 1000, "light"), ("desktop-dark", 1000, "dark"), ("mobile-390", 390, "light")):
            ctx = b.new_context(viewport={"width": w, "height": 1100}, color_scheme=scheme, device_scale_factor=1)
            pg = ctx.new_page()
            errs = []
            pg.on("console", lambda m: errs.append(m.text[:120]) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
            pg.goto(url, wait_until="load", timeout=60000)
            pg.wait_for_timeout(1500)
            m = pg.evaluate("""() => ({
                overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
                scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth,
                sections: document.querySelectorAll('section').length,
                findings: document.querySelectorAll('.finding').length,
                tables: document.querySelectorAll('table').length,
                unwritten: !!document.querySelector('.judg .warn'),
                imgsBroken: [...document.images].filter(i => i.complete && i.naturalWidth === 0).length,
                title: document.title,
                h2: [...document.querySelectorAll('h2')].map(h => h.textContent.trim().slice(0, 30)),
            })""")
            pg.screenshot(path=os.path.join(qa_dir, f"{label}.png"))
            chk(f"{label} 가로 넘침 없음", not m["overflow"], f"scrollW={m['scrollW']} clientW={m['clientW']}")
            chk(f"{label} 콘솔 오류 0", not errs, "; ".join(errs[:3]))
            if label == "desktop-light":
                chk("섹션 8개 이상", m["sections"] >= 8, f"sections={m['sections']} h2={m['h2']}")
                chk("finding 카드 존재", m["findings"] > 0, f"findings={m['findings']}")
                chk("깨진 이미지 0", m["imgsBroken"] == 0, f"broken={m['imgsBroken']}")
                chk("룰셋 밖의 판단 작성됨 (narrative.json)", not m["unwritten"], "판단 미작성 경고가 보고서에 남아 있음" if m["unwritten"] else "")
                # 비밀·로컬 경로 노출
                src = open(html_path, encoding="utf-8").read()
                leak = [k for k in ("C:\\Users", "C:/Users", "SRV_PW", "password", "Bearer ") if k in src]
                chk("로컬 경로·비밀 노출 0", not leak, ", ".join(leak))
            ctx.close()
        b.close()
    if a.pdf:
        exp = os.path.expanduser("~/.claude/skills/thejsk/scripts/export_pdf.py")
        if os.path.exists(exp):
            pdf = os.path.join(a.out, "report.pdf")
            r = subprocess.run([sys.executable, exp, "--html", html_path, "--output", pdf, "--qa-json", os.path.join(qa_dir, "pdf-qa.json")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            chk("PDF 생성", r.returncode == 0 and os.path.exists(pdf), (r.stdout or r.stderr)[-200:])
        else:
            chk("PDF 생성", False, "thejsk export_pdf.py 없음")
    save_json(os.path.join(qa_dir, "qa.json"), res)
    log(f"[qa] {'PASS' if res['pass'] else 'FAIL'} → {qa_dir}")
    sys.exit(0 if res["pass"] else 1)


if __name__ == "__main__":
    main()
