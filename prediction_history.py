"""Historique public des prévisions (page /historique-previsions et export CSV).

Principe (audit 2026-09-29, section 6) : chaque prévision est montrée telle
qu'elle a été émise N jours avant (N = 1 à 15), avec la température moyenne
prévue utilisée pour ce calcul, puis comparée à la couleur officielle publiée
par EDF. Aucune sélection de jours.

Sources et exclusions :
- predictions : lignes réelles uniquement (simulated = 0, cycle_id hors
  « backtest », émission >= Config.PREDICTION_START_DATE). La couleur émise est
  couleur_originale quand la ligne a été confirmée (confirm_prediction écrase
  couleur_predite et les probabilités), sinon couleur_predite. Horizon N = date
  cible - date d'émission (même règle que performance.jours_avance). Plusieurs
  lignes pour un même (date, N) : la dernière émise ce jour-là fait foi.
- performance : évaluations figées (jamais purgées) ; lignes « backtest »
  exclues. Sert de source pour les jours dont la prédiction détaillée n'est plus
  en base (purge historique > 90 jours, supprimée le 2026-09-29) : couleur seule,
  température « n/d ».
- actuals : couleurs officielles réelles (synthetic = 0).
- weather_cache : température moyenne observée (dernier relevé du jour).
- weather_forecast_log : température prévue si la ligne de prédiction ne l'a pas.

Pas d'appel à performance_tracker.get_daily_recap() (vue admin, autres
agrégats) ; les deux partagent emission_horizon() et is_backtest() pour ne pas
diverger sur l'horizon (date d'émission) ni sur l'exclusion des backtests. SQL portable SQLite/PostgreSQL, agrégation en Python.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import Config

logger = logging.getLogger(__name__)

HORIZONS = tuple(range(1, 16))            # J-1 ... J-15
GRID_HORIZONS = tuple(range(15, 0, -1))   # ordre d'affichage : J-15 ... J-1
RELIABLE = (2, 3, 4, 5)                   # zone fiable (site_facts.HORIZON_FIABLE)
PCT_MIN_EFFECTIF = 20                     # en dessous : effectifs bruts seulement
COLORS = ("BLEU", "BLANC", "ROUGE")
CACHE_TTL_SECONDS = 300

NA = "n/a"  # prévision impossible : service pas encore lancé à J-N

_cache: dict = {"data": None, "ts": 0.0}


def invalidate_history_cache() -> None:
    """Vide le cache (appelé avec les autres caches de performance)."""
    _cache["data"] = None
    _cache["ts"] = 0.0


def today_paris() -> date:
    return datetime.now(tz=ZoneInfo("Europe/Paris")).date()


def season_start_year(d: date) -> int:
    """Saison Tempo = 1er septembre -> 31 août ; renvoie l'année de début."""
    return d.year if d.month >= 9 else d.year - 1


def season_label(start_year: int) -> str:
    return f"{start_year}-{start_year + 1}"


def parse_season_label(label: str) -> int | None:
    """'2025-2026' -> 2025 ; None si format invalide."""
    if not isinstance(label, str) or len(label) != 9 or label[4] != "-":
        return None
    a, b = label[:4], label[5:]
    if not (a.isdigit() and b.isdigit()) or int(b) != int(a) + 1:
        return None
    return int(a)


def _start_date() -> str:
    return getattr(Config, "PREDICTION_START_DATE", None) or "0000-00-00"


def _pct(num: int, den: int) -> int | None:
    """Pourcentage arrondi, uniquement si l'effectif atteint le seuil."""
    if den < PCT_MIN_EFFECTIF or den == 0:
        return None
    return round(100 * num / den)


def is_backtest(value) -> bool:
    """cycle_id ou évaluation (performance.contexte_meteo) issus d'un backtest."""
    return str(value or "").strip().lower().startswith("backtest")


_is_backtest = is_backtest  # alias historique


def emission_horizon(target, emitted) -> int | None:
    """Horizon réel N = date cible - date d'émission, en jours.

    Même règle que performance.jours_avance ; le libellé predictions.horizon
    n'est pas fiable (il peut diverger de l'écart réel). Accepte date/datetime
    ou chaîne ISO (seuls les 10 premiers caractères comptent). None si illisible.
    Fonction commune à cette page et à performance_tracker.get_daily_recap().
    """
    try:
        t = target if isinstance(target, date) else date.fromisoformat(str(target)[:10])
        e = emitted if isinstance(emitted, date) else date.fromisoformat(str(emitted)[:10])
    except (TypeError, ValueError):
        return None
    if isinstance(t, datetime):
        t = t.date()
    if isinstance(e, datetime):
        e = e.date()
    return (t - e).days


def _num(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# ================================================================
# Chargement (5 requêtes simples, agrégation en Python, cache 5 min)
# ================================================================

_PROBA_COL = {"BLEU": "probabilite_bleu", "BLANC": "probabilite_blanc", "ROUGE": "probabilite_rouge"}


def _load_preds(conn, start: str) -> tuple[dict, dict]:
    """({(date_cible, N): {"couleur", "ts", "emise_le", "temp", "proba"}}, info).

    info["emissions"] : jours où au moins un calcul réel a été enregistré (toutes
    lignes réelles, quel que soit l'horizon) ; sert à repérer les jours sans calcul.
    info["publiee_avant_calcul"] : dates cibles dont la ligne « la veille » a été
    enregistrée directement comme couleur officielle (confirmed = 1 sans
    couleur_originale) : EDF avait déjà publié la couleur avant le calcul de 18 h,
    predict_range() n'a donc pas émis de prévision pour ce jour (comportement voulu).
    """
    preds: dict = {}
    backtest_keys: set = set()
    emissions: set = set()
    publiee: set = set()
    # 1) Évaluations figées (table performance) : couleur émise, jamais purgée.
    for p in conn.execute(
        "SELECT date_prediction, date_cible, jours_avance, couleur_predite, contexte_meteo "
        "FROM performance WHERE date_prediction >= ? AND jours_avance >= 1 AND jours_avance <= 15",
        (start,),
    ).fetchall():
        try:
            n = int(p["jours_avance"])
            target = date.fromisoformat(str(p["date_cible"])[:10])
            emitted = str(p["date_prediction"])[:10]
            if emission_horizon(target, emitted) != n:
                continue
        except (TypeError, ValueError):
            continue
        if not _is_backtest(p["contexte_meteo"]):
            emissions.add(emitted)
        if _is_backtest(p["contexte_meteo"]):
            # Une prédiction de backtest importée sans cycle_id (db_sync n'exporte pas
            # cette colonne) est reconnue par son évaluation « backtest ».
            backtest_keys.add((target.isoformat(), n))
            continue
        if p["couleur_predite"] not in COLORS:
            continue
        preds[(target.isoformat(), n)] = {"couleur": p["couleur_predite"], "ts": emitted,
                                         "emise_le": emitted, "temp": None, "proba": None}

    # 2) Prédictions détaillées (couleur, température prévue, probabilité).
    live: dict = {}
    for r in conn.execute(
        "SELECT date, couleur_predite, couleur_originale, confirmed, cycle_id, "
        "timestamp_prediction, temp_moy_prevue, probabilite_bleu, probabilite_blanc, "
        "probabilite_rouge FROM predictions WHERE simulated = 0 AND timestamp_prediction >= ?",
        (start,),
    ).fetchall():
        if _is_backtest(r["cycle_id"]):
            continue
        ts = str(r["timestamp_prediction"] or "")
        try:
            emitted = date.fromisoformat(ts[:10])
            target = date.fromisoformat(str(r["date"])[:10])
        except ValueError:
            continue
        n = emission_horizon(target, emitted)
        if emitted.isoformat() < start:
            continue
        emissions.add(emitted.isoformat())
        if n not in HORIZONS:
            continue
        couleur, proba = r["couleur_predite"], None
        if r["confirmed"] and n == 1 and not r["couleur_originale"]:
            publiee.add(target.isoformat())
        if r["confirmed"]:
            # confirm_prediction() écrase couleur et probabilités : couleur émise =
            # couleur_originale ; perdue -> l'évaluation figée (performance) fait foi.
            couleur = r["couleur_originale"] or None
        elif couleur in COLORS:
            p = _num(r[_PROBA_COL[couleur]])
            proba = round(p * 100) if p is not None and 0 < p <= 1 else None
        key = (target.isoformat(), n)
        if key in backtest_keys:
            continue
        prev = live.get(key)
        if prev is None or ts > prev["ts"]:
            live[key] = {"couleur": couleur, "ts": ts, "emise_le": ts[:16],
                         "temp": _num(r["temp_moy_prevue"]), "proba": proba}
    for key, v in live.items():
        if v["couleur"] in COLORS:
            preds[key] = v
        elif key in preds and v["temp"] is not None:
            preds[key]["temp"] = v["temp"]  # couleur figée + température de la ligne

    # 3) Température prévue manquante : journal des prévisions météo du jour d'émission.
    log = None
    if any(v["temp"] is None for v in preds.values()):
        log = _load_forecast_log(conn, start)
        for (d, _n), v in preds.items():
            if v["temp"] is None:
                v["temp"] = log.get((d, v["emise_le"][:10]))
    return preds, {"emissions": emissions, "publiee_avant_calcul": publiee, "forecast_log": log}


def _load_forecast_log(conn, start: str) -> dict:
    """{(date cible, jour d'émission): température moyenne prévue} (weather_forecast_log)."""
    log = {}
    for w in conn.execute(
        "SELECT target_date, forecast_date, temp_moy FROM weather_forecast_log "
        "WHERE target_date >= ? AND temp_moy IS NOT NULL", (start,),
    ).fetchall():
        log[(str(w["target_date"])[:10], str(w["forecast_date"])[:10])] = _num(w["temp_moy"])
    return log


def _load_actuals(conn, start: str) -> tuple[dict, dict, dict]:
    """(couleurs officielles, dates de confirmation, températures observées)."""
    actuals, confirmed_at = {}, {}
    for a in conn.execute(
        "SELECT date, couleur_reelle, timestamp_confirmation FROM actuals "
        "WHERE synthetic = 0 AND date >= ?", (start,),
    ).fetchall():
        d = str(a["date"])[:10]
        if a["couleur_reelle"] in COLORS:
            actuals[d] = a["couleur_reelle"]
            confirmed_at[d] = str(a["timestamp_confirmation"] or "")[:10]
    observed: dict = {}
    seen: dict = {}
    for w in conn.execute(
        "SELECT date, temp_moy, fetched_at FROM weather_cache "
        "WHERE date >= ? AND temp_moy IS NOT NULL", (start,),
    ).fetchall():
        d, f = str(w["date"])[:10], str(w["fetched_at"] or "")
        if d not in seen or f > seen[d]:
            seen[d] = f
            observed[d] = _num(w["temp_moy"])
    return actuals, confirmed_at, observed


# ================================================================
# Agrégation
# ================================================================

def _empty_color_stats() -> dict:
    return {"reels": 0, "reels_emis": 0, "annonces": 0, "sans_prevision": 0,
            "alertes": 0, "alertes_justes": 0}


def _horizon_stats(days: list[dict], n: int) -> dict:
    """Effectifs pour un horizon N sur une liste de jours (déjà bornée à la saison).

    Décision fondateur du 2026-09-30 : les taux se calculent sur les prévisions
    RÉELLEMENT ÉMISES (un jour sans prévision n'est ni juste ni erroné) ; la
    couverture (jours sans prévision) est publiée à part. Le repère « dire bleu
    tous les jours » est calculé sur les mêmes jours (ceux qui ont une prévision).
    """
    st = {c: _empty_color_stats() for c in ("ROUGE", "BLANC")}
    eligible = justes = bleus = sans = 0
    for d in days:
        cell = d["cells"][n]
        if cell == NA:
            continue
        eligible += 1
        reel = d["couleur"]
        prevu = cell["couleur"] if cell else None
        sans += prevu is None
        justes += prevu == reel
        bleus += reel == "BLEU" and prevu is not None
        for c, s in st.items():
            if reel == c:
                s["reels"] += 1
                if prevu == c:
                    s["annonces"] += 1
                elif prevu is None:
                    s["sans_prevision"] += 1
            if prevu == c:
                s["alertes"] += 1
                s["alertes_justes"] += reel == c
    for s in st.values():
        s["reels_emis"] = s["reels"] - s["sans_prevision"]
        s["rappel_pct"] = _pct(s["annonces"], s["reels_emis"])
        s["precision_pct"] = _pct(s["alertes_justes"], s["alertes"])
    emises = eligible - sans
    return {
        "n": n, "fiable": n in RELIABLE, "ROUGE": st["ROUGE"], "BLANC": st["BLANC"],
        "jours": eligible, "sans_prevision": sans, "emises": emises,
        "justes": justes, "erronees": emises - justes, "justes_pct": _pct(justes, emises),
        "toujours_bleu": bleus, "toujours_bleu_pct": _pct(bleus, emises),
    }


def _bref(fiables: list[dict]) -> dict:
    """Bloc « En bref » : effectifs J-2 à J-5 cumulés (une prévision par jour et par délai)."""
    tot = {"jours": 0, "emises": 0, "justes": 0, "erronees": 0, "sans_prevision": 0, "toujours_bleu": 0}
    col = {c: {"reels": 0, "reels_emis": 0, "annonces": 0, "alertes": 0, "alertes_justes": 0}
           for c in ("ROUGE", "BLANC")}
    for h in fiables:
        for k in tot:
            tot[k] += h[k]
        for c, s in col.items():
            for k in s:
                s[k] += h[c][k]
    for s in col.values():
        s["rappel_pct"] = _pct(s["annonces"], s["reels_emis"])
        s["precision_pct"] = _pct(s["alertes_justes"], s["alertes"])
    return {**tot, "justes_pct": _pct(tot["justes"], tot["emises"]),
            "toujours_bleu_pct": _pct(tot["toujours_bleu"], tot["emises"]), **col}


def _couleurs_voisines(cells: dict, reel: str | None = None) -> dict[int, str]:
    """Couleur affichée dans les cases sans prévision (décisions fondateur des 2026-09-30,
    rendu seulement) : {N: couleur}.

    - J-1 vide (EDF avait publié la couleur avant notre calcul de 18 h, ou jour sans
      calcul) : couleur officielle ``reel``.
    - Autres délais : sur la même ligne (même date cible), prévision émise la plus
      proche avec un délai plus petit (la plus récente), à défaut la plus proche avec
      un délai plus grand. Deux voisines de même couleur donnent donc cette couleur.
    Ces cases ne sont JAMAIS des prévisions : cells[N] reste None (couverture « sans
    prévision »), aucun taux ni effectif ne les compte, rien n'est écrit en base.
    """
    out = {}
    for n in HORIZONS:
        if cells.get(n) is not None:
            continue
        if n == 1:
            if reel in COLORS:
                out[n] = reel
            continue
        apres = next((cells[m] for m in range(n - 1, 0, -1)
                      if isinstance(cells.get(m), dict)), None)
        avant = next((cells[m] for m in range(n + 1, max(HORIZONS) + 1)
                      if isinstance(cells.get(m), dict)), None)
        voisine = apres or avant
        if voisine:
            out[n] = voisine["couleur"]
    return out


def _temp_voisine(cells: dict, n: int) -> float | None:
    """Température d'une case sans prévision quand aucune météo n'a été enregistrée ce
    jour-là (décision fondateur du 2026-10-01 : aucune température manquante) : celle de
    la prévision émise la plus proche sur la même ligne, délai plus petit d'abord (même
    ordre que la couleur), sinon délai plus grand. Valeur réellement prévue, jamais
    interpolée ; None si la ligne n'a aucune température prévue."""
    ordre = list(range(n - 1, 0, -1)) + list(range(n + 1, max(HORIZONS) + 1))
    for m in ordre:
        c = cells.get(m)
        if isinstance(c, dict) and c.get("temp") is not None:
            return c["temp"]
    return None


def _jours_sans_calcul(emissions: set, start_d: date | None, today: date) -> list[str]:
    """Jours d'émission sans AUCUN calcul enregistré, de la première émission à hier.

    Aujourd'hui est exclu (le calcul de 18 h n'a peut-être pas encore eu lieu).
    Aucune prévision n'est jamais recréée pour ces jours (règle zéro invention).
    """
    if not emissions:
        return []
    try:
        first = min(date.fromisoformat(e) for e in emissions)
    except ValueError:
        return []
    if start_d and first < start_d:
        first = start_d
    out, d = [], first
    while d < today:
        if d.isoformat() not in emissions:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _build(conn, today: date) -> dict:
    start = _start_date()
    preds, info = _load_preds(conn, start)
    actuals, confirmed_at, observed = _load_actuals(conn, start)
    start_d = date.fromisoformat(start) if start[:4] != "0000" else None
    sans_calcul = _jours_sans_calcul(info["emissions"], start_d, today)
    sans_calcul_set = set(sans_calcul)
    publiee = info["publiee_avant_calcul"]
    forecast_log = info.get("forecast_log")

    by_season: dict[int, list] = {}
    for d_iso in sorted(actuals):
        d = date.fromisoformat(d_iso)
        if d > today:
            continue  # uniquement les jours passés ou aujourd'hui
        cells, causes = {}, {}
        for n in HORIZONS:
            if start_d and d - timedelta(days=n) < start_d:
                cells[n] = NA
            else:
                p = preds.get((d_iso, n))
                cells[n] = ({"couleur": p["couleur"], "emise_le": p["emise_le"],
                             "temp": p["temp"], "proba": p["proba"]} if p else None)
                if not p:
                    e_iso = (d - timedelta(days=n)).isoformat()
                    if e_iso in sans_calcul_set:
                        causes[n] = ("interruption", e_iso)
                    elif n == 1 and d_iso in publiee:
                        causes[n] = ("publiee", e_iso)
        if all(c == NA for c in cells.values()):
            continue  # service pas encore lancé pour ce jour, quel que soit l'horizon
        remplies = {}
        for n, couleur in _couleurs_voisines(cells, actuals[d_iso]).items():
            if forecast_log is None:
                forecast_log = _load_forecast_log(conn, start)
            # Température prévue enregistrée ce jour-là, sinon celle de la prévision
            # voisine (jamais interpolée ni inventée).
            e_iso = (d - timedelta(days=n)).isoformat()
            temp = forecast_log.get((d_iso, e_iso))
            if temp is None:
                temp = _temp_voisine(cells, n)
            remplies[n] = {"couleur": couleur, "temp": temp,
                           "source": "edf" if n == 1 else "voisines"}
        by_season.setdefault(season_start_year(d), []).append(
            {"date": d_iso, "couleur": actuals[d_iso], "cells": cells, "causes": causes,
             "remplies": remplies,
             "temp_observee": observed.get(d_iso),
             "confirme_le": confirmed_at.get(d_iso, "")})

    seasons = {}
    last_eval = None
    for y, days in by_season.items():
        # Une saison n'est proposée que si au moins une prévision réelle existe.
        if not any(isinstance(c, dict) for d in days for c in d["cells"].values()):
            continue
        dates_conf = [d["confirme_le"] for d in days if d["confirme_le"]]
        lm = max(dates_conf) if dates_conf else days[-1]["date"]
        last_eval = max(last_eval, lm) if last_eval else lm
        seasons[season_label(y)] = {
            "label": season_label(y),
            "days": days,
            "first_date": days[0]["date"],
            "last_date": days[-1]["date"],
            "last_evaluation": lm,
            "horizons": [_horizon_stats(days, n) for n in HORIZONS],
            "jours_sans_calcul": [e for e in sans_calcul
                                  if season_start_year(date.fromisoformat(e)) == y],
        }
    return {"seasons": seasons, "last_evaluation": last_eval}


def season_is_complete(label: str, today: date) -> bool:
    """Saison terminée : son 31 août est passé."""
    y = parse_season_label(label)
    return y is not None and today > date(y + 1, 8, 31)


def pct_visible(label: str, today: date) -> bool:
    """Décision fondateur du 2026-09-30 : pas de pourcentage pour la saison en cours
    avant le 1er novembre (avant, presque tous les jours sont bleus et le taux ne
    veut pas dire grand-chose). Saisons passées : toujours visibles."""
    y = parse_season_label(label)
    if y is None:
        return False
    return season_is_complete(label, today) or today >= date(y, 11, 1)


def season_summary(data: dict, label: str) -> dict | None:
    """Résumé 2 à 5 jours avant d'une saison : effectifs, taux sur prévisions émises,
    couverture et repère « toujours bleu ». None si aucune prévision émise."""
    s = data.get("seasons", {}).get(label)
    if not s:
        return None
    b = _bref([h for h in s["horizons"] if h["fiable"]])
    if not b["emises"]:
        return None
    return {**b, "label": label, "jours_sans_calcul": len(s.get("jours_sans_calcul", []))}


def last_complete_season_summary(data: dict, today: date | None = None) -> dict | None:
    """Taux mis en avant sur tout le site (décision fondateur du 2026-09-30) :
    celui de la DERNIÈRE SAISON COMPLÈTE (31 août passé), calculé, jamais en dur."""
    today = today or today_paris()
    for label in sorted(data.get("seasons", {}), reverse=True):
        if season_is_complete(label, today):
            summary = season_summary(data, label)
            if summary and summary["justes_pct"] is not None:
                return summary
    return None


def get_history(force: bool = False) -> dict:
    """Historique complet, toutes saisons (cache mémoire 5 min).

    Ne lève jamais : base absente ou illisible -> {"seasons": {}, ...}.
    """
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < CACHE_TTL_SECONDS:
        return _cache["data"]
    try:
        from database import get_db
        conn = get_db()
        try:
            data = _build(conn, today_paris())
        finally:
            conn.close()
    except Exception as e:  # base pas prête, table absente, etc.
        logger.warning(f"[Historique] Données indisponibles : {e}")
        return {"seasons": {}, "last_evaluation": None, "error": True}
    _cache["data"] = data
    _cache["ts"] = now
    return data


# ================================================================
# Export CSV (Excel FR : séparateur « ; », virgule décimale, UTF-8 avec BOM)
# ================================================================

def _fr_temp(t: float | None) -> str:
    return "" if t is None else f"{t:.1f}".replace(".", ",")


CSV_HEADER = (
    ["saison", "date", "couleur_officielle", "temp_observee"]
    + [col for n in GRID_HORIZONS
       for col in (f"prevision_J-{n}", f"temp_prevue_J-{n}", f"emise_le_J-{n}")]
)


VOISINES_CSV = "non enregistrée (couleur des prévisions voisines)"
VOISINES_TXT = "couleur des prévisions voisines, calcul non enregistré ce jour-là"
EDF_CSV = "non enregistrée (couleur publiée par EDF avant notre calcul de 18 h)"
EDF_TXT = "couleur publiée par EDF avant notre calcul de 18 h"


def to_csv(data: dict) -> str:
    """Toutes les saisons, mêmes lignes que la grille jour par jour.

    Prévision absente : cellules vides. Service pas encore lancé à cet horizon : n/a.
    Température prévue non conservée : vide. Case sans prévision reprise pour
    l'affichage : couleur, température réelle si connue, et « emise_le » = EDF_CSV
    (J-1, couleur officielle) ou VOISINES_CSV à la place d'une date d'émission.
    """
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    for label in sorted(data.get("seasons", {})):
        for d in data["seasons"][label]["days"]:
            row = [label, d["date"], d["couleur"], _fr_temp(d["temp_observee"])]
            for n in GRID_HORIZONS:
                c = d["cells"][n]
                if c == NA:
                    row += [NA, NA, NA]
                elif c:
                    row += [c["couleur"], _fr_temp(c["temp"]), c["emise_le"]]
                elif n in d.get("remplies", {}):
                    v = d["remplies"][n]
                    row += [v["couleur"], _fr_temp(v["temp"]),
                            EDF_CSV if v.get("source") == "edf" else VOISINES_CSV]
                else:
                    row += ["", "", ""]
            w.writerow(row)
    return buf.getvalue()


def to_csv_bytes(data: dict) -> bytes:
    """CSV encodé en UTF-8 précédé du BOM (ouverture correcte dans Excel)."""
    import codecs
    return codecs.BOM_UTF8 + to_csv(data).encode("utf-8")


# ================================================================
# Modèle de vue de la page HTML
# ================================================================

NOMS = {"BLEU": "Bleu", "BLANC": "Blanc", "ROUGE": "Rouge"}


def season_path(label: str, current_label: str) -> str:
    return "/historique-previsions" if label == current_label else f"/historique-previsions/{label}"


def _temp_label(t: float | None) -> str:
    """Même format que les cartes du calendrier : « 16,1° », vrai signe moins."""
    import site_facts
    return site_facts.fr_temp(t) or "n/d"


def _grid_cell(cell, n: int, reel: str, cause: tuple | None = None,
               remplie: dict | None = None) -> dict:
    import site_facts
    avant = "la veille" if n == 1 else f"{n} jours avant"
    if cell == NA:
        return {"state": "na", "title": f"{avant.capitalize()} : service pas encore lancé"}
    if not cell and remplie:
        # Rendu identique à une prévision émise (coche ou croix), seule l'infobulle diffère.
        juste = remplie["couleur"] == reel
        t = site_facts.fr_temp(remplie["temp"])
        txt = EDF_TXT if remplie.get("source") == "edf" else VOISINES_TXT
        parts = [f"{avant.capitalize()} : {NOMS[remplie['couleur']]}, {'juste' if juste else 'erroné'}",
                 txt[0].upper() + txt[1:]]
        if t is not None:
            parts.append(f"température moyenne prévue {t[:-1]} °C")
        return {"state": "ok", "voisine": True, "source": remplie.get("source", "voisines"),
                "couleur": remplie["couleur"], "juste": juste,
                "temp": t or "", "has_temp": t is not None, "title": " ; ".join(parts)}
    if not cell:
        if cause and cause[0] == "interruption":
            e = site_facts.fr_date(date.fromisoformat(cause[1]), with_weekday=False)
            title = f"{avant.capitalize()} : pas de prévision, service interrompu le {e}"
        elif cause and cause[0] == "publiee":
            title = (f"{avant.capitalize()} : pas de prévision, couleur déjà publiée par EDF "
                     "avant notre calcul de 18 h")
        else:
            title = f"{avant.capitalize()} : aucune prévision enregistrée"
        return {"state": "absente", "cause": cause[0] if cause else None, "title": title}
    juste = cell["couleur"] == reel
    parts = [f"Prévu {avant} : {NOMS[cell['couleur']]}, {'juste' if juste else 'erroné'}"]
    if cell["proba"] is not None:
        parts.append(f"probabilité {cell['proba']} %")
    t = site_facts.fr_temp(cell["temp"])
    parts.append("température prévue non conservée" if t is None
                 else f"température moyenne prévue {t[:-1]} °C")
    return {"state": "ok", "couleur": cell["couleur"], "juste": juste,
            "temp": _temp_label(cell["temp"]), "has_temp": t is not None,
            "title": " ; ".join(parts)}


def _mask_pcts(obj):
    """Retire les pourcentages (clés *_pct) d'un bloc de statistiques."""
    if isinstance(obj, dict):
        return {k: (None if k.endswith("_pct") else _mask_pcts(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_pcts(v) for v in obj]
    return obj


def _fr_liste_dates(isos: list[str]) -> str:
    """['2026-09-15', '2026-09-17', '2026-10-02'] -> '15 et 17 septembre 2026, 2 octobre 2026'
    (dates groupées par mois, séparateur final « et »)."""
    import site_facts
    groups: list[tuple[tuple[int, int], list[int]]] = []
    for iso in isos:
        d = date.fromisoformat(iso)
        if groups and groups[-1][0] == (d.year, d.month):
            groups[-1][1].append(d.day)
        else:
            groups.append(((d.year, d.month), [d.day]))
    parts = []
    for (y, m), days in groups:
        nums = ["1er" if x == 1 else str(x) for x in days]
        txt = nums[0] if len(nums) == 1 else ", ".join(nums[:-1]) + " et " + nums[-1]
        parts.append(f"{txt} {site_facts.MOIS_FR[m]} {y}")
    return " ; ".join(parts)


def page_context(data: dict, label: str, current_label: str, today: date | None = None) -> dict:
    """Contexte Jinja pour une saison (dates formatées, liens de saison)."""
    import site_facts
    today = today or today_paris()

    def short(d_iso: str) -> str:
        return site_facts.fr_date(date.fromisoformat(d_iso), with_weekday=False)

    seasons = data.get("seasons", {})
    labels = sorted(set(seasons) | {current_label}, reverse=True)
    season_links = [
        {"label": s, "path": season_path(s, current_label), "active": s == label}
        for s in labels
    ]
    s = seasons.get(label)
    view = None
    show_pct = pct_visible(label, today)
    if s:
        rows = []
        for d in reversed(s["days"]):  # plus récent en premier
            dd = date.fromisoformat(d["date"])
            causes = d.get("causes", {})
            rows.append({
                "iso": d["date"],
                "label": f"{site_facts.JOURS_FR[dd.weekday()][:3]}. {dd.day:02d}/{dd.month:02d}",
                "label_long": site_facts.fr_date(dd),
                "couleur": d["couleur"],
                "temp_observee": _temp_label(d["temp_observee"]),
                "cells": [_grid_cell(d["cells"][n], n, d["couleur"], causes.get(n),
                                     d.get("remplies", {}).get(n)) for n in GRID_HORIZONS],
            })
        hz = s["horizons"]
        if not show_pct:
            hz = _mask_pcts(hz)
        sans_calcul = s.get("jours_sans_calcul", [])
        # HIS-T2 : cause des cases sans prévision de la zone 2 à 5 jours avant (mêmes
        # effectifs que « sans prévision » du bloc En bref). « autre » = cause non enregistrée.
        causes_fiables = {"interruption": 0, "autre": 0}
        for d in s["days"]:
            for n in RELIABLE:
                if d["cells"].get(n) is None:
                    c = (d.get("causes", {}).get(n) or ("autre",))[0]
                    causes_fiables["interruption" if c == "interruption" else "autre"] += 1
        view = {
            "label": label,
            "first_date": short(s["first_date"]),
            "last_date": short(s["last_date"]),
            "first_iso": s["first_date"],
            "last_iso": s["last_date"],
            "nb_days": len(s["days"]),
            "veille": [h for h in hz if h["n"] == 1],
            "fiables": [h for h in hz if h["fiable"]],
            "bref": _bref([h for h in hz if h["fiable"]]) if show_pct
                    else _mask_pcts(_bref([h for h in hz if h["fiable"]])),
            "indicatifs": [h for h in hz if h["n"] > max(RELIABLE)],
            "rows": rows,
            "temp_manquante": any(c["state"] == "ok" and not c["has_temp"] and not c.get("voisine")
                                  for r in rows for c in r["cells"]),
            "voisines": any(c.get("source") == "voisines" for r in rows for c in r["cells"]),
            "absentes": any(c["state"] == "absente" for r in rows for c in r["cells"]),
            "jours_sans_calcul": sans_calcul,
            "jours_sans_calcul_txt": _fr_liste_dates(sans_calcul),
            "causes_fiables": causes_fiables,
            "veille_publiee": any(c.get("source") == "edf" for r in rows for c in r["cells"]),
        }
    y = parse_season_label(label) or 0
    reference = last_complete_season_summary(data, today)
    if reference:
        reference = {**reference, "path": season_path(reference["label"], current_label)}
    return {
        "season_label": label,
        "is_current": label == current_label,
        "season_links": season_links,
        "h": view,
        "pct_min": PCT_MIN_EFFECTIF,
        "pct_visible": show_pct,
        # Taux de référence publié (dernière saison complète), rappelé en tête de la saison en cours
        "reference": reference if reference and label == current_label and reference["label"] != label else None,
        "grid_horizons": GRID_HORIZONS,
        "season_start_iso": f"{y}-09-01",
        "season_end_iso": f"{y + 1}-08-31",
        "start_date_label": short(_start_date()) if _start_date()[:4] != "0000" else "",
    }
