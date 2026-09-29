"""Jumeau de l'algorithme RTE de choix des jours Tempo.

Source : note RTE « Méthode de choix des jours Tempo », indice 2 du 7/01/2025
(PDF à la racine du dépôt). Étude et résultats : docs/audits/2026-09-29-jumeau-rte.md.

1. Consommation nette C_nette : consommation nationale moins productions
   éolienne et photovoltaïque, moyenne journalière sur la journée Tempo
   (6 h du jour j à 6 h du jour j+1).
2. Normalisation (quantiles d'un an d'historique) :
       C_std = (C_nette - q_conso,0.4) / ((q_conso,0.8 - q_conso,0.4) * exp(-GAMMA * (q_temp,0.3 - KAPPA)))
   avec GAMMA = -0.1176 et KAPPA = 8.3042 °C (paramètres publiés).
3. Seuils (paramètres publiés) :
       Seuil Blanc+Rouge = 4.00 - 0.015 * JourTempo - 0.026 * Stock(Blanc+Rouge)
       Seuil Rouge       = 3.15 - 0.010 * JourTempo - 0.031 * Stock(Rouge)
   C_std > Seuil Rouge et jour éligible ROUGE -> ROUGE ; sinon C_std > Seuil
   Blanc+Rouge et jour éligible BLANC -> BLANC ; sinon BLEU.
4. Placement : ROUGE du 1er novembre au 31 mars, hors samedi, dimanche et
   jours fériés, 5 ROUGE consécutifs au plus ; BLANC hors dimanche.
   Écoulement du stock : si le stock restant égale le nombre de jours
   éligibles restants, le jour est placé même sous le seuil.

Calculs purs (aucun accès réseau). Seule lecture base : la météo de l'année
Tempo précédente (weather_cache) pour les quantiles de normalisation.
Point d'entrée production : forecast_colors(), appelé par
predictor._apply_rte_twin() si Config.RTE_TWIN_ENABLED.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

GAMMA = -0.1176
KAPPA = 8.3042
SEUIL_ROUGE = (3.15, -0.010, -0.031)        # A, B, C
SEUIL_BLANC_ROUGE = (4.00, -0.015, -0.026)  # A, B, C
Q_CONSO_BAS, Q_CONSO_HAUT, Q_TEMP = 0.4, 0.8, 0.3
MAX_ROUGES_CONSECUTIFS = 5
STOCK_ROUGE, STOCK_BLANC = 22, 43


def tempo_season_start(d: date) -> date:
    return date(d.year, 9, 1) if d.month >= 9 else date(d.year - 1, 9, 1)


def jour_tempo(d: date) -> int:
    """Rang du jour dans l'année Tempo (1er septembre = 1)."""
    return (d - tempo_season_start(d)).days + 1


def quantile(values: Iterable[float], q: float) -> float:
    """Quantile par interpolation linéaire (convention numpy par défaut)."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        raise ValueError("quantile d'une série vide")
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


@dataclass(frozen=True)
class Normalisation:
    q_conso_04: float
    q_conso_08: float
    q_temp_03: float

    @classmethod
    def from_history(cls, cnette: Iterable[float], temperatures: Iterable[float]) -> "Normalisation":
        c = list(cnette)
        return cls(quantile(c, Q_CONSO_BAS), quantile(c, Q_CONSO_HAUT), quantile(temperatures, Q_TEMP))

    @property
    def echelle(self) -> float:
        return (self.q_conso_08 - self.q_conso_04) * math.exp(-GAMMA * (self.q_temp_03 - KAPPA))

    def normalise(self, cnette_mw: float) -> float:
        return (cnette_mw - self.q_conso_04) / self.echelle


def seuils(d: date, stock_rouge: int, stock_blanc: int) -> tuple[float, float]:
    """(seuil Blanc+Rouge, seuil Rouge) du jour d pour les stocks restants donnés."""
    j = jour_tempo(d)
    a, b, c = SEUIL_BLANC_ROUGE
    s_br = a + b * j + c * (stock_blanc + stock_rouge)
    a, b, c = SEUIL_ROUGE
    s_r = a + b * j + c * stock_rouge
    return s_br, s_r


def rouge_autorise(d: date, is_holiday) -> bool:
    return (d.month >= 11 or d.month <= 3) and d.weekday() < 5 and not is_holiday(d)


def blanc_autorise(d: date) -> bool:
    return d.weekday() != 6


def jours_eligibles_restants(d: date, couleur: str, is_holiday) -> int:
    """Jours éligibles de d (inclus) à la fin de la période de la couleur."""
    s0 = tempo_season_start(d)
    fin = date(s0.year + 1, 3, 31) if couleur == "ROUGE" else date(s0.year + 1, 8, 31)
    n, k = 0, d
    while k <= fin:
        ok = rouge_autorise(k, is_holiday) if couleur == "ROUGE" else blanc_autorise(k)
        n += ok
        k += timedelta(days=1)
    return n


def decide(d: date, c_std: float, stock_rouge: int, stock_blanc: int,
           rouges_consecutifs_avant: int, is_holiday, ecoulement: bool = True) -> str:
    """Couleur du jour d selon les seuils officiels et les règles de placement.

    rouges_consecutifs_avant : nombre de ROUGE consécutifs se terminant la veille.
    """
    s_br, s_r = seuils(d, stock_rouge, stock_blanc)
    rouge_ok = (stock_rouge > 0 and rouge_autorise(d, is_holiday)
                and rouges_consecutifs_avant < MAX_ROUGES_CONSECUTIFS)
    blanc_ok = stock_blanc > 0 and blanc_autorise(d)
    if rouge_ok and (c_std > s_r or (ecoulement and stock_rouge >= jours_eligibles_restants(d, "ROUGE", is_holiday))):
        return "ROUGE"
    if blanc_ok and (c_std > s_br or (ecoulement and stock_blanc >= jours_eligibles_restants(d, "BLANC", is_holiday))):
        return "BLANC"
    return "BLEU"


def simulate(days: list[date], c_std: dict[date, float], stock_rouge: int, stock_blanc: int,
             couleurs_connues: dict[date, str], is_holiday, ecoulement: bool = True) -> dict[date, str]:
    """Décide les jours `days` dans l'ordre, en décrémentant les stocks.

    couleurs_connues : couleurs déjà publiées (pour la règle des 5 ROUGE consécutifs) ;
    les stocks passés en argument doivent déjà en tenir compte.
    """
    couleurs = dict(couleurs_connues)
    out = {}
    for d in days:
        n, k = 0, d - timedelta(days=1)
        while couleurs.get(k) == "ROUGE":
            n += 1
            k -= timedelta(days=1)
        c = decide(d, c_std[d], stock_rouge, stock_blanc, n, is_holiday, ecoulement)
        if c == "ROUGE":
            stock_rouge -= 1
        elif c == "BLANC":
            stock_blanc -= 1
        couleurs[d] = out[d] = c
    return out


# ================================================================
# Estimation de C_nette à partir des prévisions météo (weather_client)
# ================================================================
# Modèle linéaire interprétable, appris hors ligne sur la C_nette réelle
# eCO2mix (tools/replay/rte_twin_forecast.py). Variables : uniquement ce que
# weather_client fournit à J+h (moyennes 9 villes) et le calendrier.

FEATURE_NAMES = (
    "const", "dj16", "dj16_veille", "dj16_avant_veille", "dc20", "temp_min", "amplitude",
    "vent", "vent2", "humidite", "pression",
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "ferie",
    "sin_doy", "cos_doy", "sin_2doy", "cos_2doy", "noel", "aout",
)


def _wv(w: dict, key: str, default: float) -> float:
    v = w.get(key) if w else None
    return default if v is None else float(v)


def cnette_features(d: date, w: dict, t_veille: float | None, t_avant_veille: float | None,
                    is_holiday) -> list[float]:
    """Variables du jour d. t_veille / t_avant_veille : temp_moy des 2 jours précédents
    (prévues ou observées) ; à défaut, la température du jour."""
    t = _wv(w, "temp_moy", 10.0)
    t1 = t if t_veille is None else t_veille
    t2 = t1 if t_avant_veille is None else t_avant_veille
    tmin, tmax = _wv(w, "temp_min", t - 3), _wv(w, "temp_max", t + 3)
    vent = _wv(w, "wind_speed", 10.0)
    doy = 2 * math.pi * d.timetuple().tm_yday / 365.25
    dow = d.weekday()
    return [1.0, max(0.0, 16 - t), max(0.0, 16 - t1), max(0.0, 16 - t2), max(0.0, t - 20),
            tmin, tmax - tmin, vent, vent * vent / 10, _wv(w, "humidity", 70.0),
            _wv(w, "pressure", 1013.0) - 1013,
            *[1.0 if dow == k else 0.0 for k in range(6)], 1.0 if is_holiday(d) else 0.0,
            math.sin(doy), math.cos(doy), math.sin(2 * doy), math.cos(2 * doy),
            1.0 if (d.month == 12 and d.day >= 24) or (d.month == 1 and d.day == 1) else 0.0,
            1.0 if d.month == 8 and d.day <= 25 else 0.0]


def estimate_cnette(coefs: list[float], features: list[float]) -> float:
    if len(coefs) != len(features):
        raise ValueError("coefficients et variables de longueurs différentes")
    return sum(c * x for c, x in zip(coefs, features))


# ================================================================
# Exécution en production
# ================================================================
MODEL_PATH = Path(__file__).parent / "rte_twin_model.json"
MIN_JOURS_NORMALISATION = 330
_MODEL: dict | None = None
_NORM_CACHE: dict[date, Normalisation | None] = {}


def load_model() -> dict | None:
    """Coefficients de C_nette (rte_twin_model.json) ; None si absent ou invalide."""
    global _MODEL
    if _MODEL is None:
        try:
            m = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
            if tuple(m["feature_names"]) != FEATURE_NAMES:
                raise ValueError("variables du modèle différentes de FEATURE_NAMES")
            _MODEL = m
        except Exception as e:  # modèle absent : jumeau inactif
            logger.warning(f"[RTE twin] modèle indisponible : {e}")
            return None
    return _MODEL


def _load_weather_history(start: date, end: date) -> dict[date, dict]:
    """Météo journalière (weather_cache) de start à end inclus."""
    from database import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT date, temp_min, temp_max, temp_moy, pressure, humidity, wind_speed "
            "FROM weather_cache WHERE date >= ? AND date <= ?",
            (start.isoformat(), end.isoformat())).fetchall()
        return {date.fromisoformat(r["date"]): dict(r) for r in rows if r["temp_moy"] is not None}
    finally:
        conn.close()


def normalisation_for_season(season_start: date, is_holiday) -> Normalisation | None:
    """Quantiles de l'année Tempo PRÉCÉDENTE, C_nette estimée depuis la météo observée.

    None si le modèle manque ou si moins de MIN_JOURS_NORMALISATION jours de météo.
    """
    if season_start in _NORM_CACHE:
        return _NORM_CACHE[season_start]
    model = load_model()
    norm = None
    if model is not None:
        prev_start = date(season_start.year - 1, 9, 1)
        wx = _load_weather_history(prev_start - timedelta(days=2), season_start - timedelta(days=1))
        days = [d for d in wx if d >= prev_start]
        if len(days) >= MIN_JOURS_NORMALISATION:
            c = [estimate_cnette(model["coefs"], cnette_features(
                d, wx[d], (wx.get(d - timedelta(days=1)) or {}).get("temp_moy"),
                (wx.get(d - timedelta(days=2)) or {}).get("temp_moy"), is_holiday)) for d in days]
            norm = Normalisation.from_history(c, [wx[d]["temp_moy"] for d in days])
        else:
            logger.warning(f"[RTE twin] {len(days)} jours de météo pour {prev_start}..{season_start}"
                           f" (< {MIN_JOURS_NORMALISATION}) : jumeau inactif pour cette saison")
    _NORM_CACHE[season_start] = norm
    return norm


def forecast_colors(targets: list[date], weather_by_date: dict[date, dict], stock_rouge: int,
                    stock_blanc: int, known_colors: dict[date, str], is_holiday) -> dict[date, str] | None:
    """Couleurs du jumeau pour `targets` (ordre chronologique), stocks décrémentés au fil des jours.

    weather_by_date : météo prévue des cibles et des 2 jours qui précèdent chacune.
    Renvoie None si le jumeau ne peut pas s'exécuter (modèle ou normalisation absents).
    """
    if not targets:
        return {}
    model = load_model()
    norm = normalisation_for_season(tempo_season_start(targets[0]), is_holiday)
    if model is None or norm is None or any(t not in weather_by_date for t in targets):
        return None
    cstd = {}
    for t in targets:
        w1 = weather_by_date.get(t - timedelta(days=1)) or {}
        w2 = weather_by_date.get(t - timedelta(days=2)) or {}
        f = cnette_features(t, weather_by_date[t], w1.get("temp_moy"), w2.get("temp_moy"), is_holiday)
        cstd[t] = norm.normalise(estimate_cnette(model["coefs"], f))
    return simulate(targets, cstd, stock_rouge, stock_blanc, known_colors, is_holiday)
