"""Historique public des prévisions (page /historique-previsions et export CSV).

Principe (audit 2026-09-29, section 6) : chaque prévision est montrée telle
qu'elle a été émise N jours avant (N = 2 à 5), comparée à la couleur officielle
publiée par EDF. Aucune sélection de jours.

Sources et exclusions :
- predictions : lignes réelles uniquement (simulated = 0, cycle_id hors
  « backtest », émission >= Config.PREDICTION_START_DATE). La couleur émise est
  couleur_originale quand la ligne a été confirmée (confirm_prediction écrase
  couleur_predite), sinon couleur_predite. Horizon N = date cible - date
  d'émission (même règle que performance.jours_avance). Plusieurs lignes pour
  un même (date, N) : la dernière émise ce jour-là fait foi.
- performance : évaluations figées (jamais purgées, contrairement aux prédictions
  live supprimées après 90 jours par purge_old_data) ; lignes « backtest » exclues.
  Une ligne encore présente dans predictions prime (même donnée, horodatage complet).
- actuals : couleurs officielles réelles (synthetic = 0).

SQL volontairement minimal et portable SQLite/PostgreSQL ; agrégation en Python.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import Config

logger = logging.getLogger(__name__)

HORIZONS = (2, 3, 4, 5)
TABLE_HORIZONS = (5, 4, 3, 2)  # ordre d'affichage du tableau jour par jour
PCT_MIN_EFFECTIF = 20  # en dessous : effectifs bruts seulement
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


# ================================================================
# Chargement (3 requêtes simples, agrégation en Python)
# ================================================================

def _is_backtest(value) -> bool:
    return str(value or "").strip().lower().startswith("backtest")


def _load_rows(conn) -> tuple[dict, dict, dict]:
    """Renvoie (preds, actuals, confirmed_at).

    preds : {(date_cible, N): {"couleur", "emise_le"}} pour N dans HORIZONS.
    actuals : {date_iso: couleur officielle réelle}.
    confirmed_at : {date_iso: date de confirmation EDF (AAAA-MM-JJ)}.
    """
    start = _start_date()
    rows = conn.execute(
        "SELECT date, couleur_predite, couleur_originale, confirmed, cycle_id, "
        "timestamp_prediction FROM predictions "
        "WHERE simulated = 0 AND timestamp_prediction >= ?",
        (start,),
    ).fetchall()

    # 1) Évaluations figées (table performance, jamais purgée) : source durable,
    #    indispensable car purge_old_data() supprime les prédictions live > 90 jours.
    perf = conn.execute(
        "SELECT date_prediction, date_cible, jours_avance, couleur_predite, contexte_meteo "
        "FROM performance WHERE date_prediction >= ? AND jours_avance >= 2 AND jours_avance <= 5",
        (start,),
    ).fetchall()
    preds: dict = {}
    for p in perf:
        if _is_backtest(p["contexte_meteo"]) or p["couleur_predite"] not in COLORS:
            continue
        try:
            n = int(p["jours_avance"])
            target = date.fromisoformat(str(p["date_cible"])[:10])
            emitted = str(p["date_prediction"])[:10]
        except (TypeError, ValueError):
            continue
        if n not in HORIZONS or (target - date.fromisoformat(emitted)).days != n:
            continue
        preds[(target.isoformat(), n)] = {"couleur": p["couleur_predite"], "ts": emitted,
                                         "emise_le": emitted}

    # 2) Prédictions encore en base (90 derniers jours) : dernière émise le jour J-N.
    live: dict = {}
    for r in rows:
        if _is_backtest(r["cycle_id"]):
            continue
        ts = str(r["timestamp_prediction"] or "")
        try:
            emitted = date.fromisoformat(ts[:10])
            target = date.fromisoformat(str(r["date"])[:10])
        except ValueError:
            continue
        if emitted.isoformat() < start:
            continue
        n = (target - emitted).days
        if n not in HORIZONS:
            continue
        couleur = r["couleur_predite"]
        if r["confirmed"]:
            # confirm_prediction() écrase couleur_predite : la couleur émise est
            # couleur_originale ; perdue -> on garde l'évaluation figée si elle existe.
            couleur = r["couleur_originale"] or None
        key = (target.isoformat(), n)
        prev = live.get(key)
        if prev is None or ts > prev["ts"]:
            live[key] = {"couleur": couleur, "ts": ts, "emise_le": ts[:16]}
    for key, v in live.items():
        if v["couleur"] in COLORS:
            preds[key] = v
    # Couleur émise inconnue partout : prévision absente (jamais comptée juste).

    actual_rows = conn.execute(
        "SELECT date, couleur_reelle, timestamp_confirmation FROM actuals "
        "WHERE synthetic = 0 AND date >= ?",
        (start,),
    ).fetchall()
    actuals: dict = {}
    confirmed_at: dict = {}
    for a in actual_rows:
        d = str(a["date"])[:10]
        if a["couleur_reelle"] in COLORS:
            actuals[d] = a["couleur_reelle"]
            confirmed_at[d] = str(a["timestamp_confirmation"] or "")[:10]
    return preds, actuals, confirmed_at


# ================================================================
# Agrégation
# ================================================================

def _empty_color_stats() -> dict:
    return {"reels": 0, "annonces": 0, "sans_prevision": 0,
            "alertes": 0, "alertes_justes": 0}


def _season_stats(days: list[dict]) -> dict:
    """Effectifs par horizon pour une liste de jours (déjà bornée à la saison)."""
    horizons = []
    for n in HORIZONS:
        st = {c: _empty_color_stats() for c in ("ROUGE", "BLANC")}
        eligible = justes = bleus = sans = 0
        for d in days:
            cell = d["cells"][n]
            if cell == NA:
                continue
            eligible += 1
            reel = d["couleur"]
            prevu = cell["couleur"] if cell else None
            if prevu is None:
                sans += 1
            if prevu == reel:
                justes += 1
            if reel == "BLEU":
                bleus += 1
            for c in ("ROUGE", "BLANC"):
                s = st[c]
                if reel == c:
                    s["reels"] += 1
                    if prevu == c:
                        s["annonces"] += 1
                    elif prevu is None:
                        s["sans_prevision"] += 1
                if prevu == c:
                    s["alertes"] += 1
                    if reel == c:
                        s["alertes_justes"] += 1
        for s in st.values():
            s["rappel_pct"] = _pct(s["annonces"], s["reels"])
            s["precision_pct"] = _pct(s["alertes_justes"], s["alertes"])
        horizons.append({
            "n": n,
            "ROUGE": st["ROUGE"],
            "BLANC": st["BLANC"],
            "jours": eligible,
            "sans_prevision": sans,
            "justes": justes,
            "justes_pct": _pct(justes, eligible),
            "toujours_bleu": bleus,
            "toujours_bleu_pct": _pct(bleus, eligible),
        })
    return {"horizons": horizons}


def _build(conn, today: date) -> dict:
    preds, actuals, confirmed_at = _load_rows(conn)
    start = _start_date()
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
                cells[n] = {"couleur": p["couleur"], "emise_le": p["emise_le"]} if p else None
        if all(c == NA for c in cells.values()):
            continue  # service pas encore lancé pour ce jour, quel que soit l'horizon
        by_season.setdefault(season_start_year(d), []).append(
            {"date": d_iso, "couleur": actuals[d_iso], "cells": cells,
             "confirme_le": confirmed_at.get(d_iso, "")})

    seasons = {}
    last_eval = None
    for y, days in by_season.items():
        # Une saison n'est proposée que si au moins une prévision réelle existe.
        if not any(isinstance(c, dict) for d in days for c in d["cells"].values()):
            continue
        stats = _season_stats(days)
        dates_conf = [d["confirme_le"] for d in days if d["confirme_le"]]
        lm = max(dates_conf) if dates_conf else days[-1]["date"]
        last_eval = max(last_eval, lm) if last_eval else lm
        seasons[season_label(y)] = {
            "label": season_label(y),
            "days": days,
            "first_date": days[0]["date"],
            "last_date": days[-1]["date"],
            "last_evaluation": lm,
            **stats,
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
# Export CSV (Excel FR : séparateur « ; », UTF-8 avec BOM)
# ================================================================

CSV_HEADER = (
    ["saison", "date", "couleur_officielle"]
    + [f"prevision_J-{n}" for n in TABLE_HORIZONS]
    + [f"emise_le_J-{n}" for n in TABLE_HORIZONS]
)


def to_csv(data: dict) -> str:
    """Toutes les saisons, mêmes lignes que les tableaux jour par jour.

    Prévision absente : cellule vide. Service pas encore lancé à cet horizon : n/a.
    """
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    for label in sorted(data.get("seasons", {})):
        for d in data["seasons"][label]["days"]:
            cols, stamps = [], []
            for n in TABLE_HORIZONS:
                c = d["cells"][n]
                if c == NA:
                    cols.append(NA)
                    stamps.append(NA)
                elif c:
                    cols.append(c["couleur"])
                    stamps.append(c["emise_le"])
                else:
                    cols.append("")
                    stamps.append("")
            w.writerow([label, d["date"], d["couleur"]] + cols + stamps)
    return buf.getvalue()


def to_csv_bytes(data: dict) -> bytes:
    """CSV encodé en UTF-8 précédé du BOM (ouverture correcte dans Excel)."""
    import codecs
    return codecs.BOM_UTF8 + to_csv(data).encode("utf-8")


# ================================================================
# Modèle de vue de la page HTML
# ================================================================

def season_path(label: str, current_label: str) -> str:
    return "/historique-previsions" if label == current_label else f"/historique-previsions/{label}"


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
                "label": site_facts.fr_date(dd, with_weekday=True, with_year=False),
                "couleur": d["couleur"],
                "cells": [
                    {"n": n,
                     "state": "na" if d["cells"][n] == NA else ("ok" if d["cells"][n] else "absente"),
                     "couleur": d["cells"][n]["couleur"] if isinstance(d["cells"][n], dict) else None,
                     "juste": isinstance(d["cells"][n], dict) and d["cells"][n]["couleur"] == d["couleur"]}
                    for n in TABLE_HORIZONS
                ],
            })
        view = {
            "label": label,
            "first_date": short(s["first_date"]),
            "last_date": short(s["last_date"]),
            "first_iso": s["first_date"],
            "last_iso": s["last_date"],
            "nb_days": len(s["days"]),
            "horizons": s["horizons"],
            "rows": rows,
        }
    y = parse_season_label(label) or 0
    return {
        "season_label": label,
        "is_current": label == current_label,
        "season_links": season_links,
        "h": view,
        "pct_min": PCT_MIN_EFFECTIF,
        "table_horizons": TABLE_HORIZONS,
        "season_start_iso": f"{y}-09-01",
        "season_end_iso": f"{y + 1}-08-31",
        "start_date_label": short(_start_date()) if _start_date()[:4] != "0000" else "",
    }
