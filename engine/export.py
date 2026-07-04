"""정적 JSON export (설계서 §03 L3) — out/index.json + out/issues/{id}.json.

프런트(front/index.html)는 이 파일들만 fetch 한다. archived 는 제외.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import ROOT
from .db import now_iso
from .viz import build_visual

OUT = ROOT / "out"


def _issue_card(conn: sqlite3.Connection, issue: sqlite3.Row) -> dict:
    o = conn.execute("SELECT * FROM llm_output WHERE issue_id=?", (issue["id"],)).fetchone()
    return {
        "id": issue["id"],
        "one_liner": o["one_liner"] if o else issue["canonical_title"],
        "title": issue["canonical_title"],
        "category": issue["category"],
        "status": issue["status"],
        "origin": issue["origin"],
        "sources": issue["seen_sources"],
        "last_update": issue["last_update"],
        "has_visual": bool(o and o["visual_type"] and o["visual_type"] != "none"),
    }


def _issue_detail(conn: sqlite3.Connection, issue: sqlite3.Row) -> dict:
    o = conn.execute("SELECT * FROM llm_output WHERE issue_id=?", (issue["id"],)).fetchone()
    timeline = [dict(r) for r in conn.execute(
        "SELECT kind, title, source, url, at FROM timeline_entry WHERE issue_id=? "
        "ORDER BY at DESC LIMIT 30", (issue["id"],))]
    headlines = [dict(r) for r in conn.execute(
        "SELECT title, source, url, published_at FROM article WHERE issue_id=? AND is_dup=0 "
        "ORDER BY published_at DESC LIMIT 10", (issue["id"],))]
    anchors = [dict(r) for r in conn.execute(
        "SELECT entity, metric, value, unit, prev, period, source FROM numeric_anchor "
        "WHERE issue_id=? ORDER BY observed_at DESC LIMIT 12", (issue["id"],))]

    visual = None
    if o and o["visual_type"] and o["visual_type"] != "none":
        visual = build_visual(conn, o["visual_type"], issue["category"], issue["id"])

    return {
        **_issue_card(conn, issue),
        "details": json.loads(o["details_json"]) if o else [],
        "effects": json.loads(o["effects_json"]) if o else [],
        "model": o["model"] if o else None,
        "visual": visual,
        "anchors": anchors,
        "headlines": headlines,
        "timeline": timeline,
        "created_at": issue["created_at"],
    }


def export_all(conn: sqlite3.Connection, out_dir: Path | None = None) -> dict:
    out = out_dir or OUT
    (out / "issues").mkdir(parents=True, exist_ok=True)

    issues = conn.execute(
        "SELECT * FROM issue WHERE status IN ('active','stale') "
        "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, last_update DESC "
        "LIMIT 200").fetchall()

    index = {"generated_at": now_iso(),
             "attribution": "News metadata via GDELT (gdeltproject.org) · "
                            "Data: FRED, 한국은행 ECOS, DART, SEC EDGAR",
             "issues": [_issue_card(conn, i) for i in issues]}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1),
                                    encoding="utf-8")

    live_ids = set()
    for i in issues:
        live_ids.add(i["id"])
        detail = _issue_detail(conn, i)
        (out / "issues" / f"{i['id']}.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")

    # archived 이슈 상세 파일 정리
    removed = 0
    for f in (out / "issues").glob("*.json"):
        if f.stem not in live_ids:
            f.unlink()
            removed += 1
    return {"issues": len(issues), "removed": removed}
