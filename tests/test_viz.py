import json

from engine.db import now_iso
from engine.viz import REGISTRY, allowed_for_category, build_visual


def test_allowed_for_category_whitelist():
    assert set(allowed_for_category("RATE")) == {"kr_base_rate", "us_policy_rate", "us_10y"}
    assert allowed_for_category("EARNINGS") == ["earnings_quarterly"]
    assert allowed_for_category("POLICY") == []   # 시각자료 없음 (§07)
    assert allowed_for_category("GEO") == []


def test_build_visual_rejects_category_mismatch(conn):
    # usdkrw 는 FX 전용 — RATE 이슈에 요청해도 생성 금지
    assert build_visual(conn, "usdkrw", "RATE", "i1") is None
    assert build_visual(conn, "없는타입", "RATE", "i1") is None


def test_build_visual_no_key_returns_none(conn):
    # 오프라인(키 없음) → 시리즈 없음 → None (이슈는 차트 없이 발행)
    assert build_visual(conn, "kr_base_rate", "RATE", "i1") is None


def test_build_visual_uses_cache(conn):
    payload = {"type": "kr_base_rate", "chart": "step", "title": "캐시", "unit": "%",
               "source": "ECOS", "series": [{"t": "2026-07", "v": 3.0}]}
    conn.execute("INSERT INTO viz_cache(cache_key,payload,fetched_at) VALUES(?,?,?)",
                 ("kr_base_rate", json.dumps(payload, ensure_ascii=False), now_iso()))
    got = build_visual(conn, "kr_base_rate", "RATE", "i1")
    assert got == payload   # fetch 없이 캐시 반환


def test_earnings_from_anchors(conn):
    for period, rev, op in [("2026-1Q", 100.0, 10.0), ("2026-2Q", 120.0, 12.0)]:
        for metric, v in [("매출액", rev), ("영업이익", op)]:
            conn.execute(
                "INSERT INTO numeric_anchor(issue_id,entity,metric,value,unit,period,source,"
                "observed_at) VALUES(?,?,?,?,?,?,?,?)",
                ("i1", "삼성전자", metric, v, "조원", period, "DART", now_iso()))
    got = build_visual(conn, "earnings_quarterly", "EARNINGS", "i1")
    assert got is not None and got["chart"] == "bar"
    names = {g["name"] for g in got["groups"]}
    assert names == {"매출액", "영업이익"}
    assert all(REGISTRY["earnings_quarterly"]["cats"] == ["EARNINGS"] for _ in [0])
