#!/usr/bin/env python3
"""Audit ML complet du systeme de prediction TempoForecast."""
import sqlite3
from collections import defaultdict

conn = sqlite3.connect("tempo.db")
conn.row_factory = sqlite3.Row

SEP = "=" * 70
print(SEP)
print("AUDIT ML COMPLET — TempoForecast")
print(SEP)

# 1. Distribution des classes dans actuals
print("\n### 1. DISTRIBUTION DES CLASSES (actuals, synthetic=0)")
rows = conn.execute(
    "SELECT couleur_reelle, COUNT(*) as c FROM actuals WHERE synthetic=0 "
    "GROUP BY couleur_reelle"
).fetchall()
total_actuals = sum(r["c"] for r in rows)
for r in rows:
    pct = r["c"] / total_actuals * 100
    print(f"  {r['couleur_reelle']:6s}: {r['c']:4d} ({pct:5.1f}%)")
print(f"  TOTAL : {total_actuals}")

# 2. Confusion matrix from performance table
print("\n### 2. MATRICE DE CONFUSION (backtest J-1)")
rows = conn.execute(
    "SELECT couleur_predite, couleur_reelle, COUNT(*) as c "
    "FROM performance WHERE jours_avance <= 1 "
    "GROUP BY couleur_predite, couleur_reelle"
).fetchall()
matrix = defaultdict(lambda: defaultdict(int))
for r in rows:
    matrix[r["couleur_reelle"]][r["couleur_predite"]] = r["c"]

colors = ["BLEU", "BLANC", "ROUGE"]
header = "REEL vs PREDIT"
print(f"\n  {header:>14s}  {'BLEU':>6s}  {'BLANC':>6s}  {'ROUGE':>6s}  {'Total':>6s}  {'Recall':>7s}")
print("  " + "-" * 56)
for real in colors:
    row_total = sum(matrix[real][p] for p in colors)
    recall = matrix[real][real] / row_total * 100 if row_total else 0
    vals = "  ".join(f"{matrix[real][pred]:6d}" for pred in colors)
    print(f"  {real:>14s}  {vals}  {row_total:6d}  {recall:5.1f}%")

# Precision per predicted class
print(f"\n  {'Precision':>14s}", end="")
for pred in colors:
    col_total = sum(matrix[real][pred] for real in colors)
    prec = matrix[pred][pred] / col_total * 100 if col_total else 0
    print(f"  {prec:5.1f}%", end="")
print()

# Overall accuracy
total_perf = sum(matrix[r][p] for r in colors for p in colors)
correct_perf = sum(matrix[c][c] for c in colors)
print(f"\n  Accuracy globale: {correct_perf}/{total_perf} = {correct_perf/total_perf*100:.1f}%")

# 3. Precision / Recall / F1 par classe
print("\n### 3. PRECISION / RECALL / F1 PAR CLASSE")
metrics = {}
for couleur in colors:
    tp = matrix[couleur][couleur]
    fp = sum(matrix[r][couleur] for r in colors if r != couleur)
    fn = sum(matrix[couleur][p] for p in colors if p != couleur)
    precision = tp / (tp + fp) * 100 if (tp + fp) else 0
    recall = tp / (tp + fn) * 100 if (tp + fn) else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0
    support = tp + fn
    metrics[couleur] = {"P": precision, "R": recall, "F1": f1, "support": support}
    print(f"  {couleur:6s}: P={precision:5.1f}%  R={recall:5.1f}%  F1={f1:5.1f}%  Support={support}")

weighted_f1 = sum(m["F1"] * m["support"] for m in metrics.values()) / total_perf
print(f"\n  Weighted F1: {weighted_f1:.1f}%")
macro_f1 = sum(m["F1"] for m in metrics.values()) / 3
print(f"  Macro F1:    {macro_f1:.1f}%")

# 4. Baseline comparison
print("\n### 4. COMPARAISON VS BASELINES")
bleu_count = sum(matrix["BLEU"][p] for p in colors)
always_bleu_acc = bleu_count / total_perf * 100
model_acc = correct_perf / total_perf * 100
print(f"  Baseline 'Toujours BLEU':  {bleu_count}/{total_perf} = {always_bleu_acc:.1f}%")
print(f"  Modele TempoForecast:      {correct_perf}/{total_perf} = {model_acc:.1f}%")
delta = model_acc - always_bleu_acc
print(f"  Delta:                      {delta:+.1f} pp")
if delta < 0:
    print(f"  *** LE MODELE EST INFERIEUR A LA BASELINE NAIVE ***")
    print(f"  Le modele sacrifie {always_bleu_acc - model_acc:.1f}pp d'accuracy globale")
    print(f"  en echange de la detection ROUGE/BLANC (recall rouge={metrics['ROUGE']['R']:.0f}%)")

# 5. Performance par mois
print("\n### 5. PERFORMANCE PAR MOIS")
rows = conn.execute(
    "SELECT substr(date_cible, 6, 2) as month, "
    "COUNT(*) as total, SUM(correct) as correct "
    "FROM performance WHERE jours_avance <= 1 "
    "GROUP BY month ORDER BY month"
).fetchall()
for r in rows:
    pct = r["correct"] / r["total"] * 100 if r["total"] else 0
    bar = "#" * int(pct / 2)
    print(f"  Mois {r['month']}: {r['correct']:3d}/{r['total']:3d} = {pct:5.1f}% {bar}")

# 6. Sub-scores analysis
print("\n### 6. ANALYSE SUB-SCORES MOYENS vs COULEUR REELLE")
rows = conn.execute("""
    SELECT a.couleur_reelle,
           AVG(p.score_temperature_raw) as avg_temp,
           AVG(p.score_budget_raw) as avg_budget,
           AVG(p.score_weekday_raw) as avg_weekday,
           AVG(p.score_gradient_raw) as avg_gradient,
           AVG(p.score_clustering_raw) as avg_cluster,
           AVG(p.score_rte_raw) as avg_rte,
           AVG(p.score_risque) as avg_composite,
           COUNT(*) as n
    FROM predictions p
    JOIN actuals a ON p.date = a.date
    WHERE a.synthetic = 0 AND p.simulated = 0
      AND (p.score_temperature_raw + p.score_budget_raw) > 0
    GROUP BY a.couleur_reelle
    ORDER BY a.couleur_reelle
""").fetchall()

print(f"\n  {'Color':>8s} {'Temp':>6s} {'Budget':>7s} {'Wkday':>6s} "
      f"{'Grad':>6s} {'Clust':>6s} {'RTE':>5s} {'Compos':>7s} {'N':>5s}")
print("  " + "-" * 62)
for r in rows:
    print(f"  {r['couleur_reelle']:>8s} {r['avg_temp']:6.1f} {r['avg_budget']:7.1f} "
          f"{r['avg_weekday']:6.1f} {r['avg_gradient']:6.1f} {r['avg_cluster']:6.1f} "
          f"{r['avg_rte']:5.1f} {r['avg_composite']:7.1f} {r['n']:5d}")

# 7. Temperature distribution by color
print("\n### 7. TEMPERATURE NATIONALE PAR COULEUR")
rows = conn.execute("""
    SELECT a.couleur_reelle,
           MIN(wc.temp_moy) as t_min, AVG(wc.temp_moy) as t_avg,
           MAX(wc.temp_moy) as t_max, COUNT(*) as n
    FROM actuals a JOIN weather_cache wc ON a.date = wc.date
    WHERE a.synthetic = 0
    GROUP BY a.couleur_reelle
""").fetchall()
for r in rows:
    print(f"  {r['couleur_reelle']:6s}: min={r['t_min']:5.1f}  avg={r['t_avg']:5.1f}  "
          f"max={r['t_max']:5.1f}  (n={r['n']})")

# 8. Temperature range analysis
print("\n### 8. ZONE CRITIQUE : distribution temperature par couleur")
ranges = [("<0C", -99, 0), ("0-2C", 0, 2), ("2-5C", 2, 5),
          ("5-8C", 5, 8), ("8-12C", 8, 12), (">12C", 12, 99)]
for label, tmin, tmax in ranges:
    row = conn.execute("""
        SELECT a.couleur_reelle, COUNT(*) as c
        FROM actuals a JOIN weather_cache wc ON a.date = wc.date
        WHERE a.synthetic = 0 AND wc.temp_moy >= ? AND wc.temp_moy < ?
        GROUP BY a.couleur_reelle
    """, (tmin, tmax)).fetchall()
    counts = {r["couleur_reelle"]: r["c"] for r in row}
    total = sum(counts.values())
    if total == 0:
        continue
    bl_pct = counts.get("BLEU", 0) / total * 100
    w_pct = counts.get("BLANC", 0) / total * 100
    r_pct = counts.get("ROUGE", 0) / total * 100
    print(f"  {label:>5s}: B={counts.get('BLEU',0):3d}({bl_pct:3.0f}%) "
          f"W={counts.get('BLANC',0):3d}({w_pct:3.0f}%) "
          f"R={counts.get('ROUGE',0):3d}({r_pct:3.0f}%) n={total}")

# 9. Score zones vs reality
print("\n### 9. SCORES COMPOSITES vs COULEUR REELLE")
zones = [(0, 35, "0-35 (predit BLEU)"), (35, 65, "35-65 (predit BLANC)"),
         (65, 101, "65-100 (predit ROUGE)")]
for smin, smax, label in zones:
    row = conn.execute("""
        SELECT a.couleur_reelle, COUNT(*) as c
        FROM predictions p JOIN actuals a ON p.date = a.date
        WHERE a.synthetic = 0 AND p.simulated = 0
          AND p.score_risque >= ? AND p.score_risque < ?
        GROUP BY a.couleur_reelle
    """, (smin, smax)).fetchall()
    counts = {r["couleur_reelle"]: r["c"] for r in row}
    total = sum(counts.values())
    if total == 0:
        continue
    parts = []
    for c in colors:
        n = counts.get(c, 0)
        pct = n / total * 100
        parts.append(f"{c}={n}({pct:.0f}%)")
    print(f"  {label:25s}: {' '.join(parts)}  total={total}")

# 10. Type d'erreur dominant
print("\n### 10. ERREURS PAR TYPE")
err_types = conn.execute("""
    SELECT couleur_predite, couleur_reelle, COUNT(*) as c
    FROM performance WHERE jours_avance <= 1 AND correct = 0
    GROUP BY couleur_predite, couleur_reelle
    ORDER BY c DESC
""").fetchall()
total_errors = sum(r["c"] for r in err_types)
for r in err_types:
    pct = r["c"] / total_errors * 100
    print(f"  Predit {r['couleur_predite']:6s} -> Reel {r['couleur_reelle']:6s}: "
          f"{r['c']:4d} ({pct:5.1f}% des erreurs)")

# 11. Poids actuels
print("\n### 11. POIDS ACTUELS")
wrow = conn.execute(
    "SELECT weights_json FROM weights_history ORDER BY id DESC LIMIT 1"
).fetchone()
if wrow:
    import json
    weights = json.loads(wrow["weights_json"])
    for k, v in sorted(weights.items(), key=lambda x: -x[1]):
        bar = "#" * int(v * 100)
        print(f"  {k:20s}: {v:.2f} {bar}")

# 12. Feature discriminant power (difference between ROUGE avg and BLEU avg)
print("\n### 12. POUVOIR DISCRIMINANT DES FEATURES (ecart ROUGE - BLEU)")
all_scores = conn.execute("""
    SELECT a.couleur_reelle,
           p.score_temperature_raw, p.score_budget_raw, p.score_weekday_raw,
           p.score_gradient_raw, p.score_clustering_raw, p.score_rte_raw
    FROM predictions p JOIN actuals a ON p.date = a.date
    WHERE a.synthetic = 0 AND p.simulated = 0
      AND (p.score_temperature_raw + p.score_budget_raw) > 0
""").fetchall()

by_color = defaultdict(lambda: defaultdict(list))
features = ["score_temperature_raw", "score_budget_raw", "score_weekday_raw",
            "score_gradient_raw", "score_clustering_raw", "score_rte_raw"]
for row in all_scores:
    for f in features:
        by_color[row["couleur_reelle"]][f].append(row[f])

print(f"\n  {'Feature':>20s} {'Moy BLEU':>9s} {'Moy BLANC':>10s} {'Moy ROUGE':>10s} "
      f"{'R-B delta':>10s} {'Discrimin':>10s}")
print("  " + "-" * 72)
for f in features:
    fname = f.replace("score_", "").replace("_raw", "")
    avg_bl = sum(by_color["BLEU"][f]) / len(by_color["BLEU"][f]) if by_color["BLEU"][f] else 0
    avg_w = sum(by_color["BLANC"][f]) / len(by_color["BLANC"][f]) if by_color["BLANC"][f] else 0
    avg_r = sum(by_color["ROUGE"][f]) / len(by_color["ROUGE"][f]) if by_color["ROUGE"][f] else 0
    delta = avg_r - avg_bl
    # Discrimination = delta normalized by std
    all_vals = by_color["BLEU"][f] + by_color["BLANC"][f] + by_color["ROUGE"][f]
    mean_all = sum(all_vals) / len(all_vals)
    std_all = (sum((v - mean_all)**2 for v in all_vals) / len(all_vals)) ** 0.5
    discrim = delta / std_all if std_all > 0 else 0
    print(f"  {fname:>20s} {avg_bl:9.1f} {avg_w:10.1f} {avg_r:10.1f} "
          f"{delta:+10.1f} {discrim:+10.2f}")

# 13. Weekday analysis
print("\n### 13. PERFORMANCE PAR JOUR DE SEMAINE")
rows = conn.execute("""
    SELECT date_cible, correct
    FROM performance WHERE jours_avance <= 1
""").fetchall()
dow_names = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
from collections import Counter
dow_total = Counter()
dow_correct = Counter()
for r in rows:
    d = date.fromisoformat(r["date_cible"])
    dow = d.weekday()  # 0=Monday, 6=Sunday
    dow_total[dow] += 1
    dow_correct[dow] += r["correct"]
for dow in range(7):
    total = dow_total[dow]
    correct = dow_correct[dow]
    pct = correct / total * 100 if total else 0
    bar = "#" * int(pct / 2)
    print(f"  {dow_names[dow]}: {correct:3d}/{total:3d} = {pct:5.1f}% {bar}")

conn.close()
print(f"\n{SEP}")
print("FIN DE L'AUDIT ML")
print(SEP)
