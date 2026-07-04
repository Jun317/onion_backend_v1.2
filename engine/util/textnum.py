"""숫자 추출 유틸 — ①normalize 의 후보 수치 태깅 ②LLM 출력 숫자 대조 (설계서 §6-4)."""
from __future__ import annotations

import re

# 숫자+단위 후보 (제목·리드 태깅용): 3.5%, 25bp, 1.2조원, 30억달러 ...
UNIT_RE = re.compile(r"(-?\d[\d,]*\.?\d*)\s*(%p|%|퍼센트|bp|포인트|조\s?원|억\s?원|억\s?달러|조\s?달러|원|달러|\$)")

# 일반 숫자 (대조용)
NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def tag_numbers(text: str) -> list[str]:
    """제목·리드에서 '숫자+단위' 후보 문자열 추출."""
    return [f"{m.group(1)}{m.group(2).replace(' ', '')}" for m in UNIT_RE.finditer(text or "")]


def extract_numbers(text: str) -> set[float]:
    """문자열의 모든 수치를 float 집합으로 (콤마 제거)."""
    out: set[float] = set()
    for m in NUM_RE.finditer(text or ""):
        try:
            out.add(round(float(m.group(0).replace(",", "")), 6))
        except ValueError:
            continue
    return out


def numbers_subset(output_text: str, allowed: set[float]) -> bool:
    """출력의 모든 수치가 허용 집합에 존재하는가 (환각 숫자 차단).

    허용 집합에는 파생값(예: 0.25%p 인하 → 3.0, 3.25, 0.25)이 미리 포함돼야 한다.
    """
    return extract_numbers(output_text).issubset(allowed)
