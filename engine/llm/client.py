"""무료 LLM 클라이언트 (설계서 §6-1, §6-4 폴백 체인).

Gemini Flash-Lite(JSON mode) → 429/5xx 재시도 → Groq(OpenAI 호환) → None(템플릿 폴백).
FakeLLM: 테스트/dry-run 용 — 페이로드에서 결정적으로 유효 JSON 생성 (네트워크 0).
"""
from __future__ import annotations

import json
import time

import requests

from ..config import cfg, env

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def retry_plan(status: int, attempt: int, rl_used: int, c: dict) -> tuple[bool, float, bool]:
    """HTTP 상태 → (재시도 여부, 대기 초, 429 소비 여부).

    - 429(분당 한도): 고정 rate_limit_wait_s 대기, rate_limit_retries 회로 상한 (60초 창을 넘김)
    - 5xx(서버): 짧은 지수 백오프로 재시도
    - 그 외(400/403/404 등): 재시도 없이 즉시 실패
    순수 함수 — 테스트에서 실제 sleep 없이 검증 가능.
    """
    if status == 429:
        if rl_used >= int(c.get("rate_limit_retries", 2)):
            return False, 0.0, True
        return True, float(c.get("rate_limit_wait_s", 20)), True
    if status >= 500:
        return True, 3 * (2 ** attempt), False
    return False, 0.0, False


class LlmClient:
    network = False  # 실제 외부 호출 여부 (handler 의 호출 간격 스로틀 판단용)

    def generate(self, system: str, payload: dict) -> tuple[str | None, str]:
        """반환: (JSON 문자열 or None, 사용 모델명)."""
        raise NotImplementedError


class RealLlm(LlmClient):
    network = True

    def __init__(self):
        self._turn = 0  # 이슈별 provider 교대 카운터

    def generate(self, system: str, payload: dict) -> tuple[str | None, str]:
        c = cfg()["llm"]
        user = "[입력]\n" + json.dumps(payload, ensure_ascii=False)
        # provider_order 기본 순서. alternate_providers 면 이슈마다 회전시켜 두 API 가
        # 부하를 나눠 갖는다(실질 RPM 2배). Groq 우선이 기본(현장 성공률).
        order = list(c.get("provider_order", ["groq", "gemini"]))
        if c.get("alternate_providers") and order:
            shift = self._turn % len(order)
            order = order[shift:] + order[:shift]
            self._turn += 1
        for prov in order:
            if prov == "gemini":
                text = self._gemini(system, user, c)
                if text:
                    return text, env("GEMINI_MODEL", c["gemini_model"])
            elif prov == "groq":
                text = self._groq(system, user, c)
                if text:
                    return text, env("GROQ_MODEL", c["groq_model"])
        return None, ""

    def _gemini(self, system: str, user: str, c: dict) -> str | None:
        key = env("GEMINI_API_KEY")
        if not key:
            return None
        model = env("GEMINI_MODEL", c["gemini_model"])
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": c["temperature"],
                                 "maxOutputTokens": c["max_output_tokens"],
                                 "responseMimeType": "application/json"},
        }
        rl_used = 0
        for attempt in range(c["retry_per_provider"]):
            try:
                res = requests.post(GEMINI_URL.format(model=model),
                                    params={"key": key}, json=body, timeout=60)
                if res.status_code == 200:
                    data = res.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                retry, wait, is_rl = retry_plan(res.status_code, attempt, rl_used, c)
                if is_rl:
                    rl_used += 1
                if retry:
                    if res.status_code == 429:
                        print(f"[llm] gemini 429 rate limit — {wait:.0f}s 대기 후 재시도")
                    time.sleep(wait)
                    continue
                print(f"[llm] gemini HTTP {res.status_code}: {res.text[:200]}")
                return None
            except (requests.RequestException, KeyError, IndexError, ValueError) as e:
                print(f"[llm] gemini 오류(시도 {attempt + 1}): {e}")
                time.sleep(2 * (2 ** attempt))
        return None

    def _groq(self, system: str, user: str, c: dict) -> str | None:
        key = env("GROQ_API_KEY")
        if not key:
            return None
        body = {
            "model": env("GROQ_MODEL", c["groq_model"]),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": c["temperature"], "max_tokens": c["max_output_tokens"],
            "response_format": {"type": "json_object"},
        }
        rl_used = 0
        for attempt in range(c["retry_per_provider"]):
            try:
                res = requests.post(GROQ_URL, json=body, timeout=60,
                                    headers={"Authorization": f"Bearer {key}"})
                if res.status_code == 200:
                    return res.json()["choices"][0]["message"]["content"]
                retry, wait, is_rl = retry_plan(res.status_code, attempt, rl_used, c)
                if is_rl:
                    rl_used += 1
                if retry:
                    if res.status_code == 429:
                        print(f"[llm] groq 429 rate limit — {wait:.0f}s 대기 후 재시도")
                    time.sleep(wait)
                    continue
                print(f"[llm] groq HTTP {res.status_code}: {res.text[:200]}")
                return None
            except (requests.RequestException, KeyError, IndexError, ValueError) as e:
                print(f"[llm] groq 오류(시도 {attempt + 1}): {e}")
                time.sleep(2 * (2 ** attempt))
        return None


class FakeLlm(LlmClient):
    """결정적 유효 출력 — 검증 체인·파이프라인 테스트용. 숫자는 anchors 것만 사용."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode  # ok | invalid_json | bad_numbers

    def generate(self, system: str, payload: dict) -> tuple[str | None, str]:
        if self.mode == "invalid_json":
            return "이건 JSON 이 아님", "fake"
        anchors = payload.get("anchors", [])
        a = anchors[0] if anchors else None
        if self.mode == "bad_numbers":
            details = ["기준금리가 9.99% 가 됐어요."]
        elif a and a.get("value") is not None:
            unit = a.get("unit", "")
            details = [f"{a.get('entity', '기관')}의 {a.get('metric', '지표')}은(는) "
                       f"{a['value']}{unit}이에요."]
            if a.get("prev") is not None:
                details.append(f"직전 값은 {a['prev']}{unit}였어요.")
            details.append("자세한 흐름은 차트에서 볼 수 있어요.")
        else:
            details = ["공식 발표가 나왔어요.", "핵심 내용을 정리했어요.", "출처 링크를 확인할 수 있어요."]
        while len(details) < 4:  # details 규칙 4~6문장 충족
            details.append("관련 소식이 이어지고 있어요.")
        from ..viz import allowed_for_category
        allowed = allowed_for_category(payload.get("category", "ETC"))
        out = {
            "title": "핵심 이슈 정리",
            "one_liner": "핵심 소식이 발표됐어요",
            "why_now": "시장에 영향을 줄 수 있어요.",
            "details": details[:6],
            "visual_type": allowed[0] if allowed else "none",
            "effects": ["시장 변동성이 커질 수 있어요!"],
            "glossary": [{"term": "이슈", "easy": "사람들이 주목하는 소식이나 사건이에요.",
                          "example": "오늘 뉴스에서 가장 큰 이슈로 다뤄졌어요."}],
        }
        return json.dumps(out, ensure_ascii=False), "fake"


def get_client(fake: bool = False, fake_mode: str = "ok") -> LlmClient:
    return FakeLlm(fake_mode) if fake else RealLlm()
