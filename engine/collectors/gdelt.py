"""GDELT DOC 2.0 수집 — 군집 원료 (설계서 §02-5). 무키·상업 무제한. 본문 없음."""
from __future__ import annotations

import re

from ..config import cfg
from ..util.http import get_json
from .base import append_articles

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"


def parse_seendate(s: str) -> str | None:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$", str(s or ""))
    if not m:
        return None
    y, mo, d, h, mi, se = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{se}+00:00"


def map_articles(data: dict | None) -> list[dict]:
    """DOC 응답 → 공통 기사 스키마 (순수 함수 — 테스트 대상)."""
    out = []
    for a in (data or {}).get("articles", []):
        if not a.get("url") or not a.get("title"):
            continue
        out.append({
            "url": a["url"], "title": a["title"],
            "lead": "",  # GDELT 는 본문 미제공 — 방화벽 자동 충족
            "source": a.get("domain") or "gdelt",
            "published_at": parse_seendate(a.get("seendate")),
            "lang": "ko" if str(a.get("language", "")).lower() in ("korean", "ko") else "en",
        })
    return out


def collect() -> int:
    c = cfg()["collect"]["gdelt"]
    total = 0
    for q in c["queries"]:
        data = get_json(ENDPOINT, params={
            "query": q, "mode": "ArtList", "format": "json",
            "maxrecords": str(c["max_records"]), "timespan": c["timespan"],
            "sort": "HybridRel",
        })
        total += append_articles("gdelt", "wire", map_articles(data))
    return total
