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
