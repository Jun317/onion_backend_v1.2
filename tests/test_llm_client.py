"""LLM 클라이언트 스로틀·재시도·교대 (v2.5) — 실제 네트워크 없이 검증."""
from engine.llm.client import FakeLlm, RealLlm, get_client, retry_plan

C = {"rate_limit_wait_s": 20, "rate_limit_retries": 2}


def test_retry_plan_rate_limit():
    # 429: 고정 20초 대기, rate_limit_retries(2)회까지 재시도
    assert retry_plan(429, 0, 0, C) == (True, 20.0, True)
    assert retry_plan(429, 1, 1, C) == (True, 20.0, True)
    # 상한 도달 → 재시도 중단
    assert retry_plan(429, 2, 2, C) == (False, 0.0, True)


def test_retry_plan_server_error():
    # 5xx: 짧은 지수 백오프, 429 카운터 소비 안 함
    assert retry_plan(500, 0, 0, C) == (True, 3.0, False)
    assert retry_plan(503, 1, 0, C) == (True, 6.0, False)


def test_retry_plan_client_error_no_retry():
    # 400/403/404: 즉시 실패 (재시도 없음)
    for status in (400, 401, 403, 404):
        assert retry_plan(status, 0, 0, C) == (False, 0.0, False)


def test_network_flag():
    # handler 의 호출 간격 스로틀은 network=True 인 실호출 클라이언트만 적용
    assert RealLlm().network is True
    assert FakeLlm().network is False
    assert get_client(fake=True).network is False


def test_alternate_providers_rotation(monkeypatch):
    """alternate_providers 면 이슈마다 provider 순서가 회전한다 (부하 분담)."""
    import engine.llm.client as mod
    calls = []
    monkeypatch.setattr(mod, "cfg", lambda: {"llm": {
        "provider_order": ["groq", "gemini"], "alternate_providers": True,
        "gemini_model": "g", "groq_model": "q"}})
    client = RealLlm()
    # 각 provider 는 즉시 None 반환하도록 스텁 → generate 는 순회만 하고 순서를 기록
    def stub_groq(s, u, c): calls.append("groq"); return None
    def stub_gemini(s, u, c): calls.append("gemini"); return None
    monkeypatch.setattr(client, "_groq", stub_groq)
    monkeypatch.setattr(client, "_gemini", stub_gemini)
    client.generate("sys", {})   # turn 0 → [groq, gemini]
    client.generate("sys", {})   # turn 1 → [gemini, groq]
    assert calls == ["groq", "gemini", "gemini", "groq"]
