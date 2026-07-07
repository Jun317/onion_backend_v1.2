from engine.classify import PrototypeClassifier, classify_all, keyword_category
from engine.embed import FakeEmbedder, build_input

from .conftest import insert_article, insert_issue


def test_keyword_category_title_first():
    assert keyword_category("한은 기준금리 인하 결정", []) == "RATE"
    assert keyword_category("삼성전자 분기 실적 발표", []) == "EARNINGS"
    assert keyword_category("원달러 환율 급등", []) == "FX"
    assert keyword_category("아무 관련 없는 제목", ["멤버 제목에 소비자물가 있음"]) == "MACRO"
    assert keyword_category("아무 관련 없는 제목", []) is None


def test_keyword_weighted_scoring():
    # '환율'(FX)·'급등'(MARKET) 둘 다 걸리지만 FX 키워드가 더 많이 매칭 → FX
    assert keyword_category("원달러 환율 급등, 달러 강세", []) == "FX"
    # 제목 우선: 제목이 RATE 를 맞추면, 멤버에 MACRO 키워드가 여러 개여도 RATE 유지
    assert keyword_category("기준금리 결정", ["소비자물가 상승", "인플레이션 우려"]) == "RATE"
    # 제목이 아무것도 못 맞추면 멤버로 폴백
    assert keyword_category("속보 정리", ["코스피 급등 마감"]) == "MARKET"


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
