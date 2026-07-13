from engine.llm.validate import allowed_numbers, clean_glossary, parse_output, validate

PAYLOAD = {
    "category": "RATE",
    "anchors": [{"entity": "한국은행", "metric": "기준금리", "value": 3.0, "unit": "%",
                 "prev": 3.25, "period": "2026-07", "source": "ECOS"}],
    "headlines": ["한은, 기준금리 0.25%p 인하…3.00%"],
    "official_lines": [],
}
GOOD = {
    "title": "한국 기준금리 인하",
    "one_liner": "한국은행이 기준금리를 내려 연 3.0%가 됐어요",
    "why_now": "금리가 내리면 대출 이자가 줄어 가계 부담이 낮아져요.",
    "details": ["한국은행이 기준금리를 0.25%p 내렸어요.", "새 기준금리는 연 3.00%예요.",
                "직전 기준금리는 연 3.25%였어요.", "시장 예상에 부합하는 결정이었어요."],
    "visual_type": "kr_base_rate",
    "effects": ["대출 이자 부담이 줄어들 것으로 예상돼요!"],
    "glossary": [{"term": "기준금리", "easy": "한국은행이 정하는 나라의 기본 이자율이에요.",
                  "example": "기준금리가 내리면 대출 이자도 보통 따라 내려요."}],
}
ALLOWED_VIZ = ["kr_base_rate", "us_policy_rate", "us_10y"]


def test_good_output_passes():
    assert validate(dict(GOOD), PAYLOAD, ALLOWED_VIZ) == []


def test_one_liner_rules():
    bad = {**GOOD, "one_liner": "한국 기준금리가 내려갔다"}   # 해요체 아님
    assert any("해요체" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))
    old_style = {**GOOD, "one_liner": "한국은행이 기준금리를 내려 연 3.0%가 됨"}   # 음슴체도 거부
    assert any("해요체" in e for e in validate(old_style, PAYLOAD, ALLOWED_VIZ))
    long = {**GOOD, "one_liner": "가" * 50 + "요"}   # 51자 — 50자 초과
    assert any("50자" in e for e in validate(long, PAYLOAD, ALLOWED_VIZ))


def test_title_rules():
    missing = {k: v for k, v in GOOD.items() if k != "title"}
    assert any("title 누락" in e for e in validate(missing, PAYLOAD, ALLOWED_VIZ))
    long = {**GOOD, "title": "가" * 23}
    assert any("title 22자 초과" in e for e in validate(long, PAYLOAD, ALLOWED_VIZ))


def test_why_now_rules():
    missing = {k: v for k, v in GOOD.items() if k != "why_now"}
    assert any("why_now 누락" in e for e in validate(missing, PAYLOAD, ALLOWED_VIZ))
    bad = {**GOOD, "why_now": "금리가 내리면 부담이 낮아진다"}   # 어요체 아님
    assert any("why_now 어요체" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))
    # title/why_now 의 숫자도 환각 검사 대상
    halluc = {**GOOD, "title": "금리 7.77% 인하"}
    assert any("입력에 없는 숫자" in e for e in validate(halluc, PAYLOAD, ALLOWED_VIZ))


def test_details_count_and_ending():
    # 3개는 이제 허용(하한 완화) — 2개는 리젝트
    ok3 = {**GOOD, "details": ["첫 문장이에요.", "둘째 문장이에요.", "셋째 문장이에요."]}
    assert validate(dict(ok3), PAYLOAD, ALLOWED_VIZ) == []
    few = {**GOOD, "details": ["짧아요.", "둘째 문장이에요."]}
    assert any("3문장 미만" in e for e in validate(few, PAYLOAD, ALLOWED_VIZ))
    bad2 = {**GOOD, "details": ["금리를 내렸다.", "둘째 문장이에요.", "셋째 문장이에요.",
                                "넷째 문장이에요."]}
    assert any("어요체" in e for e in validate(bad2, PAYLOAD, ALLOWED_VIZ))


def test_details_auto_repair_clips_long_sentence():
    """55자 초과 details 는 리젝트 대신 문장 경계에서 자동 절단된다."""
    longd = "한국은행이 기준금리를 내렸는데 이것은 아주 길고 긴 설명이라서 오십오자를 넘어가게 되는 문장이에요"
    out = {**GOOD, "details": ["첫 문장이에요.", "둘째 문장이에요.", longd]}
    assert validate(out, PAYLOAD, ALLOWED_VIZ) == []
    assert all(len(d) <= 55 for d in out["details"])


def test_cjk_in_title_rejected():
    bad = {**GOOD, "title": "美 기준금리 인하"}
    assert any("한자/가나" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))
    bad2 = {**GOOD, "one_liner": "美 연준이 금리를 내렸어요"}
    assert any("한자/가나" in e for e in validate(bad2, PAYLOAD, ALLOWED_VIZ))


def test_free_numbers_allowed():
    """연도·작은 카운트는 환각으로 보지 않는다."""
    out = {**GOOD, "details": ["2024년부터 이어진 흐름이에요.", "둘째 문장이에요.",
                               "셋째 문장이에요."]}
    assert validate(out, PAYLOAD, ALLOWED_VIZ) == []


def test_number_hallucination_blocked():
    bad = {**GOOD, "details": ["기준금리가 9.99%가 됐어요.", "둘째 문장이에요.",
                               "셋째 문장이에요.", "넷째 문장이에요."]}
    assert any("입력에 없는 숫자" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))


def test_derived_numbers_allowed():
    """변동폭(0.25)·bp 환산(25)은 파생값으로 허용."""
    nums = allowed_numbers(PAYLOAD)
    assert 0.25 in nums and 25.0 in nums and 3.0 in nums and 3.25 in nums
    ok = {**GOOD, "details": ["변동폭은 25bp예요.", "둘째 문장이에요.",
                              "셋째 문장이에요.", "넷째 문장이에요."]}
    assert validate(ok, PAYLOAD, ALLOWED_VIZ) == []


def test_banned_phrase_and_viz_whitelist():
    bad = {**GOOD, "effects": ["무조건 오를 것으로 예상돼요!"]}
    assert any("금지어" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))
    bad2 = {**GOOD, "visual_type": "usdkrw"}   # RATE 허용 목록 밖
    assert any("허용 목록 밖" in e for e in validate(bad2, PAYLOAD, ALLOWED_VIZ))


def test_effects_exclamation():
    bad = {**GOOD, "effects": ["느낌표가 없어요."]}
    assert any("느낌표" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))


def test_parse_output_recovers_fenced_json():
    assert parse_output('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_output("완전 잘못된 출력") is None


# ── glossary 소프트 검증 ─────────────────────────────────────


def test_glossary_valid_entry_kept():
    out = dict(GOOD)
    assert validate(out, PAYLOAD, ALLOWED_VIZ) == []
    assert out["glossary"] == GOOD["glossary"]   # 유효 항목은 그대로 유지


def test_glossary_violations_dropped_softly():
    """glossary 위반은 항목만 드롭 — 전체 재생성 사유(errors)가 아니다."""
    out = {**GOOD, "glossary": [
        {"term": "기준금리", "easy": "한국은행이 정하는 기본 이자율이에요.", "example": ""},
        {"term": "양적완화", "easy": "돈을 시중에 푸는 정책이에요.", "example": ""},   # 본문 미등장
        {"term": "금리", "easy": "돈을 빌린 값이다.", "example": ""},                 # 해요체 아님
        {"term": "인하", "easy": "유동성이 커지는 것이에요.", "example": ""},          # 하드워드
        {"term": "", "easy": "빈 용어예요.", "example": ""},                          # term 누락
    ]}
    assert validate(out, PAYLOAD, ALLOWED_VIZ) == []
    assert [g["term"] for g in out["glossary"]] == ["기준금리"]


def test_glossary_hallucinated_numbers_allowed():
    """해설 속 환산 숫자(입력에 없는 수치)는 환각 검사 대상이 아니다."""
    out = {**GOOD, "glossary": [
        {"term": "기준금리", "easy": "1bp는 0.01%포인트를 뜻해요.", "example": ""}]}
    assert validate(out, PAYLOAD, ALLOWED_VIZ) == []


def test_glossary_missing_or_wrong_type_normalized():
    out = {k: v for k, v in GOOD.items() if k != "glossary"}
    assert validate(out, PAYLOAD, ALLOWED_VIZ) == []
    assert out["glossary"] == []
    out2 = {**GOOD, "glossary": "문자열"}
    clean_glossary(out2)
    assert out2["glossary"] == []


def test_glossary_capped_at_four():
    entries = [{"term": t, "easy": f"{t}에 대한 설명이에요.", "example": ""}
               for t in ["한국", "기준금리", "인하", "연", "대출"]]   # 전부 본문 등장
    out = {**GOOD, "glossary": entries}
    clean_glossary(out)
    assert len(out["glossary"]) == 4
