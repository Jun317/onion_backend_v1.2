"""LLM 출력 검증 체인 (설계서 §6-4).

JSON 파스 → 스키마·길이·어미 정규식 → 숫자 대조(환각 차단) → 금지어 → 실패 목록 반환.
"""
from __future__ import annotations

import json
import re

from ..config import cfg
from ..util.textnum import extract_numbers

DETAIL_RE = re.compile(r"(요|에요|예요)\s*[.!]?\s*$")
EFFECT_RE = re.compile(r"!\s*$")
_TRAIL_RE = re.compile(r"[\s.!?…'\"]+$")


def is_eumseumche(text: str) -> bool:
    """음슴체 판정 — 마지막 음절의 종성이 ㅁ (함/됨/임/감/옴 …. 설계서 예시 '내려감' 허용)."""
    t = _TRAIL_RE.sub("", text or "")
    if not t:
        return False
    ch = ord(t[-1])
    if not (0xAC00 <= ch <= 0xD7A3):
        return False
    return (ch - 0xAC00) % 28 == 16  # 종성 인덱스 16 = ㅁ


def allowed_numbers(payload: dict) -> set[float]:
    """입력 페이로드의 모든 수치 + 파생값(변동폭, 절대값, bp 환산)."""
    nums = extract_numbers(json.dumps(payload, ensure_ascii=False))
    derived: set[float] = set()
    for n in nums:
        derived.add(abs(n))
    for a in payload.get("anchors", []):
        v, p = a.get("value"), a.get("prev")
        if isinstance(v, (int, float)) and isinstance(p, (int, float)):
            d = round(abs(v - p), 6)
            derived.update({d, round(d * 100, 6)})  # %p ↔ bp
        if isinstance(v, (int, float)):
            derived.update({round(float(v), 6), round(float(v) * 100, 6)})
    return nums | derived


def parse_output(text: str) -> dict | None:
    """JSON 파스 (코드펜스 등 흔한 오염 1회 구제)."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", text or "", re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


def validate(out: dict, payload: dict, allowed_viz: list[str]) -> list[str]:
    """검증 실패 사유 목록 (빈 리스트 = 통과)."""
    errors: list[str] = []
    if not isinstance(out, dict):
        return ["출력이 JSON 객체가 아님"]

    ol = out.get("one_liner")
    if not isinstance(ol, str) or not ol.strip():
        errors.append("one_liner 누락")
    else:
        ol = ol.strip()
        if len(ol) > 30:
            errors.append(f"one_liner 30자 초과({len(ol)})")
        if not is_eumseumche(ol):
            errors.append("one_liner 음슴체(함/됨/임) 아님")

    details = out.get("details")
    if not isinstance(details, list) or not (3 <= len(details) <= 5):
        errors.append("details 3~5문장 아님")
    else:
        for i, d in enumerate(details):
            if not isinstance(d, str):
                errors.append(f"details[{i}] 문자열 아님")
                continue
            if len(d.strip()) > 45:
                errors.append(f"details[{i}] 45자 초과")
            if not DETAIL_RE.search(d.strip()):
                errors.append(f"details[{i}] 어요체 아님")

    vt = out.get("visual_type", "none")
    if vt not in (allowed_viz + ["none"]):
        errors.append(f"visual_type '{vt}' 허용 목록 밖")

    effects = out.get("effects")
    if not isinstance(effects, list) or not (1 <= len(effects) <= 2):
        errors.append("effects 1~2문장 아님")
    else:
        for i, e in enumerate(effects):
            if not isinstance(e, str) or not EFFECT_RE.search(e.strip()):
                errors.append(f"effects[{i}] 느낌표 종결 아님")

    # 숫자 대조 — 출력의 모든 수치가 입력(+파생값)에 존재해야 함
    allowed = allowed_numbers(payload)
    out_text = " ".join([str(out.get("one_liner", ""))]
                        + [str(d) for d in (details or [])]
                        + [str(e) for e in (effects or [])])
    extra = extract_numbers(out_text) - allowed
    if extra:
        errors.append(f"입력에 없는 숫자: {sorted(extra)[:5]}")

    for phrase in cfg()["llm"]["banned_phrases"]:
        if phrase in out_text:
            errors.append(f"금지어: {phrase}")

    return errors
