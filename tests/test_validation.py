"""Тесты проверок, которые не требуют обращения к LLM."""

from __future__ import annotations

from app.llm import LLMError, extract_json
from app.schemas import ProductCard, ProductInput, SpecItem
from app.validation import is_grounded, numbers_in, postprocess, to_markdown


def card(**overrides) -> ProductCard:
    payload = {
        "seo_title": "Датчик давления ПД100-ДИ1,6-111-0,5, 4–20 мА",
        "seo_description": "Д" * 900,
        "key_specs": [
            {"name": "Диапазон измерений", "value": "0–1,6 МПа"},
            {"name": "Выходной сигнал", "value": "4–20 мА"},
        ],
        "search_queries": ["датчик давления пд100", "датчик 1,6 мпа 4-20 ма", "пд100 купить"],
        "commercial_block": "Подходит для ЖКХ.",
        "missing_data": ["Не указан класс точности"],
    }
    payload.update(overrides)
    return ProductCard.model_validate(payload)


def product(**overrides) -> ProductInput:
    payload = {
        "name": "ПД100-ДИ1,6-111-0,5",
        "manufacturer": "ОВЕН",
        "category": "Датчики давления",
        "specs": {
            "Диапазон измерений": "0–1,6 МПа",
            "Выходной сигнал": "4–20 мА",
            "Напряжение питания": "12–36 В",
        },
        "description": "Датчик давления общепромышленный.",
    }
    payload.update(overrides)
    return ProductInput.model_validate(payload)


# --- нормализация чисел -------------------------------------------------


def test_numbers_handles_comma_and_dash():
    assert numbers_in("0–1,6 МПа") == {"0", "1.6"}
    assert numbers_in("от 4 до 20 мА") == {"4", "20"}


def test_numbers_strips_trailing_zeros():
    assert numbers_in("1,60") == numbers_in("1,6")


# --- защита от выдуманных характеристик --------------------------------


def test_grounded_value_passes():
    assert is_grounded("0–1,6 МПа", "Диапазон: 0–1,6 МПа")


def test_invented_number_fails():
    assert not is_grounded("0–2,5 МПа", "Диапазон: 0–1,6 МПа")


def test_text_value_without_numbers_passes():
    assert is_grounded("IP65", "Степень защиты корпуса: IP65")


def test_text_value_without_numbers_fails():
    assert not is_grounded("IP68", "Степень защиты корпуса: IP65")


def test_word_order_does_not_break_matching():
    # Модель вправе переставить слова местами — это не выдумка.
    assert is_grounded("белый пластик", "Крышка: пластик, цвет белый")


def test_unknown_word_is_rejected():
    assert not is_grounded("титановый корпус", "Корпус: пластик, цвет белый")


def test_word_inflection_is_not_a_fabrication():
    assert is_grounded(
        "двухпозиционное регулирование",
        "Закон регулирования: двухпозиционный",
    )
    assert is_grounded("двухпозиционный", "работает в двухпозиционного режиме")
    assert is_grounded("ЖК-дисплей", "Прибор оснащен ЖК-дисплеем")


# --- постобработка ------------------------------------------------------


def test_postprocess_drops_invented_spec():
    cleaned, report = postprocess(
        card(
            key_specs=[
                {"name": "Диапазон измерений", "value": "0–1,6 МПа"},
                {"name": "Степень защиты", "value": "IP68"},
            ]
        ),
        product(),
    )
    assert [item.value for item in cleaned.key_specs] == ["0–1,6 МПа"]
    assert report.specs_total == 2
    assert report.specs_verified == 1
    assert len(report.dropped_specs) == 1
    assert report.dropped_specs[0].value == "IP68"


def test_postprocess_deduplicates_specs():
    cleaned, _ = postprocess(
        card(
            key_specs=[
                {"name": "Диапазон измерений", "value": "0–1,6 МПа"},
                {"name": "диапазон  измерений", "value": "0–1,6 МПа"},
            ]
        ),
        product(),
    )
    assert len(cleaned.key_specs) == 1


def test_postprocess_caps_queries_at_five():
    cleaned, report = postprocess(
        card(search_queries=[f"запрос {i}" for i in range(9)]), product()
    )
    assert len(cleaned.search_queries) == 5
    assert any("оставлены первые" in warning for warning in report.warnings)


def test_shorten_title_does_not_leave_preposition():
    from app.validation import _shorten_title

    shortened = _shorten_title("Д" * 66 + " и хвост")
    assert len(shortened) <= 70
    assert not shortened.endswith(" и")


def test_postprocess_reports_short_description():
    cleaned, report = postprocess(card(seo_description="Коротко."), product())
    assert cleaned.seo_description == "Коротко."
    assert report.description_ok is False
    assert any("короче" in warning for warning in report.warnings)


def test_postprocess_warns_when_no_specs():
    cleaned, report = postprocess(
        card(key_specs=[]),
        product(specs={}, description=None),
    )
    assert cleaned.key_specs == []
    assert any("нет характеристик" in warning for warning in report.warnings)


def test_postprocess_shortens_long_title():
    long_title = (
        "Датчик давления общепромышленный ПД100-ДИ1,6-111-0,5 "
        "с выходом 4–20 мА и корпусом IP65"
    )
    assert len(long_title) > 70
    cleaned, report = postprocess(card(seo_title=long_title), product())
    assert len(cleaned.seo_title) <= 70
    assert any("сокращён" in warning for warning in report.warnings)


# --- разбор ответа модели ------------------------------------------------


def test_extract_json_strips_code_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_ignores_chatter():
    assert extract_json('Вот результат:\n{"a": 1}\nГотово') == {"a": 1}


def test_extract_json_raises_on_garbage():
    try:
        extract_json("извините, не могу")
    except LLMError:
        pass
    else:  # pragma: no cover
        raise AssertionError("ожидалась LLMError")


# --- схемы ---------------------------------------------------------------


def test_specs_accepts_numbers_and_drops_empty():
    item = ProductInput.model_validate(
        {"name": "Прибор", "specs": {"Диаметр": 100, "Пусто": "", "Нет": None}}
    )
    assert item.specs == {"Диаметр": "100"}


def test_specs_rejects_list():
    try:
        ProductInput.model_validate({"name": "Прибор", "specs": ["a", "b"]})
    except Exception:
        pass
    else:  # pragma: no cover
        raise AssertionError("ожидалась ошибка валидации")


def test_card_accepts_specs_mapping():
    parsed = ProductCard.model_validate(
        {
            "seo_title": "т",
            "seo_description": "о",
            "commercial_block": "к",
            "key_specs": {"Диапазон": "0–1,6 МПа"},
        }
    )
    assert parsed.key_specs == [SpecItem(name="Диапазон", value="0–1,6 МПа")]


# --- markdown ------------------------------------------------------------


def test_markdown_contains_all_sections():
    markdown = to_markdown(card(), product())
    for section in ["Ключевые характеристики", "Описание", "Поисковые запросы", "Коммерческий блок"]:
        assert section in markdown
