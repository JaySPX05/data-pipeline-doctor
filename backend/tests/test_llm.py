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


# ---------------------------------------------------------------- Gemini fallback
from app.agent.llm import GEMINI, GROQ, prepare_messages  # noqa: E402

GCFG = Settings(
    llm_model="primary", llm_fallback_model="fallback", groq_api_key="", gemini_api_key="",
    gemini_model="gemini-flash", llm_reasoning_effort="low", llm_max_tokens=1000, max_rate_limit_wait_s=20,
)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def two_providers(groq_script, gemini_script, clock=None):
    groq, gem = FakeOpenAI(*groq_script), FakeOpenAI(*gemini_script)
    client = LLMClient(GCFG, clients={GROQ: groq, GEMINI: gem}, clock=clock or Clock())
    return client, groq, gem


def test_chain_order_with_and_without_gemini():
    with_gemini, _, _ = two_providers([], [])
    assert with_gemini.describe() == ["groq/primary", "gemini/gemini-flash", "groq/fallback"]
    groq_only = LLMClient(GCFG, client=FakeOpenAI())
    assert groq_only.describe() == ["groq/primary", "groq/fallback"]  # today's behaviour
    gemini_only = LLMClient(GCFG, clients={GEMINI: FakeOpenAI()})
    assert gemini_only.describe() == ["gemini/gemini-flash"] and gemini_only.available


def test_groq_rate_limit_falls_over_to_gemini(monkeypatch):
    monkeypatch.setattr("app.agent.llm.time.sleep", lambda s: pytest.fail("should not wait"))
    daily = GroqError("rate_limit_exceeded", status=429, message="Used 200000 tokens per day (TPD)")
    client, groq, gem = two_providers([daily], ['{"verdict": "NEW"}'])
    result = client.chat([{"role": "user", "content": "hi"}])
    assert (result.provider, result.model) == (GEMINI, "gemini-flash")
    assert result.attempts == ["groq/primary:rate_limit_exceeded", "gemini/gemini-flash:ok"]


def test_gemini_request_params():
    client, _, gem = two_providers([GroqError("x", status=500)], ['{"verdict": "NEW"}'])
    client.chat([{"role": "user", "content": "hi"}])
    sent = gem.calls[0]
    assert sent["max_tokens"] == 1000 and sent["reasoning_effort"] == "low"
    assert "temperature" not in sent and "max_completion_tokens" not in sent


def test_rate_limited_target_cools_down_between_calls(monkeypatch):
    monkeypatch.setattr("app.agent.llm.time.sleep", lambda s: None)
    clock = Clock()
    long_wait = GroqError("rate_limit_exceeded", status=429, headers={"retry-after": "45"})
    client, groq, gem = two_providers([long_wait], ['{"verdict": "NEW"}', '{"verdict": "NEW"}'], clock)
    client.chat([])
    client.chat([])  # primary is cooling down: go straight to Gemini
    assert len(groq.calls) == 1 and len(gem.calls) == 2
    clock.t = 46
    assert client._plan(None)[0][0].label == "groq/primary"  # back in front after the cooldown


def test_gemini_resource_exhausted_and_retry_hint(monkeypatch):
    waits = []
    monkeypatch.setattr("app.agent.llm.time.sleep", waits.append)
    exhausted = GroqError(None, status=429, message="RESOURCE_EXHAUSTED: Please retry in 12.5s.")
    client = LLMClient(GCFG, clients={GEMINI: FakeOpenAI(exhausted, '{"verdict": "NEW"}')})
    assert client.chat([]).provider == GEMINI and waits == [12.5]
    per_day = GroqError(None, status=429, message="Quota GenerateRequestsPerDayPerProjectPerModel-FreeTier exceeded")
    assert retry_after_seconds(per_day) is None


def test_bad_groq_key_still_lets_gemini_answer():
    client, groq, gem = two_providers([GroqError("invalid_api_key", status=401)], ['{"verdict": "NEW"}', '{"verdict": "NEW"}'])
    assert client.chat([]).provider == GEMINI
    client.chat([])
    assert len(groq.calls) == 1  # Groq skipped for the rest of the process


def test_gemini_model_not_found_moves_on():
    client, groq, gem = two_providers(
        [GroqError("x", status=500), '{"verdict": "NEW"}'], [GroqError(None, status=404, message="model not found")]
    )
    assert client.chat([]).model == "fallback"


TOOL_HISTORY = [
    {"role": "user", "content": "diagnose"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "grep_log", "arguments": '{"pattern": "x"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": '{"matches": []}'},
]
SIGNED = {"google": {"thought_signature": "sig=="}}


def test_groq_tool_history_is_flattened_for_gemini():
    out = prepare_messages(TOOL_HISTORY, GEMINI)
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in out)
    assert "I called tools: grep_log" in out[1]["content"]
    assert out[2] == {"role": "user", "content": 'Result of grep_log: {"matches": []}'}


def test_gemini_signatures_kept_for_gemini_and_stripped_for_groq():
    signed = [dict(m) for m in TOOL_HISTORY]
    signed[1] = {**signed[1], "tool_calls": [{**TOOL_HISTORY[1]["tool_calls"][0], "extra_content": SIGNED}]}
    assert prepare_messages(signed, GEMINI) == signed
    to_groq = prepare_messages(signed, GROQ)
    assert "extra_content" not in to_groq[1]["tool_calls"][0] and to_groq[2]["role"] == "tool"
    assert "extra_content" in signed[1]["tool_calls"][0]  # original history untouched
