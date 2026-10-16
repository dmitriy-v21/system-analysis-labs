import csv
import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "products.yaml"
PRICES_FILE = BASE_DIR / "prices.csv"
BASKET_FILE = BASE_DIR / "basket_summary.csv"

# Федеральный МРОТ на 2026 год
MROT = 27093.00
QUARTER_MROT = MROT / 4
MIN_BASKET = QUARTER_MROT * 0.95
MAX_BASKET = QUARTER_MROT * 1.05

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

PRICE_COLUMNS = [
    "Дата",
    "Магазин",
    "Город",
    "Категория",
    "Группа",
    "Название товара",
    "Цена, руб.",
    "Объем, л",
    "Цена за литр, руб.",
    "Ссылка",
]

BASKET_COLUMNS = [
    "Дата",
    "Магазин",
    "Город",
    "МРОТ, руб.",
    "Четверть МРОТ, руб.",
    "Минимум корзины, руб.",
    "Максимум корзины, руб.",
    "Количество товаров, шт.",
    "Стоимость корзины, руб.",
    "Результат проверки",
]


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    config.setdefault("store", "Магнит")
    config.setdefault("city", "Общий каталог")
    config.setdefault("category", "Напитки")
    config.setdefault("catalogs", [])
    config.setdefault("products", [])
    return config


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=40)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def parse_price(text):
    if not text:
        return None

    text = text.replace("\xa0", " ").replace(" ", " ")
    match = re.search(
        r"(?<!\d)(\d{1,6}(?:[ .,]\d{1,2})?)\s*(?:₽|руб\.?)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    value = match.group(1).replace(" ", "").replace(",", ".")
    value = value.replace(".", ".", 1)

    try:
        return round(float(value), 2)
    except ValueError:
        return None


def parse_volume(text):
    """Возвращает объём жидкости в литрах, если он явно указан."""
    if not text:
        return None

    text = text.lower().replace("\xa0", " ").replace("\u202f", " ")
    text = text.replace(",", ".")

    # Не считаем граммы/килограммы/пакетики объёмом напитка.
    patterns = (
        r"(?<![\d.,])(\d+(?:\.\d+)?)\s*(?:мл|миллилитр(?:а|ов)?)(?![а-я])",
        r"(?<![\d.,])(\d+(?:\.\d+)?)\s*(?:л|литр(?:а|ов)?)(?![а-я])",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if index == 0:
                value /= 1000
            if 0 < value <= 30:
                return round(value, 3)
    return None


def is_ready_beverage(name):
    """Отсекает сухие продукты и оставляет готовые напитки."""
    name = re.sub(r"\s+", " ", name.lower()).strip()

    # Сухие продукты/заварка/добавки — не готовые напитки.
    excluded = (
        r"\bкофе\b", r"\bцикор\w*", r"какао[- ]порош",
        r"\bшоколад\b", r"\bпакетик\w*", r"\bпакет\s*\d*\b",
        r"\b\d+\s*пак\b", r"\b\d+\s*пакет\w*",
        r"\bзавар\w*", r"\bрастворим\w*\s+(?:кофе|какао|цикор)",
        r"\bсублимир\w*", r"\bгранул\w*", r"\bпорош\w*",
        r"\bсух\w*\s+(?:смесь|напиток)", r"добавк\w*\s+в\s+чай",
        r"\bсироп\b", r"\bконцентрат\b",
    )
    if any(re.search(pattern, name) for pattern in excluded):
        return False

    # Чай оставляем только как готовый напиток с явно указанным объёмом.
    # Сухая заварка и чайные пакетики без объёма не проходят.
    if "чай" in name and not any(
        marker in name for marker in ("холодный чай", "ice tea", "iced tea")
    ):
        if parse_volume(name) is None or "напиток" not in name:
            return False

    # Готовые питьевые категории. Для прочих названий не гадаем.
    included = (
        "вода", "сок", "нектар", "морс", "лимонад", "газированн",
        "холодный чай", "ice tea", "iced tea", "компот", "квас",
        "энергетик", "изотоник", "напиток", "кола", "тоник",
        "тархун", "фруктов", "питьев",
    )
    return any(word in name for word in included)

def product_group_matches(name, group):
    name_lower = name.lower()
    if not is_ready_beverage(name_lower):
        return False

    group_lower = group.lower()
    if group_lower == "вода":
        return "вод" in name_lower
    if group_lower == "соки":
        return any(word in name_lower for word in ("сок", "нектар", "морс"))
    if "лимонад" in group_lower or "газиров" in group_lower:
        return any(word in name_lower for word in ("лимонад", "газирован", "кола", "тоник", "тархун"))
    if "холодный чай" in group_lower:
        return any(word in name_lower for word in ("холодный чай", "ice tea", "iced tea"))
    return True


def make_id(url):
    return "item_" + hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:12]


def extract_candidate(element, catalog_url, group):
    """
    Ищет название, цену и ссылку в блоке страницы.
    Не зависит от одного конкретного CSS-класса.
    """
    text = element.get_text(" ", strip=True)

    if not text or len(text) > 1000:
        return None

    price = parse_price(text)

    if price is None or price <= 0:
        return None

    links = element.select("a[href]")
    if not links:
        return None

    product_link = None
    for link in links:
        href = link.get("href", "").strip()
        if href and "/catalog/" not in href:
            product_link = urljoin(catalog_url, href)
            break

    if not product_link:
        # Ссылка может быть на карточку товара с другой структурой.
        href = links[0].get("href", "").strip()
        if not href:
            return None
        product_link = urljoin(catalog_url, href)

    # Находим в блоке название, исключая текст с ценой и кнопками.
    name = ""

    for link in links:
        link_text = link.get_text(" ", strip=True)
        if (
            len(link_text) >= 5
            and not re.search(r"в корзину|₽|руб", link_text, re.I)
        ):
            name = link_text
            break

    if not name:
        # Пробуем взять текст до первого обозначения цены.
        name = re.split(r"\d[\d ,.]*\s*(?:₽|руб\.?)", text, maxsplit=1)[-1]
        name = name.strip(" -–—|")

    if not name or len(name) < 5:
        return None

    if not product_group_matches(name, group):
        return None

    # Берём объём прежде всего из названия, а затем из текста карточки.
    volume = parse_volume(name) or parse_volume(text)
    # В таблицу попадают только готовые напитки с определённым объёмом:
    # так не будет NULL в объёме и цене за литр.
    if volume is None:
        return None

    price_per_liter = round(price / volume, 2)

    return {
        "id": make_id(product_link),
        "store": "Магнит",
        "city": "Общий каталог",
        "category": "Напитки",
        "group": group,
        "name": name[:200],
        "price": price,
        "volume": volume,
        "price_per_liter": price_per_liter,
        "url": product_link,
    }


def discover_catalog(catalog):
    group = catalog["group"]
    url = catalog["url"]

    print(f"\nЗагружаю группу: {group}")
    print(f"Страница: {url}")

    soup = get_soup(url)

    # Считаем возможные карточки по разным признакам разметки.
    possible_cards = soup.select(
        '[class*="product"], '
        '[class*="Product"], '
        '[data-testid*="product"], '
        'article'
    )

    print(f"Найдено возможных блоков страницы: {len(possible_cards)}")

    found = {}

    # Сначала проверяем блоки, похожие на карточки товаров.
    for element in possible_cards:
        item = extract_candidate(element, url, group)

        if item:
            found[item["url"]] = item

    # Дополнительный проход: ищем небольшие контейнеры,
    # содержащие цену и ссылку на товар.
    for link in soup.select("a[href]"):
        parent = link

        for _ in range(6):
            if parent is None:
                break

            item = extract_candidate(parent, url, group)

            if item:
                found[item["url"]] = item
                break

            parent = parent.parent

    products = list(found.values())

    print(f"Товаров с распознанной ценой: {len(products)}")
    return products


def read_csv(path, columns):
    """
    Читает CSV с разделителем ; или , и определяет его по заголовку.
    и переводит старые английские заголовки в русские.
    """
    if not path.exists():
        return []

    translations = {
        "date": "Дата",
        "store": "Магазин",
        "city": "Город",
        "category": "Категория",
        "group": "Группа",
        "name": "Название товара",
        "price": "Цена, руб.",
        "volume_l": "Объем, л",
        "price_per_liter": "Цена за литр, руб.",
        "url": "Ссылка",
        "mrot": "МРОТ, руб.",
        "quarter_mrot": "Четверть МРОТ, руб.",
        "minimum": "Минимум корзины, руб.",
        "maximum": "Максимум корзины, руб.",
        "item_count": "Количество товаров, шт.",
        "basket_total": "Стоимость корзины, руб.",
        "status": "Результат проверки",
    }

    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        # Выбираем разделитель по тому, сколько ожидаемых заголовков
        # совпадает с первой строкой. Это надёжнее Sniffer, потому что
        # сами заголовки содержат запятые: «Цена, руб.», «Объем, л».
        sample = file.read(8192)
        file.seek(0)
        first_line = file.readline()

        def header_matches(delimiter):
            parsed = next(csv.reader([first_line], delimiter=delimiter), [])
            return sum(
                1 for name in parsed
                if name.strip() in columns or name.strip() in translations
            )

        delimiter = ";" if header_matches(";") >= header_matches(",") else ","
        file.seek(0)
        reader = csv.DictReader(file, delimiter=delimiter)
        rows = []

        for row in reader:
            converted = {}

            for key, value in row.items():
                if key is None:
                    continue
                converted[translations.get(key, key)] = value

            # Пропускаем явно поврежденные старые строки.
            if not any(converted.get(column) for column in columns):
                continue

            rows.append({
                column: converted.get(column, "")
                for column in columns
            })

    return rows


def write_csv(path, columns, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=columns,
            delimiter=",",
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def save_price_history(products, config):
    today = date.today().isoformat()
    rows = read_csv(PRICES_FILE, PRICE_COLUMNS)
    # Удаляем из старой истории сухие продукты и строки без объёма.
    rows = [
        row for row in rows
        if is_ready_beverage(row.get("Название товара", ""))
        and row.get("Объем, л")
        and row.get("Цена за литр, руб.")
    ]

    existing = {
        (row.get("Дата"), row.get("Город"), row.get("Ссылка"))
        for row in rows
    }

    added = 0

    for product in products:
        key = (today, config["city"], product["url"])

        if key in existing:
            continue

        rows.append({
            "Дата": today,
            "Магазин": config["store"],
            "Город": config["city"],
            "Категория": config["category"],
            "Группа": product["group"],
            "Название товара": product["name"],
            "Цена, руб.": f'{product["price"]:.2f}',
            "Объем, л": (
                f'{product["volume"]:.3f}'
                if product["volume"] is not None
                else ""
            ),
            "Цена за литр, руб.": (
                f'{product["price_per_liter"]:.2f}'
                if product["price_per_liter"] is not None
                else ""
            ),
            "Ссылка": product["url"],
        })

        existing.add(key)
        added += 1

    write_csv(PRICES_FILE, PRICE_COLUMNS, rows)
    print(f"Новых записей цен добавлено: {added}")
    print(f"Всего строк истории цен: {len(rows)}")


def build_basket(products):
    """
    Сначала добавляет разные товары по одной штуке.
    Если сумма меньше минимальной, добавляет товары
    по очереди, не выходя за верхний предел.
    """
    available = [
        product for product in products
        if product["price"] > 0
    ]

    available.sort(key=lambda item: item["price"])

    basket = []
    counts = {}
    total = 0.0

    # Сначала стараемся включить разные товары.
    for product in available:
        if total + product["price"] <= MAX_BASKET:
            basket.append(product)
            counts[product["id"]] = 1
            total = round(total + product["price"], 2)

    # Затем добавляем повторные позиции, начиная с дешевых.
    # Повторные позиции используются только если необходимо.
    if available:
        index = 0
        attempts = 0

        while total < MIN_BASKET and attempts < 20000:
            product = available[index % len(available)]
            attempts += 1
            index += 1

            if total + product["price"] > MAX_BASKET:
                continue

            counts[product["id"]] = (
                counts.get(product["id"], 0) + 1
            )
            total = round(total + product["price"], 2)

    details = []

    for product in available:
        quantity = counts.get(product["id"], 0)

        if quantity > 0:
            details.append({
                "name": product["name"],
                "group": product["group"],
                "price": product["price"],
                "quantity": quantity,
                "subtotal": round(
                    product["price"] * quantity,
                    2,
                ),
            })

    return details, round(total, 2)


def save_basket_summary(config, basket, total):
    today = date.today().isoformat()
    rows = read_csv(BASKET_FILE, BASKET_COLUMNS)

    rows = [
        row for row in rows
        if not (
            row.get("Дата") == today
            and row.get("Магазин") == config["store"]
            and row.get("Город") == config["city"]
        )
    ]

    status = (
        "Соответствует условию"
        if MIN_BASKET <= total <= MAX_BASKET
        else "Не соответствует условию"
    )

    rows.append({
        "Дата": today,
        "Магазин": config["store"],
        "Город": config["city"],
        "МРОТ, руб.": f"{MROT:.2f}",
        "Четверть МРОТ, руб.": f"{QUARTER_MROT:.2f}",
        "Минимум корзины, руб.": f"{MIN_BASKET:.2f}",
        "Максимум корзины, руб.": f"{MAX_BASKET:.2f}",
        "Количество товаров, шт.": sum(
            item["quantity"] for item in basket
        ),
        "Стоимость корзины, руб.": f"{total:.2f}",
        "Результат проверки": status,
    })

    write_csv(BASKET_FILE, BASKET_COLUMNS, rows)

    print("\n--- Проверка корзины ---")
    print(f"МРОТ: {MROT:.2f} руб.")
    print(f"1/4 МРОТ: {QUARTER_MROT:.2f} руб.")
    print(f"Допустимый диапазон: {MIN_BASKET:.2f}–{MAX_BASKET:.2f} руб.")
    print(f"Стоимость корзины: {total:.2f} руб.")
    print(f"Результат: {status}")

    print("\nСостав корзины:")
    for item in basket:
        print(
            f'{item["group"]}: {item["name"]} | '
            f'{item["quantity"]} шт. × {item["price"]:.2f} руб. '
            f'= {item["subtotal"]:.2f} руб.'
        )


def update_products_yaml(config, products):
    old_products = config.get("products", [])
    by_url = {}

    for item in old_products:
        if (
            isinstance(item, dict)
            and item.get("url")
            and is_ready_beverage(item.get("name", ""))
            and parse_volume(item.get("name", "")) is not None
        ):
            by_url[item["url"]] = item

    for item in products:
        by_url[item["url"]] = {
            "id": item["id"],
            "group": item["group"],
            "name": item["name"],
            "url": item["url"],
        }

    config["products"] = list(by_url.values())

    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        yaml.safe_dump(
            config,
            file,
            allow_unicode=True,
            sort_keys=False,
        )


def main():
    print("Сбор цен в магазине «Магнит»")
    print(f"Дата сбора: {datetime.now():%d.%m.%Y %H:%M}")

    config = load_config()
    all_products = []

    for catalog in config["catalogs"]:
        try:
            products = discover_catalog(catalog)
            all_products.extend(products)
        except requests.RequestException as error:
            print(f"Ошибка загрузки {catalog['group']}: {error}")

    # Убираем дубликаты ссылок.
    unique = {}

    for product in all_products:
        unique[product["url"]] = product

    all_products = list(unique.values())

    water_count = sum(
        1 for item in all_products if item["group"] == "Вода"
    )
    juice_count = sum(
        1 for item in all_products if item["group"] == "Соки"
    )

    print("\n--- Итог сбора ---")
    print(f"Уникальных товаров всего: {len(all_products)}")
    print(f"Вода: {water_count}")
    print(f"Соки и морсы: {juice_count}")

    if not all_products:
        print(
            "\nТовары не распознаны. CSV не обновлялся новыми товарами. "
            "Возможно, сайт использует динамическую загрузку."
        )
        return

    update_products_yaml(config, all_products)
    save_price_history(all_products, config)

    print("\n--- Первые 20 найденных товаров ---")
    for item in all_products[:20]:
        unit = (
            f'{item["price_per_liter"]:.2f} руб./л'
            if item["price_per_liter"] is not None
            else "объем не определен"
        )

        print(
            f'[{item["group"]}] {item["name"]} — '
            f'{item["price"]:.2f} руб. ({unit})'
        )

    basket, total = build_basket(all_products)
    save_basket_summary(config, basket, total)

    print("\nГотово.")
    print(f"История цен: {PRICES_FILE}")
    print(f"Сводка корзины: {BASKET_FILE}")
    print(f"Список товаров: {CONFIG_FILE}")


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        yaml.YAMLError,
    ) as error:
        print(f"Ошибка программы: {error}")
