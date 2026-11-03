"""CLI: генерация карточки без запуска веб-сервера.

Примеры:
    python -m app.cli data/pd100.json
    python -m app.cli data/pd100.json --json --out examples/pd100.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .config import Settings
from .generator import generate_card
from .llm import LLMError
from .schemas import ProductInput


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Генерация черновика карточки товара через LLM"
    )
    parser.add_argument("input", type=Path, help="Путь к JSON с данными товара")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести полный JSON-ответ вместо Markdown",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Сохранить результат в файл (по умолчанию — в stdout)",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Не удалось прочитать {args.input}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Некорректный JSON в {args.input}: {exc}", file=sys.stderr)
        return 1

    try:
        product = ProductInput.model_validate(payload)
    except ValidationError as exc:
        print(f"Данные товара не прошли валидацию: {exc}", file=sys.stderr)
        return 1

    settings = Settings.from_env()
    try:
        result = await generate_card(product, settings)
    except LLMError as exc:
        print(f"Ошибка LLM: {exc}", file=sys.stderr)
        return 2

    output = result.model_dump_json(indent=2) if args.json else result.markdown

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + ("" if output.endswith("\n") else "\n"), encoding="utf-8")
        print(f"Сохранено: {args.out}", file=sys.stderr)
        if not args.json:
            print(
                f"Заголовок: {result.validation.title_length} символов, "
                f"описание: {result.validation.description_length} символов, "
                f"характеристик подтверждено: "
                f"{result.validation.specs_verified}/{result.validation.specs_total}",
                file=sys.stderr,
            )
    else:
        print(output)
    return 0


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
