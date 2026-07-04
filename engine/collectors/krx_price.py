"""[선택 #8] 공공데이터포털 주식시세 — KOSPI 종가 (kospi_close viz 용).

키(DATA_GO_KR_API_KEY) 없으면 자동 skip. KRX 직접 데이터의 무료 대체재 (설계서 §02-8).
결과는 state 파일에 시계열로 누적 → viz.py 가 읽는다 (급행/기사 아님).
"""
from __future__ import annotations

from ..config import cfg, env
from ..util.http import get_json
from .base import load_state, save_state

URL = ("https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService"
       "/getStockMarketIndex")


def collect() -> int:
    if not cfg()["collect"]["krx_price"].get("enabled", True):
        return 0
    key = env("DATA_GO_KR_API_KEY")
    if not key:
        print("[krx_price] DATA_GO_KR_API_KEY 없음 — skip (선택 소스)")
        return 0
    data = get_json(URL, params={
        "serviceKey": key, "resultType": "json", "numOfRows": "70",
        "idxNm": "코스피", "beginBasDt": "",
    })
    items = ((data or {}).get("response", {}).get("body", {})
             .get("items", {}).get("item", []))
    series = []
    for it in items:
        try:
            d = str(it["basDt"])
            series.append({"t": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "v": float(it["clpr"])})
        except (KeyError, ValueError):
            continue
    if not series:
        return 0
    series.sort(key=lambda x: x["t"])
    state = load_state("krx_price")
    merged = {p["t"]: p["v"] for p in state.get("kospi", [])}
    merged.update({p["t"]: p["v"] for p in series})
    state["kospi"] = [{"t": t, "v": v} for t, v in sorted(merged.items())][-120:]
    save_state("krx_price", state)
    print(f"[krx_price] kospi {len(series)}건 갱신")
    return len(series)
