import io
import json

from fastapi.testclient import TestClient

from app import web
from app.feed import find_offer
from app.input_check import parse_product

from .conftest import EXAMPLES, FakeClient, good_reply

MANOMETER_JSON = (EXAMPLES / "tm-510r-m2.json").read_text(encoding="utf-8")


def make_app():
    reply = good_reply(parse_product(MANOMETER_JSON))
    web.app.state.client_factory = lambda: FakeClient(reply)
    return TestClient(web.app)


def test_api_returns_card_and_markdown():
    resp = make_app().post("/api/card", content=MANOMETER_JSON.encode("utf-8"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["card"]["seo_title"].startswith("Манометр")
    assert "## SEO-описание" in body["markdown"]


def test_api_invalid_json_is_400():
    resp = make_app().post("/api/card", content=b'{"name": ')
    assert resp.status_code == 400
    assert "Некорректный JSON" in resp.json()["error"]


def test_form_fields_without_json():
    resp = make_app().post("/card", data={
        "raw_json": "", "name": "Манометр ТМ-510Р.00 (0–10 кгс/см2) G1/2 М2", "manufacturer": "РОСМА",
        "specs": "класс точности: 1,5\nдиаметр корпуса: 100 мм", "description": "",
    })
    assert resp.status_code == 200
    assert "Скопировать" in resp.text or "Копировать" in resp.text


def test_form_shows_input_error():
    resp = make_app().post("/card", data={"raw_json": "[1, 2]"})
    assert "Ошибка входных данных" in resp.text


def test_index_page():
    resp = make_app().get("/")
    assert resp.status_code == 200 and "Сформировать карточку" in resp.text


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<yml_catalog><shop><offers>
<offer id="1"><name>АНТ-1</name><typePrefix>антенна</typePrefix><vendor>ОВЕН</vendor><url>u1</url></offer>
<offer id="65316"><name>ПД100-ДИ1,6-111-0,5</name><typePrefix>датчик давления общепромышленный</typePrefix>
<url>https://www.souz-pribor.ru/product/pd100/</url><vendor>ОВЕН</vendor><vendorCode>123</vendorCode>
<param name="Артикул">123</param><param name="Код товара">65316</param></offer>
</offers></shop></yml_catalog>"""


def test_feed_offer_to_input_skeleton():
    product = find_offer(io.BytesIO(FEED.encode("utf-8")), search="пд100-ди1,6")
    assert product["name"] == "Датчик давления общепромышленный ПД100-ДИ1,6-111-0,5"
    assert product["manufacturer"] == "ОВЕН" and product["sku"] == "65316"
    assert product["specs"] == {} and product["description"] == ""
    parsed = parse_product(json.dumps(product, ensure_ascii=False))
    assert parsed.device_type == "датчик давления"
    assert "Выходной сигнал" in parsed.missing_specs
