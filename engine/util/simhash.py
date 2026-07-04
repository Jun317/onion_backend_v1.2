"""제목 simhash64 — 근접중복 판정 (설계서 §04-②).

피처: 소문자 단어 토큰 + 문자 3-gram(한국어 대응). FNV-1a 64bit.
"""
from __future__ import annotations

import re

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK = (1 << 64) - 1


def _fnv1a(s: str) -> int:
    h = _FNV_OFFSET
    for b in s.encode("utf-8"):
        h = ((h ^ b) * _FNV_PRIME) & _MASK
    return h


def _features(title: str) -> list[str]:
    t = re.sub(r"\s+", " ", (title or "").lower()).strip()
    words = re.findall(r"[0-9a-z가-힣]+", t)
    feats = list(words)
    compact = "".join(words)
    feats += [compact[i:i + 3] for i in range(max(0, len(compact) - 2))]
    return feats or [t]


def simhash64(title: str) -> int:
    v = [0] * 64
    for f in _features(title):
        h = _fnv1a(f)
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= (1 << i)
    # SQLite INTEGER 는 signed 64-bit — 부호 있는 값으로 변환해 저장
    return out - (1 << 64) if out >= (1 << 63) else out


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & _MASK).count("1")
