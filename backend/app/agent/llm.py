"""LLM access for the agent: Groq first, Google Gemini (AI Studio) as a fallback. Owner: P1 (Jay).

Both providers are called through the OpenAI SDK (Gemini via its OpenAI-compatible endpoint).
The chain of targets, in order:
  1. Groq   LLM_MODEL            (openai/gpt-oss-120b)
  2. Gemini GEMINI_MODEL         (only when GEMINI_API_KEY is set; separate quota)
  3. Groq   LLM_FALLBACK_MODEL   (openai/gpt-oss-20b)
then, if tools were offered, one last try on the last target without tools.
Without GEMINI_API_KEY the chain is exactly what it was before (1 then 3).

Failure handling:
- Groq 400 `tool_use_failed`: if the model already wrote our final JSON in `failed_generation`,
  salvage it; otherwise go to the next target.
- 429 (Groq) / RESOURCE_EXHAUSTED (Gemini): wait for Retry-After / "retry in Ns" once (up to
  max_rate_limit_wait_s), else put that target on cooldown and move on. Daily quotas skip the wait.
  Free tier: Groq gpt-oss-120b = 30 RPM, 8K TPM, 200K TPD.
- 401/403/invalid key: that provider is skipped for the rest of the process; the others still run.
- 404 / model_not_found: the model id is retired or not on this account; next target.
- Prose instead of JSON, ```json fences, <think> blocks: extract_json() copes.

Gemini 3 thought signatures: Gemini returns `extra_content.google.thought_signature` on tool calls
and rejects (400) a follow-up that drops it. We keep it on Gemini's own tool calls, strip it before
sending history to Groq, and turn tool history that came from Groq into plain text before sending
it to Gemini (that history has no signature to give back).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.config import Settings, settings as default_settings

log = logging.getLogger(__name__)

GROQ, GEMINI = "groq", "gemini"


@dataclass
class ChatResult:
    message: Any                      # object with .content and .tool_calls
    model: str
    provider: str = GROQ
    attempts: list[str] = field(default_factory=list)  # e.g. ["groq/openai/gpt-oss-120b:http_429", "gemini/gemini-3.5-flash:ok"]
    salvaged: bool = False            # final answer recovered from a failed tool call


@dataclass
class Target:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------------- error helpers
def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    status = getattr(exc, "status_code", None)
    return f"http_{status}" if status else type(exc).__name__


def _error_text(exc: Exception) -> str:
    return f"{exc} {getattr(exc, 'body', '')}".lower()


def is_rate_limit(code: str, exc: Exception) -> bool:
    return code in ("http_429", "rate_limit_exceeded") or "resource_exhausted" in _error_text(exc)


def is_auth_error(code: str, exc: Exception) -> bool:
    text = _error_text(exc)
    return code in ("http_401", "http_403", "invalid_api_key") or "api key not valid" in text or "api_key_invalid" in text


def is_missing_model(code: str) -> bool:
    return code in ("model_not_found", "http_404")


def retry_after_seconds(exc: Exception) -> float | None:
    """Seconds the provider asks us to wait on a rate limit, or None if a daily quota is gone."""
    text = _error_text(exc)
    if "per day" in text or "perday" in text or "(tpd)" in text or "(rpd)" in text:
        return None  # waiting a few seconds won't help; go to the next target
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    if value is None:
        hint = re.search(r"retry in ([\d.]+)\s*s", text)  # Gemini: "Please retry in 23.4s"
        value = hint.group(1) if hint else None
    try:
        return float(value) if value is not None else 2.0
    except ValueError:
        return 2.0


def salvage_failed_generation(exc: Exception) -> str | None:
    """If Groq rejected a tool call but the model already wrote our final JSON, return that text."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    generated = body.get("failed_generation")
    if not generated:
        return None
    data = extract_json(generated)
    return generated if data and "verdict" in data else None


# ---------------------------------------------------------------------- per-provider requests
def model_params(cfg: Settings, model: str, provider: str = GROQ) -> dict:
    """Request params each provider accepts."""
    if provider == GEMINI:
        # OpenAI-compat layer: reasoning_effort maps to Gemini thinking levels. We leave
        # temperature at Gemini's default and use the classic max_tokens name.
        params: dict[str, Any] = {"max_tokens": cfg.llm_max_tokens}
        if cfg.llm_reasoning_effort:
            params["reasoning_effort"] = cfg.llm_reasoning_effort
        return params
    params = {"max_completion_tokens": cfg.llm_max_tokens}
    if model.startswith("openai/gpt-oss") and cfg.llm_reasoning_effort:
        params["reasoning_effort"] = cfg.llm_reasoning_effort
    return params


def _has_signature(tool_call: dict) -> bool:
    return bool(((tool_call.get("extra_content") or {}).get("google") or {}).get("thought_signature"))


def prepare_messages(messages: list[dict], provider: str) -> list[dict]:
    """Adapt chat history to the provider (see module docstring on thought signatures)."""
    tool_turns = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
    if not tool_turns:
        return messages

    if provider != GEMINI:
        out = []
        for m in messages:
            if m.get("tool_calls"):
                m = {**m, "tool_calls": [{k: v for k, v in tc.items() if k != "extra_content"} for tc in m["tool_calls"]]}
            out.append(m)
        return out

    if all(_has_signature(m["tool_calls"][0]) for m in tool_turns):
        return messages  # Gemini's own history: send back as-is, signatures included

    # History from another provider: no signatures to give back, so flatten tool turns into text.
    names = {tc["id"]: tc["function"]["name"] for m in tool_turns for tc in m["tool_calls"]}
    out = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = ", ".join(f"{tc['function']['name']}({tc['function'].get('arguments') or ''})" for tc in m["tool_calls"])
            out.append({"role": "assistant", "content": (m.get("content") or "") + f"\n[I called tools: {calls}]"})
        elif m.get("role") == "tool":
            name = names.get(m.get("tool_call_id"), "tool")
            out.append({"role": "user", "content": f"Result of {name}: {m.get('content')}"})
        else:
            out.append(m)
    return out


# ---------------------------------------------------------------------- client
class LLMClient:
    def __init__(
        self,
        cfg: Settings = default_settings,
        client: Any | None = None,
        clients: dict[str, Any] | None = None,
        clock=time.monotonic,
    ):
        """`client` (tests) is used for Groq; `clients` maps provider -> OpenAI-compatible client."""
        self.cfg = cfg
        self._clock = clock
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients.setdefault(GROQ, client)
        if GROQ not in self._clients and cfg.groq_api_key:
            self._clients[GROQ] = self._openai(cfg.groq_api_key, cfg.groq_base_url)
        if GEMINI not in self._clients and cfg.gemini_api_key:
            self._clients[GEMINI] = self._openai(cfg.gemini_api_key, cfg.gemini_base_url)

        self.targets: list[Target] = []
        if GROQ in self._clients:
            self.targets.append(Target(GROQ, cfg.llm_model))
        if GEMINI in self._clients and cfg.gemini_model:
            self.targets.append(Target(GEMINI, cfg.gemini_model))
        if GROQ in self._clients and cfg.llm_fallback_model and cfg.llm_fallback_model != cfg.llm_model:
            self.targets.append(Target(GROQ, cfg.llm_fallback_model))
        self.available = bool(self.targets)

        self._cooldown_until: dict[str, float] = {}  # target label -> monotonic time
        self._dead_providers: set[str] = set()        # bad API key: skip for this process

    @staticmethod
    def _openai(api_key: str, base_url: str):
        from openai import OpenAI

        return OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=0)

    def describe(self) -> list[str]:
        return [t.label for t in self.targets]

    def _plan(self, tools: list[dict] | None) -> list[tuple[Target, list[dict] | None]]:
        live = [t for t in self.targets if t.provider not in self._dead_providers]
        now = self._clock()
        ready = [t for t in live if self._cooldown_until.get(t.label, 0) <= now]
        ordered = ready + [t for t in live if t not in ready]  # cooling-down targets last, not dropped
        plan = [(t, tools) for t in ordered]
        if tools and ordered:
            plan.append((ordered[-1], None))
        return plan

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> ChatResult:
        """One model turn, walking the target chain until one answers."""
        attempts: list[str] = []
        last_exc: Exception | None = None
        auth_failed = False
        for target, attempt_tools in self._plan(tools):
            if target.provider in self._dead_providers:
                continue
            use_json = json_mode
            for retry in range(2):  # second pass only after a short rate-limit wait or JSON-mode rejection
                try:
                    kwargs: dict[str, Any] = {
                        "model": target.model,
                        "messages": prepare_messages(messages, target.provider),
                        **model_params(self.cfg, target.model, target.provider),
                    }
                    if target.provider != GEMINI:
                        kwargs["temperature"] = temperature
                    if attempt_tools:
                        kwargs.update(tools=attempt_tools, tool_choice="auto")
                    elif use_json:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = self._clients[target.provider].chat.completions.create(**kwargs)
                    attempts.append(f"{target.label}:ok")
                    return ChatResult(
                        message=resp.choices[0].message, model=target.model, provider=target.provider, attempts=attempts
                    )
                except Exception as exc:
                    last_exc, code = exc, _error_code(exc)
                    attempts.append(f"{target.label}:{code}")
                    log.warning("LLM call failed on %s (tools=%s): %s", target.label, bool(attempt_tools), code)

                    if is_auth_error(code, exc):
                        auth_failed = True
                        self._dead_providers.add(target.provider)
                        log.error("%s rejected the API key; skipping %s from now on. Check your .env.", target.provider, target.provider)
                        break
                    if code == "tool_use_failed" and (text := salvage_failed_generation(exc)):
                        return ChatResult(
                            message=SimpleNamespace(content=text, tool_calls=None),
                            model=target.model,
                            provider=target.provider,
                            attempts=attempts,
                            salvaged=True,
                        )
                    if is_rate_limit(code, exc):
                        wait = retry_after_seconds(exc)
                        if retry == 0 and wait is not None and wait <= self.cfg.max_rate_limit_wait_s:
                            log.info("Rate limited on %s, waiting %.1fs", target.label, wait)
                            time.sleep(wait)
                            continue
                        # Daily quota or a long wait: skip this target for a while.
                        cooldown = wait if wait is not None else 600.0
                        self._cooldown_until[target.label] = self._clock() + cooldown
                        break
                    if is_missing_model(code):
                        log.error("Model %s is not available. Check the model id in .env", target.label)
                        break
                    if use_json and retry == 0 and code in ("json_validate_failed", "http_400"):
                        use_json = False  # some models reject JSON mode; retry as plain text
                        continue
                    break

        if not self.targets:
            raise LLMError("No LLM configured: set GROQ_API_KEY and/or GEMINI_API_KEY in .env")
        hint = " An API key was rejected: check GROQ_API_KEY / GEMINI_API_KEY." if auth_failed else ""
        raise LLMError(f"All LLM attempts failed ({', '.join(attempts) or 'no targets left'}).{hint} Last error: {last_exc}")


_THINK = re.compile(r"<think>.*?</think>", re.S)


def extract_json(text: str | None) -> dict | None:
    """Return the first JSON object in a model reply, preferring one with a "verdict" key.

    Handles ```json fences, <think> blocks (qwen3), leading/trailing prose and braces in prose.
    """
    if not text:
        return None
    text = _THINK.sub("", text)
    decoder = json.JSONDecoder()
    first: dict | None = None
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if "verdict" in obj:
                return obj
            first = first or obj
    return first
