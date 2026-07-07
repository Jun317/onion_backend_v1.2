from engine.glossary import terms_in, terms_in_parts


def test_finds_terms_and_masks_substrings():
    out = terms_in("한국은행이 기준금리를 인하했어요")
    terms = [t["term"] for t in out]
    assert "기준금리" in terms
    assert "한국은행" in terms
    # '기준금리' 안의 '금리'는 마스킹돼 중복으로 잡히지 않음
    assert "금리" not in terms
    assert all(t["def"] for t in out)   # 모든 항목에 설명 존재


def test_standalone_short_term_still_found():
    out = [t["term"] for t in terms_in("환율이 급등했어요")]
    assert "환율" in out


def test_terms_in_parts_merges():
    out = [t["term"] for t in terms_in_parts("CPI 발표", "인플레이션 압력", "")]
    assert "CPI" in out and "인플레이션" in out


def test_no_terms_returns_empty():
    assert terms_in("동네 빵집이 새로 문을 열었어요") == []
