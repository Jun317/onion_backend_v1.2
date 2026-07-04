"""dry-run/e2e 테스트용 합성 데이터 생성 — 네트워크·키·모델 없이 전체 사이클 검증.

같은 사건을 다룬 기사 클러스터 2개 + 단독 기사 + 급행 이벤트 1건을
현재 시각 기준으로 만들어 낸다 (raw_lookback 창에 걸리도록).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .collectors.base import url_hash


def make_fixture_data(data_dir: Path) -> None:
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    raw = data_dir / "raw" / month
    raw.mkdir(parents=True, exist_ok=True)
    (data_dir / "express").mkdir(parents=True, exist_ok=True)

    def art(url: str, title: str, lead: str, source: str, lang: str,
            tier: str = "wire", mins_ago: int = 30) -> dict:
        ts = (now - timedelta(minutes=mins_ago)).isoformat()
        return {"id": url_hash(url), "source": source, "tier": tier, "url": url,
                "title": title, "lead": lead, "published_at": ts, "lang": lang,
                "collected_at": now.isoformat()}

    # 클러스터 A: 한국은행 금리 인하 (서로 다른 3개 원출처 + 전재 중복 1건)
    cluster_a = [
        art("https://ex1.com/a1", "한국은행, 기준금리 0.25%p 인하해 연 3.00%로",
            "한국은행 금융통화위원회가 기준금리를 내렸다", "연합뉴스", "ko"),
        art("https://ex2.com/a2", "한은 기준금리 인하 결정…연 3.00%",
            "금통위가 통화정책방향 회의에서 기준금리 인하를 결정", "SBS", "ko"),
        art("https://ex3.com/a3", "한국은행 기준금리 3.00%로 인하 단행",
            "기준금리가 0.25%포인트 내려갔다", "한겨레", "ko", mins_ago=20),
        # 근접중복 (문장부호만 다른 전재 — simhash 로 걸려야 함)
        art("https://ex4.com/a4", "한국은행, 기준금리 0.25%p 인하해 연 3.00%로.",
            "한국은행 금융통화위원회가 기준금리를 내렸다", "전재매체", "ko", mins_ago=25),
    ]
    # 클러스터 B: 삼성전자 실적 (2개 원출처)
    cluster_b = [
        art("https://ex5.com/b1", "삼성전자 2분기 영업이익 10조원 돌파",
            "삼성전자가 2분기 잠정실적을 발표했다", "경향신문", "ko", mins_ago=50),
        art("https://ex6.com/b2", "삼성전자 2분기 잠정실적 영업이익 10조원",
            "반도체 업황 회복으로 실적이 개선됐다", "gdelt.example.com", "ko", mins_ago=45),
    ]
    # 단독 기사 (군집 안 됨 → 풀 잔류)
    single = [art("https://ex7.com/c1", "글로벌 해운 운임 3주 연속 하락",
                  "컨테이너 운임 지수가 하락세다", "BBC", "en", mins_ago=60)]

    with (raw / "fixture.jsonl").open("w", encoding="utf-8") as f:
        for a in cluster_a + cluster_b + single:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")

    # 급행 이벤트: ECOS 기준금리 변동 (§05)
    event = {
        "key": "ecos_base_rate_202607", "created_at": now.isoformat(),
        "category": "RATE", "entity": "한국은행", "period": "2026-07",
        "title": "한국은행 기준금리 인하 (3.00%)",
        "anchors": [
            {"entity": "한국은행", "metric": "기준금리", "value": 3.0, "unit": "%",
             "prev": 3.25, "period": "2026-07", "source": "ECOS"},
            {"entity": "한국은행", "metric": "변동폭", "value": 0.25, "unit": "%p",
             "prev": None, "period": "2026-07", "source": "ECOS"}],
        "timeline": {"kind": "official", "title": "한국은행 기준금리 3.00% (2026-07)",
                     "source": "ECOS", "url": "https://ecos.bok.or.kr"},
    }
    (data_dir / "express" / "ecos_base_rate_202607.json").write_text(
        json.dumps(event, ensure_ascii=False, indent=1), encoding="utf-8")
