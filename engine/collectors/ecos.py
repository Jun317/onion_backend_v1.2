"""한국은행 ECOS 수집 (설계서 §02-4, §05) — 기준금리·원/달러·국내 CPI.

한은 자체 작성 통계만 상업 무료 — 통계코드별 확인은 사용자 체크리스트.
급행: 기준금리 변동 → RATE, CPI 신규 관측 → MACRO.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import cfg, env
from ..util.http import get_json
from .base import load_state, save_state, write_express

BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

_FMT = {"D": "%Y%m%d", "M": "%Y%m", "Q": "%Y%m", "A": "%Y"}


def _range(cycle: str, days: int = 400) -> tuple[str, str]:
    now = datetime.now(timezone.utc) + timedelta(hours=9)  # KST
    fmt = _FMT.get(cycle, "%Y%m")
    return (now - timedelta(days=days)).strftime(fmt), now.strftime(fmt)


def fetch_series(api_key: str, stat: str, item: str, cycle: str, count: int = 60) -> list[dict]:
    start, end = _range(cycle)
    url = f"{BASE}/{api_key}/json/kr/1/{count}/{stat}/{cycle}/{start}/{end}/{item}"
    data = get_json(url)
    rows = (data or {}).get("StatisticSearch", {}).get("row", [])
    out = []
    for r in rows:
        try:
            out.append({"time": r["TIME"], "value": float(r["DATA_VALUE"])})
        except (KeyError, ValueError, TypeError):
            continue
    return out


def make_rate_event(spec: dict, latest: dict, prev: dict) -> dict:
    period = latest["time"][:4] + "-" + latest["time"][4:6]
    delta = round(latest["value"] - prev["value"], 4)
    return {
        "category": "RATE", "entity": spec.get("entity", "한국은행"), "period": period,
        "title": f"한국은행 기준금리 {'인하' if delta < 0 else '인상'} ({latest['value']:.2f}%)",
        "anchors": [
            {"entity": "한국은행", "metric": "기준금리", "value": latest["value"],
             "unit": "%", "prev": prev["value"], "period": period, "source": "ECOS"},
            {"entity": "한국은행", "metric": "변동폭", "value": abs(delta),
             "unit": "%p", "prev": None, "period": period, "source": "ECOS"}],
        "timeline": {"kind": "official",
                     "title": f"한국은행 기준금리 {latest['value']:.2f}% ({period})",
                     "source": "ECOS", "url": "https://ecos.bok.or.kr"},
    }


def make_macro_event(spec: dict, latest: dict, prev: dict) -> dict:
    period = latest["time"][:4] + "-" + latest["time"][4:6]
    return {
        "category": "MACRO", "entity": spec.get("entity", "KR"), "period": period,
        "title": f"한국 소비자물가 {period} 발표",
        "anchors": [{"entity": "KR", "metric": "소비자물가지수", "value": latest["value"],
                     "unit": "", "prev": prev["value"], "period": period, "source": "ECOS"}],
        "timeline": {"kind": "official", "title": f"ECOS 소비자물가 {latest['time']} 신규 관측",
                     "source": "ECOS", "url": "https://ecos.bok.or.kr"},
    }


def collect() -> int:
    api_key = env("ECOS_API_KEY")
    if not api_key:
        print("[ecos] ECOS_API_KEY 없음 — skip")
        return 0
    series = cfg()["collect"]["ecos"]["series"]
    state = load_state("ecos")
    n = 0
    for name, spec in series.items():
        rows = fetch_series(api_key, spec["stat"], str(spec["item"]), spec["cycle"])
        if not rows:
            continue
        latest = rows[-1]
        prev = state.get(name)  # {time, value}
        state[name] = latest
        if prev is None or not spec.get("express"):
            continue
        event = None
        if spec["express"] == "RATE" and latest["value"] != prev["value"]:
            event = make_rate_event(spec, latest, prev)
        elif spec["express"] == "MACRO" and latest["time"] != prev["time"]:
            event = make_macro_event(spec, latest, prev)
        if event and write_express(f"ecos_{name}_{latest['time']}", event):
            n += 1
    save_state("ecos", state)
    return n
