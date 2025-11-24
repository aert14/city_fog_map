#!/usr/bin/env python3
"""Script to translate Moscow district names from Russian to English."""

import re

# Dictionary of district name translations
translations = {
    # Format: Russian name -> English name
    "Академический район": "Akademichesky District",
    "Алексеевский район": "Alekseyevsky District",
    "Алтуфьевский район": "Altufyevsky District",
    "Бабушкинский район": "Babushkinsky District",
    "Басманный район": "Basmanny District",
    "Бескудниковский район": "Beskuydnikovsky District",
    "Бутырский район": "Butyrsky District",
    "Войковский район": "Voykovsky District",
    "Гагаринский район": "Gagarinsky District",
    "Головинский район": "Golovinsky District",
    "Даниловский район": "Danilovsky District",
    "Дмитровский район": "Dmitrovsky District",
    "Донской район": "Donskoy District",
    "Красносельский район": "Krasnoselsky District",
    "Ломоносовский район": "Lomonosovsky District",
    "Лосиноостровский район": "Losinoostrovsky District",
    "Мещанский район": "Meshchansky District",
    "Можайский район": "Mozhaysky District",
    "Молжаниновский район": "Molzhaninovsky District",
    "Нагорный район": "Nagorny District",
    "Нижегородский район": "Nizhegorodsky District",
    "Обручевский район": "Obruchersky District",
    "Останкинский район": "Ostankinsky District",
    "Пресненский район": "Presnensky District",
    "Рязанский район": "Ryazansky District",
    "Савёловский район": "Savyolovsky District",
    "Таганский район": "Tagansky District",
    "Тверской район": "Tverskoy District",
    "Тимирязевский район": "Timiryazevsky District",
    "Хорошёвский район": "Khoroshevsky District",
    "Южнопортовый район": "Yuzhnoportovy District",
    "Ярославский район": "Yaroslavsky District",
    "район Арбат": "Arbat District",
    "район Аэропорт": "Aeroport District",
    "район Беговой": "Begovoy District",
    "район Бибирево": "Bibirevo District",
    "район Бирюлёво Восточное": "Biryulyovo Vostochnoye District",
    "район Бирюлёво Западное": "Biryulyovo Zapadnoye District",
    "район Богородское": "Bogorodskoye District",
    "район Братеево": "Brateyevo District",
    "район Вешняки": "Veshnyaki District",
    "район Восточное Дегунино": "Vostochnoye Degunino District",
    "район Восточное Измайлово": "Vostochnoye Izmaylovo District",
    "район Восточный": "Vostochny District",
    "район Выхино-Жулебино": "Vykhino-Zhulebino District",
    "район Гольяново": "Golyanovo District",
    "район Дорогомилово": "Dorogomilovo District",
    "район Замоскворечье": "Zamoskvorechye District",
    "район Западное Дегунино": "Zapadnoye Degunino District",
    "район Зюзино": "Zyuzino District",
    "район Зябликово": "Zyablikovo District",
    "район Ивановское": "Ivanovskoye District",
    "район Измайлово": "Izmaylovo District",
    "район Капотня": "Kapotnya District",
    "район Коньково": "Konkovo District",
    "район Коптево": "Koptevo District",
    "район Косино-Ухтомский": "Kosino-Ukhtomsky District",
    "район Котловка": "Kotlovka District",
    "район Крылатское": "Krylat-skoye District",
    "район Крюково": "Kryukovo District",
    "район Кузьминки": "Kuzminki District",
    "район Кунцево": "Kuntsevo District",
    "район Куркино": "Kurki District",
    "район Левобережный": "Levoberezhny District",
    "район Лефортово": "Lefortovo District",
    "район Лианозово": "Lianozovo District",
    "район Люблино": "Lyublino District",
    "район Марфино": "Marfino District",
    "район Марьина Роща": "Maryina Roshcha District",
    "район Марьино": "Maryino District",
    "район Матушкино": "Matushkino District",
    "район Метрогородок": "Metrogorodok District",
    "район Митино": "Mitino District",
    "район Москворечье-Сабурово": "Moskvorechye-Saburovo District",
    "район Нагатино-Садовники": "Nagatino-Sadovniki District",
    "район Нагатинский Затон": "Nagatinsky Zaton District",
    "район Некрасовка": "Nekrasovka District",
    "район Новогиреево": "Novogireyevo District",
    "район Новокосино": "Novokosino District",
    "район Орехово-Борисово Северное": "Orekhovo-Borisovo Severnoye District",
    "район Орехово-Борисово Южное": "Orekhovo-Borisovo Yuzhnoye District",
    "район Отрадное": "Otradnoye District",
    "район Очаково-Матвеевское": "Ochakovo-Matveyevskoye District",
    "район Перово": "Perovo District",
    "район Печатники": "Pechatniki District",
    "район Покровское-Стрешнево": "Pokrovskoye-Streshnevo District",
    "район Преображенское": "Preobrazhenskoye District",
    "район Проспект Вернадского": "Prospect Vernadskogo District",
    "район Раменки": "Ramenki District",
    "район Ростокино": "Rostokino District",
    "район Савёлки": "Savёlki District",
    "район Свиблово": "Sviblovo District",
    "район Северное Бутово": "Severnoye Butovo District",
    "район Северное Измайлово": "Severnoye Izmaylovo District",
    "район Северное Медведково": "Severnoye Medvedkovo District",
    "район Северное Тушино": "Severnoye Tushino District",
    "район Северный": "Severny District",
    "район Силино": "Silino District",
    "район Сокол": "Sokol District",
    "район Соколиная Гора": "Sokol'naya Gora District",
    "район Сокольники": "Sokolniki District",
    "район Старое Крюково": "Staroye Kryukovo District",
    "район Строгино": "Strogino District",
    "район Текстильщики": "Tekstilshchiki District",
    "район Тёплый Стан": "Tёply Stan District",
    "район Фили-Давыдково": "Fili-Davydkovo District",
    "район Филёвский Парк": "Filevsky Park District",
    "район Хамовники": "Khamovniki District",
    "район Ховрино": "Khovrino District",
    "район Хорошёво-Мнёвники": "Khoroshyovo-Mnyovniki District",
    "район Царицыно": "Tsaritsyno District",
    "район Чертаново Южное": "Chertanovo Yuzhnoye District",
    "район Черёмушки": "Chёrmushki District",
    "район Щукино": "Shchukino District",
    "район Южное Бутово": "Yuzhnoye Butovo District",
    "район Южное Медведково": "Yuzhnoye Medvedkovo District",
    "район Южное Тушино": "Yuzhnoye Tushino District",
    "район Якиманка": "Yakimanka District",
    "район Ясенево": "Yasenovo District",
}

def main():
    """Apply translations to the GeoJSON file."""
    with open('data/moscow_districts.geojson', 'r', encoding='utf-8') as f:
        content = f.read()

    for russian, english in translations.items():
        # Escape quotes for regex
        russian_escaped = russian.replace('"', '\\"')
        english_escaped = english.replace('"', '\\"')

        # Replace in the JSON
        content = content.replace(f'"name_ru": "{russian}"', f'"name_ru": "{english}"')

    with open('data/moscow_districts.geojson', 'w', encoding='utf-8') as f:
        f.write(content)

    print("Translation completed!")

if __name__ == "__main__":
    main()
