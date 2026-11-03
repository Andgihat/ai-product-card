"""Проверка подключения к LLM по настройкам из .env.

Делает два запроса:
1. GET /models — отвечает ли сервер и есть ли среди моделей указанная;
2. POST /chat/completions — проходит ли реальный запрос.

Запуск:
    python scripts/check_llm.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402

HINTS = {
    401: "проверьте LLM_API_KEY",
    403: "ключ есть, но доступ к модели закрыт — проверьте тариф и права ключа",
    404: "проверьте LLM_BASE_URL: адрес обычно заканчивается на /v1",
    429: "превышен лимит запросов — подождите или проверьте квоту",
}


def request_json(url: str, api_key: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}"}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:300]}


def main() -> int:
    settings = Settings.from_env()
    print(f"Адрес:  {settings.base_url}")
    print(f"Модель: {settings.model}")
    print()

    models_url = f"{settings.base_url}/models"
    try:
        status, payload = request_json(models_url, settings.api_key)
    except urllib.error.URLError as exc:
        print(f"[x] Сервер недоступен: {getattr(exc, 'reason', exc)}")
        print("    Проверьте, что модель запущена и LLM_BASE_URL указывает на неё.")
        return 1
    except TimeoutError:
        print("[x] Сервер не ответил за 60 с — проверьте адрес и LLM_TIMEOUT.")
        return 1

    if status != 200:
        print(f"[x] /models вернул HTTP {status}: {payload}")
        print(f"    Подсказка: {HINTS.get(status, 'проверьте адрес и ключ')}")
        return 1

    available = [
        item.get("id") for item in payload.get("data", []) if isinstance(item, dict)
    ]
    print(f"[+] Сервер отвечает, моделей в списке: {len(available)}")
    if available and settings.model not in available:
        print(f"[!] Модели «{settings.model}» нет в списке. Доступные: {', '.join(map(str, available[:5]))}")
        print("    Возможно, LLM_MODEL указан с опечаткой.")
    elif available:
        print(f"[+] Модель «{settings.model}» найдена")

    print()
    print("Пробный запрос…")
    started = time.perf_counter()
    try:
        status, payload = request_json(
            f"{settings.base_url}/chat/completions",
            settings.api_key,
            {
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": "Отвечай максимально кратко."},
                    {"role": "user", "content": "Скажи одно слово: работает"},
                ],
                "max_tokens": 1024,
                "temperature": 0,
                "stream": False,
            },
        )
    except urllib.error.URLError as exc:
        print(f"[x] Запрос не прошёл: {getattr(exc, 'reason', exc)}")
        return 1

    elapsed = time.perf_counter() - started
    if status != 200:
        print(f"[x] HTTP {status} за {elapsed:.1f} с: {payload}")
        print(f"    Подсказка: {HINTS.get(status, 'проверьте настройки модели')}")
        return 1

    try:
        choice = payload["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        finish = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError):
        print(f"[x] Неожиданный формат ответа: {str(payload)[:300]}")
        return 1

    print(f"[+] Ответ получен за {elapsed:.1f} с")
    reasoning = (
        (payload.get("usage") or {}).get("completion_tokens_details") or {}
    ).get("reasoning_tokens")
    if reasoning:
        print(f"    Reasoning-токенов: {reasoning}")
    if content:
        print(f"    Модель: {content[:200]}")
    else:
        print(f"    Пустой content (finish_reason={finish}).")
        print("    Похоже, весь бюджет ушёл на reasoning — увеличьте LLM_MAX_TOKENS.")
    print()
    print("Подключение работает.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
