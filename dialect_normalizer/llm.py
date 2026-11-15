"""
Claude API 호출의 유일한 통로 (대화·초안 모듈용)

팀 규칙(evidence_engine/agent/llm_client.py)과 같은 원칙을 따른다.
  - ALLOW_PAID_CALLS=1 이 없으면 실제 호출을 막는다. (사람이 .env에 직접 설정)
  - 호출마다 모델, 입력·출력 토큰 수를 logs/usage.csv 에 남긴다.
  - 같은 입력은 메모리 캐시로 다시 호출하지 않는다.
  - CLAUDE_MOCK=1 이면 네트워크 없이 가짜 응답(빈 결과)을 돌려준다. (화면·연결 테스트용)

응답 형식은 Structured Outputs(output_config.format = json_schema)로 강제한다.
팀원 llm_client.py에 Claude 호출이 완성되면 call_json()만 그쪽으로 연결하면 된다.

.env
  ANTHROPIC_API_KEY=sk-ant-...
  CLAUDE_MODEL=claude-haiku-5-5
  ALLOW_PAID_CALLS=1
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
USAGE_LOG = BASE / "logs" / "usage.csv"
DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_MAX_TOKENS = 4096

_cache: dict[str, dict] = {}
_lock = threading.Lock()
_client = None


class PaidCallNotAllowed(RuntimeError):
    """ALLOW_PAID_CALLS=1 없이 유료 호출을 시도했을 때."""


class LLMError(RuntimeError):
    """거절, 토큰 한도 초과, JSON 파싱 실패 등."""


def model_name() -> str:
    return os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)


def _mock_response(schema: dict) -> dict:
    """스키마 모양만 맞춘 빈 응답."""
    def empty(s):
        t = s.get("type")
        if t == "object":
            return {k: empty(v) for k, v in s.get("properties", {}).items()}
        if t == "array":
            return []
        if "enum" in s:
            return "" if "" in s["enum"] else s["enum"][0]
        return {"string": "", "number": 0, "integer": 0, "boolean": False}.get(t, None)
    return empty(schema)


def call_json(system: str, user: str, schema: dict, *, purpose: str = "",
              max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    """system/user 프롬프트를 보내고 schema 모양의 dict를 돌려준다."""
    model = model_name()
    key = hashlib.sha256("\x1f".join(
        [model, system, user, json.dumps(schema, sort_keys=True)]).encode("utf-8")).hexdigest()
    with _lock:
        if key in _cache:
            return _cache[key]

    if os.getenv("CLAUDE_MOCK") == "1":
        result = _mock_response(schema)
        _log(model + "(mock)", 0, 0, purpose)
    else:
        if os.getenv("ALLOW_PAID_CALLS") != "1":
            raise PaidCallNotAllowed(
                "유료 호출이 막혀 있어요. .env에 ALLOW_PAID_CALLS=1 을 직접 적어야 실제로 호출돼요.")
        result = _call_claude(system, user, schema, model, max_tokens, purpose)

    with _lock:
        _cache[key] = result
    return result


def _call_claude(system, user, schema, model, max_tokens, purpose):
    global _client
    # 키가 없으면 SDK 는 요청 직전에 TypeError 를 낸다 → 호출한 쪽이 'LLM 을 못 씀'으로 처리할 수 있게 LLMError 로 알린다
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LLMError("ANTHROPIC_API_KEY 가 없어요. .env 에 넣거나 환경 변수로 설정해 주세요.")
    import anthropic
    if _client is None:
        _client = anthropic.Anthropic()        # ANTHROPIC_API_KEY 환경변수를 읽음

    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    # 단순 변환·추출이라 생각(thinking) 기능은 끈다 (생각 토큰도 출력 요금으로 청구됨).
    # 모델이 끌 수 없게 되어 있으면 설정 없이 다시 보낸다.
    if os.getenv("CLAUDE_THINKING", "disabled") == "disabled":
        kwargs["thinking"] = {"type": "disabled"}

    for attempt in range(4):
        try:
            resp = _client.messages.create(**kwargs)
            break
        except anthropic.BadRequestError as e:
            if "thinking" in kwargs and "thinking" in str(e).lower():
                kwargs.pop("thinking")
                continue
            raise
        except (anthropic.RateLimitError, anthropic.APIConnectionError,
                anthropic.InternalServerError) as e:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt * 2)

    usage = getattr(resp, "usage", None)
    _log(resp.model, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0), purpose)

    if resp.stop_reason == "refusal":
        raise LLMError("모델이 응답을 거절했어요 (안전 정책).")
    if resp.stop_reason == "max_tokens":
        raise LLMError("응답이 max_tokens에서 잘렸어요. max_tokens를 늘려주세요.")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 파싱 실패: {e}: {text[:200]}")


def _log(model, input_tokens, output_tokens, purpose):
    with _lock:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        new = not USAGE_LOG.exists()
        with USAGE_LOG.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "provider", "model", "input_tokens", "output_tokens", "purpose"])
            w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), "anthropic",
                        model, input_tokens, output_tokens, purpose])


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE / ".env")
    except ImportError:
        pass
