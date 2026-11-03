from app.grounding import invented_numbers, invented_terms, numbers_in

SOURCE = "Класс точности 1,5. Диапазон 0–10 кгс/см2. Наработка не менее 500 000 ч. Корпус IP40. Межповерочный интервал 2 года."


def test_number_normalization():
    assert numbers_in("1,5 и 1.50 и 010") == {"1.5", "10"}
    assert numbers_in("500 000 ч") == {"500000"}


def test_same_values_in_other_notation_are_allowed():
    assert invented_numbers("класс 1.5, до 500000 ч, шкала 0-10", SOURCE) == []


def test_unit_conversion_is_caught():
    # 10 кгс/см2 ≈ 0,98 МПа — пересчёт = новое число, которого нет в данных
    assert invented_numbers("диапазон до 0,98 МПа", SOURCE) == ["0.98"]


def test_invented_values_are_caught():
    assert invented_numbers("гарантия 3 года, температура до 120 °C", SOURCE) == ["3", "120"]


def test_risky_terms():
    assert invented_terms("Соответствует ГОСТ 2405-88, внесён в Госреестр", SOURCE) == ["ГОСТ", "Госреестр СИ"]
    assert invented_terms("Корпус со степенью защиты IP40, межповерочный интервал 2 года", SOURCE) == []
    assert invented_terms("Подходит для агрессивных сред и кислорода", SOURCE) == [
        "агрессивные среды", "кислород / кислородное исполнение"]
