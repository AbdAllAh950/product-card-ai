"""Общая точка входа для CLI и веб-сервиса."""

from .config import get_settings
from .generator import Card, generate_card
from .input_check import parse_product
from .llm import LLMClient


def make_client() -> LLMClient:
    return LLMClient(get_settings())


def card_from_json(raw: str | bytes | dict, client=None) -> Card:
    product = parse_product(raw)  # InputError — до обращения к модели
    return generate_card(product, client or make_client())
