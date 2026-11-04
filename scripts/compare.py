"""Сравнение карточек двух моделей: python scripts/compare.py examples/output examples/output-local"""

import json
import sys
from pathlib import Path


def _timings(folder: Path) -> dict[str, str]:
    path = folder / "timings.tsv"
    if not path.exists():
        return {}
    rows = (line.split("\t") for line in path.read_text(encoding="utf-8").splitlines() if "\t" in line)
    return {name: seconds for name, seconds in rows}


def _row(name: str, folder: Path, times: dict[str, str]) -> str:
    path = folder / f"{name}.json"
    if not path.exists():
        return f"| {name} | — | нет карточки (ошибка генерации) | | | | |"
    card = json.loads(path.read_text(encoding="utf-8"))
    meta = card.get("meta", {})
    model = str(meta.get("model", "?")).replace("gpt://<folder_id>/", "")
    return (f"| {name} | {model} | {meta.get('attempts', '?')} | {meta.get('title_length', '?')} "
            f"| {meta.get('description_length', '?')} | {len(card.get('warnings', []))} | {times.get(name, '—')} |")


def main(base: str, other: str) -> int:
    a, b = Path(base), Path(other)
    names = sorted({p.stem for p in a.glob("*.json")} | {p.stem for p in b.glob("*.json")})
    ta, tb = _timings(a), _timings(b)
    print("| Товар | Модель | Попыток | Заголовок, симв. | Описание, симв. | Замечаний | Время, с |")
    print("|---|---|---|---|---|---|---|")
    for name in names:
        print(_row(name, a, ta))
        print(_row(name, b, tb))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
