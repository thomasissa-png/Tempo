"""Compare les prévisions de température Open-Meteo et Météo France à la
température observée, par horizon J+0..J+4, sur les dates communes.

Contexte (2026-10-01) : le scoring utilise Open-Meteo ; Météo France (AROME +
ARPEGE) est archivé à côté dans weather_forecast_mf_log (v26) SANS être utilisé.
Ce script mesure, sur données RÉELLES archivées, laquelle des deux sources est
la plus juste avant toute décision (qui imposera ensuite un rejeu, CLAUDE.md).

Sources :
  - Open-Meteo     : weather_forecast_log (horizon_days), source 'open-meteo' par défaut
  - Météo France   : weather_forecast_mf_log, modèle 'merged' par défaut
  - Référence      : weather_cache.temp_moy, dernière valeur par date (même
                     référence que l'admin « fiabilité météo », get_weather_reliability)

Limite connue : la référence est la prévision J+0 relevée en fin de journée
(Open-Meteo depuis février), donc légèrement favorable à Open-Meteo à J+0.

Usage (depuis la racine du dépôt) :
  python tools/replay/compare_weather_sources.py [--since AAAA-MM-JJ] [--json [FICHIER]]
  DATABASE_URL=postgresql://... python tools/replay/compare_weather_sources.py
  python tools/replay/compare_weather_sources.py --database-url postgresql://...
"""

import argparse
import json
import os
import sys

MF_SOURCES = ("arome", "arpege")


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _stats(errors: list[float]) -> dict:
    if not errors:
        return {"mae": None, "bias": None}
    return {"mae": round(sum(abs(e) for e in errors) / len(errors), 2),
            "bias": round(sum(errors) / len(errors), 2)}


def compare(conn, since: str | None = None, max_horizon: int = 4,
            mf_model: str = "merged", om_source: str | None = "open-meteo",
            ref_source: str | None = None) -> dict:
    """Calcule MAE et biais (prévu - observé) de temp_moy par horizon.

    om_source : filtre weather_forecast_log.source (None = toutes sources).
    ref_source : filtre weather_cache.description, qui contient la source de
    la référence (None = toutes, comme l'admin).
    """
    since = since or "0000-00-00"
    ref_rows = _rows(conn, """
        SELECT wc1.date, wc1.temp_moy, wc1.description FROM weather_cache wc1
        WHERE wc1.temp_moy IS NOT NULL AND wc1.date >= ?
          AND wc1.fetched_at = (SELECT MAX(wc2.fetched_at) FROM weather_cache wc2
                                WHERE wc2.date = wc1.date)""", (since,))
    warnings = []
    n_ref_mf = sum(1 for r in ref_rows if (r["description"] or "") in MF_SOURCES)
    if n_ref_mf:
        warnings.append(
            f"{n_ref_mf} date(s) de référence issues de Météo France (description "
            f"'arome'/'arpege') : valeur possiblement d'une seule échéance ; "
            f"--ref-source open-meteo pour les exclure")
    if ref_source:
        ref_rows = [r for r in ref_rows if (r["description"] or "") == ref_source]
    ref = {r["date"]: float(r["temp_moy"]) for r in ref_rows}

    om_rows = _rows(conn, """
        SELECT target_date, horizon_days, temp_moy, source FROM weather_forecast_log
        WHERE temp_moy IS NOT NULL AND target_date >= ?
          AND horizon_days >= 0 AND horizon_days <= ?""", (since, max_horizon))
    excluded = sum(1 for r in om_rows if om_source and r["source"] != om_source)
    if excluded:
        warnings.append(f"{excluded} ligne(s) weather_forecast_log d'une autre source "
                        f"que '{om_source}' exclue(s)")
    om = {(r["target_date"], int(r["horizon_days"])): float(r["temp_moy"])
          for r in om_rows if not om_source or r["source"] == om_source}

    mf_rows = _rows(conn, """
        SELECT target_date, horizon_days, temp_moy FROM weather_forecast_mf_log
        WHERE temp_moy IS NOT NULL AND model = ? AND target_date >= ?
          AND horizon_days >= 0 AND horizon_days <= ?""", (mf_model, since, max_horizon))
    mf = {(r["target_date"], int(r["horizon_days"])): float(r["temp_moy"]) for r in mf_rows}

    horizons = {}
    for h in range(max_horizon + 1):
        dates = sorted(d for (d, hh) in mf if hh == h and (d, h) in om and d in ref)
        om_err = [om[(d, h)] - ref[d] for d in dates]
        mf_err = [mf[(d, h)] - ref[d] for d in dates]
        horizons[f"J+{h}"] = {
            "n": len(dates), "open_meteo": _stats(om_err), "meteofrance": _stats(mf_err),
            "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
        }
    return {"reference": "weather_cache.temp_moy (dernière valeur par date)",
            "since": since if since != "0000-00-00" else None, "mf_model": mf_model,
            "om_source": om_source, "ref_source": ref_source,
            "horizons": horizons, "warnings": warnings}


def _fmt(v) -> str:
    return "  n/d" if v is None else f"{v:+5.2f}"


def format_text(res: dict) -> str:
    lines = [
        "Température moyenne prévue vs observée (dates communes aux deux sources)",
        f"Référence : {res['reference']}"
        + (f", source {res['ref_source']}" if res["ref_source"] else ""),
        f"Open-Meteo : weather_forecast_log (source {res['om_source'] or 'toutes'}) ; "
        f"Météo France : weather_forecast_mf_log (modèle {res['mf_model']})"
        + (f" ; depuis {res['since']}" if res["since"] else ""),
        "",
        "Horizon    n | Open-Meteo  EAM   biais | Météo France  EAM   biais | plus juste",
    ]
    for h, st in res["horizons"].items():
        om, mf = st["open_meteo"], st["meteofrance"]
        best = "—"
        if st["n"] and om["mae"] is not None and mf["mae"] is not None:
            best = ("égalité" if om["mae"] == mf["mae"]
                    else "Open-Meteo" if om["mae"] < mf["mae"] else "Météo France")
        lines.append(f"{h:<6} {st['n']:>4} |           {_fmt(om['mae'])} {_fmt(om['bias'])} |"
                     f"             {_fmt(mf['mae'])} {_fmt(mf['bias'])} | {best}")
    lines.append("")
    lines.append("EAM = erreur absolue moyenne (°C) ; biais = prévu - observé (°C).")
    if not any(st["n"] for st in res["horizons"].values()):
        lines.append("Aucune date commune : laisser l'archive Météo France tourner ~10 jours.")
    lines += [f"Attention : {w}" for w in res["warnings"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", help="date cible minimale AAAA-MM-JJ")
    ap.add_argument("--max-horizon", type=int, default=4)
    ap.add_argument("--mf-model", default="merged", choices=["merged", "arome", "arpege"])
    ap.add_argument("--om-source", default="open-meteo",
                    help="source weather_forecast_log retenue ('all' = toutes)")
    ap.add_argument("--ref-source", default=None,
                    help="source de la référence weather_cache (défaut : toutes, comme l'admin)")
    ap.add_argument("--database-url", help="URL PostgreSQL (sinon DATABASE_URL, sinon SQLite local)")
    ap.add_argument("--json", nargs="?", const="-", metavar="FICHIER",
                    help="sortie JSON (sur la sortie standard sans FICHIER)")
    args = ap.parse_args(argv)

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from database import get_db  # après DATABASE_URL : config.py le lit à l'import

    conn = get_db()
    try:
        res = compare(conn, since=args.since, max_horizon=args.max_horizon,
                      mf_model=args.mf_model,
                      om_source=None if args.om_source == "all" else args.om_source,
                      ref_source=args.ref_source)
    finally:
        conn.close()

    if args.json == "-":
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(format_text(res))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
