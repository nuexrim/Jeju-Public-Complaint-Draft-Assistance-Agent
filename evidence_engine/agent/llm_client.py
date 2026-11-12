"""LLM 호출의 유일한 통로.

- 기본 provider는 mock이며 네트워크 호출이 없다.
- 실제 provider는 환경변수 ALLOW_PAID_CALLS=1 이 없으면 PaidCallNotAllowed 를 낸다.
  이 변수는 사람이 직접 설정한다. 코드·테스트에서 설정하지 않는다.
- 호출마다 모델, 입력·출력 토큰 수를 logs/usage.csv 에 남긴다.
- 같은 입력은 메모리 캐시로 다시 호출하지 않는다.

상태: mock만 동작한다. 실제 provider 호출부는 모델·API가 확정된 뒤 구현한다.
"""
from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USAGE_LOG = ENGINE_ROOT / "logs" / "usage.csv"
PAID_PROVIDERS = {"anthropic", "openrouter"}
USAGE_HEADER = ["timestamp", "provider", "model", "input_tokens", "output_tokens", "purpose"]


class PaidCallNotAllowed(RuntimeError):
    """ALLOW_PAID_CALLS=1 없이 유료 LLM 호출을 시도했을 때."""


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
    ) -> None:
        if provider != "mock" and provider not in PAID_PROVIDERS:
            raise ValueError(f"알 수 없는 provider: {provider}")
        self.provider = provider
        self.model = model or ("mock-llm" if provider == "mock" else None)
        self.max_tokens = max_tokens
        self.usage_log = usage_log
        self._cache: dict[str, LLMResponse] = {}

    def complete(self, prompt: str, *, system: str = "", purpose: str = "") -> LLMResponse:
        key = hashlib.sha256(
            "\x1f".join([self.provider, str(self.model), system, prompt]).encode("utf-8")
        ).hexdigest()
        if key in self._cache:
            hit = self._cache[key]
            return LLMResponse(hit.text, hit.model, hit.input_tokens, hit.output_tokens, cached=True)

        if self.provider == "mock":
            response = self._mock(prompt, system)
        else:
            # 가드가 먼저다. 이 아래로 내려가기 전에는 어떤 네트워크 호출도 없다.
            if not paid_calls_allowed():
                raise PaidCallNotAllowed(
                    f"유료 호출 차단: provider={self.provider}. "
                    "승인 후 사람이 ALLOW_PAID_CALLS=1 을 설정해야 한다."
                )
            response = self._call_paid(prompt, system)

        self._cache[key] = response
        self._log_usage(response, purpose)
        return response

    def _mock(self, prompt: str, system: str) -> LLMResponse:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        text = f"[MOCK LLM {digest}] 실제 모델 응답이 아닙니다."
        return LLMResponse(text, "mock-llm", len(system) + len(prompt), len(text))

    def _call_paid(self, prompt: str, system: str) -> LLMResponse:
        raise NotImplementedError(
            f"{self.provider} 실제 호출부는 아직 구현되지 않았다 (모델·API 확정 후 구현)."
        )

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
