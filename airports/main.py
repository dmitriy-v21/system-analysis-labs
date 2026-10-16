
import csv
import json
import re
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from timezonefinder import TimezoneFinder

# Папки проекта
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

AIRPORTS_FILE = DATA_DIR / "airports.csv"
COUNTRIES_FILE = DATA_DIR / "countries.csv"
RUNWAYS_FILE = DATA_DIR / "runways.csv"

OUTPUT_CSV = BASE_DIR / "airports_processed.csv"
OUTPUT_JSON = BASE_DIR / "airports.json"

tf = TimezoneFinder()


def load_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def to_float(value):
    try:
        return float(value) if value not in ("", None) else None
    except (ValueError, TypeError):
        return None


def to_int(value):
    try:
        return int(float(value)) if value not in ("", None) else None
    except (ValueError, TypeError):
        return None


def get_icao(airport):
    # Используем только GPS-код из исходных данных,
    # если он соответствует формату ICAO.
    code = (airport.get("gps_code") or "").strip().upper()

    if re.fullmatch(r"[A-Z]{4}", code):
        return code

    return None


def get_timezone(latitude, longitude):
    if latitude is None or longitude is None:
        return None

    try:
        zone_name = tf.timezone_at(
            lat=latitude,
            lng=longitude
        )

        if not zone_name:
            return None

        local_time = datetime.now(ZoneInfo(zone_name))
        offset = local_time.utcoffset()

        if offset is None:
            return None

        # В задании требуется целое число часов.
        return round(offset.total_seconds() / 3600)

    except (ValueError, TypeError, KeyError):
        return None


def main():
    print("Загружаем исходные CSV...")

    airports = load_csv(AIRPORTS_FILE)
    countries = load_csv(COUNTRIES_FILE)
    runways = load_csv(RUNWAYS_FILE)

    print(f"Аэропортов в источнике: {len(airports)}")
    print(f"Стран: {len(countries)}")
    print(f"Записей о полосах: {len(runways)}")

    # Справочник стран
    country_names = {
        country["code"]: country["name"]
        for country in countries
    }

    # Группируем полосы по идентификатору аэропорта
    runway_dict = {}

    for runway in runways:
        airport_ident = runway.get("airport_ident")

        if not airport_ident:
            continue

        runway_id = (
            (runway.get("le_ident") or "") + "/" +
            (runway.get("he_ident") or "")
        ).strip("/")

        if not runway_id:
            runway_id = str(runway.get("id", ""))

        runway_data = {
            "ID": runway_id,
            "Length": to_int(runway.get("length_ft")),
            "Surface": runway.get("surface") or None
        }

        runway_dict.setdefault(airport_ident, []).append(runway_data)

    result = []

    for index, airport in enumerate(airports, start=1):
        latitude = to_float(airport.get("latitude_deg"))
        longitude = to_float(airport.get("longitude_deg"))

        airport_type = airport.get("type") or None

        status = (
            "close" if airport_type == "closed"
            else "open" if airport_type
            else None
        )

        airport_ident = airport.get("ident", "")

        record = {
            "ICAO": get_icao(airport),
            "Latitude": latitude,
            "Longitude": longitude,
            "Status": status,
            "Type": airport_type,
            "Country": country_names.get(
                airport.get("iso_country"), None
            ),
            "Elevation": to_int(airport.get("elevation_ft")),
            "Timezone": get_timezone(latitude, longitude),
            "Runways": runway_dict.get(airport_ident, [])
        }

        result.append(record)

        if index % 5000 == 0:
            print(f"Обработано: {index} / {len(airports)}")

    # Сохраняем CSV
    fieldnames = [
        "ICAO", "Latitude", "Longitude", "Status",
        "Type", "Country", "Elevation", "Timezone",
        "Runways"
    ]

    with open(
        OUTPUT_CSV, "w", encoding="utf-8-sig",
        newline=""
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for airport in result:
            row = airport.copy()
            row["Runways"] = json.dumps(
                row["Runways"], ensure_ascii=False
            )
            writer.writerow(row)

    # Сохраняем JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as file:
        json.dump(
            result, file,
            ensure_ascii=False,
            indent=4
        )

    print("\nСбор завершён!")
    print(f"Всего обработано: {len(result)}")
    print(f"CSV: {OUTPUT_CSV}")
    print(f"JSON: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
