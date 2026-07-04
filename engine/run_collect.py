"""L1 수집 러너 — GitHub Actions cron 에서 호출 (로컬 실행도 동일).

usage: python -m engine.run_collect --group fast|slow|all
  fast (30분): dart, edgar, gdelt, gov_rss   ← 공시·뉴스
  slow (1시간): fred, ecos, krx_price        ← 지표
수집기 하나가 죽어도 나머지는 계속 (소스 격리).
"""
from __future__ import annotations

import argparse
import sys

from .collectors import dart, ecos, edgar, fred, gdelt, gov_rss, krx_price

GROUPS = {
    "fast": [("dart", dart.collect), ("edgar", edgar.collect),
             ("gdelt", gdelt.collect), ("gov_rss", gov_rss.collect)],
    "slow": [("fred", fred.collect), ("ecos", ecos.collect),
             ("krx_price", krx_price.collect)],
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["fast", "slow", "all"], default="all")
    args = ap.parse_args(argv)

    targets = GROUPS["fast"] + GROUPS["slow"] if args.group == "all" else GROUPS[args.group]
    failures = 0
    for name, fn in targets:
        try:
            n = fn()
            print(f"[collect] {name}: {n}")
        except Exception as e:  # noqa: BLE001 — 소스 격리
            failures += 1
            print(f"[collect] {name} FAILED: {e}", file=sys.stderr)
    # 전 소스 실패 시에만 비정상 종료 (부분 실패는 정상 — 다음 사이클 재시도)
    return 1 if failures == len(targets) else 0


if __name__ == "__main__":
    raise SystemExit(main())
