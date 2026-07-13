"""텍스트 스크립트 판별 · 한자 약자 한국어화 (설계서 §04 보강, v2.4).

- is_foreign_title: 한글/영문 뉴스가 아닌 외국어(중국어·일본어 등) 제목 판별 → 수집 드롭
- normalize_hanja: 언론이 쓰는 한자 약자(美·中·日 …)를 한국어로 치환 → 표시 정규화
- has_cjk: LLM 출력(title·one_liner)에 한자/가나가 섞였는지 검사 → 재생성 유도
"""
from __future__ import annotations

import re

# 한글 음절/자모
_HANGUL = re.compile(r"[가-힣㄰-㆏]")
# 한자(CJK 통합)
_HAN = re.compile(r"[一-鿿㐀-䶿]")
# 일본어 가나
_KANA = re.compile(r"[぀-ヿ]")
# 라틴 알파벳
_LATIN = re.compile(r"[A-Za-z]")

_CJK = re.compile(r"[一-鿿㐀-䶿぀-ヿ]")


def has_cjk(text: str) -> bool:
    """한자 또는 가나가 하나라도 있으면 True (LLM 출력 가드용)."""
    return bool(_CJK.search(text or ""))


def is_foreign_title(title: str) -> bool:
    """한글·영문 뉴스가 아닌 외국어 제목인지 — 수집 단계에서 드롭할지 판단.

    규칙: 한글이 하나라도 있으면 한국어 기사로 본다(한자 약자 섞여도 OK).
    한글이 전혀 없고 가나가 있거나, 한자가 라틴 알파벳보다 많으면 외국어로 본다.
    (영어 기사는 라틴 위주라 통과, 중국어 기사는 한자 위주라 드롭)
    """
    t = title or ""
    if _HANGUL.search(t):
        return False
    if _KANA.search(t):
        return True
    han = len(_HAN.findall(t))
    # 한글 없이 한자가 많으면(4자+) 라틴이 섞여 있어도 외국어(중국어 등)로 본다.
    # 한국어 기사의 한자 약자(美·中 …)는 1~2자라 여기 안 걸린다.
    if han >= 4:
        return True
    latin = len(_LATIN.findall(t))
    return han > latin and han >= 2


# 언론 관용 한자 약자 → 한국어. 긴 표기 먼저(２자 국가명), 다음 1자 약자.
_HANJA_MAP: list[tuple[str, str]] = [
    ("中國", "중국"), ("日本", "일본"), ("北韓", "북한"), ("韓國", "한국"),
    ("美國", "미국"), ("英國", "영국"), ("獨逸", "독일"), ("佛蘭西", "프랑스"),
    ("美", "미국"), ("中", "중국"), ("日", "일본"), ("北", "북한"),
    ("韓", "한국"), ("英", "영국"), ("獨", "독일"), ("佛", "프랑스"),
    ("露", "러시아"), ("EU", "EU"),
]


def normalize_hanja(text: str) -> str:
    """표시용 한자 약자를 한국어로 치환. 치환 후에도 한자가 남으면 원문 유지분은 그대로 둔다."""
    if not text or not _HAN.search(text):
        return text or ""
    out = text
    for hanja, ko in _HANJA_MAP:
        out = out.replace(hanja, ko)
    return out
