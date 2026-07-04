import json

import numpy as np

from engine import cluster
from engine.embed import from_blob

from .conftest import insert_article, insert_issue, unit_vec


def test_assign_joins_above_tau_with_entity_gate(conn):
    base = unit_vec(seed=1)
    insert_issue(conn, "i1", centroid=base, entity_keys='["BOK"]', status="active")
    insert_article(conn, "a1", vec=unit_vec(base=base, noise=0.1), entity_keys='["BOK"]')
    stats = cluster.assign(conn)
    assert stats["joined"] == 1
    row = conn.execute("SELECT issue_id FROM article WHERE id='a1'").fetchone()
    assert row["issue_id"] == "i1"
    # centroid EMA 갱신 + 타임라인 + 출처 수
    issue = conn.execute("SELECT * FROM issue WHERE id='i1'").fetchone()
    assert issue["seen_sources"] == 1
    assert conn.execute("SELECT COUNT(*) FROM timeline_entry WHERE issue_id='i1'").fetchone()[0] == 1


def test_assign_entity_gate_blocks(conn):
    base = unit_vec(seed=2)
    insert_issue(conn, "i1", centroid=base, entity_keys='["FED"]', status="active")
    # 유사도는 높지만 개체 불일치 (기사 BOK vs 이슈 FED) → 편입 금지
    insert_article(conn, "a1", vec=unit_vec(base=base, noise=0.05), entity_keys='["BOK"]')
    stats = cluster.assign(conn)
    assert stats["joined"] == 0 and stats["pooled"] == 1


def test_assign_hold_band_increments_then_pools(conn):
    base = unit_vec(seed=3)
    # cos ≈ 0.78 (tau_hold 0.74 ≤ x < tau_join 0.82) 이 되도록 노이즈 조정
    v = None
    for noise in np.linspace(0.5, 1.2, 30):
        cand = unit_vec(base=base, noise=float(noise), seed=99)
        s = float(cand @ base)
        if 0.75 <= s <= 0.81:
            v = cand
            break
    assert v is not None, "보류 구간 벡터 생성 실패"
    insert_issue(conn, "i1", centroid=base, entity_keys='["BOK"]', status="active")
    insert_article(conn, "a1", vec=v, entity_keys='["BOK"]')
    s1 = cluster.assign(conn)
    assert s1["held"] == 1
    assert conn.execute("SELECT hold_count FROM article WHERE id='a1'").fetchone()[0] == 1
    s2 = cluster.assign(conn)   # 2회째 실패 → 신규 후보 풀
    assert s2["pooled"] == 1


def test_seed_greedy_pairing_creates_candidate(conn):
    base = unit_vec(seed=4)
    insert_article(conn, "a1", vec=unit_vec(base=base, noise=0.1, seed=5),
                   entity_keys='["KRX:005930"]', title="이른 기사",
                   published_at="2026-07-04T00:00:00+00:00", source="s1")
    insert_article(conn, "a2", vec=unit_vec(base=base, noise=0.1, seed=6),
                   entity_keys='["KRX:005930"]', title="늦은 기사",
                   published_at="2026-07-04T01:00:00+00:00", source="s2")
    created = cluster.seed(conn)
    assert created == 1
    issue = conn.execute("SELECT * FROM issue").fetchone()
    assert issue["status"] == "candidate" and issue["origin"] == "cluster"
    assert issue["canonical_title"] == "이른 기사"       # 더 이른 기사 제목 임시 사용
    assert set(json.loads(issue["entity_keys"])) == {"KRX:005930"}


def test_promote_requires_distinct_nondup_sources(conn):
    base = unit_vec(seed=7)
    insert_issue(conn, "i1", centroid=base, status="candidate")
    insert_article(conn, "a1", issue_id="i1", source="s1", vec=base)
    insert_article(conn, "a2", issue_id="i1", source="s1", vec=base)      # 같은 출처
    insert_article(conn, "a3", issue_id="i1", source="s2", vec=base, is_dup=1)  # 전재
    assert cluster.promote(conn) == 0    # distinct 비중복 출처 1곳뿐
    insert_article(conn, "a4", issue_id="i1", source="s3", vec=base)
    assert cluster.promote(conn) == 1
    issue = conn.execute("SELECT status, frozen FROM issue WHERE id='i1'").fetchone()
    assert issue["status"] == "active" and issue["frozen"] == 1


def test_merge_official_origin_wins(conn):
    base = unit_vec(seed=8)
    insert_issue(conn, "cl", centroid=base, status="active", origin="cluster",
                 entity_keys='["BOK"]', created_at="2026-07-01T00:00:00+00:00")
    insert_issue(conn, "ex", centroid=unit_vec(base=base, noise=0.05), status="active",
                 origin="official_event", entity_keys='["BOK"]', frozen=1,
                 created_at="2026-07-03T00:00:00+00:00")
    insert_article(conn, "a1", issue_id="cl", source="s1", vec=base)
    merged = cluster.merge(conn)
    assert merged == 1
    # 공식 origin 승자 — 나중에 생겼어도
    assert conn.execute("SELECT status FROM issue WHERE id='cl'").fetchone()[0] == "archived"
    assert conn.execute("SELECT issue_id FROM article WHERE id='a1'").fetchone()[0] == "ex"


def test_frozen_semantics_allows_member_add(conn):
    """동결 = 멤버 제거·재배정 금지. 추가·centroid 갱신은 허용 (§04-④)."""
    base = unit_vec(seed=9)
    insert_issue(conn, "i1", centroid=base, entity_keys='["BOK"]', status="active", frozen=1)
    insert_article(conn, "a1", vec=unit_vec(base=base, noise=0.1), entity_keys='["BOK"]')
    old_centroid = conn.execute("SELECT centroid FROM issue WHERE id='i1'").fetchone()[0]
    stats = cluster.assign(conn)
    assert stats["joined"] == 1
    new_centroid = conn.execute("SELECT centroid FROM issue WHERE id='i1'").fetchone()[0]
    assert not np.allclose(from_blob(old_centroid), from_blob(new_centroid))
