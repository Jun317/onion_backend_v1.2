"""FRED 수집 (설계서 §02-2, §05) — 미 기준금리(DFF)·10년물·CPI.

미 정부 출처 시리즈만 사용. 'Copyright' 표기 시리즈(S&P500 등) 금지 — 사용자 재확인 항목.
급행: DFF 값 변동 → RATE, CPIAUCSL 신규 관측 → MACRO.
"""
from __future__ import annotations

from ..config import cfg, env
from ..util.http import get_json
from .base import load_state, save_state, write_express

BASE = "https://api.stlouisfed.org/fred/series/observations"


def latest_observation(series_id: str, api_key: str, units: str = "lin") -> dict | None:
    data = get_json(BASE, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": "5", "units": units,
    })
    for obs in (data or {}).get("observations", []):
        if obs.get("value") not in (None, "", "."):
            return {"date": obs["date"], "value": float(obs["value"])}
    return None


def check_series(series_id: str, spec: dict, api_key: str, state: dict) -> dict | None:
    """신규/변동 관측 감지 → 급행 이벤트 dict 반환 (없으면 None). 순수 로직은 분리 테스트."""
    obs = latest_observation(series_id, api_key)
    if not obs:
        return None
    prev = state.get(series_id)  # {date, value}
    state[series_id] = obs
    if prev is None:
        return None  # 초회 관측은 기준점만 기록
    if spec.get("express") == "RATE" and obs["value"] != prev["value"]:
        return make_rate_event(series_id, spec, obs, prev)
    if spec.get("express") == "MACRO" and obs["date"] != prev["date"]:
        return make_macro_event(series_id, spec, obs)
    return None


def make_rate_event(series_id: str, spec: dict, obs: dict, prev: dict) -> dict:
    entity = spec.get("entity", "Fed")
    period = obs["date"][:7]
    delta_bp = round((obs["value"] - prev["value"]) * 100)
    return {
        "category": "RATE", "entity": entity, "period": period,
        "title": f"미 연준 기준금리 변동 ({obs['value']:.2f}%)",
        "anchors": [{"entity": entity, "metric": "기준금리", "value": obs["value"],
                     "unit": "%", "prev": prev["value"], "period": period, "source": "FRED"},
                    {"entity": entity, "metric": "변동폭", "value": delta_bp,
                     "unit": "bp", "prev": None, "period": period, "source": "FRED"}],
        "timeline": {"kind": "official", "title": f"FRED {series_id} {obs['date']} = {obs['value']}",
                     "source": "FRED", "url": f"https://fred.stlouisfed.org/series/{series_id}"},
    }


def make_macro_event(series_id: str, spec: dict, obs: dict) -> dict:
    entity = spec.get("entity", "US")
    period = obs["date"][:7]
    return {
        "category": "MACRO", "entity": entity, "period": period,
        "title": f"미국 CPI {period} 발표",
        "anchors": [{"entity": entity, "metric": "CPI(YoY%)", "value": obs["value"],
                     "unit": "%", "prev": None, "period": period, "source": "FRED"}],
        "timeline": {"kind": "official", "title": f"FRED {series_id} {obs['date']} 신규 관측",
                     "source": "FRED", "url": f"https://fred.stlouisfed.org/series/{series_id}"},
    }


def collect() -> int:
    api_key = env("FRED_API_KEY")
    if not api_key:
        print("[fred] FRED_API_KEY 없음 — skip")
        return 0
    series = cfg()["collect"]["fred"]["series"]
    state = load_state("fred")
    n = 0
    for sid, spec in series.items():
        # CPI 급행은 YoY 로 (pc1) — viz 와 동일 단위
        units = "pc1" if spec.get("express") == "MACRO" else "lin"
        obs = latest_observation(sid, api_key, units=units)
        if not obs:
            continue
        prev = state.get(sid)
        state[sid] = obs
        if prev is None or not spec.get("express"):
            continue
        event = None
        if spec["express"] == "RATE" and obs["value"] != prev["value"]:
            event = make_rate_event(sid, spec, obs, prev)
        elif spec["express"] == "MACRO" and obs["date"] != prev["date"]:
            event = make_macro_event(sid, spec, obs)
        if event:
            key = f"fred_{sid}_{obs['date']}"
            if write_express(key, event):
                n += 1
    save_state("fred", state)
    return n
