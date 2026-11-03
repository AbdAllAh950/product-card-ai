"""Клиент для любого OpenAI-совместимого Chat Completions API.

Подходит для OpenAI, DeepSeek, Yandex AI Studio (YandexGPT), GigaChat через совместимый шлюз
и локальных моделей (Ollama, vLLM): меняются только LLM_BASE_URL, LLM_MODEL и ключ.
"""

import time
from typing import Callable

import httpx

from .config import Settings

RETRY_STATUSES = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Ошибка обращения к модели. Текст показывается пользователю."""


def _is_local(url: str) -> bool:
    return any(host in url for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self._transport = transport
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self.settings.model

    def chat(self, messages: list[dict]) -> str:
        s = self.settings
        if not s.api_key and not _is_local(s.base_url):
            raise LLMError("Не задан ключ API: укажите LLM_API_KEY в файле .env (см. .env.example).")

        headers = {"Content-Type": "application/json", **s.extra_headers}
        if s.api_key:
            headers["Authorization"] = f"{s.auth_scheme} {s.api_key}"
        payload = {"model": s.model, "messages": messages, "temperature": s.temperature}
        json_mode = s.json_mode
        last_error = "неизвестная ошибка"

        with httpx.Client(timeout=s.timeout, transport=self._transport) as client:
            attempt = 0
            while attempt <= s.max_retries:
                body = dict(payload)
                if json_mode:
                    body["response_format"] = {"type": "json_object"}
                try:
                    resp = client.post(f"{s.base_url}/chat/completions", headers=headers, json=body)
                except httpx.TimeoutException:
                    last_error = f"модель не ответила за {s.timeout:g} с"
                    attempt += 1
                    self._sleep(min(2**attempt, 10))
                    continue
                except httpx.HTTPError as exc:
                    last_error = f"сетевая ошибка: {exc.__class__.__name__}"
                    attempt += 1
                    self._sleep(min(2**attempt, 10))
                    continue

                if resp.status_code in (401, 403):
                    raise LLMError(f"Ключ API не принят (HTTP {resp.status_code}). Проверьте LLM_API_KEY и права ключа.")
                if resp.status_code in (400, 422) and json_mode:
                    # провайдер может не поддерживать response_format — повторяем без него,
                    # JSON всё равно требуется промптом
                    json_mode = False
                    continue
                if resp.status_code in RETRY_STATUSES:
                    last_error = f"HTTP {resp.status_code} от API модели"
                    attempt += 1
                    self._sleep(min(2**attempt, 10))
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"API модели вернул ошибку HTTP {resp.status_code}: {resp.text[:300]}")

                try:
                    content = resp.json()["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError):
                    raise LLMError("API модели вернул ответ неожиданного формата.") from None
                if not content or not str(content).strip():
                    raise LLMError("Модель вернула пустой ответ.")
                return str(content)

        raise LLMError(f"Не удалось получить ответ модели после {s.max_retries + 1} попыток: {last_error}.")
