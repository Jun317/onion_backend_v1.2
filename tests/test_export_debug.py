import json

from engine.db import now_iso
from engine.export import export_debug

from .conftest import insert_article, insert_issue, unit_vec


def _setup(conn):
    base = unit_vec(seed=7)
    insert_issue(conn, "pub", title="발행 이슈", category="RATE", status="active",
                 origin="official_event", centroid=base, entity_keys='["BOK"]')
    insert_issue(conn, "cand", title="후보 이슈", category="ETC", status="candidate")
    insert_article(conn, "m1", title="멤버 기사", issue_id="pub",
                   vec=unit_vec(base=base, noise=0.1))
    insert_article(conn, "float", title="미배정 기사", vec=unit_vec(seed=8))
    conn.execute("UPDATE article SET last_sim=0.66, last_sim_issue='pub' WHERE id='float'")
    conn.execute(
        "INSERT INTO llm_output(issue_id,fact_hash,one_liner,details_json,visual_type,"
        "effects_json,model,created_at,payload_json,raw_response,validation_json) "
        "VALUES('pub','fh','한 줄','[\"d1\"]','none','[]','gemini',?,"
        "'{\"headlines\":[\"h\"]}','{raw}','[{\"model\":\"gemini\",\"errors\":[]}]')",
        (now_iso(),))
    conn.execute("INSERT INTO review_queue(issue_id,reason,at) VALUES('cand',?,?)",
                 (json.dumps({"summary": "실패", "attempts": []}), now_iso()))
    conn.commit()


def test_export_debug_files_and_content(conn, tmp_path):
    _setup(conn)
    stats = export_debug(conn, tmp_path, current_stats={"raw_loaded": 5})
    assert stats["issues"] == 2

    summary = json.loads((tmp_path / "debug" / "summary.json").read_text())
    assert summary["issue_counts"] == {"active": 1, "candidate": 1}
    assert summary["article_counts"]["unassigned"] == 1
    # 현재 사이클 통계가 이력 맨 앞에 합성 삽입
    assert summary["run_history"][0]["stats"] == {"raw_loaded": 5}
    assert "tau_join" in summary["config"]

    issues = json.loads((tmp_path / "debug" / "issues.json").read_text())["issues"]
    assert {i["id"] for i in issues} == {"pub", "cand"}  # candidate 포함

    detail = json.loads((tmp_path / "debug" / "issues" / "pub.json").read_text())
    assert detail["members"][0]["sim_to_centroid"] is not None
    assert detail["llm"]["model"] == "gemini"
    assert detail["llm"]["payload"] == {"headlines": ["h"]}
    assert detail["llm"]["attempts"][0]["errors"] == []

    arts = json.loads((tmp_path / "debug" / "articles.json").read_text())["articles"]
    fl = next(a for a in arts if a["id"] == "float")
    assert fl["state"] == "미배정" and fl["last_sim"] == 0.66

    review = json.loads((tmp_path / "debug" / "review.json").read_text())["queue"]
    assert review[0]["issue_id"] == "cand" and review[0]["reason"]["summary"] == "실패"

    llm = json.loads((tmp_path / "debug" / "llm.json").read_text())
    assert "RATE" in llm["system_prompts"]


def test_export_debug_prunes_stale_detail_files(conn, tmp_path):
    _setup(conn)
    stale = tmp_path / "debug" / "issues" / "gone.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}")
    export_debug(conn, tmp_path)
    assert not stale.exists()
