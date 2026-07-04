"""HTTP 유틸 — 공통 User-Agent, 지수 백오프 재시도. 모든 외부 호출은 여기로."""
from __future__ import annotations

import time

import requests

from ..config import user_agent

DEFAULT_TIMEOUT = 30


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        retries: int = 3, timeout: int = DEFAULT_TIMEOUT) -> requests.Response | None:
    """GET + 재시도(429/5xx/네트워크). 최종 실패 시 None — 수집기는 소스 단위로 격리 실패."""
    h = {"User-Agent": user_agent()}
    if headers:
        h.update(headers)
    for attempt in range(retries + 1):
        try:
            res = requests.get(url, params=params, headers=h, timeout=timeout)
            if res.status_code == 429 or res.status_code >= 500:
                raise RuntimeError(f"HTTP {res.status_code}")
            return res
        except Exception as e:  # noqa: BLE001 — 소스별 격리를 위해 광범위 캐치
            if attempt >= retries:
                print(f"[http] give up {url.split('?')[0]}: {e}")
                return None
            wait = 2.0 * (2 ** attempt)
            time.sleep(wait)
    return None


def get_json(url: str, **kw) -> dict | list | None:
    res = get(url, **kw)
    if res is None:
        return None
    try:
        return res.json()
    except ValueError:
        print(f"[http] non-JSON from {url.split('?')[0]}")
        return None


def get_text(url: str, **kw) -> str | None:
    res = get(url, **kw)
    return res.text if res is not None else None
