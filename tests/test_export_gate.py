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


def test_steady_v2_latest_match_and_table(conn, tmp_path):
    """steady latest_match 자동 해석 + impact·table 전달 (steady.yaml 실파일 기반)."""
    # 코스피 매칭용 발행 이슈 (최신)
    insert_issue(conn, "k1", title="코스피 사상 최고 경신", category="MARKET",
                 status="active", last_update="2026-07-01T00:00:00+00:00")
    _insert_llm(conn, "k1")
    insert_issue(conn, "k2", title="코스피 8,400 돌파", category="MARKET",
                 status="stale", last_update="2026-05-27T00:00:00+00:00")
    _insert_llm(conn, "k2")
    export_all(conn, tmp_path / "out")
    idx = json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))
    steady = {s["id"]: s for s in idx["steady"]}
    rally = steady["kospi-rally"]
    assert rally["latest_issue"]["id"] == "k1"          # 최신(last_update) 우선
    assert rally["impact"] and all(isinstance(s, str) for s in rally["impact"])
    assert rally["table"]["columns"] and rally["table"]["rows"]
    assert rally["table"]["source"]                      # 수치 출처 표기
    # 매칭 이슈가 없는 주제는 latest_issue null (섹션 숨김)
    assert steady["china-taiwan"]["latest_issue"] is None


def test_english_untranslated_hidden(conn, tmp_path):
    """template 폴백 + 영어 제목 이슈는 피드에서 숨김 (번역되면 재노출)."""
    insert_issue(conn, "eng", title="Insider Selling : Qualcomm", category="MARKET",
                 status="active", entity_keys='["QCOM"]')
    conn.execute("INSERT INTO llm_output(issue_id,model,title) VALUES('eng','template','Insider Selling : Qualcomm')")
    # 같은 이슈가 한국어로 가공되면 유지
    insert_issue(conn, "kor", title="퀄컴 실적 발표", category="EARNINGS",
                 status="active", entity_keys='["QCOM"]')
    conn.execute("INSERT INTO llm_output(issue_id,model,title,one_liner,why_now,details_json,effects_json,visual_type,glossary_json)"
                 " VALUES('kor','llama','퀄컴 실적','퀄컴이 실적을 발표했어요','중요해요.','[]','[]','none','[]')")
    export_all(conn, tmp_path / "out")
    ids = {c["id"] for c in json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))["issues"]}
    assert "eng" not in ids and "kor" in ids


def test_offtopic_etc_excluded(conn, tmp_path):
    """개체·앵커 없는 ETC 이슈는 경제 무관으로 보고 제외."""
    insert_issue(conn, "off", title="여행 성수기 항공권 안내", category="ETC",
                 status="active", entity_keys='[]')
    conn.execute("INSERT INTO llm_output(issue_id,model,title) VALUES('off','template','여행 성수기 항공권 안내')")
    stats = export_all(conn, tmp_path / "out")
    ids = {c["id"] for c in json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))["issues"]}
    assert "off" not in ids


def test_event_at_from_timeline(conn, tmp_path):
    """v3: 카드의 event_at 은 타임라인 최신 사건 시각 — 처리 시각과 분리 (P0-3)."""
    insert_issue(conn, "ev", title="금리 이슈", category="RATE", status="active")
    conn.execute(
        "INSERT INTO numeric_anchor(issue_id,entity,metric,value,unit,period,source,"
        "observed_at) VALUES('ev','한국은행','기준금리',3.0,'%','2026-07','ECOS',"
        "'2026-07-10T09:00:00+00:00')")
    conn.execute(
        "INSERT INTO timeline_entry(issue_id,kind,title,source,url,at) "
        "VALUES('ev','official','기준금리 발표','ECOS','','2026-07-10T09:00:00+00:00')")
    export_all(conn, tmp_path / "out")
    index = json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))
    card = next(c for c in index["issues"] if c["id"] == "ev")
    assert card["event_at"] == "2026-07-10T09:00:00+00:00"
    assert card["event_at"] != card["last_update"]
    assert index["schema_version"] == 3


def _kospi_issue(conn, iid: str, anchor_value: float):
    from engine.db import now_iso
    insert_issue(conn, iid, title="코스피 이슈", category="MARKET", status="active")
    conn.execute(
        "INSERT INTO llm_output(issue_id,model,title,one_liner,why_now,details_json,"
        "effects_json,visual_type,glossary_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (iid, "fake", "코스피 소식", "코스피가 움직였어요", "중요해요.",
         '["첫 문장이에요.","둘째 문장이에요.","셋째 문장이에요."]', '[]',
         "kospi_close", '[]'))
    conn.execute(
        "INSERT INTO numeric_anchor(issue_id,entity,metric,value,unit,period,source,trust,"
        "observed_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (iid, "코스피", "코스피 지수", anchor_value, "pt", "2026-07", "KRX", "official",
         "2026-07-14T00:00:00+00:00"))
    series = [{"t": f"2026-07-{d:02d}", "v": 6800.0} for d in range(1, 8)]
    conn.execute(
        "INSERT INTO viz_cache(cache_key,payload,fetched_at) VALUES('kospi_close',?,?) "
        "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload",
        (json.dumps({"type": "kospi_close", "chart": "line", "title": "KOSPI 종가",
                     "unit": "pt", "source": "금융위 시세정보", "series": series},
                    ensure_ascii=False), now_iso()))


def test_visual_consistency_gate_drops_conflicting_chart(conn, tmp_path):
    """헤드라인 수치(8,476)와 차트 최신값(6,800)이 모순 → 차트만 숨기고 이슈는 발행 (P0-5)."""
    _kospi_issue(conn, "bad", anchor_value=8476.48)
    export_all(conn, tmp_path / "out")
    index = json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))
    card = next(c for c in index["issues"] if c["id"] == "bad")
    assert card["has_visual"] is False and card["spark"] is None
    detail = json.loads((tmp_path / "out" / "issues" / "bad.json").read_text("utf-8"))
    assert detail["visual"] is None


def test_visual_consistency_gate_keeps_matching_chart(conn, tmp_path):
    """앵커와 차트 최신값이 허용오차 안 → 차트 유지."""
    _kospi_issue(conn, "good", anchor_value=6806.93)
    export_all(conn, tmp_path / "out")
    index = json.loads((tmp_path / "out" / "index.json").read_text("utf-8"))
    card = next(c for c in index["issues"] if c["id"] == "good")
    assert card["has_visual"] is True
    detail = json.loads((tmp_path / "out" / "issues" / "good.json").read_text("utf-8"))
    assert detail["visual"] is not None
