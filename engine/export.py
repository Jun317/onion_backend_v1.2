"""정적 JSON export (설계서 §03 L3) — out/index.json + out/issues/{id}.json.

프런트(front/index.html)는 이 파일들만 fetch 한다. archived 는 제외.
out/debug/* 는 모니터링 대시보드용 진단 export (전 상태 이슈·판정 근거·LLM 이력 포함).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .config import ROOT, cfg, categories
from .db import daily_counter, meta_get, now_iso
from .embed import from_blob
from .glossary import terms_in_parts
from .viz import REGISTRY, build_visual

OUT = ROOT / "out"

# out/index.json 스키마 버전 — 필드 추가·형태 변경 시 올린다 (프런트 호환성 협상용)
SCHEMA_VERSION = 2


def _fmt_num(v: float) -> str:
    """표기용 숫자 — 정수면 천단위 콤마, 소수는 2자리까지 (뒤 0 제거)."""
    if v == int(v):
        return f"{int(v):,}"
    return f"{round(v, 2):,}".rstrip("0").rstrip(".")


def _headline_stat(conn: sqlite3.Connection, issue: sqlite3.Row) -> dict | None:
    """카드/히어로 핵심 숫자 — 최신 anchor 1건. 표기 문자열까지 여기서 완성한다
    (단위·자릿수 통일을 위해 프런트 계산 금지, redesignspec §5)."""
    # '변동폭' 은 본 지표의 보조값 — 대표 숫자로 부적합 (viz._earnings_groups 와 동일 제외)
    a = conn.execute(
        "SELECT entity, metric, value, unit, prev FROM numeric_anchor "
        "WHERE issue_id=? AND value IS NOT NULL AND metric != '변동폭' "
        "ORDER BY observed_at DESC, id DESC LIMIT 1", (issue["id"],)).fetchone()
    if not a:
        return None
    unit = a["unit"] or ""
    stat = {
        "label": f"{a['entity']} {a['metric']}".strip(),
        "value": _fmt_num(a["value"]),
        "unit": unit,
        "delta_text": None,
        "direction": "flat",
        "prev_text": None,
    }
    if a["prev"] is not None:
        delta = a["value"] - a["prev"]
        stat["direction"] = "up" if delta > 0 else "down" if delta < 0 else "flat"
        sign = "+" if delta > 0 else "-" if delta < 0 else "±"
        delta_unit = "%p" if unit == "%" else unit  # 퍼센트 지표의 차이는 %p 로 표기
        stat["delta_text"] = f"{sign}{_fmt_num(abs(delta))}{delta_unit}"
        stat["prev_text"] = f"직전 {_fmt_num(a['prev'])}{unit}"
    return stat


def _spark(visual: dict | None) -> list[float] | None:
    """visual.series 의 v 값 최근 5–8개 — 카드 스파크라인 소스. 없으면 None."""
    series = (visual or {}).get("series") or []
    vals = [p["v"] for p in series if isinstance(p.get("v"), (int, float))]
    if len(vals) < 2:
        return None
    return vals[-8:]


def _issue_icon(issue: sqlite3.Row) -> str:
    """이슈 아이콘 이모지 1개 — config.yaml icons: 개체 우선, 카테고리 폴백 (저작권 프리)."""
    icons = cfg().get("icons", {}) or {}
    entity_map = icons.get("entities") or {}
    for key in json.loads(issue["entity_keys"] or "[]"):
        if key in entity_map:
            return entity_map[key]
    by_category = icons.get("categories") or {}
    return by_category.get(issue["category"]) or icons.get("default", "📰")


def _related_issues(conn: sqlite3.Connection, issue: sqlite3.Row, limit: int = 5) -> list[dict]:
    """공유 개체 또는 같은 카테고리의 다른 발행 이슈 — 맥락 링크용 (중요도 순)."""
    kset = set(json.loads(issue["entity_keys"] or "[]"))
    out = []
    for r in conn.execute(
            "SELECT id, canonical_title, category, entity_keys FROM issue "
            "WHERE id != ? AND status IN ('active','stale') "
            "ORDER BY importance DESC, last_update DESC LIMIT 60", (issue["id"],)):
        shared = kset & set(json.loads(r["entity_keys"] or "[]"))
        if shared or r["category"] == issue["category"]:
            out.append({"id": r["id"], "title": r["canonical_title"],
                        "category": r["category"], "shared": sorted(shared)})
        if len(out) >= limit:
            break
    return out


def _issue_card(conn: sqlite3.Connection, issue: sqlite3.Row) -> dict:
    o = conn.execute("SELECT * FROM llm_output WHERE issue_id=?", (issue["id"],)).fetchone()
    has_visual = bool(o and o["visual_type"] and o["visual_type"] != "none")
    # spark 용 시리즈 — build_visual 은 viz_cache(6h) 를 타므로 카드 단위 호출 부담 없음
    visual = build_visual(conn, o["visual_type"], issue["category"], issue["id"]) \
        if has_visual else None
    return {
        "id": issue["id"],
        # title = 짧고 직관적인 제목(LLM), one_liner = 더 길고 정보 있는 한 줄, why_now = 왜 중요한지
        "title": (o["title"] if o and o["title"] else issue["canonical_title"]),
        "one_liner": (o["one_liner"] if o and o["one_liner"] else issue["canonical_title"]),
        "why_now": o["why_now"] if o else None,
        "raw_title": issue["canonical_title"],   # 원 기사 제목 (참고)
        "category": issue["category"],
        "status": issue["status"],
        "origin": issue["origin"],
        "sources": issue["seen_sources"],
        "importance": issue["importance"],
        "last_update": issue["last_update"],
        "has_visual": has_visual,
        # v2 (redesignspec §5): 핵심 숫자 · 스파크 시리즈 · 이슈 아이콘
        "headline_stat": _headline_stat(conn, issue),
        "spark": _spark(visual),
        "icon": _issue_icon(issue),
    }


def _issue_detail(conn: sqlite3.Connection, issue: sqlite3.Row) -> dict:
    o = conn.execute("SELECT * FROM llm_output WHERE issue_id=?", (issue["id"],)).fetchone()
    timeline = [dict(r) for r in conn.execute(
        "SELECT kind, title, source, url, at FROM timeline_entry WHERE issue_id=? "
        "ORDER BY at DESC LIMIT 30", (issue["id"],))]
    headlines = [dict(r) for r in conn.execute(
        "SELECT title, source, url, published_at FROM article WHERE issue_id=? AND is_dup=0 "
        "ORDER BY published_at DESC LIMIT 10", (issue["id"],))]
    # 같은 (entity, metric, period) 는 최신 1건만 — 과거 중복 누적분 방어 (db._migrate 참고)
    anchors = [dict(r) for r in conn.execute(
        "SELECT entity, metric, value, unit, prev, period, source FROM numeric_anchor "
        "WHERE issue_id=? AND id IN (SELECT MAX(id) FROM numeric_anchor WHERE issue_id=? "
        "GROUP BY entity, metric, period) "
        "ORDER BY observed_at DESC LIMIT 12", (issue["id"], issue["id"]))]

    visual = None
    if o and o["visual_type"] and o["visual_type"] != "none":
        visual = build_visual(conn, o["visual_type"], issue["category"], issue["id"])

    details = json.loads(o["details_json"]) if o else []
    card = _issue_card(conn, issue)
    glossary = terms_in_parts(card["title"], card["one_liner"], card.get("why_now") or "",
                              *details)
    return {
        **card,
        "details": details,
        "effects": json.loads(o["effects_json"]) if o else [],
        "model": o["model"] if o else None,
        "visual": visual,
        "anchors": anchors,
        "headlines": headlines,
        "timeline": timeline,
        "glossary": glossary,
        "related": _related_issues(conn, issue),
        "created_at": issue["created_at"],
    }


def _load_steady(conn: sqlite3.Connection) -> list[dict]:
    """steady.yaml (수동 큐레이션) → index.json 최상위 steady 배열.

    검증 규칙:
      - refs.phrase 는 문단 text 에 반드시 포함 (아니면 ref 제거)
      - refs 는 issue_id 또는 anchor_key 로 참조 — 발행 중(active/stale)이 아니면 링크만 제거
      - visual_type 은 viz.REGISTRY 재사용 (실패 시 차트 없이 발행)
    """
    path = ROOT / "steady.yaml"
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        print(f"[export] steady.yaml 파싱 실패 — steady 생략: {e}")
        return []

    live = {r["id"] for r in conn.execute(
        "SELECT id FROM issue WHERE status IN ('active','stale')")}
    by_anchor = {r["anchor_key"]: r["id"] for r in conn.execute(
        "SELECT anchor_key, id FROM issue WHERE anchor_key IS NOT NULL "
        "AND status IN ('active','stale')")}

    out = []
    for item in raw.get("items", []):
        if not item.get("id") or not item.get("title"):
            continue
        visual = None
        vtype = item.get("visual_type")
        if vtype in REGISTRY:
            visual = build_visual(conn, vtype, REGISTRY[vtype]["cats"][0],
                                  f"steady:{item['id']}")
        detail = []
        for para in item.get("detail", []):
            text = str(para.get("text") or "")
            if not text:
                continue
            refs = []
            for ref in para.get("refs") or []:
                phrase = str(ref.get("phrase") or "")
                iid = ref.get("issue_id") or by_anchor.get(ref.get("anchor_key", ""))
                if phrase and phrase in text and iid in live:
                    refs.append({"phrase": phrase, "issue_id": iid})
            entry = {"text": text}
            if refs:
                entry["refs"] = refs
            detail.append(entry)
        out.append({"id": str(item["id"]), "icon": item.get("icon"),
                    "title": item["title"], "one_liner": item.get("one_liner", ""),
                    "status_note": item.get("status_note"), "visual": visual,
                    "detail": detail})
    return out


def export_all(conn: sqlite3.Connection, out_dir: Path | None = None) -> dict:
    out = out_dir or OUT
    (out / "issues").mkdir(parents=True, exist_ok=True)

    issues = conn.execute(
        "SELECT * FROM issue WHERE status IN ('active','stale') "
        "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, "
        "importance DESC, last_update DESC LIMIT 200").fetchall()

    index = {"schema_version": SCHEMA_VERSION,
             "generated_at": now_iso(),
             "attribution": "News metadata via GDELT (gdeltproject.org) · "
                            "Data: FRED, 한국은행 ECOS, DART, SEC EDGAR",
             "issues": [_issue_card(conn, i) for i in issues],
             "steady": _load_steady(conn)}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1),
                                    encoding="utf-8")

    live_ids = set()
    for i in issues:
        live_ids.add(i["id"])
        detail = _issue_detail(conn, i)
        (out / "issues" / f"{i['id']}.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")

    # archived 이슈 상세 파일 정리
    removed = 0
    for f in (out / "issues").glob("*.json"):
        if f.stem not in live_ids:
            f.unlink()
            removed += 1
    return {"issues": len(issues), "removed": removed}


# ============================================================================
# 진단 export — 모니터링 대시보드 (out/debug/*)
# ============================================================================

def _write(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _debug_summary(conn: sqlite3.Connection, current_stats: dict | None = None) -> dict:
    issue_counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM issue GROUP BY status")}
    art = conn.execute(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN is_dup=1 THEN 1 ELSE 0 END) AS dup,"
        " SUM(CASE WHEN is_dup=0 AND issue_id IS NOT NULL THEN 1 ELSE 0 END) AS assigned,"
        " SUM(CASE WHEN is_dup=0 AND issue_id IS NULL AND hold_count>0 THEN 1 ELSE 0 END) AS held,"
        " SUM(CASE WHEN is_dup=0 AND issue_id IS NULL AND hold_count=0 THEN 1 ELSE 0 END)"
        "   AS unassigned FROM article").fetchone()
    c = cfg()
    # 이번 실행은 아직 log_run 전 — 현재 사이클 통계를 이력 맨 앞에 합성 삽입
    history = json.loads(meta_get(conn, "run_history", "[]"))
    if current_stats is not None:
        history = [{"kind": "pipeline", "at": now_iso(), "ok": True,
                    "stats": current_stats}] + history
    return {
        "generated_at": now_iso(),
        "run_history": history,
        "issue_counts": issue_counts,
        "article_counts": {k: art[k] or 0 for k in
                           ("total", "dup", "assigned", "held", "unassigned")},
        "llm_calls_today": daily_counter(conn, "llm_calls"),
        "review_queue": conn.execute("SELECT COUNT(*) AS n FROM review_queue").fetchone()["n"],
        "config": {  # 판정에 쓰인 핵심 임계값 스냅샷
            "tau_join": c["cluster"]["tau_join"], "tau_hold": c["cluster"]["tau_hold"],
            "min_publish": c["cluster"]["min_publish"], "merge_sim": c["cluster"]["merge_sim"],
            "active_window_h": c["cluster"]["active_window_h"],
            "llm_daily_cap": c["llm"]["daily_cap"],
            "importance": c.get("importance", {}),
            "dart_express_major_only": c["collect"]["dart"].get("express_major_only", False),
        },
    }


def _debug_articles(conn: sqlite3.Connection, hours: int = 72) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT id, source, tier, url, title, published_at, lang, issue_id, is_dup, "
        "hold_count, entity_keys, last_sim, last_sim_issue, collected_at, "
        "embedding IS NOT NULL AS has_embedding FROM article "
        "WHERE collected_at >= ? ORDER BY collected_at DESC LIMIT 1500", (cutoff,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["entity_keys"] = json.loads(d["entity_keys"] or "[]")
        d["state"] = ("중복" if d["is_dup"] else
                      "배정" if d["issue_id"] else
                      "보류" if d["hold_count"] > 0 else "미배정")
        out.append(d)
    return out


def _debug_issue_detail(conn: sqlite3.Connection, issue: sqlite3.Row) -> dict:
    centroid = from_blob(issue["centroid"]) if issue["centroid"] else None
    members = []
    for a in conn.execute(
            "SELECT id, source, tier, url, title, published_at, is_dup, embedding "
            "FROM article WHERE issue_id=? ORDER BY published_at DESC LIMIT 50",
            (issue["id"],)):
        sim = None
        if centroid is not None and a["embedding"]:
            sim = round(float(centroid @ from_blob(a["embedding"])), 4)
        members.append({"id": a["id"], "source": a["source"], "tier": a["tier"],
                        "url": a["url"], "title": a["title"],
                        "published_at": a["published_at"], "is_dup": a["is_dup"],
                        "sim_to_centroid": sim})
    anchors = [dict(r) for r in conn.execute(
        "SELECT entity, metric, value, unit, prev, period, source, trust, observed_at "
        "FROM numeric_anchor WHERE issue_id=? ORDER BY observed_at DESC LIMIT 20",
        (issue["id"],))]
    timeline = [dict(r) for r in conn.execute(
        "SELECT kind, title, source, url, at FROM timeline_entry WHERE issue_id=? "
        "ORDER BY at DESC LIMIT 50", (issue["id"],))]

    o = conn.execute("SELECT * FROM llm_output WHERE issue_id=?", (issue["id"],)).fetchone()
    llm = None
    if o:
        llm = {"model": o["model"], "created_at": o["created_at"],
               "payload": json.loads(o["payload_json"] or "{}"),
               "raw_response": o["raw_response"],
               "attempts": json.loads(o["validation_json"] or "[]"),
               "output": {"title": o["title"],
                          "one_liner": o["one_liner"],
                          "why_now": o["why_now"],
                          "details": json.loads(o["details_json"] or "[]"),
                          "effects": json.loads(o["effects_json"] or "[]"),
                          "visual_type": o["visual_type"]},
               "fact_hash": o["fact_hash"]}
    review = conn.execute("SELECT reason, at FROM review_queue WHERE issue_id=?",
                          (issue["id"],)).fetchone()

    gloss_src = []
    if llm:
        o2 = llm["output"]
        gloss_src = [o2.get("title") or "", o2.get("one_liner") or "",
                     o2.get("why_now") or "", *(o2.get("details") or [])]
    else:
        gloss_src = [issue["canonical_title"] or ""]

    return {"id": issue["id"], "title": issue["canonical_title"],
            "category": issue["category"], "status": issue["status"],
            "origin": issue["origin"], "frozen": issue["frozen"],
            "anchor_key": issue["anchor_key"],
            "entity_keys": json.loads(issue["entity_keys"] or "[]"),
            "seen_sources": issue["seen_sources"], "importance": issue["importance"],
            "created_at": issue["created_at"], "last_update": issue["last_update"],
            "members": members, "anchors": anchors, "timeline": timeline,
            "llm": llm, "review": dict(review) if review else None,
            "glossary": terms_in_parts(*gloss_src),
            "related": _related_issues(conn, issue)}


def export_debug(conn: sqlite3.Connection, out_dir: Path | None = None,
                 current_stats: dict | None = None) -> dict:
    """모니터링 대시보드용 진단 JSON — 전 상태 이슈 + 판정 근거 + LLM 이력."""
    out = (out_dir or OUT) / "debug"
    (out / "issues").mkdir(parents=True, exist_ok=True)

    _write(out / "summary.json", _debug_summary(conn, current_stats))
    _write(out / "articles.json",
           {"generated_at": now_iso(), "articles": _debug_articles(conn)})

    # 이슈: 살아있는 전부 + 최근 7일 archived (병합·수명 종료 추적용)
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    issues = conn.execute(
        "SELECT * FROM issue WHERE status IN ('candidate','active','stale') "
        "OR (status='archived' AND last_update >= ?) "
        "ORDER BY importance DESC, last_update DESC LIMIT 500", (week_ago,)).fetchall()
    index = []
    live_ids = set()
    for i in issues:
        live_ids.add(i["id"])
        detail = _debug_issue_detail(conn, i)
        _write(out / "issues" / f"{i['id']}.json", detail)
        out_o = detail["llm"]["output"] if detail["llm"] else {}
        index.append({k: detail[k] for k in
                      ("id", "title", "category", "status", "origin", "seen_sources",
                       "importance", "entity_keys", "created_at", "last_update")}
                     | {"members": len(detail["members"]),
                        "llm_title": out_o.get("title"),      # 정제된 직관 제목
                        "one_liner": out_o.get("one_liner"),  # 정보 있는 한 줄
                        "llm_model": detail["llm"]["model"] if detail["llm"] else None,
                        "in_review": detail["review"] is not None})
    _write(out / "issues.json", {"generated_at": now_iso(), "issues": index})

    removed = 0
    for f in (out / "issues").glob("*.json"):
        if f.stem not in live_ids:
            f.unlink()
            removed += 1

    # LLM 프로토콜: 카테고리별 시스템 프롬프트 전문 (가공에 쓰인 그대로)
    from .llm.prompt import system_prompt
    _write(out / "llm.json", {
        "generated_at": now_iso(),
        "daily_cap": cfg()["llm"]["daily_cap"],
        "calls_today": daily_counter(conn, "llm_calls"),
        "models": {"gemini": cfg()["llm"]["gemini_model"], "groq": cfg()["llm"]["groq_model"],
                   "fallback": "template (anchor 기계 조립)"},
        "banned_phrases": cfg()["llm"]["banned_phrases"],
        "system_prompts": {cat: system_prompt(cat) for cat in categories()},
    })

    reviews = [dict(r) for r in conn.execute(
        "SELECT issue_id, reason, at FROM review_queue ORDER BY at DESC")]
    for r in reviews:
        try:
            r["reason"] = json.loads(r["reason"])
        except (ValueError, TypeError):
            pass  # 구버전 문자열 사유 호환
    _write(out / "review.json", {"generated_at": now_iso(), "queue": reviews})

    return {"issues": len(issues), "removed": removed, "reviews": len(reviews)}
