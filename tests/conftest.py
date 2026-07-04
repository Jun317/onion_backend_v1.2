"""공용 픽스처 — 전 테스트 오프라인 (네트워크·키·실모델 0)."""
from __future__ import annotations

import numpy as np
import pytest

from engine.db import connect
from engine.embed import to_blob


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    yield c
    c.close()


def unit_vec(dim: int = 384, seed: int = 0, base: np.ndarray | None = None,
             noise: float = 0.0) -> np.ndarray:
    """테스트용 단위 벡터. base 주면 base + noise·(단위 노이즈) — cos ≈ 1/√(1+noise²)."""
    rng = np.random.default_rng(seed)
    v = base.copy() if base is not None else rng.standard_normal(dim).astype(np.float32)
    if noise:
        n = rng.standard_normal(dim).astype(np.float32)
        v = v + noise * (n / np.linalg.norm(n))
    return (v / np.linalg.norm(v)).astype(np.float32)


def insert_article(conn, aid: str, *, title="제목", source="src", tier="wire",
                   vec=None, entity_keys='[]', published_at="2026-07-04T00:00:00+00:00",
                   collected_at=None, issue_id=None, is_dup=0, simhash=0, lang="ko"):
    from engine.db import now_iso
    conn.execute(
        "INSERT INTO article(id,source,tier,url,url_hash,title,lead,published_at,lang,"
        "simhash,embedding,issue_id,is_dup,entity_keys,collected_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, source, tier, f"https://t.co/{aid}", aid, title, "", published_at, lang,
         simhash, to_blob(vec) if vec is not None else None, issue_id, is_dup,
         entity_keys, collected_at or now_iso()))


def insert_issue(conn, iid: str, *, title="이슈", category="ETC", status="candidate",
                 origin="cluster", centroid=None, entity_keys='[]', frozen=0,
                 anchor_key=None, created_at=None, last_update=None):
    from engine.db import now_iso
    now = now_iso()
    conn.execute(
        "INSERT INTO issue(id,canonical_title,category,status,origin,centroid,entity_keys,"
        "created_at,last_update,frozen,anchor_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (iid, title, category, status, origin,
         to_blob(centroid) if centroid is not None else None, entity_keys,
         created_at or now, last_update or now, frozen, anchor_key))
