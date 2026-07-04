from engine.classify import PrototypeClassifier, classify_all, keyword_category
from engine.embed import FakeEmbedder, build_input

from .conftest import insert_article, insert_issue


def test_keyword_category_title_first():
    assert keyword_category("한은 기준금리 인하 결정", []) == "RATE"
    assert keyword_category("삼성전자 분기 실적 발표", []) == "EARNINGS"
    assert keyword_category("원달러 환율 급등", []) == "FX"
    assert keyword_category("아무 관련 없는 제목", ["멤버 제목에 소비자물가 있음"]) == "MACRO"
    assert keyword_category("아무 관련 없는 제목", []) is None


def test_prototype_fallback():
    e = FakeEmbedder()
    proto = PrototypeClassifier(e)
    v = e.encode([build_input("중앙은행이 기준금리를 조정했다", "")])[0]
    assert proto.classify(v) == "RATE"


def test_classify_all_skips_official(conn):
    e = FakeEmbedder()
    centroid = e.encode([build_input("기준금리 인하", "")])[0]
    insert_issue(conn, "c1", title="한은 기준금리 인하", origin="cluster",
                 category="ETC", centroid=centroid)
    insert_issue(conn, "e1", title="아무제목", origin="official_event", category="MACRO")
    insert_article(conn, "a1", issue_id="c1", title="한은 기준금리 인하")
    n = classify_all(conn, e)
    assert n == 1
    assert conn.execute("SELECT category FROM issue WHERE id='c1'").fetchone()[0] == "RATE"
    assert conn.execute("SELECT category FROM issue WHERE id='e1'").fetchone()[0] == "MACRO"
