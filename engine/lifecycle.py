"""생애주기 (설계서 §04-⑧) — stale 72h · archived 30d. 병합은 cluster.merge."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .config import cfg


def tick(conn: sqlite3.Connection) -> dict:
    lc = cfg()["lifecycle"]
    now = datetime.now(timezone.utc)
    stale_cut = (now - timedelta(hours=lc["stale_h"])).isoformat()
    archive_cut = (now - timedelta(days=lc["archive_d"])).isoformat()

    stale = conn.execute(
        "UPDATE issue SET status='stale' WHERE status='active' AND last_update < ?",
        (stale_cut,)).rowcount
    archived = conn.execute(
        "UPDATE issue SET status='archived' WHERE status IN ('active','stale','candidate') "
        "AND last_update < ?", (archive_cut,)).rowcount
    return {"stale": stale, "archived": archived}
