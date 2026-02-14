#!/usr/bin/env python3
"""Backtest prédicteur Tempo sur février-mars de toutes les saisons disponibles.

Simule predict_day() pour chaque jour de février et mars avec les données
réelles (météo + RTE + quotas reconstitués) et compare aux couleurs EDF.
"""

import os
import sys
import sqlite3
from datetime import date, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from predictor import predict_day, is_french_holiday
from config import Config
from database import get_db, get_current_weights
from tempo_client import is_in_season


def load_actuals():
    """Charge toutes les couleurs réelles depuis la DB."""
    conn = get_db()
    rows = conn.execute("SELECT date, couleur_reelle FROM actuals WHERE synthetic = 0").fetchall()
    conn.close()
    return {r["date"]: r["couleur_reelle"] for r in rows}


def load_weather():
    """Charge toutes les données météo depuis weather_cache."""
    conn = get_db()
    rows = conn.execute(
        "SELECT date, temp_min, temp_max, temp_moy, pressure, humidity, wind_speed FROM weather_cache"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result[r["date"]] = {
            "date": r["date"],
            "temp_min": r["temp_min"] or 5,
            "temp_max": r["temp_max"] or 10,
            "temp_moy": r["temp_moy"] or 7.5,
            "pressure": r["pressure"],
            "humidity": r["humidity"] or 50,
            "wind_speed": r["wind_speed"] or 10,
            "source": "open-meteo",
            "forecast_quality": "backtest",
        }
    return result


def load_rte():
    """Charge les données RTE depuis rte_daily."""
    conn = get_db()
    rows = conn.execute("SELECT date, conso_peak_mw FROM rte_daily WHERE conso_peak_mw IS NOT NULL").fetchall()
    conn.close()
    return {r["date"]: r["conso_peak_mw"] for r in rows}


def compute_rte_score(conso_mw):
    """Score RTE à partir de la consommation en MW."""
    if conso_mw >= Config.RTE_CONSO_SEUIL_CRITIQUE:
        score = 80
    elif conso_mw >= Config.RTE_CONSO_SEUIL_HAUT:
        score = 60
    elif conso_mw >= Config.RTE_CONSO_SEUIL_MOYEN:
        score = 35
    else:
        score = 10
    return {"available": True, "score": score, "peak_mw": int(conso_mw)}


def estimate_remaining(target, colors):
    """Estime les quotas restants à une date donnée."""
    if target.month >= 9:
        season_start = date(target.year, 9, 1)
    else:
        season_start = date(target.year - 1, 9, 1)

    used_rouge = 0
    used_blanc = 0
    current = season_start
    while current < target:
        d_str = current.isoformat()
        if d_str in colors:
            if colors[d_str] == "ROUGE":
                used_rouge += 1
            elif colors[d_str] == "BLANC":
                used_blanc += 1
        current += timedelta(days=1)

    return {
        "ROUGE": max(0, Config.JOURS_ROUGES_TOTAL - used_rouge),
        "BLANC": max(0, Config.JOURS_BLANCS_TOTAL - used_blanc),
        "BLEU": 300 - (Config.JOURS_ROUGES_TOTAL - used_rouge) - (Config.JOURS_BLANCS_TOTAL - used_blanc),
    }


def run():
    colors = load_actuals()
    weather = load_weather()
    rte = load_rte()
    weights = get_current_weights()

    # Identifier les saisons avec des données février-mars
    year_months = set()
    for d_str in colors:
        d = date.fromisoformat(d_str)
        if d.month in (2, 3):
            year_months.add((d.year, d.month))

    years = sorted(set(y for y, m in year_months))

    print(f"\n{'='*80}")
    print(f"BACKTEST FÉVRIER-MARS — Prédicteur Tempo (avec density override progressive)")
    print(f"{'='*80}")
    print(f"Années disponibles : {years}")
    print(f"Données : {len(colors)} actuals, {len(weather)} weather, {len(rte)} RTE\n")

    # Stats globales
    global_stats = {
        "total": 0, "correct": 0,
        "rouge_tp": 0, "rouge_fp": 0, "rouge_fn": 0, "rouge_tn": 0,
        "blanc_tp": 0, "blanc_fp": 0, "blanc_fn": 0,
    }
    confusion_global = defaultdict(lambda: defaultdict(int))
    year_results = {}

    for year in years:
        stats = {
            "total": 0, "correct": 0,
            "rouge_tp": 0, "rouge_fp": 0, "rouge_fn": 0,
            "blanc_tp": 0, "blanc_fp": 0, "blanc_fn": 0,
            "details": [],
        }
        confusion = defaultdict(lambda: defaultdict(int))

        for month in [2, 3]:
            d = date(year, month, 1)
            while d.month == month:
                d_str = d.isoformat()
                if d_str in colors and d_str in weather:
                    real = colors[d_str]
                    w = weather[d_str]

                    # Fenêtre de forecasts pour gradient
                    forecasts = []
                    target_idx = 0
                    for offset in range(-2, 3):
                        fd = d + timedelta(days=offset)
                        fd_str = fd.isoformat()
                        if fd_str in weather:
                            forecasts.append(weather[fd_str])
                            if offset == 0:
                                target_idx = len(forecasts) - 1

                    remaining = estimate_remaining(d, colors)

                    actuals_cache = {}
                    for offset in range(1, 8):
                        pd = d - timedelta(days=offset)
                        pd_str = pd.isoformat()
                        if pd_str in colors:
                            actuals_cache[pd_str] = colors[pd_str]

                    rte_score = None
                    if d_str in rte:
                        rte_score = compute_rte_score(rte[d_str])

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

                    # Rouge stats
                    if real == "ROUGE" and predicted == "ROUGE":
                        stats["rouge_tp"] += 1
                    elif real != "ROUGE" and predicted == "ROUGE":
                        stats["rouge_fp"] += 1
                    elif real == "ROUGE" and predicted != "ROUGE":
                        stats["rouge_fn"] += 1

                    # Blanc stats
                    if real == "BLANC" and predicted == "BLANC":
                        stats["blanc_tp"] += 1
                    elif real != "BLANC" and predicted == "BLANC":
                        stats["blanc_fp"] += 1
                    elif real == "BLANC" and predicted != "BLANC":
                        stats["blanc_fn"] += 1

                    # Détails erreurs
                    if not correct:
                        stats["details"].append({
                            "date": d_str,
                            "real": real,
                            "predicted": predicted,
                            "score": pred["score_risque"],
                            "temp": w["temp_moy"],
                            "remaining_r": remaining["ROUGE"],
                            "raison": pred.get("raison", "")[:60],
                        })

                d += timedelta(days=1)

        if stats["total"] == 0:
            continue

        accuracy = round(stats["correct"] / stats["total"] * 100, 1)
        rouge_recall = round(stats["rouge_tp"] / max(1, stats["rouge_tp"] + stats["rouge_fn"]) * 100, 1)
        rouge_precision = round(stats["rouge_tp"] / max(1, stats["rouge_tp"] + stats["rouge_fp"]) * 100, 1)
        blanc_recall = round(stats["blanc_tp"] / max(1, stats["blanc_tp"] + stats["blanc_fn"]) * 100, 1)

        # Saison = year-1 / year
        saison = f"{year-1}/{year}"
        year_results[year] = {
            "saison": saison,
            "accuracy": accuracy,
            "rouge_recall": rouge_recall,
            "rouge_precision": rouge_precision,
            "blanc_recall": blanc_recall,
            "confusion": dict(confusion),
            "stats": stats,
        }

        # Totaux globaux
        for k in ["total", "correct", "rouge_tp", "rouge_fp", "rouge_fn",
                   "blanc_tp", "blanc_fp", "blanc_fn"]:
            global_stats[k] += stats[k]

        # Affichage par année
        r_real = confusion["ROUGE"]["ROUGE"] + confusion["ROUGE"]["BLANC"] + confusion["ROUGE"]["BLEU"]
        b_real = confusion["BLANC"]["ROUGE"] + confusion["BLANC"]["BLANC"] + confusion["BLANC"]["BLEU"]
        bl_real = confusion["BLEU"]["ROUGE"] + confusion["BLEU"]["BLANC"] + confusion["BLEU"]["BLEU"]

        print(f"--- Saison {saison} (fév-mars {year}) ---")
        print(f"  Jours évalués : {stats['total']} (ROUGE réels: {r_real}, BLANC: {b_real}, BLEU: {bl_real})")
        print(f"  Accuracy : {accuracy}%")
        print(f"  ROUGE recall : {rouge_recall}% ({stats['rouge_tp']}/{stats['rouge_tp']+stats['rouge_fn']})")
        print(f"  ROUGE precision : {rouge_precision}% ({stats['rouge_tp']}/{stats['rouge_tp']+stats['rouge_fp']})")
        print(f"  BLANC recall : {blanc_recall}% ({stats['blanc_tp']}/{stats['blanc_tp']+stats['blanc_fn']})")
        print(f"  Confusion (réel\\prédit) :")
        print(f"    {'':8s} BLEU  BLANC ROUGE")
        for real_c in ["BLEU", "BLANC", "ROUGE"]:
            row = confusion[real_c]
            print(f"    {real_c:8s} {row['BLEU']:4d}  {row['BLANC']:4d}  {row['ROUGE']:4d}")

        # Erreurs ROUGE manqués
        missed_reds = [e for e in stats["details"] if e["real"] == "ROUGE"]
        if missed_reds:
            print(f"  ROUGE manqués :")
            for e in missed_reds:
                print(f"    {e['date']} prédit={e['predicted']} score={e['score']:.0f} "
                      f"temp={e['temp']:.1f}°C R_rest={e['remaining_r']}")

        # Fausses alertes ROUGE
        false_reds = [e for e in stats["details"] if e["predicted"] == "ROUGE" and e["real"] != "ROUGE"]
        if false_reds:
            print(f"  Fausses alertes ROUGE ({len(false_reds)}) :")
            for e in false_reds[:5]:
                print(f"    {e['date']} réel={e['real']} score={e['score']:.0f} "
                      f"temp={e['temp']:.1f}°C")
        print()

    # Résumé global
    g = global_stats
    g_acc = round(g["correct"] / max(1, g["total"]) * 100, 1)
    g_r_recall = round(g["rouge_tp"] / max(1, g["rouge_tp"] + g["rouge_fn"]) * 100, 1)
    g_r_prec = round(g["rouge_tp"] / max(1, g["rouge_tp"] + g["rouge_fp"]) * 100, 1)
    g_b_recall = round(g["blanc_tp"] / max(1, g["blanc_tp"] + g["blanc_fn"]) * 100, 1)
    g_r_f1 = round(2 * g_r_recall * g_r_prec / max(1, g_r_recall + g_r_prec), 1)

    print(f"\n{'='*80}")
    print(f"BILAN GLOBAL FÉV-MARS ({g['total']} jours, {len(year_results)} saisons)")
    print(f"{'='*80}")
    print(f"  Accuracy globale      : {g_acc}%")
    print(f"  ROUGE recall          : {g_r_recall}% ({g['rouge_tp']}/{g['rouge_tp']+g['rouge_fn']})")
    print(f"  ROUGE precision       : {g_r_prec}% ({g['rouge_tp']}/{g['rouge_tp']+g['rouge_fp']})")
    print(f"  ROUGE F1              : {g_r_f1}%")
    print(f"  BLANC recall          : {g_b_recall}% ({g['blanc_tp']}/{g['blanc_tp']+g['blanc_fn']})")
    print(f"  ROUGE manqués (FN)    : {g['rouge_fn']}")
    print(f"  Fausses alertes (FP)  : {g['rouge_fp']}")

    print(f"\n  Matrice de confusion globale (réel\\prédit) :")
    print(f"    {'':8s} BLEU  BLANC ROUGE")
    for real_c in ["BLEU", "BLANC", "ROUGE"]:
        row = confusion_global[real_c]
        print(f"    {real_c:8s} {row['BLEU']:4d}  {row['BLANC']:4d}  {row['ROUGE']:4d}")

    print(f"\n  Tableau récapitulatif par saison :")
    print(f"  {'Saison':<12s} {'Acc':>5s} {'R.Rec':>6s} {'R.Prec':>7s} {'R.F1':>5s} {'B.Rec':>6s} {'R_miss':>7s} {'R_fp':>5s}")
    print(f"  {'-'*60}")
    for year in sorted(year_results.keys()):
        yr = year_results[year]
        s = yr["stats"]
        r_f1 = round(2 * yr["rouge_recall"] * yr["rouge_precision"] /
                     max(1, yr["rouge_recall"] + yr["rouge_precision"]), 1)
        print(f"  {yr['saison']:<12s} {yr['accuracy']:>4.0f}% {yr['rouge_recall']:>5.0f}% "
              f"{yr['rouge_precision']:>6.0f}%  {r_f1:>4.0f}% {yr['blanc_recall']:>5.0f}% "
              f"{s['rouge_fn']:>6d} {s['rouge_fp']:>5d}")
    print(f"  {'-'*60}")
    print(f"  {'GLOBAL':<12s} {g_acc:>4.0f}% {g_r_recall:>5.0f}% "
          f"{g_r_prec:>6.0f}%  {g_r_f1:>4.0f}% {g_b_recall:>5.0f}% "
          f"{g['rouge_fn']:>6d} {g['rouge_fp']:>5d}")
    print()


if __name__ == "__main__":
    run()
