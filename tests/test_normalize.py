from engine.normalize import extract_entity_keys, normalize_record
from engine.util.textnum import extract_numbers, tag_numbers


def test_entity_extraction_korean_company():
    keys = extract_entity_keys("삼성전자 2분기 영업이익 10조원 돌파")
    assert "KRX:005930" in keys


def test_entity_extraction_institution_and_boundary():
    assert "BOK" in extract_entity_keys("한국은행이 기준금리를 인하했다")
    assert "FED" in extract_entity_keys("Fed keeps rates steady")
    # 2자 이하 별칭('SK' 등)은 단어 경계 밖 오탐 금지: task/risk 등 영단어 내부 매칭 안 됨
    assert "KRX:034730" not in extract_entity_keys("new risk task force announced")


def test_tag_numbers():
    tags = tag_numbers("기준금리 0.25%p 인하, 영업이익 10조원")
    assert "0.25%p" in tags and "10조원" in tags


def test_extract_numbers():
    assert extract_numbers("금리 3.00%와 1,250원") == {3.0, 1250.0}


def test_normalize_record_requires_fields():
    assert normalize_record({"id": "x", "url": "https://a.b", "title": ""}) is None
    rec = normalize_record({"id": "x", "url": "https://a.b", "title": "한국은행 금리 인하",
                            "lead": "본문 요약", "lang": "ko",
                            "collected_at": "2026-07-04T00:00:00+00:00"})
    assert rec is not None and rec["url_hash"] == "x" and rec["simhash"] != 0
    assert "BOK" in rec["entity_keys"]
