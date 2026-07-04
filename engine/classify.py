"""카테고리 분류 3단 폴백 (설계서 §04-⑦).

① 규칙: 급행 이슈는 트리거=카테고리 (express.py 가 생성 시 이미 세팅)
② 키워드: categories.yaml 사전 매칭 (canonical_title 우선, 멤버 제목 보조)
③ 임베딩: 카테고리별 프로토타입 문장 3개 평균 벡터 vs centroid cos argmax ≥ proto_min → 미만 ETC
"""
from __future__ import annotations

import sqlite3

import numpy as np

from .config import categories, cfg
from .embed import build_input, from_blob


def keyword_category(title: str, member_titles: list[str]) -> str | None:
    cats = categories()
    texts = [title.lower()] + [t.lower() for t in member_titles]
    for cat, spec in cats.items():
        if cat == "ETC":
            continue
        for kw in spec.get("keywords", []):
            k = str(kw).lower()
            if k and any(k in t for t in texts):
                return cat
    return None


class PrototypeClassifier:
    def __init__(self, embedder):
        cats = categories()
        self.names = [c for c in cats if c != "ETC"]
        protos = []
        for c in self.names:
            sents = cats[c].get("prototypes", [])
            vecs = embedder.encode([build_input(s, "") for s in sents])
            m = np.mean(vecs, axis=0)
            protos.append(m / max(float(np.linalg.norm(m)), 1e-12))
        self.protos = np.stack(protos)

    def classify(self, centroid: np.ndarray) -> str:
        sims = self.protos @ centroid
        best = int(np.argmax(sims))
        if float(sims[best]) >= cfg()["classify"]["proto_min"]:
            return self.names[best]
        return "ETC"


def classify_all(conn: sqlite3.Connection, embedder) -> int:
    """미분류(ETC) 군집 이슈만 분류. 급행 이슈는 ①규칙으로 이미 확정 — 건드리지 않음."""
    rows = conn.execute(
        "SELECT id, canonical_title, centroid FROM issue "
        "WHERE origin='cluster' AND category='ETC' AND status != 'archived'").fetchall()
    if not rows:
        return 0
    proto = None
    n = 0
    for r in rows:
        members = [m["title"] for m in conn.execute(
            "SELECT title FROM article WHERE issue_id=? LIMIT 10", (r["id"],))]
        cat = keyword_category(r["canonical_title"] or "", members)
        if cat is None and r["centroid"]:
            if proto is None:
                proto = PrototypeClassifier(embedder)
            cat = proto.classify(from_blob(r["centroid"]))
        if cat and cat != "ETC":
            conn.execute("UPDATE issue SET category=? WHERE id=?", (cat, r["id"]))
            n += 1
    return n
