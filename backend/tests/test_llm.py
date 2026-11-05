"""LLM wrapper: JSON extraction and Groq error handling, with a fake OpenAI client."""
from types import SimpleNamespace

import pytest

from app.agent.llm import LLMClient, LLMError, extract_json, model_params, retry_after_seconds, salvage_failed_generation
from app.config import Settings


class GroqError(Exception):
    def __init__(self, code, status=400, body=None, headers=None, message=None):
        super().__init__(message or code)
        self.code, self.status_code, self.body = code, status, body or {"code": code}
        self.response = SimpleNamespace(headers=headers or {})


class FakeOpenAI:
    """Mimics client.chat.completions.create; each scripted item is a reply text or an exception."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        msg = SimpleNamespace(content=item, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


CFG = Settings(llm_model="primary", llm_fallback_model="fallback", groq_api_key="", max_rate_limit_wait_s=20)
TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]


@pytest.mark.parametrize(
    "text",
    [
        '{"verdict": "FLAKY", "confidence": 0.8}',
        '```json\n{"verdict": "FLAKY", "confidence": 0.8}\n```',
        '<think>maybe {not json}</think>Here you go: {"verdict": "FLAKY", "confidence": 0.8} hope it helps',
        'Notes {"a": 1} then the answer {"verdict": "FLAKY", "confidence": 0.8}',
    ],
)
def test_extract_json_variants(text):
    assert extract_json(text)["verdict"] == "FLAKY"


def test_extract_json_none():
    assert extract_json("no json here") is None and extract_json(None) is None


def test_salvage_failed_generation():
    exc = GroqError("tool_use_failed", body={"code": "tool_use_failed", "failed_generation": 'x {"verdict": "NEW"}'})
    assert salvage_failed_generation(exc)
    assert salvage_failed_generation(GroqError("tool_use_failed")) is None


def test_tool_use_failed_falls_back_then_drops_tools():
    fake = FakeOpenAI(GroqError("tool_use_failed"), GroqError("tool_use_failed"), '{"verdict": "NEW"}')
    result = LLMClient(CFG, client=fake).chat([{"role": "user", "content": "hi"}], tools=TOOLS)
    assert [c["model"] for c in fake.calls] == ["primary", "fallback", "fallback"]
    assert "tools" not in fake.calls[-1]
    assert result.model == "fallback"


def test_tool_use_failed_with_final_answer_is_salvaged():
    exc = GroqError("tool_use_failed", body={"code": "tool_use_failed", "failed_generation": '{"verdict": "FLAKY"}'})
    result = LLMClient(CFG, client=FakeOpenAI(exc)).chat([], tools=TOOLS)
    assert result.salvaged and extract_json(result.message.content)["verdict"] == "FLAKY"


def test_bad_key_fails_fast():
    fake = FakeOpenAI(GroqError("invalid_api_key", status=401))
    with pytest.raises(LLMError, match="API key"):
        LLMClient(CFG, client=fake).chat([])
    assert len(fake.calls) == 1


def test_rate_limit_retries_same_model(monkeypatch):
    monkeypatch.setattr("app.agent.llm.time.sleep", lambda s: None)
    fake = FakeOpenAI(GroqError("rate_limit_exceeded", status=429), '{"verdict": "NEW"}')
    result = LLMClient(CFG, client=fake).chat([])
    assert [c["model"] for c in fake.calls] == ["primary", "primary"] and result.model == "primary"


def test_json_mode_rejected_retries_without_it():
    fake = FakeOpenAI(GroqError("json_validate_failed"), '{"verdict": "NEW"}')
    LLMClient(CFG, client=fake).chat([], json_mode=True)
    assert "response_format" in fake.calls[0] and "response_format" not in fake.calls[1]


def test_everything_fails_raises():
    fake = FakeOpenAI(GroqError("x", status=500), GroqError("x", status=500))
    with pytest.raises(LLMError):
        LLMClient(CFG, client=fake).chat([])


def test_retry_after_header_is_honoured(monkeypatch):
    waits = []
    monkeypatch.setattr("app.agent.llm.time.sleep", waits.append)
    fake = FakeOpenAI(GroqError("rate_limit_exceeded", status=429, headers={"retry-after": "7.5"}), '{"verdict": "NEW"}')
    LLMClient(CFG, client=fake).chat([])
    assert waits == [7.5]


def test_long_wait_or_daily_quota_goes_to_fallback(monkeypatch):
    monkeypatch.setattr("app.agent.llm.time.sleep", lambda s: pytest.fail("should not wait"))
    too_long = GroqError("rate_limit_exceeded", status=429, headers={"retry-after": "45"})
    daily = GroqError("rate_limit_exceeded", status=429, message="Limit 200000, Used 199990 tokens per day (TPD)")
    for exc in (too_long, daily):
        fake = FakeOpenAI(exc, '{"verdict": "NEW"}')
        assert LLMClient(CFG, client=fake).chat([]).model == "fallback"
    assert retry_after_seconds(daily) is None


def test_model_not_found_goes_to_fallback():
    fake = FakeOpenAI(GroqError("model_not_found", status=404), '{"verdict": "NEW"}')
    assert LLMClient(CFG, client=fake).chat([]).model == "fallback"


def test_gpt_oss_gets_reasoning_effort_others_do_not():
    cfg = Settings(llm_reasoning_effort="low", llm_max_tokens=1000)
    assert model_params(cfg, "openai/gpt-oss-120b") == {"max_completion_tokens": 1000, "reasoning_effort": "low"}
    assert model_params(cfg, "llama-3.1-8b-instant") == {"max_completion_tokens": 1000}
