#!/usr/bin/env python3
"""Peuple tempo.db avec toutes les données historiques réelles.

À lancer sur Replit (accès internet requis pour Open-Meteo et ODRE).

Sources :
  1. Couleurs EDF  → fichiers ICS locaux (2023-2026) + XLSX (2021-2022)
  2. RTE eco2mix   → fichiers TSV locaux (2019-2024) + API ODRE (2025-2026)
  3. Météo          → API Open-Meteo Archive (9 villes, données horaires)

Usage :
    python populate_db.py

Durée estimée : ~5 minutes (Open-Meteo horaire pour 4+ saisons × 9 villes).
"""

import os
import sys
import re
import csv
import glob
import time
import logging
import asyncio
from datetime import date, datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from database import get_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Villes pondérées (identiques à config.py)
CITIES = Config.WEATHER_CITIES

# Open-Meteo Archive API
ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = "temperature_2m,relative_humidity_2m,pressure_msl,wind_speed_10m"

# ODRE (Open Data Réseaux Énergies) — consommation nationale RTE
ODRE_API_URL = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets"
ODRE_DATASET_CONSDEF = "eco2mix-national-cons-def"  # Consolidé+définitif (jan 2012 → ~nov 2024)
ODRE_DATASET_TR = "eco2mix-national-tr"              # Temps réel (~dernier mois glissant)


# ================================================================
# ÉTAPE 1 : Couleurs EDF (local)
# ================================================================

ICS_FILES = [
    "Jours Tempo période 2023-2024.ics",
    "Jours Tempo période 2024-2025.ics",
    "Jours Tempo période 2025-2026 en cours.ics",
]


def load_all_colors() -> dict[str, str]:
    """Charge les couleurs EDF depuis ICS (2023-2026) et XLSX (2021-2022)."""
    colors = {}

    # ICS
    color_map = {"bleu": "BLEU", "blanc": "BLANC", "rouge": "ROUGE"}
    for filename in ICS_FILES:
        filepath = os.path.join(BASE_DIR, filename)
        if not os.path.exists(filepath):
            logger.warning(f"  ICS introuvable : {filename}")
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        count = 0
        for event in content.split("BEGIN:VEVENT")[1:]:
            sm = re.search(r"SUMMARY:Tempo\s*:\s*(\w+)", event)
            dm = re.search(r"DTSTART[^:]*:(\d{8})", event)
            if sm and dm:
                color = color_map.get(sm.group(1).lower().strip())
                ds = dm.group(1)
                if color:
                    colors[f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"] = color
                    count += 1
        logger.info(f"  {filename}: {count} jours")

    # XLSX 2021-2022
    xlsx_path = os.path.join(BASE_DIR, "Tempo_2021-2022.xlsx")
    if os.path.exists(xlsx_path):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(xlsx_path, read_only=True)
            ws = wb.active
            count = 0
            valid = {"BLEU", "BLANC", "ROUGE"}
            for row in ws.iter_rows(values_only=True):
                if row[0] and isinstance(row[0], datetime) and row[1] in valid:
                    colors[row[0].date().isoformat()] = row[1]
                    count += 1
            wb.close()
            logger.info(f"  Tempo_2021-2022.xlsx: {count} jours")
        except Exception as e:
            logger.warning(f"  XLSX erreur: {e}")

    return colors


def store_colors(colors: dict[str, str]) -> int:
    """Insère les couleurs dans la table actuals."""
    conn = get_db()
    try:
        inserted = 0
        now = datetime.now().isoformat()
        for d_str, couleur in sorted(colors.items()):
            cursor = conn.execute(
                """INSERT OR IGNORE INTO actuals
                   (date, couleur_reelle, synthetic, timestamp_confirmation)
                   VALUES (?, ?, 0, ?)""",
                (d_str, couleur, now),
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


# ================================================================
# ÉTAPE 2 : RTE eco2mix (local TSV + API ODRE pour 2025+)
# ================================================================

def load_local_rte() -> dict[str, dict]:
    """Parse les fichiers eco2mix TSV locaux.

    Retourne {date: {conso_peak, conso_mean, nucleaire, eolien, solaire,
                     gaz, hydraulique, taux_co2}}.
    """
    daily = {}
    for filepath in sorted(glob.glob(os.path.join(BASE_DIR, "eCO2mix_RTE_Annuel*.xls")) +
                           glob.glob(os.path.join(BASE_DIR, "eCO2mix_RTE_En*.xls"))):
        try:
            with open(filepath, "r", encoding="latin-1") as f:
                reader = csv.reader(f, delimiter="\t")
                header = next(reader)

                # Trouver les indices des colonnes utiles
                idx = {}
                col_names = {
                    "Date": "date", "Consommation": "conso",
                    "Nucl": "nucleaire", "Eolien": "eolien",
                    "Solaire": "solaire", "Gaz": "gaz",
                    "Hydraulique": "hydraulique", "Taux de Co2": "co2",
                }
                for i, h in enumerate(header):
                    for pattern, name in col_names.items():
                        if pattern in h and name not in idx:
                            idx[name] = i

                if "date" not in idx or "conso" not in idx:
                    continue

                for row in reader:
                    if len(row) <= max(idx.values()):
                        continue
                    d = row[idx["date"]].strip()
                    if not d:
                        continue

                    def _val(name):
                        if name in idx:
                            v = row[idx[name]].strip()
                            if v:
                                try:
                                    return float(v)
                                except ValueError:
                                    pass
                        return None

                    conso = _val("conso")
                    if conso is None:
                        continue

                    if d not in daily:
                        daily[d] = {"conso_vals": [], "nuc": [], "eol": [],
                                    "sol": [], "gaz": [], "hyd": [], "co2": []}

                    daily[d]["conso_vals"].append(conso)
                    for key, name in [("nuc", "nucleaire"), ("eol", "eolien"),
                                      ("sol", "solaire"), ("gaz", "gaz"),
                                      ("hyd", "hydraulique"), ("co2", "co2")]:
                        v = _val(name)
                        if v is not None:
                            daily[d][key].append(v)

            logger.info(f"  {os.path.basename(filepath)}: OK")
        except Exception as e:
            logger.warning(f"  {os.path.basename(filepath)}: {e}")

    # Agréger en peak/mean par jour
    result = {}
    for d, vals in daily.items():
        cv = vals["conso_vals"]
        result[d] = {
            "conso_peak_mw": max(cv),
            "conso_mean_mw": round(sum(cv) / len(cv)),
            "nucleaire_mean_mw": round(sum(vals["nuc"]) / len(vals["nuc"])) if vals["nuc"] else None,
            "eolien_mean_mw": round(sum(vals["eol"]) / len(vals["eol"])) if vals["eol"] else None,
            "solaire_mean_mw": round(sum(vals["sol"]) / len(vals["sol"])) if vals["sol"] else None,
            "gaz_mean_mw": round(sum(vals["gaz"]) / len(vals["gaz"])) if vals["gaz"] else None,
            "hydraulique_mean_mw": round(sum(vals["hyd"]) / len(vals["hyd"])) if vals["hyd"] else None,
            "taux_co2_mean": round(sum(vals["co2"]) / len(vals["co2"]), 1) if vals["co2"] else None,
        }

    return result


async def fetch_odre_rte(start_date: date, end_date: date) -> dict[str, dict]:
    """Récupère les données RTE depuis l'API ODRE (gratuite, sans auth).

    Stratégie en 2 passes :
      1. eco2mix-national-cons-def (consolidé+définitif, jan 2012 → ~nov 2024)
      2. eco2mix-national-tr (temps réel, ~dernier mois glissant) pour les trous

    Agrégation côté serveur (GROUP BY jour via ODSQL) : ~1 requête par chunk
    de 90 jours au lieu de ~43 (pagination par 100 records éliminée).
    """
    import httpx

    daily = {}

    async with httpx.AsyncClient(timeout=60) as client:
        # Passe 1 : données consolidées (historique profond)
        logger.info(f"    Passe 1 : cons-def ({start_date} → {end_date})...")
        consdef = await _fetch_odre_dataset(client, ODRE_DATASET_CONSDEF, start_date, end_date)
        daily.update(consdef)
        logger.info(f"    → cons-def: {len(consdef)} jours")

        # Passe 2 : temps réel pour les dates manquantes
        # (eco2mix-national-tr ne garde que ~1 mois glissant, mais couvre
        # le trou entre la fin de cons-def et aujourd'hui)
        expected_days = set()
        d = start_date
        while d <= end_date:
            expected_days.add(d.isoformat())
            d += timedelta(days=1)
        missing = expected_days - set(daily.keys())

        if missing:
            miss_sorted = sorted(missing)
            tr_start = date.fromisoformat(miss_sorted[0])
            tr_end = date.fromisoformat(miss_sorted[-1])
            logger.info(f"    Passe 2 : temps-réel ({tr_start} → {tr_end}, {len(missing)} jours manquants)...")
            tr_data = await _fetch_odre_dataset(client, ODRE_DATASET_TR, tr_start, tr_end)
            daily.update(tr_data)
            logger.info(f"    → temps-réel: {len(tr_data)} jours récupérés")
        else:
            logger.info(f"    → Pas de trous, passe temps-réel inutile")

    return daily


async def _fetch_odre_dataset(client, dataset: str, start_date: date, end_date: date) -> dict:
    """Récupère les données d'un dataset ODRE par chunks de 90 jours.

    Tente l'agrégation ODSQL d'abord, puis fallback pagination si échec.
    """
    daily = {}
    chunk_start = start_date

    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=90), end_date)

        # Tenter l'agrégation serveur (1 requête = 90 jours)
        chunk_data = await _fetch_odre_chunk_aggregated(client, dataset, chunk_start, chunk_end)

        if chunk_data is None:
            # Fallback : pagination classique avec réutilisation du client
            chunk_data = await _fetch_odre_chunk_paginated(client, dataset, chunk_start, chunk_end)

        daily.update(chunk_data)
        chunk_start = chunk_end + timedelta(days=1)
        await asyncio.sleep(0.5)

    return daily


async def _fetch_odre_chunk_aggregated(client, dataset: str, chunk_start: date, chunk_end: date) -> dict | None:
    """Agrégation côté serveur : MAX/AVG par jour en une seule requête ODSQL.

    Réduit ~4320 records (90j × 48 demi-heures cons-def, ou 96 quarts-heure
    pour temps-réel) à ~90 lignes agrégées.
    Retourne None si l'API ne supporte pas l'agrégation (fallback pagination).
    """
    select = (
        "date_format(date_heure, 'YYYY-MM-dd') as day, "
        "max(consommation) as conso_peak, "
        "avg(consommation) as conso_mean, "
        "max(prevision_j1) as prev_j1_peak, "
        "avg(nucleaire) as nuc_mean, "
        "avg(eolien) as eol_mean, "
        "avg(solaire) as sol_mean, "
        "avg(gaz) as gaz_mean, "
        "avg(hydraulique) as hyd_mean"
    )
    group_by = "date_format(date_heure, 'YYYY-MM-dd') as day"

    params = {
        "where": (
            f"date_heure >= '{chunk_start.isoformat()}' "
            f"AND date_heure <= '{chunk_end.isoformat()}T23:59:59'"
        ),
        "select": select,
        "group_by": group_by,
        "order_by": "day",
        "limit": 100,
    }

    try:
        url = f"{ODRE_API_URL}/{dataset}/records"
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("results", [])
        if not records:
            return None

        result = {}
        for rec in records:
            day = rec.get("day")
            conso_peak = rec.get("conso_peak")
            if not day or conso_peak is None:
                continue
            conso_mean = rec.get("conso_mean")
            result[day] = {
                "conso_peak_mw": round(conso_peak),
                "conso_mean_mw": round(conso_mean) if conso_mean else round(conso_peak),
                "prevision_j1_peak_mw": round(rec["prev_j1_peak"]) if rec.get("prev_j1_peak") else None,
                "nucleaire_mean_mw": round(rec["nuc_mean"]) if rec.get("nuc_mean") else None,
                "eolien_mean_mw": round(rec["eol_mean"]) if rec.get("eol_mean") else None,
                "solaire_mean_mw": round(rec["sol_mean"]) if rec.get("sol_mean") else None,
                "gaz_mean_mw": round(rec["gaz_mean"]) if rec.get("gaz_mean") else None,
                "hydraulique_mean_mw": round(rec["hyd_mean"]) if rec.get("hyd_mean") else None,
            }
        return result

    except Exception as e:
        logger.warning(f"  ODRE agrégation échouée ({dataset}), fallback pagination: {e}")
        return None


async def _fetch_odre_chunk_paginated(client, dataset: str, chunk_start: date, chunk_end: date) -> dict:
    """Fallback : pagination classique record par record (limit=100).

    Utilisé uniquement si l'agrégation ODSQL échoue. Le client httpx est
    réutilisé (pas de nouvelle connexion TCP+TLS par page).
    """
    FIELDS = "date_heure,consommation,eolien,solaire,nucleaire,gaz,hydraulique,prevision_j1"
    offset = 0
    chunk_raw = {}

    while True:
        params = {
            "where": (
                f"date_heure >= '{chunk_start.isoformat()}' "
                f"AND date_heure <= '{chunk_end.isoformat()}T23:59:59'"
            ),
            "select": FIELDS,
            "order_by": "date_heure",
            "limit": 100,
            "offset": offset,
        }

        try:
            url = f"{ODRE_API_URL}/{dataset}/records"
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            records = data.get("results", [])
            if not records:
                break

            for rec in records:
                dt_str = rec.get("date_heure", "")
                conso = rec.get("consommation")
                if not dt_str or conso is None:
                    continue
                day = dt_str[:10]
                if day not in chunk_raw:
                    chunk_raw[day] = {"conso": [], "eol": [], "sol": [],
                                      "nuc": [], "gaz": [], "hyd": [],
                                      "prev_j1": []}
                chunk_raw[day]["conso"].append(conso)
                for field, key in [("eolien", "eol"), ("solaire", "sol"),
                                   ("nucleaire", "nuc"), ("gaz", "gaz"),
                                   ("hydraulique", "hyd"),
                                   ("prevision_j1", "prev_j1")]:
                    v = rec.get(field)
                    if v is not None:
                        chunk_raw[day][key].append(v)

            if len(records) < 100:
                break
            offset += 100

        except Exception as e:
            logger.warning(f"  ODRE erreur ({dataset}, offset={offset}): {e}")
            break

    # Agréger par jour
    result = {}
    for d, vals in chunk_raw.items():
        cv = vals["conso"]
        result[d] = {
            "conso_peak_mw": max(cv),
            "conso_mean_mw": round(sum(cv) / len(cv)),
            "prevision_j1_peak_mw": max(vals["prev_j1"]) if vals["prev_j1"] else None,
            "nucleaire_mean_mw": round(sum(vals["nuc"]) / len(vals["nuc"])) if vals["nuc"] else None,
            "eolien_mean_mw": round(sum(vals["eol"]) / len(vals["eol"])) if vals["eol"] else None,
            "solaire_mean_mw": round(sum(vals["sol"]) / len(vals["sol"])) if vals["sol"] else None,
            "gaz_mean_mw": round(sum(vals["gaz"]) / len(vals["gaz"])) if vals["gaz"] else None,
            "hydraulique_mean_mw": round(sum(vals["hyd"]) / len(vals["hyd"])) if vals["hyd"] else None,
        }
    return result


def store_rte(rte_data: dict[str, dict]) -> int:
    """Stocke les données RTE dans rte_daily.

    Utilise INSERT OR REPLACE pour que les données complètes (avec éolien/solaire)
    écrasent les données partielles (conso seule) insérées par ODRE.
    """
    conn = get_db()
    try:
        inserted = 0
        for d_str, vals in sorted(rte_data.items()):
            cursor = conn.execute(
                """INSERT OR REPLACE INTO rte_daily
                   (date, conso_peak_mw, conso_mean_mw, prevision_j1_peak_mw,
                    nucleaire_mean_mw, eolien_mean_mw, solaire_mean_mw,
                    gaz_mean_mw, hydraulique_mean_mw, taux_co2_mean)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d_str,
                 vals.get("conso_peak_mw"),
                 vals.get("conso_mean_mw"),
                 vals.get("prevision_j1_peak_mw"),
                 vals.get("nucleaire_mean_mw"),
                 vals.get("eolien_mean_mw"),
                 vals.get("solaire_mean_mw"),
                 vals.get("gaz_mean_mw"),
                 vals.get("hydraulique_mean_mw"),
                 vals.get("taux_co2_mean")),
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


# ================================================================
# ÉTAPE 3 : Météo historique (Open-Meteo Archive API)
# ================================================================

def _aggregate_hourly_to_daily(hourly: dict) -> dict[str, dict]:
    """Agrège les données horaires Open-Meteo en statistiques journalières."""
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    humidities = hourly.get("relative_humidity_2m", [])
    pressures = hourly.get("pressure_msl", [])
    winds = hourly.get("wind_speed_10m", [])

    days = defaultdict(lambda: {"temps": [], "humids": [], "pressures": [], "winds": []})

    for i, ts in enumerate(times):
        day_str = ts[:10]
        if i < len(temps) and temps[i] is not None:
            days[day_str]["temps"].append(temps[i])
        if i < len(humidities) and humidities[i] is not None:
            days[day_str]["humids"].append(humidities[i])
        if i < len(pressures) and pressures[i] is not None:
            days[day_str]["pressures"].append(pressures[i])
        if i < len(winds) and winds[i] is not None:
            days[day_str]["winds"].append(winds[i])

    result = {}
    for day_str, data in days.items():
        if not data["temps"]:
            continue
        result[day_str] = {
            "temp_min": round(min(data["temps"]), 1),
            "temp_max": round(max(data["temps"]), 1),
            "temp_moy": round(sum(data["temps"]) / len(data["temps"]), 1),
            "wind_speed": round(max(data["winds"]), 1) if data["winds"] else 10.0,
            "humidity": round(sum(data["humids"]) / len(data["humids"]), 1) if data["humids"] else 70.0,
            "pressure": round(sum(data["pressures"]) / len(data["pressures"]), 1) if data["pressures"] else None,
        }
    return result


def _merge_cities(city_data: dict) -> dict[str, dict]:
    """Fusionne les données de toutes les villes en moyennes pondérées nationales."""
    all_dates = set()
    for info in city_data.values():
        all_dates.update(info["daily"].keys())

    result = {}
    for day_str in sorted(all_dates):
        tw = {"min": 0, "max": 0, "moy": 0, "wind": 0, "hum": 0, "press": 0}
        total_w = 0.0
        press_w = 0.0

        for city_name, info in city_data.items():
            daily = info["daily"]
            if day_str not in daily:
                continue
            d = daily[day_str]
            w = info["weight"]
            total_w += w
            tw["min"] += d["temp_min"] * w
            tw["max"] += d["temp_max"] * w
            tw["moy"] += d["temp_moy"] * w
            tw["wind"] += d["wind_speed"] * w
            tw["hum"] += d["humidity"] * w
            if d["pressure"] is not None:
                tw["press"] += d["pressure"] * w
                press_w += w

        if total_w == 0:
            continue

        result[day_str] = {
            "temp_min": round(tw["min"] / total_w, 1),
            "temp_max": round(tw["max"] / total_w, 1),
            "temp_moy": round(tw["moy"] / total_w, 1),
            "wind_speed": round(tw["wind"] / total_w, 1),
            "humidity": round(tw["hum"] / total_w, 1),
            "pressure": round(tw["press"] / press_w, 1) if press_w > 0 else None,
        }
    return result


async def fetch_weather_period(start_date: date, end_date: date, label: str) -> dict[str, dict]:
    """Récupère la météo pour une période via Open-Meteo Archive (9 villes)."""
    import httpx

    # Ne pas demander de dates futures
    yesterday = date.today() - timedelta(days=1)
    if end_date > yesterday:
        end_date = yesterday
    if start_date > end_date:
        logger.info(f"  {label}: période dans le futur, ignorée")
        return {}

    city_data = {}
    for city in CITIES:
        params = {
            "latitude": city["lat"],
            "longitude": city["lon"],
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "hourly": HOURLY_VARS,
            "timezone": "Europe/Paris",
        }

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(ARCHIVE_API_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    daily = _aggregate_hourly_to_daily(data.get("hourly", {}))
                    city_data[city["name"]] = {"weight": city["weight"], "daily": daily}
                    logger.info(f"    {city['name']}: {len(daily)} jours")
                    break
            except Exception as e:
                if attempt < 2:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"    {city['name']} erreur (retry {wait}s): {e}")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"    {city['name']} ÉCHEC: {e}")

        await asyncio.sleep(0.3)

    if not city_data:
        logger.error(f"  {label}: aucune ville n'a répondu !")
        return {}

    merged = _merge_cities(city_data)
    logger.info(f"  {label}: {len(merged)} jours de météo (moyenne {len(city_data)} villes)")
    return merged


def store_weather(weather_data: dict[str, dict]) -> int:
    """Stocke la météo dans weather_cache.

    Utilise DELETE + INSERT au lieu de INSERT OR IGNORE car weather_cache
    a un UNIQUE INDEX sur date (migration v3) et la migration dé-duplique
    par MAX(id). On écrase les entrées existantes pour injecter l'historique.
    """
    conn = get_db()
    try:
        inserted = 0
        now = datetime.now().isoformat()
        for d_str, w in sorted(weather_data.items()):
            # Supprimer l'entrée existante pour cette date
            conn.execute("DELETE FROM weather_cache WHERE date = ?", (d_str,))
            conn.execute(
                """INSERT INTO weather_cache
                   (date, temp_min, temp_max, temp_moy, pressure,
                    humidity, wind_speed, description, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d_str, w["temp_min"], w["temp_max"], w["temp_moy"],
                 w.get("pressure"), w.get("humidity", 70),
                 w["wind_speed"], "Open-Meteo Archive (horaire agrégé)", now),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


# ================================================================
# ORCHESTRATION
# ================================================================

async def main():
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("POPULATE DB — Données historiques réelles")
    logger.info("=" * 60)

    init_db()

    # --- Étape 1 : Couleurs EDF (local) ---
    logger.info("\n[1/4] Couleurs EDF (ICS + XLSX locaux)...")
    colors = load_all_colors()
    inserted = store_colors(colors)
    logger.info(f"  → {len(colors)} jours chargés, {inserted} insérés en DB")

    # --- Étape 2 : RTE eco2mix (local + ODRE) ---
    logger.info("\n[2/4] RTE eco2mix (fichiers locaux)...")
    rte_local = load_local_rte()
    rte_inserted = store_rte(rte_local)
    logger.info(f"  → {len(rte_local)} jours locaux, {rte_inserted} insérés")

    # Compléter avec ODRE pour la période manquante (2025+)
    # IMPORTANT : les TSV locaux sont chargés en premier (INSERT OR REPLACE)
    # pour que les données complètes (avec éolien/solaire) priment.
    # ODRE ne vient combler que les dates ABSENTES des TSV.
    local_rte_dates = set(rte_local.keys())
    color_dates = set(colors.keys())
    missing_rte_dates = sorted(color_dates - local_rte_dates)

    if missing_rte_dates:
        miss_start = date.fromisoformat(missing_rte_dates[0])
        miss_end = date.fromisoformat(missing_rte_dates[-1])
        logger.info(f"\n[2b/4] RTE manquant via ODRE ({miss_start} → {miss_end})...")
        try:
            rte_odre = await fetch_odre_rte(miss_start, miss_end)
            if rte_odre:
                odre_inserted = store_rte(rte_odre)
                logger.info(f"  → {len(rte_odre)} jours ODRE, {odre_inserted} insérés")
            else:
                logger.warning("  → Aucune donnée ODRE récupérée")
        except Exception as e:
            logger.warning(f"  → ODRE erreur: {e}")
    else:
        logger.info("  → Pas de période RTE manquante")

    # --- Étape 3 : Météo historique (Open-Meteo Archive) ---
    logger.info("\n[3/4] Météo historique (Open-Meteo Archive, 9 villes)...")

    # Découper par saison pour lisibilité et robustesse
    all_dates = sorted(colors.keys())
    first = date.fromisoformat(all_dates[0])
    last = date.fromisoformat(all_dates[-1])

    # Construire les périodes par saison
    periods = []
    if first.month >= 9:
        y = first.year
    else:
        y = first.year - 1

    while True:
        s_start = date(y, 9, 1)
        s_end = date(y + 1, 8, 31)
        # Couper aux bornes réelles
        p_start = max(s_start, first)
        p_end = min(s_end, last)
        if p_start <= p_end:
            periods.append((p_start, p_end, f"{y}/{y+1}"))
        y += 1
        if s_start > last:
            break

    all_weather = {}
    for p_start, p_end, label in periods:
        logger.info(f"  Saison {label} ({p_start} → {p_end})...")
        weather = await fetch_weather_period(p_start, p_end, label)
        all_weather.update(weather)
        await asyncio.sleep(1)

    weather_inserted = store_weather(all_weather)
    logger.info(f"  → {len(all_weather)} jours de météo total, {weather_inserted} insérés")

    # --- Étape 4 : Vérification ---
    logger.info("\n[4/4] Vérification de la base...")
    conn = get_db()
    try:
        n_actuals = conn.execute("SELECT COUNT(*) FROM actuals").fetchone()[0]
        n_weather = conn.execute("SELECT COUNT(*) FROM weather_cache").fetchone()[0]
        n_rte = conn.execute("SELECT COUNT(*) FROM rte_daily").fetchone()[0]

        # Couverture
        dates_with_all = conn.execute(
            """SELECT COUNT(DISTINCT a.date) FROM actuals a
               JOIN weather_cache w ON a.date = w.date"""
        ).fetchone()[0]

        dates_with_rte = conn.execute(
            """SELECT COUNT(DISTINCT a.date) FROM actuals a
               JOIN rte_daily r ON a.date = r.date"""
        ).fetchone()[0]

        dates_full = conn.execute(
            """SELECT COUNT(DISTINCT a.date) FROM actuals a
               JOIN weather_cache w ON a.date = w.date
               JOIN rte_daily r ON a.date = r.date"""
        ).fetchone()[0]
    finally:
        conn.close()

    elapsed = round(time.time() - start_time, 1)

    logger.info(f"\n{'=' * 60}")
    logger.info(f"BASE PEUPLÉE EN {elapsed}s")
    logger.info(f"{'=' * 60}")
    logger.info(f"  Actuals (couleurs EDF) : {n_actuals}")
    logger.info(f"  Weather (météo)        : {n_weather}")
    logger.info(f"  RTE (consommation)     : {n_rte}")
    logger.info(f"")
    logger.info(f"  Couverture :")
    logger.info(f"    Couleur + Météo      : {dates_with_all}/{n_actuals}")
    logger.info(f"    Couleur + RTE        : {dates_with_rte}/{n_actuals}")
    logger.info(f"    Couleur + Météo + RTE: {dates_full}/{n_actuals} (complet)")
    logger.info(f"{'=' * 60}")

    if dates_with_all < n_actuals * 0.9:
        logger.warning(
            f"  ⚠ Moins de 90% de couverture météo ({dates_with_all}/{n_actuals}). "
            f"Vérifiez les logs ci-dessus pour les erreurs API."
        )


if __name__ == "__main__":
    asyncio.run(main())
