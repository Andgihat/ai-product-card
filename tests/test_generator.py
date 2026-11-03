"""Интеграционные тесты пайплайна с подставным LLM-клиентом (без сети и GPU)."""

from __future__ import annotations

import asyncio
import json

from app.config import Settings
from app.generator import generate_card
from app.schemas import ProductInput


class FakeClient:
    """Отдаёт заранее подготовленные ответы и запоминает вызовы."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages, **kwargs) -> str:  # noqa: ARG002
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("FakeClient: не хватило заготовленных ответов")
        return self.responses.pop(0)


def settings(**overrides) -> Settings:
    base = {
        "base_url": "http://127.0.0.1:1/v1",
        "model": "fake",
        "api_key": "test",
        "timeout": 5,
        "temperature": 0.4,
        "max_tokens": 1000,
        "retries": 0,
    }
    base.update(overrides)
    return Settings(**base)


def product() -> ProductInput:
    return ProductInput.model_validate(
        {
            "name": "ПД100-ДИ1,6-111-0,5",
            "manufacturer": "ОВЕН",
            "specs": {"Диапазон измерений": "0–1,6 МПа"},
            "description": "Датчик давления.",
        }
    )


def payload(**overrides) -> str:
    data = {
        "seo_title": "Датчик давления ПД100-ДИ1,6-111-0,5 с выходом 4–20 мА",
        "seo_description": "Д" * 900,
        "key_specs": [{"name": "Диапазон измерений", "value": "0–1,6 МПа"}],
        "search_queries": ["датчик давления пд100", "датчик давления 1,6 мпа", "пд100 купить"],
        "commercial_block": "Подходит для ЖКХ.",
        "missing_data": ["Не указан класс точности"],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_pipeline_returns_card_and_markdown():
    client = FakeClient([payload()])
    result = asyncio.run(generate_card(product(), settings(), client=client))

    assert len(client.calls) == 1
    assert result.card.seo_title.startswith("Датчик давления")
    assert result.validation.title_ok is True
    assert result.validation.description_ok is True
    assert result.validation.repaired is False
    assert "Ключевые характеристики" in result.markdown
    assert result.model == "fake"
    assert result.elapsed_ms >= 0


def test_pipeline_drops_invented_spec():
    client = FakeClient(
        [
            payload(
                key_specs=[
                    {"name": "Диапазон измерений", "value": "0–1,6 МПа"},
                    {"name": "Степень защиты", "value": "IP68"},
                ]
            )
        ]
    )
    result = asyncio.run(generate_card(product(), settings(), client=client))

    assert [item.name for item in result.card.key_specs] == ["Диапазон измерений"]
    assert result.validation.dropped_specs[0].value == "IP68"
    assert any("без подтверждения" in warning for warning in result.validation.warnings)


def test_pipeline_repairs_short_description_once():
    client = FakeClient(
        [
            payload(seo_description="Слишком коротко."),
            payload(),
        ]
    )
    result = asyncio.run(generate_card(product(), settings(), client=client))

    assert len(client.calls) == 2
    assert result.validation.repaired is True
    assert result.validation.description_ok is True
    assert "требуется от 800" in client.calls[1][1]["content"]


def test_pipeline_retries_on_broken_schema():
    client = FakeClient(
        [
            json.dumps({"seo_title": "без остальных полей"}, ensure_ascii=False),
            payload(),
        ]
    )
    result = asyncio.run(generate_card(product(), settings(), client=client))

    assert len(client.calls) == 2
    assert "не соответствует требуемой структуре" in client.calls[1][-1]["content"]
    assert result.validation.title_ok is True


# --- второй проход -------------------------------------------------------


def test_verifier_flags_unsupported_claim():
    verifier_answer = json.dumps(
        {
            "unsupported": [
                {
                    "quote": "Подходит для ЖКХ.",
                    "reason": "во входных данных нет области применения",
                }
            ]
        },
        ensure_ascii=False,
    )
    client = FakeClient([payload(), verifier_answer])
    result = asyncio.run(
        generate_card(product(), settings(verify_llm=True), client=client)
    )

    assert result.validation.verifier_used is True
    assert len(result.validation.unsupported_claims) == 1
    assert result.validation.unsupported_claims[0].quote == "Подходит для ЖКХ."


def test_verifier_drops_invented_quotes():
    verifier_answer = json.dumps(
        {"unsupported": [{"quote": "Такого текста в карточке нет", "reason": "…"}]},
        ensure_ascii=False,
    )
    client = FakeClient([payload(), verifier_answer])
    result = asyncio.run(
        generate_card(product(), settings(verify_llm=True), client=client)
    )

    assert result.validation.unsupported_claims == []


def test_verifier_failure_does_not_break_card():
    client = FakeClient([payload(), "не json"])
    result = asyncio.run(
        generate_card(product(), settings(verify_llm=True), client=client)
    )

    assert result.validation.verifier_used is False
    assert result.validation.title_ok is True
    assert any(
        "вторым проходом не удалась" in warning
        for warning in result.validation.warnings
    )
