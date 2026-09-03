# -*- coding: utf-8 -*-
"""9단계 — 비교. 여러 감사 폴더의 scores.json 을 표로 (N사이트 비교) 또는 같은 사이트의 전/후 델타 (재진단).

py -3 site_compare.py ./a-audit ./b-audit ./c-audit            # 비교표 → compare.md/json (첫 폴더에 저장)
py -3 site_compare.py ./before-audit ./after-audit --delta      # 재진단: 항목별 해소/신규/유지
"""
from __future__ import annotations

import argparse
import os

from _common import fix_path, load_json, log, save_json


def row(d: str) -> dict:
    s = load_json(os.path.join(d, "scores.json")) or {}
    c = load_json(os.path.join(d, "collect.json")) or {}
    r = load_json(os.path.join(d, "render.json")) or {}
    f = load_json(os.path.join(d, "findings.json")) or {}
    m = ((r.get("pages") or {}).get("/") or next(iter((r.get("pages") or {}).values()), {})).get("mobile_4x") or {}
    return {"dir": d, "host": c.get("host") or os.path.basename(d), "seo": (s.get("seo") or {}).get("total"), "geo": (s.get("geo") or {}).get("total"),
            "design": (s.get("design") or {}).get("verdict"), "design_hits": (s.get("design") or {}).get("total_hits"),
            "pages": len(c.get("pages") or {}), "body_chars": c.get("body_chars_total"),
            "mobile_mb": round((m.get("total_bytes") or 0) / 1048576, 2), "lcp": round(m.get("lcp") or 0), "tbt": round(m.get("tbt") or 0),
            "p0": (f.get("summary") or {}).get("by_severity", {}).get("P0"), "p1": (f.get("summary") or {}).get("by_severity", {}).get("P1"),
            "checked_at": f.get("checked_at")}


def main():
    ap = argparse.ArgumentParser(description="site-audit 9단계 비교")
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--delta", action="store_true", help="같은 사이트 전/후 재진단 비교 (2개 폴더)")
    a = ap.parse_args()
    a.dirs = [fix_path(d) for d in a.dirs]
    if a.delta:
        if len(a.dirs) != 2:
            raise SystemExit("--delta 는 폴더 2개 (전, 후)")
        before = load_json(os.path.join(a.dirs[0], "findings.json")) or {}
        after = load_json(os.path.join(a.dirs[1], "findings.json")) or {}
        b = {f["id"]: f for f in before.get("findings", []) if f["status"] == "FAIL"}
        af = {f["id"]: f for f in after.get("findings", []) if f["status"] == "FAIL"}
        resolved = [b[k] for k in b if k not in af]
        new = [af[k] for k in af if k not in b]
        kept = [af[k] for k in af if k in b]
        rb, ra = row(a.dirs[0]), row(a.dirs[1])
        lines = [f"# 재진단 델타 · {ra['host']}", "", f"| | 전 ({rb['checked_at']}) | 후 ({ra['checked_at']}) |", "|---|---|---|",
                 f"| SEO | {rb['seo']} | {ra['seo']} |", f"| GEO | {rb['geo']} | {ra['geo']} |", f"| 디자인 | {rb['design']} ({rb['design_hits']}) | {ra['design']} ({ra['design_hits']}) |",
                 f"| 모바일 전송 | {rb['mobile_mb']} MB | {ra['mobile_mb']} MB |", f"| LCP / TBT | {rb['lcp']} / {rb['tbt']} ms | {ra['lcp']} / {ra['tbt']} ms |", "",
                 f"## 해소 {len(resolved)}", *[f"- {f['severity']} {f['title']}" for f in resolved], "",
                 f"## 신규 {len(new)}", *[f"- {f['severity']} {f['title']}" for f in new], "",
                 f"## 유지 {len(kept)}", *[f"- {f['severity']} {f['title']}" for f in kept]]
        out = os.path.join(a.dirs[1], "delta.md")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        save_json(os.path.join(a.dirs[1], "delta.json"), {"before": rb, "after": ra, "resolved": resolved, "new": new, "kept": kept})
        log(f"[compare] 해소 {len(resolved)} · 신규 {len(new)} · 유지 {len(kept)} → {out}")
        return
    rows = sorted([row(d) for d in a.dirs], key=lambda x: -(x["seo"] or 0))
    lines = ["| 사이트 | SEO | GEO | 디자인 | 페이지 | 본문 | 모바일 전송 | LCP | TBT | P0/P1 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['host']} | {r['seo']} | {r['geo']} | {r['design']} ({r['design_hits']}) | {r['pages']} | {(r['body_chars'] or 0):,} | {r['mobile_mb']} MB | {r['lcp']} | {r['tbt']} | {r['p0']}/{r['p1']} |")
    md = "\n".join(lines)
    out = os.path.join(a.dirs[0], "compare.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(md)
    save_json(os.path.join(a.dirs[0], "compare.json"), rows)
    print(md)
    log(f"[compare] → {out}")


if __name__ == "__main__":
    main()
