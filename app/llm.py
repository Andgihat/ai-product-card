"""Клиент OpenAI-совместимого API (llama.cpp, Ollama, vLLM, OpenAI, …)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from .config import Settings


class LLMError(RuntimeError):
    """Понятная для пользователя ошибка обращения к модели."""


class _UnsupportedJsonMode(Exception):
    """Сервер не знает про response_format — повторим запрос без него."""


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Достаёт JSON-объект из ответа модели.

    Модели любят оборачивать JSON в ```-блоки или добавлять текст вокруг,
    поэтому сначала снимаем обёртку, затем пробуем разобрать ответ целиком,
    и только потом — первый {…}.
    """
    cleaned = text.strip()
    fence = _FENCE_RE.match(cleaned)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    snippet = text.strip().replace("\n", " ")[:300]
    raise LLMError(f"Модель вернула ответ, который не удалось разобрать как JSON: {snippet!r}")


class LLMClient:
    """Тонкая обёртка над /chat/completions с ретраями и таймаутом."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout),
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Отправляет сообщения в модель и возвращает текст ответа."""
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "temperature": self._settings.temperature if temperature is None else temperature,
            "max_tokens": self._settings.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if not json_mode:
            return await self._request(payload)

        payload["response_format"] = {"type": "json_object"}
        try:
            return await self._request(payload)
        except _UnsupportedJsonMode:
            # Без этого поля модель всё равно вернёт JSON: он требуется в промпте.
            payload.pop("response_format", None)
            return await self._request(payload)

    async def _request(self, payload: dict[str, Any]) -> str:
        """Один логический запрос: повторы только на временных ошибках."""
        url = f"{self._settings.base_url}/chat/completions"
        attempts = self._settings.retries + 1
        last_error: LLMError | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post(url, json=payload)
            except httpx.TimeoutException:
                last_error = LLMError(
                    f"Модель не ответила за {self._settings.timeout:.0f} с. "
                    "Увеличьте LLM_TIMEOUT или возьмите модель поменьше."
                )
            except httpx.HTTPError as exc:
                raise LLMError(
                    f"Не удалось подключиться к LLM по адресу {self._settings.base_url}. "
                    "Проверьте, что сервер модели запущен и LLM_BASE_URL указан верно. "
                    f"Детали: {exc}"
                ) from exc
            else:
                if response.status_code == 200:
                    return self._read_content(response)

                if (
                    response.status_code == 400
                    and "response_format" in response.text.lower()
                ):
                    raise _UnsupportedJsonMode

                if response.status_code == 429 or response.status_code >= 500:
                    last_error = LLMError(
                        f"LLM вернула HTTP {response.status_code}: {response.text[:300]}"
                    )
                else:
                    # Остальные 4xx ретраить бессмысленно: неверный запрос или ключ.
                    raise LLMError(
                        f"LLM отклонила запрос (HTTP {response.status_code}): "
                        f"{response.text[:300]}"
                    )

            if attempt < attempts:
                await asyncio.sleep(1.5 * 2 ** (attempt - 1))

        assert last_error is not None
        raise last_error

    @staticmethod
    def _read_content(response: httpx.Response) -> str:
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"Неожиданный формат ответа LLM: {response.text[:300]}"
            ) from exc
        if not content or not str(content).strip():
            raise LLMError("Модель вернула пустой ответ.")
        return str(content)

    async def health(self) -> tuple[bool, str | None]:
        """Проверяет доступность сервера модели (эндпоинт /models)."""
        try:
            response = await self._client.get(
                f"{self._settings.base_url}/models", timeout=5.0
            )
            if response.status_code == 200:
                return True, None
            return False, f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            return False, str(exc)
