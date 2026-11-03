"""Разбор и проверка входных данных о товаре.

Фатальные ошибки (некорректный JSON, нет названия) → InputError с понятным текстом.
Нефатальные (пустые поля, нет характеристик) → список issues: карточка всё равно
строится, а недостающие данные явно показываются контент-менеджеру.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from .checklist import detect_type, missing_recommended

MAX_NAME = 300
MAX_DESCRIPTION = 8000
MAX_SPECS = 80


class InputError(ValueError):
    """Входные данные нельзя обработать. Текст ошибки показывается пользователю."""


@dataclass
class Product:
    name: str
    manufacturer: str
    specs: dict[str, str]
    description: str
    device_type: str
    extra: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    missing_specs: list[str] = field(default_factory=list)  # из чек-листа типа, но нет в данных
    empty_specs: list[str] = field(default_factory=list)  # переданы во входных данных без значения

    @property
    def is_sparse(self) -> bool:
        """Мало фактов: нет развёрнутого описания и меньше 6 характеристик.

        В этом случае модель не просят «дотягивать» описание до 800 символов —
        иначе она заполнит объём общими фразами.
        """
        return len(self.description) < 300 and len(self.specs) < 6

    def facts(self) -> dict[str, Any]:
        """Данные, которые передаются в модель как единственный источник фактов."""
        data: dict[str, Any] = {
            "name": self.name,
            "manufacturer": self.manufacturer or "данных нет",
            "specs": self.specs,
            "description": self.description or "данных нет",
        }
        if self.extra.get("category"):
            data["category"] = self.extra["category"]
        return data

    def source_text(self) -> str:
        """Весь текст исходных данных — для проверки, что в ответе нет чужих цифр."""
        parts = [self.name, self.manufacturer, self.description, self.extra.get("category", "")]
        parts += [f"{k}: {v}" for k, v in self.specs.items()]
        return "\n".join(str(p) for p in parts if p)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return " ".join(str(value).split())


def _parse_specs(raw: Any, issues: list[str]) -> tuple[dict[str, str], list[str]]:
    """specs: объект {"параметр": "значение"} или список [{"name": ..., "value": ...}]."""
    if raw is None:
        return {}, []
    pairs: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "name" in item:
                pairs.append((item.get("name"), item.get("value")))
            else:
                issues.append("Элемент списка specs пропущен: ожидается {\"name\": ..., \"value\": ...}.")
    else:
        raise InputError("Поле specs должно быть объектом {\"параметр\": \"значение\"} или списком пар.")

    specs: dict[str, str] = {}
    empty: list[str] = []
    for key, value in pairs[:MAX_SPECS]:
        key = _clean(key)
        if not key:
            continue
        value = _clean(value)
        if value:
            specs[key] = value
        else:
            empty.append(key)
    if len(pairs) > MAX_SPECS:
        issues.append(f"Передано больше {MAX_SPECS} характеристик, лишние отброшены.")
    return specs, empty


def parse_product(raw: str | bytes | dict) -> Product:
    if isinstance(raw, (str, bytes)):
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        if not text.strip():
            raise InputError("Пустой запрос: передайте JSON с данными о товаре.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(
                f"Некорректный JSON: {exc.msg} (строка {exc.lineno}, позиция {exc.colno})."
            ) from None
    else:
        data = raw

    if not isinstance(data, dict):
        raise InputError("Ожидается JSON-объект с полями name, manufacturer, specs, description.")

    issues: list[str] = []
    name = _clean(data.get("name"))
    if not name:
        raise InputError("Не указано название товара (поле name): без него карточку не построить.")
    if len(name) > MAX_NAME:
        raise InputError(f"Название длиннее {MAX_NAME} символов — проверьте поле name.")

    manufacturer = _clean(data.get("manufacturer"))
    if not manufacturer:
        issues.append("Не указан производитель (manufacturer).")

    specs, empty = _parse_specs(data.get("specs"), issues)
    if not specs:
        issues.append("Нет ни одной заполненной характеристики (specs).")
    for key in empty:
        issues.append(f"Характеристика «{key}» передана без значения.")

    description = _clean(data.get("description"))
    if not description:
        issues.append("Нет описания или фрагмента паспорта (description): текст будет опираться только на характеристики.")
    elif len(description) > MAX_DESCRIPTION:
        description = description[:MAX_DESCRIPTION]
        issues.append(f"Описание обрезано до {MAX_DESCRIPTION} символов.")

    extra = {k: data[k] for k in ("sku", "source_url", "category") if data.get(k)}
    device_type = detect_type(name, _clean(data.get("category")))
    empty_lower = {k.lower() for k in empty}
    missing = [m for m in missing_recommended(device_type, specs) if m.lower() not in empty_lower]
    for item in missing:
        issues.append(f"Для типа «{device_type}» обычно указывают «{item}» — данных нет.")

    return Product(
        name=name,
        manufacturer=manufacturer,
        specs=specs,
        description=description,
        device_type=device_type,
        extra=extra,
        issues=issues,
        missing_specs=missing,
        empty_specs=empty,
    )
