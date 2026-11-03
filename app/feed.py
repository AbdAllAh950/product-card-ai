"""Заготовка входного JSON из YML-фида магазина (формат Яндекс.Маркета).

    python -m app.feed feed.xml --offer-id 54079
    python -m app.feed feed.xml --search "ПД100-ДИ1,6-111-0,5"

В фиде souz-pribor.ru у товаров есть название, тип, производитель, артикул и ссылка,
но нет характеристик и описания. Заготовка честно оставляет specs и description пустыми —
сервис покажет, каких данных не хватает, а характеристики добавляются из паспорта или со страницы товара.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET

SKIP_PARAMS = {"Артикул", "Код товара"}


def offer_to_product(offer: ET.Element) -> dict:
    def text(tag: str) -> str:
        el = offer.find(tag)
        return " ".join((el.text or "").split()) if el is not None and el.text else ""

    params = {p.get("name", ""): " ".join((p.text or "").split()) for p in offer.findall("param")}
    type_prefix, name = text("typePrefix"), text("name")
    product = {
        "name": f"{type_prefix[:1].upper()}{type_prefix[1:]} {name}".strip() if type_prefix else name,
        "manufacturer": text("vendor"),
        "category": type_prefix,
        "sku": params.get("Код товара") or text("vendorCode"),
        "source_url": text("url"),
        "specs": {k: v for k, v in params.items() if k and k not in SKIP_PARAMS and v},
        "description": text("description"),
    }
    return {k: v for k, v in product.items() if v or k in ("specs", "description")}


def find_offer(source, offer_id: str | None = None, search: str | None = None) -> dict | None:
    needle = (search or "").lower()
    for _, el in ET.iterparse(source, events=("end",)):
        if el.tag != "offer":
            continue
        name_el = el.find("name")
        name = (name_el.text or "") if name_el is not None else ""
        if (offer_id and el.get("id") == offer_id) or (needle and needle in name.lower()):
            return offer_to_product(el)
        el.clear()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Заготовка JSON товара из YML-фида.")
    parser.add_argument("feed", help="путь к feed.xml")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--offer-id")
    group.add_argument("--search", help="часть названия товара")
    args = parser.parse_args(argv)
    try:
        product = find_offer(args.feed, args.offer_id, args.search)
    except (OSError, ET.ParseError) as exc:
        print(f"Не удалось прочитать фид: {exc}", file=sys.stderr)
        return 2
    if not product:
        print("Товар не найден в фиде.", file=sys.stderr)
        return 1
    print(json.dumps(product, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
