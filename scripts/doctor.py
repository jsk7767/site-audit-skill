# -*- coding: utf-8 -*-
"""0단계 — 사전점검. Python · playwright chromium · Node/npx anti-slop · (선택) curl_cffi/thejsk export_pdf 를 확인한다.

py -3 doctor.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = {"python": sys.version.split()[0], "python_exe": sys.executable}
    try:
        from playwright.sync_api import sync_playwright  # noqa
        r["playwright"] = True
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                r["chromium"] = b.version
                b.close()
        except Exception as e:
            r["chromium"] = f"ERROR {str(e)[:120]} → py -3 -m playwright install chromium"
    except Exception as e:
        r["playwright"] = f"ERROR {e} → py -3 -m pip install playwright && py -3 -m playwright install chromium"
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    r["node"] = shutil.which("node") or "없음"
    if npx:
        try:
            p = subprocess.run([npx, "-y", "@gessobuild/anti-slop", "--version"], capture_output=True, text=True, timeout=120)
            r["anti_slop"] = (p.stdout or p.stderr).strip().splitlines()[-1][:40]
        except Exception as e:
            r["anti_slop"] = f"ERROR {e}"
    else:
        r["anti_slop"] = "npx 없음 (Node.js 설치 필요)"
    try:
        import curl_cffi  # noqa
        r["curl_cffi"] = True
    except Exception:
        r["curl_cffi"] = False
    r["thejsk_export_pdf"] = os.path.exists(os.path.expanduser("~/.claude/skills/thejsk/scripts/export_pdf.py"))
    r["naverai"] = os.path.exists(os.path.expanduser("~/.claude/skills/naverai/naver_ai_overview.py"))
    ok = r.get("playwright") is True and isinstance(r.get("chromium"), str) and not str(r.get("chromium")).startswith("ERROR") and "ERROR" not in str(r.get("anti_slop")) and "없음" not in str(r.get("anti_slop"))
    r["ready"] = ok
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        for k, v in r.items():
            print(f"  {k:18} {v}")
        print("READY" if ok else "NOT READY: 위 ERROR 항목을 먼저 해결")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
