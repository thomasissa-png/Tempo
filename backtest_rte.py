#!/usr/bin/env python3
"""Backtest de l'algorithme RTE officiel sur les données historiques.

Réplique fidèle du document "Méthode de choix des jours Tempo" (indice 2, 07/01/2025).

Étapes :
  1. Calcul de C_nette = Conso - (Éolien + Solaire) pour chaque jour
  2. Normalisation z-score quantile avec correction température (γ, κ)
  3. Comparaison aux deux seuils linéaires (Blanc+Rouge, Rouge)
  4. Application des contraintes de placement (R1-R4)
  5. Gestion du stock (décrémentation séquentielle)

Usage :
    python backtest_rte.py
"""

import sqlite3
import json
import logging
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ================================================================
# PARAMÈTRES RTE OFFICIELS (document indice 2 du 07/01/2025)
# ================================================================

# Normalisation (page 2-3)
GAMMA = -0.1176       # sensibilité conso/température
KAPPA = 8.3042        # référence température (°C), moyenne du quantile 30%

# Seuils (page 4, tableau des paramètres calibrés)
# Seuil_k = A + B * jour_tempo + C * stock_restant_k
SEUIL_ROUGE = {"A": 3.15, "B": -0.010, "C": -0.031}
SEUIL_BLANC_ROUGE = {"A": 4.00, "B": -0.015, "C": -0.026}

# ================================================================
# JOURS FÉRIÉS FRANÇAIS
# ================================================================

def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)

def get_french_holidays(year: int) -> set[date]:
    holidays = {
        date(year, 1, 1), date(year, 5, 1), date(year, 5, 8),
        date(year, 7, 14), date(year, 8, 15), date(year, 11, 1),
        date(year, 11, 11), date(year, 12, 25),
    }
    easter = _easter(year)
    holidays.add(easter)
    holidays.add(easter + timedelta(days=1))
    holidays.add(easter + timedelta(days=39))
    holidays.add(easter + timedelta(days=50))
    return holidays

_holidays_cache = {}
def is_french_holiday(d: date) -> bool:
    if d.year not in _holidays_cache:
        _holidays_cache[d.year] = get_french_holidays(d.year)
    return d in _holidays_cache[d.year]

# ================================================================
# CHARGEMENT DES DONNÉES
# ================================================================

def load_data(db_path: str = "tempo.db") -> tuple[dict, dict, dict]:
    """Charge rte_daily, weather_cache, actuals depuis la DB.

    Retourne (rte_by_date, weather_by_date, actuals_by_date).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # RTE
    rte = {}
    for row in conn.execute("SELECT * FROM rte_daily WHERE eolien_mean_mw IS NOT NULL"):
        rte[row["date"]] = dict(row)

    # Météo (pour normalisation température)
    weather = {}
    for row in conn.execute("SELECT date, temp_moy FROM weather_cache"):
        weather[row["date"]] = row["temp_moy"]

    # Actuals
    actuals = {}
    for row in conn.execute("SELECT date, couleur_reelle FROM actuals WHERE synthetic = 0"):
        actuals[row["date"]] = row["couleur_reelle"]

    conn.close()
    return rte, weather, actuals

# ================================================================
# CALCUL DE LA CONSOMMATION NETTE
# ================================================================

def compute_c_nette(rte: dict) -> dict[str, float]:
    """Calcule C_nette = conso_mean - éolien_mean - solaire_mean pour chaque jour.

    Approximation : on utilise les moyennes journalières (00h-00h) au lieu
    de la somme 06h-06h. La normalisation corrigera ce biais systématique.
    """
    c_nette = {}
    for d, row in rte.items():
        conso = row.get("conso_mean_mw")
        eolien = row.get("eolien_mean_mw")
        solaire = row.get("solaire_mean_mw")
        if conso is not None and eolien is not None and solaire is not None:
            c_nette[d] = conso - eolien - solaire
    return c_nette

# ================================================================
# NORMALISATION (formule exacte RTE, page 2-3)
# ================================================================

def compute_normalization_params(c_nette: dict, weather: dict,
                                  ref_date: date, window_days: int = 365
                                  ) -> tuple[float, float, float] | None:
    """Calcule les paramètres de normalisation sur 1 an glissant.

    Retourne (q_conso_04, q_conso_08, q_temp_03) ou None si données insuffisantes.
    """
    start = ref_date - timedelta(days=window_days)
    end = ref_date - timedelta(days=1)

    conso_values = []
    temp_values = []

    d = start
    while d <= end:
        ds = d.isoformat()
        if ds in c_nette:
            conso_values.append(c_nette[ds])
        if ds in weather:
            temp_values.append(weather[ds])
        d += timedelta(days=1)

    if len(conso_values) < 100:
        return None

    q_conso_04 = float(np.percentile(conso_values, 40))
    q_conso_08 = float(np.percentile(conso_values, 80))

    if temp_values:
        q_temp_03 = float(np.percentile(temp_values, 30))
    else:
        # Fallback : estimer la température depuis la consommation
        # (corrélation forte en France, chauffage électrique dominant)
        q_temp_03 = KAPPA  # utiliser la référence RTE

    return q_conso_04, q_conso_08, q_temp_03


def normalize_c_nette(c_nette_value: float,
                       q_conso_04: float, q_conso_08: float,
                       q_temp_03: float) -> float:
    """Applique la formule de normalisation RTE.

    C_nette_std = (C_nette - q_conso_04) / ((q_conso_08 - q_conso_04) * e^(-γ(q_temp_03 - κ)))
    """
    import math
    spread = q_conso_08 - q_conso_04
    if spread <= 0:
        return 0.0
    temp_correction = math.exp(-GAMMA * (q_temp_03 - KAPPA))
    return (c_nette_value - q_conso_04) / (spread * temp_correction)

# ================================================================
# SEUILS DYNAMIQUES (page 3-4)
# ================================================================

def compute_seuil_rouge(jour_tempo: int, stock_rouge: int) -> float:
    """Seuil_rouge = 3.15 - 0.010 * jour_tempo - 0.031 * stock_rouge"""
    s = SEUIL_ROUGE
    return s["A"] + s["B"] * jour_tempo + s["C"] * stock_rouge

def compute_seuil_blanc_rouge(jour_tempo: int, stock_rouge: int, stock_blanc: int) -> float:
    """Seuil_B+R = 4.00 - 0.015 * jour_tempo - 0.026 * (stock_rouge + stock_blanc)"""
    s = SEUIL_BLANC_ROUGE
    return s["A"] + s["B"] * jour_tempo + s["C"] * (stock_rouge + stock_blanc)

def jour_tempo(d: date) -> int:
    """Numéro du jour dans l'année Tempo (0 = 1er septembre)."""
    if d.month >= 9:
        season_start = date(d.year, 9, 1)
    else:
        season_start = date(d.year - 1, 9, 1)
    return (d - season_start).days

# ================================================================
# CONTRAINTES CALENDAIRES (R1-R4)
# ================================================================

def can_be_rouge(d: date, consecutive_rouge: int) -> bool:
    """Vérifie si le jour peut être rouge (R1, R2, R4)."""
    # R1 : Rouge seulement du 1er novembre au 31 mars
    if not (d.month >= 11 or d.month <= 3):
        return False
    # R2 : Pas de rouge le weekend
    if d.weekday() >= 5:
        return False
    # R2 : Pas de rouge les jours fériés
    if is_french_holiday(d):
        return False
    # R4 : Max 5 jours rouges consécutifs
    if consecutive_rouge >= 5:
        return False
    return True

def can_be_blanc(d: date) -> bool:
    """Vérifie si le jour peut être blanc (R3)."""
    # R3 : Pas de blanc le dimanche
    if d.weekday() == 6:
        return False
    return True

# ================================================================
# BACKTEST PRINCIPAL
# ================================================================

def run_backtest(db_path: str = "tempo.db") -> dict:
    """Exécute le backtest de l'algorithme RTE sur toutes les saisons disponibles."""

    rte, weather, actuals = load_data(db_path)
    c_nette = compute_c_nette(rte)

    logger.info(f"Données chargées: {len(rte)} jours RTE, {len(weather)} jours météo, "
                f"{len(actuals)} couleurs EDF, {len(c_nette)} jours C_nette")

    # Identifier les saisons complètes
    all_dates = sorted(actuals.keys())
    if not all_dates:
        logger.error("Aucune couleur EDF en base !")
        return {}

    first = date.fromisoformat(all_dates[0])
    last = date.fromisoformat(all_dates[-1])

    # Déterminer les saisons
    seasons = []
    if first.month >= 9:
        y = first.year
    else:
        y = first.year - 1
    while True:
        s_start = date(y, 9, 1)
        s_end = date(y + 1, 8, 31)
        if s_start > last:
            break
        seasons.append((s_start, s_end, f"{y}/{y+1}"))
        y += 1

    results_by_season = {}
    all_predictions = []

    for s_start, s_end, season_label in seasons:
        logger.info(f"\n{'='*60}")
        logger.info(f"SAISON {season_label}")
        logger.info(f"{'='*60}")

        # Stock initial
        stock_rouge = 22
        stock_blanc = 43
        consecutive_rouge = 0

        season_preds = []
        d = s_start

        while d <= s_end:
            ds = d.isoformat()
            actual_color = actuals.get(ds)

            if actual_color is None or ds not in c_nette:
                d += timedelta(days=1)
                # Reset consecutive si pas rouge
                consecutive_rouge = 0
                continue

            # Calculer normalisation (1 an glissant)
            norm_params = compute_normalization_params(c_nette, weather, d)
            if norm_params is None:
                d += timedelta(days=1)
                consecutive_rouge = 0
                continue

            q_c04, q_c08, q_t03 = norm_params
            c_nette_std = normalize_c_nette(c_nette[ds], q_c04, q_c08, q_t03)

            jt = jour_tempo(d)
            seuil_r = compute_seuil_rouge(jt, stock_rouge)
            seuil_br = compute_seuil_blanc_rouge(jt, stock_rouge, stock_blanc)

            # --- Décision (flowchart page 5) ---
            predicted = "BLEU"

            if c_nette_std > seuil_br:
                # Franchissement du premier seuil
                if c_nette_std > seuil_r:
                    # Franchissement des deux seuils
                    if can_be_rouge(d, consecutive_rouge) and stock_rouge > 0:
                        predicted = "ROUGE"
                    elif can_be_blanc(d) and stock_blanc > 0:
                        predicted = "BLANC"
                else:
                    # Uniquement le seuil blanc+rouge
                    if can_be_blanc(d) and stock_blanc > 0:
                        predicted = "BLANC"

            # --- Forçage fin de période (stock non écoulé) ---
            # Si on approche de la fin et qu'il reste du stock
            if predicted == "BLEU":
                # Rouge : deadline 31 mars
                if stock_rouge > 0 and (d.month >= 11 or d.month <= 3):
                    if d.month <= 3:
                        red_deadline = date(d.year, 3, 31)
                    else:
                        red_deadline = date(d.year + 1, 3, 31)
                    red_days_left = (red_deadline - d).days
                    # Compter jours éligibles restants
                    eligible = 0
                    check = d
                    while check <= red_deadline:
                        if check.weekday() < 5 and not is_french_holiday(check):
                            eligible += 1
                        check += timedelta(days=1)
                    if eligible <= stock_rouge and can_be_rouge(d, consecutive_rouge):
                        predicted = "ROUGE"

                # Blanc : deadline 31 mai (approximation — l'été est rare pour blanc)
                if predicted == "BLEU" and stock_blanc > 0:
                    if d.month <= 5:
                        wh_deadline = date(d.year, 5, 31)
                    elif d.month >= 9:
                        wh_deadline = date(d.year + 1, 5, 31)
                    else:
                        wh_deadline = None
                    if wh_deadline:
                        wh_days_left = (wh_deadline - d).days
                        wh_eligible = 0
                        check = d
                        while check <= wh_deadline:
                            if check.weekday() != 6:
                                wh_eligible += 1
                            check += timedelta(days=1)
                        if wh_eligible <= stock_blanc and can_be_blanc(d):
                            predicted = "BLANC"

            # --- Mise à jour du stock (page 6) ---
            # Chaque couleur décrémente uniquement SON propre stock.
            # Le seuil B+R utilise la SOMME (stock_rouge + stock_blanc).
            if predicted == "ROUGE":
                stock_rouge -= 1
                consecutive_rouge += 1
            elif predicted == "BLANC":
                stock_blanc -= 1
                consecutive_rouge = 0
            else:
                consecutive_rouge = 0

            season_preds.append({
                "date": ds,
                "actual": actual_color,
                "predicted": predicted,
                "c_nette": round(c_nette[ds]),
                "c_nette_std": round(c_nette_std, 3),
                "seuil_rouge": round(seuil_r, 3),
                "seuil_br": round(seuil_br, 3),
                "jour_tempo": jt,
                "stock_rouge": stock_rouge,
                "stock_blanc": stock_blanc,
            })
            all_predictions.append(season_preds[-1])

            d += timedelta(days=1)

        # --- Métriques de la saison ---
        if season_preds:
            metrics = compute_metrics(season_preds, season_label)
            results_by_season[season_label] = metrics

    # --- Métriques globales ---
    if all_predictions:
        logger.info(f"\n{'='*60}")
        logger.info("RÉSULTATS GLOBAUX")
        logger.info(f"{'='*60}")
        global_metrics = compute_metrics(all_predictions, "GLOBAL")
        results_by_season["GLOBAL"] = global_metrics

    # Sauvegarder les résultats détaillés
    with open("backtest_rte_results.json", "w") as f:
        json.dump({
            "predictions": all_predictions,
            "metrics": results_by_season,
        }, f, indent=2, ensure_ascii=False)
    logger.info(f"\nRésultats détaillés sauvegardés dans backtest_rte_results.json")

    return results_by_season


def compute_metrics(predictions: list[dict], label: str) -> dict:
    """Calcule les métriques de performance."""
    total = len(predictions)
    correct = sum(1 for p in predictions if p["actual"] == p["predicted"])
    accuracy = correct / total if total else 0

    # Par couleur
    colors = ["ROUGE", "BLANC", "BLEU"]
    metrics = {"total": total, "correct": correct, "accuracy": round(accuracy * 100, 1)}

    for color in colors:
        tp = sum(1 for p in predictions if p["actual"] == color and p["predicted"] == color)
        fp = sum(1 for p in predictions if p["actual"] != color and p["predicted"] == color)
        fn = sum(1 for p in predictions if p["actual"] == color and p["predicted"] != color)
        tn = sum(1 for p in predictions if p["actual"] != color and p["predicted"] != color)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        metrics[color] = {
            "count_actual": tp + fn,
            "count_predicted": tp + fp,
            "TP": tp, "FP": fp, "FN": fn,
            "precision": round(precision * 100, 1),
            "recall": round(recall * 100, 1),
            "f1": round(f1 * 100, 1),
        }

    # Matrice de confusion
    confusion = defaultdict(lambda: defaultdict(int))
    for p in predictions:
        confusion[p["actual"]][p["predicted"]] += 1

    logger.info(f"\n  [{label}] {total} jours, accuracy = {accuracy*100:.1f}%")
    logger.info(f"  {'':8} | {'ROUGE':>7} {'BLANC':>7} {'BLEU':>7} | count")
    logger.info(f"  {'-'*8}-+-{'-'*7}-{'-'*7}-{'-'*7}-+------")
    for actual in colors:
        row = f"  {actual:8} |"
        for pred in colors:
            row += f" {confusion[actual][pred]:>7}"
        row += f" | {metrics[actual]['count_actual']}"
        logger.info(row)

    for color in colors:
        m = metrics[color]
        logger.info(f"  {color}: precision={m['precision']}%, recall={m['recall']}%, "
                    f"F1={m['f1']}% (TP={m['TP']}, FP={m['FP']}, FN={m['FN']})")

    return metrics


if __name__ == "__main__":
    run_backtest()
