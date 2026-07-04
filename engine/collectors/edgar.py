"""SEC EDGAR 수집 (설계서 §02-1, §05) — 미국 공시. 10-Q/10-K/8-K(2.02) → 급행.

getcurrent Atom 피드 폴링 (키 불필요, User-Agent 필수, 10 req/s 이하).
멱등 키 = accession number. 앵커 = XBRL companyconcept (best-effort).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..config import cfg
from ..util.http import get_json, get_text
from ..util.rss import parse_feed
from .base import write_express

CURRENT_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
               "&type={form}&company=&dateb=&owner=include&count=40&output=atom")
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:0>10}/us-gaap/{tag}.json"

REVENUE_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet"]
OPINC_TAGS = ["OperatingIncomeLoss"]

ACCESSION_RE = re.compile(r"accession[-_ ]?(?:number|no)?[=:\s]*([\d-]{18,20})", re.I)
CIK_RE = re.compile(r"CIK=(\d+)", re.I)


def parse_entry(entry: dict, form: str) -> dict | None:
    """Atom 엔트리 → {accession, company, cik} (순수 함수 — 테스트 대상)."""
    blob = f"{entry.get('link', '')} {entry.get('summary', '')} {entry.get('title', '')}"
    acc = ACCESSION_RE.search(blob)
    if not acc:
        m = re.search(r"/(\d{10}-\d{2}-\d{6})", blob)
        if not m:
            return None
        accession = m.group(1)
    else:
        accession = acc.group(1)
    cik_m = CIK_RE.search(blob)
    company = re.sub(rf"\s*\({form}\)\s*$", "", (entry.get("title") or "").strip())
    company = re.sub(r"\s*\(\d{7,10}\)\s*(\(.*\))?$", "", company).strip()
    return {"accession": accession, "company": company or "Unknown",
            "cik": cik_m.group(1) if cik_m else None, "link": entry.get("link", "")}


def latest_quarterlies(cik: str, tags: list[str], metric: str) -> list[dict]:
    """XBRL companyconcept 에서 최근 분기값 (최대 4개, USD)."""
    for tag in tags:
        data = get_json(CONCEPT_URL.format(cik=cik, tag=tag))
        units = (data or {}).get("units", {}).get("USD", [])
        qs = [u for u in units if u.get("form") in ("10-Q", "10-K") and u.get("frame")]
        qs = qs or [u for u in units if u.get("form") in ("10-Q", "10-K")]
        if not qs:
            continue
        qs.sort(key=lambda u: u.get("end", ""), reverse=True)
        out = []
        for u in qs[:4]:
            try:
                out.append({"metric": metric, "value": round(float(u["val"]) / 1e6, 1),
                            "unit": "백만달러", "period": u.get("end", ""), "source": "EDGAR",
                            "prev": None})
            except (KeyError, ValueError):
                continue
        if out:
            return out
    return []


def collect() -> int:
    c = cfg()["collect"]["edgar"]
    item202 = re.compile(c["item_202_regex"], re.I)
    n = 0
    for form in c["forms"]:
        text = get_text(CURRENT_URL.format(form=form))
        if not text:
            continue
        for entry in parse_feed(text):
            # 8-K 는 실적(Item 2.02) 표시가 있을 때만
            if form == "8-K" and not item202.search(f"{entry['title']} {entry['summary']}"):
                continue
            info = parse_entry(entry, form)
            if not info:
                continue
            now = datetime.now(timezone.utc)
            period = f"{now.year}-{(now.month - 1) // 3 + 1}Q"
            anchors = []
            if info["cik"]:
                anchors = ([{"entity": info["company"], **a} for a in
                            latest_quarterlies(info["cik"], REVENUE_TAGS, "매출")]
                           + [{"entity": info["company"], **a} for a in
                              latest_quarterlies(info["cik"], OPINC_TAGS, "영업이익")])[:4]
            event = {
                "category": "EARNINGS", "entity": info["company"], "period": period,
                "title": f"{info['company']} {form} 공시",
                "anchors": anchors,
                "timeline": {"kind": "official", "title": f"{info['company']} {form}",
                             "source": "EDGAR", "url": info["link"]},
            }
            if write_express(f"edgar_{info['accession']}", event):
                n += 1
    return n
