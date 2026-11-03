"""Программные проверки карточки: защита от выдуманных характеристик и лимиты.

LLM — вероятностная модель, поэтому одним промптом ограничиваться нельзя.
После генерации каждое значение из key_specs сверяется с исходными данными:
все числа из значения должны встречаться во входных данных. Значения без чисел
проверяются как подстрока. Неподтверждённые характеристики удаляются.
"""

from __future__ import annotations

import re

from .schemas import ProductCard, ProductInput, SpecItem, ValidationReport

TITLE_MIN, TITLE_MAX = 40, 70
DESCRIPTION_MIN, DESCRIPTION_MAX = 800, 1200
QUERIES_MIN, QUERIES_MAX = 3, 5

_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_NON_WORD_RE = re.compile(r"[^0-9a-zа-я]+")

# Предлоги и союзы, на которых некрасиво обрывать заголовок при сокращении.
_DANGLING = {
    "и", "с", "со", "для", "на", "по", "в", "во", "из", "от", "до", "или",
    "а", "но", "при", "под", "над", "к", "ко", "о", "об", "за", "у",
}


def normalize(text: str) -> str:
    """Приводит текст к виду, удобному для сравнения."""
    text = text.replace("\u00a0", " ").replace("ё", "е").replace("Ё", "Е")
    for dash in _DASHES:
        text = text.replace(dash, "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_number(raw: str) -> str:
    value = raw.replace(",", ".")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value or "0"


def numbers_in(text: str) -> set[str]:
    """Множество чисел в тексте, нормализованных (1,60 → 1.6)."""
    return {_normalize_number(match) for match in _NUM_RE.findall(normalize(text))}


def _words(text: str) -> list[str]:
    return [word for word in _NON_WORD_RE.sub(" ", normalize(text)).split() if len(word) > 2]


def _same_word(left: str, right: str) -> bool:
    """Считает слова одной лексемой, если у них длинный общий префикс.

    «дисплей» и «дисплеем», «двухпозиционный» и «двухпозиционное» — одно слово.
    Порог в 4 символа отсекает случайные совпадения коротких предлогов.
    """
    if left == right:
        return True
    limit = min(len(left), len(right))
    prefix = 0
    while prefix < limit and left[prefix] == right[prefix]:
        prefix += 1
    return prefix >= max(4, limit - 3)


def is_grounded(value: str, source: str) -> bool:
    """Проверяет, что значение подтверждается исходными данными.

    Если в значении есть числа — каждое из них должно быть в источнике.
    Если чисел нет (например, «IP65», «пластик, цвет белый») — все значимые слова
    значения должны найтись в источнике. Порядок слов и падежи не важны:
    модель может переставить «белый пластик» и «пластик белый».
    """
    value_numbers = numbers_in(value)
    if value_numbers:
        return value_numbers <= numbers_in(source)

    words = _words(value)
    if not words:
        return False
    source_words = _words(source)
    return all(
        any(_same_word(word, candidate) for candidate in source_words)
        for word in words
    )


def source_text(product: ProductInput) -> str:
    """Склеивает все входные данные в один текст для проверок."""
    parts = [
        product.name,
        product.manufacturer or "",
        product.category or "",
        product.description or "",
    ]
    parts.extend(f"{key}: {value}" for key, value in product.specs.items())
    return "\n".join(part for part in parts if part)


def collect_issues(card: ProductCard) -> list[str]:
    """Формальные проблемы, которые имеет смысл попробовать исправить повтором."""
    issues: list[str] = []
    title_len = len(card.seo_title)
    if not TITLE_MIN <= title_len <= TITLE_MAX:
        issues.append(
            f"seo_title: {title_len} символов, требуется от {TITLE_MIN} до {TITLE_MAX}. "
            "Перепиши короче/длиннее, сохранив суть."
        )
    description_len = len(card.seo_description)
    if not DESCRIPTION_MIN <= description_len <= DESCRIPTION_MAX:
        direction = "короче" if description_len > DESCRIPTION_MAX else "длиннее"
        issues.append(
            f"seo_description: {description_len} символов, требуется от "
            f"{DESCRIPTION_MIN} до {DESCRIPTION_MAX}. Сделай текст {direction}."
        )
    if not QUERIES_MIN <= len(card.search_queries) <= QUERIES_MAX:
        issues.append(
            f"search_queries: {len(card.search_queries)} запросов, "
            f"требуется от {QUERIES_MIN} до {QUERIES_MAX}."
        )
    return issues


def _shorten_title(title: str, limit: int = TITLE_MAX) -> str:
    """Обрезает заголовок по границе слова, не оставляя предлог в конце."""
    if len(title) <= limit:
        return title
    cut = title[:limit].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    words = cut.split()
    while words and words[-1].lower().strip(" ,.-–—:;") in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,.-–—:;")


def postprocess(
    card: ProductCard, product: ProductInput
) -> tuple[ProductCard, ValidationReport]:
    """Чистит карточку и собирает отчёт о проверке."""
    source = source_text(product)
    warnings: list[str] = []

    kept: list[SpecItem] = []
    dropped: list[SpecItem] = []
    seen: set[str] = set()
    for item in card.key_specs:
        name = item.name.strip()
        value = item.value.strip()
        if not name or not value:
            continue
        key = normalize(name)
        if key in seen:
            continue
        if not is_grounded(value, source):
            dropped.append(SpecItem(name=name, value=value))
            continue
        seen.add(key)
        kept.append(SpecItem(name=name, value=value))

    if dropped:
        warnings.append(
            f"Удалено характеристик без подтверждения во входных данных: {len(dropped)}. "
            "Модель попыталась добавить значения, которых нет в INPUT."
        )

    if not product.specs:
        warnings.append(
            "Во входных данных нет характеристик — карточка собрана только по названию "
            "и описанию, её нужно проверить внимательнее."
        )

    queries: list[str] = []
    seen_queries: set[str] = set()
    for query in card.search_queries:
        cleaned = " ".join(str(query).split())
        key = normalize(cleaned)
        if not cleaned or key in seen_queries:
            continue
        seen_queries.add(key)
        queries.append(cleaned)
    if len(queries) > QUERIES_MAX:
        warnings.append(
            f"Модель предложила {len(queries)} запросов — оставлены первые {QUERIES_MAX}."
        )
        queries = queries[:QUERIES_MAX]

    title = card.seo_title.strip()
    if len(title) > TITLE_MAX:
        title = _shorten_title(title)
        warnings.append("SEO-заголовок был длиннее 70 символов и сокращён автоматически.")

    description = card.seo_description.strip()
    if len(description) > DESCRIPTION_MAX:
        warnings.append(
            f"SEO-описание длиннее {DESCRIPTION_MAX} символов — проверьте перед публикацией."
        )
    elif len(description) < DESCRIPTION_MIN:
        warnings.append(
            f"SEO-описание короче {DESCRIPTION_MIN} символов — стоит дописать вручную."
        )

    cleaned_card = ProductCard(
        seo_title=title,
        seo_description=description,
        key_specs=kept,
        search_queries=queries,
        commercial_block=card.commercial_block.strip(),
        missing_data=[str(item).strip() for item in card.missing_data if str(item).strip()],
    )

    report = ValidationReport(
        title_length=len(cleaned_card.seo_title),
        title_ok=TITLE_MIN <= len(cleaned_card.seo_title) <= TITLE_MAX,
        description_length=len(cleaned_card.seo_description),
        description_ok=DESCRIPTION_MIN
        <= len(cleaned_card.seo_description)
        <= DESCRIPTION_MAX,
        specs_total=len(card.key_specs),
        specs_verified=len(kept),
        dropped_specs=dropped,
        warnings=warnings,
    )
    return cleaned_card, report


def to_markdown(card: ProductCard, product: ProductInput) -> str:
    """Рендер карточки в Markdown — удобно копировать в CMS или письмо."""
    lines: list[str] = [f"# {card.seo_title}", ""]
    meta: list[str] = []
    if product.manufacturer:
        meta.append(f"**Производитель:** {product.manufacturer}")
    if product.category:
        meta.append(f"**Категория:** {product.category}")
    if meta:
        lines.extend(meta)
        lines.append("")

    lines.extend(["## Ключевые характеристики", "", "| Характеристика | Значение |", "| --- | --- |"])
    for item in card.key_specs:
        lines.append(f"| {item.name} | {item.value} |")
    lines.append("")

    lines.extend(["## Описание", "", card.seo_description, ""])
    lines.extend(["## Поисковые запросы", ""])
    lines.extend(f"- {query}" for query in card.search_queries)
    lines.append("")
    lines.extend(["## Коммерческий блок", "", card.commercial_block, ""])

    if card.missing_data:
        lines.extend(["## Что уточнить у производителя", ""])
        lines.extend(f"- {item}" for item in card.missing_data)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
