"""e2e dry-run — 합성 데이터로 전체 사이클 (네트워크·키·실모델 0)."""
import json

from engine.dryrun import make_fixture_data
from engine.run_pipeline import run


def test_full_cycle(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "out"
    make_fixture_data(data)

    stats = run(dry_run=True, db_path=str(tmp_path / "e.db"), data_dir=data, out_dir=out)

    # 수집 반영: 기사 7건 로드, 전재 1건은 근접중복 마킹
    assert stats["inserted"] == 7
    assert stats["near_dups"] == 1
    # 급행 이벤트 2건(금리·유가) → 이슈 즉시 발행 + centroid 보강
    assert stats["express"] == 2
    assert stats["express_centroids"] == 2
    # 군집: 한은 클러스터는 급행 이슈로 편입/병합, 삼성 클러스터는 seed → promote
    assert stats["seeded"] >= 1
    assert stats["promoted"] >= 1
    # LLM 가공 (FakeLLM) — 발행 이슈 전부
    assert stats["llm"]["generated"] >= 2

    # export 산출 검증
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert index["schema_version"] == 3
    assert index["issues"], "발행 이슈가 export 되어야 함"
    cats = {i["category"] for i in index["issues"]}
    assert "RATE" in cats            # 급행 이슈
    assert "COMMODITY" in cats       # 원자재 급행 이슈 (v3 신설 카테고리)
    for card in index["issues"]:
        assert card["one_liner"] and card["id"]
        assert card["event_at"], "v3: 사건 시각이 카드에 있어야 함"
        assert "impact_line" in card
        detail_path = out / "issues" / f"{card['id']}.json"
        assert detail_path.exists()
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
        assert isinstance(detail["details"], list)
        assert detail["timeline"], "타임라인이 있어야 함"

    # 급행(RATE) 이슈에 기사들이 병합/편입됐는지 — 공식 origin 이슈 확인
    rate_issue = next(i for i in index["issues"] if i["category"] == "RATE")
    assert rate_issue["origin"] == "official_event"   # 공식 origin 승자 (§05-⑤)
    assert rate_issue["sources"] >= 2                  # 한은 기사 편입 (전재 제외)

    # 유가(COMMODITY) 이슈 — 사건 시각(3일 전 타임라인)이 처리 시각과 분리돼야 함
    oil_issue = next(i for i in index["issues"] if i["category"] == "COMMODITY")
    assert oil_issue["event_at"] < oil_issue["last_update"]


def test_dry_run_is_idempotent(tmp_path):
    """같은 데이터로 2회 실행해도 이슈·기사 중복 없음 (멱등)."""
    data = tmp_path / "data"
    make_fixture_data(data)
    run(dry_run=True, db_path=str(tmp_path / "e.db"), data_dir=data, out_dir=tmp_path / "o1")
    s2 = run(dry_run=True, db_path=str(tmp_path / "e.db"), data_dir=data, out_dir=tmp_path / "o2")
    assert s2["inserted"] == 0        # url_hash 로 전부 걸러짐
    assert s2["express"] == 0         # 멱등 키로 재처리 안 됨
    assert s2["llm"]["generated"] == 0 and s2["llm"]["cached"] >= 2   # fact_hash 캐시
