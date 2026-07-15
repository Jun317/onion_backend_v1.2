"""증분 그리디 군집 (설계서 §04-④⑤⑥⑧) — UMAP/HDBSCAN 없음, numpy 브루트포스.

- assign: 활성 이슈 centroid 와 코사인 + 개체 게이트 → 편입/보류/신규 풀
- seed:   풀 내부 그리디 페어링 → candidate 이슈 생성
- promote: 원출처 distinct ≥ min_publish → active + frozen (급행은 면제)
- merge:  centroid cos ≥ merge_sim + 공유 개체 → 공식 origin 승자
frozen 의미: 멤버 제거·재배정 금지. 멤버 추가·centroid 갱신은 허용 (§04-④).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np

from .config import cfg
from .db import now_iso
from .embed import from_blob, to_blob


# --- 공통 헬퍼 ---------------------------------------------------------------

def _l2(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-12)


def entity_gate(article_keys: list[str], issue_keys: list[str], issue_origin: str,
                allow_empty: bool) -> bool:
    """개체 게이트 (§04-④): 공유 entity_key ≥1. 급행 이슈는 개체 일치로 판정.
    양쪽 다 비어 있으면 allow_empty 설정을 따른다 (무개체 GEO 뉴스 대응)."""
    a, b = set(article_keys), set(issue_keys)
    if a & b:
        return True
    if issue_origin == "official_event":
        return False  # 급행 이슈는 개체 일치 필수 (기간은 anchor_key 에 반영됨)
    return allow_empty and not a and not b


def recount_sources(conn: sqlite3.Connection, issue_id: str) -> int:
    n = conn.execute(
        "SELECT COUNT(DISTINCT source) AS n FROM article WHERE issue_id=? AND is_dup=0",
        (issue_id,)).fetchone()["n"]
    conn.execute("UPDATE issue SET seen_sources=? WHERE id=?", (n, issue_id))
    return n


def member_count(conn: sqlite3.Connection, issue_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM article WHERE issue_id=? AND is_dup=0",
        (issue_id,)).fetchone()["n"]


def _add_member(conn: sqlite3.Connection, issue_row, article_row, alpha: float) -> None:
    """기사를 이슈에 편입: issue_id, centroid EMA, entity 합집합, 타임라인.
    멤버 ≥ centroid_freeze_members 면 centroid 를 고정 — 대형 이슈의 주제 드리프트 방지."""
    freeze_at = cfg()["cluster"].get("centroid_freeze_members", 20)
    frozen_centroid = (issue_row["centroid"] is not None
                       and member_count(conn, issue_row["id"]) >= freeze_at)
    conn.execute("UPDATE article SET issue_id=?, hold_count=0 WHERE id=?",
                 (issue_row["id"], article_row["id"]))
    v = from_blob(article_row["embedding"])
    c = from_blob(issue_row["centroid"]) if issue_row["centroid"] else v
    new_c = c if frozen_centroid else _l2((1 - alpha) * c + alpha * v)
    keys = sorted(set(json.loads(issue_row["entity_keys"] or "[]"))
                  | set(json.loads(article_row["entity_keys"] or "[]")))
    conn.execute("UPDATE issue SET centroid=?, entity_keys=?, last_update=? WHERE id=?",
                 (to_blob(new_c), json.dumps(keys, ensure_ascii=False), now_iso(),
                  issue_row["id"]))
    conn.execute(
        "INSERT INTO timeline_entry(issue_id,kind,title,source,url,at) VALUES(?,?,?,?,?,?)",
        (issue_row["id"], "official" if article_row["tier"] == "official" else "headline",
         article_row["title"], article_row["source"], article_row["url"],
         article_row["published_at"] or now_iso()))
    recount_sources(conn, issue_row["id"])


def _active_issues(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    c = cfg()["cluster"]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=c["active_window_h"])).isoformat()
    max_members = c.get("max_members", 40)
    # 정원 초과 이슈는 배정 후보에서 제외 — 메가 클러스터 성장 차단
    return conn.execute(
        "SELECT * FROM issue WHERE status IN ('candidate','active') AND last_update > ? "
        "AND centroid IS NOT NULL "
        "AND (SELECT COUNT(*) FROM article a WHERE a.issue_id = issue.id AND a.is_dup = 0) < ?",
        (cutoff, max_members)).fetchall()


def _unassigned(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    ttl_h = cfg()["lifecycle"]["seed_pool_ttl_h"]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_h)).isoformat()
    return conn.execute(
        "SELECT * FROM article WHERE issue_id IS NULL AND embedding IS NOT NULL "
        "AND collected_at >= ?", (cutoff,)).fetchall()


# --- ④ 증분 배정 -------------------------------------------------------------

def assign(conn: sqlite3.Connection) -> dict:
    c = cfg()["cluster"]
    issues = _active_issues(conn)
    articles = _unassigned(conn)
    stats = {"joined": 0, "held": 0, "pooled": 0}
    if not articles:
        return stats
    if not issues:
        stats["pooled"] = len(articles)
        return stats

    centroids = np.stack([from_blob(i["centroid"]) for i in issues])  # (I, d)
    issue_keys = [json.loads(i["entity_keys"] or "[]") for i in issues]

    for art in articles:
        v = from_blob(art["embedding"])
        sims = centroids @ v  # 정규화 벡터 → 코사인
        # 게이트 통과 이슈 중 best (미통과 이슈는 편입 불가)
        order = np.argsort(-sims)
        best_i, best_sim = -1, -1.0
        for idx in order:
            if sims[idx] < c["tau_hold"]:
                break  # 이하로는 볼 필요 없음
            if entity_gate(json.loads(art["entity_keys"] or "[]"), issue_keys[idx],
                           issues[idx]["origin"], c["entity_gate_allow_empty"]):
                best_i, best_sim = int(idx), float(sims[idx])
                break

        # 진단: 게이트 무관 전체 best 를 기록 — "왜 편입 안 됐나" 추적 (모니터링)
        raw_best = int(np.argmax(sims))
        conn.execute("UPDATE article SET last_sim=?, last_sim_issue=? WHERE id=?",
                     (round(float(sims[raw_best]), 4), issues[raw_best]["id"], art["id"]))

        if best_i >= 0 and best_sim >= c["tau_join"]:
            issue_row = conn.execute("SELECT * FROM issue WHERE id=?",
                                     (issues[best_i]["id"],)).fetchone()
            _add_member(conn, issue_row, art, c["centroid_alpha"])
            stats["joined"] += 1
        elif best_i >= 0 and best_sim >= c["tau_hold"]:
            if art["hold_count"] + 1 >= c["hold_max_tries"]:
                stats["pooled"] += 1  # 보류 만료 → 신규 후보 풀 (seed 가 처리)
            else:
                conn.execute("UPDATE article SET hold_count=hold_count+1 WHERE id=?",
                             (art["id"],))
                stats["held"] += 1
        else:
            stats["pooled"] += 1
    return stats


# --- ⑤ 신규 이슈 생성 --------------------------------------------------------

def _pool(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """seed 대상: 미배정 + (보류 소진 or 보류 미달). held 상태(재평가 대기)는 제외."""
    c = cfg()["cluster"]
    return [a for a in _unassigned(conn)
            if a["hold_count"] == 0 or a["hold_count"] >= c["hold_max_tries"]]


def seed(conn: sqlite3.Connection) -> int:
    c = cfg()["cluster"]
    pool = _pool(conn)
    if len(pool) < 2:
        return 0
    vecs = np.stack([from_blob(a["embedding"]) for a in pool])
    keys = [json.loads(a["entity_keys"] or "[]") for a in pool]
    sims = vecs @ vecs.T
    np.fill_diagonal(sims, -1.0)

    created = 0
    used: set[int] = set()
    for i in range(len(pool)):
        if i in used:
            continue
        order = np.argsort(-sims[i])
        for j in order:
            j = int(j)
            if j in used or sims[i][j] < c["tau_join"]:
                break
            if not entity_gate(keys[i], keys[j], "cluster", c["entity_gate_allow_empty"]):
                continue
            _create_cluster_issue(conn, pool[i], pool[j], c["centroid_alpha"])
            used.update((i, j))
            created += 1
            break
    return created


def _create_cluster_issue(conn: sqlite3.Connection, a, b, alpha: float) -> str:
    earlier = a if (a["published_at"] or "") <= (b["published_at"] or "") else b
    iid = "c" + hashlib.sha1(f"{a['id']}|{b['id']}".encode()).hexdigest()[:16]
    centroid = _l2(from_blob(a["embedding"]) + from_blob(b["embedding"]))
    keys = sorted(set(json.loads(a["entity_keys"] or "[]"))
                  | set(json.loads(b["entity_keys"] or "[]")))
    now = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO issue(id,canonical_title,status,origin,centroid,entity_keys,"
        "created_at,last_update) VALUES(?,?,?,?,?,?,?,?)",
        (iid, earlier["title"], "candidate", "cluster", to_blob(centroid),
         json.dumps(keys, ensure_ascii=False), now, now))
    for art in (a, b):
        conn.execute("UPDATE article SET issue_id=?, hold_count=0 WHERE id=?", (iid, art["id"]))
        conn.execute(
            "INSERT INTO timeline_entry(issue_id,kind,title,source,url,at) VALUES(?,?,?,?,?,?)",
            (iid, "official" if art["tier"] == "official" else "headline",
             art["title"], art["source"], art["url"], art["published_at"] or now))
    recount_sources(conn, iid)
    return iid


# --- ⑥ 발행 판정 -------------------------------------------------------------

def promote(conn: sqlite3.Connection) -> int:
    """candidate → active(발행) + frozen. 급행(official_event)은 §05 에서 이미 active."""
    min_publish = cfg()["cluster"]["min_publish"]
    rows = conn.execute("SELECT id FROM issue WHERE status='candidate'").fetchall()
    n = 0
    for r in rows:
        if recount_sources(conn, r["id"]) >= min_publish:
            conn.execute("UPDATE issue SET status='active', frozen=1, last_update=? WHERE id=?",
                         (now_iso(), r["id"]))
            n += 1
    return n


# --- ⑧ 병합 ------------------------------------------------------------------

def merge(conn: sqlite3.Connection) -> int:
    """active 이슈 쌍 centroid cos ≥ merge_sim AND 공유 개체 ≥1 → 병합.
    승자: official origin 우선, 없으면 오래된 쪽 (§04-⑧, §05-⑤)."""
    c = cfg()["cluster"]
    issues = conn.execute(
        "SELECT * FROM issue WHERE status='active' AND centroid IS NOT NULL").fetchall()
    if len(issues) < 2:
        return 0
    vecs = np.stack([from_blob(i["centroid"]) for i in issues])
    keys = [set(json.loads(i["entity_keys"] or "[]")) for i in issues]
    sims = vecs @ vecs.T

    merged = 0
    gone: set[int] = set()
    for i in range(len(issues)):
        for j in range(i + 1, len(issues)):
            if i in gone or j in gone:
                continue
            if sims[i][j] < c["merge_sim"] or not (keys[i] & keys[j]):
                continue
            a, b = issues[i], issues[j]
            # 병합 결과가 정원을 넘으면 스킵 — 체인 머지로 인한 메가 클러스터 방지
            if (member_count(conn, a["id"]) + member_count(conn, b["id"])
                    > c.get("max_members", 40)):
                continue
            if a["origin"] == "official_event" and b["origin"] != "official_event":
                win, lose, lose_idx = a, b, j
            elif b["origin"] == "official_event" and a["origin"] != "official_event":
                win, lose, lose_idx = b, a, i
            elif (a["created_at"] or "") <= (b["created_at"] or ""):
                win, lose, lose_idx = a, b, j
            else:
                win, lose, lose_idx = b, a, i
            _merge_into(conn, win, lose)
            gone.add(lose_idx)
            merged += 1
    return merged


def _merge_into(conn: sqlite3.Connection, win, lose) -> None:
    """패자 멤버·앵커·타임라인 이관 후 archived (frozen 이슈도 병합 이관은 허용 —
    동결은 '재군집 셔플 금지' 의미이고 병합은 §04-⑧ 명시 동작)."""
    for table in ("article", "numeric_anchor", "timeline_entry"):
        conn.execute(f"UPDATE {table} SET issue_id=? WHERE issue_id=?",  # noqa: S608
                     (win["id"], lose["id"]))
    keys = sorted(set(json.loads(win["entity_keys"] or "[]"))
                  | set(json.loads(lose["entity_keys"] or "[]")))
    conn.execute("UPDATE issue SET entity_keys=?, last_update=? WHERE id=?",
                 (json.dumps(keys, ensure_ascii=False), now_iso(), win["id"]))
    conn.execute("UPDATE issue SET status='archived', last_update=? WHERE id=?",
                 (now_iso(), lose["id"]))
    conn.execute("DELETE FROM llm_output WHERE issue_id=?", (lose["id"],))
    recount_sources(conn, win["id"])
