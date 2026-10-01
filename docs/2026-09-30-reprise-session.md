# Reprise : migration Cloudflare et refonte du site (état au 2026-09-30 au soir)

Note pour la session suivante. Lire aussi `cloudflare/README.md` (runbook) et CLAUDE.md (« Décisions fondateur »).

## 1. Migration Replit → Cloudflare

Fait :
- Scripts via l'API SQL HTTPS de Neon (`cloudflare/neon_http.py`), base Replit toujours en transaction READ ONLY.
- `check_access.py` tout OK : clé des numéros prouvée identique à Replit (228/229 ; l'abonné id 1 est illisible aussi sur Replit).
- Worker + conteneur déployés : https://calendrier-tempo.thomas-issa.workers.dev (mode test : sans WhatsApp ni agents IA).
- Copie Replit → Neon faite et vérifiée (empreintes MD5 identiques), à REFAIRE juste avant la bascule.
- Zone DNS Cloudflare créée et ACTIVE (`dns_zone.py`, 14 enregistrements IONOS, site encore sur Replit, TTL 60 s). Serveurs DNS changés chez IONOS par Thomas.
- Mode test WhatsApp (`WHATSAPP_TEST_NUMBERS`) : diffusions réservées au numéro de Thomas ; `push_secrets.py --prod` le rend obligatoire ; `--sans-mode-test` pour ouvrir à tous quand Thomas valide.

**Bascule FAITE le 2026-10-01 à 7h02** (voir cloudflare/README.md, phase 2). Reste : réparer WhatsApp (point ouvert ci-dessous), puis `push_secrets.py --prod --sans-mode-test` quand Thomas valide. Ancienne procédure, pour mémoire :
1. Vérifier que `TEMPO_WHATSAPP_TEST_NUMBERS` et `WHATSAPP_APP_SECRET` sont lus (nouvelle session), sans les afficher. Thomas doit être inscrit aux alertes avec ce numéro.
2. `check_access.py`, puis `copy_database.py`, puis `push_secrets.py --prod`.
3. Sur workers.dev : `/admin/whatsapp-diagnostic` (token_set, test_mode true), pages, admin.
4. DNS `www` + apex → `custom_domain` du Worker (décommenter `routes` dans `wrangler.jsonc`, `wrangler deploy`), vérifier www depuis Cloudflare.
5. Dire à Thomas « Stop maintenant » dans Replit (avant 17 h 30), puis `merge_users.py`.
6. Tests WhatsApp sur le numéro de Thomas jusqu'à sa validation, puis `push_secrets.py --prod --sans-mode-test`.
Après un déploiement : attendre la bascule du conteneur et vérifier un marqueur PROPRE au nouveau code dans le HTML avant toute capture (leçon du 2026-09-30).

## 2. Refonte du site (sur la branche, en ligne sur workers.dev)

Fait : audits @ux/@design, premiers écrans, relecture @copywriter, météo dans les 15 jours, historique sans case vide (décision Thomas), taux publics = dernière saison complète (2025-2026 : 90 %), accueil allégé (un seul bouton), rattrapage du calcul de 18 h (18h45/20h/22h, sans WhatsApp).

Revue notée, tour 1 (docs/audits/2026-09-30-revue-tour1-ux.md et -design.md) : moyennes UX 8,7 / 8,1 (ordinateur / mobile), design 8,3 / 7,9. Corrections du tour 1 appliquées, commitées (ab4458c), déployées et vérifiées en ligne (version d'assets 20260930e, 10 pages en 200, 0 case vide dans l'historique). Non faits au tour 1 : MET-T2 (Performance et Limites en tête de /methodologie, accord de Thomas à demander), ALE-T1 (exemple d'alerte rouge : gabarit Meta hors dépôt), API-T2, ACC-T4 (température sous Aujourd'hui/Demain), ALE-T4, APR-T1/T2, G4 (P2).

Reste à faire :
- Tour 2 de revue notée (@ux + @design) sur la version en ligne, puis corrections, jusqu'à 9/10 minimum partout (exigence Thomas). Captures : attendre `.forecast-card` sur /calendrier ; fournir des tranches à l'échelle 1 pour les pages longues (article, mentions légales, historique mobile) ; inclure la fenêtre d'inscription et l'admin.
- Leçon du 2026-10-01 (accueil noté 4/10 par Thomas alors que les agents donnaient 8,5) : toute revue se fait CÔTE À CÔTE avec la version de référence que Thomas apprécie (captures « avant », images/4.webp pour l'accueil), et la note de Thomas prime. Chaque passe ne doit pas ajouter de texte : sobriété et espace d'abord. L'accueil revient à l'ancienne mise en page (2 rangées de 5 pastilles égales, une ligne de message, un bouton).
- NE PAS appliquer : distinction visuelle des cases remplies de l'historique, retrait de la coche/anneau vert (décisions Thomas).

## 3. Points ouverts

- **WhatsApp HS depuis le 31/03/2026 (constat du 2026-10-01)** : base Replit, dernier envoi réussi le 21/03, puis 456 échecs « API access blocked » (code 200) jusqu'au 31/03, plus rien depuis. Numéro expéditeur côté Meta : status PENDING, platform_type NOT_APPLICABLE, code_verification_status EXPIRED ; tout envoi renvoie 133010 « Account not registered » (testé sur le seul numéro de test, rien n'est parti). À refaire dans le WhatsApp Manager de Thomas : vérifier pourquoi l'accès a été bloqué en mars (paiement, restriction), revalider le numéro (code SMS/appel), puis l'enregistrer sur l'API Cloud (PIN à 6 chiffres). À régler avant le 1er novembre (début des jours rouges). Le format national du numéro de test (06…) est corrigé (config._parse_phone_list).

- Mentions légales §7 (« prévisions 5 jours », « éco2mix temps réel ») à mettre à jour avec @legal ; relecture par un avocat conseillée.
- « Numéro jamais partagé » retiré de la fenêtre d'inscription : à confirmer avec Thomas.
- Rattrapage 18 h : se déclenche seulement si AUCUNE prévision du jour (celle de 7 h 30 suffit) ; à durcir si Thomas veut exiger l'émission de 18 h.
- Première semaine sur Cloudflare : vérifier chaque jour « derniere_emission » et « jours_sans_calcul_30j » (admin, onglet Actions).
- Après 7 jours sans incident : Thomas peut résilier Replit (garder un export).
