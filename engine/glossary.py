"""용어 사전 매칭 — 이슈 표시 텍스트에서 어려운 용어를 찾아 쉬운 정의와 함께 반환.

프런트가 툴팁으로 쓸 수 있게, 실제 이슈에 등장한 용어만 [{term, def}] 로 내보낸다.
긴 표기 우선 + 매칭 구간 마스킹으로 '기준금리' 안의 '금리' 중복 매칭을 막는다.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from .config import ROOT


@lru_cache(maxsize=1)
def _terms() -> list[tuple[str, str]]:
    p = ROOT / "glossary.yaml"
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    items = [(str(t), str(d)) for t, d in data.items()]
    return sorted(items, key=lambda x: -len(x[0]))  # 긴 표기 먼저 (부분매칭 오탐 방지)


def terms_in(text: str) -> list[dict]:
    t = text or ""
    found: list[dict] = []
    for term, definition in _terms():
        if term in t:
            found.append({"term": term, "def": definition})
            t = t.replace(term, " " * len(term))  # 매칭 구간 마스킹
    return found


def terms_in_parts(*parts: str) -> list[dict]:
    """여러 텍스트 조각을 합쳐 등장 용어 추출 (제목+요약+상세 등)."""
    return terms_in(" ".join(p for p in parts if p))
