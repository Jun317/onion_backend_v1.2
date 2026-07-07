from engine.llm.validate import allowed_numbers, parse_output, validate

PAYLOAD = {
    "category": "RATE",
    "anchors": [{"entity": "한국은행", "metric": "기준금리", "value": 3.0, "unit": "%",
                 "prev": 3.25, "period": "2026-07", "source": "ECOS"}],
    "headlines": ["한은, 기준금리 0.25%p 인하…3.00%"],
    "official_lines": [],
}
GOOD = {
    "title": "한국 기준금리 인하",
    "one_liner": "한국은행이 기준금리를 0.25%p 내려 연 3.0%가 됨",
    "why_now": "금리가 내리면 대출 이자가 줄어 가계 부담이 낮아져요.",
    "details": ["한국은행이 기준금리를 0.25%p 내렸어요.", "새 기준금리는 연 3.00%예요.",
                "지난 금리는 3.25%였어요."],
    "visual_type": "kr_base_rate",
    "effects": ["대출 이자 부담이 줄어들 것으로 예상돼요!"],
}
ALLOWED_VIZ = ["kr_base_rate", "us_policy_rate", "us_10y"]


def test_good_output_passes():
    assert validate(GOOD, PAYLOAD, ALLOWED_VIZ) == []


def test_one_liner_rules():
    bad = {**GOOD, "one_liner": "한국 기준금리가 내려갔다"}   # 음슴체 아님
    assert any("음슴체" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))
    long = {**GOOD, "one_liner": "가" * 46 + "됨"}
    assert any("45자" in e for e in validate(long, PAYLOAD, ALLOWED_VIZ))


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
    bad = {**GOOD, "details": ["짧아요."]}                    # 3개 미만
    assert any("3~5" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))
    bad2 = {**GOOD, "details": ["금리를 내렸다.", "둘째 문장이에요.", "셋째 문장이에요."]}
    assert any("어요체" in e for e in validate(bad2, PAYLOAD, ALLOWED_VIZ))


def test_number_hallucination_blocked():
    bad = {**GOOD, "details": ["기준금리가 9.99%가 됐어요.", "둘째 문장이에요.", "셋째 문장이에요."]}
    assert any("입력에 없는 숫자" in e for e in validate(bad, PAYLOAD, ALLOWED_VIZ))


def test_derived_numbers_allowed():
    """변동폭(0.25)·bp 환산(25)은 파생값으로 허용."""
    nums = allowed_numbers(PAYLOAD)
    assert 0.25 in nums and 25.0 in nums and 3.0 in nums and 3.25 in nums
    ok = {**GOOD, "details": ["변동폭은 25bp예요.", "둘째 문장이에요.", "셋째 문장이에요."]}
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
