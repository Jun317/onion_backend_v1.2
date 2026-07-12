"""빈약 이슈 발행 게이트 (export._is_substantial) + 공지성 수집 차단 regex."""
import json
import re

from engine.export import export_all

from .conftest import insert_article, insert_issue


def _insert_llm(conn, iid: str, model: str = "fake"):
    conn.execute(
        "INSERT INTO llm_output(issue_id,model,title,one_liner,why_now,details_json,"
        "effects_json,visual_type,glossary_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (iid, model, "제목", "한 줄 요약이에요", "중요해요.",
         '["첫 문장이에요.","둘째 문장이에요.","셋째 문장이에요.","넷째 문장이에요."]',
         '[]', "none", '[]'))


def test_thin_issue_excluded_from_export(conn, tmp_path):
    # 빈약: 수치 0 + 실기사 0 + LLM 가공 없음 → 제외
    insert_issue(conn, "thin", title="공지성 이슈", category="ETC", status="active")
    # 실속: 앵커 보유 → 유지
    insert_issue(conn, "rich", title="금리 이슈", category="RATE", status="active")
    conn.execute(
        "INSERT INTO numeric_anchor(issue_id,entity,metric,value,unit,period,source,"
        "observed_at) VALUES('rich','한국은행','기준금리',3.0,'%','2026-07','ECOS',"
        "'2026-07-04T00:00:00+00:00')")
    stats = export_all(conn, tmp_path / "out")
    ids = {c["id"] for c in json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))["issues"]}
    assert "rich" in ids and "thin" not in ids
    assert stats["thin_skipped"] == 1


def test_two_source_issue_kept(conn, tmp_path):
    insert_issue(conn, "duo", title="두 출처 이슈", category="GEO", status="active")
    insert_article(conn, "a1", issue_id="duo", source="s1", title="기사 하나")
    insert_article(conn, "a2", issue_id="duo", source="s2", title="기사 둘")
    export_all(conn, tmp_path / "out")
    ids = {c["id"] for c in json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))["issues"]}
    assert "duo" in ids


def test_llm_processed_issue_kept_even_if_thin(conn, tmp_path):
    insert_issue(conn, "done", title="가공된 이슈", category="ETC", status="active")
    _insert_llm(conn, "done", model="fake")
    export_all(conn, tmp_path / "out")
    ids = {c["id"] for c in json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))["issues"]}
    assert "done" in ids


def test_template_thin_issue_excluded(conn, tmp_path):
    """템플릿 폴백(가공 실패) + 소재 빈약 → 제외."""
    insert_issue(conn, "tmpl", title="폴백 이슈", category="ETC", status="active")
    _insert_llm(conn, "tmpl", model="template")
    stats = export_all(conn, tmp_path / "out")
    ids = {c["id"] for c in json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))["issues"]}
    assert "tmpl" not in ids and stats["thin_skipped"] == 1


def test_gov_rss_exclude_regex_blocks_notices():
    from engine.config import cfg
    rx = re.compile(cfg()["collect"]["gov_rss"]["exclude_regex"])
    noise = ["2026 하반기 경제정책방향 포럼 개최", "장관, 반도체 업계 간담회 개최",
             "청년 인턴십 참가자 모집 공고", "부총리 신년 인사말", "OO공사와 업무협약 체결",
             "국민 체험 행사 안내"]
    substantive = ["기준금리 0.25%p 인하 결정", "12월 소비자물가 동향",
                   "유럽산 플라스틱 원료 반덤핑 관세 부과", "외환시장 변동성 대응 방안 발표"]
    assert all(rx.search(t) for t in noise)
    assert not any(rx.search(t) for t in substantive)
