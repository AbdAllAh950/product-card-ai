import json

import pytest

from app.input_check import InputError, parse_product


def test_invalid_json_gives_readable_error():
    with pytest.raises(InputError, match="Некорректный JSON"):
        parse_product('{"name": "Манометр", }')


@pytest.mark.parametrize("raw", ["[]", '"текст"', "42"])
def test_root_must_be_object(raw):
    with pytest.raises(InputError, match="JSON-объект"):
        parse_product(raw)


def test_empty_body():
    with pytest.raises(InputError, match="Пустой запрос"):
        parse_product("   ")


def test_name_is_required():
    with pytest.raises(InputError, match="name"):
        parse_product({"manufacturer": "ОВЕН", "specs": {"а": "б"}})


def test_specs_must_be_object_or_list():
    with pytest.raises(InputError, match="specs"):
        parse_product({"name": "Манометр", "specs": "0–1 МПа"})


def test_empty_fields_are_reported_not_invented():
    product = parse_product({"name": "Манометр ТМ-210Р", "specs": {"диапазон измерений": "0–2,5 МПа", "класс точности": ""}})
    joined = " ".join(product.issues)
    assert "производитель" in joined
    assert "описания" in joined
    assert "«класс точности» передана без значения" in joined
    assert product.empty_specs == ["класс точности"]
    assert "Класс точности" not in product.missing_specs  # не дублируем пустое поле
    assert "Диаметр корпуса" in product.missing_specs
    assert product.facts()["manufacturer"] == "данных нет"


def test_specs_as_list_and_non_string_values():
    product = parse_product(
        {"name": "Датчик давления ПД100", "manufacturer": "ОВЕН",
         "specs": [{"name": "верхний предел измерений", "value": 1.6}, {"name": "IP", "value": "IP65"}, "мусор"],
         "description": "Датчик."}
    )
    assert product.specs["верхний предел измерений"] == "1.6"
    assert product.device_type == "датчик давления"
    assert any("пропущен" in i for i in product.issues)


def test_examples_are_complete(manometer, sensor):
    assert manometer.device_type == "манометр" and manometer.missing_specs == []
    assert sensor.device_type == "датчик давления" and sensor.missing_specs == []


def test_long_description_is_trimmed():
    product = parse_product(json.dumps({"name": "Манометр", "manufacturer": "РОСМА", "specs": {"а": "1"}, "description": "а" * 9000}))
    assert len(product.description) == 8000
    assert any("обрезано" in i for i in product.issues)
