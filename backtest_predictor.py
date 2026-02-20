#!/usr/bin/env python3
"""Backtest du prédicteur Tempo v3.3 sur données historiques 2019-2026.

Simule les prédictions J+2 pour chaque jour ayant un actual,
en utilisant les données météo et RTE historiques réelles.

Pour chaque jour D :
  1. Calcule les remaining_colors à D-2 (simule J+2 sans actual D-1)
  2. Récupère la météo de D (= forecast parfait, borne supérieure)
  3. Appelle predict_day() avec forward clustering (predicted_colors propagé)
  4. Compare la prédiction à la couleur EDF réelle

Fix v3.3 :
  - P7: remaining utilise actuals jusqu'à D-2 (pas D-1) pour simuler J+2
  - P1: forward clustering activé (predicted_colors propagé entre jours)

Limitation : on utilise la météo RÉELLE du jour D, pas un forecast J+2.
Le backtest mesure donc la qualité du SCORING et des SEUILS, pas la
qualité des prévisions météo. C'est une borne supérieure de performance.

Usage :
    python backtest_predictor.py
"""

import sqlite3
import logging
import json
from datetime import date, timedelta
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "tempo (1).db"

# ================================================================
# JOURS FÉRIÉS
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
# CHARGEMENT DONNÉES
# ================================================================

def load_data():
    """Charge actuals, weather_cache, rte_daily depuis la DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Actuals
    actuals = {}
    for row in conn.execute("SELECT date, couleur_reelle FROM actuals WHERE synthetic = 0 ORDER BY date"):
        actuals[row["date"]] = row["couleur_reelle"]

    # Weather
    weather = {}
    for row in conn.execute("SELECT date, temp_moy, temp_min, temp_max, wind_speed, pressure, humidity FROM weather_cache"):
        weather[row["date"]] = dict(row)

    # RTE
    rte = {}
    for row in conn.execute("SELECT * FROM rte_daily WHERE conso_mean_mw IS NOT NULL"):
        rte[row["date"]] = dict(row)

    conn.close()
    logger.info(f"Données: {len(actuals)} actuals, {len(weather)} météo, {len(rte)} RTE")
    return actuals, weather, rte


# ================================================================
# CALCUL REMAINING (état du stock à une date donnée)
# ================================================================

def compute_remaining_at_date(actuals: dict, target_date: date) -> dict:
    """Calcule les jours restants par couleur à une date donnée.

    Simule les informations disponibles pour une prédiction J+2 :
    - À 18h le jour T, on prédit pour T+2.
    - EDF annonce la couleur du jour T le matin (~10h30).
    - Donc à 18h on connaît les actuals jusqu'à T = target_date - 2.
    - On utilise d < target_date - 1 pour simuler ce décalage.

    Fix P7 audit v3.3 : supprime le biais optimiste d'1 jour
    (l'ancien code utilisait d < target_date = actuals jusqu'à D-1).
    """
    # Déterminer la saison
    if target_date.month >= 9:
        season_start = date(target_date.year, 9, 1)
    else:
        season_start = date(target_date.year - 1, 9, 1)
    season_end = date(season_start.year + 1, 8, 31)

    rouge_used = 0
    blanc_used = 0
    bleu_used = 0

    # Simuler J+2 : actuals connus jusqu'à D-2 (= target_date - 2)
    cutoff = target_date - timedelta(days=1)
    d = season_start
    while d < cutoff:
        ds = d.isoformat()
        color = actuals.get(ds)
        if color == "ROUGE":
            rouge_used += 1
        elif color == "BLANC":
            blanc_used += 1
        elif color == "BLEU":
            bleu_used += 1
        d += timedelta(days=1)

    total_days = (season_end - season_start).days + 1

    return {
        "ROUGE": max(0, 22 - rouge_used),
        "BLANC": max(0, 43 - blanc_used),
        "BLEU": max(0, total_days - 22 - 43 - bleu_used),
    }


# ================================================================
# BACKTEST PRINCIPAL
# ================================================================

def run_backtest():
    """Exécute le backtest du prédicteur sur toutes les saisons."""
    from predictor import predict_day, _score_c_nette, _estimate_c_nette_gw
    from config import Config

    actuals, weather, rte = load_data()

    # Identifier les saisons
    seasons = [
        ("2019/2020", date(2019, 9, 1), date(2020, 8, 31)),
        ("2020/2021", date(2020, 9, 1), date(2021, 8, 31)),
        ("2021/2022", date(2021, 9, 1), date(2022, 8, 31)),
        ("2022/2023", date(2022, 9, 1), date(2023, 8, 31)),
        ("2023/2024", date(2023, 9, 1), date(2024, 8, 31)),
        ("2024/2025", date(2024, 9, 1), date(2025, 8, 31)),
        ("2025/2026", date(2025, 9, 1), date(2026, 2, 20)),
    ]

    all_results = []
    results_by_season = {}
    c_nette_analysis = []  # Pour analyser la corrélation C_nette proxy vs réel

    for season_label, s_start, s_end in seasons:
        logger.info(f"\n{'='*70}")
        logger.info(f"SAISON {season_label}")
        logger.info(f"{'='*70}")

        season_results = []
        # Cache des actuals pour la saison (pour clustering)
        actuals_cache = {}
        # Fix P1 audit v3.3 : propager les couleurs prédites (forward clustering)
        # En production, predict_range() construit ce dict. En backtest jour-par-jour
        # on le construit manuellement pour que le clustering ait un signal.
        predicted_colors = {}

        d = s_start
        while d <= s_end:
            ds = d.isoformat()
            actual_color = actuals.get(ds)
            w = weather.get(ds)

            # Besoin de l'actual ET de la météo
            if actual_color is None or w is None:
                d += timedelta(days=1)
                continue

            # Construire les entrées du prédicteur
            remaining = compute_remaining_at_date(actuals, d)

            weather_input = {
                "date": ds,
                "temp_moy": w["temp_moy"],
                "temp_min": w["temp_min"],
                "temp_max": w["temp_max"],
                "wind_speed": w["wind_speed"],
                "pressure": w.get("pressure") or 1015,
                "humidity": w.get("humidity") or 60,
                "source": "archive",
                "forecast_quality": "archive",
            }

            # RTE score (si disponible) — simule J+2 = pas de RTE direct
            # Pour J+2, notre prédicteur utilise C_nette proxy, pas RTE réel.
            # Donc on passe rte_score=None pour simuler un vrai J+2.
            rte_score = None

            # Construire un mini-forecast (jour courant) pour que C_nette fonctionne
            forecasts_mini = [weather_input]

            # Appeler le prédicteur avec forward clustering
            try:
                pred = predict_day(
                    d,
                    weather=weather_input,
                    remaining=remaining,
                    weights=Config.DEFAULT_WEIGHTS,
                    rte_score=rte_score,
                    _actuals_cache=actuals_cache,
                    forecasts=forecasts_mini,
                    target_idx=0,
                    _predicted_colors=predicted_colors,
                )
            except Exception as e:
                logger.warning(f"Erreur predict_day({ds}): {e}")
                d += timedelta(days=1)
                continue

            predicted_color = pred["couleur_predite"]
            score = pred.get("score_risque", 0)

            season_results.append({
                "date": ds,
                "actual": actual_color,
                "predicted": predicted_color,
                "score": round(score, 1),
                "temp_moy": w["temp_moy"],
                "wind_speed": w["wind_speed"],
                "score_temperature": pred.get("score_temperature"),
                "score_budget": pred.get("score_budget"),
                "score_rte": pred.get("score_rte"),
                "score_clustering": pred.get("score_clustering"),
                "remaining_rouge": remaining["ROUGE"],
                "remaining_blanc": remaining["BLANC"],
            })
            all_results.append(season_results[-1])

            # Analyse C_nette proxy vs réel
            rte_day = rte.get(ds)
            if rte_day and rte_day.get("eolien_mean_mw") is not None:
                c_nette_real = (rte_day["conso_mean_mw"] -
                                rte_day["eolien_mean_mw"] -
                                (rte_day.get("solaire_mean_mw") or 0)) / 1000  # GW
                c_nette_proxy = _estimate_c_nette_gw(
                    w["temp_moy"], w["wind_speed"], d.month)
                c_nette_analysis.append({
                    "date": ds,
                    "actual": actual_color,
                    "c_nette_real": round(c_nette_real, 1),
                    "c_nette_proxy": round(c_nette_proxy, 1),
                    "delta": round(c_nette_proxy - c_nette_real, 1),
                    "temp_moy": w["temp_moy"],
                    "wind_speed": w["wind_speed"],
                })

            # Mettre à jour le cache actuals pour le clustering
            actuals_cache[ds] = actual_color
            # Propager la couleur prédite pour le forward clustering
            predicted_colors[ds] = predicted_color
            d += timedelta(days=1)

        # Métriques de la saison
        if season_results:
            metrics = compute_metrics(season_results, season_label)
            results_by_season[season_label] = metrics

    # Métriques globales
    if all_results:
        logger.info(f"\n{'='*70}")
        logger.info("RÉSULTATS GLOBAUX (toutes saisons)")
        logger.info(f"{'='*70}")
        global_metrics = compute_metrics(all_results, "GLOBAL")
        results_by_season["GLOBAL"] = global_metrics

    # Analyse C_nette proxy vs réel
    if c_nette_analysis:
        analyze_c_nette(c_nette_analysis)

    # Analyse des erreurs
    analyze_errors(all_results)

    # Sauvegarder
    with open("backtest_predictor_results.json", "w") as f:
        json.dump({
            "predictions": all_results,
            "metrics": results_by_season,
            "c_nette_analysis_summary": summarize_c_nette(c_nette_analysis) if c_nette_analysis else None,
        }, f, indent=2, ensure_ascii=False)
    logger.info(f"\nRésultats sauvegardés dans backtest_predictor_results.json")

    return results_by_season


# ================================================================
# MÉTRIQUES
# ================================================================

def compute_metrics(predictions: list[dict], label: str) -> dict:
    """Calcule accuracy, precision, recall, F1 par couleur."""
    total = len(predictions)
    correct = sum(1 for p in predictions if p["actual"] == p["predicted"])
    accuracy = correct / total if total else 0

    colors = ["ROUGE", "BLANC", "BLEU"]
    metrics = {"total": total, "correct": correct, "accuracy": round(accuracy * 100, 1)}

    for color in colors:
        tp = sum(1 for p in predictions if p["actual"] == color and p["predicted"] == color)
        fp = sum(1 for p in predictions if p["actual"] != color and p["predicted"] == color)
        fn = sum(1 for p in predictions if p["actual"] == color and p["predicted"] != color)

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
    logger.info(f"  {'':8} | {'ROUGE':>7} {'BLANC':>7} {'BLEU':>7} | actual")
    logger.info(f"  {'-'*8}-+-{'-'*7}-{'-'*7}-{'-'*7}-+------")
    for actual in colors:
        row = f"  {actual:8} |"
        for pred in colors:
            val = confusion[actual][pred]
            row += f" {val:>7}"
        row += f" | {metrics[actual]['count_actual']}"
        logger.info(row)
    logger.info(f"  {'predicted':8} | {metrics['ROUGE']['count_predicted']:>7} {metrics['BLANC']['count_predicted']:>7} {metrics['BLEU']['count_predicted']:>7} |")

    for color in colors:
        m = metrics[color]
        logger.info(f"  {color}: P={m['precision']}%, R={m['recall']}%, "
                    f"F1={m['f1']}% (TP={m['TP']}, FP={m['FP']}, FN={m['FN']})")

    return metrics


# ================================================================
# ANALYSE C_NETTE PROXY VS RÉEL
# ================================================================

def analyze_c_nette(analysis: list[dict]):
    """Compare le proxy C_nette (depuis météo) au C_nette réel (RTE)."""
    deltas = [a["delta"] for a in analysis]
    import statistics
    mean_delta = statistics.mean(deltas)
    median_delta = statistics.median(deltas)
    stdev_delta = statistics.stdev(deltas) if len(deltas) > 1 else 0

    logger.info(f"\n{'='*70}")
    logger.info("ANALYSE C_NETTE PROXY vs RÉEL (RTE)")
    logger.info(f"{'='*70}")
    logger.info(f"  Échantillons: {len(analysis)}")
    logger.info(f"  Delta (proxy - réel): mean={mean_delta:+.1f} GW, "
                f"median={median_delta:+.1f} GW, stdev={stdev_delta:.1f} GW")

    # Par couleur
    for color in ["ROUGE", "BLANC", "BLEU"]:
        color_deltas = [a["delta"] for a in analysis if a["actual"] == color]
        if color_deltas:
            m = statistics.mean(color_deltas)
            logger.info(f"  {color}: mean delta={m:+.1f} GW (n={len(color_deltas)})")

    # Corrélation proxy/réel
    reals = [a["c_nette_real"] for a in analysis]
    proxys = [a["c_nette_proxy"] for a in analysis]
    n = len(reals)
    if n > 2:
        mean_r = sum(reals) / n
        mean_p = sum(proxys) / n
        cov = sum((r - mean_r) * (p - mean_p) for r, p in zip(reals, proxys)) / n
        std_r = (sum((r - mean_r)**2 for r in reals) / n) ** 0.5
        std_p = (sum((p - mean_p)**2 for p in proxys) / n) ** 0.5
        corr = cov / (std_r * std_p) if std_r > 0 and std_p > 0 else 0
        logger.info(f"  Corrélation proxy/réel: r={corr:.3f}")
        logger.info(f"  C_nette réel : mean={mean_r:.1f}, range=[{min(reals):.1f}, {max(reals):.1f}]")
        logger.info(f"  C_nette proxy: mean={mean_p:.1f}, range=[{min(proxys):.1f}, {max(proxys):.1f}]")


def summarize_c_nette(analysis: list[dict]) -> dict:
    """Résumé JSON de l'analyse C_nette."""
    import statistics
    deltas = [a["delta"] for a in analysis]
    reals = [a["c_nette_real"] for a in analysis]
    proxys = [a["c_nette_proxy"] for a in analysis]
    return {
        "n": len(analysis),
        "delta_mean": round(statistics.mean(deltas), 1),
        "delta_median": round(statistics.median(deltas), 1),
        "delta_stdev": round(statistics.stdev(deltas), 1) if len(deltas) > 1 else 0,
        "real_mean": round(statistics.mean(reals), 1),
        "proxy_mean": round(statistics.mean(proxys), 1),
    }


# ================================================================
# ANALYSE DES ERREURS
# ================================================================

def analyze_errors(results: list[dict]):
    """Analyse détaillée des erreurs du prédicteur."""
    logger.info(f"\n{'='*70}")
    logger.info("ANALYSE DES ERREURS")
    logger.info(f"{'='*70}")

    # ROUGE manqués (FN ROUGE — le plus critique)
    rouge_fn = [r for r in results if r["actual"] == "ROUGE" and r["predicted"] != "ROUGE"]
    logger.info(f"\n  --- ROUGE MANQUÉS (FN) : {len(rouge_fn)} ---")
    if rouge_fn:
        for r in sorted(rouge_fn, key=lambda x: x["temp_moy"])[:15]:
            logger.info(f"    {r['date']}: prédit {r['predicted']:6} (score={r['score']:5.1f}, "
                        f"temp={r['temp_moy']:.1f}°C, wind={r['wind_speed']:.0f}km/h, "
                        f"R_rest={r['remaining_rouge']}, B_rest={r['remaining_blanc']})")
        if len(rouge_fn) > 15:
            logger.info(f"    ... et {len(rouge_fn) - 15} autres")

    # Faux ROUGE (FP ROUGE)
    rouge_fp = [r for r in results if r["actual"] != "ROUGE" and r["predicted"] == "ROUGE"]
    logger.info(f"\n  --- FAUX ROUGE (FP) : {len(rouge_fp)} ---")
    if rouge_fp:
        for r in sorted(rouge_fp, key=lambda x: -x["temp_moy"])[:15]:
            logger.info(f"    {r['date']}: réel {r['actual']:6} (score={r['score']:5.1f}, "
                        f"temp={r['temp_moy']:.1f}°C, wind={r['wind_speed']:.0f}km/h, "
                        f"R_rest={r['remaining_rouge']}, B_rest={r['remaining_blanc']})")
        if len(rouge_fp) > 15:
            logger.info(f"    ... et {len(rouge_fp) - 15} autres")

    # BLANC manqués (FN BLANC)
    blanc_fn = [r for r in results if r["actual"] == "BLANC" and r["predicted"] != "BLANC"]
    logger.info(f"\n  --- BLANC MANQUÉS (FN) : {len(blanc_fn)} ---")

    # Distribution des scores par couleur réelle
    logger.info(f"\n  --- DISTRIBUTION SCORES PAR COULEUR RÉELLE ---")
    for color in ["ROUGE", "BLANC", "BLEU"]:
        scores = [r["score"] for r in results if r["actual"] == color]
        if scores:
            import statistics
            logger.info(f"    {color}: mean={statistics.mean(scores):.1f}, "
                        f"median={statistics.median(scores):.1f}, "
                        f"p10={sorted(scores)[len(scores)//10]:.1f}, "
                        f"p90={sorted(scores)[len(scores)*9//10]:.1f}, "
                        f"range=[{min(scores):.1f}, {max(scores):.1f}]")

    # Analyse des erreurs par mois
    logger.info(f"\n  --- ACCURACY PAR MOIS ---")
    month_stats = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        m = date.fromisoformat(r["date"]).month
        month_stats[m]["total"] += 1
        if r["actual"] == r["predicted"]:
            month_stats[m]["correct"] += 1
    month_names = {1: "Jan", 2: "Fév", 3: "Mar", 4: "Avr", 5: "Mai", 6: "Jun",
                   7: "Jul", 8: "Aoû", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Déc"}
    for m in range(1, 13):
        s = month_stats[m]
        if s["total"] > 0:
            acc = s["correct"] / s["total"] * 100
            logger.info(f"    {month_names[m]}: {acc:.1f}% ({s['correct']}/{s['total']})")

    # Analyse des erreurs : ROUGE manqués par tranche de température
    logger.info(f"\n  --- ROUGE MANQUÉS PAR TRANCHE DE TEMPÉRATURE ---")
    temp_bins = [(-5, 0), (0, 3), (3, 5), (5, 7), (7, 10), (10, 15)]
    actual_rouge = [r for r in results if r["actual"] == "ROUGE"]
    for lo, hi in temp_bins:
        in_bin = [r for r in actual_rouge if lo <= r["temp_moy"] < hi]
        if in_bin:
            missed = sum(1 for r in in_bin if r["predicted"] != "ROUGE")
            logger.info(f"    [{lo:>3},{hi:>3})°C: {len(in_bin)} ROUGE, "
                        f"{missed} manqués ({missed/len(in_bin)*100:.0f}%)")

    # Analyse weekend/férié
    logger.info(f"\n  --- ERREURS RÈGLES EDF ---")
    rule_violations = 0
    for r in results:
        d = date.fromisoformat(r["date"])
        pred = r["predicted"]
        # R2: ROUGE sur weekend/férié
        if pred == "ROUGE" and (d.weekday() >= 5 or is_french_holiday(d)):
            rule_violations += 1
            logger.info(f"    VIOLATION R2: {r['date']} ({['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'][d.weekday()]}) "
                        f"prédit ROUGE (holiday={is_french_holiday(d)})")
        # R3: BLANC le dimanche
        if pred == "BLANC" and d.weekday() == 6:
            rule_violations += 1
            logger.info(f"    VIOLATION R3: {r['date']} (Dim) prédit BLANC")
        # R1: ROUGE hors nov-mars
        if pred == "ROUGE" and not (d.month >= 11 or d.month <= 3):
            rule_violations += 1
            logger.info(f"    VIOLATION R1: {r['date']} (mois {d.month}) prédit ROUGE")
    if rule_violations == 0:
        logger.info(f"    Aucune violation de règle EDF détectée ✓")


if __name__ == "__main__":
    run_backtest()
