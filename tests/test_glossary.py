from engine.glossary import lint, terms_in, terms_in_parts


def _terms(s):
    return [t["term"] for t in terms_in(s)]


def test_finds_terms_and_masks_substrings():
    out = terms_in("한국은행이 기준금리를 인하했어요")
    terms = [t["term"] for t in out]
    assert "기준금리" in terms and "한국은행" in terms
    assert "금리" not in terms          # '기준금리' 안의 '금리'는 마스킹
    assert all(t["easy"] for t in out)  # 모든 항목에 쉬운 해설 존재


def test_overlap_prefers_left_then_longest():
    # '한국은행 기준금리 인하' → 더 왼쪽에서 시작하는 '기준금리'가 '금리 인하'를 이김
    assert _terms("한국은행 기준금리 인하") == ["한국은행", "기준금리"]
    # 기준금리가 없으면 '금리 인하'로
    assert _terms("미국 금리 인하 단행") == ["금리 인하"]


def test_case_insensitive_and_alias():
    assert "CPI" in _terms("오늘 cpi 발표")            # 대소문자 무시
    assert "소비자물가" in _terms("소비자물가지수 상승")  # 별칭
    assert "기준금리" in _terms("기준 금리 동결")         # 별칭(띄어쓰기)


def test_terms_in_parts_merges():
    out = [t["term"] for t in terms_in_parts("CPI 발표", "인플레이션 압력", "")]
    assert "CPI" in out and "인플레이션" in out


def test_no_terms_returns_empty():
    assert terms_in("동네 빵집이 새로 문을 열었어요") == []


def test_definitions_pass_easiness_lint():
    """배포된 glossary.yaml 의 모든 해설이 '문외한도 이해' 규칙을 지키는지 — CI 게이트.
    (해설 안에 어려운 말이 섞이거나, 너무 길거나, 어미가 어긋나면 여기서 실패)"""
    errors = lint()
    assert errors == [], "쉬움 규칙 위반:\n" + "\n".join(errors)
