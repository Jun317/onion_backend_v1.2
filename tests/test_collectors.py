from engine.collectors.base import append_articles, norm_url, url_hash, write_express
from engine.collectors.dart import report_period
from engine.collectors.edgar import parse_entry
from engine.collectors.gdelt import map_articles, parse_seendate
from engine.util.rss import parse_feed

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>테스트</title>
<item><title>보도자료 &lt;b&gt;제목&lt;/b&gt;</title><link>https://gov.kr/1</link>
<description>요약문</description><pubDate>Fri, 04 Jul 2026 09:00:00 +0900</pubDate></item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>EDGAR</title>
<entry><title>APPLE INC (8-K)</title>
<link rel="alternate" href="https://sec.gov/acc?accession_number=0000320193-26-000001&amp;CIK=320193"/>
<summary>Item 2.02 Results of Operations</summary>
<updated>2026-07-04T00:00:00Z</updated></entry></feed>"""


def test_rss_and_atom_parse():
    rss = parse_feed(RSS_SAMPLE)
    assert rss[0]["title"] == "보도자료 제목"          # HTML 태그 제거
    assert rss[0]["published"].startswith("2026-07-04T00:00:00")  # KST→UTC
    atom = parse_feed(ATOM_SAMPLE)
    assert atom[0]["link"].startswith("https://sec.gov/acc")


def test_edgar_parse_entry():
    entry = parse_feed(ATOM_SAMPLE)[0]
    info = parse_entry(entry, "8-K")
    assert info["accession"] == "0000320193-26-000001"
    assert info["cik"] == "320193"
    assert info["company"].startswith("APPLE")


def test_gdelt_mapping():
    data = {"articles": [
        {"url": "https://a.com/1", "title": "제목", "domain": "a.com",
         "seendate": "20260704T010203Z", "language": "Korean"},
        {"url": "", "title": "버려짐"},
    ]}
    arts = map_articles(data)
    assert len(arts) == 1 and arts[0]["lang"] == "ko"
    assert parse_seendate("20260704T010203Z") == "2026-07-04T01:02:03+00:00"
    assert map_articles(None) == []


def test_dart_report_period():
    assert report_period("분기보고서 (2026.03)", "20260515") == "2026-1Q"
    assert report_period("잠정실적", "20260704") == "2026-3Q"   # 접수일 폴백


def test_norm_url_and_hash():
    a = "https://EX.com/path?utm_source=x&id=1#frag"
    b = "https://ex.com/path?id=1"
    assert norm_url(a) == b and url_hash(a) == url_hash(b)


def test_append_articles_seen_state(tmp_path):
    items = [{"url": "https://x.com/1", "title": "제목", "lead": "요약", "lang": "ko"}]
    assert append_articles("src", "wire", items, data_dir=tmp_path) == 1
    assert append_articles("src", "wire", items, data_dir=tmp_path) == 0   # seen → skip
    month_files = list((tmp_path / "raw").rglob("src.jsonl"))
    assert len(month_files) == 1


def test_write_express_idempotent(tmp_path):
    ev = {"category": "RATE", "entity": "한국은행", "period": "2026-07", "title": "t"}
    assert write_express("k1", ev, data_dir=tmp_path) is True
    assert write_express("k1", ev, data_dir=tmp_path) is False
