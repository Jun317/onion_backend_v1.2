from engine.db import daily_counter, meta_set
from engine.llm.client import FakeLlm
from engine.llm.handler import process_all, template_output

from .conftest import insert_article, insert_issue, unit_vec


def _published_issue(conn, iid="i1"):
    insert_issue(conn, iid, title="한은 기준금리 인하", category="RATE",
                 status="active", centroid=unit_vec(seed=1), frozen=1)
    insert_article(conn, f"{iid}_a", issue_id=iid, source="s1",
                   title="한은, 기준금리 0.25%p 인하…3.00%")
    conn.execute("INSERT INTO numeric_anchor(issue_id,entity,metric,value,unit,prev,period,"
                 "source,observed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (iid, "한국은행", "기준금리", 3.0, "%", 3.25, "2026-07", "ECOS", "2026-07-04"))


def test_generate_and_cache(conn):
    _published_issue(conn)
    s1 = process_all(conn, FakeLlm())
    assert s1["generated"] == 1
    row = conn.execute("SELECT * FROM llm_output WHERE issue_id='i1'").fetchone()
    assert row["one_liner"] and row["model"] == "fake"
    # LLM 생성 용어 해설이 저장된다 (본문 등장 용어만)
    import json as _json
    glossary = _json.loads(row["glossary_json"])
    assert glossary and glossary[0]["term"] == "이슈" and glossary[0]["easy"]
    calls_after_first = daily_counter(conn, "llm_calls")
    # 팩트 불변 → 재호출 0 (캐시 히트)
    s2 = process_all(conn, FakeLlm())
    assert s2["cached"] == 1 and s2["generated"] == 0
    assert daily_counter(conn, "llm_calls") == calls_after_first


def test_fact_change_triggers_regenerate(conn):
    _published_issue(conn)
    process_all(conn, FakeLlm())
    insert_article(conn, "new_a", issue_id="i1", source="s2",
                   title="한은 금리 인하 후속 반응")   # headlines 변경 → fact_hash 변경
    s = process_all(conn, FakeLlm())
    assert s["generated"] == 1


def test_validation_failure_falls_back_to_template(conn):
    _published_issue(conn)
    s = process_all(conn, FakeLlm(mode="invalid_json"))
    assert s["template"] == 1
    row = conn.execute("SELECT model FROM llm_output WHERE issue_id='i1'").fetchone()
    assert row["model"] == "template"
    assert conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 1


def test_template_backoff_then_retry(conn):
    """템플릿 폴백은 백오프 간격 전엔 재시도 안 함, 경과 후엔 재시도해 교체된다."""
    _published_issue(conn)
    process_all(conn, FakeLlm(mode="invalid_json"))   # → template
    # 방금 만든 template 은 백오프에 걸려 재시도 안 됨
    s1 = process_all(conn, FakeLlm())
    assert s1["generated"] == 0 and s1["backoff"] == 1
    assert conn.execute("SELECT model FROM llm_output WHERE issue_id='i1'").fetchone()["model"] == "template"
    # created_at 을 4시간 전으로 낮추면(백오프 경과) 재시도 → 성공 교체
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    conn.execute("UPDATE llm_output SET created_at=? WHERE issue_id='i1'", (old,))
    s2 = process_all(conn, FakeLlm())
    assert s2["generated"] == 1
    assert conn.execute("SELECT model FROM llm_output WHERE issue_id='i1'").fetchone()["model"] == "fake"


def test_template_title_assembled_in_korean():
    """anchor 가 있으면 영어 헤드라인 절단 대신 한국어 기계 조립 (one_liner 는 해요체)."""
    out = template_output({"category": "RATE",
                           "anchors": [{"entity": "한국은행", "metric": "기준금리",
                                        "value": 3.0, "unit": "%", "prev": 3.25}],
                           "headlines": ["BOK cuts rates by 25bp in surprise move"]})
    assert out["title"] == "한국은행 기준금리 3.0%"
    assert out["one_liner"] == "한국은행 기준금리가 3.25%에서 3.0%로 바뀌었어요"
    assert out["glossary"] == []


def test_daily_cap_defers(conn):
    _published_issue(conn)
    from datetime import datetime, timezone
    key = f"llm_calls_{datetime.now(timezone.utc):%Y%m%d}"
    meta_set(conn, key, "300")   # 캡 도달 상태
    s = process_all(conn, FakeLlm())
    assert s["deferred"] == 1 and s["generated"] == 0


def test_template_output_uses_anchors_only():
    out = template_output({"category": "RATE",
                           "anchors": [{"entity": "한국은행", "metric": "기준금리",
                                        "value": 3.0, "unit": "%", "prev": 3.25}],
                           "headlines": ["한은 금리 인하"]})
    assert "3.0" in out["details"][0] and out["effects"] == []


def test_max_per_run_defers_rest(conn, monkeypatch):
    """실호출(network=True) 경로는 max_per_run 을 넘는 이슈를 다음 주기로 이월 — 타임아웃 방지."""
    import engine.llm.handler as h
    base = h.cfg()
    over = {**base, "llm": {**base["llm"], "max_per_run": 1,
                            "call_interval_s": 0, "max_seconds_per_run": 0}}
    monkeypatch.setattr(h, "cfg", lambda: over)
    _published_issue(conn, "i1")
    _published_issue(conn, "i2")

    class NetFake(FakeLlm):
        network = True

    s = process_all(conn, NetFake())
    assert s["generated"] == 1 and s["deferred"] == 1   # 1개만 처리, 나머지 이월


def test_fake_client_has_no_runtime_budget(conn):
    """FakeLlm(network=False)은 예산 제한이 없어 테스트·dry-run 이 그대로 전량 처리된다."""
    _published_issue(conn, "i1")
    _published_issue(conn, "i2")
    s = process_all(conn, FakeLlm())
    assert s["generated"] == 2 and s["deferred"] == 0


def test_template_output_hides_fallback_copy():
    """템플릿 폴백은 노출용 문구 대신 None — 프런트가 섹션을 숨긴다 (P0-4)."""
    out = template_output({"category": "RATE",
                           "anchors": [{"entity": "한국은행", "metric": "기준금리",
                                        "value": 3.0, "unit": "%", "prev": 3.25}],
                           "headlines": ["한은 금리 인하"]})
    assert out["why_now"] is None
    assert out["impact_line"] is None
