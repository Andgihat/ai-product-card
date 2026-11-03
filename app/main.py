"""FastAPI-приложение: веб-форма и JSON API."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .generator import generate_card
from .llm import LLMClient, LLMError
from .schemas import GenerationResult, HealthResponse, ProductInput

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR.parent / "data"

# Pydantic отдаёт часть сообщений на английском — переводим самые частые,
# остальные оставляем как есть, чтобы не терять детали.
_ERROR_MESSAGES = {
    "Field required": "обязательное поле",
    "Input should be a valid string": "ожидалась строка",
    "Input should be a valid dictionary": "ожидался объект",
    "String should have at least 2 characters": "минимум 2 символа",
    "Input should be a valid list": "ожидался массив",
    "JSON decode error": "не удалось разобрать JSON",
}


def _humanize(message: str) -> str:
    if message.startswith("Value error, "):
        return message[len("Value error, ") :]
    return _ERROR_MESSAGES.get(message, message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings
    app.state.llm = LLMClient(settings)
    try:
        yield
    finally:
        await app.state.llm.aclose()


app = FastAPI(
    title="AI Product Card",
    description="Черновики карточек товаров для B2B-каталога КИП",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_client(request: Request) -> LLMClient:
    return request.app.state.llm


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Человекочитаемая ошибка вместо стандартного списка Pydantic."""
    details = []
    for error in exc.errors():
        location = " → ".join(
            str(part) for part in error["loc"] if isinstance(part, str) and part != "body"
        )
        details.append(f"{location or 'тело запроса'}: {_humanize(error['msg'])}")
    return JSONResponse(
        status_code=422,
        content={
            "error": "Некорректные входные данные",
            "details": details,
        },
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=HealthResponse)
async def health(
    settings: Settings = Depends(get_settings),
    client: LLMClient = Depends(get_client),
) -> HealthResponse:
    available, detail = await client.health()
    return HealthResponse(
        status="ok",
        llm_base_url=settings.base_url,
        llm_model=settings.model,
        llm_available=available,
        detail=detail,
    )


@app.get("/api/examples")
async def list_examples() -> list[dict[str, str]]:
    """Список демонстрационных товаров из каталога data/."""
    examples = []
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        examples.append(
            {
                "id": path.stem,
                "name": payload.get("name", path.stem),
                "manufacturer": payload.get("manufacturer", ""),
                "category": payload.get("category", ""),
            }
        )
    return examples


@app.get("/api/examples/{example_id}")
async def get_example(example_id: str) -> dict:
    # Имя файла приходит из URL, поэтому пропускаем только безопасный алфавит.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", example_id):
        raise HTTPException(status_code=404, detail="Пример не найден")
    path = DATA_DIR / f"{example_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Пример не найден")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Файл примера повреждён") from exc


@app.post("/api/generate", response_model=GenerationResult)
async def generate(
    product: ProductInput,
    settings: Settings = Depends(get_settings),
    client: LLMClient = Depends(get_client),
) -> GenerationResult:
    try:
        return await generate_card(product, settings, client=client)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
