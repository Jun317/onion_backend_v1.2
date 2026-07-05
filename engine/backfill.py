"""과거 굵직한 뉴스 백필 (1회성) — 파이프라인 검증용 데이터 주입.

published_at 은 실제 과거 날짜, collected_at 은 현재 → L2 가 일반 수집분과
똑같이 처리한다 (정규화→중복제거→임베딩→군집→발행→LLM). 별도 경로 없음.

  ① FRED·ECOS: 지난 N개월 관측치 전체 조회 → 값이 변동한 시점마다 급행
     RATE/MACRO 이벤트 생성 (실수치·실날짜, 수집기와 동일 키로 멱등)
  ② data/seed/major_events.yaml: 이벤트별 GDELT 과거 시간창 조회로 실기사 확보,
     2건 미만이면 시드에 수기 작성된 헤드라인으로 보충

usage:
  python -m engine.backfill                 # 기본 12개월
  python -m engine.backfill --months 6
  python -m engine.backfill --skip-gdelt    # 네트워크 최소화 (FRED/ECOS/시드만)
실행 후 다음 pipeline 사이클이 자동 처리한다 (Actions 는 1시간 주기).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .collectors import ecos, fred, gdelt
from .collectors.base import append_articles, load_state, save_state, write_express
from .config import ROOT, cfg, env
from .util.http import get_json

SEED_PATH = ROOT / "data" / "seed" / "major_events.yaml"


def _bcfg() -> dict:
    return cfg().get("backfill", {})


# --- ① FRED ------------------------------------------------------------------

def fred_observations(series_id: str, api_key: str, start: str,
                      units: str = "lin") -> list[dict]:
    """관측치 전체 (오름차순). 결측('.') 제외."""
    data = get_json(fred.BASE, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "asc", "observation_start": start, "units": units,
    })
    out = []
    for o in (data or {}).get("observations", []):
        if o.get("value") not in (None, "", "."):
            out.append({"date": o["date"], "value": float(o["value"])})
    return out


def backfill_fred(months: int) -> int:
    api_key = env("FRED_API_KEY")
    if not api_key:
        print("[backfill:fred] FRED_API_KEY 없음 — skip")
        return 0
    b = _bcfg()
    min_delta = float(b.get("rate_min_delta", 0.05))
    macro_months = int(b.get("macro_months", 3))
    start = (datetime.now(timezone.utc) - timedelta(days=months * 30)).date().isoformat()
    macro_cutoff = (datetime.now(timezone.utc)
                    - timedelta(days=macro_months * 31)).date().isoformat()

    state = load_state("fred")
    n = 0
    for sid, spec in cfg()["collect"]["fred"]["series"].items():
        express = spec.get("express")
        units = "pc1" if express == "MACRO" else "lin"
        obs = fred_observations(sid, api_key, start, units)
        if not obs:
            continue
        if express:
            prev = None
            for o in obs:
                event = None
                if prev is not None:
                    if express == "RATE" and abs(o["value"] - prev["value"]) >= min_delta:
                        event = fred.make_rate_event(sid, spec, o, prev)
                    elif express == "MACRO" and o["date"][:7] != prev["date"][:7] \
                            and o["date"] >= macro_cutoff:
                        event = fred.make_macro_event(sid, spec, o)
                if event:
                    event["created_at"] = f"{o['date']}T00:00:00+00:00"  # 실제 날짜 유지
                    if write_express(f"fred_{sid}_{o['date']}", event):
                        n += 1
                prev = o
        state[sid] = obs[-1]  # 수집기가 같은 이벤트를 재발행하지 않도록 기준점 갱신
    save_state("fred", state)
    print(f"[backfill:fred] 급행 이벤트 {n}건")
    return n


# --- ① ECOS ------------------------------------------------------------------

def backfill_ecos(months: int) -> int:
    api_key = env("ECOS_API_KEY")
    if not api_key:
        print("[backfill:ecos] ECOS_API_KEY 없음 — skip")
        return 0
    macro_months = int(_bcfg().get("macro_months", 3))
    macro_cutoff = (datetime.now(timezone.utc)
                    - timedelta(days=macro_months * 31)).strftime("%Y%m")

    state = load_state("ecos")
    n = 0
    for name, spec in cfg()["collect"]["ecos"]["series"].items():
        express = spec.get("express")
        rows = sorted(
            ecos.fetch_series(api_key, spec["stat"], str(spec["item"]), spec["cycle"],
                              count=max(60, months * 31)),
            key=lambda r: r["time"])
        if not rows:
            continue
        if express:
            prev = None
            for row in rows:
                event = None
                if prev is not None:
                    if express == "RATE" and row["value"] != prev["value"]:
                        event = ecos.make_rate_event(spec, row, prev)
                    elif express == "MACRO" and row["time"] != prev["time"] \
                            and row["time"][:6] >= macro_cutoff:
                        event = ecos.make_macro_event(spec, row, prev)
                if event:
                    t = row["time"]
                    event["created_at"] = (f"{t[:4]}-{t[4:6]}-{t[6:8] or '01'}"
                                           "T00:00:00+00:00") if len(t) >= 6 else None
                    if write_express(f"ecos_{name}_{t}", event):
                        n += 1
                prev = row
        state[name] = rows[-1]
    save_state("ecos", state)
    print(f"[backfill:ecos] 급행 이벤트 {n}건")
    return n


# --- ② 시드 이벤트 (GDELT 과거 조회 + 수기 헤드라인) ---------------------------

def fetch_gdelt_window(query: str, date: str, window_days: int, max_records: int) -> list[dict]:
    """GDELT DOC 2.0 과거 시간창 조회 — 실기사·실URL."""
    d = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    fmt = "%Y%m%d%H%M%S"
    data = get_json(gdelt.ENDPOINT, params={
        "query": query, "mode": "ArtList", "format": "json",
        "maxrecords": str(max_records), "sort": "HybridRel",
        "startdatetime": (d - timedelta(days=window_days)).strftime(fmt),
        "enddatetime": (d + timedelta(days=window_days)).strftime(fmt),
    })
    return gdelt.map_articles(data)


def backfill_seed(skip_gdelt: bool = False, seed_path: Path | None = None) -> dict:
    path = seed_path or SEED_PATH
    if not path.exists():
        print(f"[backfill:seed] {path} 없음 — skip")
        return {"events": 0, "articles": 0, "express": 0}
    b = _bcfg()
    window = int(b.get("window_days", 3))
    per_event = int(b.get("gdelt_per_event", 25))

    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stats = {"events": 0, "articles": 0, "express": 0}
    for ev in doc.get("events", []):
        stats["events"] += 1
        arts: list[dict] = []
        if ev.get("gdelt_query") and not skip_gdelt:
            try:
                arts = fetch_gdelt_window(ev["gdelt_query"], ev["date"], window, per_event)
            except Exception as e:  # noqa: BLE001 — 소스 하나 실패해도 계속
                print(f"[backfill:seed] {ev['id']} GDELT 실패: {e}")
        if len(arts) < 2:  # 과거 창 조회가 빈약하면 수기 헤드라인으로 보충
            arts += [{
                "url": a["url"], "title": a["title"], "lead": a.get("lead", ""),
                "source": a["source"], "published_at": a["published_at"],
                "lang": a.get("lang", "ko"),
            } for a in ev.get("articles", [])]
        stats["articles"] += append_articles("seed", "wire", arts)

        if ev.get("express"):
            event = dict(ev["express"])
            event.setdefault("created_at", f"{ev['date']}T00:00:00+00:00")
            if write_express(f"seed_{ev['id']}", event):
                stats["express"] += 1
    print(f"[backfill:seed] {stats}")
    return stats


# --- 진입점 -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=int(_bcfg().get("months", 12)))
    ap.add_argument("--skip-gdelt", action="store_true",
                    help="GDELT 과거 조회 생략 (FRED/ECOS/시드 수기분만)")
    args = ap.parse_args(argv)

    total_express = backfill_fred(args.months) + backfill_ecos(args.months)
    seed = backfill_seed(skip_gdelt=args.skip_gdelt)
    print(f"[backfill] 완료 — 급행 {total_express + seed['express']}건, "
          f"기사 {seed['articles']}건. 다음 pipeline 사이클(1시간 주기)이 자동 처리합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
