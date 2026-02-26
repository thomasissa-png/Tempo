#!/usr/bin/env python3
"""Backtest complet par saison — prédicteur Tempo.

Sources de données :
  - Couleurs EDF : fichiers iCal (2023-2026) + XLSX (2021-2022)
  - Consommation RTE : fichiers eco2mix TSV (2019-2024, données réelles)
  - Météo : synthétique déterministe (climatologie française + bruit hash)
    car les APIs Open-Meteo sont bloquées dans ce sandbox.

Pour la comparaison avant/après, les MÊMES données sont utilisées
dans les deux runs → le delta est significatif.
"""

import os
import sys
import csv
import re
import math
import json
import glob
import hashlib
from datetime import date, datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from predictor import predict_day, is_french_holiday
from config import Config
from database import get_db, init_db, get_current_weights
from tempo_client import is_in_season

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ================================================================
# 1. Chargement des couleurs EDF (ICS + XLSX)
# ================================================================

ICS_FILES = [
    "Jours Tempo période 2023-2024.ics",
    "Jours Tempo période 2024-2025.ics",
    "Jours Tempo période 2025-2026 en cours.ics",
]


def load_all_colors() -> dict:
    """Charge les couleurs EDF depuis ICS (2023-2026) et XLSX (2021-2022)."""
    colors = {}

    # --- ICS files ---
    color_map = {"bleu": "BLEU", "blanc": "BLANC", "rouge": "ROUGE"}
    for filename in ICS_FILES:
        filepath = os.path.join(BASE_DIR, filename)
        if not os.path.exists(filepath):
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        for event in content.split("BEGIN:VEVENT")[1:]:
            sm = re.search(r"SUMMARY:Tempo\s*:\s*(\w+)", event)
            dm = re.search(r"DTSTART[^:]*:(\d{8})", event)
            if sm and dm:
                color = color_map.get(sm.group(1).lower().strip())
                ds = dm.group(1)
                if color:
                    colors[f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"] = color

    # --- XLSX 2021-2022 ---
    xlsx_path = os.path.join(BASE_DIR, "Tempo_2021-2022.xlsx")
    if os.path.exists(xlsx_path):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(xlsx_path, read_only=True)
            ws = wb.active
            color_map_xl = {"BLEU": "BLEU", "BLANC": "BLANC", "ROUGE": "ROUGE"}
            for row in ws.iter_rows(values_only=True):
                if row[0] and isinstance(row[0], datetime) and row[1] in color_map_xl:
                    d = row[0].date() if isinstance(row[0], datetime) else row[0]
                    colors[d.isoformat()] = color_map_xl[row[1]]
            wb.close()
        except Exception as e:
            print(f"  Warning: XLSX parse error: {e}")

    return colors


# ================================================================
# 2. Chargement des données RTE réelles (eco2mix TSV)
# ================================================================

def load_all_rte() -> dict:
    """Parse les fichiers eco2mix TSV pour extraire le pic de consommation journalier."""
    daily = {}
    for filepath in sorted(glob.glob(os.path.join(BASE_DIR, "eCO2mix_RTE_*.xls"))):
        try:
            with open(filepath, "r", encoding="latin-1") as f:
                reader = csv.reader(f, delimiter="\t")
                header = next(reader)
                date_idx = conso_idx = None
                for i, h in enumerate(header):
                    if "Date" in h:
                        date_idx = i
                    if "Consommation" in h:
                        conso_idx = i
                if date_idx is None or conso_idx is None:
                    continue
                for row in reader:
                    if len(row) <= max(date_idx, conso_idx):
                        continue
                    d = row[date_idx].strip()
                    c = row[conso_idx].strip()
                    if d and c:
                        try:
                            conso = float(c)
                            if d not in daily or conso > daily[d]:
                                daily[d] = conso
                        except ValueError:
                            pass
        except Exception:
            continue
    return daily


def compute_rte_score(conso_mw: float) -> dict:
    """Convertit la consommation en score RTE."""
    if conso_mw >= Config.RTE_CONSO_SEUIL_CRITIQUE:
        score = 80
    elif conso_mw >= Config.RTE_CONSO_SEUIL_HAUT:
        score = 60
    elif conso_mw >= Config.RTE_CONSO_SEUIL_MOYEN:
        score = 35
    else:
        score = 10
    return {"available": True, "score": score, "peak_mw": int(conso_mw)}


# ================================================================
# 3. Météo synthétique déterministe (climatologie française)
# ================================================================

MONTHLY_TEMP_MOY = {
    1: 3.5, 2: 4.2, 3: 7.0, 4: 10.5, 5: 14.5, 6: 18.0,
    7: 20.0, 8: 19.5, 9: 16.0, 10: 12.0, 11: 7.0, 12: 4.5,
}
MONTHLY_TEMP_STD = {
    1: 3.5, 2: 3.5, 3: 3.0, 4: 2.5, 5: 2.5, 6: 2.0,
    7: 2.0, 8: 2.0, 9: 2.5, 10: 3.0, 11: 3.0, 12: 3.5,
}


def _det_noise(date_str: str, salt: str = "") -> float:
    """Bruit déterministe [-1, 1] basé sur hash(date+salt)."""
    h = hashlib.md5((date_str + salt).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF * 2 - 1


def generate_weather(d: date) -> dict:
    """Météo synthétique réaliste et déterministe."""
    d_str = d.isoformat()
    m = d.month
    day_frac = d.day / 30.0
    m_next = m + 1 if m < 12 else 1

    base_temp = MONTHLY_TEMP_MOY[m] * (1 - day_frac) + MONTHLY_TEMP_MOY[m_next] * day_frac
    std = MONTHLY_TEMP_STD[m] * (1 - day_frac) + MONTHLY_TEMP_STD[m_next] * day_frac

    noise = sum(_det_noise((d + timedelta(days=o)).isoformat(), "temp") * std for o in range(-1, 2)) / 3
    temp_moy = round(base_temp + noise, 1)
    temp_min = round(temp_moy - 3 - abs(_det_noise(d_str, "tmin")) * 2, 1)
    temp_max = round(temp_moy + 3 + abs(_det_noise(d_str, "tmax")) * 2, 1)

    return {
        "date": d_str,
        "temp_min": temp_min,
        "temp_max": temp_max,
        "temp_moy": temp_moy,
        "pressure": round(1015 + _det_noise(d_str, "press") * 15, 1),
        "humidity": round(max(30, min(100, 70 + _det_noise(d_str, "humid") * 20)), 1),
        "wind_speed": round(max(0, 15 + _det_noise(d_str, "wind") * 10), 1),
        "source": "synthetic-climatology",
        "forecast_quality": "backtest",
    }


# ================================================================
# 4. Budget estimator
# ================================================================

def estimate_remaining(target: date, colors: dict) -> dict:
    """Estime les quotas restants à une date donnée."""
    if target.month >= 9:
        season_start = date(target.year, 9, 1)
    else:
        season_start = date(target.year - 1, 9, 1)

    used_rouge = used_blanc = 0
    current = season_start
    while current < target:
        c = colors.get(current.isoformat())
        if c == "ROUGE":
            used_rouge += 1
        elif c == "BLANC":
            used_blanc += 1
        current += timedelta(days=1)

    return {
        "ROUGE": max(0, Config.JOURS_ROUGES_TOTAL - used_rouge),
        "BLANC": max(0, Config.JOURS_BLANCS_TOTAL - used_blanc),
        "BLEU": 999,
    }


# ================================================================
# 5. Backtest Engine
# ================================================================

def run_backtest(output_file: str = None):
    """Exécute le backtest complet et retourne les résultats structurés."""
    print("Chargement des données...")
    colors = load_all_colors()
    rte_data = load_all_rte()
    weights = get_current_weights()

    if not colors:
        print("ERREUR: Aucune couleur trouvée !")
        return None

    # Identifier les saisons
    seasons = {}
    for d_str in colors:
        d = date.fromisoformat(d_str)
        if d.month >= 9:
            s = f"{d.year}/{d.year+1}"
        else:
            s = f"{d.year-1}/{d.year}"
        if s not in seasons:
            seasons[s] = []
        seasons[s].append(d_str)

    # Pré-générer toute la météo
    weather_cache = {}
    all_dates = set(colors.keys())
    # Ajouter aussi J-2/J+2 autour de chaque date pour le gradient
    for d_str in list(all_dates):
        d = date.fromisoformat(d_str)
        for offset in range(-2, 3):
            nd = (d + timedelta(days=offset)).isoformat()
            if nd not in weather_cache:
                weather_cache[nd] = generate_weather(date.fromisoformat(nd))

    print(f"\n{'='*80}")
    print(f"BACKTEST COMPLET — Prédicteur Tempo")
    print(f"{'='*80}")
    print(f"Couleurs EDF   : {len(colors)} jours")
    print(f"Données RTE    : {len(rte_data)} jours (eco2mix réel)")
    print(f"Météo          : synthétique déterministe (climatologie FR)")
    print(f"Saisons        : {sorted(seasons.keys())}")
    print()

    confusion_global = defaultdict(lambda: defaultdict(int))
    season_results = {}

    for s_name in sorted(seasons.keys()):
        s_dates = sorted(seasons[s_name])
        stats = defaultdict(int)
        confusion = defaultdict(lambda: defaultdict(int))
        details = []

        for d_str in s_dates:
            d = date.fromisoformat(d_str)
            if not is_in_season(d):
                continue

            real = colors[d_str]
            w = weather_cache.get(d_str, generate_weather(d))

            # Fenêtre forecasts pour gradient
            forecasts = []
            target_idx = 0
            for offset in range(-2, 3):
                fd_str = (d + timedelta(days=offset)).isoformat()
                if fd_str in weather_cache:
                    forecasts.append(weather_cache[fd_str])
                    if offset == 0:
                        target_idx = len(forecasts) - 1

            remaining = estimate_remaining(d, colors)

            actuals_cache = {}
            for offset in range(1, 8):
                pd_str = (d - timedelta(days=offset)).isoformat()
                if pd_str in colors:
                    actuals_cache[pd_str] = colors[pd_str]

            # Score RTE : réel si disponible, sinon neutre
            rte_score = None
            if d_str in rte_data:
                rte_score = compute_rte_score(rte_data[d_str])

            pred = predict_day(
                d, weather=w, forecasts=forecasts, target_idx=target_idx,
                remaining=remaining, weights=weights,
                rte_score=rte_score, _actuals_cache=actuals_cache,
            )

            predicted = pred["couleur_predite"]
            correct = predicted == real
            stats["total"] += 1
            if correct:
                stats["correct"] += 1

            confusion[real][predicted] += 1
            confusion_global[real][predicted] += 1

            if real == "ROUGE" and predicted == "ROUGE":
                stats["rouge_tp"] += 1
            elif real != "ROUGE" and predicted == "ROUGE":
                stats["rouge_fp"] += 1
            elif real == "ROUGE" and predicted != "ROUGE":
                stats["rouge_fn"] += 1

            if real == "BLANC" and predicted == "BLANC":
                stats["blanc_tp"] += 1
            elif real != "BLANC" and predicted == "BLANC":
                stats["blanc_fp"] += 1
            elif real == "BLANC" and predicted != "BLANC":
                stats["blanc_fn"] += 1

            if not correct:
                details.append({
                    "date": d_str, "real": real, "predicted": predicted,
                    "score": pred["score_risque"], "temp": w["temp_moy"],
                    "remaining_r": remaining["ROUGE"],
                    "raison": pred.get("raison", "")[:60],
                })

        if stats["total"] == 0:
            continue

        acc = round(stats["correct"] / stats["total"] * 100, 1)
        r_rec = round(stats["rouge_tp"] / max(1, stats["rouge_tp"] + stats["rouge_fn"]) * 100, 1)
        r_prc = round(stats["rouge_tp"] / max(1, stats["rouge_tp"] + stats["rouge_fp"]) * 100, 1)
        r_f1 = round(2 * r_rec * r_prc / max(1, r_rec + r_prc), 1)
        b_rec = round(stats["blanc_tp"] / max(1, stats["blanc_tp"] + stats["blanc_fn"]) * 100, 1)
        b_prc = round(stats["blanc_tp"] / max(1, stats["blanc_tp"] + stats["blanc_fp"]) * 100, 1)

        season_results[s_name] = {
            "accuracy": acc, "rouge_recall": r_rec, "rouge_precision": r_prc,
            "rouge_f1": r_f1, "blanc_recall": b_rec, "blanc_precision": b_prc,
            "confusion": {r: dict(confusion[r]) for r in ["BLEU", "BLANC", "ROUGE"]},
            "stats": dict(stats), "details": details,
        }

        # Affichage
        r_real = sum(confusion["ROUGE"].values())
        b_real = sum(confusion["BLANC"].values())
        bl_real = sum(confusion["BLEU"].values())

        print(f"--- Saison {s_name} ---")
        print(f"  Jours : {stats['total']} (R:{r_real} B:{b_real} BL:{bl_real})"
              f"  |  RTE réel: {sum(1 for dd in s_dates if dd in rte_data)}/{len(s_dates)}")
        print(f"  Accuracy      : {acc}%")
        print(f"  ROUGE recall  : {r_rec}% ({stats['rouge_tp']}/{stats['rouge_tp']+stats['rouge_fn']})")
        print(f"  ROUGE prec    : {r_prc}% ({stats['rouge_tp']}/{stats['rouge_tp']+stats['rouge_fp']})")
        print(f"  ROUGE F1      : {r_f1}%")
        print(f"  BLANC recall  : {b_rec}% ({stats['blanc_tp']}/{stats['blanc_tp']+stats['blanc_fn']})")
        print(f"  Confusion :")
        print(f"    {'':8s} BLEU  BLANC ROUGE")
        for rc in ["BLEU", "BLANC", "ROUGE"]:
            row = confusion[rc]
            print(f"    {rc:8s} {row['BLEU']:4d}  {row['BLANC']:4d}  {row['ROUGE']:4d}")

        missed = [e for e in details if e["real"] == "ROUGE"]
        if missed:
            print(f"  ROUGE manqués ({len(missed)}) :")
            for e in missed[:5]:
                print(f"    {e['date']} prédit={e['predicted']} score={e['score']:.0f} "
                      f"temp={e['temp']:.1f}°C R_rest={e['remaining_r']}")
        print()

    # --- Bilan global ---
    g_r_tp = sum(sr["stats"]["rouge_tp"] for sr in season_results.values())
    g_r_fp = sum(sr["stats"]["rouge_fp"] for sr in season_results.values())
    g_r_fn = sum(sr["stats"]["rouge_fn"] for sr in season_results.values())
    g_b_tp = sum(sr["stats"]["blanc_tp"] for sr in season_results.values())
    g_b_fp = sum(sr["stats"]["blanc_fp"] for sr in season_results.values())
    g_b_fn = sum(sr["stats"]["blanc_fn"] for sr in season_results.values())

    g_r_rec = round(g_r_tp / max(1, g_r_tp + g_r_fn) * 100, 1)
    g_r_prc = round(g_r_tp / max(1, g_r_tp + g_r_fp) * 100, 1)
    g_r_f1 = round(2 * g_r_rec * g_r_prc / max(1, g_r_rec + g_r_prc), 1)
    g_b_rec = round(g_b_tp / max(1, g_b_tp + g_b_fn) * 100, 1)

    total_eval = sum(sr["stats"]["total"] for sr in season_results.values())
    total_correct = sum(sr["stats"]["correct"] for sr in season_results.values())
    g_acc = round(total_correct / max(1, total_eval) * 100, 1)

    print(f"\n{'='*80}")
    print(f"BILAN GLOBAL ({total_eval} jours, {len(season_results)} saisons)")
    print(f"{'='*80}")
    print(f"  Accuracy          : {g_acc}%")
    print(f"  ROUGE recall      : {g_r_rec}% ({g_r_tp}/{g_r_tp+g_r_fn})")
    print(f"  ROUGE precision   : {g_r_prc}% ({g_r_tp}/{g_r_tp+g_r_fp})")
    print(f"  ROUGE F1          : {g_r_f1}%")
    print(f"  BLANC recall      : {g_b_rec}% ({g_b_tp}/{g_b_tp+g_b_fn})")
    print(f"  ROUGE manqués     : {g_r_fn}")
    print(f"  Fausses alertes R : {g_r_fp}")

    print(f"\n  Confusion globale :")
    print(f"    {'':8s} BLEU  BLANC ROUGE")
    for rc in ["BLEU", "BLANC", "ROUGE"]:
        row = confusion_global[rc]
        print(f"    {rc:8s} {row['BLEU']:4d}  {row['BLANC']:4d}  {row['ROUGE']:4d}")

    print(f"\n  {'Saison':<12s} {'Acc':>5s} {'R.Rec':>6s} {'R.Prc':>6s} {'R.F1':>5s} "
          f"{'B.Rec':>6s} {'R_fn':>5s} {'R_fp':>5s}")
    print(f"  {'-'*55}")
    for s in sorted(season_results.keys()):
        sr = season_results[s]
        st = sr["stats"]
        print(f"  {s:<12s} {sr['accuracy']:>4.0f}% {sr['rouge_recall']:>5.0f}% "
              f"{sr['rouge_precision']:>5.0f}% {sr['rouge_f1']:>4.0f}% "
              f"{sr['blanc_recall']:>5.0f}% {st['rouge_fn']:>5d} {st['rouge_fp']:>5d}")
    print(f"  {'-'*55}")
    print(f"  {'GLOBAL':<12s} {g_acc:>4.0f}% {g_r_rec:>5.0f}% "
          f"{g_r_prc:>5.0f}% {g_r_f1:>4.0f}% {g_b_rec:>5.0f}% "
          f"{g_r_fn:>5d} {g_r_fp:>5d}")
    print()

    results = {
        "global": {
            "total": total_eval, "correct": total_correct, "accuracy": g_acc,
            "rouge_recall": g_r_rec, "rouge_precision": g_r_prc, "rouge_f1": g_r_f1,
            "blanc_recall": g_b_rec, "rouge_fn": g_r_fn, "rouge_fp": g_r_fp,
        },
        "seasons": {},
    }
    for s, sr in season_results.items():
        results["seasons"][s] = {
            k: sr[k] for k in ["accuracy", "rouge_recall", "rouge_precision",
                                "rouge_f1", "blanc_recall", "blanc_precision", "stats"]
        }

    if output_file:
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  Résultats → {output_file}")

    return results


if __name__ == "__main__":
    init_db()
    run_backtest(output_file="backtest_results.json")
