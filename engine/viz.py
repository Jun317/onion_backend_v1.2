"""시각자료 레지스트리 (설계서 §07) — LLM 은 타입만 고르고 데이터는 코드가 채운다.

visual_type 9종 화이트리스트. 카테고리 밖 타입은 생성 자체를 막는다 (환각·불필요 차트 방지).
시리즈는 viz_cache 6h 캐시 (같은 타입 이슈끼리 공유).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .config import cfg, env
from .db import now_iso
from .util.http import get_json

REGISTRY: dict[str, dict] = {
    "kr_base_rate": {"cats": ["RATE"], "chart": "step",
                     "title": "한국은행 기준금리 (최근 3년)", "unit": "%", "source": "한국은행 ECOS"},
    "us_policy_rate": {"cats": ["RATE"], "chart": "step",
                       "title": "미 연방기금금리 (최근 3년)", "unit": "%", "source": "FRED"},
    "us_10y": {"cats": ["RATE", "MARKET"], "chart": "line",
               "title": "미 국채 10년물 금리 (최근 1년)", "unit": "%", "source": "FRED"},
    "us_cpi_yoy": {"cats": ["MACRO"], "chart": "line",
                   "title": "미국 CPI 상승률 YoY (최근 3년)", "unit": "%", "source": "FRED"},
    "kr_cpi_yoy": {"cats": ["MACRO"], "chart": "line",
                   "title": "한국 소비자물가 상승률 YoY (최근 3년)", "unit": "%", "source": "한국은행 ECOS"},
    "usdkrw": {"cats": ["FX"], "chart": "line",
               "title": "원/달러 환율 (최근 6개월)", "unit": "원", "source": "한국은행 ECOS"},
    "earnings_quarterly": {"cats": ["EARNINGS"], "chart": "bar",
                           "title": "분기 실적", "unit": "", "source": "DART/EDGAR"},
    "kospi_close": {"cats": ["MARKET"], "chart": "line",
                    "title": "KOSPI 종가 (최근 3개월)", "unit": "pt", "source": "금융위 시세정보"},
}


def allowed_for_category(category: str) -> list[str]:
    return [t for t, spec in REGISTRY.items() if category in spec["cats"]]


# --- 캐시 --------------------------------------------------------------------

def _cache_get(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT payload, fetched_at FROM viz_cache WHERE cache_key=?",
                       (key,)).fetchone()
    if not row:
        return None
    ttl = timedelta(hours=cfg()["viz"]["cache_h"])
    try:
        if datetime.fromisoformat(row["fetched_at"]) + ttl < datetime.now(timezone.utc):
            return None
    except ValueError:
        return None
    return json.loads(row["payload"])


def _cache_put(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    conn.execute("INSERT INTO viz_cache(cache_key,payload,fetched_at) VALUES(?,?,?) "
                 "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, "
                 "fetched_at=excluded.fetched_at",
                 (key, json.dumps(payload, ensure_ascii=False), now_iso()))


# --- 소스별 fetcher ----------------------------------------------------------

def _fred_series(series_id: str, years: float, units: str = "lin") -> list[dict]:
    key = env("FRED_API_KEY")
    if not key:
        return []
    start = (datetime.now(timezone.utc) - timedelta(days=int(365 * years))).strftime("%Y-%m-%d")
    data = get_json("https://api.stlouisfed.org/fred/series/observations", params={
        "series_id": series_id, "api_key": key, "file_type": "json",
        "observation_start": start, "units": units})
    out = []
    for o in (data or {}).get("observations", []):
        if o.get("value") in (None, "", "."):
            continue
        try:
            out.append({"t": o["date"][:7], "v": float(o["value"])})
        except ValueError:
            continue
    # 월 단위 다운샘플 (일 단위 시리즈 완화 — 마지막 값 대표)
    monthly: dict[str, float] = {}
    for p in out:
        monthly[p["t"]] = p["v"]
    return [{"t": t, "v": v} for t, v in sorted(monthly.items())]


def _ecos_series(name: str, months: int, yoy: bool = False) -> list[dict]:
    from .collectors.ecos import fetch_series
    key = env("ECOS_API_KEY")
    spec = cfg()["collect"]["ecos"]["series"].get(name)
    if not key or not spec:
        return []
    rows = fetch_series(key, spec["stat"], str(spec["item"]), spec["cycle"],
                        count=max(60, months + 14))
    pts = [{"t": f"{r['time'][:4]}-{r['time'][4:6]}", "v": r["value"]} for r in rows
           if len(r["time"]) >= 6]
    if yoy:
        by_t = {p["t"]: p["v"] for p in pts}
        out = []
        for p in pts:
            y, m = int(p["t"][:4]), int(p["t"][5:7])
            base = by_t.get(f"{y - 1:04d}-{m:02d}")
            if base:
                out.append({"t": p["t"], "v": round((p["v"] / base - 1) * 100, 2)})
        pts = out
    return pts[-months:]


def _kospi_series(months: int) -> list[dict]:
    from .collectors.base import load_state
    series = load_state("krx_price").get("kospi", [])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=months * 31)).strftime("%Y-%m-%d")
    return [p for p in series if p["t"] >= cutoff]


def _earnings_groups(conn: sqlite3.Connection, issue_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT metric, value, unit, period FROM numeric_anchor WHERE issue_id=? "
        "ORDER BY period, id", (issue_id,)).fetchall()
    # 같은 (metric, period) 중복은 마지막(최신 id) 값만 — 정정 공시 잔재 방어
    points: dict[str, dict[str, float]] = {}
    unit = ""
    for r in rows:
        if r["metric"] in ("변동폭",):
            continue
        points.setdefault(r["metric"], {})[r["period"]] = r["value"]
        unit = r["unit"] or unit
    return [{"name": m, "series": [{"t": t, "v": v} for t, v in list(s.items())[-4:]],
             "unit": unit} for m, s in points.items()]


# --- 메인 --------------------------------------------------------------------

def build_visual(conn: sqlite3.Connection, vtype: str, category: str,
                 issue_id: str) -> dict | None:
    """visual_type → 프런트가 즉시 렌더할 수 있는 visual dict (§07 형식). 실패 시 None."""
    spec = REGISTRY.get(vtype)
    if not spec or category not in spec["cats"]:
        return None  # 화이트리스트 밖 — 생성 금지

    cache_key = f"{vtype}:{issue_id}" if vtype == "earnings_quarterly" else vtype
    cached = _cache_get(conn, cache_key)
    if cached:
        return cached

    base = {"type": vtype, "chart": spec["chart"], "title": spec["title"],
            "unit": spec["unit"], "source": spec["source"]}
    if vtype == "kr_base_rate":
        base["series"] = _ecos_series("base_rate", 36)
    elif vtype == "us_policy_rate":
        base["series"] = _fred_series("DFF", 3)
    elif vtype == "us_10y":
        base["series"] = _fred_series("DGS10", 1)
    elif vtype == "us_cpi_yoy":
        base["series"] = _fred_series("CPIAUCSL", 3, units="pc1")
    elif vtype == "kr_cpi_yoy":
        base["series"] = _ecos_series("cpi", 36, yoy=True)
    elif vtype == "usdkrw":
        base["series"] = _ecos_series("usdkrw", 6)
    elif vtype == "kospi_close":
        base["series"] = _kospi_series(3)
    elif vtype == "earnings_quarterly":
        groups = _earnings_groups(conn, issue_id)
        if not groups:
            return None
        base["groups"] = groups
        base["unit"] = groups[0].get("unit", "")

    if not base.get("series") and not base.get("groups"):
        return None  # 키 없음/데이터 없음 → 차트 생략 (이슈는 그대로 발행)
    _cache_put(conn, cache_key, base)
    return base
