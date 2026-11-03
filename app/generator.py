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
from .llm import LLMError
from .prompts import (
    DESCRIPTION_MAX,
    DESCRIPTION_MIN,
    QUERIES_MAX,
    QUERIES_MIN,
    SYSTEM_PROMPT,
    TITLE_MAX,
    VERIFY_SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
    build_verify_prompt,
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


EXPECTED_KEYS = ("seo_title", "seo_description", "search_queries", "commercial")

# Служебные фразы об отсутствии данных уместны в missing_or_unclear, но не в тексте для покупателя.
META_RE = re.compile(
    r"(?:\bв|\bво) (?:исходных |предоставленных |имеющихся |этих )?данных|данных нет|"
    r"сведени\w* (?:нет|отсутству)|не указан\w* в данных",
    re.IGNORECASE,
)


TOP_KEYS = [("seo_title", str), ("seo_description", str), ("search_queries", list),
            ("commercial", dict), ("missing_or_unclear", list)]
COMMERCIAL_KEYS = [("for_whom", str), ("tasks", list), ("selling_points", list)]


def repair_keys(obj: dict, order: list[tuple[str, type]]) -> tuple[dict, list[str]]:
    """Восстанавливает имена ключей, которые модель потеряла.

    На реальных ответах Alice AI встречалось `{"seo_title": "...", ",": "<описание>"}`:
    вместо имени ключа — знак препинания. Такой ключ сопоставляем с первым недостающим
    ключом схемы того же типа (порядок ключей в ответе совпадает со схемой).
    """
    missing = [name for name, _ in order if name not in obj]
    fixed: dict = {}
    repaired: list[str] = []
    for key, value in obj.items():
        if re.search(r"\w", str(key)) or not missing:
            fixed[key] = value
            continue
        target = next((name for name, kind in order if name in missing and isinstance(value, kind)), None)
        if target:
            fixed[target] = value
            missing.remove(target)
            repaired.append(target)
        else:
            fixed[key] = value
    return fixed, repaired


def unwrap(data: dict) -> tuple[dict, list[str]]:
    """Приводит ответ к схеме: снимает обёртку {"card": {...}} и чинит потерянные имена ключей."""
    data, repaired = repair_keys(data, TOP_KEYS)
    if not any(k in data for k in EXPECTED_KEYS):
        for value in data.values():
            if isinstance(value, dict) and any(k in value for k in EXPECTED_KEYS):
                data, repaired = repair_keys(value, TOP_KEYS)
                break
        else:
            keys = ", ".join(list(data)[:8]) or "нет"
            raise ValueError(f"нет ключей seo_title, seo_description, search_queries, commercial; получены ключи: {keys}")
    if isinstance(data.get("commercial"), dict):
        data["commercial"], fixed = repair_keys(data["commercial"], COMMERCIAL_KEYS)
        repaired += [f"commercial.{k}" for k in fixed]
    return data, repaired


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
    if META_RE.search(card["seo_description"]):
        problems.append(
            "seo_description: текст для покупателя не должен сообщать об отсутствии данных — "
            "убери такие фразы, недостающее перечисли в missing_or_unclear."
        )

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


_WORD_RE = re.compile(r"[а-яёa-z]{4,}", re.IGNORECASE)
LEXICAL_SUPPORT_MIN = 0.8


def _stems(text: str) -> set[str]:
    """Грубая основа слова для русского текста: первые 5 букв слов длиной от 4 букв."""
    return {w.lower()[:5] for w in _WORD_RE.findall(text)}


def lexical_support(statement: str, source: str) -> float:
    """Доля значимых слов утверждения, основы которых встречаются во входных данных."""
    words = _stems(statement)
    return len(words & _stems(source)) / len(words) if words else 1.0


def _sentences(paragraph: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", paragraph) if s.strip()]


def collect_statements(card: dict) -> list[str]:
    """Утверждения для проверки моделью-фактчекером: предложения описания и блок менеджера."""
    statements = [s for p in card["seo_description"].split("\n\n") for s in _sentences(p)]
    for_whom = card["commercial"]["for_whom"]
    if for_whom and for_whom != NO_APPLICATION:
        statements.append(for_whom)
    return statements + card["commercial"]["tasks"] + card["commercial"]["selling_points"]


def verify_statements(product: Product, card: dict, client: "ChatClient") -> tuple[dict[str, str], str | None]:
    """Второй уровень защиты: модель сверяет утверждения без чисел с данными о товаре.

    Возвращает {утверждение: причина} и текст замечания, если проверку выполнить не удалось.
    """
    statements = collect_statements(card)
    if not statements:
        return {}, None
    try:
        reply = client.chat([
            {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
            {"role": "user", "content": build_verify_prompt(product, statements)},
        ])
        data = parse_json_reply(reply)
    except (LLMError, ValueError) as exc:
        return {}, f"Проверка утверждений моделью не выполнена ({exc}); проверьте блок для менеджера вручную."
    found: dict[str, str] = {}
    source = product.source_text()
    items = data.get("unsupported", [])
    for item in items if isinstance(items, list) else []:
        raw_id = item.get("id") if isinstance(item, dict) else item
        reason = _one_line(item.get("reason", "")) if isinstance(item, dict) else ""
        try:
            idx = int(raw_id)
        except (TypeError, ValueError):
            continue
        if not 1 <= idx <= len(statements):
            continue
        statement = statements[idx - 1]
        # Фактчекер тоже ошибается: на реальных ответах он помечал дословные цитаты из описания.
        # Учитываем отметку, только если есть конкретная причина и утверждение заметно расходится
        # с лексикой исходных данных.
        if reason and lexical_support(statement, source) < LEXICAL_SUPPORT_MIN:
            found[statement] = reason
    return found, None


def enforce(card: dict, product: Product, warnings: list[str], unsupported: dict[str, str] | None = None) -> dict:
    source = product.source_text()
    unsupported = unsupported or {}

    def problem(text: str) -> str | None:
        if _unverified(text, source):
            return "данные, которых нет во входных"
        if text in unsupported:
            reason = unsupported[text]
            return "утверждение не подтверждается данными" + (f": {reason}" if reason else "")
        return None

    if not card["seo_title"] or _unverified(card["seo_title"], source):
        if card["seo_title"]:
            warnings.append(f"SEO-заголовок модели содержал непроверенные данные и заменён названием товара: «{card['seo_title']}».")
        card["seo_title"] = product.name
    if len(card["seo_title"]) > TITLE_MAX:
        card["seo_title"] = _cut_words(card["seo_title"], TITLE_MAX)
        warnings.append(f"SEO-заголовок обрезан до {TITLE_MAX} символов — проверьте формулировку.")

    kept_paragraphs = []
    for paragraph in card["seo_description"].split("\n\n"):
        kept = []
        for sentence in _sentences(paragraph):
            reason = problem(sentence)
            if reason:
                warnings.append(f"Из описания удалена фраза ({reason}): «{sentence}»")
            elif META_RE.search(sentence):
                warnings.append(f"Из описания удалена служебная фраза об отсутствии данных: «{sentence}»")
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
    reason = problem(commercial["for_whom"]) if commercial["for_whom"] else "пусто"
    if reason:
        if commercial["for_whom"]:
            warnings.append(f"Блок «Кому подходит» заменён ({reason}): «{commercial['for_whom']}».")
        commercial["for_whom"] = NO_APPLICATION
    for key in ("tasks", "selling_points"):
        clean = []
        for item in commercial[key]:
            reason = problem(item)
            if reason:
                warnings.append(f"Из блока для менеджера удалён пункт ({reason}): «{item}».")
            else:
                clean.append(item)
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


def _ask(client: ChatClient, messages: list[dict], product: Product, raw_replies: list[str],
         keys_repaired: list[str]) -> tuple[dict | None, list[str], str]:
    """Один запрос к модели: ответ → карточка и список проблем."""
    reply = client.chat(messages)
    raw_replies.append(reply[:4000])
    try:
        data, repaired = unwrap(parse_json_reply(reply))
    except ValueError as exc:  # json.JSONDecodeError — подкласс ValueError
        return None, [f"Ответ не соответствует формату ({exc}). Верни только JSON-объект строго по схеме."], reply
    keys_repaired += repaired
    card = normalize(data)
    return card, find_problems(card, product.source_text(), sparse=product.is_sparse), reply


def generate_card(
    product: Product,
    client: ChatClient,
    max_repairs: int = MAX_REPAIRS,
    keep_raw: bool = False,
    verify: bool = False,
) -> Card:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(product)},
    ]
    best: tuple[dict, list[str]] | None = None  # ответ с наименьшим числом проблем
    repair_log: list[list[str]] = []
    raw_replies: list[str] = []
    keys_repaired: list[str] = []
    attempts = 0

    # 1. Генерация и исправление по детерминированным проверкам
    for attempt in range(max_repairs + 1):
        attempts += 1
        card, problems, reply = _ask(client, messages, product, raw_replies, keys_repaired)
        if card is not None and (best is None or len(problems) < len(best[1])):
            best = (card, problems)
        repair_log.append(problems)
        if not problems:
            break
        if attempt < max_repairs:
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": build_repair_prompt(problems)},
            ]

    if best is None:
        raise GenerationError(f"Модель не вернула корректный JSON по схеме за {attempts} попыток.")
    card = best[0]

    # 2. Проверка утверждений без чисел моделью-фактчекером + одно исправление
    unsupported: dict[str, str] = {}
    claims_log: list[dict] = []
    notes: list[str] = []
    if verify:
        unsupported, note = verify_statements(product, card, client)
        claims_log.append({"checked": len(collect_statements(card)), "unsupported": unsupported})
        if note:
            notes.append(note)
        if unsupported:
            attempts += 1
            fix = [f"Утверждение «{s}» не подтверждается данными ({r or 'нет в данных'}) — убери его или перепиши строго по данным."
                   for s, r in unsupported.items()]
            retry = messages + [
                {"role": "assistant", "content": json.dumps(card, ensure_ascii=False)},
                {"role": "user", "content": build_repair_prompt(fix)},
            ]
            new_card, new_problems, _ = _ask(client, retry, product, raw_replies, keys_repaired)
            repair_log.append(new_problems)
            if new_card is not None and len(new_problems) <= len(best[1]):
                card = new_card
                unsupported, note = verify_statements(product, card, client)
                claims_log.append({"checked": len(collect_statements(card)), "unsupported": unsupported})
                if note:
                    notes.append(note)

    # 3. Страховка: всё непроверенное удаляется с замечанием
    warnings: list[str] = list(notes)
    card = enforce(card, product, warnings, unsupported)
    extra_meta = {"raw_replies": raw_replies} if keep_raw else {}
    if keys_repaired:
        extra_meta["keys_repaired"] = keys_repaired
    if verify:
        extra_meta["claims_check"] = claims_log

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
            **extra_meta,
        },
    )
