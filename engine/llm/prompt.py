"""LLM 프롬프트 (설계서 §6-3 전문 그대로) + 입력 페이로드 조립 (§6-2)."""
from __future__ import annotations

import json
import sqlite3

from ..config import cfg
from ..viz import allowed_for_category

SYSTEM_PROMPT = """너는 배경지식이 부족한 한국 일반인을 위한 경제 뉴스 정리 도우미다.
아래 [입력]의 정보만 사용해 지정된 JSON 하나만 출력한다. 다른 텍스트·마크다운·코드펜스 금지.

절대 규칙:
- 입력에 없는 숫자·기업명·주장을 만들지 않는다.
- 특정 종목 매수·매도 권유, 단정적 시장 예측을 하지 않는다.
- 전문용어는 반드시 쉬운 말로 바꾼다. (예: "매파적 기조 강화" → "금리를 올리려는 분위기가 강해짐")

필드 규칙:
1) title — 이슈를 한눈에 알아볼 짧고 직관적인 제목. 22자 이내. 명사구·라벨형.
   전문용어·낚시성·문장부호 남발 금지. 무엇에 관한 소식인지 즉시 알 수 있게.
   (예: "한국 기준금리 인하", "미국 반도체 관세 부과")
2) one_liner — 무슨 일이 있었는지 알려주는 한 문장. 45자 이내, "~함/~됨/~임" 음슴체.
   title 보다 구체적이어야 한다(누가·무엇을·얼마나). 핵심 사건 하나에 집중.
   (예: "한국은행이 기준금리를 0.25%p 내려 연 3.0%가 됨")
3) why_now — 이 소식이 왜 중요한지 배경지식 없는 사람에게 알려주는 한 문장.
   55자 이내, "~어요/~해요"체. 투자 조언이 아니라 '의미·일상 영향'을 쉽게 풀어 쓴다.
   (예: "금리가 내리면 대출 이자가 줄어 가계 부담이 낮아지는 게 보통이에요.")
4) details — 3~5개 문장 배열. 각 문장은 "~어요/~해요"체, 45자 이내,
   문장 하나에 정보 하나만. 숫자는 입력의 anchors 값을 그대로 쓴다.
5) visual_type — 허용 목록 {ALLOWED_VIZ} 중 하나. 해당 없으면 "none".
6) effects — 1~2개 문장 배열. 이 이슈에서 전형적으로 따라오는 패턴만
   말한다(구체적 수치·시점 예측 금지). 각 문장은 "~될 것으로 예상돼요!"
   또는 "~할 수 있어요!"처럼 느낌표로 끝내고, 문장 하나에 정보 하나만.

출력 JSON 스키마:
{"title": "...", "one_liner": "...", "why_now": "...", "details": ["..."], "visual_type": "...", "effects": ["..."]}
"""

# 황금세트 확보 전 기본 few-shot 1건 (사용자가 황금세트에서 카테고리별로 교체 예정)
FEW_SHOT = """[좋은 예시]
입력: {"category":"RATE","anchors":[{"entity":"한국은행","metric":"기준금리","value":3.0,"unit":"%","prev":3.25,"period":"2026-07","source":"ECOS"}],"headlines":["한은, 기준금리 0.25%p 인하…3.00%"],"official_lines":[]}
출력: {"title":"한국 기준금리 인하","one_liner":"한국은행이 기준금리를 0.25%p 내려 연 3.0%가 됨","why_now":"금리가 내리면 대출 이자가 줄어 가계 부담이 낮아지는 게 보통이에요.","details":["한국은행이 기준금리를 0.25%p 내렸어요.","새 기준금리는 연 3.00%예요.","시장 예상에 부합하는 결정이었어요."],"visual_type":"kr_base_rate","effects":["대출 이자 부담이 줄어들 것으로 예상돼요!","채권 가격 상승 요인이 될 수 있어요!"]}
"""


def system_prompt(category: str) -> str:
    allowed = allowed_for_category(category) + ["none"]
    return SYSTEM_PROMPT.replace("{ALLOWED_VIZ}", json.dumps(allowed, ensure_ascii=False)) \
        + "\n" + FEW_SHOT


def build_payload(conn: sqlite3.Connection, issue: sqlite3.Row) -> dict:
    """입력 페이로드 (§6-2) — 본문 절대 미포함 (저작권 방화벽 + 토큰 절약)."""
    max_h = cfg()["llm"]["max_headlines"]
    anchors = [dict(r) for r in conn.execute(
        "SELECT entity, metric, value, unit, prev, period, source FROM numeric_anchor "
        "WHERE issue_id=? ORDER BY observed_at DESC LIMIT 8", (issue["id"],))]
    headlines = [r["title"] for r in conn.execute(
        "SELECT title FROM article WHERE issue_id=? AND is_dup=0 "
        "ORDER BY published_at DESC LIMIT ?", (issue["id"], max_h))]
    if not headlines:
        headlines = [issue["canonical_title"]]
    # 공공저작물(부처 보도자료)만 원문 인용 가능 — 리드 1~2건
    official = [r["lead"] for r in conn.execute(
        "SELECT lead FROM article WHERE issue_id=? AND tier='official' AND lead != '' "
        "ORDER BY published_at DESC LIMIT 2", (issue["id"],))]
    return {"category": issue["category"], "anchors": anchors,
            "headlines": headlines, "official_lines": official}
