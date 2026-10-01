/**
 * Calendrier Tempo EDF — JavaScript principal du dashboard v3.
 *
 * Refonte UX : langage humain, conseils actionnables, format tel FR,
 * résumé hebdomadaire, prix concrets.
 */

const JOURS = ['dim.', 'lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.'];
const JOURS_FULL = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi'];
const MOIS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];
const MOIS_FULL = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

// Tarifs indicatifs Tempo au 1er août 2026 (€/kWh TTC — vérifiez sur votre contrat EDF)
const TARIFS = {
    BLEU:  { hp: 0.1654, hc: 0.1356 },
    BLANC: { hp: 0.1921, hc: 0.1536 },
    ROUGE: { hp: 0.7295, hc: 0.1615 },
};

// M-04 QA : helper fetch avec timeout (10s par défaut)
function fetchWithTimeout(url, options = {}, timeoutMs = 10000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(timer));
}

// ================================================================
// INITIALISATION
// ================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Charger toutes les données en parallèle (au lieu de séquentiellement)
    loadAllData();
    setupBackToTop();
    setupUnsubscribeForm();
    setupWelcomeBanner();
    checkHorsSaison();

    // Fix #31 : auto-refresh toutes les 5 min pour refléter
    // les confirmations EDF sans recharger la page manuellement
    setInterval(() => {
        // Propager confirmations EDF avant de recharger les prédictions
        fetchWithTimeout('/api/today', {}, 3000).catch(() => {});
        fetchWithTimeout('/api/tomorrow', {}, 3000).catch(() => {});
        setTimeout(() => {
            Promise.all([
                loadRemaining(),
                loadPredictions(),
                loadWeekSummary(),
            ]);
        }, 1000); // laisser 1s aux appels EDF pour propager
    }, 5 * 60 * 1000);
});

/**
 * Charge toutes les données. Si l'API n'est pas encore prête (cold start),
 * retente avec backoff exponentiel : 2s, 4s, 6s, 8s, 10s (= 30s max).
 * Si toutes les tentatives échouent, planifie un dernier essai à 30s
 * pour couvrir les cold starts lents (Replit free tier).
 */
async function loadAllData(attempt = 0) {
    // Étape 1 : Appeler /api/today et /api/tomorrow pour propager les confirmations EDF
    // vers la DB (store_actual + confirm_prediction). Ces appels DOIVENT précéder
    // loadPredictions() pour que les prédictions reflètent les couleurs confirmées.
    try {
        const todayResp = await fetchWithTimeout('/api/today', {}, 5000);
        if (todayResp.status === 503 && attempt < 5) {
            const delay = (attempt + 1) * 2000;
            setTimeout(() => loadAllData(attempt + 1), delay);
            return;
        }
    } catch {
        if (attempt < 5) {
            const delay = (attempt + 1) * 2000;
            setTimeout(() => loadAllData(attempt + 1), delay);
            return;
        }
        // Dernier recours : un retry isolé à 30s pour les cold starts très lents
        if (attempt === 5) {
            setTimeout(() => loadAllData(6), 30000);
            return;
        }
    }
    // /api/tomorrow : propage la confirmation EDF demain (non bloquant)
    fetchWithTimeout('/api/tomorrow', {}, 5000).catch(() => {});

    // Étape 2 : Charger les données d'affichage en parallèle
    await Promise.all([
        loadRemaining(),
        loadPredictions(),
        loadWeekSummary(),
        loadBadge(),
    ]);
}

// ================================================================
// CHARGEMENT DES DONNÉES
// ================================================================

async function loadToday() {
    const el = document.getElementById('today-card');
    if (!el) return;
    try {
        const resp = await fetchWithTimeout('/api/today');
        if (!resp.ok) { el.innerHTML = '<p class="loading-state">Données non disponibles</p>'; return; }
        const data = await resp.json();
        if (data && data.status === 'ok') {
            renderTodayCard(el, data, "Aujourd'hui");
        } else {
            el.innerHTML = '<p class="loading-state">Données non disponibles</p>';
        }
    } catch {
        el.innerHTML = '<p class="loading-state">Erreur de connexion<br><button class="retry-btn" onclick="loadToday()">Réessayer</button></p>';
    }
}

async function loadTomorrow() {
    const el = document.getElementById('tomorrow-card');
    if (!el) return;
    try {
        const resp = await fetchWithTimeout('/api/tomorrow');
        if (!resp.ok) { el.innerHTML = '<p class="loading-state">Données non disponibles</p>'; return; }
        const data = await resp.json();
        if (data && data.status === 'ok') {
            renderTodayCard(el, data, 'Demain');
        } else {
            // Couleur de demain pas encore connue
            el.innerHTML = `
                <h3>DEMAIN</h3>
                <div class="couleur-circle couleur-UNKNOWN" role="img" aria-label="En attente">?</div>
                <div style="font-size:1.1rem;font-weight:600;margin:4px 0;color:var(--text-secondary)">En attente</div>
                <div class="date-text">EDF n'a pas encore publi&eacute; la couleur de demain.<br>Elle sera d&eacute;tect&eacute;e automatiquement d&egrave;s sa publication.</div>
            `;
        }
    } catch {
        el.innerHTML = '<p class="loading-state">Erreur de connexion<br><button class="retry-btn" onclick="loadTomorrow()">Réessayer</button></p>';
    }
}

async function loadRemaining() {
    try {
        const resp = await fetchWithTimeout('/api/remaining');
        if (!resp.ok) { throw new Error(`HTTP ${resp.status}`); }
        const data = await resp.json();
        if (!data || data.status !== 'ok') {
            setText('count-rouge', '-');
            setText('count-blanc', '-');
            setText('count-bleu', '-');
            setText('days-left', 'Données temporairement indisponibles');
            return;
        }

        const r = data.remaining;
        const t = data.totals;
        const totalRouge = t ? t.ROUGE : 22;
        const totalBlanc = t ? t.BLANC : 43;
        const totalBleu  = t ? t.BLEU : 208;
        ['count-rouge', 'count-blanc', 'count-bleu'].forEach(id => {
            const el = document.getElementById(id);
            if (el) { el.classList.remove('count-pending'); el.removeAttribute('aria-label'); }
        });
        setText('count-rouge', `${r.ROUGE}/${totalRouge}`);
        setText('count-blanc', `${r.BLANC}/${totalBlanc}`);
        setText('count-bleu',  `${r.BLEU}/${totalBleu}`);
        setText('days-left', `${data.days_left_in_season} jour${data.days_left_in_season > 1 ? 's' : ''} avant la fin de la saison ${data.season_start ? data.season_start.slice(0,4) : ''}-${data.season_end ? data.season_end.slice(0,4) : ''}`);

        // P-21 : barres de progression visuelles
        setProgress('progress-rouge', (totalRouge - r.ROUGE) / totalRouge);
        setProgress('progress-blanc', (totalBlanc - r.BLANC) / totalBlanc);
        setProgress('progress-bleu',  (totalBleu  - r.BLEU)  / totalBleu);

        // P-06 : contexte humain pour Paul
        // Les jours rouges ne peuvent être placés que du 1er nov au 31 mars (R1).
        const ctxEl = document.getElementById('counter-context');
        if (ctxEl && data.season_end) {
            const seasonEndYear = new Date(data.season_end).getFullYear();
            const rougeDeadline = new Date(seasonEndYear, 2, 31); // 31 mars
            const daysLeftRouge = Math.max(0, Math.ceil((rougeDeadline - new Date()) / 86400000));
            if (r.ROUGE === 0) {
                ctxEl.textContent = `Les ${totalRouge} jours rouges de la saison ont été utilisés : il n'y en aura plus avant novembre.`;
            } else if (daysLeftRouge === 0) {
                ctxEl.textContent = 'La période des jours rouges est terminée (fin mars). Prochains jours rouges possibles dès novembre.';
            } else if (daysLeftRouge < 60) {
                // Fréquence en langage naturel (1 jour rouge par X jours)
                const freq = Math.round(daysLeftRouge / r.ROUGE);
                ctxEl.innerHTML = `<strong style="color:var(--rouge)">Attention\u00a0:</strong> il reste ${r.ROUGE} jour${r.ROUGE > 1 ? 's' : ''} rouge${r.ROUGE > 1 ? 's' : ''} à placer d'ici le 31 mars (${daysLeftRouge} jours), soit environ <strong>1 tous les ${freq} jours</strong>.`;
            } else {
                ctxEl.textContent = `Encore ${r.ROUGE} jours rouges à placer d'ici fin mars.`;
            }
        }
    } catch (e) {
        setText('count-rouge', '-');
        setText('count-blanc', '-');
        setText('count-bleu', '-');
        const daysEl = document.getElementById('days-left');
        if (daysEl) {
            daysEl.innerHTML = 'Erreur de connexion <button class="retry-btn" onclick="loadRemaining()">Réessayer</button>';
        }
        console.error('Erreur chargement compteurs:', e);
    }
}

async function loadPredictions() {
    const container = document.getElementById('forecast-container');
    if (!container) return;
    container.innerHTML = '<div class="loading-state"><div class="loader"></div><p>Chargement des prévisions…</p></div>';

    try {
        const resp = await fetchWithTimeout('/api/predictions');
        if (!resp.ok) { throw new Error(`HTTP ${resp.status}`); }
        const data = await resp.json();
        if (!data || data.status !== 'ok') {
            container.innerHTML = '<p class="loading-state">Les prévisions n’ont pas pu être chargées.<br><button class="retry-btn" onclick="loadPredictions()">Réessayer</button></p>';
            return;
        }

        container.innerHTML = '';

        const preds = data.predictions || [];

        if (preds.length === 0) {
            container.innerHTML = '<p class="loading-state">' +
                escapeHtml(data.message || 'Aucune prévision disponible pour le moment.') + '</p>';
            return;
        }

        // Date de dernière mise à jour (timezone Paris) — affichée en haut de page
        if (data.generated_at) {
            updateLastUpdateBar(data.generated_at);
        }

        // Résumé de la semaine
        renderWeekSummary(preds);

        // Météo : une ligne d'explication en tête, ou une note unique si aucune température
        const hasTemp = preds.some(p => fmtTemp(p.temp_moy_prevue) !== null);
        const note = document.createElement('p');
        note.className = 'forecast-temp-note';
        if (hasTemp) {
            note.innerHTML = 'Sous chaque jour : température moyenne prévue en France, pondérée sur 9 villes. '
                + 'Mise à jour à chaque recalcul. <a href="/methodologie">Méthode</a>';
        } else {
            note.textContent = 'Les températures prévues ne sont pas disponibles pour le moment.';
        }
        container.appendChild(note);
        const cardOpts = { noTemp: !hasTemp };

        // Grouper les prédictions par horizon avec labels humains
        const groupPrimary = preds.slice(0, 3);
        const groupMedium  = preds.slice(3, 7);
        const groupFar     = preds.slice(7);

        if (groupPrimary.length > 0) {
            const label1 = document.createElement('div');
            label1.className = 'forecast-group-label';
            label1.textContent = 'Les 3 prochains jours : nos prévisions les plus fiables';
            container.appendChild(label1);

            const grid1 = document.createElement('div');
            grid1.className = 'forecast-grid forecast-grid-primary';
            groupPrimary.forEach(pred => {
                grid1.appendChild(createForecastCard(pred, cardOpts));

            });
            container.appendChild(grid1);
        }

        if (groupMedium.length > 0) {
            const label2 = document.createElement('div');
            label2.className = 'forecast-group-label';
            label2.textContent = 'Les jours suivants : prévisions moins sûres';
            container.appendChild(label2);

            const grid2 = document.createElement('div');
            grid2.className = 'forecast-grid';
            groupMedium.forEach(pred => {
                grid2.appendChild(createForecastCard(pred, cardOpts));

            });
            container.appendChild(grid2);
        }

        if (groupFar.length > 0) {
            const label3 = document.createElement('div');
            label3.className = 'forecast-group-label';
            label3.textContent = 'Semaine prochaine et au-delà\u00a0: tendances indicatives';
            container.appendChild(label3);

            // UX audit #27: explanation for distant predictions
            const hint3 = document.createElement('div');
            hint3.className = 'forecast-group-hint';
            hint3.textContent = 'Ces tendances évoluent souvent. Revenez dans 2 ou 3 jours pour les confirmer.';
            hint3.style.cssText = 'font-size:0.8rem;color:var(--text-secondary);margin:-6px 0 10px;font-style:italic;';
            container.appendChild(hint3);

            // Jours lointains : pastille en pointillé (CSS), tuiles compactes en mobile
            const grid3 = document.createElement('div');
            grid3.className = 'forecast-grid forecast-grid-far';
            groupFar.forEach(pred => {
                grid3.appendChild(createForecastCard(pred, { ...cardOpts, compact: true }));

            });
            container.appendChild(grid3);
        }

    } catch (e) {
        container.innerHTML = `
            <div class="maintenance-banner">
                <div class="maintenance-icon">&#9881;</div>
                <h3 style="margin:0 0 6px">Prévisions momentanément indisponibles</h3>
                <p style="margin:0;font-size:.9rem;color:var(--text-secondary)">
                    Nous n'avons pas pu les charger.<br>
                    Réessayez dans quelques minutes.
                </p>
                <button class="retry-btn" onclick="loadPredictions()" style="margin-top:12px">Réessayer</button>
            </div>`;
        console.error('Erreur prédictions:', e);
    }
}

/**
 * Charge le résumé des 10 prochains jours indépendamment de loadPredictions.
 * Sur la homepage, #forecast-container n'existe pas → loadPredictions() fait
 * un early return et renderWeekSummary() n'est jamais appelée. Cette fonction
 * garantit que le résumé est toujours mis à jour via JS (fallback si SSR vide
 * lors d'un cold start).
 */
async function loadWeekSummary() {
    const container = document.getElementById('week-summary');
    if (!container) return;
    try {
        const resp = await fetchWithTimeout('/api/predictions');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (!data || data.status !== 'ok') throw new Error('status not ok');
        const preds = data.predictions || [];
        if (preds.length === 0) throw new Error('no predictions');
        if (data.generated_at) {
            updateLastUpdateBar(data.generated_at);
        }
        renderWeekSummary(preds);
    } catch {
        // Si le SSR a déjà rendu des dots, on les garde. Sinon, afficher un message maintenance.
        if (!container.querySelector('.week-dot')) {
            container.innerHTML = `
                <div class="maintenance-banner">
                    <div class="maintenance-icon">&#9881;</div>
                    <h3 style="margin:0 0 6px">Prévisions momentanément indisponibles</h3>
                    <p style="margin:0;font-size:.9rem;color:var(--text-secondary)">
                        Nous n'avons pas pu les charger.<br>
                        Réessayez dans quelques minutes.
                    </p>
                    <button class="retry-btn" onclick="loadWeekSummary()" style="margin-top:12px">Réessayer</button>
                </div>`;
        }
    }
}

async function loadBadge() {
    // FAQ « Vos prévisions sont-elles fiables ? » : le taux est rendu côté serveur
    // (dernière saison complète). Ce complément ne sert qu'au démarrage à froid,
    // quand la page a été servie sans données. Jamais de qualificatif flatteur : le chiffre seul.
    const faqText = document.getElementById('faq-precision-text');
    if (!faqText || document.getElementById('faq-precision-value')) return;
    try {
        const resp = await fetchWithTimeout('/api/performance/badge');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data || data.status !== 'ok' || data.precision == null) return;
        faqText.innerHTML = `${escapeHtml(data.label)} Détail par couleur dans `
            + `<a href="${escapeHtml(data.detail_url || '/historique-previsions')}">l'historique de nos prévisions</a>.`;
    } catch (e) {
        console.error('Erreur badge:', e);
    }
}

// ================================================================
// RÉSUMÉ DE LA SEMAINE
// ================================================================

const COULEUR_NOM = { BLEU: 'Bleu', BLANC: 'Blanc', ROUGE: 'Rouge' };

// "jeudi 1er octobre" / "lundi 5" (miroir de site_facts.fr_date et _jour_court)
function frJour(d, withMonth) {
    const num = d.getDate() === 1 ? '1er' : String(d.getDate());
    const txt = `${JOURS_FULL[d.getDay()].toLowerCase()} ${num}`;
    return withMonth ? `${txt} ${MOIS_FULL[d.getMonth()]}` : txt;
}

/**
 * Ligne de message en tête de la carte « Résumé des 10 prochains jours ».
 * Miroir exact de site_facts.week_outlook_html : modifier les deux ensemble.
 */
function weekOutlookHtml(days) {
    if (days.length === 0) return '';
    const fin = `d'ici le ${frJour(days[days.length - 1].d, true)}`;
    const parts = [];
    [['ROUGE', 'rouge'], ['BLANC', 'blanc']].forEach(([couleur, nom]) => {
        const jours = days.filter(x => x.couleur === couleur)
            .map(x => (x.isToday ? 'aujourd\'hui' : x.isTomorrow ? 'demain' : frJour(x.d, false)));
        if (jours.length > 0) {
            const s = jours.length > 1 ? 's' : '';
            parts.push(`<strong>${jours.length} jour${s} ${nom}${s}</strong> (${jours.join(', ')})`);
        }
    });
    if (parts.length === 0) {
        return `Bonne nouvelle&nbsp;: <strong>aucun jour rouge ni blanc</strong> en vue ${fin}. Consommez normalement&nbsp;!`;
    }
    return `${parts.join(' et ')} en vue ${fin}. Planifiez vos machines les jours bleus.`;
}

// Une pastille : même HTML que la boucle de templates/dashboard.html (test de parité)
function weekDotHtml(x) {
    const shapeClass = x.couleur === 'BLANC' ? ' dot-blanc' : x.couleur === 'ROUGE' ? ' dot-rouge' : '';
    const confirmedClass = x.confirmed ? ' confirmed-dot' : '';
    const label = x.isToday ? '<strong>Aujourd\'hui</strong>'
        : x.isTomorrow ? '<strong>Demain</strong>'
        : `${JOURS_FULL[x.d.getDay()].slice(0, 3)} ${x.d.getDate()}`;
    const statut = x.confirmed ? 'couleur officielle' : `prévision ${x.confidence}&nbsp;%`;
    // Aujourd'hui et Demain : couleur écrite en toutes lettres (coche verte si officielle)
    const hero = x.isToday || x.isTomorrow;
    const proba = `<span class="week-dot-proba">${x.confidence}&nbsp;%</span>`;
    const info = hero
        ? (x.confirmed ? `<span class="week-dot-confirmed">&#10003;&nbsp;${COULEUR_NOM[x.couleur]}</span>`
            : `<span class="week-dot-color">${COULEUR_NOM[x.couleur]}</span> ${proba}`)
        : (x.confirmed ? '<span class="week-dot-confirmed">Confirmé</span>' : proba);
    // Pastille Demain : lien discret vers /couleur-tempo-demain, même aspect que les autres
    const open = x.isTomorrow
        ? `<a class="week-dot week-dot-highlight week-dot-hero week-dot-link" href="/couleur-tempo-demain" aria-label="Couleur Tempo de demain, ${frJour(x.d, true)} : ${COULEUR_NOM[x.couleur]}, ${x.confirmed ? 'confirmée par EDF' : `prévision ${x.confidence}&nbsp;%`}">`
        : `<div class="week-dot${x.isToday ? ' week-dot-highlight week-dot-hero' : ''}">`;
    return open
        + `<div class="week-dot-circle${shapeClass}${confirmedClass}" style="background:var(--${x.couleur.toLowerCase()})" aria-hidden="true">${x.couleur[0]}</div>`
        + (x.isTomorrow ? '' : `<span class="sr-only">${frJour(x.d, true)} : ${COULEUR_NOM[x.couleur]}, ${statut}</span>`)
        + `<span class="week-dot-label" aria-hidden="true">${label}</span>`
        + `<span class="week-dot-info" aria-hidden="true">${info}</span>`
        + (x.isTomorrow ? '</a>' : '</div>');
}

function renderWeekSummary(preds) {
    const container = document.getElementById('week-summary');
    if (!container) return;

    // Les 10 premiers jours, 2 rangées de 5 séparées par un filet
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const days = preds.slice(0, 10)
        .filter(p => COULEUR_NOM[p.couleur_predite])
        .map(p => {
            const d = parseLocalDate(p.date);
            const prob = p[`probabilite_${p.couleur_predite.toLowerCase()}`] || 0;
            return {
                d, couleur: p.couleur_predite, confirmed: !!p.confirmed,
                confidence: Math.round(prob * 100),
                isToday: d.getTime() === today.getTime(),
                isTomorrow: d.getTime() === tomorrow.getTime(),
            };
        });
    if (days.length === 0) return;

    const dots = days.map((x, i) => (i === 5 ? '<div class="week-dots-separator" aria-hidden="true"></div>' : '') + weekDotHtml(x));
    const card = document.getElementById('week-summary-card');
    if (card) card.classList.toggle('has-rouge', days.some(x => x.couleur === 'ROUGE'));
    container.innerHTML = `<div class="week-summary-dots">${dots.join('')}</div>`;
    const outlookEl = document.getElementById('week-outlook');
    if (outlookEl) {
        const outlook = weekOutlookHtml(days);
        outlookEl.innerHTML = outlook;
        outlookEl.hidden = !outlook;
    }
}

// ================================================================
// RENDU DES COMPOSANTS
// ================================================================

/**
 * Retourne un conseil actionnable selon la couleur.
 */
function getTipForColor(couleur) {
    switch (couleur) {
        case 'ROUGE':
            return { text: 'Reportez lessive, sèche-linge, four et recharge VE', css: 'tip-rouge' };
        case 'BLANC':
            return { text: 'Tarif moyen, pas de précaution particulière', css: 'tip-blanc' };
        case 'BLEU':
            return { text: 'Tarif avantageux : consommez librement !', css: 'tip-bleu' };
        default:
            return null;
    }
}

/**
 * Retourne un label de prix pour la couleur.
 */
function getPriceLabel(couleur) {
    const t = TARIFS[couleur];
    if (!t) return '';
    return `HP ${t.hp.toFixed(2).replace('.', ',')} €/kWh`;
}

/**
 * Convertit une confiance (0-100%) en label humain.
 */
function confidenceToLabel(confidence) {
    if (confidence >= 85) return 'Très probable';
    if (confidence >= 70) return 'Probable';
    if (confidence >= 55) return 'Assez probable';
    if (confidence >= 40) return 'Incertain';
    return 'Peu probable';
}

function renderTodayCard(el, data, label) {
    const VALID_COULEURS = ['BLEU', 'BLANC', 'ROUGE', 'UNKNOWN'];
    const couleur = VALID_COULEURS.includes(data.couleur) ? data.couleur : 'UNKNOWN';
    const couleurLabel = couleur === 'UNKNOWN' ? 'Inconnu' : couleur;
    const dateStr = data.date ? formatDateFr(data.date) : '';

    let tipHtml = '';
    const tip = getTipForColor(couleur);
    if (tip) {
        tipHtml = `<div class="today-tip ${tip.css}">${escapeHtml(tip.text)}</div>`;
    }

    let priceHtml = '';
    if (couleur !== 'UNKNOWN') {
        priceHtml = `<div class="today-price" style="color:${couleur === 'ROUGE' ? 'var(--rouge)' : 'var(--text-secondary)'}">${escapeHtml(getPriceLabel(couleur))}</div>`;
    }

    el.innerHTML = `
        <h3>${escapeHtml(label)}</h3>
        <div class="couleur-circle couleur-${couleur}" role="img" aria-label="Couleur ${escapeHtml(couleurLabel)}">${couleur === 'UNKNOWN' ? '?' : couleur[0]}</div>
        <div style="font-size:1.1rem;font-weight:600;margin:4px 0">${escapeHtml(couleurLabel)}</div>
        ${priceHtml}
        <div class="date-text">${escapeHtml(dateStr)}</div>
        ${tipHtml}
    `;
}

// ================================================================
// MÉTÉO DES PRÉVISIONS (audits UX et design du 2026-09-30)
// Température moyenne prévue, pondérée sur 9 villes (predictions.temp_moy_prevue),
// même format que /historique-previsions : une décimale, virgule, vrai signe moins, « ° ».
// Miroir de site_facts.fr_temp (même arrondi, mêmes bornes de plausibilité).
// ================================================================
const TEMP_MIN_PLAUSIBLE = -30;
const TEMP_MAX_PLAUSIBLE = 45;

// 14.06 -> '14,1°' ; -3.2 -> '−3,2°' ; -0.04 -> '0,0°' ; absent ou aberrant -> null
function fmtTemp(v) {
    if (v === null || v === undefined || v === '') return null;
    const n = Number(v);
    if (!Number.isFinite(n) || n < TEMP_MIN_PLAUSIBLE || n > TEMP_MAX_PLAUSIBLE) return null;
    let r = Math.round(n * 10) / 10;
    if (r === 0) r = 0;  // évite « -0,0 »
    return `${r < 0 ? '−' : ''}${Math.abs(r).toFixed(1).replace('.', ',')}°`;
}

function meteoHtml(pred) {
    const t = fmtTemp(pred.temp_moy_prevue);
    if (t === null) {
        return '<div class="fc-temp fc-temp-nd">Météo indisponible<span class="sr-only"> (température prévue indisponible)</span></div>';
    }
    const lu = t.replace('−', 'moins ').replace('°', ' degrés Celsius');
    return `<div class="fc-temp"><span class="fc-temp-val" aria-hidden="true">${t}</span>`
        + `<span class="sr-only">Température moyenne prévue ${lu}</span></div>`;
}

const ICON_WARN = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>';

function createForecastCard(pred, opts = {}) {
    const VALID_COULEURS = ['BLEU', 'BLANC', 'ROUGE'];
    const couleur = VALID_COULEURS.includes(pred.couleur_predite) ? pred.couleur_predite : 'BLEU';

    const card = document.createElement('div');
    card.className = `forecast-card color-${couleur}`;
    if (opts.compact) card.classList.add('fc-compact');

    if (pred.confirmed) {
        card.classList.add('confirmed');
    }

    const d = parseLocalDate(pred.date);
    const dow = JOURS[d.getDay()];
    const dayNum = d.getDate();
    const month = MOIS[d.getMonth()];

    const probKey = `probabilite_${couleur.toLowerCase()}`;
    const confidence = Math.round((pred[probKey] || 0) * 100);

    // Probabilités des 3 couleurs
    const pRouge = pred.probabilite_rouge || 0;
    const pBlanc = pred.probabilite_blanc || 0;
    const pBleu  = pred.probabilite_bleu  || 0;

    // Aucune température sur toute la période : une note unique au-dessus des cartes
    const tempHtml = opts.noTemp ? '' : meteoHtml(pred);

    // Raison technique supprimée (reco 3 audit UX — pas utile pour le prospect)

    let confirmedHtml = '';
    if (pred.confirmed) {
        confirmedHtml = '<div class="fc-confirmed">Confirmé par EDF</div>';
    }

    let changedHtml = '';
    if (pred.couleur_precedente) {
        changedHtml = `<div class="fc-changed" title="Notre prévision a changé avec les nouvelles données météo.">Avant : ${escapeHtml(COULEUR_NOM[pred.couleur_precedente] || pred.couleur_precedente)}</div>`;
    }

    // Confiance en langage humain (% visible dans la barre de proba en dessous)
    const confidenceLabel = confidenceToLabel(confidence);
    const confidenceText = pred.confirmed
        ? ''
        : escapeHtml(confidenceLabel);

    // Barre tricolore de probabilités (masquée si confirmé)
    let probaBarHtml = '';
    if (!pred.confirmed) {
        const pctRouge = Math.round(pRouge * 100);
        const pctBlanc = Math.round(pBlanc * 100);
        const pctBleu  = Math.round(pBleu * 100);

        // Construire les segments (n'afficher que ceux > 5%)
        const segments = [];
        if (pctBleu > 5)  segments.push(`<div class="proba-seg seg-bleu" style="width:${pctBleu}%"></div>`);
        if (pctBlanc > 5) segments.push(`<div class="proba-seg seg-blanc" style="width:${pctBlanc}%"></div>`);
        if (pctRouge > 5) segments.push(`<div class="proba-seg seg-rouge" style="width:${pctRouge}%"></div>`);

        // Labels sous la barre (n'afficher que ceux > 10%)
        const labels = [];
        if (pctBleu > 10)  labels.push(`<span class="proba-label"><span class="proba-dot" style="background:var(--bleu)"></span>${pctBleu}%</span>`);
        if (pctBlanc > 10) labels.push(`<span class="proba-label"><span class="proba-dot" style="background:var(--blanc)"></span>${pctBlanc}%</span>`);
        if (pctRouge > 10) labels.push(`<span class="proba-label"><span class="proba-dot" style="background:var(--rouge)"></span>${pctRouge}%</span>`);

        probaBarHtml = `
            <div class="fc-proba-bar" role="img" aria-label="Probabilités : bleu ${pctBleu}%, blanc ${pctBlanc}%, rouge ${pctRouge}%">
                ${segments.join('')}
            </div>
            <div class="fc-proba-labels">${labels.join('')}</div>`;
    }

    // Badge incertitude si les 2 premières probas sont proches (écart < 30%)
    let uncertainHtml = '';
    if (!pred.confirmed) {
        const probs = [
            { color: 'rouge', val: pRouge },
            { color: 'blanc', val: pBlanc },
            { color: 'bleu',  val: pBleu },
        ].sort((a, b) => b.val - a.val);

        const gap = probs[0].val - probs[1].val;
        if (gap < 0.30 && probs[1].val > 0.15) {
            const colorNames = { rouge: 'Rouge', blanc: 'Blanc', bleu: 'Bleu' };
            uncertainHtml = `<div class="fc-uncertain-badge">`
                + `<span class="uncertain-icon" aria-hidden="true">${ICON_WARN}</span>`
                + `Hésitation<span class="fc-hesi-colors"> ${colorNames[probs[0].color]}/${colorNames[probs[1].color]}</span></div>`;
        }
    }

    card.innerHTML = `
        <div class="fc-day">${escapeHtml(dow)}</div>
        <div class="fc-date">${dayNum === 1 ? '1er' : dayNum} ${escapeHtml(month)}</div>
        <span class="fc-couleur ${couleur}" role="img" aria-label="${pred.confirmed ? 'Couleur officielle' : 'Couleur prévue'} : ${COULEUR_NOM[couleur]}">${escapeHtml(couleur)}</span>
        ${confirmedHtml}
        ${tempHtml}
        <div class="fc-confidence">${confidenceText}</div>
        ${uncertainHtml}
        ${probaBarHtml}
        ${changedHtml}
    `;

    // P-22 : animation d'entrée staggerée
    card.classList.add('fc-animate');

    return card;
}

/**
 * Transforme la raison technique en texte compréhensible.
 */
function simplifyRaison(raison) {
    if (!raison) return '';

    // Remplacements de termes techniques
    let s = raison;

    // Patterns techniques → humain
    if (/vague.+froid/i.test(s)) return 'Vague de froid détectée';
    if (/chute.+temp/i.test(s)) return 'Forte baisse des températures';
    if (/budget.*[8-9]\d?%|urgence.*budget/i.test(s)) return 'Beaucoup de jours rouges encore à placer';

    // Nettoyer les termes techniques résiduels
    s = s.replace(/Score temp \d+/gi, 'Froid')
         .replace(/Budget \d+[A-Z]?\/\d+j?/gi, 'Pression budgétaire')
         .replace(/Gradient -?\d+°C/gi, 'Chute de température')
         .replace(/\bMardi \(pic\)/gi, 'Mardi (jour à risque)')
         .replace(/Conso RTE.*/gi, 'Forte demande électrique')
         .replace(/\s*·\s*/g, ', ');

    // Limiter la longueur
    if (s.length > 60) s = s.substring(0, 57) + '...';

    return s;
}

// ================================================================
// FORMULAIRE INSCRIPTION SMS
// ================================================================

/**
 * Normalise un numéro de mobile vers le format international (+33, +32, +41, +352, +49).
 * Même règle que l'inscription : délègue à normalizePhoneIntl (fenêtre d'inscription,
 * incluse sur toutes les pages publiques). Sans elle : France uniquement.
 */
function normalizePhone(input) {
    if (typeof window !== 'undefined' && typeof window.normalizePhoneIntl === 'function') {
        return window.normalizePhoneIntl(input || '');
    }
    const cleaned = (input || '').replace(/[\s.\-()]/g, '');
    if (/^0[67]\d{8}$/.test(cleaned)) return '+33' + cleaned.substring(1);
    if (/^\+?33[67]\d{8}$/.test(cleaned)) return '+' + cleaned.replace(/^\+/, '');
    return null;
}

function showFormResult(el, type, message) {
    el.className = `form-result ${type}`;
    el.textContent = message;
    el.style.display = 'block';
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ================================================================
// WELCOME BANNER (dismissable, remembered in localStorage)
// ================================================================

function setupWelcomeBanner() {
    // Welcome banner is now always visible (no close button)
}

// ================================================================
// UTILITAIRES
// ================================================================

/**
 * Parse une date ISO "YYYY-MM-DD" en date LOCALE (pas UTC).
 * Fix #32 : new Date("2026-02-10") crée une date UTC midnight,
 * ce qui décale getDay()/getDate() pour les utilisateurs en CET.
 */
function parseLocalDate(dateStr) {
    const [y, m, d] = dateStr.split('-').map(Number);
    return new Date(y, m - 1, d);
}

function formatDateFr(dateStr) {
    const d = parseLocalDate(dateStr);
    const dow = JOURS_FULL[d.getDay()].toLowerCase();
    return `${dow} ${d.getDate()} ${MOIS_FULL[d.getMonth()]}`;
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// P-21 : mise à jour d'une barre de progression
function setProgress(id, ratio) {
    const el = document.getElementById(id);
    if (!el) return;
    const fill = el.querySelector('.counter-progress-fill');
    if (fill) fill.style.width = `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%`;
}

/**
 * Met à jour la barre "Dernière mise à jour" en haut de page.
 */
function updateLastUpdateBar(timestamp) {
    const bar = document.getElementById('last-update-bar');
    const textEl = document.getElementById('last-update-text');
    if (!bar || !textEl) return;

    let ts = timestamp;
    // Si le timestamp n'a pas de timezone, assumer UTC
    if (ts.length > 10 && !ts.includes('+') && !ts.includes('Z') && !ts.match(/\d{2}:\d{2}:\d{2}-/)) {
        ts += 'Z';
    }
    const genDate = new Date(ts);
    if (!isNaN(genDate.getTime())) {
        const opts = {timeZone: 'Europe/Paris', hour: '2-digit', minute: '2-digit'};
        const heure = genDate.toLocaleTimeString('fr-FR', opts).replace(':', '\u00a0h\u00a0');
        textEl.textContent = `Dernière mise à jour : ${genDate.toLocaleDateString('fr-FR', {timeZone: 'Europe/Paris'})} à ${heure}`;
        bar.style.display = '';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}


// ================================================================
// BACK TO TOP BUTTON
// ================================================================

function setupBackToTop() {
    const btn = document.getElementById('back-to-top');
    if (!btn) return;

    window.addEventListener('scroll', () => {
        if (window.scrollY > 400) {
            btn.classList.add('visible');
        } else {
            btn.classList.remove('visible');
        }
    }, { passive: true });

    btn.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
}

// ================================================================
// P-15 : DÉTECTION HORS-SAISON
// ================================================================

function checkHorsSaison() {
    const now = new Date();
    const month = now.getMonth() + 1; // 1-12
    // Saison Tempo : 1er sept → 31 août. Juin-août = tout BLEU, rien à signaler
    if (month >= 6 && month <= 8) {
        const banner = document.getElementById('hors-saison-banner');
        if (banner) banner.style.display = 'flex';
    }
}

// ================================================================
// FORMULAIRE DÉSINSCRIPTION (BUG-02 QA)
// ================================================================

function setupUnsubscribeForm() {
    const toggleBtn = document.getElementById('show-unsubscribe');
    const form = document.getElementById('unsubscribe-form');
    if (!toggleBtn || !form) return;

    toggleBtn.addEventListener('click', () => {
        form.classList.toggle('hidden');
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const resultEl = document.getElementById('unsub-result');
        const btn = document.getElementById('unsub-btn');
        resultEl.className = 'form-result';
        resultEl.style.display = 'none';

        const rawPhone = document.getElementById('unsub-phone').value;
        const phone = normalizePhone(rawPhone);

        if (!phone) {
            showFormResult(resultEl, 'error',
                'Numéro non reconnu. Saisissez-le comme à l\'inscription, ou répondez STOP dans WhatsApp.');
            return;
        }

        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Désinscription…';

        try {
            const formData = new FormData();
            formData.set('phone', phone);

            const resp = await fetchWithTimeout('/api/unsubscribe', {
                method: 'POST',
                body: formData,
            });
            const data = await resp.json();

            if (resp.ok) {
                showFormResult(resultEl, 'success',
                    data.message || 'Désinscription effectuée. Vous ne recevrez plus de messages.');
                form.reset();
            } else {
                showFormResult(resultEl, 'error',
                    data.detail || 'Numéro non trouvé dans nos inscrits.');
            }
        } catch {
            showFormResult(resultEl, 'error', 'Connexion impossible. Réessayez dans un instant.');
        } finally {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    });
}

// Modal is now in the shared _subscribe_modal.html partial (included in all pages)
