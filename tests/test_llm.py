import json

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError


def ok(content="{}"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def client_with(handler, **settings):
    base = {"api_key": "test-key", "base_url": "https://llm.example/v1", "model": "m", "max_retries": 2}
    base.update(settings)
    return LLMClient(Settings(**base), transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_success_sends_auth_and_json_mode():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return ok('{"a": 1}')

    assert client_with(handler).chat([{"role": "user", "content": "x"}]) == '{"a": 1}'
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_missing_key_fails_before_request():
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        client_with(lambda r: ok(), api_key="").chat([])


def test_local_model_needs_no_key():
    assert client_with(lambda r: ok("x"), api_key="", base_url="http://localhost:11434/v1").chat([]) == "x"


def test_401_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(LLMError, match="Ключ API не принят"):
        client_with(handler).chat([])
    assert len(calls) == 1


def test_429_is_retried_then_succeeds():
    responses = iter([httpx.Response(429), httpx.Response(503), ok("готово")])
    assert client_with(lambda r: next(responses)).chat([]) == "готово"


def test_server_errors_exhaust_retries():
    with pytest.raises(LLMError, match="после 3 попыток"):
        client_with(lambda r: httpx.Response(500)).chat([])


def test_timeout_is_reported():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError, match="не ответила"):
        client_with(handler, timeout=5).chat([])


def test_provider_without_json_mode_falls_back():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, text="response_format is not supported")
        return ok("{}")

    assert client_with(handler).chat([]) == "{}"
    assert "response_format" not in bodies[-1]


def test_bad_request_is_reported_after_json_fallback():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, text="bad model name")

    with pytest.raises(LLMError, match="HTTP 400: bad model name"):
        client_with(handler).chat([])
    assert len(calls) == 2 and "response_format" not in calls[1]


def test_empty_or_strange_answer():
    with pytest.raises(LLMError, match="пустой"):
        client_with(lambda r: ok("  ")).chat([])
    with pytest.raises(LLMError, match="неожиданного формата"):
        client_with(lambda r: httpx.Response(200, json={"foo": 1})).chat([])
