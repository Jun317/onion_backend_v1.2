"""이슈 중요도 점수 — 굵직한 뉴스(금리·대기업·정책) 우선 노출·가공.

score = 카테고리 가중치 + 개체 보너스(기관/주요기업) + 출처 보너스 + 공식 보너스.
가중치는 전부 config.yaml `importance:` 에서 조정한다 (하드코딩 금지 원칙).
사용처: export 정렬(index.json) · LLM 일일 캡 소비 순서 (handler).
"""
from __future__ import annotations

import json
import sqlite3

from .config import cfg, entity_groups


def score_issue(conn: sqlite3.Connection, issue: sqlite3.Row) -> int:
    c = cfg().get("importance", {})
    weights = c.get("category_weights", {})
    score = int(weights.get(issue["category"], weights.get("ETC", 10)))

    groups = entity_groups()
    ent_groups = {groups.get(k) for k in json.loads(issue["entity_keys"] or "[]")}
    if "institutions" in ent_groups:
        score += int(c.get("institution_bonus", 30))
    if ent_groups & {"kr_companies", "us_companies"}:
        score += int(c.get("company_bonus", 25))

    per, cap = int(c.get("source_per", 5)), int(c.get("source_cap", 25))
    score += min((issue["seen_sources"] or 0) * per, cap)

    official = issue["origin"] == "official_event" or conn.execute(
        "SELECT 1 FROM article WHERE issue_id=? AND tier='official' AND is_dup=0 LIMIT 1",
        (issue["id"],)).fetchone() is not None
    if official:
        score += int(c.get("official_bonus", 10))
    return score


def recompute_all(conn: sqlite3.Connection) -> int:
    """archived 제외 전 이슈 재계산 (멤버 추가로 개체·출처가 변하므로 매 사이클)."""
    rows = conn.execute(
        "SELECT * FROM issue WHERE status IN ('candidate','active','stale')").fetchall()
    for issue in rows:
        conn.execute("UPDATE issue SET importance=? WHERE id=?",
                     (score_issue(conn, issue), issue["id"]))
    return len(rows)
