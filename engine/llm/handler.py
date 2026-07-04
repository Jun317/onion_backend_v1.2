"""LLM 가공 오케스트레이션 (설계서 §6-4) — fact_hash 캐시 · 일일 캡 · 폴백.

호출 트리거: 신규 발행 또는 fact_hash 변경 이슈만. 캐시 히트 시 호출 0.
실패 시 1회 재생성 → 템플릿 요약(anchor 조립) + 검수 큐.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from ..config import cfg
from ..db import bump_daily_counter, daily_counter, now_iso
from ..viz import allowed_for_category
from .client import LlmClient
from .prompt import build_payload, system_prompt
from .validate import parse_output, validate


def fact_hash(payload: dict) -> str:
    basis = json.dumps({"a": payload.get("anchors", []),
                        "h": payload.get("headlines", [])},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def template_output(payload: dict) -> dict:
    """최종 폴백 — anchor 값 기계 조립 (LLM 문체 규칙 비적용, model='template')."""
    details = []
    for a in payload.get("anchors", [])[:4]:
        if a.get("value") is None:
            continue
        line = f"{a.get('entity', '')} {a.get('metric', '')}: {a['value']}{a.get('unit', '')}"
        if a.get("prev") is not None:
            line += f" (직전 {a['prev']}{a.get('unit', '')})"
        details.append(line.strip())
    if not details:
        details = [h for h in payload.get("headlines", [])[:3]]
    head = (payload.get("headlines") or ["새 소식"])[0]
    allowed = allowed_for_category(payload.get("category", "ETC"))
    return {"one_liner": head[:30], "details": details[:5],
            "visual_type": allowed[0] if allowed else "none", "effects": []}


def _issues_needing_llm(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """발행된(active/stale) 이슈 전부 — fact_hash 변경 여부는 페이로드로 재계산해 판정.
    (멤버 추가로 headlines 가 바뀌면 재가공해야 하므로 저장된 해시만으론 감지 불가)"""
    return conn.execute(
        "SELECT * FROM issue WHERE status IN ('active','stale') "
        "ORDER BY last_update DESC").fetchall()


def _save(conn: sqlite3.Connection, issue_id: str, fh: str, out: dict, model: str) -> None:
    conn.execute(
        "INSERT INTO llm_output(issue_id,fact_hash,one_liner,details_json,visual_type,"
        "effects_json,model,created_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(issue_id) DO UPDATE SET fact_hash=excluded.fact_hash, "
        "one_liner=excluded.one_liner, details_json=excluded.details_json, "
        "visual_type=excluded.visual_type, effects_json=excluded.effects_json, "
        "model=excluded.model, created_at=excluded.created_at",
        (issue_id, fh, out["one_liner"], json.dumps(out["details"], ensure_ascii=False),
         out.get("visual_type", "none"), json.dumps(out.get("effects", []), ensure_ascii=False),
         model, now_iso()))
    conn.execute("UPDATE issue SET fact_hash=? WHERE id=?", (fh, issue_id))


def process_all(conn: sqlite3.Connection, client: LlmClient) -> dict:
    c = cfg()["llm"]
    stats = {"generated": 0, "cached": 0, "template": 0, "deferred": 0}

    for issue in _issues_needing_llm(conn):
        payload = build_payload(conn, issue)
        fh = fact_hash(payload)
        existing = conn.execute("SELECT fact_hash FROM llm_output WHERE issue_id=?",
                                (issue["id"],)).fetchone()
        if existing and existing["fact_hash"] == fh:
            conn.execute("UPDATE issue SET fact_hash=? WHERE id=?", (fh, issue["id"]))
            stats["cached"] += 1
            continue

        if daily_counter(conn, "llm_calls") >= c["daily_cap"]:
            stats["deferred"] += 1  # 초과분 다음 사이클 이월 (§6-4)
            continue

        allowed = allowed_for_category(issue["category"])
        sysp = system_prompt(issue["category"])
        out, model = None, ""
        for _ in range(2):  # 최초 1회 + 재생성 1회
            bump_daily_counter(conn, "llm_calls")
            text, model = client.generate(sysp, payload)
            if text is None:
                break  # 전 프로바이더 실패 → 템플릿
            parsed = parse_output(text)
            errors = validate(parsed, payload, allowed) if parsed else ["JSON 파스 실패"]
            if not errors:
                out = parsed
                break
            print(f"[llm] {issue['id']} 검증 실패: {errors[:3]}")

        if out is None:
            out, model = template_output(payload), "template"
            conn.execute("INSERT INTO review_queue(issue_id,reason,at) VALUES(?,?,?) "
                         "ON CONFLICT(issue_id) DO UPDATE SET reason=excluded.reason, "
                         "at=excluded.at",
                         (issue["id"], "LLM 검증 실패 → 템플릿 폴백", now_iso()))
            stats["template"] += 1
        else:
            stats["generated"] += 1
        _save(conn, issue["id"], fh, out, model)

    return stats
