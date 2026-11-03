"""Второй проход: LLM-фактчекер прозы карточки.

Детерминированная проверка (`validation.py`) гарантирует, что в характеристиках
нет чисел и слов, которых нет во входных данных. Но она не читает смысл описания
и коммерческого блока: фразу «подходит для агрессивных сред» регуляркой не поймать.

Этот модуль — опциональный слой для таких случаев. Он не правит карточку,
а только возвращает список утверждений, которые не подтверждаются INPUT.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from .llm import LLMClient, LLMError, extract_json
from .schemas import ProductCard, ProductInput, UnsupportedClaim
from .validation import normalize

VERIFIER_SYSTEM = """Ты — фактчекер B2B-каталога контрольно-измерительных приборов.
Тебе дают INPUT с данными прибора и готовую карточку товара.
Найди в полях seo_description и commercial_block утверждения, которые НЕ подтверждаются INPUT.

Правила:
- Проверяй только факты: числа, материалы, диапазоны, области применения, совместимость, условия эксплуатации.
- Не придирайся к стилю, порядку слов и общим фразам вроде «прибор предназначен для измерения давления», если это следует из INPUT.
- Для каждого нарушения приведи точную цитату из карточки и объясни, чего не хватает во входных данных.
- Если утверждение подтверждается INPUT хотя бы косвенно — не включай его.
- Если сомневаешься — не включай: ложное обвинение хуже пропуска.
- Если нарушений нет, верни пустой список.

Отвечай одним JSON-объектом без markdown:
{"unsupported": [{"quote": "точная цитата из карточки", "reason": "чего не хватает во входных данных"}]}"""


def build_verifier_prompt(product: ProductInput, card: ProductCard) -> str:
    payload = {
        "INPUT": product.model_dump(exclude_none=True),
        "CARD": {
            "seo_description": card.seo_description,
            "commercial_block": card.commercial_block,
        },
    }
    return (
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Верни JSON указанной структуры."
    )


def _quote_exists(quote: str, card: ProductCard) -> bool:
    """Защита от выдуманных цитат: цитата должна быть в тексте карточки."""
    text = normalize(f"{card.seo_description} {card.commercial_block}")
    return bool(quote) and normalize(quote) in text


async def verify_card(
    client: LLMClient,
    product: ProductInput,
    card: ProductCard,
) -> list[UnsupportedClaim]:
    """Возвращает утверждения, не подтверждённые входными данными."""
    raw = await client.chat(
        [
            {"role": "system", "content": VERIFIER_SYSTEM},
            {"role": "user", "content": build_verifier_prompt(product, card)},
        ],
        temperature=0.0,
    )
    data = extract_json(raw)

    claims = data.get("unsupported", [])
    if not isinstance(claims, list):
        raise LLMError("Фактчекер вернул поле 'unsupported' не в виде списка.")

    result: list[UnsupportedClaim] = []
    for item in claims:
        try:
            claim = UnsupportedClaim.model_validate(item)
        except ValidationError:
            continue
        claim.quote = claim.quote.strip()
        claim.reason = claim.reason.strip()
        if _quote_exists(claim.quote, card):
            result.append(claim)
    return result
