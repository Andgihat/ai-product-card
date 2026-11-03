"""Pydantic-схемы входных данных, результата и служебных отчётов."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductInput(BaseModel):
    """Данные о приборе, которые приходят на вход сервиса."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="Название товара, например «ПД100-ДИ1,6-111-0,5»")
    manufacturer: str | None = Field(None, description="Производитель")
    category: str | None = Field(None, description="Категория каталога")
    specs: dict[str, str] = Field(
        default_factory=dict,
        description='Характеристики в виде {"диапазон измерений": "0–1,6 МПа"}',
    )
    description: str | None = Field(
        None, description="Текстовое описание или фрагмент технического паспорта"
    )
    url: str | None = Field(None, description="Ссылка на страницу товара (необязательно)")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("поле «name» не может быть пустым")
        return value

    @field_validator("manufacturer", "category", "description", "url", mode="before")
    @classmethod
    def _normalize_optional(cls, value: Any) -> Any:
        """Пустые строки и null приводим к None, чтобы не тащить мусор в промпт."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("specs", mode="before")
    @classmethod
    def _coerce_specs(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(
                "поле «specs» должно быть объектом вида "
                '{"характеристика": "значение"}'
            )
        result: dict[str, str] = {}
        for key, raw in value.items():
            if raw is None:
                continue
            text = str(raw).strip()
            key_text = str(key).strip()
            if key_text and text:
                result[key_text] = text
        return result


class SpecItem(BaseModel):
    """Одна характеристика в готовой карточке."""

    name: str
    value: str


class ProductCard(BaseModel):
    """Черновик карточки товара — результат работы LLM."""

    model_config = ConfigDict(extra="ignore")

    seo_title: str
    seo_description: str
    key_specs: list[SpecItem] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    commercial_block: str
    missing_data: list[str] = Field(default_factory=list)

    @field_validator("key_specs", mode="before")
    @classmethod
    def _accept_mapping(cls, value: Any) -> Any:
        """Модель иногда отдаёт характеристики словарём вместо списка пар."""
        if isinstance(value, dict):
            return [{"name": key, "value": str(item)} for key, item in value.items()]
        return value

    @field_validator("seo_title", "seo_description", "commercial_block")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return " ".join(str(value).split())


class UnsupportedClaim(BaseModel):
    """Утверждение из карточки, которое второй проход не нашёл во входных данных."""

    quote: str
    reason: str


class ValidationReport(BaseModel):
    """Результат программной проверки карточки после генерации."""

    title_length: int
    title_ok: bool
    description_length: int
    description_ok: bool
    specs_total: int
    specs_verified: int
    dropped_specs: list[SpecItem] = Field(default_factory=list)
    unsupported_claims: list[UnsupportedClaim] = Field(default_factory=list)
    verifier_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    repaired: bool = False


class GenerationResult(BaseModel):
    """Полный ответ API."""

    product: ProductInput
    card: ProductCard
    validation: ValidationReport
    markdown: str
    model: str
    elapsed_ms: int


class HealthResponse(BaseModel):
    status: str
    llm_base_url: str
    llm_model: str
    llm_available: bool
    detail: str | None = None
