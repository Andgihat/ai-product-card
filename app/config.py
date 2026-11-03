"""Настройки сервиса. Все значения читаются из переменных окружения / .env."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from dotenv import load_dotenv

load_dotenv()

T = TypeVar("T", int, float)


def _env(name: str, default: T, convert: Callable[[str], T]) -> T:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return convert(raw)
    except ValueError as exc:
        raise ValueError(f"Переменная {name} должна быть числом, получено: {raw!r}") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Параметры подключения к LLM и лимиты генерации."""

    base_url: str
    model: str
    api_key: str
    timeout: float
    temperature: float
    max_tokens: int
    retries: int
    # Второй проход фактчекинга. По умолчанию выключен: удваивает время генерации.
    verify_llm: bool = False
    verifier_model: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            base_url=os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1").strip().rstrip("/"),
            model=os.getenv("LLM_MODEL", "local").strip(),
            # llama.cpp и Ollama ключ не проверяют, но без него не работают
            # облачные OpenAI-совместимые провайдеры.
            api_key=os.getenv("LLM_API_KEY", "not-needed").strip(),
            timeout=_env("LLM_TIMEOUT", 900.0, float),
            temperature=_env("LLM_TEMPERATURE", 0.4, float),
            max_tokens=_env("LLM_MAX_TOKENS", 16384, int),
            retries=_env("LLM_RETRIES", 2, int),
            verify_llm=_env_bool("VERIFY_LLM"),
            verifier_model=os.getenv("VERIFIER_MODEL", "").strip() or None,
        )
