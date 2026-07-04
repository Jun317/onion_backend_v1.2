"""L2 오케스트레이터 (설계서 §03·§04) — Actions cron/로컬 공용.

usage:
  python -m engine.run_pipeline              # 실모델 + 실 LLM (키 필요)
  python -m engine.run_pipeline --dry-run    # FakeEmbedder + FakeLLM + 픽스처 (네트워크 0)

사이클: raw 로드 → dedup → 급행 반영 → 임베딩(+급행 centroid 보강)
       → assign → seed → promote → classify → merge → lifecycle → LLM 가공 → export
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import cluster, dedup, express, lifecycle
from .classify import classify_all
from .collectors.base import DATA
from .config import ROOT
from .db import connect, log_run, prune
from .embed import embed_articles, get_embedder
from .export import export_all
from .llm.client import get_client
from .llm.handler import process_all as llm_process
from .normalize import load_recent_raw


def run(dry_run: bool = False, db_path: str | None = None,
        data_dir: Path | None = None, out_dir: Path | None = None) -> dict:
    conn = connect(db_path)
    data = data_dir or DATA
    stats: dict = {}
    try:
        # 1) 수집 산출 반영
        records = load_recent_raw(data)
        inserted = dedup.insert_articles(conn, records)
        stats["raw_loaded"] = len(records)
        stats["inserted"] = len(inserted)
        stats["near_dups"] = dedup.mark_near_duplicates(conn, inserted)

        # 2) 급행 이벤트 (군집 스킵 — §05)
        stats["express"] = express.process_all(conn, data)

        # 3) 임베딩 (+ 급행 이슈 centroid 보강)
        embedder = get_embedder(fake=dry_run or None)
        stats["embedded"] = embed_articles(conn, embedder)
        stats["express_centroids"] = express.ensure_centroids(conn, embedder)

        # 4) 군집: 편입 → 신규 → 발행
        stats["assign"] = cluster.assign(conn)
        stats["seeded"] = cluster.seed(conn)
        stats["promoted"] = cluster.promote(conn)

        # 5) 분류 · 병합 · 생애주기
        stats["classified"] = classify_all(conn, embedder)
        stats["merged"] = cluster.merge(conn)
        stats["lifecycle"] = lifecycle.tick(conn)

        # 6) LLM 가공 (fact_hash 캐시 · 일일 캡 · 폴백 — §06)
        client = get_client(fake=dry_run)
        stats["llm"] = llm_process(conn, client)

        # 7) export + 프루닝
        stats["export"] = export_all(conn, out_dir)
        stats["pruned"] = prune(conn)

        log_run(conn, "pipeline", True, stats)
        conn.commit()
        print("[pipeline] OK", json.dumps(stats, ensure_ascii=False))
        return stats
    except Exception as e:
        conn.rollback()
        log_run(conn, "pipeline", False, stats, error=str(e))
        conn.commit()
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="FakeEmbedder+FakeLLM, tests/fixtures 데이터, 임시 DB")
    args = ap.parse_args(argv)
    if args.dry_run:
        import shutil
        import tempfile

        from .dryrun import make_fixture_data
        tmp = Path(tempfile.mkdtemp(prefix="onion-dry-"))
        make_fixture_data(tmp / "data")
        out = ROOT / "out_dry"
        shutil.rmtree(out, ignore_errors=True)
        run(dry_run=True, db_path=str(tmp / "engine.db"),
            data_dir=tmp / "data", out_dir=out)
        print(f"[pipeline] dry-run 산출: {out}")
    else:
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
