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
