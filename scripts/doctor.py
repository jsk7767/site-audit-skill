# -*- coding: utf-8 -*-
"""0단계 — 사전점검.

이 스크립트를 실행한 **바로 그 인터프리터**로 나머지도 돌려야 한다.
`py -3` 이나 `python` 이 다른 파이썬을 가리키는 환경이 흔해서(가상환경·Windows 런처),
여기서 검사에 통과한 실행 파일 경로를 그대로 찍어 준다. run_all.py 는 sys.executable 로
하위 스크립트를 부르므로, 한 번 올바른 인터프리터로 시작하면 그 뒤는 알아서 따라간다.

    <검사에 통과한 python> doctor.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

MIN_PY = (3, 10)


def _which_version(cmd: list[str]) -> tuple[str, str] | None:
    """명령이 실제로 어떤 파이썬을 가리키는지. (버전, 실행파일경로)"""
    try:
        p = subprocess.run(cmd + ["-c", "import sys;print(sys.version.split()[0]);print(sys.executable)"],
                           capture_output=True, text=True, timeout=25)
        out = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()]
        if p.returncode == 0 and len(out) >= 2:
            return out[0], out[1]
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    exe = sys.executable
    r: dict = {"python": sys.version.split()[0], "python_exe": exe}
    problems: list[str] = []
    notes: list[str] = []

    if sys.version_info < MIN_PY:
        problems.append(f"Python {'.'.join(map(str, MIN_PY))} 이상이 필요합니다 (지금 {r['python']})")

    # --- 필수: playwright + chromium ---
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        r["playwright"] = True
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                r["chromium"] = b.version
                b.close()
        except Exception as e:
            r["chromium"] = f"ERROR {str(e)[:120]}"
            problems.append(f'chromium 없음 → "{exe}" -m playwright install chromium')
    except Exception:
        r["playwright"] = False
        problems.append(f'playwright 없음 → "{exe}" -m pip install playwright && "{exe}" -m playwright install chromium')

    # --- 필수: anti-slop (Node/npx) ---
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    r["node"] = shutil.which("node") or "없음"
    if npx:
        try:
            p = subprocess.run([npx, "-y", "@gessobuild/anti-slop", "--version"],
                               capture_output=True, text=True, timeout=180)
            line = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
            r["anti_slop"] = line[-1][:40] if line else "실행됨"
            if p.returncode != 0:
                r["anti_slop"] = f"ERROR rc={p.returncode}"
                problems.append("anti-slop 실행 실패 → npx -y @gessobuild/anti-slop --version 로 확인")
        except Exception as e:
            r["anti_slop"] = f"ERROR {str(e)[:80]}"
            problems.append("anti-slop 실행 실패 (첫 실행은 npx 설치로 30초 넘게 걸립니다)")
    else:
        r["anti_slop"] = "npx 없음"
        problems.append("Node.js 필요 → https://nodejs.org 설치 후 npx 사용 가능")

    # --- 필수: 동봉 자산 ---
    here = os.path.dirname(os.path.abspath(__file__))
    axe = os.path.join(os.path.dirname(here), "vendor", "axe.min.js")
    r["axe_core"] = os.path.exists(axe)
    if not r["axe_core"]:
        problems.append(f"vendor/axe.min.js 없음 → 접근성 룰(D-a11y-*)이 HOLD 로 빠집니다. 저장소에서 다시 받으세요")

    # --- 선택: 같은 계정의 다른 스킬. 없어도 진단은 끝까지 돈다 ---
    home = os.path.expanduser("~/.claude/skills")
    r["naverai"] = os.path.exists(os.path.join(home, "naverai", "naver_ai_overview.py"))
    r["thejsk"] = os.path.exists(os.path.join(home, "thejsk", "scripts", "export_pdf.py"))
    if not r["naverai"]:
        notes.append("naverai 없음 — 네이버 AI 브리핑·AiRS 실측을 못 합니다. "
                     "GEO 점수와 나머지 룰은 그대로 나오고, 보고서 '확인하지 못한 것' 에 그 사실을 적으면 됩니다.")
    if not r["thejsk"]:
        notes.append("thejsk 없음 — 네이버 플레이스 정밀 대조와 PDF 내보내기를 못 합니다. 보고서 HTML 은 정상입니다.")
    try:
        import curl_cffi  # noqa: F401
        r["curl_cffi"] = True
    except Exception:
        r["curl_cffi"] = False

    r["ready"] = not problems
    r["problems"] = problems
    r["notes"] = notes

    # --- 인터프리터 혼동 경고 ---
    aliases = {}
    for name, cmd in (("py -3", ["py", "-3"]), ("python", ["python"]), ("python3", ["python3"])):
        if shutil.which(cmd[0]):
            got = _which_version(cmd)
            if got:
                aliases[name] = {"version": got[0], "exe": got[1], "same": os.path.normcase(got[1]) == os.path.normcase(exe)}
    r["aliases"] = aliases
    r["run_with"] = exe

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        raise SystemExit(0 if r["ready"] else 1)

    print("필수")
    for k in ("python", "python_exe", "playwright", "chromium", "node", "anti_slop", "axe_core"):
        print(f"  {k:<14} {r.get(k)}")
    print("선택 (없어도 진단은 끝까지 돕니다)")
    for k in ("naverai", "thejsk", "curl_cffi"):
        print(f"  {k:<14} {r.get(k)}")

    if aliases:
        print("\n이 컴퓨터에서 각 명령이 가리키는 파이썬")
        for name, v in aliases.items():
            mark = "  ← 이것과 같음" if v["same"] else "  ← 다릅니다"
            print(f"  {name:<9} {v['version']:<9} {v['exe']}{mark}")
        if any(not v["same"] for v in aliases.values()):
            print("  * 다른 것을 쓰면 playwright 가 없다고 나옵니다. 아래 경로로 실행하세요.")

    if notes:
        print("\n참고")
        for n in notes:
            print(f"  - {n}")

    if problems:
        print("\nNOT READY — 먼저 해결할 것")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)

    print("\nREADY. 이 인터프리터로 실행하세요:")
    print(f'  "{exe}" "{os.path.join(here, "run_all.py")}" <URL> --name "매장명" --out ./example-audit')
    print("  (run_all.py 가 하위 스크립트를 같은 인터프리터로 부릅니다)")


if __name__ == "__main__":
    main()
