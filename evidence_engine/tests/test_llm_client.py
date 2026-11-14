import csv

import pytest

from agent.llm_client import LLMClient, PaidCallNotAllowed


def test_default_is_mock_and_needs_no_permission(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    client = LLMClient(usage_log=tmp_path / "usage.csv")
    assert client.provider == "mock"
    response = client.complete("버스가 줄었다를 가설로 바꿔줘", purpose="test")
    assert response.model == "mock-llm" and "MOCK" in response.text


@pytest.mark.parametrize("provider", ["anthropic", "openrouter"])
@pytest.mark.parametrize("env_value", [None, "", "0", "true"])
def test_paid_provider_is_blocked_without_allow_flag(monkeypatch, tmp_path, provider, env_value):
    if env_value is None:
        monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    else:
        monkeypatch.setenv("ALLOW_PAID_CALLS", env_value)
    log = tmp_path / "usage.csv"
    client = LLMClient(provider=provider, model="some-model", usage_log=log)
    # 호출부에 닿으면 실패하도록 막아 두고, 가드가 먼저 걸리는지 본다.
    monkeypatch.setattr(client, "_call_paid", lambda *a, **k: pytest.fail("유료 호출부에 도달함"))
    with pytest.raises(PaidCallNotAllowed):
        client.complete("hello")
    assert not log.exists(), "차단된 호출은 사용량 로그에 남지 않아야 한다"


def test_usage_is_logged_and_same_input_is_cached(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    log = tmp_path / "usage.csv"
    client = LLMClient(usage_log=log)
    first = client.complete("같은 질문", purpose="p1")
    second = client.complete("같은 질문", purpose="p1")
    client.complete("다른 질문", purpose="p2")
    assert not first.cached and second.cached and second.text == first.text
    rows = list(csv.DictReader(log.open(encoding="utf-8")))
    assert [r["purpose"] for r in rows] == ["p1", "p2"]
    assert all(r["provider"] == "mock" and int(r["input_tokens"]) > 0 for r in rows)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        LLMClient(provider="gpt")


# ---- 실제 호출부: 네트워크 대신 가짜로 요청 형식·토큰 기록·예산·디스크 캐시를 확인 ----
from types import SimpleNamespace as NS

FAKE_KEY = "sk-test-not-real"


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setenv("ALLOW_PAID_CALLS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)


def fake_message(text="답", stop="end_turn", inp=7, out=3, model="claude-x", category=None):
    """anthropic SDK 응답(Message)과 같은 모양의 가짜. thinking 블록은 본문에서 빠져야 한다."""
    return NS(model=model, stop_reason=stop, stop_details=NS(category=category),
              content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
              usage=NS(input_tokens=inp, output_tokens=out))


@pytest.fixture
def sdk(monkeypatch):
    import agent.llm_client as mod
    seen = []
    state = {"reply": fake_message()}

    def create(params):
        seen.append(params)
        return state["reply"]

    monkeypatch.setattr(mod.LLMClient, "_anthropic_create", staticmethod(create))
    return seen, state


def test_anthropic_request_shape_for_opus_5_5(allowed, sdk, tmp_path):
    seen, _ = sdk
    log = tmp_path / "usage.csv"
    client = LLMClient(provider="anthropic", model="claude-opus-5-5", max_tokens=16000, usage_log=log, effort="high")
    r = client.complete("질문", system="시스템", purpose="t")
    assert r.text == "답" and (r.input_tokens, r.output_tokens) == (7, 3)
    p = seen[0]
    assert "temperature" not in p and "thinking" not in p           # Opus 5.5: temperature 400, thinking 은 항상 켜짐
    assert p["output_config"] == {"effort": "high"} and p["max_tokens"] == 16000 and p["system"] == "시스템"
    assert p["fallbacks"] == "default" and p["betas"] == ["server-side-fallback-2026-07-01"]
    rows = list(csv.DictReader(log.open(encoding="utf-8")))
    assert rows[0]["input_tokens"] == "7" and FAKE_KEY not in log.read_text(encoding="utf-8")


def test_refusal_raises_and_truncation_is_marked(allowed, sdk):
    from agent.llm_client import LLMCallFailed
    _, state = sdk
    state["reply"] = fake_message(stop="refusal", category="cyber")
    with pytest.raises(LLMCallFailed, match="거절"):
        LLMClient(provider="anthropic", model="m", usage_log=None).complete("x")
    state["reply"] = fake_message(text="반쯤", stop="max_tokens")
    assert "잘림" in LLMClient(provider="anthropic", model="m", usage_log=None).complete("y").text


def test_openrouter_request_and_usage(allowed, monkeypatch, tmp_path):
    import agent.llm_client as mod
    seen = {}
    monkeypatch.setattr(mod.LLMClient, "_post", staticmethod(lambda url, body, headers: seen.update(
        url=url, body=body, headers=headers) or {"model": "m", "choices": [{"message": {"content": "답"}}],
                                                  "usage": {"prompt_tokens": 7, "completion_tokens": 3}}))
    r = LLMClient(provider="openrouter", model="m", max_tokens=64, usage_log=None, temperature=0.2).complete("질문")
    assert r.text == "답" and "Authorization" in seen["headers"] and seen["body"]["temperature"] == 0.2


def test_budget_stops_extra_calls_and_disk_cache_avoids_repaying(allowed, sdk, tmp_path):
    from agent.llm_client import CallBudgetExceeded
    seen, _ = sdk
    cache = tmp_path / "cache"
    client = LLMClient(provider="anthropic", model="m", usage_log=None, max_calls=1, cache_dir=cache)
    client.complete("a")
    with pytest.raises(CallBudgetExceeded):
        client.complete("b")
    again = LLMClient(provider="anthropic", model="m", usage_log=None, max_calls=0, cache_dir=cache)
    assert again.complete("a").cached and len(seen) == 1


def test_http_error_message_has_no_key(allowed, monkeypatch):
    import io
    import urllib.error
    import agent.llm_client as mod
    from agent.llm_client import LLMCallFailed

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with pytest.raises(LLMCallFailed) as exc:
        LLMClient(provider="openrouter", model="m", usage_log=None).complete("x")
    assert "401" in str(exc.value) and FAKE_KEY not in str(exc.value)


def test_sdk_errors_become_llmcallfailed_without_key(allowed, monkeypatch):
    """실제 _anthropic_create 경로: SDK 가 401 을 내면 키 없이 LLMCallFailed 로 바뀌는가."""
    import anthropic
    import httpx2
    from agent.llm_client import LLMCallFailed
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError("invalid x-api-key", response=httpx2.Response(401, request=req), body=None)
    made = {}

    class FakeAnthropic:
        def __init__(self, api_key, timeout, max_retries):
            made["key_seen"] = api_key == FAKE_KEY
            boom = NS(create=lambda **kw: (_ for _ in ()).throw(err))
            self.messages = boom
            self.beta = NS(messages=boom)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    with pytest.raises(LLMCallFailed) as exc:
        LLMClient(provider="anthropic", model="m", usage_log=None).complete("x")
    assert "401" in str(exc.value) and FAKE_KEY not in str(exc.value) and made["key_seen"]


# ---- 설정으로 공급자 고르기 (dev_llm: RIGOR·개념 태그 / llm: 실행 중 SQL 초안) ----
def test_client_from_config_sections():
    from agent.llm_client import client_from_config
    cfg = {"llm": {"provider": "mock"}, "dev_llm": {"provider": "anthropic", "model": "claude-x", "effort": "high"}}
    assert client_from_config("llm", cfg, usage_log=None).provider == "mock"
    dev = client_from_config("dev_llm", cfg, usage_log=None, max_calls=3)
    assert (dev.provider, dev.model, dev.max_calls, dev.effort) == ("anthropic", "claude-x", 3, "high")
    assert client_from_config("dev_llm", cfg, usage_log=None, effort="low").effort == "low"
    assert client_from_config("dev_llm", cfg, usage_log=None, provider="openrouter").provider == "openrouter"


def test_repo_config_uses_sonnet_5_5_for_dev_jobs_and_mock_at_runtime():
    from agent.llm_client import client_from_config
    dev = client_from_config("dev_llm", usage_log=None)
    assert (dev.provider, dev.model) == ("anthropic", "claude-sonnet-5-5")
    assert client_from_config("llm", usage_log=None).provider == "mock"


def test_rigor_calls_are_routed_to_anthropic(allowed, sdk, monkeypatch, tmp_path):
    """RIGOR의 OpenRouterLLM.generate 가 우리 클라이언트 → Anthropic SDK 로 가는지 (가짜 RIGOR 모듈)."""
    import types
    from ontology import build_ontology
    import agent.llm_client as mod
    seen, state = sdk
    state["reply"] = fake_message(text="Class: :X")
    monkeypatch.setattr(build_ontology, "BUILD", tmp_path)
    monkeypatch.setattr(mod.LLMClient, "_log_usage", lambda self, r, p: None)   # 실제 logs/usage.csv 에 남기지 않음

    class OpenRouterLLM:
        def __init__(self, api_key, model="m"):
            raise AssertionError("RIGOR 원래 OpenRouter 클라이언트를 만들면 안 된다")

    class OntologyLLM:
        def _build_prompt(self, *a, **k):
            return "prompt"

    app = types.SimpleNamespace(OpenRouterLLM=OpenRouterLLM, OntologyLLM=OntologyLLM)
    client = build_ontology.route_llm_through_client(app, "anthropic", "claude-opus-5-5", max_calls=2, max_tokens=16000)
    llm = app.OpenRouterLLM("ignored")
    assert llm.generate("hello", max_tokens=10000) == "Class: :X"
    assert len(seen) == 1 and seen[0]["model"] == "claude-opus-5-5" and "temperature" not in seen[0]
    assert seen[0]["max_tokens"] == 10000 and client.calls == 1
    assert "@ko" in app.OntologyLLM()._build_prompt()          # 한글 라벨 요구가 덧붙는다
