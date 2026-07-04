"""수집기 공통 — raw JSONL append, 급행 이벤트 파일, per-source 상태(멱등 커서).

L1 산출 규약 (설계서 §03):
  data/raw/YYYY-MM/{source}.jsonl   ← 기사/신호 append (본문 없음)
  data/express/{key}.json           ← 급행 트리거 (§05, 파일 존재 = 멱등)
  data/state/{source}.json          ← seen 키·마지막 관측값 (수집기 전용)
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import ROOT

DATA = ROOT / "data"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_TRACKING_PARAMS = re.compile(r"^(utm_[a-z]+|fbclid|gclid|cmpid|ref)$", re.I)


def norm_url(url: str) -> str:
    """URL 정규화: 스킴/호스트 소문자화, 추적 파라미터·프래그먼트 제거."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip()
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not _TRACKING_PARAMS.match(k)])
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha1(norm_url(url).encode("utf-8")).hexdigest()


def _lead(text: str, limit: int = 200) -> str:
    """RSS description 앞 200자만 — 저작권 방화벽 (본문 저장 금지)."""
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def append_articles(source: str, tier: str, items: list[dict], data_dir: Path | None = None) -> int:
    """기사 레코드를 월별 JSONL 에 append. seen 상태로 재수집 중복 방지."""
    data = data_dir or DATA
    state = load_state(source, data)
    seen: list[str] = state.get("seen", [])
    seen_set = set(seen)

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    out_dir = data / "raw" / month
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{source}.jsonl"

    n = 0
    with path.open("a", encoding="utf-8") as f:
        for it in items:
            h = url_hash(it["url"])
            if h in seen_set:
                continue
            rec = {
                "id": h, "source": it.get("source", source), "tier": tier,
                "url": norm_url(it["url"]), "title": (it.get("title") or "").strip(),
                "lead": _lead(it.get("lead") or ""),
                "published_at": it.get("published_at"), "lang": it.get("lang", "en"),
                "collected_at": now_iso(),
            }
            if not rec["title"]:
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            seen_set.add(h)
            seen.append(h)
            n += 1

    state["seen"] = seen[-5000:]  # 상태 파일 무한 성장 방지
    save_state(source, state, data)
    if n:
        print(f"[{source}] +{n} articles")
    return n


def write_express(key: str, event: dict, data_dir: Path | None = None) -> bool:
    """급행 이벤트 파일 생성. 파일명 = 멱등 키 (이미 있으면 skip)."""
    data = data_dir or DATA
    safe = re.sub(r"[^0-9A-Za-z가-힣._-]", "_", key)[:150]
    out_dir = data / "express"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{safe}.json"
    if path.exists():
        return False
    event = {"key": key, "created_at": now_iso(), **event}
    path.write_text(json.dumps(event, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[express] {key}")
    return True


def load_state(source: str, data_dir: Path | None = None) -> dict:
    p = (data_dir or DATA) / "state" / f"{source}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save_state(source: str, state: dict, data_dir: Path | None = None) -> None:
    p = (data_dir or DATA) / "state" / f"{source}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
