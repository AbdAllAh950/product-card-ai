"""Представление карточки в Markdown — удобно копировать в CMS или документ."""

from .generator import Card
from .prompts import DESCRIPTION_MAX, DESCRIPTION_MIN, TITLE_MAX


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|")


def to_markdown(card: Card) -> str:
    meta = card.meta
    lines = [
        f"# {card.name}",
        "",
        f"_Черновик карточки товара · модель: {meta.get('model', '—')} · "
        f"проверка фактов: {meta.get('fact_check', '—')}_",
    ]
    if meta.get("source_url"):
        lines.append(f"_Источник данных: {meta['source_url']}_")

    lines += [
        "",
        f"## SEO-заголовок ({len(card.seo_title)}/{TITLE_MAX})",
        "",
        card.seo_title,
        "",
        f"## SEO-описание ({len(card.seo_description)} символов, норма {DESCRIPTION_MIN}–{DESCRIPTION_MAX})",
        "",
        card.seo_description,
        "",
        "## Ключевые характеристики",
        "",
        "| Характеристика | Значение |",
        "|---|---|",
    ]
    lines += [f"| {_cell(row['name'])} | {_cell(row['value'])} |" for row in card.characteristics]

    lines += ["", "## Поисковые запросы", ""]
    lines += [f"- {q}" for q in card.search_queries] or ["- данных нет"]

    commercial = card.commercial
    lines += ["", "## Для менеджера", "", f"**Кому подходит:** {commercial.get('for_whom', '')}", ""]
    if commercial.get("tasks"):
        lines += ["**Какие задачи решает:**", ""] + [f"- {t}" for t in commercial["tasks"]] + [""]
    if commercial.get("selling_points"):
        lines += ["**Аргументы для клиента:**", ""] + [f"- {t}" for t in commercial["selling_points"]] + [""]

    if card.needs_clarification:
        lines += ["## Требует уточнения", ""] + [f"- {t}" for t in card.needs_clarification] + [""]
    if card.warnings:
        lines += ["## Замечания автоматической проверки", ""] + [f"- {t}" for t in card.warnings] + [""]
    return "\n".join(lines).rstrip() + "\n"
