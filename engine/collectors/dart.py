"""OpenDART 수집 (설계서 §02-3, §05) — 한국 공시. 실적류 → 급행 EARNINGS.

멱등 키 = rcept_no. 앵커 = fnlttSinglAcnt 매출액·영업이익 (best-effort).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..config import cfg, env
from ..normalize import extract_entity_keys
from ..util.http import get_json
from .base import write_express

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"


def report_period(report_nm: str, rcept_dt: str) -> str:
    """공시명에서 기간 버킷 추출 — anchor_key 용. 예: '분기보고서 (2026.03)' → 2026-1Q."""
    m = re.search(r"\((\d{4})[.\s]*(\d{1,2})\)", report_nm or "")
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return f"{y}-{(mo - 1) // 3 + 1}Q"
    # 폴백: 접수일 분기
    y, mo = int(rcept_dt[:4]), int(rcept_dt[4:6])
    return f"{y}-{(mo - 1) // 3 + 1}Q"


def fetch_earnings_anchors(api_key: str, corp_code: str, year: str) -> list[dict]:
    """단일회사 주요계정 — 매출액·영업이익 (실패 시 빈 리스트, 이벤트는 그대로 발행)."""
    anchors = []
    for code in ("11013", "11012", "11014", "11011"):  # 1Q, 반기, 3Q, 사업
        data = get_json(FNLTT_URL, params={
            "crtfc_key": api_key, "corp_code": corp_code, "bsns_year": year, "reprt_code": code})
        if not data or data.get("status") != "000":
            continue
        for row in data.get("list", []):
            if row.get("fs_div") != "CFS":  # 연결 우선
                continue
            nm = row.get("account_nm", "")
            if nm in ("매출액", "영업이익"):
                try:
                    val = float(str(row["thstrm_amount"]).replace(",", ""))
                except (KeyError, ValueError):
                    continue
                anchors.append({"metric": nm, "value": round(val / 1e8, 1), "unit": "억원",
                                "period": f"{year}-{code}", "source": "DART", "prev": None})
        if anchors:
            break
    return anchors[:4]


def collect() -> int:
    api_key = env("DART_API_KEY")
    if not api_key:
        print("[dart] DART_API_KEY 없음 — skip")
        return 0
    c = cfg()["collect"]["dart"]
    earnings_re = re.compile(c["earnings_regex"])

    kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    bgn = (kst_now - timedelta(days=2)).strftime("%Y%m%d")
    data = get_json(LIST_URL, params={
        "crtfc_key": api_key, "bgn_de": bgn, "end_de": kst_now.strftime("%Y%m%d"),
        "page_count": "100", "pblntf_ty": "A",  # 정기공시
    })
    if not data or data.get("status") not in ("000", "013"):  # 013 = 조회 결과 없음
        print(f"[dart] list 응답 이상: {(data or {}).get('status')}")
        return 0

    n = 0
    skipped_minor = 0
    for item in data.get("list", []):
        report_nm = item.get("report_nm", "")
        if not earnings_re.search(report_nm):
            continue
        rcept_no = item["rcept_no"]
        corp = item.get("corp_name", "")
        # 주요 기업 화이트리스트 (entities.yaml) — 미등재 중소기업 공시는 급행 제외
        if c.get("express_major_only") and not extract_entity_keys(corp):
            skipped_minor += 1
            continue
        period = report_period(report_nm, item.get("rcept_dt", ""))
        anchors = [{"entity": corp, **a} for a in
                   fetch_earnings_anchors(api_key, item.get("corp_code", ""), period[:4])]
        event = {
            "category": "EARNINGS", "entity": corp, "period": period,
            "title": f"{corp} {period} {('잠정' if '잠정' in report_nm else '')}실적 공시",
            "anchors": anchors,
            "timeline": {"kind": "official", "title": f"{corp} {report_nm}", "source": "DART",
                         "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"},
        }
        if write_express(f"dart_{rcept_no}", event):
            n += 1
    if skipped_minor:
        print(f"[dart] 미등재 기업 공시 {skipped_minor}건 급행 제외 (express_major_only)")
    return n
