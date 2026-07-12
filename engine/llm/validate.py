"""LLM 출력 검증 체인 (설계서 §6-4).

JSON 파스 → 스키마·길이·어미 정규식 → 숫자 대조(환각 차단) → 금지어 → 실패 목록 반환.
glossary 는 소프트 검증: 위반 항목만 조용히 드롭하고 재생성 사유로 삼지 않는다.
"""
from __future__ import annotations

import json
import re

from ..config import cfg
from ..util.textnum import extract_numbers

DETAIL_RE = re.compile(r"(요|에요|예요)\s*[.!]?\s*$")
EFFECT_RE = re.compile(r"!\s*$")

GLOSSARY_MAX = 4


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


def clean_glossary(out: dict) -> None:
    """glossary 소프트 검증 — 형식·환각 위반 항목만 드롭하고 out 을 제자리 정리.

    유지 기준: term 이 본문(title/one_liner/why_now/details)에 실제 등장,
    easy 필수(≤50자·해요체·하드워드 금지), example ≤50자(초과 시 비움).
    숫자환각·금지어 검사 대상이 아니므로 "1bp는 0.01%예요" 같은 환산 해설도 허용된다.
    """
    raw = out.get("glossary") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        out["glossary"] = []
        return
    body = " ".join(
        [str(out.get("title", "")), str(out.get("one_liner", "")), str(out.get("why_now", ""))]
        + [str(d) for d in out.get("details") or []]).lower()
    hard = cfg().get("glossary_hard_words", []) or []
    cleaned: list[dict] = []
    seen: set[str] = set()
    for g in raw:
        if not isinstance(g, dict):
            continue
        term = str(g.get("term") or "").strip()
        easy = str(g.get("easy") or "").strip()
        example = str(g.get("example") or "").strip()
        key = term.lower()
        if not term or not easy or key in seen:
            continue
        if key not in body:  # 본문에 없는 용어 = 환각 → 드롭
            continue
        if len(easy) > 50 or not DETAIL_RE.search(easy):
            continue
        if any(h in easy for h in hard):  # 해설 안에 또 다른 전문용어 금지
            continue
        if len(example) > 50:
            example = ""
        cleaned.append({"term": term, "easy": easy, "example": example})
        seen.add(key)
        if len(cleaned) >= GLOSSARY_MAX:
            break
    out["glossary"] = cleaned


def validate(out: dict, payload: dict, allowed_viz: list[str]) -> list[str]:
    """검증 실패 사유 목록 (빈 리스트 = 통과)."""
    errors: list[str] = []
    if not isinstance(out, dict):
        return ["출력이 JSON 객체가 아님"]

    title = out.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title 누락")
    elif len(title.strip()) > 22:
        errors.append(f"title 22자 초과({len(title.strip())})")

    ol = out.get("one_liner")
    if not isinstance(ol, str) or not ol.strip():
        errors.append("one_liner 누락")
    else:
        ol = ol.strip()
        if len(ol) > 50:
            errors.append(f"one_liner 50자 초과({len(ol)})")
        if not DETAIL_RE.search(ol):
            errors.append("one_liner 해요체 아님")

    why = out.get("why_now")
    if not isinstance(why, str) or not why.strip():
        errors.append("why_now 누락")
    else:
        why = why.strip()
        if len(why) > 55:
            errors.append(f"why_now 55자 초과({len(why)})")
        if not DETAIL_RE.search(why):
            errors.append("why_now 어요체 아님")

    details = out.get("details")
    if not isinstance(details, list) or not (4 <= len(details) <= 6):
        errors.append("details 4~6문장 아님")
    else:
        for i, d in enumerate(details):
            if not isinstance(d, str):
                errors.append(f"details[{i}] 문자열 아님")
                continue
            if len(d.strip()) > 55:
                errors.append(f"details[{i}] 55자 초과")
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

    # 숫자 대조 — 출력의 모든 수치가 입력(+파생값)에 존재해야 함.
    # glossary 는 제외: 해설 특성상 환산·비유 숫자("1bp는 0.01%예요")가 필요하다.
    allowed = allowed_numbers(payload)
    out_text = " ".join([str(out.get("title", "")), str(out.get("one_liner", "")),
                         str(out.get("why_now", ""))]
                        + [str(d) for d in (details or [])]
                        + [str(e) for e in (effects or [])])
    extra = extract_numbers(out_text) - allowed
    if extra:
        errors.append(f"입력에 없는 숫자: {sorted(extra)[:5]}")

    for phrase in cfg()["llm"]["banned_phrases"]:
        if phrase in out_text:
            errors.append(f"금지어: {phrase}")

    # glossary 는 소프트 정리 — 통과/실패와 무관하게 저장 전 형태를 보증
    clean_glossary(out)

    return errors
