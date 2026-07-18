"""MVP 콘텐츠 패키지 export 검증 — 카운트·링크 해석·시각자료·용어 매칭."""
import json

import pytest

from engine.export_mvp import export_all


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("out_mvp")
    stats = export_all(d)
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    details = {c["id"]: json.loads((d / "issues" / f"{c['id']}.json").read_text("utf-8"))
               for c in index["issues"]}
    return stats, index, details


def test_counts(out):
    stats, index, details = out
    assert index["schema_version"] == 4
    assert len(index["issues"]) == 57
    assert len(index["steady"]) == 4
    assert len(index["categories"]) == 7
    assert len(index["scoreboard"]) == 13
    assert len(details) == 57


def test_period_tiers_partition(out):
    _, index, _ = out
    tiers = {}
    for c in index["issues"]:
        tiers.setdefault(c["period_tier"], []).append(c["id"])
    assert set(tiers) == {"weekly", "monthly", "yearly"}
    assert len(tiers["weekly"]) == 7 and len(tiers["monthly"]) == 16
    assert len(tiers["yearly"]) == 34


def test_importance_orders_weekly_first(out):
    _, index, _ = out
    tiers = [c["period_tier"] for c in index["issues"]]
    # importance 내림차순 정렬 결과가 weekly → monthly → yearly 블록이어야 함
    first_monthly = tiers.index("monthly")
    first_yearly = tiers.index("yearly")
    assert all(t == "weekly" for t in tiers[:first_monthly])
    assert all(t == "monthly" for t in tiers[first_monthly:first_yearly])


def test_cards_have_v4_fields(out):
    _, index, _ = out
    for c in index["issues"]:
        assert c["origin"] == "curated" and c["status"] == "active"
        assert len(c["key_stats"]) == 2
        for s in c["key_stats"]:
            assert s["direction"] in {"up", "down", "flat"}
        assert c["date_label"] and c["date_label_short"]
        assert c["event_at"] == c["last_update"]


def test_details_effect_rows_and_visuals(out):
    _, index, details = out
    n_rows = n_visuals = 0
    for d in details.values():
        for r in d["effect_rows"]:
            assert r["direction"] in {"up", "down", "info"}
            assert r["label"] and r["text"]
        assert d["effects"] == [r["text"] for r in d["effect_rows"]]
        n_rows += len(d["effect_rows"])
        n_visuals += len(d["visuals"])
        # 대표 visual 은 차트 kind 만 (표·타임라인은 visuals 에서만)
        if d["visual"] is not None:
            assert d["visual"].get("kind", "chart") == "chart"
        assert d["has_visual"] == (d["visual"] is not None)
    assert n_rows >= 57 * 2 - 5   # 이슈당 2~3행
    assert n_visuals > 57         # 시각자료 다중 첨부가 실제로 존재


def test_steady_six_blocks_and_links(out):
    _, index, details = out
    for s in index["steady"]:
        assert s["definition"] and len(s["score"]) == 3 and len(s["story"]) == 3
        assert len(s["timeline"]) >= 5 and len(s["next_up"]) == 3
        assert any(t.get("hot") for t in s["timeline"])
        for entry in s["timeline"]:
            for link in entry.get("links") or []:
                assert link["issue_id"] in details, f"{s['id']} → {link}"
        assert s["visual"] is not None   # 스테디 카드 미니차트 소스


def test_visual_kinds_preserved(out):
    _, index, details = out
    kinds = set()
    null_gap_ok = False
    for d in details.values():
        for v in d["visuals"]:
            kinds.add(v.get("kind", "chart"))
            if v["id"] == "C4":
                null_gap_ok = any(p["v"] is None for p in v["series"])
            if v.get("series_multi"):
                for sm in v["series_multi"]:
                    assert len([p for p in sm["series"]
                                if isinstance(p.get("v"), (int, float))]) >= 2
    assert kinds == {"chart", "table", "timeline"}
    assert null_gap_ok, "C4 의 null 슬롯(확인중)이 보존돼야 함"


def test_glossary_matched_and_grounded(out):
    _, index, details = out
    matched_terms = set()
    for d in details.values():
        corpus = " ".join([d["title"], *(d["details"]), *(d["tips"]),
                           *[r["text"] + " " + (r.get("basis") or "")
                             for r in d["effect_rows"]],
                           d.get("impact_line") or ""]).lower()
        for g in d["glossary"]:
            matched_terms.add(g["term"])
            assert g["easy"]
        # 붙은 용어는 실제 본문에 등장해야 함 (표기 또는 별칭)
        for g in d["glossary"]:
            assert g["term"].lower() in corpus or any(
                a.lower() in corpus for a in _aliases(g["term"]))
    assert len(matched_terms) >= 20   # 사전 대부분이 실제로 쓰임


def _aliases(term: str) -> list[str]:
    import yaml
    from engine.export_mvp import MVP
    terms = yaml.safe_load((MVP / "glossary.yaml").read_text("utf-8"))["terms"]
    for g in terms:
        if g["term"] == term:
            return [str(a) for a in (g.get("aliases") or [])]
    return []


def test_spark_from_representative_chart(out):
    _, index, _ = out
    with_spark = [c for c in index["issues"] if c["spark"]]
    assert with_spark, "스파크가 있는 카드가 있어야 함"
    for c in with_spark:
        assert 2 <= len(c["spark"]) <= 8
        assert all(isinstance(v, (int, float)) for v in c["spark"])
