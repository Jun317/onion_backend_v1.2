import json

from engine.importance import recompute_all, score_issue

from .conftest import insert_article, insert_issue, unit_vec


def _issue(conn, iid):
    return conn.execute("SELECT * FROM issue WHERE id=?", (iid,)).fetchone()


def test_rate_with_institution_outscores_minor_earnings(conn):
    # 금리 이슈: RATE(100) + 기관(30) + 급행 공식(10) + 출처 보너스
    insert_issue(conn, "rate", category="RATE", status="active",
                 origin="official_event", entity_keys='["BOK"]')
    conn.execute("UPDATE issue SET seen_sources=3 WHERE id='rate'")
    # 미등재 중소기업 실적 군집: EARNINGS(40) + 출처 1
    insert_issue(conn, "minor", category="EARNINGS", status="active",
                 entity_keys='["듀켐바이오"]')
    conn.execute("UPDATE issue SET seen_sources=1 WHERE id='minor'")

    s_rate = score_issue(conn, _issue(conn, "rate"))
    s_minor = score_issue(conn, _issue(conn, "minor"))
    assert s_rate == 100 + 30 + 15 + 10
    assert s_minor == 40 + 5
    assert s_rate > s_minor


def test_major_company_bonus_and_official_member(conn):
    insert_issue(conn, "i1", category="EARNINGS", status="active",
                 entity_keys='["KRX:005930"]')
    conn.execute("UPDATE issue SET seen_sources=2 WHERE id='i1'")
    insert_article(conn, "a1", tier="official", issue_id="i1", vec=unit_vec(seed=1))
    # EARNINGS(40) + 주요기업(25) + 출처 2×5 + official 멤버(10)
    assert score_issue(conn, _issue(conn, "i1")) == 40 + 25 + 10 + 10


def test_source_bonus_capped(conn):
    insert_issue(conn, "i1", category="ETC", status="active")
    conn.execute("UPDATE issue SET seen_sources=99 WHERE id='i1'")
    assert score_issue(conn, _issue(conn, "i1")) == 10 + 25  # cap 25


def test_recompute_all_writes_and_skips_archived(conn):
    insert_issue(conn, "a", category="RATE", status="active", entity_keys='["FED"]')
    insert_issue(conn, "b", category="ETC", status="archived")
    n = recompute_all(conn)
    assert n == 1
    assert _issue(conn, "a")["importance"] > 0
    assert _issue(conn, "b")["importance"] == 0
