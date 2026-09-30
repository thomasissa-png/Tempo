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


def _load_preds(conn, start: str) -> dict:
    """{(date_cible, N): {"couleur", "ts", "emise_le", "temp", "proba"}}."""
    preds: dict = {}
    backtest_keys: set = set()
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
        if emitted.isoformat() < start or n not in HORIZONS:
            continue
        couleur, proba = r["couleur_predite"], None
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
    if any(v["temp"] is None for v in preds.values()):
        log = {}
        for w in conn.execute(
            "SELECT target_date, forecast_date, temp_moy FROM weather_forecast_log "
            "WHERE target_date >= ? AND temp_moy IS NOT NULL", (start,),
        ).fetchall():
            log[(str(w["target_date"])[:10], str(w["forecast_date"])[:10])] = _num(w["temp_moy"])
        for (d, _n), v in preds.items():
            if v["temp"] is None:
                v["temp"] = log.get((d, v["emise_le"][:10]))
    return preds


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
    return {"reels": 0, "annonces": 0, "sans_prevision": 0,
            "alertes": 0, "alertes_justes": 0}


def _horizon_stats(days: list[dict], n: int) -> dict:
    """Effectifs pour un horizon N sur une liste de jours (déjà bornée à la saison)."""
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
        bleus += reel == "BLEU"
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
        s["rappel_pct"] = _pct(s["annonces"], s["reels"])
        s["precision_pct"] = _pct(s["alertes_justes"], s["alertes"])
    return {
        "n": n, "fiable": n in RELIABLE, "ROUGE": st["ROUGE"], "BLANC": st["BLANC"],
        "jours": eligible, "sans_prevision": sans,
        "justes": justes, "justes_pct": _pct(justes, eligible),
        "toujours_bleu": bleus, "toujours_bleu_pct": _pct(bleus, eligible),
    }


def _bref(fiables: list[dict]) -> dict:
    """Bloc « En bref » : effectifs J-2 à J-5 cumulés (une prévision par jour et par délai)."""
    tot = {"jours": 0, "justes": 0, "toujours_bleu": 0}
    col = {c: {"reels": 0, "annonces": 0, "alertes": 0, "alertes_justes": 0} for c in ("ROUGE", "BLANC")}
    for h in fiables:
        for k in tot:
            tot[k] += h[k]
        for c, s in col.items():
            for k in s:
                s[k] += h[c][k]
    for s in col.values():
        s["rappel_pct"] = _pct(s["annonces"], s["reels"])
        s["precision_pct"] = _pct(s["alertes_justes"], s["alertes"])
    return {**tot, "justes_pct": _pct(tot["justes"], tot["jours"]),
            "toujours_bleu_pct": _pct(tot["toujours_bleu"], tot["jours"]), **col}


def _build(conn, today: date) -> dict:
    start = _start_date()
    preds = _load_preds(conn, start)
    actuals, confirmed_at, observed = _load_actuals(conn, start)
    start_d = date.fromisoformat(start) if start[:4] != "0000" else None

    by_season: dict[int, list] = {}
    for d_iso in sorted(actuals):
        d = date.fromisoformat(d_iso)
        if d > today:
            continue  # uniquement les jours passés ou aujourd'hui
        cells = {}
        for n in HORIZONS:
            if start_d and d - timedelta(days=n) < start_d:
                cells[n] = NA
            else:
                p = preds.get((d_iso, n))
                cells[n] = ({"couleur": p["couleur"], "emise_le": p["emise_le"],
                             "temp": p["temp"], "proba": p["proba"]} if p else None)
        if all(c == NA for c in cells.values()):
            continue  # service pas encore lancé pour ce jour, quel que soit l'horizon
        by_season.setdefault(season_start_year(d), []).append(
            {"date": d_iso, "couleur": actuals[d_iso], "cells": cells,
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
        }
    return {"seasons": seasons, "last_evaluation": last_eval}


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


def to_csv(data: dict) -> str:
    """Toutes les saisons, mêmes lignes que la grille jour par jour.

    Prévision absente : cellules vides. Service pas encore lancé à cet horizon : n/a.
    Température prévue non conservée : vide.
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
    return "n/d" if t is None else f"{_fr_temp(t)}°"


def _grid_cell(cell, n: int, reel: str) -> dict:
    avant = "la veille" if n == 1 else f"{n} jours avant"
    if cell == NA:
        return {"state": "na", "title": f"{avant.capitalize()} : service pas encore lancé"}
    if not cell:
        return {"state": "absente", "title": f"{avant.capitalize()} : aucune prévision émise"}
    juste = cell["couleur"] == reel
    parts = [f"Prévu {avant} : {NOMS[cell['couleur']]}, {'juste' if juste else 'erroné'}"]
    if cell["proba"] is not None:
        parts.append(f"probabilité {cell['proba']} %")
    parts.append("température prévue non conservée" if cell["temp"] is None
                 else f"température moyenne prévue {_fr_temp(cell['temp'])} °C")
    return {"state": "ok", "couleur": cell["couleur"], "juste": juste,
            "temp": _temp_label(cell["temp"]), "has_temp": cell["temp"] is not None,
            "title": " ; ".join(parts)}


def page_context(data: dict, label: str, current_label: str) -> dict:
    """Contexte Jinja pour une saison (dates formatées, liens de saison)."""
    import site_facts

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
    if s:
        rows = []
        for d in reversed(s["days"]):  # plus récent en premier
            dd = date.fromisoformat(d["date"])
            rows.append({
                "iso": d["date"],
                "label": f"{site_facts.JOURS_FR[dd.weekday()][:3]}. {dd.day:02d}/{dd.month:02d}",
                "label_long": site_facts.fr_date(dd),
                "couleur": d["couleur"],
                "temp_observee": _temp_label(d["temp_observee"]),
                "cells": [_grid_cell(d["cells"][n], n, d["couleur"]) for n in GRID_HORIZONS],
            })
        hz = s["horizons"]
        view = {
            "label": label,
            "first_date": short(s["first_date"]),
            "last_date": short(s["last_date"]),
            "first_iso": s["first_date"],
            "last_iso": s["last_date"],
            "nb_days": len(s["days"]),
            "veille": [h for h in hz if h["n"] == 1],
            "fiables": [h for h in hz if h["fiable"]],
            "bref": _bref([h for h in hz if h["fiable"]]),
            "indicatifs": [h for h in hz if h["n"] > max(RELIABLE)],
            "rows": rows,
            "temp_manquante": any(c["state"] == "ok" and not c["has_temp"]
                                  for r in rows for c in r["cells"]),
        }
    y = parse_season_label(label) or 0
    return {
        "season_label": label,
        "is_current": label == current_label,
        "season_links": season_links,
        "h": view,
        "pct_min": PCT_MIN_EFFECTIF,
        "grid_horizons": GRID_HORIZONS,
        "season_start_iso": f"{y}-09-01",
        "season_end_iso": f"{y + 1}-08-31",
        "start_date_label": short(_start_date()) if _start_date()[:4] != "0000" else "",
    }
