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
