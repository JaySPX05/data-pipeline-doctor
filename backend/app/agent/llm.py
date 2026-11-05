"""Thin wrapper over Groq's OpenAI-compatible API. Owner: P1 (Jay).

Groq failure modes this handles:
- 400 `tool_use_failed`: the model produced a malformed tool call. Groq returns what the model
  wrote in `failed_generation`; if that already contains our final JSON we salvage it,
  otherwise we retry on the fallback model, then once more without tools.
- 429 rate limit: one short back-off, then the fallback model.
- 401/403: bad key. Fail fast (retrying other models cannot help).
- Prose instead of JSON, ```json fences, <think> blocks: extract_json() copes.
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


@dataclass
class ChatResult:
    message: Any                      # object with .content and .tool_calls
    model: str
    attempts: list[str] = field(default_factory=list)  # e.g. ["gpt-oss-120b:tool_use_failed", "qwen3-32b:ok"]
    salvaged: bool = False            # final answer recovered from a failed tool call


class LLMError(RuntimeError):
    pass


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    status = getattr(exc, "status_code", None)
    return f"http_{status}" if status else type(exc).__name__


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


class LLMClient:
    def __init__(self, cfg: Settings = default_settings, client: Any | None = None):
        self.cfg = cfg
        self._client = client
        self.available = client is not None or bool(cfg.groq_api_key)
        if self._client is None and self.available:
            from openai import OpenAI

            self._client = OpenAI(api_key=cfg.groq_api_key, base_url=cfg.groq_base_url, timeout=60, max_retries=0)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> ChatResult:
        """One model turn, trying primary -> fallback -> fallback without tools."""
        plan = [(self.cfg.llm_model, tools), (self.cfg.llm_fallback_model, tools)]
        if tools:
            plan.append((self.cfg.llm_fallback_model, None))

        attempts: list[str] = []
        last_exc: Exception | None = None
        for model, attempt_tools in plan:
            for retry in range(2):  # second pass only after a rate limit
                try:
                    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
                    if attempt_tools:
                        kwargs.update(tools=attempt_tools, tool_choice="auto")
                    elif json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = self._client.chat.completions.create(**kwargs)
                    attempts.append(f"{model}:ok")
                    return ChatResult(message=resp.choices[0].message, model=model, attempts=attempts)
                except Exception as exc:
                    last_exc, code = exc, _error_code(exc)
                    attempts.append(f"{model}:{code}")
                    log.warning("LLM call failed on %s (tools=%s): %s", model, bool(attempt_tools), code)

                    if code in ("http_401", "http_403", "invalid_api_key"):
                        raise LLMError(f"Groq rejected the API key ({code}). Check GROQ_API_KEY.") from exc
                    if code == "tool_use_failed" and (text := salvage_failed_generation(exc)):
                        return ChatResult(
                            message=SimpleNamespace(content=text, tool_calls=None),
                            model=model,
                            attempts=attempts,
                            salvaged=True,
                        )
                    if code in ("http_429", "rate_limit_exceeded") and retry == 0:
                        time.sleep(2)
                        continue
                    if json_mode and code in ("json_validate_failed", "http_400") and retry == 0:
                        json_mode = False  # some models reject JSON mode; retry as plain text
                        continue
                    break
        raise LLMError(f"All LLM attempts failed ({', '.join(attempts)}): {last_exc}")


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
