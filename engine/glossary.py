"""용어 사전 매칭 + 쉬움 검증 — 어려운 말을 찾아 '문외한용 해설'과 함께 반환.

- 해설은 {easy, example} 구조. easy=일상어 한 문장, example=내 생활에 어떤 영향인지.
- terms_in: 이슈 표시 텍스트에서 등장 용어를 찾음. 대소문자 무시, 긴 표기 우선 +
  매칭 구간 마스킹(‘기준금리’ 안의 ‘금리’ 중복 방지), 별칭(aliases) 지원.
- lint: 해설이 '쉬움 규칙'을 지키는지 검사(하드워드/길이/어미) — 테스트가 CI 게이트로 사용.
런타임 LLM 생성 없음(정적 큐레이션). 초안 작성 보조는 engine/glossary_draft.py.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from .config import ROOT, cfg


def _load() -> dict:
    p = ROOT / "glossary.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _norm(term: str, val) -> tuple[str, str, list[str]]:
    """glossary.yaml 값 → (easy, example, aliases). 구버전 문자열도 호환."""
    if isinstance(val, dict):
        return (str(val.get("easy", "")), str(val.get("example", "") or ""),
                [str(a) for a in (val.get("aliases") or [])])
    return (str(val), "", [])   # 구버전: 문자열이면 easy 로 승격


@lru_cache(maxsize=1)
def _entries() -> list[tuple[str, str, str, str]]:
    """[(match_key_lower, 정식표기, easy, example)] — 표기+별칭 각각, 긴 표기 우선."""
    rows = []
    for term, val in _load().items():
        term = str(term)
        easy, example, aliases = _norm(term, val)
        for surface in [term, *aliases]:
            if len(surface) >= 2:          # 1자 이하 스킵 (오탐 방지)
                rows.append((surface.lower(), term, easy, example))
    return sorted(rows, key=lambda r: -len(r[0]))   # 긴 표기 먼저


@lru_cache(maxsize=1)
def _by_term() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for term, val in _load().items():
        easy, example, _ = _norm(str(term), val)
        out[str(term)] = (easy, example)
    return out


def terms_in(text: str) -> list[dict]:
    """텍스트에 등장한 용어 → [{term, easy, example}] (정식 표기 기준, 중복 제거).

    겹치는 표기는 '더 왼쪽 → 더 긴 표기' 우선으로 하나만 택한다.
    (예: '기준금리 인하'는 '금리 인하'가 아니라 더 왼쪽에서 시작하는 '기준금리'로.)"""
    low = (text or "").lower()
    occ: list[tuple[int, int, str, str, str]] = []   # (start, -len, term, easy, example)
    for key, term, easy, example in _entries():
        i = low.find(key)
        while i != -1:
            occ.append((i, -len(key), term, easy, example))
            i = low.find(key, i + 1)
    occ.sort(key=lambda o: (o[0], o[1]))             # 왼쪽 먼저, 같은 위치면 긴 표기 먼저

    found: list[dict] = []
    seen: set[str] = set()
    covered_end = -1
    for start, neg_len, term, easy, example in occ:
        if start < covered_end:                      # 이미 택한 구간과 겹침 → 건너뜀
            continue
        covered_end = start + (-neg_len)
        if term not in seen:
            found.append({"term": term, "easy": easy, "example": example})
            seen.add(term)
    return found


def terms_in_parts(*parts: str) -> list[dict]:
    """여러 텍스트 조각을 합쳐 등장 용어 추출 (제목+요약+상세 등)."""
    return terms_in(" ".join(p for p in parts if p))


# --- 쉬움 검증 (테스트 게이트) -----------------------------------------------

def lint(easy_max: int = 50, example_max: int = 50) -> list[str]:
    """해설이 '문외한도 이해' 규칙을 지키는지 검사. 위반 목록 반환(빈 리스트=통과).

    규칙: ①easy 존재·어미 '요' ②길이 상한 ③해설 안에 '하드워드'(사전에도 없는 어려운 말)
    포함 금지 — 걸리면 그 말을 풀어쓰거나 사전에 추가해야 함."""
    errors: list[str] = []
    by_term = _by_term()
    hard = set(cfg().get("glossary_hard_words", []))
    for term, (easy, example) in by_term.items():
        if not easy.strip():
            errors.append(f"{term}: easy 누락")
            continue
        if len(easy) > easy_max:
            errors.append(f"{term}: easy {easy_max}자 초과({len(easy)})")
        if example and len(example) > example_max:
            errors.append(f"{term}: example {example_max}자 초과({len(example)})")
        if not easy.rstrip(" .!?…\"'").endswith("요"):
            errors.append(f"{term}: easy 어미 '요' 아님")
        blob = f"{easy} {example}"
        for hw in hard:
            if hw and hw != term and hw in blob:
                errors.append(f"{term}: 해설에 어려운 말 '{hw}' — 풀어쓰거나 사전에 추가")
    return errors
