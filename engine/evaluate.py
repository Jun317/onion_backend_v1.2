"""군집 임계값(tau_join) 평가 — 황금세트로 정밀도/재현율/F1 측정.

data/goldset/pairs.yaml 의 라벨(같은 이슈여야 하나)과, 두 제목의 실제 코사인 유사도를
비교해 tau 후보별 성능을 표로 출력한다. tau_join 을 '추측'이 아니라 '측정'으로 고르는 도구.

usage:
  python -m engine.evaluate                 # 실모델(e5-small) — pip install sentence-transformers
  python -m engine.evaluate --fake          # FakeEmbedder (오프라인 스모크; 절대 수치는 무의미)
  python -m engine.evaluate --tau-min 0.74 --tau-max 0.90 --step 0.02
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import yaml

from .config import ROOT
from .embed import build_input, get_embedder

GOLDSET = ROOT / "data" / "goldset" / "pairs.yaml"


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def pair_similarities(pairs: list[dict], embedder) -> list[float]:
    va = embedder.encode([build_input(p["a"], "") for p in pairs])
    vb = embedder.encode([build_input(p["b"], "") for p in pairs])
    return [_cos(a, b) for a, b in zip(va, vb)]


def sweep(sims: list[float], labels: list[bool],
          tau_min: float, tau_max: float, step: float) -> list[dict]:
    rows = []
    tau = tau_min
    while tau <= tau_max + 1e-9:
        pred = [s >= tau for s in sims]
        tp = sum(1 for p, l in zip(pred, labels) if p and l)
        fp = sum(1 for p, l in zip(pred, labels) if p and not l)
        fn = sum(1 for p, l in zip(pred, labels) if not p and l)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        rows.append({"tau": round(tau, 3), "precision": round(prec, 3),
                     "recall": round(rec, 3), "f1": round(f1, 3),
                     "tp": tp, "fp": fp, "fn": fn})
        tau += step
    return rows


def evaluate(goldset_path: Path | None = None, fake: bool = False,
             tau_min: float = 0.72, tau_max: float = 0.90, step: float = 0.02) -> dict:
    path = goldset_path or GOLDSET
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pairs = doc.get("pairs", [])
    if not pairs:
        raise SystemExit(f"[evaluate] {path} 에 pairs 가 없습니다.")
    labels = [bool(p.get("same")) for p in pairs]
    sims = pair_similarities(pairs, get_embedder(fake=fake or None))
    rows = sweep(sims, labels, tau_min, tau_max, step)
    best = max(rows, key=lambda r: (r["f1"], r["tau"]))
    return {"n_pairs": len(pairs), "n_same": sum(labels), "sims": sims,
            "rows": rows, "best": best}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--tau-min", type=float, default=0.72)
    ap.add_argument("--tau-max", type=float, default=0.90)
    ap.add_argument("--step", type=float, default=0.02)
    args = ap.parse_args(argv)

    res = evaluate(fake=args.fake, tau_min=args.tau_min, tau_max=args.tau_max, step=args.step)
    print(f"쌍 {res['n_pairs']}개 (같은 이슈 {res['n_same']}개)"
          f"{'  [FAKE — 절대수치 무의미]' if args.fake else ''}")
    print(f"{'tau':>6} {'precision':>10} {'recall':>8} {'f1':>6}   tp/fp/fn")
    for r in res["rows"]:
        mark = " ←최고 F1" if r["tau"] == res["best"]["tau"] else ""
        print(f"{r['tau']:>6.2f} {r['precision']:>10.3f} {r['recall']:>8.3f} "
              f"{r['f1']:>6.3f}   {r['tp']}/{r['fp']}/{r['fn']}{mark}")
    b = res["best"]
    print(f"\n추천 tau_join ≈ {b['tau']}  (F1 {b['f1']}, precision {b['precision']}, "
          f"recall {b['recall']}) — config.yaml cluster.tau_join 후보")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
