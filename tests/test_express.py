from engine.embed import FakeEmbedder
from engine.express import anchor_key, ensure_centroids, process_event

EVENT = {
    "key": "ecos_base_rate_202607", "category": "RATE", "entity": "한국은행",
    "period": "2026-07", "title": "한국은행 기준금리 인하 (3.00%)",
    "anchors": [{"entity": "한국은행", "metric": "기준금리", "value": 3.0, "unit": "%",
                 "prev": 3.25, "period": "2026-07", "source": "ECOS"}],
    "timeline": {"kind": "official", "title": "기준금리 3.00%", "source": "ECOS",
                 "url": "https://ecos.bok.or.kr"},
}


def test_express_creates_active_frozen_issue(conn):
    iid = process_event(conn, EVENT)
    issue = conn.execute("SELECT * FROM issue WHERE id=?", (iid,)).fetchone()
    assert issue["status"] == "active"        # 발행 관문 면제
    assert issue["frozen"] == 1
    assert issue["origin"] == "official_event"
    assert issue["category"] == "RATE"
    assert issue["anchor_key"] == anchor_key(EVENT)
    assert "BOK" in issue["entity_keys"]      # 개체 사전 매칭
    assert conn.execute("SELECT COUNT(*) FROM numeric_anchor WHERE issue_id=?",
                        (iid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM timeline_entry WHERE issue_id=?",
                        (iid,)).fetchone()[0] == 1


def test_express_idempotent_key(conn):
    assert process_event(conn, EVENT) is not None
    assert process_event(conn, EVENT) is None   # 같은 키 재처리 금지
    assert conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0] == 1


def test_express_same_anchor_key_appends(conn):
    process_event(conn, EVENT)
    second = {**EVENT, "key": "ecos_base_rate_202607_수정",
              "anchors": [{"entity": "한국은행", "metric": "변동폭", "value": 0.25,
                           "unit": "%p", "prev": None, "period": "2026-07", "source": "ECOS"}]}
    process_event(conn, second)
    assert conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0] == 1   # 이슈 재사용
    assert conn.execute("SELECT COUNT(*) FROM numeric_anchor").fetchone()[0] == 2


def test_ensure_centroids(conn):
    iid = process_event(conn, EVENT)
    assert conn.execute("SELECT centroid FROM issue WHERE id=?", (iid,)).fetchone()[0] is None
    n = ensure_centroids(conn, FakeEmbedder())
    assert n == 1
    assert conn.execute("SELECT centroid FROM issue WHERE id=?", (iid,)).fetchone()[0] is not None


def test_express_headlines_create_articles(conn):
    """headlines 필드 → article 행 직접 연결 (큐레이션 이벤트용, 멱등)."""
    event = {**EVENT, "key": "seed_headline_test", "period": "2026-08",
             "headlines": [
                 {"title": "첫 기사 제목", "url": "https://a.example/1", "source": "매체A",
                  "published_at": "2026-08-01T00:00:00+00:00"},
                 {"title": "둘째 기사 제목", "url": "https://b.example/2", "source": "매체B",
                  "published_at": "2026-08-02T00:00:00+00:00"},
                 {"title": "", "url": "https://c.example/3", "source": "매체C"},  # 제목 없음 → skip
             ]}
    iid = process_event(conn, event)
    rows = conn.execute(
        "SELECT source, title FROM article WHERE issue_id=? AND is_dup=0 ORDER BY source",
        (iid,)).fetchall()
    assert [(r["source"], r["title"]) for r in rows] == [
        ("매체A", "첫 기사 제목"), ("매체B", "둘째 기사 제목")]
    # 같은 URL 재처리해도 중복 생성 없음 (다른 key 로 재실행)
    event2 = {**event, "key": "seed_headline_test_2"}
    process_event(conn, event2)
    n = conn.execute("SELECT COUNT(*) FROM article WHERE issue_id=?", (iid,)).fetchone()[0]
    assert n == 2


def test_express_uses_event_created_at(conn):
    """이벤트 created_at(과거 실날짜)이 이슈 created_at 에 반영된다."""
    event = {**EVENT, "key": "seed_created_test", "period": "2026-03",
             "created_at": "2026-03-01T00:00:00+00:00"}
    iid = process_event(conn, event)
    issue = conn.execute("SELECT created_at FROM issue WHERE id=?", (iid,)).fetchone()
    assert issue["created_at"] == "2026-03-01T00:00:00+00:00"


def test_express_headlines_set_simhash(conn):
    """헤드라인 기사에 simhash 가 설정된다 (NULL 이면 dedup 크래시 — 반드시 비-NULL)."""
    event = {**EVENT, "key": "seed_simhash_test", "period": "2026-09",
             "headlines": [{"title": "코스피 사상 최고 경신", "url": "https://a.example/sh1",
                            "source": "매체", "published_at": "2026-09-01T00:00:00+00:00"}]}
    iid = process_event(conn, event)
    row = conn.execute("SELECT simhash FROM article WHERE issue_id=? AND is_dup=0",
                       (iid,)).fetchone()
    assert row["simhash"] is not None
