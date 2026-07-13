"""외국어 판별·한자 치환·CJK 검사 (engine/util/lang.py, v2.4)."""
from engine.util.lang import has_cjk, is_foreign_title, normalize_hanja


def test_is_foreign_title_drops_chinese():
    assert is_foreign_title("今天写的一篇文章：投资的教训")           # 중국어 → 드롭
    assert is_foreign_title("日本銀行が金利を引き上げた")             # 일본어 → 드롭
    assert not is_foreign_title("한국은행 기준금리 인하")            # 한국어 → 유지
    assert not is_foreign_title("美, 이란 핵시설 공습")             # 한자 약자 섞인 한국어 → 유지
    assert not is_foreign_title("Fed signals rate cut in July")   # 영어 → 유지
    assert not is_foreign_title("KOSPI hits record high")


def test_normalize_hanja_maps_abbreviations():
    assert normalize_hanja("美, 이란 핵시설 공습") == "미국, 이란 핵시설 공습"
    assert normalize_hanja("美연준, 금리 인하") == "미국연준, 금리 인하"
    assert normalize_hanja("中國 반도체 규제") == "중국 반도체 규제"
    assert normalize_hanja("한국은행 기준금리") == "한국은행 기준금리"  # 한자 없으면 그대로


def test_has_cjk():
    assert has_cjk("美 금리")
    assert has_cjk("金利")
    assert not has_cjk("미국 금리 인하")
    assert not has_cjk("Fed rate cut")
