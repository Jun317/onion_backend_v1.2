"""중복 제거 (설계서 §04-②).

① url_hash 일치 → DB UNIQUE 로 자동 폐기 (insert OR IGNORE)
② 제목 simhash64 해밍 ≤ k AND 발행시각 차 ≤ 48h → 근접중복:
   저장은 하되 is_dup=1 — 이슈 출처 수 집계에서 제외 (와이어 전재 = 1표)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .config import cfg
from .util.simhash import hamming


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def insert_articles(conn: sqlite3.Connection, records: list[dict]) -> list[str]:
    """신규 기사 insert (url 중복은 무시). 반환: 실제 삽입된 id 목록."""
    inserted = []
    for r in records:
        cur = conn.execute(
            "INSERT OR IGNORE INTO article(id,source,tier,url,url_hash,title,lead,"
            "published_at,lang,simhash,entity_keys,num_tags,collected_at) "
            "VALUES(:id,:source,:tier,:url,:url_hash,:title,:lead,"
            ":published_at,:lang,:simhash,:entity_keys,:num_tags,:collected_at)", r)
        if cur.rowcount:
            inserted.append(r["id"])
    return inserted


def mark_near_duplicates(conn: sqlite3.Connection, new_ids: list[str]) -> int:
    """신규 기사 vs 최근 창 기존 기사 simhash 비교 → is_dup 마킹."""
    if not new_ids:
        return 0
    d = cfg()["dedup"]
    k, window_h = d["simhash_k"], d["dup_window_h"]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_h * 2)).isoformat()

    pool = conn.execute(
        "SELECT id, simhash, published_at FROM article "
        "WHERE (published_at >= ? OR published_at IS NULL)", (cutoff,)).fetchall()
    new_set = set(new_ids)
    olds = [r for r in pool if r["id"] not in new_set]
    news = [r for r in pool if r["id"] in new_set]

    marked = 0
    # 신규↔기존 + 신규↔신규 (뒤 기사가 dup) 비교. 규모: 48h 창 수천 건 → O(n·m) 허용.
    for i, a in enumerate(news):
        ta = _parse_ts(a["published_at"])
        for b in olds + news[:i]:
            if hamming(a["simhash"], b["simhash"]) > k:
                continue
            tb = _parse_ts(b["published_at"])
            if ta and tb and abs((ta - tb).total_seconds()) > window_h * 3600:
                continue
            conn.execute("UPDATE article SET is_dup=1 WHERE id=?", (a["id"],))
            marked += 1
            break
    return marked
