"""급행 경로 L2 처리 (설계서 §05) — 공식 이벤트는 군집을 건너뛴다.

L1 이 만든 data/express/*.json 을 소비:
  ① 멱등: express_processed 테이블로 재처리 방지
  ② anchor_key=(entity|category|기간버킷) 로 기존 이슈 조회 → 있으면 anchor·timeline append
  ③ 없으면 origin=official_event, status=active, frozen=1 이슈 생성 (발행 관문 면제)
  ④ centroid 는 같은 사이클 말미에 템플릿 제목 임베딩으로 보강 (ensure_centroids)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .collectors.base import DATA
from .db import now_iso
from .embed import build_input, to_blob
from .normalize import extract_entity_keys


def anchor_key(event: dict) -> str:
    return f"{event.get('entity', '')}|{event.get('category', 'ETC')}|{event.get('period', '')}"


def _issue_entity_keys(event: dict) -> list[str]:
    """이벤트 개체 → 사전 키 매칭 (기사 쪽 추출과 동일 규칙). 미등재 개체는 원문 유지."""
    keys = extract_entity_keys(f"{event.get('entity', '')} {event.get('title', '')}")
    if not keys and event.get("entity"):
        keys = [event["entity"]]
    return keys


def process_event(conn: sqlite3.Connection, event: dict) -> str | None:
    """이벤트 1건 반영. 반환: issue_id (멱등 재처리면 None)."""
    key = event.get("key", "")
    if conn.execute("SELECT 1 FROM express_processed WHERE key=?", (key,)).fetchone():
        return None

    ak = anchor_key(event)
    row = conn.execute(
        "SELECT id FROM issue WHERE anchor_key=? AND status != 'archived'", (ak,)).fetchone()
    now = now_iso()
    if row:
        iid = row["id"]
        conn.execute("UPDATE issue SET last_update=? WHERE id=?", (now, iid))
    else:
        iid = "e" + hashlib.sha1(ak.encode("utf-8")).hexdigest()[:16]
        conn.execute(
            "INSERT OR IGNORE INTO issue(id,canonical_title,category,status,origin,"
            "entity_keys,created_at,last_update,frozen,anchor_key) "
            "VALUES(?,?,?,?,?,?,?,?,1,?)",
            (iid, event.get("title", ak), event.get("category", "ETC"), "active",
             "official_event", json.dumps(_issue_entity_keys(event), ensure_ascii=False),
             now, now, ak))

    for a in event.get("anchors", []):
        conn.execute(
            "INSERT INTO numeric_anchor(issue_id,entity,metric,value,unit,period,source,"
            "trust,prev,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (iid, a.get("entity", ""), a.get("metric", ""), a.get("value"), a.get("unit", ""),
             a.get("period", ""), a.get("source", ""), "official", a.get("prev"), now))

    tl = event.get("timeline")
    if tl:
        conn.execute(
            "INSERT INTO timeline_entry(issue_id,kind,title,source,url,at) VALUES(?,?,?,?,?,?)",
            (iid, tl.get("kind", "official"), tl.get("title", ""), tl.get("source", ""),
             tl.get("url", ""), event.get("created_at") or now))

    conn.execute("INSERT INTO express_processed(key,processed_at) VALUES(?,?)", (key, now))
    return iid


def process_all(conn: sqlite3.Connection, data_dir: Path | None = None) -> int:
    d = (data_dir or DATA) / "express"
    if not d.exists():
        return 0
    n = 0
    for path in sorted(d.glob("*.json")):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if process_event(conn, event):
            n += 1
    return n


def ensure_centroids(conn: sqlite3.Connection, embedder) -> int:
    """centroid 없는 급행 이슈에 템플릿 제목 임베딩 주입 (§05-④).
    이 전까지는 §04-④ 개체 게이트만으로 기사 편입을 허용한다."""
    rows = conn.execute(
        "SELECT id, canonical_title FROM issue WHERE centroid IS NULL "
        "AND origin='official_event' AND status != 'archived'").fetchall()
    if not rows:
        return 0
    vecs = embedder.encode([build_input(r["canonical_title"], "") for r in rows])
    for r, v in zip(rows, vecs):
        conn.execute("UPDATE issue SET centroid=? WHERE id=?", (to_blob(v), r["id"]))
    return len(rows)
