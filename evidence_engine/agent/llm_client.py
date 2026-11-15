"""LLM 호출의 유일한 통로.

- 기본 provider는 mock이며 네트워크 호출이 없다.
- 실제 provider는 환경변수 ALLOW_PAID_CALLS=1 이 없으면 PaidCallNotAllowed 를 낸다.
  이 변수는 사람이 직접 설정한다. 코드·테스트에서 설정하지 않는다.
- 호출마다 모델, 입력·출력 토큰 수를 logs/usage.csv 에 남긴다.
- 같은 입력은 캐시로 다시 호출하지 않는다(cache_dir를 주면 디스크에도 남겨 재실행 때 다시 과금되지 않게 한다).
- max_calls 를 주면 승인받은 호출 수를 넘는 순간 CallBudgetExceeded 로 멈춘다.

provider
- anthropic : 공식 anthropic SDK (ANTHROPIC_API_KEY). 기본 공급자.
              Claude Sonnet 5.5·Opus 5.5 등 최신 모델은 thinking 이 켜져 있고(끄는 설정은 400) temperature 를 받지 않는다.
              깊이는 effort(low~max)로 정한다. 안전 분류기 거절(stop_reason=refusal)에 대비해
              서버측 대체 모델(fallbacks="default")을 기본으로 켠다. 거절이 끝까지 가면 LLMCallFailed.
- openrouter: OpenRouter chat/completions (OPENROUTER_API_KEY). 선택 사항(여러 모델 비교용).
키는 환경변수 또는 evidence_engine/.env 에서 읽고, 로그·예외 메시지에 남기지 않는다.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USAGE_LOG = ENGINE_ROOT / "logs" / "usage.csv"
PAID_PROVIDERS = {"anthropic", "openrouter"}
USAGE_HEADER = ["timestamp", "provider", "model", "input_tokens", "output_tokens", "purpose"]
KEY_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
ENDPOINTS = {"openrouter": "https://openrouter.ai/api/v1/chat/completions"}
TIMEOUT_SEC = 300
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class PaidCallNotAllowed(RuntimeError):
    """ALLOW_PAID_CALLS=1 없이 유료 LLM 호출을 시도했을 때."""


class CallBudgetExceeded(RuntimeError):
    """승인받은 최대 호출 수를 넘으려 할 때."""


class LLMCallFailed(RuntimeError):
    """공급자 API 오류 (키는 메시지에 넣지 않는다)."""


def client_from_config(section: str = "dev_llm", config: dict | None = None, **kwargs) -> "LLMClient":
    """config.yaml 의 llm(실행 중) 또는 dev_llm(개발 단계 일괄 작업) 설정으로 클라이언트를 만든다.
    kwargs 로 model·max_tokens·max_calls·cache_dir 등을 덮어쓸 수 있다."""
    if config is None:
        import yaml
        config = yaml.safe_load((ENGINE_ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg = config.get(section) or {}
    provider = kwargs.pop("provider", None) or cfg.get("provider", "mock")
    kwargs.setdefault("effort", cfg.get("effort"))
    model = kwargs.pop("model", None) or cfg.get("model")
    max_tokens = kwargs.pop("max_tokens", None) or cfg.get("max_tokens", 512)
    return LLMClient(provider, model, max_tokens, **kwargs)


def load_api_key(name: str, env_path: Path = ENGINE_ROOT / ".env") -> str:
    key = os.environ.get(name, "")
    if not key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                key = line.split("=", 1)[1]
    key = key.strip().strip('"').strip("'")
    if not key:
        raise LLMCallFailed(f"{name} 가 없다. evidence_engine/.env 에 넣는다.")
    return key


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cached: bool = False


def paid_calls_allowed() -> bool:
    return os.environ.get("ALLOW_PAID_CALLS") == "1"


class LLMClient:
    def __init__(
        self,
        provider: str = "mock",
        model: str | None = None,
        max_tokens: int = 512,
        usage_log: Path | None = DEFAULT_USAGE_LOG,
        *,
        max_calls: int | None = None,
        cache_dir: Path | None = None,
        temperature: float | None = None,
        effort: str | None = None,
        fallbacks: bool = True,
    ) -> None:
        if provider != "mock" and provider not in PAID_PROVIDERS:
            raise ValueError(f"알 수 없는 provider: {provider}")
        self.provider = provider
        self.model = model or ("mock-llm" if provider == "mock" else None)
        self.max_tokens = max_tokens
        self.usage_log = usage_log
        self.max_calls = max_calls
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.temperature = temperature     # openrouter 에만 보낸다 (Anthropic 최신 모델은 400)
        self.effort = effort               # anthropic: output_config.effort (None 이면 모델 기본값)
        self.fallbacks = fallbacks         # anthropic: 거절 시 서버측 대체 모델
        self.calls = 0                     # 실제로 나간(과금된) 호출 수
        self._cache: dict[str, LLMResponse] = {}

    def complete(self, prompt: str, *, system: str = "", purpose: str = "", max_tokens: int | None = None) -> LLMResponse:
        max_tokens = max_tokens or self.max_tokens
        key = hashlib.sha256(
            "\x1f".join([self.provider, str(self.model), str(max_tokens), str(self.effort), system, prompt]).encode("utf-8")
        ).hexdigest()
        if key in self._cache:
            hit = self._cache[key]
            return LLMResponse(hit.text, hit.model, hit.input_tokens, hit.output_tokens, cached=True)
        disk = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if disk and disk.exists():
            d = json.loads(disk.read_text(encoding="utf-8"))
            hit = LLMResponse(d["text"], d["model"], d["input_tokens"], d["output_tokens"], cached=True)
            self._cache[key] = hit
            return hit

        if self.provider == "mock":
            response = self._mock(prompt, system)
        else:
            # 가드가 먼저다. 이 아래로 내려가기 전에는 어떤 네트워크 호출도 없다.
            if not paid_calls_allowed():
                raise PaidCallNotAllowed(
                    f"유료 호출 차단: provider={self.provider}. "
                    "승인 후 사람이 ALLOW_PAID_CALLS=1 을 설정해야 한다."
                )
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise CallBudgetExceeded(f"승인된 최대 호출 수 {self.max_calls}회에 도달해 멈춤")
            self.calls += 1
            response = self._call_paid(prompt, system, max_tokens)

        self._cache[key] = response
        if disk:
            disk.parent.mkdir(parents=True, exist_ok=True)
            disk.write_text(json.dumps(response.__dict__, ensure_ascii=False), encoding="utf-8")
        self._log_usage(response, purpose)
        return response

    def tool_loop(self, prompt: str, *, system: str, tools: list[dict], run_tool, max_steps: int = 4,
                  purpose: str = "", max_tokens: int | None = None, stop_tool: str = "finish") -> list[dict]:
        """Anthropic 도구 사용 루프. 모델이 고른 도구 호출 기록 [{name, input, result}] 을 돌려준다.

        - run_tool(name, input) -> dict 는 호출한 쪽(엔진)이 실행한다. 숫자는 여기서만 나온다.
        - 모델이 stop_tool 을 부르거나, 도구 없이 답하거나, max_steps 에 닿으면 멈춘다.
        - mock 이면 호출 없이 [] (호출한 쪽이 결정적 기본 경로를 쓴다).
        - 같은 입력은 고른 도구 순서를 캐시해 두고 다시 실행만 한다(재과금 없음).
        """
        if self.provider == "mock":
            return []
        if self.provider != "anthropic":
            raise LLMCallFailed("도구 사용 루프는 provider=anthropic 만 지원")
        max_tokens = max_tokens or self.max_tokens
        key = hashlib.sha256("\x1f".join([
            "tool_loop", str(self.model), str(max_tokens), str(self.effort), system, prompt,
            json.dumps(tools, ensure_ascii=False, sort_keys=True), str(max_steps)]).encode("utf-8")).hexdigest()
        disk = self.cache_dir / f"{key}.json" if self.cache_dir else None
        cached = self._cache.get(key) or (json.loads(disk.read_text(encoding="utf-8")) if disk and disk.exists() else None)
        if cached is not None:
            self._cache[key] = cached
            return [{"name": c["name"], "input": c["input"], "result": run_tool(c["name"], c["input"])} for c in cached]

        messages: list = [{"role": "user", "content": prompt}]
        trace: list[dict] = []
        for _ in range(max_steps):
            if not paid_calls_allowed():
                raise PaidCallNotAllowed("유료 호출 차단: 승인 후 사람이 ALLOW_PAID_CALLS=1 을 설정해야 한다.")
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise CallBudgetExceeded(f"승인된 최대 호출 수 {self.max_calls}회에 도달해 멈춤")
            if not self.model:
                raise LLMCallFailed("model 이 정해지지 않았다 (config.yaml llm.model)")
            self.calls += 1
            params: dict = {"model": self.model, "max_tokens": max_tokens, "system": system, "tools": tools,
                            "tool_choice": {"type": "auto"}, "messages": messages}
            if self.effort:
                params["output_config"] = {"effort": self.effort}
            if self._use_fallbacks():
                params["betas"] = [FALLBACK_BETA]
                params["fallbacks"] = "default"
            resp = self._anthropic_create(params)
            self._log_usage(LLMResponse("", resp.model, resp.usage.input_tokens or 0, resp.usage.output_tokens or 0), purpose)
            if resp.stop_reason == "refusal":
                raise LLMCallFailed("모델이 요청을 거절함")
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses or resp.stop_reason == "max_tokens":
                break
            messages.append({"role": "assistant", "content": resp.content})   # thinking 블록 포함 그대로 돌려준다
            results, done = [], False
            for u in uses:
                out = run_tool(u.name, dict(u.input))
                trace.append({"name": u.name, "input": dict(u.input), "result": out})
                results.append({"type": "tool_result", "tool_use_id": u.id,
                                "content": json.dumps(out, ensure_ascii=False)})
                done = done or u.name == stop_tool
            messages.append({"role": "user", "content": results})
            if done:
                break
        record = [{"name": t["name"], "input": t["input"]} for t in trace]
        self._cache[key] = record
        if disk:
            disk.parent.mkdir(parents=True, exist_ok=True)
            disk.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return trace

    def _use_fallbacks(self) -> bool:
        """서버측 대체 모델. Claude Haiku 5.5 는 fallbacks 를 받지 않는다(400) — Haiku 로 바꿔도 깨지지 않게 뺀다."""
        return self.fallbacks and not str(self.model or "").startswith("claude-haiku")

    def _mock(self, prompt: str, system: str) -> LLMResponse:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        text = f"[MOCK LLM {digest}] 실제 모델 응답이 아닙니다."
        return LLMResponse(text, "mock-llm", len(system) + len(prompt), len(text))

    def _call_paid(self, prompt: str, system: str, max_tokens: int) -> LLMResponse:
        if not self.model:
            raise LLMCallFailed("model 이 정해지지 않았다 (config.yaml llm.model)")
        if self.provider == "anthropic":
            return self._call_anthropic(prompt, system, max_tokens)
        key = load_api_key(KEY_NAMES[self.provider])
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "max_tokens": max_tokens, "messages": messages}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        headers = {"Authorization": f"Bearer {key}", "X-Title": "jeju-evidence-engine"}
        data = self._post(ENDPOINTS[self.provider], body, headers)
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        return LLMResponse(text, data.get("model", self.model), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    def _call_anthropic(self, prompt: str, system: str, max_tokens: int) -> LLMResponse:
        params: dict = {"model": self.model, "max_tokens": max_tokens,
                        "messages": [{"role": "user", "content": prompt}]}
        if system:
            params["system"] = system
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if self._use_fallbacks():
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        resp = self._anthropic_create(params)
        if resp.stop_reason == "refusal":
            category = getattr(getattr(resp, "stop_details", None), "category", None)
            raise LLMCallFailed(f"모델이 요청을 거절함 (category={category})")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "max_tokens":
            text += "\n[잘림: max_tokens 도달]"
        usage = resp.usage
        return LLMResponse(text, resp.model, usage.input_tokens or 0, usage.output_tokens or 0)

    @staticmethod
    def _anthropic_create(params: dict):
        """공식 SDK 호출. 키는 .env/환경변수에서 읽고 메시지에 남기지 않는다. 테스트는 이 함수를 바꿔 끼운다."""
        import anthropic
        client = anthropic.Anthropic(api_key=load_api_key(KEY_NAMES["anthropic"]), timeout=TIMEOUT_SEC, max_retries=2)
        try:
            if "fallbacks" in params:
                return client.beta.messages.create(**params)
            return client.messages.create(**params)
        except anthropic.APIStatusError as exc:
            raise LLMCallFailed(f"HTTP {exc.status_code}: {str(exc.message)[:300]}") from None
        except anthropic.APIConnectionError:
            raise LLMCallFailed("연결 실패 (Anthropic API)") from None

    @staticmethod
    def _post(url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMCallFailed(f"HTTP {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise LLMCallFailed(f"연결 실패: {exc.reason}") from None

    def _log_usage(self, response: LLMResponse, purpose: str) -> None:
        if self.usage_log is None:
            return
        path = Path(self.usage_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(USAGE_HEADER)
            writer.writerow([
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                self.provider,
                response.model,
                response.input_tokens,
                response.output_tokens,
                purpose,
            ])
