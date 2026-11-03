import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.input_check import parse_product  # noqa: E402

EXAMPLES = ROOT / "examples" / "input"


class FakeClient:
    """Подменяет модель: отдаёт заранее заданные ответы и запоминает запросы."""

    model = "fake-llm"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def grounded_description(product, target: int = 950) -> str:
    """Описание длиной 800–1200 символов только из предложений исходных данных."""
    sentences = re.split(r"(?<=[.])\s+", product.description)
    sentences += [f"{k[:1].upper()}{k[1:]}: {v}." for k, v in product.specs.items()]
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > 1150:
            continue
        out = f"{out} {s}".strip()
        if len(out) >= target:
            break
    assert 800 <= len(out) <= 1200, len(out)
    return out


def good_reply(product, **overrides) -> dict:
    reply = {
        "seo_title": "Манометр ТМ-510Р М2 РОСМА 0–10 кгс/см2, класс 1,5",
        "seo_description": grounded_description(product),
        "search_queries": ["манометр тм-510р м2", "манометр росма 100 мм", "манометр g1/2 класс точности 1,5"],
        "commercial": {
            "for_whom": "Предприятиям, обслуживающим и проектирующим объекты ЖКХ.",
            "tasks": ["Измерение избыточного давления жидких и газообразных сред"],
            "selling_points": ["Класс точности 1,5 и корпус 100 мм"],
        },
        "missing_or_unclear": [],
    }
    reply.update(overrides)
    return reply


@pytest.fixture
def manometer():
    return parse_product((EXAMPLES / "tm-510r-m2.json").read_text(encoding="utf-8"))


@pytest.fixture
def sensor():
    return parse_product((EXAMPLES / "pd100-di1.6-111-0.5.json").read_text(encoding="utf-8"))
