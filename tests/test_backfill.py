import json
from datetime import datetime, timezone

import engine.collectors.base as base
from engine.backfill import backfill_fred, backfill_seed
from engine.normalize import extract_entity_keys

SEED = """
events:
  - id: ev1
    date: "2025-09-17"
    articles:
      - { title: "미 금리 인하", url: "https://backfill.example/ev1/1",
          source: 연합뉴스, published_at: "2025-09-17T18:00:00+00:00", lang: ko }
      - { title: "Fed cuts rates", url: "https://backfill.example/ev1/2",
          source: reuters.com, published_at: "2025-09-17T18:10:00+00:00", lang: en }
    express:
      category: MARKET
      entity: KR
      period: "2025-09"
      title: "테스트 급행"
      anchors: [{ entity: KR, metric: 지수, value: 100, unit: pt,
                  period: "2025-09", source: seed, prev: null }]
"""


def test_backfill_seed_curated(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "DATA", tmp_path / "data")
    seed = tmp_path / "seed.yaml"
    seed.write_text(SEED, encoding="utf-8")

    stats = backfill_seed(skip_gdelt=True, seed_path=seed)
    assert stats == {"events": 1, "articles": 2, "express": 1}

    # published_at 은 과거 그대로, collected_at 은 현재 → 파이프라인 lookback 통과
    lines = [json.loads(line) for line in
             next((tmp_path / "data" / "raw").rglob("seed.jsonl")).read_text().splitlines()]
    assert lines[0]["published_at"].startswith("2025-09-17")
    today = datetime.now(timezone.utc).date().isoformat()
    assert lines[0]["collected_at"].startswith(today)

    # 급행 이벤트는 실제 사건 날짜를 created_at 으로 유지
    ev = json.loads((tmp_path / "data" / "express" / "seed_ev1.json").read_text())
    assert ev["created_at"].startswith("2025-09-17")
    assert ev["anchors"][0]["value"] == 100

    # 멱등: 재실행 시 seen/파일존재로 아무것도 추가 안 됨
    stats2 = backfill_seed(skip_gdelt=True, seed_path=seed)
    assert stats2["articles"] == 0 and stats2["express"] == 0


def test_backfill_fred_skips_without_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert backfill_fred(12) == 0  # 네트워크 호출 없이 skip


def test_dart_whitelist_predicate():
    # dart.py express_major_only 필터의 판정 규칙: entities.yaml 등재 여부
    assert extract_entity_keys("삼성전자")  # 주요 기업 → 급행 허용
    assert extract_entity_keys("SK하이닉스")
    assert not extract_entity_keys("듀켐바이오")  # 미등재 중소기업 → 급행 제외
