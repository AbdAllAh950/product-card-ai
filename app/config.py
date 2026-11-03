"""Настройки берутся только из переменных окружения или файла .env (он в .gitignore)."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    timeout: float = 60.0
    max_retries: int = 2
    json_mode: bool = True
    auth_scheme: str = "Bearer"  # Yandex AI Studio принимает и "Api-Key"
    verify: bool = True  # второй проход: модель-фактчекер сверяет утверждения без чисел
    extra_headers: dict = field(default_factory=dict)


def get_settings() -> Settings:
    headers = {}
    # Для Yandex AI Studio: идентификатор каталога передаётся в заголовке OpenAI-Project.
    project = os.getenv("LLM_PROJECT", "").strip()
    if project:
        headers["OpenAI-Project"] = project
    return Settings(
        base_url=os.getenv("LLM_BASE_URL", Settings.base_url).strip().rstrip("/"),
        api_key=os.getenv("LLM_API_KEY", "").strip(),
        model=os.getenv("LLM_MODEL", Settings.model).strip(),
        temperature=_float("LLM_TEMPERATURE", Settings.temperature),
        timeout=_float("LLM_TIMEOUT", Settings.timeout),
        max_retries=_int("LLM_MAX_RETRIES", Settings.max_retries),
        json_mode=os.getenv("LLM_JSON_MODE", "1").strip() == "1",
        auth_scheme=os.getenv("LLM_AUTH_SCHEME", "Bearer").strip() or "Bearer",
        verify=os.getenv("LLM_VERIFY", "1").strip() == "1",
        extra_headers=headers,
    )
