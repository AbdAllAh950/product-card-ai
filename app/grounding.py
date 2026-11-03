"""Проверка, что модель не добавила фактов, которых нет во входных данных.

Две детерминированные проверки:
1. Числа: каждое число в сгенерированном тексте должно встречаться в исходных данных
   (с учётом записи «1,5» = «1.5» и «500 000» = «500000»). Пересчёт единиц
   (МПа → бар) тоже ловится: новое число не совпадёт с исходным.
2. «Рискованные» термины: стандарты, сертификаты, взрывозащита, интерфейсы и т. п.
   Если термин есть в ответе, но его нет в исходных данных, — это выдумка.
"""

import re

_THOUSANDS_RE = re.compile(r"(?<![\d.,])(\d{1,3})((?:[   ]\d{3})+)(?![\d])")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

# (метка для отчёта, регулярное выражение)
RISKY_TERMS: list[tuple[str, str]] = [
    ("ГОСТ", r"гост"),
    ("ТР ТС / ЕАЭС", r"тр\s?тс|\bеаэс\b|\beac\b"),
    ("сертификат", r"сертифик"),
    ("взрывозащита / Ex", r"взрыво|\bex\b|atex"),
    ("гарантия / «гарантирует»", r"гаранти"),
    ("поверка", r"поверк|поверочн"),
    ("Госреестр СИ", r"госреестр|реестр\w* си\b|реестр средств измерений"),
    ("интерфейс RS-485/Modbus/HART", r"rs-?485|modbus|hart|profibus|profinet"),
    ("беспроводная связь", r"bluetooth|wi-?fi|gsm|lora|nb-iot"),
    ("степень защиты IP", r"\bip\s?\d{2}"),
    ("нержавеющая сталь", r"нержаве"),
    ("агрессивные среды", r"агрессивн"),
    ("пищевое исполнение", r"пищев"),
    ("кислород / кислородное исполнение", r"кислород"),
    ("аммиак", r"аммиа"),
    ("виброустойчивость", r"вибр"),
    ("гидроудары", r"гидроудар"),
    ("морское исполнение", r"морск|судов"),
    ("атомная отрасль", r"атомн|аэс\b"),
    ("медицина", r"медицин"),
    ("цена / наличие / сроки поставки", r"₽|руб\.|\bруб\b|в наличии|срок поставки|доставк"),
]


def _join_thousands(text: str) -> str:
    return _THOUSANDS_RE.sub(lambda m: m.group(1) + re.sub(r"\D", "", m.group(2)), text)


def numbers_in(text: str) -> set[str]:
    """Нормализованные числа текста: «1,5» → «1.5», «500 000» → «500000», «010» → «10»."""
    found = set()
    for raw in _NUMBER_RE.findall(_join_thousands(text or "")):
        value = raw.replace(",", ".")
        if "." in value:
            whole, frac = value.split(".", 1)
            frac = frac.rstrip("0")
            value = f"{int(whole)}.{frac}" if frac else str(int(whole))
        else:
            value = str(int(value))
        found.add(value)
    return found


def invented_numbers(generated: str, source: str) -> list[str]:
    allowed = numbers_in(source)
    return sorted(numbers_in(generated) - allowed, key=lambda s: (len(s), s))


def invented_terms(generated: str, source: str) -> list[str]:
    gen, src = (generated or "").lower(), (source or "").lower()
    return [label for label, pattern in RISKY_TERMS if re.search(pattern, gen) and not re.search(pattern, src)]


def check_text(generated: str, source: str) -> tuple[list[str], list[str]]:
    return invented_numbers(generated, source), invented_terms(generated, source)
