"""경량 RSS/Atom 파서 (stdlib xml.etree) — feedparser 의존 제거.

RSS 2.0 <item> 과 Atom <entry> 를 공통 스키마로 파싱한다:
  {title, link, summary, published}  (published = ISO8601 UTC or None)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _to_iso(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    # RFC822 (RSS pubDate)
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        pass
    # ISO8601 (Atom updated/published)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_feed(text: str) -> list[dict]:
    """RSS/Atom 문자열 → 엔트리 리스트. 파싱 실패 시 빈 리스트 (소스 격리)."""
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError:
        # 흔한 오염(선행 잡음/BOM) 1회 구제 시도
        m = re.search(r"<(rss|feed)[\s>]", text or "")
        if not m:
            return []
        try:
            root = ET.fromstring(text[m.start():])
        except ET.ParseError:
            return []

    entries: list[dict] = []

    for item in root.iter("item"):  # RSS 2.0
        entries.append({
            "title": _strip_html(item.findtext("title") or ""),
            "link": (item.findtext("link") or "").strip(),
            "summary": _strip_html(item.findtext("description") or ""),
            "published": _to_iso(item.findtext("pubDate") or item.findtext(
                "{http://purl.org/dc/elements/1.1/}date")),
        })

    for entry in root.iter(f"{ATOM_NS}entry"):  # Atom
        link = ""
        for l in entry.findall(f"{ATOM_NS}link"):
            if l.get("rel") in (None, "alternate"):
                link = l.get("href", "")
                break
        entries.append({
            "title": _strip_html(entry.findtext(f"{ATOM_NS}title") or ""),
            "link": link.strip(),
            "summary": _strip_html(entry.findtext(f"{ATOM_NS}summary")
                                   or entry.findtext(f"{ATOM_NS}content") or ""),
            "published": _to_iso(entry.findtext(f"{ATOM_NS}published")
                                 or entry.findtext(f"{ATOM_NS}updated")),
        })

    return [e for e in entries if e["title"] and e["link"]]
