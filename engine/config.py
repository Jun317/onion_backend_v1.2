"""config.yaml / entities.yaml / categories.yaml / .env 로더.

모든 모듈은 이 모듈의 cfg() 를 통해서만 설정을 읽는다 (하드코딩 금지 원칙).
.env 는 외부 패키지 없이 직접 파싱한다 (KEY=VALUE, # 주석).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    """`.env` 를 os.environ 에 주입 (이미 있는 키는 유지 — Actions Secrets 우선)."""
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


@lru_cache(maxsize=1)
def cfg() -> dict:
    load_env()
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def entities() -> list[dict]:
    """entities.yaml 의 모든 그룹을 [{key, aliases}] 로 평탄화."""
    data = yaml.safe_load((ROOT / "entities.yaml").read_text(encoding="utf-8"))
    out: list[dict] = []
    for group in data.values():
        out.extend(group or [])
    return out


@lru_cache(maxsize=1)
def entity_groups() -> dict[str, str]:
    """entity_key → 그룹명(institutions|kr_companies|us_companies) — 중요도 보너스 구분용."""
    data = yaml.safe_load((ROOT / "entities.yaml").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for group, items in data.items():
        for ent in items or []:
            out[str(ent["key"])] = group
    return out


@lru_cache(maxsize=1)
def categories() -> dict:
    return yaml.safe_load((ROOT / "categories.yaml").read_text(encoding="utf-8"))


def env(key: str, default: str = "") -> str:
    load_env()
    return os.environ.get(key, default)


def user_agent() -> str:
    return env("ONION_USER_AGENT", "onion-backend/1.2 (github actions)")
