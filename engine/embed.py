"""임베딩 (설계서 §04-③) — multilingual-e5-small 384d, fp16 BLOB.

입력 = "query: {title}. {lead}" · L2 정규화 · 배치 64.
오프라인 테스트/dry-run 은 FakeEmbedder (결정적 합성 벡터, 모델·네트워크 불필요).
"""
from __future__ import annotations

import hashlib

import numpy as np

from .config import cfg, env


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float16).tobytes()


def from_blob(blob: bytes, dim: int | None = None) -> np.ndarray:
    d = dim or cfg()["embedding"]["dim"]
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)[:d]


def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.maximum(n, 1e-12)


def build_input(title: str, lead: str) -> str:
    prefix = cfg()["embedding"]["prefix"]
    text = f"{title}. {lead}".strip().rstrip(".") if lead else title
    return f"{prefix}{text}"


class FakeEmbedder:
    """텍스트 해시 기반 결정적 벡터 (테스트/dry-run 전용, 모델·네트워크 0).

    '같은 주제 문장 = 가까운 벡터' 를 흉내내기 위해 단어별 접두사 bag
    (한국어 조사 흡수: '기준금리를' → 기준, 기준금, 기준금리, ...) 을
    고정 랜덤 벡터로 투영해 평균한다 — 클러스터링 로직 검증에 충분."""

    def __init__(self, dim: int | None = None):
        self.dim = dim or cfg()["embedding"]["dim"]
        self._cache: dict[str, np.ndarray] = {}

    def _feat_vec(self, feat: str) -> np.ndarray:
        v = self._cache.get(feat)
        if v is None:
            seed = int.from_bytes(hashlib.sha1(feat.encode("utf-8")).digest()[:4], "big")
            rng = np.random.default_rng(seed)
            v = rng.standard_normal(self.dim).astype(np.float32)
            self._cache[feat] = v
        return v

    @staticmethod
    def _features(text: str) -> list[str]:
        import re
        words = re.findall(r"[0-9a-z가-힣.%]+", text.lower())
        feats: list[str] = []
        for w in words:
            for ln in range(2, len(w) + 1):
                feats.append(w[:ln])
            if len(w) < 2:
                feats.append(w)
        return feats or [text.lower()]

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            feats = self._features(t)
            for f in feats:
                out[i] += self._feat_vec(f)
            out[i] /= max(1, len(feats))
        out = _l2(out)
        # e5 계열의 '코사인 0.75~0.95 압축' 분포 모사: 공통 bias 성분 혼합
        # cos' ≈ 0.65 + 0.35·cos → 동일 주제쌍이 tau_join(0.82) 위로 올라온다
        bias = self._feat_vec("__COMMON_BIAS__")
        bias = bias / max(float(np.linalg.norm(bias)), 1e-12)
        out = np.sqrt(0.35) * out + np.sqrt(0.65) * bias[None, :]
        return _l2(out)


class StEmbedder:
    """sentence-transformers 실모델 (Actions/로컬 전용 — 최초 1회 HF 다운로드)."""

    def __init__(self):
        from sentence_transformers import SentenceTransformer  # 지연 import
        self.model = SentenceTransformer(cfg()["embedding"]["model"])

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, batch_size=cfg()["embedding"]["batch_size"],
                                 normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


def get_embedder(fake: bool | None = None):
    if fake is None:
        fake = env("EMBEDDING_BACKEND", "st") == "fake"
    return FakeEmbedder() if fake else StEmbedder()


def embed_articles(conn, embedder, limit: int | None = None) -> int:
    """embedding NULL 인 기사 임베딩 (배치). 반환: 처리 건수."""
    lim = limit or cfg()["pipeline"]["max_articles_per_run"]
    rows = conn.execute(
        "SELECT id, title, lead FROM article WHERE embedding IS NULL "
        "ORDER BY collected_at DESC LIMIT ?", (lim,)).fetchall()
    if not rows:
        return 0
    texts = [build_input(r["title"], r["lead"] or "") for r in rows]
    vecs = embedder.encode(texts)
    for r, v in zip(rows, vecs):
        conn.execute("UPDATE article SET embedding=? WHERE id=?", (to_blob(v), r["id"]))
    return len(rows)
