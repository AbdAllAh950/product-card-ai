import pytest

from app.generator import NO_APPLICATION, NO_DATA, GenerationError, generate_card, parse_json_reply
from app.input_check import parse_product
from app.render import to_markdown

from .conftest import FakeClient, good_reply


def test_happy_path_single_call(manometer):
    client = FakeClient(good_reply(manometer))
    card = generate_card(manometer, client)
    assert len(client.calls) == 1
    assert card.warnings == []
    assert card.meta["fact_check"] == "пройдена"
    assert card.characteristics[0] == {"name": "Производитель", "value": "РОСМА"}
    # таблица характеристик строится из входных данных, а не моделью
    assert {"name": "Класс точности", "value": "1,5"} in card.characteristics
    assert len(card.characteristics) == 1 + len(manometer.specs)
    md = to_markdown(card)
    assert "## Ключевые характеристики" in md and "| Диаметр корпуса | 100 мм |" in md


def test_prompt_contains_only_input_facts(manometer):
    client = FakeClient(good_reply(manometer))
    generate_card(manometer, client)
    system, user = client.calls[0][0]["content"], client.calls[0][1]["content"]
    assert "используй только факты" in system
    assert "0–10 кгс/см2" in user and "РОСМА" in user
    assert "source_url" not in user  # служебные поля в модель не уходят


def test_invented_number_triggers_repair(manometer):
    bad = good_reply(manometer, seo_title="Манометр ТМ-510Р М2 на 16 бар")
    client = FakeClient(bad, good_reply(manometer))
    card = generate_card(manometer, client)
    assert len(client.calls) == 2
    repair = client.calls[1][-1]["content"]
    assert "seo_title: числа 16" in repair
    assert card.warnings == [] and card.meta["attempts"] == 2


def test_persistent_invention_is_removed_with_warning(manometer):
    reply = good_reply(manometer)
    reply["seo_description"] += " Гарантия производителя 3 года."
    reply["seo_title"] = "Манометр ТМ-510Р М2 по ГОСТ"
    reply["search_queries"].append("манометр 16 бар")
    card = generate_card(manometer, FakeClient(reply))
    assert "Гарантия" not in card.seo_description
    assert card.seo_title == manometer.name
    assert "манометр 16 бар" not in card.search_queries
    assert len(card.warnings) == 3
    assert card.meta["fact_check"].startswith("есть замечания")


def test_bad_json_then_good(manometer):
    client = FakeClient("Конечно! Вот карточка: ...", good_reply(manometer))
    card = generate_card(manometer, client)
    assert "корректным JSON" in client.calls[1][-1]["content"]
    assert card.warnings == []


def test_never_json_raises(manometer):
    with pytest.raises(GenerationError):
        generate_card(manometer, FakeClient("не JSON"))


def test_json_in_code_fence_is_accepted():
    assert parse_json_reply('```json\n{"seo_title": "x"}\n```') == {"seo_title": "x"}


def test_long_title_is_cut_and_reported(manometer):
    long_title = "Манометр ТМ-510Р М2 РОСМА " + "класс точности 1,5 " * 5
    card = generate_card(manometer, FakeClient(good_reply(manometer, seo_title=long_title)))
    assert len(card.seo_title) <= 70
    assert any("обрезан" in w for w in card.warnings)


def test_queries_limited_to_five(manometer):
    queries = [f"манометр тм-510р м2 вариант {n}" for n in ("а", "б", "в", "г", "д", "е", "ж")]
    card = generate_card(manometer, FakeClient(good_reply(manometer, search_queries=queries)))
    assert len(card.search_queries) == 5


def test_incomplete_product_marks_missing_data():
    product = parse_product({"name": "Манометр ТМ-210Р.00(0-2,5МПа) M12x1,5.2,5.", "manufacturer": "РОСМА",
                             "specs": {"диапазон измерений": "0–2,5 МПа", "класс точности": ""}, "description": ""})
    reply = {
        "seo_title": "Манометр ТМ-210Р РОСМА 0–2,5 МПа",
        "seo_description": "Манометр ТМ-210Р производства РОСМА с диапазоном измерений 0–2,5 МПа.",
        "search_queries": ["манометр тм-210р", "манометр росма 2,5 мпа", "манометр тм-210р 0-2,5 мпа"],
        "commercial": {"for_whom": NO_APPLICATION, "tasks": ["Измерение давления в диапазоне 0–2,5 МПа"], "selling_points": []},
        "missing_or_unclear": ["Нет класса точности", "Нет описания"],
    }
    card = generate_card(product, FakeClient(reply))
    values = {row["name"]: row["value"] for row in card.characteristics}
    assert values["Класс точности"] == NO_DATA
    assert values["Диаметр корпуса"] == NO_DATA
    assert any("SEO-описание" in w and "вручную" in w for w in card.warnings)
    assert "Нет класса точности" in card.needs_clarification
    assert card.commercial["for_whom"] == NO_APPLICATION


def test_sensor_example_passes_with_grounded_reply(sensor):
    from .conftest import grounded_description

    reply = {
        "seo_title": "Датчик давления ОВЕН ПД100-ДИ1,6-111-0,5, 4…20 мА",
        "seo_description": grounded_description(sensor),
        "search_queries": ["датчик давления пд100", "овен пд100-ди1,6-111-0,5", "датчик давления 4-20 ма 1,6 мпа"],
        "commercial": {"for_whom": "Для гидро- и пневмосистем, систем водоподготовки и водоснабжения.",
                       "tasks": ["Передача давления в контроллер сигналом 4…20 мА"],
                       "selling_points": ["Погрешность 0,5 % ВПИ, корпус IP65"]},
        "missing_or_unclear": [],
    }
    card = generate_card(sensor, FakeClient(reply))
    assert card.warnings == []


def test_sparse_product_is_not_padded():
    product = parse_product({"name": "Манометр ТМ-210Р", "manufacturer": "РОСМА",
                             "specs": {"диапазон измерений": "0–2,5 МПа"}})
    assert product.is_sparse
    reply = {
        "seo_title": "манометр ТМ-210Р РОСМА 0–2,5 МПа",
        "seo_description": "Манометр ТМ-210Р производства РОСМА, диапазон измерений 0–2,5 МПа.",
        "search_queries": ["Манометр ТМ-210Р", "манометр росма", "манометр 0–2,5 мпа"],
        "commercial": {"for_whom": NO_APPLICATION, "tasks": ["Измерение давления 0–2,5 МПа"], "selling_points": []},
        "missing_or_unclear": ["диаметр корпуса", "Нет паспорта прибора"],
    }
    client = FakeClient(reply)
    card = generate_card(product, client)
    assert len(client.calls) == 1  # короткое описание не отправляется на «дописывание»
    assert "Данных о товаре мало" in client.calls[0][1]["content"]
    assert card.seo_title.startswith("Манометр")  # заглавная буква
    assert card.search_queries[0] == "манометр тм-210р"  # нижний регистр
    # «диаметр корпуса» уже есть в замечаниях по входным данным — не дублируется
    assert sum("иаметр корпуса" in item for item in card.needs_clarification) == 1
    assert "Нет паспорта прибора" in card.needs_clarification
    assert card.meta["checks_per_attempt"] == [[]]


def test_rich_product_short_description_is_repaired(manometer):
    short = good_reply(manometer, seo_description="Манометр ТМ-510Р М2 производства РОСМА.")
    client = FakeClient(short, good_reply(manometer))
    card = generate_card(manometer, client)
    assert "seo_description" in client.calls[1][-1]["content"]
    assert len(card.meta["checks_per_attempt"]) == 2 and card.meta["checks_per_attempt"][1] == []
