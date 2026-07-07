"""용어 해설 '초안' 작성 보조 (오프라인·수동) — 큐레이션 가속용, 런타임 아님.

LLM(기존 llm/client.py)에게 '쉬움 규칙'을 주고 용어의 easy/example 초안을 받는다.
출력은 반드시 사람이 검수해 glossary.yaml 에 붙여넣는다 (환각 방지 — 자동 반영 안 함).

usage:
  python -m engine.glossary_draft 기준금리 국채 "양적완화"
키(GEMINI/GROQ)가 없으면 안내 후 종료한다.
"""
from __future__ import annotations

import argparse
import json

from .config import cfg
from .llm.client import RealLlm

RUBRIC = """너는 경제를 전혀 모르는 사람에게 용어를 설명하는 도우미다.
아래 [용어]를 다음 규칙으로 풀어 JSON 하나만 출력한다.
- easy: 일상어로만 쓴 핵심 한 문장. 40자 이내. 다른 전문용어(중앙은행 등)는 되도록 쓰지 말고,
  꼭 필요하면 더 쉬운 말로 바꾼다. 반드시 "~요"로 끝낸다.
- example: 그 용어가 '내 생활'에 어떻게 와닿는지 한 문장(40자 이내, "~요"로 끝).
- 하드워드 금지: {HARD}
출력: {"easy": "...", "example": "..."}"""


def draft(term: str, client: RealLlm) -> dict | None:
    hard = ", ".join(cfg().get("glossary_hard_words", []))
    system = RUBRIC.replace("{HARD}", hard)
    text, model = client.generate(system, {"용어": term})
    if not text:
        return None
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return {"_raw": text, "_model": model}
    obj["_model"] = model
    return obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("terms", nargs="+", help="초안을 받을 용어들")
    args = ap.parse_args(argv)

    client = RealLlm()
    print("# 아래 초안을 검수 후 glossary.yaml 에 붙여넣으세요 (자동 반영 아님)\n")
    for term in args.terms:
        d = draft(term, client)
        if d is None:
            print(f"{term}: LLM 응답 실패 (GEMINI_API_KEY/GROQ_API_KEY 확인)")
            continue
        print(f"{term}:")
        print(f"  easy: {json.dumps(d.get('easy', d.get('_raw', '')), ensure_ascii=False)}")
        if d.get("example"):
            print(f"  example: {json.dumps(d['example'], ensure_ascii=False)}")
        print(f"  # via {d.get('_model', '?')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
