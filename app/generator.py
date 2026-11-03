"""Сборка карточки: модель пишет тексты, код проверяет их и строит таблицу характеристик.

Цикл: запрос → разбор JSON → проверки (длины, количество запросов, числа и термины
не из входных данных) → если есть проблемы, модель получает их список и исправляет
(до MAX_REPAIRS раз). Если после этого что-то осталось, код сам убирает фразы
с непроверенными данными и пишет предупреждение — в карточку они не попадают.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .grounding import check_text
from .input_check import Product
from .prompts import (
    DESCRIPTION_MAX,
    DESCRIPTION_MIN,
    QUERIES_MAX,
    QUERIES_MIN,
    SYSTEM_PROMPT,
    TITLE_MAX,
    build_repair_prompt,
    build_user_prompt,
)

NO_DATA = "данных нет"
MAX_REPAIRS = 2
NO_APPLICATION = "Область применения в исходных данных не указана"


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict]) -> str: ...


class GenerationError(RuntimeError):
    """Модель так и не вернула пригодный ответ."""


@dataclass
class Card:
    name: str
    manufacturer: str
    seo_title: str
    seo_description: str
    characteristics: list[dict]
    search_queries: list[str]
    commercial: dict
    needs_clarification: list[str]
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- разбор ответа модели ----------

def parse_json_reply(text: str) -> dict:
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.IGNORECASE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("в ответе нет JSON-объекта")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("ответ не является JSON-объектом")
    return data


def _one_line(value) -> str:
    return " ".join(str(value).split()) if isinstance(value, (str, int, float)) else ""


def _items(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for s in (_one_line(v) for v in value) if s]


def normalize(data: dict) -> dict:
    commercial = data.get("commercial") if isinstance(data.get("commercial"), dict) else {}
    description = data.get("seo_description") if isinstance(data.get("seo_description"), str) else ""
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\\n\\n", description) if p.strip()]
    title = _one_line(data.get("seo_title", ""))
    queries = []
    for q in _items(data.get("search_queries")):
        q = q.lower()
        if q not in queries:
            queries.append(q)
    return {
        "seo_title": title[:1].upper() + title[1:],
        "seo_description": "\n\n".join(paragraphs),
        "search_queries": queries,
        "commercial": {
            "for_whom": _one_line(commercial.get("for_whom", "")),
            "tasks": _items(commercial.get("tasks")),
            "selling_points": _items(commercial.get("selling_points")),
        },
        "missing_or_unclear": _items(data.get("missing_or_unclear")),
    }


def _generated_fields(card: dict) -> list[tuple[str, str]]:
    fields = [("seo_title", card["seo_title"]), ("seo_description", card["seo_description"])]
    fields += [(f"search_queries[{i}]", q) for i, q in enumerate(card["search_queries"])]
    fields.append(("commercial.for_whom", card["commercial"]["for_whom"]))
    fields += [(f"commercial.tasks[{i}]", t) for i, t in enumerate(card["commercial"]["tasks"])]
    fields += [(f"commercial.selling_points[{i}]", t) for i, t in enumerate(card["commercial"]["selling_points"])]
    return fields


def find_problems(card: dict, source: str, sparse: bool = False) -> list[str]:
    problems = []
    title_len = len(card["seo_title"])
    if not title_len:
        problems.append("seo_title пустой.")
    elif title_len > TITLE_MAX:
        problems.append(f"seo_title — {title_len} символов, нужно не больше {TITLE_MAX}. Сократи, сохранив тип прибора и модель.")

    desc_len = len(card["seo_description"])
    if desc_len < DESCRIPTION_MIN and not sparse:
        problems.append(
            f"seo_description — {desc_len} символов, нужно {DESCRIPTION_MIN}–{DESCRIPTION_MAX}. "
            "Подробнее раскрой то, что уже есть в данных (назначение, конструкция, условия эксплуатации), без новых фактов."
        )
    elif desc_len > DESCRIPTION_MAX:
        problems.append(f"seo_description — {desc_len} символов, нужно {DESCRIPTION_MIN}–{DESCRIPTION_MAX}. Сократи, убрав повторы.")

    count = len(card["search_queries"])
    if not QUERIES_MIN <= count <= QUERIES_MAX:
        problems.append(f"search_queries — {count} шт., нужно {QUERIES_MIN}–{QUERIES_MAX}.")
    if not card["commercial"]["for_whom"]:
        problems.append("commercial.for_whom пустой.")
    if not card["commercial"]["tasks"]:
        problems.append("commercial.tasks пустой.")

    for label, text in _generated_fields(card):
        numbers, terms = check_text(text, source)
        if numbers:
            problems.append(f"{label}: числа {', '.join(numbers)} отсутствуют во входных данных — убери их или используй значения из данных.")
        if terms:
            problems.append(f"{label}: упоминается «{', '.join(terms)}», а во входных данных этого нет — убери.")
    return problems


# ---------- финальная страховка ----------

def _unverified(text: str, source: str) -> bool:
    numbers, terms = check_text(text, source)
    return bool(numbers or terms)


def _cut_words(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut or text[:limit]


def _cut_sentences(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = max(head.rfind(". "), head.rfind(".\n"), head.rfind("."))
    return head[: end + 1] if end > limit // 2 else _cut_words(text, limit)


def enforce(card: dict, product: Product, warnings: list[str]) -> dict:
    source = product.source_text()

    if not card["seo_title"] or _unverified(card["seo_title"], source):
        if card["seo_title"]:
            warnings.append(f"SEO-заголовок модели содержал непроверенные данные и заменён названием товара: «{card['seo_title']}».")
        card["seo_title"] = product.name
    if len(card["seo_title"]) > TITLE_MAX:
        card["seo_title"] = _cut_words(card["seo_title"], TITLE_MAX)
        warnings.append(f"SEO-заголовок обрезан до {TITLE_MAX} символов — проверьте формулировку.")

    kept_paragraphs = []
    for paragraph in card["seo_description"].split("\n\n"):
        sentences = re.split(r"(?<=[.!?…])\s+", paragraph)
        kept = []
        for sentence in sentences:
            if _unverified(sentence, source):
                warnings.append(f"Из описания удалена фраза с данными, которых нет во входных: «{sentence}»")
            else:
                kept.append(sentence)
        if kept:
            kept_paragraphs.append(" ".join(kept))
    card["seo_description"] = "\n\n".join(kept_paragraphs)
    if len(card["seo_description"]) > DESCRIPTION_MAX:
        card["seo_description"] = _cut_sentences(card["seo_description"], DESCRIPTION_MAX)
        warnings.append(f"SEO-описание сокращено до {DESCRIPTION_MAX} символов по границе предложения.")
    if len(card["seo_description"]) < DESCRIPTION_MIN:
        warnings.append(
            f"SEO-описание — {len(card['seo_description'])} символов (норма {DESCRIPTION_MIN}–{DESCRIPTION_MAX}): "
            "во входных данных мало фактов, дополните описание вручную."
        )

    for key, label in (("search_queries", "поисковый запрос"),):
        clean = [q for q in card[key] if not _unverified(q, source)]
        for q in card[key]:
            if q not in clean:
                warnings.append(f"Удалён {label} с непроверенными данными: «{q}».")
        card[key] = clean[:QUERIES_MAX]
    if len(card["search_queries"]) < QUERIES_MIN:
        warnings.append(f"Поисковых запросов меньше {QUERIES_MIN} — добавьте вручную.")

    commercial = card["commercial"]
    if not commercial["for_whom"] or _unverified(commercial["for_whom"], source):
        if commercial["for_whom"]:
            warnings.append(f"Блок «Кому подходит» содержал непроверенные данные и заменён: «{commercial['for_whom']}».")
        commercial["for_whom"] = NO_APPLICATION
    for key in ("tasks", "selling_points"):
        clean = [t for t in commercial[key] if not _unverified(t, source)]
        for t in commercial[key]:
            if t not in clean:
                warnings.append(f"Из блока для менеджера удалён пункт с непроверенными данными: «{t}».")
        commercial[key] = clean
    return card


# ---------- карточка ----------

def build_characteristics(product: Product) -> list[dict]:
    rows = [{"name": "Производитель", "value": product.manufacturer or NO_DATA}]
    rows += [{"name": k[:1].upper() + k[1:], "value": v} for k, v in product.specs.items()]
    rows += [{"name": k[:1].upper() + k[1:], "value": NO_DATA} for k in product.empty_specs]
    rows += [{"name": item, "value": NO_DATA} for item in product.missing_specs]
    return rows


def _clarifications(product_issues: list[str], model_items: list[str]) -> list[str]:
    """Замечания по входным данным + пункты модели, которые их не повторяют."""
    out = list(product_issues)
    known = " ".join(product_issues).lower()
    seen = set()
    for item in model_items:
        key = item.lower().strip(" .")
        if key and key not in seen and key not in known:
            seen.add(key)
            out.append(item[:1].upper() + item[1:])
    return out


def generate_card(product: Product, client: ChatClient, max_repairs: int = MAX_REPAIRS) -> Card:
    source = product.source_text()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(product)},
    ]
    card: dict | None = None
    problems: list[str] = []
    repair_log: list[list[str]] = []
    attempts = 0

    for attempt in range(max_repairs + 1):
        attempts += 1
        reply = client.chat(messages)
        try:
            card = normalize(parse_json_reply(reply))
            problems = find_problems(card, source, sparse=product.is_sparse)
        except ValueError as exc:  # json.JSONDecodeError — подкласс ValueError
            problems = [f"Ответ не является корректным JSON ({exc}). Верни только JSON-объект по схеме."]
        repair_log.append(problems)
        if not problems:
            break
        if attempt < max_repairs:
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": build_repair_prompt(problems)},
            ]

    if card is None:
        raise GenerationError(f"Модель не вернула корректный JSON за {attempts} попыток.")

    warnings: list[str] = []
    card = enforce(card, product, warnings)

    return Card(
        name=product.name,
        manufacturer=product.manufacturer or NO_DATA,
        seo_title=card["seo_title"],
        seo_description=card["seo_description"],
        characteristics=build_characteristics(product),
        search_queries=card["search_queries"],
        commercial=card["commercial"],
        needs_clarification=_clarifications(product.issues, card["missing_or_unclear"]),
        warnings=warnings,
        meta={
            "model": client.model,
            "attempts": attempts,
            "checks_per_attempt": repair_log,
            "fact_check": "пройдена" if not warnings else "есть замечания, см. warnings",
            "device_type": product.device_type,
            "title_length": len(card["seo_title"]),
            "description_length": len(card["seo_description"]),
            **{k: v for k, v in product.extra.items()},
        },
    )
