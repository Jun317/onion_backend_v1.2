"""MVP 큐레이션 콘텐츠 export — mvp/*.yaml → out_mvp/{index.json, issues/*.json}.

라이브 파이프라인(engine.db·out/)과 완전히 분리된 정적 콘텐츠 경로.
프런트는 app.json 의 base URL 을 out_mvp 로 바꾸는 것만으로 이 데이터를 사용한다
(복귀도 base URL 원복만으로 가능 — 파이프라인 코드는 이 모듈을 참조하지 않는다).

스키마 v4: v3 카드/상세 필드 호환 + period_tier · date_label · key_stats ·
effect_rows · tips · visuals(인라인) · 스테디 6블록.

실행: python -m engine.export_mvp   (sqlite/네트워크/키 불필요)
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .config import ROOT

MVP = ROOT / "mvp"
OUT = ROOT / "out_mvp"

SCHEMA_VERSION = 4

TIER_BASE = {"weekly": 3000, "monthly": 2000, "yearly": 1000}
STAT_DIRECTIONS = {"up", "down", "flat"}
EFFECT_DIRECTIONS = {"up", "down", "info"}


def _load(name: str) -> dict:
    return yaml.safe_load((MVP / name).read_text(encoding="utf-8")) or {}


# --- 용어 매칭 ----------------------------------------------------------------
# engine/glossary.terms_in 과 동일한 '왼쪽 우선 + 긴 표기 우선 + 구간 마스킹'
# 알고리즘. 원본은 lru_cache 로 루트 glossary.yaml 에 고정돼 있어(라이브 파이프라인
# 소유) 재사용하지 않고, 엔트리 리스트를 인자로 받는 순수 함수로 복제한다.

def _glossary_entries(terms: list[dict]) -> list[tuple[str, str, str, str]]:
    rows = []
    for g in terms:
        term = str(g.get("term") or "")
        easy = str(g.get("easy") or "")
        example = str(g.get("example") or "")
        for surface in [term, *[str(a) for a in (g.get("aliases") or [])]]:
            if len(surface) >= 2:
                rows.append((surface.lower(), term, easy, example))
    return sorted(rows, key=lambda r: -len(r[0]))


def _terms_in(entries: list[tuple[str, str, str, str]], *parts: str) -> list[dict]:
    low = " ".join(p for p in parts if p).lower()
    occ: list[tuple[int, int, str, str, str]] = []
    for key, term, easy, example in entries:
        i = low.find(key)
        while i != -1:
            occ.append((i, -len(key), term, easy, example))
            i = low.find(key, i + 1)
    occ.sort(key=lambda o: (o[0], o[1]))

    found: list[dict] = []
    seen: set[str] = set()
    covered_end = -1
    for start, neg_len, term, easy, example in occ:
        if start < covered_end:
            continue
        covered_end = start + (-neg_len)
        if term not in seen:
            found.append({"term": term, "easy": easy, "example": example})
            seen.add(term)
    return found


# --- 시각자료 ----------------------------------------------------------------

def _chart_series(visual: dict) -> list[dict]:
    """차트의 대표 시리즈 — 단일 series 우선, 멀티는 첫 번째."""
    if visual.get("series"):
        return visual["series"]
    multi = visual.get("series_multi") or []
    return multi[0]["series"] if multi else []


def _spark(visual: dict | None) -> list[float] | None:
    """카드 스파크라인 소스 — 대표 차트의 숫자 v 최근 8개 (export._spark 와 동형)."""
    if not visual or visual.get("kind", "chart") != "chart":
        return None
    vals = [p["v"] for p in _chart_series(visual)
            if isinstance(p.get("v"), (int, float))]
    if len(vals) < 2:
        return None
    return vals[-8:]


def _validate(meta: dict, visuals: dict, issues: list[dict], steady: list[dict],
              glossary: list[dict]) -> None:
    """저작 실수를 빌드 시점에 잡는다 — 실패 시 예외로 중단 (부분 산출 금지)."""
    errors: list[str] = []
    cat_codes = {c["code"] for c in meta.get("categories", [])}
    issue_ids = [i["id"] for i in issues]
    if len(issue_ids) != len(set(issue_ids)):
        errors.append("이슈 id 중복")

    for i in issues:
        iid = i.get("id", "?")
        if i.get("category") not in cat_codes:
            errors.append(f"{iid}: 미등록 카테고리 {i.get('category')}")
        if i.get("period_tier") not in TIER_BASE:
            errors.append(f"{iid}: period_tier '{i.get('period_tier')}'")
        if len(i.get("key_stats") or []) != 2:
            errors.append(f"{iid}: key_stats 는 2개여야 함")
        for s in i.get("key_stats") or []:
            if s.get("direction") not in STAT_DIRECTIONS:
                errors.append(f"{iid}: key_stat direction '{s.get('direction')}'")
        for r in i.get("effect_rows") or []:
            if r.get("direction") not in EFFECT_DIRECTIONS:
                errors.append(f"{iid}: effect_row direction '{r.get('direction')}'")
        for vid in i.get("visual_ids") or []:
            if vid not in visuals:
                errors.append(f"{iid}: 미등록 시각자료 {vid}")

    id_set = set(issue_ids)
    steady_ids = {s["id"] for s in steady}
    for i in issues:
        for sid in i.get("steady_ids") or []:
            if sid not in steady_ids:
                errors.append(f"{i['id']}: 미등록 스테디 {sid}")
    for s in steady:
        for vid in s.get("visual_ids") or []:
            if vid not in visuals:
                errors.append(f"{s['id']}: 미등록 시각자료 {vid}")
        for entry in s.get("timeline") or []:
            for link in entry.get("links") or []:
                if link.get("issue_id") not in id_set:
                    errors.append(f"{s['id']}: 타임라인 링크 {link.get('issue_id')} 미해석")

    for g in glossary:
        if not g.get("term") or not g.get("easy"):
            errors.append(f"용어 항목 불완전: {g}")

    if errors:
        raise ValueError("MVP 콘텐츠 검증 실패:\n- " + "\n- ".join(errors[:30]))


def _resolve_visuals(ids: list[str], visuals: dict) -> list[dict]:
    return [{"id": vid, **visuals[vid]} for vid in ids or []]


def _importance(issues: list[dict]) -> dict[str, int]:
    """파급도순 = 기간등급(이번 주 > 1개월 > 연간) + 등급 내 사건 최신순."""
    out: dict[str, int] = {}
    for tier, base in TIER_BASE.items():
        tiered = sorted((i for i in issues if i["period_tier"] == tier),
                        key=lambda i: i.get("event_at", ""), reverse=True)
        for rank, issue in enumerate(tiered):
            out[issue["id"]] = base + len(tiered) - rank
    return out


def _issue_card(issue: dict, meta: dict, visuals: dict,
                importance: dict[str, int]) -> dict:
    emoji = {c["code"]: c["emoji"] for c in meta["categories"]}
    resolved = _resolve_visuals(issue.get("visual_ids") or [], visuals)
    first_chart = next((v for v in resolved if v.get("kind", "chart") == "chart"), None)
    return {
        "id": issue["id"],
        "title": issue["title"],
        "one_liner": issue["title"],   # MVP 카드는 한 줄 요약이 곧 제목 (프런트가 중복 숨김)
        "why_now": None,
        "impact_line": issue.get("impact_line"),
        "raw_title": issue["title"],
        "category": issue["category"],
        "status": "active",
        "origin": "curated",
        "sources": 1,
        "importance": importance[issue["id"]],
        "last_update": issue["event_at"],
        "event_at": issue["event_at"],
        "has_visual": first_chart is not None,
        "headline_stat": None,
        "spark": _spark(first_chart),
        "icon": emoji.get(issue["category"], "📰"),
        # v4
        "period_tier": issue["period_tier"],
        "date_label": issue["date_label"],
        "date_label_short": issue.get("date_label_short") or issue["date_label"],
        "key_stats": issue["key_stats"],
        "steady_ids": issue.get("steady_ids") or [],
    }


def _issue_detail(issue: dict, card: dict, visuals: dict,
                  gloss_entries: list) -> dict:
    resolved = _resolve_visuals(issue.get("visual_ids") or [], visuals)
    first_chart = next((v for v in resolved if v.get("kind", "chart") == "chart"), None)
    effect_rows = issue.get("effect_rows") or []
    texts = [issue["title"], *(issue.get("details") or []),
             *(r.get("text", "") for r in effect_rows),
             *(r.get("basis", "") for r in effect_rows),
             *(issue.get("tips") or []), issue.get("impact_line") or ""]
    return {
        **card,
        "details": issue.get("details") or [],
        "effects": [r["text"] for r in effect_rows],   # 레거시 폴백 미러
        "effect_rows": effect_rows,
        "tips": issue.get("tips") or [],
        "model": "curated",
        "visual": first_chart,
        "visuals": resolved,
        "anchors": [],
        "headlines": [],
        "timeline": [],
        "glossary": _terms_in(gloss_entries, *texts),
        "related": [],
        "created_at": issue["event_at"],
    }


def _steady_item(s: dict, visuals: dict) -> dict:
    resolved = _resolve_visuals(s.get("visual_ids") or [], visuals)
    first_chart = next((v for v in resolved if v.get("kind", "chart") == "chart"), None)
    return {
        "id": s["id"],
        "icon": s.get("icon"),
        "title": s["title"],
        "one_liner": s["definition"],
        "status_note": s.get("tagline"),
        "visual": first_chart,
        "detail": [],   # 레거시 필드 (구 프런트 호환)
        # v4 6블록
        "definition": s["definition"],
        "score": s.get("score") or [],
        "story": s.get("story") or [],
        "timeline": s.get("timeline") or [],
        "impact": s.get("impact") or [],
        "next_up": s.get("next_up") or [],
        "visuals": resolved,
    }


def export_all(out_dir: Path | None = None) -> dict:
    meta = _load("meta.yaml")
    visuals = _load("visuals.yaml")["visuals"]
    issues = _load("issues.yaml")["issues"]
    steady = _load("steady.yaml")["items"]
    glossary = _load("glossary.yaml")["terms"]

    _validate(meta, visuals, issues, steady, glossary)

    gloss_entries = _glossary_entries(glossary)
    importance = _importance(issues)
    cards = [_issue_card(i, meta, visuals, importance) for i in issues]
    cards.sort(key=lambda c: c["importance"], reverse=True)

    out = out_dir or OUT
    (out / "issues").mkdir(parents=True, exist_ok=True)

    index = {
        "schema_version": SCHEMA_VERSION,
        # 큐레이션 스냅샷 — 결정적 산출을 위해 빌드 시각 대신 기준일 고정
        "generated_at": f"{meta['base_date']}T00:00:00+09:00",
        "attribution": meta["attribution"],
        "categories": meta["categories"],
        "scoreboard": meta["scoreboard"],   # 저장 전용 (프런트 미렌더)
        "issues": cards,
        "steady": [_steady_item(s, visuals) for s in steady],
    }
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    by_id = {i["id"]: i for i in issues}
    for card in cards:
        detail = _issue_detail(by_id[card["id"]], card, visuals, gloss_entries)
        (out / "issues" / f"{card['id']}.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")

    # 콘텐츠에서 빠진 이슈 상세 파일 정리 (id 변경/삭제 대응)
    live = {c["id"] for c in cards}
    removed = 0
    for f in (out / "issues").glob("*.json"):
        if f.stem not in live:
            f.unlink()
            removed += 1
    return {"issues": len(cards), "steady": len(steady),
            "visuals": len(visuals), "glossary": len(glossary), "removed": removed}


if __name__ == "__main__":
    stats = export_all()
    print(f"[export_mvp] OK {json.dumps(stats, ensure_ascii=False)} → {OUT}")
