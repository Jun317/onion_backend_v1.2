"""LLM 출력 검증 체인 (설계서 §6-4).

JSON 파스 → 스키마·길이·어미 정규식 → 숫자 대조(환각 차단) → 금지어 → 실패 목록 반환.
glossary 는 소프트 검증: 위반 항목만 조용히 드롭하고 재생성 사유로 삼지 않는다.
"""
from __future__ import annotations

import difflib
import json
import re

from ..config import cfg
from ..util.lang import has_cjk
from ..util.textnum import extract_numbers

# 문장 말미 어미: 마침표·느낌표·물결·따옴표·이모지 등이 뒤에 붙어도 인정 (관대화)
DETAIL_RE = re.compile(r"(요|에요|예요)[\s.!~…'\"”’)]*$")
EFFECT_RE = re.compile(r"!\s*$")

GLOSSARY_MAX = 4
DETAIL_MAX_LEN = 55

# details 자동 보정: 55자 초과 문장을 문장부호/공백 경계에서 자른다
_CLIP_BOUND = re.compile(r"[\s,·。.、]")


def _clip_sentence(text: str, limit: int = DETAIL_MAX_LEN) -> str:
    """어미(요/에요/예요)를 유지하며 길이 제한에 맞춰 자른다 — 자동 보정용."""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    m = list(_CLIP_BOUND.finditer(cut))
    if m and m[-1].start() >= limit // 2:
        cut = cut[:m[-1].start()]
    cut = cut.rstrip(" ,·、")
    return cut + ("요." if not DETAIL_RE.search(cut) else "")


# 문자 단위 유사도 임계 — 짧은 한국어 문장은 조사·어미가 겹쳐 기본 비율이 높다.
# 0.9 미만으로 낮추면 "코스피가 올랐어요/코스닥이 올랐어요" 같은 별개 정보를 오탐한다.
_DUP_THRESHOLD = 0.93


def _similar(a: str, b: str, threshold: float = _DUP_THRESHOLD) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def dedupe_similar(items: list[str], threshold: float = _DUP_THRESHOLD) -> list[str]:
    """근접 중복 문장 제거 (동어반복 게이트) — 먼저 온 문장을 남긴다."""
    kept: list[str] = []
    for s in items:
        if any(_similar(s, k, threshold) for k in kept):
            continue
        kept.append(s)
    return kept


def allowed_numbers(payload: dict) -> set[float]:
    """입력 페이로드의 모든 수치 + 파생값(변동폭, 절대값, bp 환산, 반올림)."""
    nums = extract_numbers(json.dumps(payload, ensure_ascii=False))
    derived: set[float] = set()
    for n in nums:
        derived.add(abs(n))
        derived.update({round(n), round(n, 1)})  # 반올림 표기 허용 (3.62 → 3.6)
    for a in payload.get("anchors", []):
        v, p = a.get("value"), a.get("prev")
        if isinstance(v, (int, float)) and isinstance(p, (int, float)):
            d = round(abs(v - p), 6)
            derived.update({d, round(d * 100, 6), round(d)})  # %p ↔ bp
        if isinstance(v, (int, float)):
            derived.update({round(float(v), 6), round(float(v) * 100, 6),
                            round(float(v)), round(float(v), 1)})
    return nums | derived


def _is_free_number(n: float) -> bool:
    """환각으로 보지 않는 값 — 연도(1900~2100)·작은 정수 카운트(0~12)."""
    return (1900 <= n <= 2100 and n == int(n)) or (0 <= n <= 12 and n == int(n))


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
    """검증 실패 사유 목록 (빈 리스트 = 통과).

    사소한 위반은 저장 전 자동 보정(auto-repair)해 통과율을 높인다 — 번역 병목 완화.
    보정 불가한 위반(누락·문체·환각·금지어)만 실패로 남긴다.
    """
    errors: list[str] = []
    if not isinstance(out, dict):
        return ["출력이 JSON 객체가 아님"]

    title = out.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title 누락")
    else:
        title = title.strip()
        out["title"] = title
        if len(title) > 22:
            errors.append(f"title 22자 초과({len(title)})")
        if has_cjk(title):
            errors.append("title 에 한자/가나 포함")

    ol = out.get("one_liner")
    if not isinstance(ol, str) or not ol.strip():
        errors.append("one_liner 누락")
    else:
        ol = ol.strip()
        out["one_liner"] = ol
        if len(ol) > 50:
            errors.append(f"one_liner 50자 초과({len(ol)})")
        if not DETAIL_RE.search(ol):
            errors.append("one_liner 해요체 아님")
        if has_cjk(ol):
            errors.append("one_liner 에 한자/가나 포함")

    why = out.get("why_now")
    if not isinstance(why, str) or not why.strip():
        errors.append("why_now 누락")
    else:
        why = why.strip()
        if len(why) > 55:
            errors.append(f"why_now 55자 초과({len(why)})")
        if not DETAIL_RE.search(why):
            errors.append("why_now 어요체 아님")

    # details 자동 보정: 3~6개 허용(하한 완화), 55자 초과분은 문장 경계에서 절단.
    # 6개 초과는 앞 6개만, 3개 미만은 보정 불가(리젝트).
    details = out.get("details")
    if not isinstance(details, list) or len(details) < 3:
        errors.append("details 3문장 미만")
    else:
        repaired = []
        for d in details[:6]:
            if not isinstance(d, str) or not d.strip():
                continue
            s = d.strip()
            if len(s) > DETAIL_MAX_LEN:
                s = _clip_sentence(s)
            repaired.append(s)
        # 동어반복 게이트 — 사실상 같은 불릿은 자동 제거 (제거 후에도 3문장은 있어야 통과)
        repaired = dedupe_similar(repaired)
        out["details"] = repaired
        if len(repaired) < 3:
            errors.append("details 3문장 미만")
        else:
            for i, d in enumerate(repaired):
                if not DETAIL_RE.search(d):
                    errors.append(f"details[{i}] 어요체 아님")
        details = repaired

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
        # "그래서" 섹션이 본문을 반복하면 새 정보가 없다 — 반복 문장은 자동 드롭,
        # 전부 반복이면 실패(재생성 사유)
        fresh = [e for e in effects if isinstance(e, str)
                 and not any(_similar(e.strip(), d) for d in (details or []))]
        if effects and not fresh:
            errors.append("effects 가 details 반복 (새 정보 없음)")
        else:
            out["effects"] = fresh
            effects = fresh

    # impact_line("나에게는?") — 소프트 검증: 위반 시 조용히 제거 (재생성 사유 아님)
    imp = out.get("impact_line")
    if isinstance(imp, str) and imp.strip():
        imp = imp.strip()
        out["impact_line"] = imp if (len(imp) <= 45 and DETAIL_RE.search(imp)) else None
    else:
        out["impact_line"] = None

    # 숫자 대조 — 출력의 모든 수치가 입력(+파생값)에 존재해야 함.
    # glossary 는 제외: 해설 특성상 환산·비유 숫자("1bp는 0.01%예요")가 필요하다.
    allowed = allowed_numbers(payload)
    out_text = " ".join([str(out.get("title", "")), str(out.get("one_liner", "")),
                         str(out.get("why_now", "")), str(out.get("impact_line") or "")]
                        + [str(d) for d in (details or [])]
                        + [str(e) for e in (effects or [])])
    extra = {n for n in (extract_numbers(out_text) - allowed) if not _is_free_number(n)}
    if extra:
        errors.append(f"입력에 없는 숫자: {sorted(extra)[:5]}")

    for phrase in cfg()["llm"]["banned_phrases"]:
        if phrase in out_text:
            errors.append(f"금지어: {phrase}")

    # glossary 는 소프트 정리 — 통과/실패와 무관하게 저장 전 형태를 보증
    clean_glossary(out)

    return errors
