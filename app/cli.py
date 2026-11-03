"""Командная строка.

    python -m app.cli examples/input/tm-510r-m2.json                 # Markdown в консоль
    python -m app.cli examples/input/*.json --out examples/output    # .md и .json для каждого товара
    python -m app.cli product.json --format json
"""

import argparse
import json
import sys
from pathlib import Path

from .generator import GenerationError
from .input_check import InputError
from .llm import LLMError
from .render import to_markdown
from .service import card_from_json, make_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Черновик карточки товара КИП по JSON с данными о приборе.")
    parser.add_argument("inputs", nargs="+", help="JSON-файлы с данными о товарах")
    parser.add_argument("--format", choices=["md", "json"], default="md", help="формат вывода в консоль")
    parser.add_argument("--out", help="папка, куда сохранить <имя>.md и <имя>.json")
    args = parser.parse_args(argv)

    client = None
    exit_code = 0
    for path in args.inputs:
        file = Path(path)
        try:
            raw = file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[{path}] не удалось прочитать файл: {exc}", file=sys.stderr)
            exit_code = 2
            continue
        try:
            client = client or make_client()
            card = card_from_json(raw, client)
        except InputError as exc:
            print(f"[{path}] ошибка входных данных: {exc}", file=sys.stderr)
            exit_code = 2
            continue
        except (LLMError, GenerationError) as exc:
            print(f"[{path}] ошибка модели: {exc}", file=sys.stderr)
            exit_code = 3
            continue

        markdown = to_markdown(card)
        data = json.dumps(card.to_dict(), ensure_ascii=False, indent=2)
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{file.stem}.md").write_text(markdown, encoding="utf-8")
            (out / f"{file.stem}.json").write_text(data + "\n", encoding="utf-8")
            print(f"[{path}] готово → {out / file.stem}.md, .json"
                  + (f" · замечаний: {len(card.warnings)}" if card.warnings else ""))
        else:
            print(markdown if args.format == "md" else data)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
