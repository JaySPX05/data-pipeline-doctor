"""Singletons shared by the API routes (overridable in tests)."""
from functools import lru_cache

from app.agent.doctor import FailureDoctor
from app.agent.llm import LLMClient
from app.memory.hindsight_store import MemoryStore


@lru_cache
def get_store() -> MemoryStore:
    return MemoryStore()


@lru_cache
def get_llm() -> LLMClient:
    return LLMClient()


@lru_cache
def get_doctor() -> FailureDoctor:
    return FailureDoctor(store=get_store(), llm=get_llm())
