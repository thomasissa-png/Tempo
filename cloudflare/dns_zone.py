"""Recrée chez Cloudflare la zone DNS de calendrier-tempo.fr, à l'identique d'IONOS.

    python3 cloudflare/dns_zone.py            # affiche l'écart, ne change rien
    python3 cloudflare/dns_zone.py --apply    # crée la zone (si absente) et les enregistrements manquants

Source : capture de la page DNS IONOS du 2026-09-30, recoupée avec le DNS public
(DNS-over-HTTPS) : 14 enregistrements, pas de DNSSEC. Tant que les serveurs DNS ne sont pas
changés chez IONOS, la zone Cloudflare reste « pending » et n'a AUCUN effet.

Le site pointe encore vers Replit (A 34.111.179.208), en DNS seul (pas de proxy) et TTL 60 s :
le changement de serveurs DNS ne change rien pour les visiteurs, et la bascule vers le Worker
(phase 2) se propagera en une minute. E-mail IONOS (MX, SPF, DKIM, DMARC, autodiscover) inchangé.
Relançable : n'ajoute que ce qui manque, ne supprime jamais rien.
"""
import os
import sys

import httpx

from neon_http import cloudflare_token

API = os.getenv("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")
ZONE = "calendrier-tempo.fr"
REPLIT_IP = "34.111.179.208"
SITE_TTL = 60  # abaissé pour la bascule (IONOS : 3600)
TTL = 3600

# (type, nom, contenu, ttl, priorité MX)
RECORDS = [
    ("A", ZONE, REPLIT_IP, SITE_TTL, None),
    ("A", f"www.{ZONE}", REPLIT_IP, SITE_TTL, None),
    ("MX", ZONE, "mx00.ionos.fr", TTL, 10),
    ("MX", ZONE, "mx01.ionos.fr", TTL, 10),
    ("TXT", ZONE, '"v=spf1 include:_spf-eu.ionos.com ~all"', TTL, None),
    ("TXT", ZONE, '"google-site-verification=NuplQYaBgoiCzOP6c5WYiHGNGt853gv00pJ3jv-Ft7U"', TTL, None),
    ("TXT", ZONE, '"replit-verify=db33bf15-f902-462d-b7ba-0252d83fb575"', TTL, None),
    ("TXT", f"www.{ZONE}", '"replit-verify=db33bf15-f902-462d-b7ba-0252d83fb575"', TTL, None),
    ("CNAME", f"_dmarc.{ZONE}", "dmarc.ionos.fr", TTL, None),
    ("CNAME", f"s1-ionos._domainkey.{ZONE}", "s1.dkim.ionos.com", TTL, None),
    ("CNAME", f"s2-ionos._domainkey.{ZONE}", "s2.dkim.ionos.com", TTL, None),
    ("CNAME", f"s42582890._domainkey.{ZONE}", "s42582890.dkim.ionos.com", TTL, None),
    ("CNAME", f"autodiscover.{ZONE}", "adsredir.ionos.info", TTL, None),
    ("CNAME", f"_domainconnect.{ZONE}", "_domainconnect.ionos.com", TTL, None),
]


def _norm(value: str) -> str:
    return value.strip().rstrip(".").strip('"').lower()


def missing_records(existing: list[dict]) -> list[tuple]:
    have = {(r["type"], r["name"].lower(), _norm(r["content"])) for r in existing}
    return [r for r in RECORDS if (r[0], r[1].lower(), _norm(r[2])) not in have]


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    token, account = cloudflare_token(), os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    if not (token and account):
        print("ERREUR : jeton Cloudflare ou CLOUDFLARE_ACCOUNT_ID absent.")
        sys.exit(1)
    c = httpx.Client(base_url=API, timeout=30, headers={"Authorization": f"Bearer {token}"})
    zones = c.get("/zones", params={"name": ZONE}).json().get("result") or []
    if not zones:
        if not apply:
            print(f"Zone {ZONE} absente : --apply la crée, puis les {len(RECORDS)} enregistrements.")
            return
        r = c.post("/zones", json={"name": ZONE, "account": {"id": account}, "type": "full",
                                   "jump_start": False})
        if r.status_code != 200 or not r.json().get("success"):
            print(f"ERREUR création zone : HTTP {r.status_code} {r.json().get('errors')}")
            sys.exit(1)
        zones = [r.json()["result"]]
    zone = zones[0]
    existing = c.get(f"/zones/{zone['id']}/dns_records", params={"per_page": 500}).json().get("result") or []
    todo = missing_records(existing)
    for rtype, name, content, ttl, prio in todo:
        if not apply:
            print(f"  à créer : {rtype:5} {name} -> {content}")
            continue
        body = {"type": rtype, "name": name, "content": content, "ttl": ttl, "proxied": False}
        if prio is not None:
            body["priority"] = prio
        r = c.post(f"/zones/{zone['id']}/dns_records", json=body)
        ok = r.status_code == 200 and r.json().get("success")
        print(f"  {'OK ' if ok else 'ÉCHEC'} {rtype:5} {name} -> {content}"
              + ("" if ok else f" ({r.json().get('errors')})"))
        if not ok:
            sys.exit(1)
    print(f"\nZone {ZONE} : {zone['status']}, {len(RECORDS) - len(todo)}/{len(RECORDS)} déjà présents"
          + ("" if apply or not todo else " (lancer avec --apply)"))
    print("Serveurs DNS à saisir chez IONOS : " + ", ".join(zone.get("name_servers", [])))


if __name__ == "__main__":
    main()
