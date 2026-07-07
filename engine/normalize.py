"""정규화 (설계서 §04-①) — raw JSONL → article 레코드 + entity_keys + 숫자 태깅."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import cfg, entities
from .collectors.base import DATA
from .util.simhash import simhash64
from .util.textnum import tag_numbers

_alias_index: list[tuple[str, str]] | None = None  # (lowercase alias, key)


def _aliases() -> list[tuple[str, str]]:
    global _alias_index
    if _alias_index is None:
        idx = []
        for ent in entities():
            for a in ent["aliases"]:
                idx.append((str(a).lower(), ent["key"]))
        # 긴 별칭 우선 (부분 문자열 오탐 완화: 'SK' 보다 'SK하이닉스' 먼저)
        _alias_index = sorted(idx, key=lambda x: -len(x[0]))
    return _alias_index


def extract_entity_keys(text: str) -> list[str]:
    """티커·기관 사전 매칭. 2자 이하 짧은 별칭은 단어 경계 요구 (오탐 방지)."""
    t = (text or "").lower()
    found: list[str] = []
    for alias, key in _aliases():
        if key in found:
            continue
        if len(alias) <= 2:
            if re.search(rf"(?<![0-9a-z]){re.escape(alias)}(?![0-9a-z])", t):
                found.append(key)
        elif alias in t:
            found.append(key)
    return found


def normalize_record(raw: dict) -> dict | None:
    """raw JSONL 1행 → article dict (DB insert 직전 형태). 필수 필드 없으면 None."""
    title = (raw.get("title") or "").strip()
    url = (raw.get("url") or "").strip()
    if not title or not url or not raw.get("id"):
        return None
    lead = (raw.get("lead") or "").strip()[:200]
    text = f"{title} {lead}"
    return {
        "id": raw["id"], "source": raw.get("source", ""), "tier": raw.get("tier", "wire"),
        "url": url, "url_hash": raw["id"], "title": title, "lead": lead,
        "published_at": raw.get("published_at") or raw.get("collected_at"),
        "lang": raw.get("lang", "en"),
        # 제목+리드로 simhash — 전재 기사(제목·리드 동일)를 더 확실히 근접중복으로 잡아
        # 가짜 2출처(같은 와이어를 여러 도메인이 転載)를 걸러낸다. 리드 없으면 제목만.
        "simhash": simhash64(text),
        "entity_keys": json.dumps(extract_entity_keys(text), ensure_ascii=False),
        "num_tags": json.dumps(tag_numbers(text), ensure_ascii=False),
        "collected_at": raw.get("collected_at"),
    }


def load_recent_raw(data_dir: Path | None = None, lookback_h: int | None = None) -> list[dict]:
    """최근 lookback 창의 raw JSONL 을 정규화해 반환 (중복은 이후 url_hash 로 걸러짐)."""
    data = data_dir or DATA
    hours = lookback_h or cfg()["pipeline"]["raw_lookback_h"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    out: list[dict] = []
    raw_dir = data / "raw"
    if not raw_dir.exists():
        return out
    # 이번 달 + 지난 달 파일만 스캔 (월 로테이션 구조 활용)
    months = sorted({d.name for d in raw_dir.iterdir() if d.is_dir()})[-2:]
    for month in months:
        for path in sorted((raw_dir / month).glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                ts = raw.get("collected_at") or ""
                try:
                    if datetime.fromisoformat(ts) < cutoff:
                        continue
                except ValueError:
                    pass
                rec = normalize_record(raw)
                if rec:
                    out.append(rec)
    return out
