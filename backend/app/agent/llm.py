"""Thin wrapper over Groq's OpenAI-compatible API with model fallback. Owner: P1.

Groq models sometimes fail tool calls (400 'tool_use_failed'); we retry on the
fallback model, then once more without tools.
"""
from __future__ import annotations

import json
import logging
import re

from app.config import Settings, settings as default_settings

log = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, cfg: Settings = default_settings):
        self.cfg = cfg
        self.available = bool(cfg.groq_api_key)
        self._client = None
        if self.available:
            from openai import OpenAI

            self._client = OpenAI(api_key=cfg.groq_api_key, base_url=cfg.groq_base_url, timeout=60)

    def chat(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2):
        """Return (message, model_used). Raises only if every attempt fails."""
        attempts = [(self.cfg.llm_model, tools), (self.cfg.llm_fallback_model, tools)]
        if tools:
            attempts.append((self.cfg.llm_fallback_model, None))
        last_exc: Exception | None = None
        for model, attempt_tools in attempts:
            try:
                kwargs = {"model": model, "messages": messages, "temperature": temperature}
                if attempt_tools:
                    kwargs.update(tools=attempt_tools, tool_choice="auto")
                resp = self._client.chat.completions.create(**kwargs)
                return resp.choices[0].message, model
            except Exception as exc:
                last_exc = exc
                log.warning("LLM call failed on %s (tools=%s): %s", model, bool(attempt_tools), exc)
        raise RuntimeError(f"All LLM attempts failed: {last_exc}")


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def extract_json(text: str | None) -> dict | None:
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # qwen3 reasoning blocks
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
