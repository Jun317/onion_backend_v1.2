from engine.db import now_iso
from engine.dedup import insert_articles, mark_near_duplicates
from engine.util.simhash import hamming, simhash64


def _rec(aid, title, source="src", published_at=None):
    return {"id": aid, "source": source, "tier": "wire", "url": f"https://x.com/{aid}",
            "url_hash": aid, "title": title, "lead": "", "published_at": published_at or now_iso(),
            "lang": "ko", "simhash": simhash64(title), "entity_keys": "[]", "num_tags": "[]",
            "collected_at": now_iso()}


def test_simhash_variants_close_and_topics_far():
    a = "한국은행, 기준금리 0.25%p 인하해 연 3.00%로"
    b = "한국은행, 기준금리 0.25%p 인하해 연 3.00%로."   # 문장부호만 상이
    c = "삼성전자 2분기 영업이익 10조원 돌파"
    assert hamming(simhash64(a), simhash64(b)) <= 3
    assert hamming(simhash64(a), simhash64(c)) > 3


def test_url_hash_dedup(conn):
    r = _rec("same", "제목")
    assert insert_articles(conn, [r]) == ["same"]
    assert insert_articles(conn, [r]) == []   # UNIQUE → 무시


def test_near_duplicate_marked(conn):
    t = "한국은행, 기준금리 0.25%p 인하해 연 3.00%로"
    insert_articles(conn, [_rec("orig", t)])
    new = insert_articles(conn, [_rec("copy", t + "."), _rec("other", "글로벌 해운 운임 하락")])
    marked = mark_near_duplicates(conn, new)
    assert marked == 1
    assert conn.execute("SELECT is_dup FROM article WHERE id='copy'").fetchone()[0] == 1
    assert conn.execute("SELECT is_dup FROM article WHERE id='other'").fetchone()[0] == 0
