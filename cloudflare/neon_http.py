"""Transport SQL HTTPS de Neon, pour les scripts de migration lancés depuis une session Claude.

Le port PostgreSQL 5432 est bloqué dans les sessions (seul le HTTPS sort) et les deux bases
sont chez Neon (Replit = ep-...us-east-1.aws.neon.tech). On passe donc par l'API SQL HTTPS de
Neon : POST https://<hôte>/sql, chaîne de connexion dans l'en-tête Neon-Connection-String.

Les valeurs reviennent en texte brut (Neon-Raw-Text-Output) : c'est la représentation texte
de PostgreSQL, qu'on renvoie telle quelle en paramètre pour recopier sans perte.

Lecture seule : Neon n'applique l'en-tête Neon-Batch-Read-Only qu'aux requêtes groupées
(une transaction). Une base ouverte avec readonly=True passe donc TOUJOURS par une
transaction READ ONLY : même un bug ne peut pas écrire dans la base Replit.
"""
import os
from urllib.parse import urlparse

import httpx


class NeonError(RuntimeError):
    pass


def env(name: str) -> str:
    """Variable d'environnement, ou sa copie TEMPO_<NOM> (noms refusés par la session)."""
    return os.getenv(name) or os.getenv(f"TEMPO_{name}", "")


def cloudflare_token() -> str:
    # Jeton utilisateur créé dans Profil > API Tokens : le fondateur l'a rangé sous le nom
    # affiché par Cloudflare (CLOUDFLARE_Token_Value).
    return env("CLOUDFLARE_API_TOKEN") or os.getenv("CLOUDFLARE_Token_Value", "")


def phone_key() -> str:
    """PHONE_ENCRYPTION_KEY telle que Replit l'utilise.

    Constat du 2026-09-30 : les réglages de l'environnement de la session ont mangé le « = »
    final de la clé Fernet (43 caractères au lieu de 44). On le restaure ; check_access.py
    prouve ensuite que c'est la bonne clé (empreinte HMAC identique à celle des abonnés).
    """
    key = os.getenv("PHONE_ENCRYPTION_KEY", "").strip()
    if key and len(key) % 4:
        key += "=" * (-len(key) % 4)
    return key


def _param(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "t" if value else "f"
    return str(value)


class NeonHTTP:
    def __init__(self, url: str, readonly: bool = False, timeout: float = 120,
                 transport: httpx.BaseTransport | None = None):
        parsed = urlparse(url)
        if not parsed.hostname:
            raise NeonError("URL de base invalide")
        self.host = parsed.hostname
        self.readonly = readonly
        self._headers = {
            "Neon-Connection-String": url,
            "Neon-Raw-Text-Output": "true",
            "Neon-Array-Mode": "true",
        }
        if readonly:
            self._headers["Neon-Batch-Read-Only"] = "true"
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _post(self, body: dict) -> dict:
        try:
            r = self._client.post(f"https://{self.host}/sql", headers=self._headers, json=body)
        except httpx.HTTPError as e:
            raise NeonError(f"{self.host} injoignable ({type(e).__name__})") from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200:
            # Message PostgreSQL seulement : jamais la chaîne de connexion.
            raise NeonError(f"HTTP {r.status_code} : {str(data.get('message', ''))[:300]}")
        return data

    def batch(self, queries: list[tuple[str, list]]) -> list[list[list]]:
        """Exécute les requêtes dans UNE transaction ; renvoie les lignes de chacune."""
        if not queries:
            return []
        body = {"queries": [{"query": q, "params": [_param(p) for p in params]}
                            for q, params in queries]}
        return [res.get("rows") or [] for res in self._post(body).get("results", [])]

    def query(self, sql: str, params: list | tuple = ()) -> list[list]:
        if self.readonly:
            return self.batch([(sql, list(params))])[0]
        data = self._post({"query": sql, "params": [_param(p) for p in params]})
        return data.get("rows") or []

    def scalar(self, sql: str, params: list | tuple = ()):
        rows = self.query(sql, params)
        return rows[0][0] if rows and rows[0] else None
