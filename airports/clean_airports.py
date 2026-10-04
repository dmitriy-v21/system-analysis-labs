
import csv
import json
import re
from pathlib import Path
from collections import Counter

BASE_DIR = Path(__file__).resolve().parent

INPUT_FILE = BASE_DIR / "airports.json"
OUTPUT_CSV = BASE_DIR / "airports_clean.csv"
OUTPUT_JSON = BASE_DIR / "airports_clean.json"

with open(INPUT_FILE, "r", encoding="utf-8") as file:
    airports = json.load(file)

clean_airports = []
removed = Counter()
seen_icao = set()

required_fields = [
    "Latitude", "Longitude", "Status",
    "Type", "Country", "Elevation", "Timezone"
]

for airport in airports:
    icao = airport.get("ICAO")

    # Проверяем наличие корректного ICAO
    if not isinstance(icao, str) or not re.fullmatch(r"[A-Z]{4}", icao):
        removed["ICAO"] += 1
        continue

    # Убираем повторяющиеся ICAO
    if icao in seen_icao:
        removed["Duplicate ICAO"] += 1
        continue

    # Проверяем обязательные поля
    missing_field = None

    for field in required_fields:
        value = airport.get(field)
        if value is None or value == "":
            missing_field = field
            break

    if missing_field:
        removed[missing_field] += 1
        continue

    # Проверяем статус
    if airport["Status"] not in ("open", "close"):
        removed["Invalid Status"] += 1
        continue

    # Проверяем координаты
    if not (-90 <= airport["Latitude"] <= 90):
        removed["Invalid Latitude"] += 1
        continue

    if not (-180 <= airport["Longitude"] <= 180):
        removed["Invalid Longitude"] += 1
        continue

    # Оставляем только полосы с полными данными
    valid_runways = []

    for runway in airport.get("Runways", []):
        if (
            isinstance(runway, dict)
            and runway.get("ID")
            and runway.get("Length") is not None
            and runway.get("Surface")
        ):
            valid_runways.append({
                "ID": runway["ID"],
                "Length": runway["Length"],
                "Surface": runway["Surface"]
            })

    cleaned = airport.copy()
    cleaned["Runways"] = valid_runways

    clean_airports.append(cleaned)
    seen_icao.add(icao)

# Сохраняем очищенный JSON
with open(OUTPUT_JSON, "w", encoding="utf-8") as file:
    json.dump(
        clean_airports,
        file,
        ensure_ascii=False,
        indent=4
    )

# Сохраняем очищенный CSV
fields = [
    "ICAO", "Latitude", "Longitude", "Status",
    "Type", "Country", "Elevation", "Timezone",
    "Runways"
]

with open(
    OUTPUT_CSV, "w", encoding="utf-8-sig", newline=""
) as file:
    writer = csv.DictWriter(file, fieldnames=fields)
    writer.writeheader()

    for airport in clean_airports:
        row = airport.copy()
        row["Runways"] = json.dumps(
            row["Runways"], ensure_ascii=False
        )
        writer.writerow(row)

print("=" * 40)
print("ОЧИСТКА ДАТАСЕТА ЗАВЕРШЕНА")
print("=" * 40)
print(f"Исходных записей: {len(airports)}")
print(f"После очистки: {len(clean_airports)}")
print(f"Удалено: {len(airports) - len(clean_airports)}")

print("\nПричины удаления:")
for reason, count in removed.items():
    print(f"{reason}: {count}")

print("\nСозданы файлы:")
print(OUTPUT_CSV)
print(OUTPUT_JSON)
