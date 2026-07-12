"""부처 보도자료 RSS (설계서 §02-6) — 공공저작물, 유일하게 원문 인용 가능 소스.

피드 URL 은 config.yaml collect.gov_rss.feeds — 사이트 개편이 잦아 사용자 확인 필요.
개별 피드 실패는 격리 (하나 죽어도 나머지 수집).
공지성 글(행사·인사·모집 등)은 exclude_regex 로 수집 단계에서 차단한다.
"""
from __future__ import annotations

import re

from ..config import cfg
from ..util.http import get_text
from ..util.rss import parse_feed
from .base import append_articles


def collect() -> int:
    c = cfg()["collect"]["gov_rss"]
    exclude = re.compile(c["exclude_regex"]) if c.get("exclude_regex") else None
    total = 0
    skipped = 0
    for feed in c["feeds"]:
        text = get_text(feed["url"])
        if not text:
            print(f"[gov_rss] fetch 실패: {feed['source']} — 격리하고 계속")
            continue
        items = []
        for e in parse_feed(text):
            if exclude and exclude.search(e["title"] or ""):
                skipped += 1  # 공지성 글 — 이슈 원료로 부적합
                continue
            items.append({
                "url": e["link"], "title": e["title"], "lead": e["summary"],
                "source": feed["source"], "published_at": e["published"], "lang": feed["lang"],
            })
        total += append_articles(f"gov_{feed['source']}", "official", items)
    if skipped:
        print(f"[gov_rss] 공지성 글 {skipped}건 수집 제외 (exclude_regex)")
    return total
