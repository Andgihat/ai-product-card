"""Оркестрация: запрос к LLM → разбор → при необходимости правка → проверки."""

from __future__ import annotations

import time
from dataclasses import replace

from pydantic import ValidationError

from .config import Settings
from .llm import LLMClient, LLMError, extract_json
from .prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from .schemas import GenerationResult, ProductCard, ProductInput, UnsupportedClaim
from .validation import collect_issues, postprocess, to_markdown
from .verifier import verify_card


def _schema_issues(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(str(item) for item in error["loc"]) or "ответ"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def _parse_card(raw: str) -> ProductCard | None:
    """Разбирает ответ модели; None, если структура не сошлась."""
    try:
        return ProductCard.model_validate(extract_json(raw))
    except ValidationError:
        return None


async def _request_card(client: LLMClient, messages: list[dict[str, str]]) -> ProductCard:
    """Первый запрос и один повтор, если модель вернула не ту структуру."""
    raw = await client.chat(messages)
    try:
        return ProductCard.model_validate(extract_json(raw))
    except ValidationError as first_error:
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Ответ не соответствует требуемой структуре: "
                    f"{_schema_issues(first_error)}. Верни исправленный JSON целиком."
                ),
            },
        ]
        raw = await client.chat(retry_messages)
        try:
            return ProductCard.model_validate(extract_json(raw))
        except ValidationError as second_error:
            raise LLMError(
                "Модель дважды вернула JSON неправильной структуры. "
                f"Ошибки: {_schema_issues(second_error)}"
            ) from second_error


async def _repair_card(
    client: LLMClient, product: ProductInput, card: ProductCard, issues: list[str]
) -> ProductCard | None:
    """Просит модель исправить только формальные замечания."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_repair_prompt(product, card, issues)},
    ]
    return _parse_card(await client.chat(messages))


async def _verify(
    client: LLMClient,
    settings: Settings,
    product: ProductInput,
    card: ProductCard,
) -> list[UnsupportedClaim]:
    """Второй проход. Для отдельной модели создаём отдельный клиент."""
    if settings.verifier_model and settings.verifier_model != settings.model:
        verifier_client = LLMClient(replace(settings, model=settings.verifier_model))
        try:
            return await verify_card(verifier_client, product, card)
        finally:
            await verifier_client.aclose()
    return await verify_card(client, product, card)


async def generate_card(
    product: ProductInput,
    settings: Settings,
    client: LLMClient | None = None,
) -> GenerationResult:
    """Генерирует карточку товара.

    Клиент можно передать снаружи (например, из FastAPI), чтобы переиспользовать
    HTTP-соединение. Если клиент не передан, он создаётся и закрывается здесь.
    """
    own_client = client is None
    if client is None:
        client = LLMClient(settings)
    try:
        return await _generate(client, product, settings)
    finally:
        if own_client:
            await client.aclose()


async def _generate(
    client: LLMClient, product: ProductInput, settings: Settings
) -> GenerationResult:
    started = time.perf_counter()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(product)},
    ]
    card = await _request_card(client, messages)

    issues = collect_issues(card)
    repaired = False
    if issues:
        repaired_card = await _repair_card(client, product, card, issues)
        if repaired_card is not None:
            card, repaired = repaired_card, True

    cleaned, report = postprocess(card, product)
    report.repaired = repaired
    if issues and not repaired:
        report.warnings.append(
            "Автоматическая правка формата не удалась, карточку нужно проверить вручную."
        )

    if settings.verify_llm:
        try:
            report.unsupported_claims = await _verify(client, settings, product, cleaned)
            report.verifier_used = True
        except (LLMError, ValidationError) as exc:
            # Проверка — вспомогательный слой: без неё карточка остаётся валидной.
            report.warnings.append(f"Проверка вторым проходом не удалась: {exc}")

    return GenerationResult(
        product=product,
        card=cleaned,
        validation=report,
        markdown=to_markdown(cleaned, product),
        model=settings.model,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
