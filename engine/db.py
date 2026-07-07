"""SQLite 스토리지 — 설계서 §03 DDL (5테이블 + 운영 보조 테이블).

단일 파일 engine.db. 백업 = 파일 복사. L2 워크플로가 repo 에 커밋해 상태 유지.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

DDL = """
CREATE TABLE IF NOT EXISTS article(
  id TEXT PRIMARY KEY,              -- url_hash 와 동일 (sha1 hex)
  source TEXT, tier TEXT, url TEXT, url_hash TEXT UNIQUE,
  title TEXT, lead TEXT, published_at TEXT, lang TEXT,
  simhash INTEGER,
  embedding BLOB,                   -- 384 × float16
  issue_id TEXT, is_dup INTEGER DEFAULT 0,
  entity_keys TEXT DEFAULT '[]',    -- json array
  num_tags TEXT DEFAULT '[]',       -- 제목·리드 숫자+단위 후보
  hold_count INTEGER DEFAULT 0,     -- 보류 큐 재평가 횟수 (§04-④)
  collected_at TEXT,
  last_sim REAL,                    -- 마지막 assign 의 best 코사인 (진단)
  last_sim_issue TEXT               -- 그 best 이슈 id (진단)
);
CREATE INDEX IF NOT EXISTS idx_article_issue ON article(issue_id);
CREATE INDEX IF NOT EXISTS idx_article_pub ON article(published_at);

CREATE TABLE IF NOT EXISTS issue(
  id TEXT PRIMARY KEY,
  canonical_title TEXT,
  category TEXT DEFAULT 'ETC',
  status TEXT DEFAULT 'candidate',  -- candidate|active|stale|archived
  origin TEXT DEFAULT 'cluster',    -- cluster|official_event
  centroid BLOB,
  entity_keys TEXT DEFAULT '[]',
  seen_sources INTEGER DEFAULT 0,   -- dedup 제외 distinct 원출처 수
  created_at TEXT, last_update TEXT,
  fact_hash TEXT DEFAULT '',
  frozen INTEGER DEFAULT 0,         -- 발행 동결: 멤버 제거·재배정 금지 (추가·centroid 갱신은 허용)
  anchor_key TEXT,                  -- 급행 이슈 병합 키 (entity|category|period)
  importance INTEGER DEFAULT 0      -- 중요도 점수 (importance.py, export 정렬·LLM 우선순위)
);
CREATE INDEX IF NOT EXISTS idx_issue_status ON issue(status, last_update);
CREATE INDEX IF NOT EXISTS idx_issue_anchor ON issue(anchor_key);

CREATE TABLE IF NOT EXISTS numeric_anchor(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id TEXT, entity TEXT, metric TEXT, value REAL, unit TEXT,
  period TEXT, source TEXT, trust TEXT DEFAULT 'official',
  prev REAL,                        -- 직전 값 (금리 변동 등)
  observed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_anchor_issue ON numeric_anchor(issue_id);

CREATE TABLE IF NOT EXISTS timeline_entry(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id TEXT, kind TEXT,         -- headline|official
  title TEXT, source TEXT, url TEXT, at TEXT
);
CREATE INDEX IF NOT EXISTS idx_timeline_issue ON timeline_entry(issue_id);

CREATE TABLE IF NOT EXISTS llm_output(
  issue_id TEXT PRIMARY KEY,
  fact_hash TEXT,                   -- 캐시 키: 불변이면 재호출 0 (§6-4)
  one_liner TEXT, details_json TEXT, visual_type TEXT, effects_json TEXT,
  model TEXT, created_at TEXT,
  payload_json TEXT,                -- LLM 입력 페이로드 (진단)
  raw_response TEXT,                -- LLM 원시 응답 (진단)
  validation_json TEXT,             -- 시도별 검증 오류 이력 (진단, [] = 1회 통과)
  title TEXT,                       -- 짧고 직관적인 이슈 제목 (one_liner 와 구분)
  why_now TEXT                      -- 왜 지금 중요한지 (배경지식 없는 사용자용)
);

-- 운영 보조 (설계서 DDL 외 최소 추가) --------------------------------------
CREATE TABLE IF NOT EXISTS express_processed(  -- 급행 파일 멱등 처리 기록
  key TEXT PRIMARY KEY, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS review_queue(       -- LLM 검증 최종 실패 검수 큐 (§6-4)
  issue_id TEXT PRIMARY KEY, reason TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS viz_cache(          -- visual_type 시리즈 6h 캐시 (§07)
  cache_key TEXT PRIMARY KEY, payload TEXT, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS meta(               -- LLM 일일 카운터, 실행 로그 등
  key TEXT PRIMARY KEY, value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 기존 DB 에 없을 수 있는 컬럼 (테이블, 컬럼 정의) — CREATE 는 신규 DB 만 커버
_MIGRATIONS = [
    ("article", "last_sim REAL"),
    ("article", "last_sim_issue TEXT"),
    ("issue", "importance INTEGER DEFAULT 0"),
    ("llm_output", "payload_json TEXT"),
    ("llm_output", "raw_response TEXT"),
    ("llm_output", "validation_json TEXT"),
    ("llm_output", "title TEXT"),
    ("llm_output", "why_now TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, coldef in _MIGRATIONS:
        col = coldef.split()[0]
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else ROOT / "engine.db"
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    _migrate(conn)
    return conn


# --- meta 카운터 ------------------------------------------------------------

def meta_get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def bump_daily_counter(conn: sqlite3.Connection, name: str) -> int:
    """오늘자 카운터 +1 후 값 반환 (LLM 일일 캡)."""
    key = f"{name}_{datetime.now(timezone.utc):%Y%m%d}"
    n = int(meta_get(conn, key, "0")) + 1
    meta_set(conn, key, str(n))
    return n


def daily_counter(conn: sqlite3.Connection, name: str) -> int:
    key = f"{name}_{datetime.now(timezone.utc):%Y%m%d}"
    return int(meta_get(conn, key, "0"))


# --- 프루닝 (repo 100MB 한도 보호) ------------------------------------------

def prune(conn: sqlite3.Connection) -> int:
    """archived 이슈 소속 기사의 임베딩 BLOB 제거 (텍스트·통계는 유지)."""
    cur = conn.execute(
        "UPDATE article SET embedding=NULL WHERE embedding IS NOT NULL AND issue_id IN "
        "(SELECT id FROM issue WHERE status='archived')")
    return cur.rowcount


def log_run(conn: sqlite3.Connection, kind: str, ok: bool, stats: dict, error: str = "") -> None:
    """최근 실행 기록 (meta 에 최신 20개 유지) — 관측성."""
    entry = {"kind": kind, "at": now_iso(), "ok": ok, "stats": stats, "error": error}
    hist = json.loads(meta_get(conn, "run_history", "[]"))
    hist = ([entry] + hist)[:20]
    meta_set(conn, "run_history", json.dumps(hist, ensure_ascii=False))
