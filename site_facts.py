"""Faits publics du site calendrier-tempo.fr : source unique pour les templates,
le JSON-LD, llms.txt et les pages SEO.

Règle : chaque chiffre affiché au public est défini UNE fois ici, avec sa source.
Aucun chiffre ne doit être recopié en dur dans un template.
"""

from __future__ import annotations

import html
import re
from datetime import date

# ================================================================
# Identité
# ================================================================
SITE_URL = "https://www.calendrier-tempo.fr"
SITE_NAME = "Calendrier Tempo EDF"
SITE_ALTERNATE_NAMES = ["Calendrier Tempo", "calendrier-tempo.fr"]
SITE_DISCLAIMER = (
    "Service indépendant et gratuit, non affilié à EDF ni à RTE. "
    "Seule la couleur publiée par EDF fait foi."
)

# ================================================================
# Règles EDF Tempo (CLAUDE.md, règles R1 à R4 + budget)
# ================================================================
JOURS_ROUGES = 22
JOURS_BLANCS = 43
JOURS_BLEUS = 300  # 301 les saisons comportant un 29 février
MAX_ROUGES_CONSECUTIFS = 5

# ================================================================
# Tarifs Tempo (source de vérité éditoriale : articles/_seo_rules.yaml,
# section tarifs_tempo ; test de cohérence dans tests/test_seo_geo_site.py)
# ================================================================
TARIFS_DATE_EFFET = date(2026, 8, 1)
TARIFS_SOURCE = (
    "arrêté du 29 juillet 2026 (n° ECOR2619295S), publié au Journal officiel "
    "n° 0177 du 31 juillet 2026"
)
TARIFS = {  # €/kWh TTC, identiques quelle que soit la puissance souscrite
    "BLEU": {"hp": 0.1654, "hc": 0.1356},
    "BLANC": {"hp": 0.1921, "hc": 0.1536},
    "ROUGE": {"hp": 0.7295, "hc": 0.1615},
}
HEURES_PLEINES = "6 h à 22 h"
HEURES_CREUSES = "22 h à 6 h"

# Ratio rouge HP / bleu HP : 0,7295 / 0,1654 = 4,41
RATIO_ROUGE_BLEU_HP = round(TARIFS["ROUGE"]["hp"] / TARIFS["BLEU"]["hp"], 1)
# Surcoût d'un kWh consommé en heures pleines un jour rouge plutôt qu'un jour bleu
SURCOUT_KWH_ROUGE_HP = round(TARIFS["ROUGE"]["hp"] - TARIFS["BLEU"]["hp"], 4)
# Hypothèses de consommation déplacée (kWh en heures pleines) pour les exemples chiffrés
KWH_EXEMPLE_BAS = 25
KWH_EXEMPLE_HAUT = 35
SURCOUT_JOUR_BAS = round(KWH_EXEMPLE_BAS * SURCOUT_KWH_ROUGE_HP)    # 14 €
SURCOUT_JOUR_HAUT = round(KWH_EXEMPLE_HAUT * SURCOUT_KWH_ROUGE_HP)  # 20 €
SURCOUT_SAISON_BAS = round(JOURS_ROUGES * KWH_EXEMPLE_BAS * SURCOUT_KWH_ROUGE_HP)  # 310 €

# ================================================================
# Publication EDF et fonctionnement du site
# (tempo_client.py : couleur de demain disponible après 11 h ;
#  scheduler.py : polling toutes les 15 min de 6 h à 11 h 15, contrôle à 11 h 30)
# ================================================================
SOURCE_COULEURS = "api-couleur-tempo.fr"
SOURCE_COULEURS_URL = "https://www.api-couleur-tempo.fr/"
SOURCE_COULEURS_PHRASE = (
    "couleurs publiées par EDF, relayées par api-couleur-tempo.fr "
    "(service tiers qui diffuse les données Tempo EDF/RTE)"
)
ANNONCE_J1 = (
    "EDF publie la couleur Tempo du lendemain chaque jour en fin de matinée, "
    "généralement vers 11 h, parfois un peu plus tôt"
)
VERIFICATION_SITE = (
    "notre site vérifie la publication toutes les 15 minutes entre 6 h et 11 h 15, "
    "puis à 11 h 30, et l'affiche dès qu'elle est connue"
)
CALCUL_PREVISIONS = "chaque jour à 18 h, et à nouveau dès qu'EDF publie une couleur"

# Alertes WhatsApp (alerts.py : récap hebdo du dimanche sur 7 jours,
# alerte avant un jour rouge, 1 alerte maximum par jour, de novembre à mars)
ALERTES_HORIZON_JOURS = 7
ALERTES_DESCRIPTION = (
    "de novembre à mars, chaque dimanche soir, le récapitulatif des 7 prochains jours, "
    "et une alerte avant chaque jour rouge prévu (une alerte par jour au maximum)"
)
FOYERS_ALERTES = "plus de 2 500"  # décision fondateur du 2026-09-29 : chiffre conservé
FOYERS_ALERTES_HTML = "plus de 2&nbsp;500"  # même chiffre, espace insécable pour le HTML
CHIFFREMENT_TELEPHONE = "Fernet (AES-128)"

# Horizons de prévision
HORIZON_MAX_JOURS = 15
HORIZON_FIABLE = "J+2 à J+5"
HORIZON_INDICATIF = "J+6 à J+15"

# ================================================================
# Performance : politique de publication (audit algorithme du 2026-09-29,
# docs/audits/2026-09-29-audit-algorithme.md). Les anciens chiffres de backtest
# (F1 83,1 %, rappel 81 %) étaient mesurés en grande partie sur les jours
# d'entraînement : ils ne doivent plus être publiés.
# ================================================================
PERFORMANCE_POLICY = (
    "Nous publions uniquement notre performance mesurée en conditions réelles : "
    "nos prévisions de J+2 à J+5, figées au moment où elles sont émises, comparées "
    "aux couleurs publiées par EDF. Les tests rétrospectifs sur des données passées "
    "ne sont pas publiés, car ils surestiment la fiabilité réelle."
)

# ================================================================
# Dates de dernière modification du contenu des pages statiques (sitemap)
# À mettre à jour à chaque modification de contenu d'une page.
# ================================================================
PAGE_LASTMOD = {
    "/alertes": date(2026, 9, 29),
    "/a-propos": date(2026, 9, 29),
    "/mentions-legales": date(2026, 9, 29),
    "/tarif-tempo-edf": date(2026, 9, 29),
    "/api-tempo": date(2026, 9, 29),
    "/methodologie": date(2026, 9, 29),
    "/historique-previsions": date(2026, 9, 29),
}
LLMS_CONTENT_DATE = date(2026, 9, 29)  # dernière révision éditoriale de llms.txt


# ================================================================
# Formatage français
# ================================================================
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
    "août", "septembre", "octobre", "novembre", "décembre",
]
COULEUR_LABEL = {"BLEU": "Bleu", "BLANC": "Blanc", "ROUGE": "Rouge"}


def fr_num(value: float, decimals: int = 1) -> str:
    """4.41 -> '4,4' ; 0.1654 (decimals=4) -> '0,1654'."""
    return f"{value:.{decimals}f}".replace(".", ",")


def fr_price(value: float) -> str:
    """Prix au kWh au format français : 0.1654 -> '0,1654'."""
    return fr_num(value, 4)


def fr_date(d: date, with_weekday: bool = True, with_year: bool = True) -> str:
    """date(2026, 9, 29) -> 'mardi 29 septembre 2026'."""
    day = "1er" if d.day == 1 else str(d.day)
    txt = f"{day} {MOIS_FR[d.month]}"
    if with_year:
        txt += f" {d.year}"
    if with_weekday:
        txt = f"{JOURS_FR[d.weekday()]} {txt}"
    return txt


def couleur_label(couleur: str | None) -> str:
    return COULEUR_LABEL.get((couleur or "").upper(), "Inconnue")


def _jour_court(d: date) -> str:
    """date(2026, 10, 5) -> 'lundi 5' ; le 1er du mois -> 'jeudi 1er'."""
    return f"{JOURS_FR[d.weekday()]} {'1er' if d.day == 1 else d.day}"


def week_outlook_html(days: list[dict]) -> str:
    """Phrase « la suite » sous les pastilles de l'accueil (après aujourd'hui et demain).

    `days` = période affichée (dicts avec date ISO, couleur, is_hero). Miroir exact de
    weekOutlookHtml() dans static/js/app.js : toute modification se fait des deux côtés
    (test tests/test_home_week_card.py).
    """
    if not days:
        return ""
    dates = [date.fromisoformat(d["date"]) for d in days]
    rest = [(dt, d["couleur"]) for dt, d in zip(dates, days) if not d.get("is_hero")]
    if not rest:
        return ""
    fin = f"d'ici le {fr_date(dates[-1], with_year=False)}"
    parts = []
    for couleur, nom in (("ROUGE", "rouge"), ("BLANC", "blanc")):
        jours = [_jour_court(dt) for dt, c in rest if c == couleur]
        if jours:
            s = "s" if len(jours) > 1 else ""
            parts.append(f"<strong>{len(jours)} jour{s} {nom}{s}</strong> ({', '.join(jours)})")
    if parts:
        total = sum(1 for _, c in rest if c in ("ROUGE", "BLANC"))
        txt = (f"{' et '.join(parts)} prévu{'s' if total > 1 else ''} {fin}. "
               "<strong>Planifiez vos machines les jours bleus.</strong>")
    else:
        txt = f"Aucun jour rouge ni blanc prévu {fin}."
    if any(d.get("is_hero") and d["couleur"] in ("ROUGE", "BLANC") for d in days):
        txt = "Ensuite, " + txt[0].lower() + txt[1:]
    # Règle EDF R1 : rouge seulement du 1er novembre au 31 mars
    if all(4 <= dt.month <= 10 for dt in dates):
        if dates[0].month >= 9:
            txt += " Aucun jour rouge possible avant le 1er novembre (règle EDF)."
        else:
            txt += " Plus de jour rouge depuis le 31 mars : aucun avant le 1er novembre (règle EDF)."
    return txt


def html_to_text(fragment: str) -> str:
    """Convertit un fragment HTML de FAQ en texte brut (JSON-LD, llms.txt)."""
    text = re.sub(r"<li[^>]*>", " ", fragment)
    text = re.sub(r"</(p|li|ul|ol|h[1-6])>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def faq_jsonld(items: list[dict]) -> dict:
    """FAQPage JSON-LD construit depuis la même liste que la FAQ visible."""
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": it["question"],
                "acceptedAnswer": {"@type": "Answer", "text": html_to_text(it["answer_html"])},
            }
            for it in items
        ],
    }


def breadcrumb_jsonld(items: list[tuple[str, str]]) -> dict:
    """[(nom, chemin), ...] -> BreadcrumbList avec URL absolue sur chaque élément."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": f"{SITE_URL}{path}"}
            for i, (name, path) in enumerate(items, start=1)
        ],
    }


# Version des assets (style.min.css, app.min.js, fonts.css) : à incrémenter à chaque
# régénération des fichiers minifiés. Un seul endroit, lu par tous les templates.
ASSET_VERSION = "20260930b"


def template_globals() -> dict:
    """Variables exposées à tous les templates Jinja (préfixe ``facts``)."""
    return {
        "SITE_URL": SITE_URL,
        "SITE_NAME": SITE_NAME,
        "SITE_DISCLAIMER": SITE_DISCLAIMER,
        "JOURS_ROUGES": JOURS_ROUGES,
        "JOURS_BLANCS": JOURS_BLANCS,
        "JOURS_BLEUS": JOURS_BLEUS,
        "MAX_ROUGES_CONSECUTIFS": MAX_ROUGES_CONSECUTIFS,
        "TARIFS": TARIFS,
        "TARIFS_DATE_EFFET": TARIFS_DATE_EFFET,
        "TARIFS_DATE_EFFET_FR": fr_date(TARIFS_DATE_EFFET, with_weekday=False),
        "TARIFS_SOURCE": TARIFS_SOURCE,
        "HEURES_PLEINES": HEURES_PLEINES,
        "HEURES_CREUSES": HEURES_CREUSES,
        "RATIO": fr_num(RATIO_ROUGE_BLEU_HP),
        "SURCOUT_KWH": fr_price(SURCOUT_KWH_ROUGE_HP),
        "KWH_BAS": KWH_EXEMPLE_BAS,
        "KWH_HAUT": KWH_EXEMPLE_HAUT,
        "SURCOUT_JOUR_BAS": SURCOUT_JOUR_BAS,
        "SURCOUT_JOUR_HAUT": SURCOUT_JOUR_HAUT,
        "SURCOUT_SAISON_BAS": SURCOUT_SAISON_BAS,
        "SOURCE_COULEURS_PHRASE": SOURCE_COULEURS_PHRASE,
        "SOURCE_COULEURS_URL": SOURCE_COULEURS_URL,
        "ANNONCE_J1": ANNONCE_J1,
        "VERIFICATION_SITE": VERIFICATION_SITE,
        "CALCUL_PREVISIONS": CALCUL_PREVISIONS,
        "ALERTES_HORIZON_JOURS": ALERTES_HORIZON_JOURS,
        "ALERTES_DESCRIPTION": ALERTES_DESCRIPTION,
        "FOYERS_ALERTES": FOYERS_ALERTES,
        "FOYERS_ALERTES_HTML": FOYERS_ALERTES_HTML,
        "CHIFFREMENT_TELEPHONE": CHIFFREMENT_TELEPHONE,
        "HORIZON_MAX_JOURS": HORIZON_MAX_JOURS,
        "HORIZON_FIABLE": HORIZON_FIABLE,
        "HORIZON_INDICATIF": HORIZON_INDICATIF,
        "PERFORMANCE_POLICY": PERFORMANCE_POLICY,
        "ASSET_V": ASSET_VERSION,
    }


# ================================================================
# FAQ : une seule source pour la FAQ visible ET le JSON-LD FAQPage.
# answer_html est du HTML de confiance (rendu avec |safe) ; le JSON-LD
# utilise html_to_text(answer_html) : mêmes questions, mêmes réponses.
# ================================================================
def _p(v: float) -> str:
    return fr_price(v)


_T = TARIFS
_R = fr_num(RATIO_ROUGE_BLEU_HP)

_ANSWER_JOUR_ROUGE = (
    f"<p>Un <strong>jour Tempo rouge</strong> est l'un des {JOURS_ROUGES} jours les plus chers de la "
    f"saison Tempo EDF. Le kWh en heures pleines y coûte <strong>{_p(_T['ROUGE']['hp'])} €</strong>, "
    f"soit {_R} fois le tarif d'un jour bleu ({_p(_T['BLEU']['hp'])} €). Les jours rouges tombent "
    "uniquement entre le 1<sup>er</sup> novembre et le 31 mars, du lundi au vendredi hors jours "
    f"fériés, et jamais plus de {MAX_ROUGES_CONSECUTIFS} jours d'affilée. Ils correspondent aux "
    "jours de plus forte consommation d'électricité, en général lors des vagues de froid.</p>"
)


def jours_bleus_saison(season_start_year: int) -> int:
    """Nombre de jours bleus d'une saison (1er sept. N au 31 août N+1)."""
    days = (date(season_start_year + 1, 9, 1) - date(season_start_year, 9, 1)).days
    return days - JOURS_ROUGES - JOURS_BLANCS


FAQ_HOME: list[dict] = [
    {
        "question": "C'est quoi l'offre Tempo EDF ?",
        "answer_html": (
            "<p>L'offre <strong>Tempo EDF</strong> (souvent appelée EDF Tempo) est une option "
            "tarifaire d'électricité dont le prix du kWh dépend de la couleur du jour. Chaque saison, "
            f"du 1<sup>er</sup> septembre au 31 août, compte {JOURS_BLEUS} jours bleus (301 quand la "
            f"saison contient un 29 février) au tarif le plus bas, {JOURS_BLANCS} jours blancs au tarif "
            f"intermédiaire et {JOURS_ROUGES} jours rouges au tarif le plus élevé. Elle est avantageuse "
            "si vous pouvez réduire votre consommation les jours rouges.</p>"
        ),
    },
    {
        "question": "Quelle couleur Tempo aujourd'hui et demain ?",
        "answer_html": (
            "<p>La <strong>couleur EDF Tempo</strong> du jour et celle de demain sont écrites en "
            "toutes lettres en haut de cette page, puis reprises dans le résumé des 10 prochains jours. "
            f"{ANNONCE_J1} ; {VERIFICATION_SITE}. Tant qu'EDF n'a pas publié la couleur de demain, "
            "nous affichons notre <strong>prévision</strong>, signalée comme telle. Voir aussi la page "
            "<a href=\"/couleur-tempo-demain\">couleur Tempo de demain</a> et le "
            "<a href=\"/calendrier\">calendrier EDF Tempo complet</a>.</p>"
        ),
    },
    {
        "question": "Quand connaît-on la couleur EDF Tempo du lendemain ?",
        "answer_html": (
            f"<p>{ANNONCE_J1}, sur son site et son application : à 12 h, la couleur du lendemain "
            f"est donc en principe connue. {VERIFICATION_SITE[0].upper() + VERIFICATION_SITE[1:]}. "
            f"Nos prévisions couvrent les {HORIZON_MAX_JOURS} prochains jours et sont recalculées "
            f"{CALCUL_PREVISIONS}.</p>"
        ),
    },
    {
        "question": "Combien coûte réellement un jour rouge ?",
        "answer_html": (
            f"<p>En heures pleines ({HEURES_PLEINES}), le kWh rouge coûte "
            f"<strong>{_p(_T['ROUGE']['hp'])} €</strong> contre {_p(_T['BLEU']['hp'])} € un jour bleu, "
            f"soit <strong>{_R} fois plus cher</strong>. En heures creuses, c'est "
            f"{_p(_T['ROUGE']['hc'])} € (rouge) contre {_p(_T['BLEU']['hc'])} € (bleu). Chaque kWh "
            f"consommé en heures pleines un jour rouge plutôt qu'un jour bleu coûte "
            f"{fr_price(SURCOUT_KWH_ROUGE_HP)} € de plus : pour {KWH_EXEMPLE_BAS} à {KWH_EXEMPLE_HAUT} kWh "
            f"en heures pleines, un jour rouge non anticipé coûte <strong>{SURCOUT_JOUR_BAS} à "
            f"{SURCOUT_JOUR_HAUT} € de plus</strong> qu'un jour bleu.\n"
            "                    Vous pouvez consulter <a href=\"https://selectra.info/energie/fournisseurs/edf/tempo#tarifs\">cette page de Selectra</a> qui donne la grille tarifaire en fonction de votre abonnement ou votre contrat EDF.</p>"
        ),
    },
    {
        "question": "Que faire concrètement un jour rouge ?",
        "answer_html": (
            "<p><strong>Les gros postes à reporter :</strong></p>"
            "<ul class=\"faq-list-items\">"
            "<li>Lave-linge et sèche-linge : reporter au lendemain</li>"
            "<li>Lave-vaisselle : lancer en heures creuses (22 h–6 h) ou reporter</li>"
            "<li>Four et plaques électriques : préférer le micro-ondes ou un repas froid</li>"
            "<li>Recharge du véhicule électrique : décaler au jour bleu suivant</li>"
            "<li>Chauffage électrique : baisser de 1 à 2 °C, préchauffer la veille</li>"
            "<li>Ballon d'eau chaude : couper en heures pleines, utiliser l'eau stockée</li>"
            "</ul>"
            "<p><strong>Astuce :</strong> la veille d'un jour rouge, montez un peu le chauffage et "
            "lancez vos machines. L'inertie thermique du logement vous portera une partie du lendemain.</p>"
        ),
    },
    {"question": "Qu'est-ce qu'un jour Tempo rouge ?", "answer_html": _ANSWER_JOUR_ROUGE},
    {
        "question": "Un jour rouge peut-il tomber un samedi, un dimanche ou un jour férié ?",
        "answer_html": (
            "<p><strong>Non.</strong> Les jours rouges tombent uniquement du lundi au vendredi, hors "
            "jours fériés, entre le 1<sup>er</sup> novembre et le 31 mars. Un samedi peut être blanc "
            "ou bleu, jamais rouge. Le dimanche n'est jamais ni rouge ni blanc : il est toujours bleu. "
            "Un jour férié peut être blanc (sauf un dimanche) ou bleu, jamais rouge.</p>"
        ),
    },
    {
        "question": "À quelle heure commence et finit un jour rouge ?",
        "answer_html": (
            "<p>Un jour Tempo va de <strong>6 h du matin à 6 h le lendemain matin</strong>, et non de "
            "minuit à minuit. Par exemple, un jour rouge le mardi commence mardi à 6 h et finit mercredi "
            "à 6 h. Avant 6 h du matin, c'est encore le tarif du jour précédent qui s'applique.</p>"
        ),
    },
    {
        "question": "Comment sont choisis les jours rouges Tempo ?",
        "answer_html": (
            "<p>La couleur de chaque jour est déterminée par RTE, le gestionnaire du réseau de "
            "transport d'électricité, selon une méthode publique, puis publiée par EDF. Elle dépend "
            "surtout de la consommation d'électricité prévue au niveau national, très liée à la "
            "température, et du nombre de jours rouges et blancs qu'il reste à placer dans la saison. "
            "Notre algorithme s'appuie sur les mêmes signaux (météo de 9 villes, consommation prévue "
            "par RTE, jours restants) : voir notre <a href=\"/methodologie\">méthodologie</a>.</p>"
        ),
    },
    {
        "question": "Quels sont les tarifs EDF Tempo en 2026 ?",
        "answer_html": (
            f"<p>Depuis le 1<sup>er</sup> août 2026, en <strong>heures pleines</strong> ({HEURES_PLEINES}) : "
            f"jour bleu <strong>{_p(_T['BLEU']['hp'])} €/kWh</strong>, jour blanc "
            f"<strong>{_p(_T['BLANC']['hp'])} €/kWh</strong>, jour rouge "
            f"<strong>{_p(_T['ROUGE']['hp'])} €/kWh</strong>. En <strong>heures creuses</strong> "
            f"({HEURES_CREUSES}) : bleu {_p(_T['BLEU']['hc'])} €, blanc {_p(_T['BLANC']['hc'])} €, "
            f"rouge {_p(_T['ROUGE']['hc'])} €/kWh. Un jour rouge en heures pleines coûte donc "
            f"<strong>{_R} fois plus cher</strong> qu'un jour bleu. Tarifs TTC fixés par l'{TARIFS_SOURCE} ; "
            "vérifiez sur votre contrat EDF. Détail sur notre page "
            "<a href=\"/tarif-tempo-edf\">tarif Tempo EDF</a>.</p>"
        ),
    },
    {
        "question": "Ce service est-il officiel EDF ?",
        "answer_html": (
            "<p>Non. Le Calendrier Tempo EDF est un service indépendant et gratuit, non affilié à EDF "
            f"ni à RTE. Les couleurs officielles affichées sont les {SOURCE_COULEURS_PHRASE}. Seule la "
            "couleur annoncée par EDF fait foi : nos prévisions sont des estimations pour vous aider à "
            "<strong>anticiper</strong>, pas des informations officielles.</p>"
        ),
    },
    {
        "id": "faq-fiabilite",
        "question": "Vos prévisions sont-elles fiables ?",
        "answer_html": (
            f"<p>Les prévisions les plus utiles sont celles de {HORIZON_FIABLE}. Au-delà de 5 jours, les "
            "prévisions météo deviennent moins précises, donc les nôtres aussi : de "
            f"{HORIZON_INDICATIF}, c'est une <strong>tendance</strong>, pas une certitude. Pour une "
            "décision importante (grosse lessive, recharge d'un véhicule), fiez-vous plutôt aux "
            "prévisions à 2 ou 3 jours. Notre taux de réussite mesuré en conditions réelles est "
            "détaillé sur la page "
            "<a href=\"/methodologie\">méthodologie</a>, et chaque prévision passée est comparée à la "
            "couleur officielle sur notre "
            "<a href=\"/historique-previsions\">historique des prévisions</a>.</p>"
        ),
    },
]


def faq_calendrier(season_label: str) -> list[dict]:
    """FAQ de /calendrier (visible + JSON-LD), paramétrée par la saison affichée."""
    try:
        bleus = jours_bleus_saison(int(season_label[:4]))
    except ValueError:
        bleus = JOURS_BLEUS
    return [
        {
            "question": "Quand sont les prochains jours rouges Tempo EDF ?",
            "answer_html": (
                "<p>Les jours rouges EDF Tempo tombent uniquement entre le 1<sup>er</sup> novembre et "
                f"le 31 mars, du lundi au vendredi hors jours fériés. {ANNONCE_J1}. Nos prévisions "
                f"couvrent les {HORIZON_MAX_JOURS} prochains jours ; les plus fiables sont celles de "
                f"{HORIZON_FIABLE}.</p>"
            ),
        },
        {
            "question": "Combien de jours rouges reste-t-il cette saison ?",
            "answer_html": (
                f"<p>La saison Tempo {season_label} compte exactement {JOURS_ROUGES} jours rouges, "
                f"{JOURS_BLANCS} jours blancs et {bleus} jours bleus. Le compteur en haut de cette page "
                "indique combien de jours de chaque couleur ont déjà été utilisés.</p>"
            ),
        },
        {
            "question": "Le calendrier Tempo EDF est-il le même chaque année ?",
            "answer_html": (
                "<p>Non, les dates des jours rouges et blancs changent chaque saison. Elles sont fixées "
                "au jour le jour en fonction de la demande d'électricité, qui dépend surtout de la météo "
                "hivernale. Les dates réelles des saisons passées sont publiées sur les pages saison de "
                "ce calendrier.</p>"
            ),
        },
        {
            "question": "Quelle couleur Tempo demain ?",
            "answer_html": (
                f"<p>{ANNONCE_J1}. En attendant l'annonce officielle, nous affichons une prévision "
                "fondée sur la météo et la consommation nationale. La couleur de demain est indiquée "
                "sur la page <a href=\"/couleur-tempo-demain\">couleur Tempo de demain</a> et sur la "
                "page d'accueil.</p>"
            ),
        },
        {"question": "Qu'est-ce qu'un jour Tempo rouge ?", "answer_html": _ANSWER_JOUR_ROUGE},
        {
            "question": "Comment être prévenu avant un jour rouge Tempo ?",
            "answer_html": (
                "<p>Inscrivez-vous gratuitement aux <a href=\"/alertes\">alertes WhatsApp</a> : vous "
                f"recevez {ALERTES_DESCRIPTION}. L'inscription prend 30 secondes, sans engagement. Ce "
                f"calendrier affiche aussi nos prévisions des {HORIZON_MAX_JOURS} prochains jours.</p>"
            ),
        },
    ]
