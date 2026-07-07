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


def test_magnitude_bonus_from_anchor(conn):
    insert_issue(conn, "m", category="MARKET", status="active")   # base 55
    base = score_issue(conn, _issue(conn, "m"))
    conn.execute("INSERT INTO numeric_anchor(issue_id,metric,value,unit,observed_at) "
                 "VALUES('m','일일 상승률',90,'%','2026-07-04')")
    boosted = score_issue(conn, _issue(conn, "m"))
    assert boosted - base == 40   # 90×1.2=108 → cap 40


def test_recency_penalty_lowers_old_issue(conn):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    insert_issue(conn, "old", category="RATE", status="active", last_update=old)
    insert_issue(conn, "fresh", category="RATE", status="active")
    # 3일 경과 → (72//24)×5 = 15점 감점
    assert score_issue(conn, _issue(conn, "fresh")) - score_issue(conn, _issue(conn, "old")) == 15
