"""LLM 가공 오케스트레이션 (설계서 §6-4) — fact_hash 캐시 · 일일 캡 · 폴백.

호출 트리거: 신규 발행 또는 fact_hash 변경 이슈만. 캐시 히트 시 호출 0.
실패 시 1회 재생성 → 템플릿 요약(anchor 조립) + 검수 큐.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from ..config import cfg
from ..db import bump_daily_counter, daily_counter, now_iso
from ..viz import allowed_for_category
from .client import LlmClient
from .prompt import build_payload, system_prompt
from .validate import parse_output, validate


def fact_hash(payload: dict) -> str:
    # "v": 2 — 출력 스키마/문체 개편(해요체·glossary) 시 솔트를 올려 전 이슈 1회 재가공 유도
    basis = json.dumps({"v": 2,
                        "a": payload.get("anchors", []),
                        "h": payload.get("headlines", [])},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _josa(word: str, with_final: str, without_final: str) -> str:
    """마지막 한글 음절의 받침 유무로 조사 선택. 비한글로 끝나면 받침 없음 취급."""
    for ch in reversed(word or ""):
        if 0xAC00 <= ord(ch) <= 0xD7A3:
            return with_final if (ord(ch) - 0xAC00) % 28 else without_final
        break
    return without_final


def _clip(text: str, limit: int) -> str:
    """길이 제한 절단 — 가능하면 단어 경계에서 자르고 말줄임표를 붙인다.
    ("Walmart Debunks 3 Myth" 처럼 어중간하게 잘린 제목 노출 방지)"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        at_word = cut.rsplit(" ", 1)[0].rstrip()
        # 너무 짧아지면(절반 미만) 단어 경계 포기 — 정보량 우선
        if len(at_word) >= limit // 2:
            cut = at_word
    return cut.rstrip() + "…"


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
    # 제목: 영어 헤드라인 절단 대신 anchor 기반 한국어 조립 우선 (번역 안 된 제목 방지).
    # 헤드라인 절단은 anchor 가 없을 때의 최후 수단.
    head = (payload.get("headlines") or ["새 소식"])[0]
    usable = [a for a in payload.get("anchors", [])
              if a.get("value") is not None and a.get("metric") != "변동폭"]
    if usable:
        a = usable[0]
        ent, met, unit = a.get("entity", ""), a.get("metric", ""), a.get("unit", "")
        title = _clip(f"{ent} {met} {a['value']}{unit}".strip(), 22)
        # one_liner 는 사용자에게 그대로 보이므로 해요체 문장으로 조립 (UX 라이팅 규칙)
        if a.get("prev") is not None:
            one_liner = _clip(
                f"{ent} {met}{_josa(met, '이', '가')} {a['prev']}{unit}에서 "
                f"{a['value']}{unit}{_josa(unit, '으로', '로')} 바뀌었어요".strip(), 50)
        else:
            one_liner = _clip(
                f"{ent} {met} {a['value']}{unit}{_josa(unit, '으로', '로')} 발표됐어요".strip(), 50)
    else:
        title, one_liner = _clip(head, 22), _clip(head, 50)
    allowed = allowed_for_category(payload.get("category", "ETC"))
    return {"title": title, "one_liner": one_liner,
            "why_now": "공식 발표 내용을 정리했어요.", "details": details[:5],
            "visual_type": allowed[0] if allowed else "none", "effects": [], "glossary": []}


def _issues_needing_llm(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """발행된(active/stale) 이슈 — 가공이 필요한 순서로.

    우선순위(번역 병목 완화): ① 아직 가공 안 된/템플릿 폴백 이슈(미번역) 먼저,
    ② 그다음 중요도. Groq 분당 한도 안에서 미번역부터 처리해 영어 잔존을 빨리 줄인다."""
    return conn.execute(
        "SELECT i.* FROM issue i LEFT JOIN llm_output o ON o.issue_id = i.id "
        "WHERE i.status IN ('active','stale') "
        "ORDER BY (o.model IS NULL OR o.model='template') DESC, "
        "i.importance DESC, i.last_update DESC").fetchall()


def _save(conn: sqlite3.Connection, issue_id: str, fh: str, out: dict, model: str,
          payload: dict | None = None, raw: str | None = None,
          attempts: list | None = None) -> None:
    conn.execute(
        "INSERT INTO llm_output(issue_id,fact_hash,one_liner,details_json,visual_type,"
        "effects_json,model,created_at,payload_json,raw_response,validation_json,title,why_now,"
        "glossary_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(issue_id) DO UPDATE SET fact_hash=excluded.fact_hash, "
        "one_liner=excluded.one_liner, details_json=excluded.details_json, "
        "visual_type=excluded.visual_type, effects_json=excluded.effects_json, "
        "model=excluded.model, created_at=excluded.created_at, "
        "payload_json=excluded.payload_json, raw_response=excluded.raw_response, "
        "validation_json=excluded.validation_json, title=excluded.title, "
        "why_now=excluded.why_now, glossary_json=excluded.glossary_json",
        (issue_id, fh, out["one_liner"], json.dumps(out["details"], ensure_ascii=False),
         out.get("visual_type", "none"), json.dumps(out.get("effects", []), ensure_ascii=False),
         model, now_iso(),
         json.dumps(payload or {}, ensure_ascii=False), raw or "",
         json.dumps(attempts or [], ensure_ascii=False),
         out.get("title", ""), out.get("why_now", ""),
         json.dumps(out.get("glossary", []), ensure_ascii=False)))
    conn.execute("UPDATE issue SET fact_hash=? WHERE id=?", (fh, issue_id))


def _template_backoff_ok(created_at: str | None, hours: float) -> bool:
    """template 폴백 이슈를 재시도해도 되는 시점인지 (마지막 시도 후 hours 경과)."""
    if not created_at:
        return True
    try:
        last = datetime.fromisoformat(created_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(hours=hours)


def process_all(conn: sqlite3.Connection, client: LlmClient) -> dict:
    c = cfg()["llm"]
    retry_h = float(c.get("template_retry_h", 3))
    interval = float(c.get("call_interval_s", 0))
    # 실호출(RealLlm)일 때만 시간·건수 예산으로 런타임을 제한 — Actions 30분 타임아웃 보호.
    # 남은 이슈는 다음 주기로 이월(deferred)되므로 매 주기 조금씩 소진된다.
    budget_s = float(c.get("max_seconds_per_run", 0)) if getattr(client, "network", False) else 0
    max_per_run = int(c.get("max_per_run", 0)) if getattr(client, "network", False) else 0
    start = time.monotonic()
    done = 0
    stats = {"generated": 0, "cached": 0, "template": 0, "deferred": 0, "backoff": 0}

    for issue in _issues_needing_llm(conn):
        payload = build_payload(conn, issue)
        fh = fact_hash(payload)
        existing = conn.execute(
            "SELECT fact_hash, model, created_at FROM llm_output WHERE issue_id=?",
            (issue["id"],)).fetchone()
        # 정상 가공분은 사실관계 불변이면 캐시 히트 (재호출 0).
        if existing and existing["fact_hash"] == fh and existing["model"] != "template":
            conn.execute("UPDATE issue SET fact_hash=? WHERE id=?", (fh, issue["id"]))
            stats["cached"] += 1
            continue
        # 템플릿 폴백은 재시도하되 매 주기가 아니라 template_retry_h 간격으로 (백오프).
        # 미번역 신규 이슈에 우선순위를 양보하고 Groq 분당 한도 낭비를 막는다.
        if (existing and existing["model"] == "template"
                and not _template_backoff_ok(existing["created_at"], retry_h)):
            stats["backoff"] += 1
            continue

        if daily_counter(conn, "llm_calls") >= c["daily_cap"]:
            stats["deferred"] += 1  # 초과분 다음 사이클 이월 (§6-4)
            continue

        # 런타임 예산 초과 시 남은 이슈는 이월 (실호출 경로만) — 타임아웃 방지
        if (budget_s and time.monotonic() - start >= budget_s) or (max_per_run and done >= max_per_run):
            stats["deferred"] += 1
            continue
        done += 1

        allowed = allowed_for_category(issue["category"])
        sysp = system_prompt(issue["category"])
        out, model, last_raw = None, "", None
        attempts: list[dict] = []  # 시도별 (model, errors) — 진단·검수 큐용
        for _ in range(2):  # 최초 1회 + 재생성 1회
            bump_daily_counter(conn, "llm_calls")
            text, model = client.generate(sysp, payload)
            if text is None:
                attempts.append({"model": model, "errors": ["전 프로바이더 응답 실패"]})
                break  # 전 프로바이더 실패 → 템플릿
            last_raw = text
            parsed = parse_output(text)
            errors = validate(parsed, payload, allowed) if parsed else ["JSON 파스 실패"]
            attempts.append({"model": model, "errors": errors})
            if not errors:
                out = parsed
                break
            print(f"[llm] {issue['id']} 검증 실패: {errors[:3]}")

        if out is None:
            out, model = template_output(payload), "template"
            reason = json.dumps({"summary": "LLM 검증 실패 → 템플릿 폴백",
                                 "attempts": attempts}, ensure_ascii=False)
            conn.execute("INSERT INTO review_queue(issue_id,reason,at) VALUES(?,?,?) "
                         "ON CONFLICT(issue_id) DO UPDATE SET reason=excluded.reason, "
                         "at=excluded.at",
                         (issue["id"], reason, now_iso()))
            stats["template"] += 1
        else:
            stats["generated"] += 1
        _save(conn, issue["id"], fh, out, model, payload, last_raw, attempts)

        # 호출 간 간격 — 무료 티어 분당 한도(RPM) 회피. 실제 네트워크 호출한 이슈만,
        # FakeLlm(테스트·dry-run)은 network=False 라 잠들지 않는다.
        if getattr(client, "network", False) and interval > 0:
            time.sleep(interval)

    return stats
