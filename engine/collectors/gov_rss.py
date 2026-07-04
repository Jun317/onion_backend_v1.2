"""부처 보도자료 RSS (설계서 §02-6) — 공공저작물, 유일하게 원문 인용 가능 소스.

피드 URL 은 config.yaml collect.gov_rss.feeds — 사이트 개편이 잦아 사용자 확인 필요.
개별 피드 실패는 격리 (하나 죽어도 나머지 수집).
"""
from __future__ import annotations

from ..config import cfg
from ..util.http import get_text
from ..util.rss import parse_feed
from .base import append_articles


def collect() -> int:
    total = 0
    for feed in cfg()["collect"]["gov_rss"]["feeds"]:
        text = get_text(feed["url"])
        if not text:
            print(f"[gov_rss] fetch 실패: {feed['source']} — 격리하고 계속")
            continue
        items = [{
            "url": e["link"], "title": e["title"], "lead": e["summary"],
            "source": feed["source"], "published_at": e["published"], "lang": feed["lang"],
        } for e in parse_feed(text)]
        total += append_articles(f"gov_{feed['source']}", "official", items)
    return total
