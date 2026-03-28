/**
 * TempoForecast — JavaScript principal du dashboard v3.
 *
 * Refonte UX : langage humain, conseils actionnables, format tel FR,
 * résumé hebdomadaire, prix concrets.
 */

const JOURS = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam'];
const JOURS_FULL = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi'];
const MOIS = ['jan', 'fév', 'mar', 'avr', 'mai', 'jun', 'jul', 'aoû', 'sep', 'oct', 'nov', 'déc'];
const MOIS_FULL = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

// Tarifs indicatifs Tempo au 1er février 2026 (€/kWh TTC — vérifiez sur votre contrat EDF)
const TARIFS = {
    BLEU:  { hp: 0.1612, hc: 0.1325 },
    BLANC: { hp: 0.1871, hc: 0.1499 },
    ROUGE: { hp: 0.7060, hc: 0.1575 },
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
    setupHamburger();
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
            setText('count-rouge', '?');
            setText('count-blanc', '?');
            setText('count-bleu', '?');
            setText('days-left', 'Données temporairement indisponibles');
            return;
        }

        const r = data.remaining;
        const t = data.totals;
        const totalRouge = t ? t.ROUGE : 22;
        const totalBlanc = t ? t.BLANC : 43;
        const totalBleu  = t ? t.BLEU : 208;
        setText('count-rouge', `${r.ROUGE}/${totalRouge}`);
        setText('count-blanc', `${r.BLANC}/${totalBlanc}`);
        setText('count-bleu',  `${r.BLEU}/${totalBleu}`);
        setText('days-left', `${data.days_left_in_season} jours restants dans la saison (${data.season_start ? data.season_start.slice(0,4) : ''}–${data.season_end ? data.season_end.slice(0,4) : ''})`);

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
                ctxEl.textContent = 'Tous les jours rouges de la saison ont \u00e9t\u00e9 utilis\u00e9s. Bonne nouvelle\u00a0!';
            } else if (daysLeftRouge === 0) {
                ctxEl.textContent = 'La p\u00e9riode des jours rouges est termin\u00e9e (fin mars). Plus de risque\u00a0!';
            } else if (daysLeftRouge < 60) {
                // Fréquence en langage naturel (1 jour rouge par X jours)
                const freq = Math.round(daysLeftRouge / r.ROUGE);
                ctxEl.innerHTML = `<strong style="color:var(--rouge)">Attention\u00a0:</strong> il reste ${r.ROUGE} jour${r.ROUGE > 1 ? 's' : ''} rouge${r.ROUGE > 1 ? 's' : ''} \u00e0 placer en ${daysLeftRouge} jours \u2014 attendez-vous \u00e0 environ <strong>1 jour rouge tous les ${freq} jours</strong>.`;
            } else {
                ctxEl.textContent = `Encore ${r.ROUGE} jours rouges \u00e0 placer d'ici fin mars. Restez vigilant\u00a0!`;
            }
        }
    } catch (e) {
        setText('count-rouge', '?');
        setText('count-blanc', '?');
        setText('count-bleu', '?');
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
    container.innerHTML = '<div class="loading-state"><div class="loader"></div><p>Chargement des prévisions...</p></div>';

    try {
        const resp = await fetchWithTimeout('/api/predictions');
        if (!resp.ok) { throw new Error(`HTTP ${resp.status}`); }
        const data = await resp.json();
        if (!data || data.status !== 'ok') {
            container.innerHTML = '<p class="loading-state">Erreur lors du chargement</p>';
            return;
        }

        container.innerHTML = '';

        const preds = data.predictions || [];

        if (preds.length === 0) {
            container.innerHTML = '<p class="loading-state">' +
                escapeHtml(data.message || 'Aucune prévision disponible. Les prévisions se mettent à jour automatiquement.') + '</p>';
            return;
        }

        // Date de dernière mise à jour (timezone Paris) — affichée en haut de page
        if (data.generated_at) {
            updateLastUpdateBar(data.generated_at);
        }

        // Résumé de la semaine
        renderWeekSummary(preds);

        // Grouper les prédictions par horizon avec labels humains
        const groupPrimary = preds.slice(0, 3);
        const groupMedium  = preds.slice(3, 7);
        const groupFar     = preds.slice(7);

        if (groupPrimary.length > 0) {
            const label1 = document.createElement('div');
            label1.className = 'forecast-group-label';
            label1.textContent = 'Les 3 prochains jours — prévisions fiables';
            container.appendChild(label1);

            const grid1 = document.createElement('div');
            grid1.className = 'forecast-grid forecast-grid-primary';
            groupPrimary.forEach(pred => {
                grid1.appendChild(createForecastCard(pred));

            });
            container.appendChild(grid1);
        }

        if (groupMedium.length > 0) {
            const label2 = document.createElement('div');
            label2.className = 'forecast-group-label';
            label2.textContent = 'Cette semaine — prévisions moyennes';
            container.appendChild(label2);

            const grid2 = document.createElement('div');
            grid2.className = 'forecast-grid';
            groupMedium.forEach(pred => {
                grid2.appendChild(createForecastCard(pred));

            });
            container.appendChild(grid2);
        }

        if (groupFar.length > 0) {
            const label3 = document.createElement('div');
            label3.className = 'forecast-group-label';
            label3.textContent = 'Semaine prochaine et au-del\u00e0 \u2014 tendances indicatives';
            container.appendChild(label3);

            // UX audit #27: explanation for distant predictions
            const hint3 = document.createElement('div');
            hint3.className = 'forecast-group-hint';
            hint3.textContent = 'Ces tendances \u00e9voluent souvent \u2014 consultez \u00e0 nouveau dans 2-3 jours pour confirmer.';
            hint3.style.cssText = 'font-size:0.8rem;color:var(--text-secondary);margin:-6px 0 10px;font-style:italic;';
            container.appendChild(hint3);

            // UX audit #9: desaturated grid for far predictions
            const grid3 = document.createElement('div');
            grid3.className = 'forecast-grid forecast-grid-far';
            groupFar.forEach(pred => {
                grid3.appendChild(createForecastCard(pred));

            });
            container.appendChild(grid3);
        }

    } catch (e) {
        container.innerHTML = `
            <div class="maintenance-banner">
                <div class="maintenance-icon">&#9881;</div>
                <h3 style="margin:0 0 6px">Maintenance en cours</h3>
                <p style="margin:0;font-size:.9rem;color:var(--text-secondary)">
                    Le calendrier est temporairement indisponible.<br>
                    Les pr\u00e9visions seront de retour tr\u00e8s vite.
                </p>
                <button class="retry-btn" onclick="loadPredictions()" style="margin-top:12px">R\u00e9essayer</button>
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
                    <h3 style="margin:0 0 6px">Maintenance en cours</h3>
                    <p style="margin:0;font-size:.9rem;color:var(--text-secondary)">
                        Le calendrier est temporairement indisponible.<br>
                        Les pr\u00e9visions seront de retour tr\u00e8s vite.
                    </p>
                    <button class="retry-btn" onclick="loadWeekSummary()" style="margin-top:12px">R\u00e9essayer</button>
                </div>`;
        }
    }
}

async function loadBadge() {
    try {
        const resp = await fetchWithTimeout('/api/performance/badge');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data || data.status !== 'ok') return;

        // Inject precision into FAQ "Vos prévisions sont-elles fiables ?"
        const faqValue = document.getElementById('faq-precision-value');
        if (faqValue) {
            if (data.total_predictions > 0 && data.precision_30j != null) {
                const pct = data.precision_30j;
                const qualif = pct >= 90 ? 'Excellente' : pct >= 80 ? 'Tr\u00e8s bonne' : pct >= 70 ? 'Bonne' : 'En am\u00e9lioration';
                faqValue.textContent = `${pct}% (${qualif}).`;
            } else {
                faqValue.textContent = 'en cours de calcul (pas encore assez de donn\u00e9es).';
            }
        }
    } catch (e) {
        console.error('Erreur badge:', e);
    }
}

// ================================================================
// RÉSUMÉ DE LA SEMAINE
// ================================================================

function renderWeekSummary(preds) {
    const container = document.getElementById('week-summary');
    if (!container) return;

    // Prendre les 10 premiers jours (2 lignes de 5)
    const weekPreds = preds.slice(0, 10);
    if (weekPreds.length === 0) return;

    const rougeCount = weekPreds.filter(p => p.couleur_predite === 'ROUGE').length;
    const blancCount = weekPreds.filter(p => p.couleur_predite === 'BLANC').length;

    // Construire le texte du résumé
    let summaryText = '';
    if (rougeCount === 0 && blancCount === 0) {
        summaryText = 'Bonne nouvelle : <strong>aucun jour rouge ni blanc</strong> en vue cette semaine. Consommez normalement !';
    } else {
        const parts = [];
        if (rougeCount > 0) {
            const rougeDays = weekPreds
                .filter(p => p.couleur_predite === 'ROUGE')
                .map(p => { const d = parseLocalDate(p.date); return JOURS_FULL[d.getDay()]; });
            parts.push(`<strong style="color:var(--rouge)">${rougeCount} jour${rougeCount > 1 ? 's' : ''} rouge${rougeCount > 1 ? 's' : ''}</strong> (${rougeDays.join(', ')})`);
        }
        if (blancCount > 0) {
            const blancDays = weekPreds
                .filter(p => p.couleur_predite === 'BLANC')
                .map(p => { const d = parseLocalDate(p.date); return JOURS_FULL[d.getDay()]; });
            parts.push(`<strong>${blancCount} jour${blancCount > 1 ? 's' : ''} blanc${blancCount > 1 ? 's' : ''}</strong> (${blancDays.join(', ')})`);
        }
        summaryText = parts.join(' et ') + ' en vue. <strong>Planifiez vos machines les jours bleus.</strong>';
    }

    // Dots visuels avec info Confirmé / probabilité
    // Les 2 premiers dots sont labellisés "Auj." et "Dem." pour éviter la redondance
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);

    let dotsHtml = '<div class="week-summary-dots">';
    weekPreds.forEach((p, idx) => {
        // Séparateur entre les 2 lignes de 5 jours
        if (idx === 5) {
            dotsHtml += '<div class="week-dots-separator" aria-hidden="true"></div>';
        }

        const d = parseLocalDate(p.date);
        const dayLabel = JOURS[d.getDay()];
        const dayNum = d.getDate();
        const couleur = p.couleur_predite;
        const bg = couleur === 'ROUGE' ? 'var(--rouge)' : couleur === 'BLANC' ? 'var(--blanc)' : 'var(--bleu)';

        // Contextual label: "Aujourd'hui" for today, "Demain" for tomorrow, day name + num otherwise
        const dTime = d.getTime();
        let displayLabel;
        if (dTime === today.getTime()) {
            displayLabel = `<strong>Aujourd'hui</strong>`;
        } else if (dTime === tomorrow.getTime()) {
            displayLabel = `<strong>Demain</strong>`;
        } else {
            displayLabel = `${escapeHtml(dayLabel)} ${dayNum}`;
        }

        // Info sous le dot : Confirmé ou probabilité
        let infoHtml = '';
        if (p.confirmed) {
            infoHtml = '<span class="week-dot-confirmed">Confirm\u00e9</span>';
        } else {
            const probKey = `probabilite_${couleur.toLowerCase()}`;
            const confidence = Math.round((p[probKey] || 0) * 100);
            infoHtml = `<span class="week-dot-proba">${confidence}%</span>`;
        }

        // UX audit #25: shape classes for colorblind users + #16: confirmed border
        const shapeClass = couleur === 'BLANC' ? ' dot-blanc' : couleur === 'ROUGE' ? ' dot-rouge' : '';
        const confirmedClass = p.confirmed ? ' confirmed-dot' : '';

        dotsHtml += `
            <div class="week-dot${dTime === today.getTime() || dTime === tomorrow.getTime() ? ' week-dot-highlight' : ''}">
                <div class="week-dot-circle${shapeClass}${confirmedClass}" style="background:${bg}">${couleur[0]}</div>
                <span class="week-dot-label">${displayLabel}</span>
                <div class="week-dot-info">${infoHtml}</div>
            </div>`;
    });
    dotsHtml += '</div>';

    const title = document.createElement('h2');
    title.className = 'section-title';
    title.style.marginTop = '0';
    title.textContent = 'R\u00e9sum\u00e9 des 10 prochains jours';

    const card = document.createElement('div');
    card.className = 'week-summary-card' + (rougeCount > 0 ? ' has-rouge' : '');
    card.innerHTML = `
        <div class="week-summary-text">${summaryText}</div>
        ${dotsHtml}
        <a href="/calendrier" class="btn btn-cta-summary" style="text-decoration:none">Voir les pr\u00e9visions d\u00e9taill\u00e9es des 15 prochains jours \u2192</a>
    `;
    container.innerHTML = '';
    container.appendChild(title);
    container.appendChild(card);

    // Bannière hiver atypique (visible jusqu'au 30 mars 2026)
    if (new Date() <= new Date('2026-03-30T23:59:59')) {
        const notice = document.createElement('div');
        notice.className = 'winter-notice';
        notice.style.cssText = 'margin-top:12px;padding:12px 16px;background:#FFF8E1;border:1px solid #FFD54F;border-radius:var(--radius-sm);font-size:.88rem;color:#5D4037;line-height:1.6';
        notice.innerHTML = '<strong>Hiver 2025-2026 atypique\u00a0:</strong> les temp\u00e9ratures exceptionnellement douces et la bonne disponibilit\u00e9 \u00e9nerg\u00e9tique rendent peu probable l\u2019utilisation de tous les jours rouges restants. La d\u00e9cision finale revient \u00e0 RTE, ce qui rend les pr\u00e9visions plus incertaines que d\u2019habitude.';
        container.appendChild(notice);
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
            return { text: 'Tarif moyen — pas de précaution particulière', css: 'tip-blanc' };
        case 'BLEU':
            return { text: 'Tarif avantageux — consommez librement !', css: 'tip-bleu' };
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

function createForecastCard(pred) {
    const VALID_COULEURS = ['BLEU', 'BLANC', 'ROUGE'];
    const couleur = VALID_COULEURS.includes(pred.couleur_predite) ? pred.couleur_predite : 'BLEU';

    const card = document.createElement('div');
    card.className = `forecast-card color-${couleur}`;

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

    let tempHtml = '';
    if (pred.temp_min_prevue != null && pred.temp_max_prevue != null) {
        tempHtml = `<div class="fc-temp">${escapeHtml(String(pred.temp_min_prevue))}° / ${escapeHtml(String(pred.temp_max_prevue))}°</div>`;
    }

    // Raison technique supprimée (reco 3 audit UX — pas utile pour le prospect)

    let confirmedHtml = '';
    if (pred.confirmed) {
        confirmedHtml = '<div class="fc-confirmed">Confirmé par EDF</div>';
    }

    let changedHtml = '';
    if (pred.couleur_precedente) {
        changedHtml = `<div class="fc-changed" title="Notre prévision a changé suite aux nouvelles données météo.">Était ${escapeHtml(pred.couleur_precedente)}</div>`;
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
            <div class="fc-proba-bar" aria-label="Probabilités : bleu ${pctBleu}%, blanc ${pctBlanc}%, rouge ${pctRouge}%">
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
            uncertainHtml = `<div class="fc-uncertain-badge">
                <span class="uncertain-icon" aria-hidden="true">&#9888;</span>
                H\u00e9sitation ${colorNames[probs[0].color]}/${colorNames[probs[1].color]}
            </div>`;
        }
    }

    card.innerHTML = `
        <div class="fc-day">${escapeHtml(dow)}</div>
        <div class="fc-date">${dayNum} ${escapeHtml(month)}</div>
        <span class="fc-couleur ${couleur}" role="img" aria-label="Couleur prédite : ${escapeHtml(couleur)}">${escapeHtml(couleur)}</span>
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
         .replace(/\s*·\s*/g, ' — ');

    // Limiter la longueur
    if (s.length > 60) s = s.substring(0, 57) + '...';

    return s;
}

// ================================================================
// FORMULAIRE INSCRIPTION SMS
// ================================================================

/**
 * Normalise un numéro de téléphone français vers le format +33.
 * Accepte : 06 12 34 56 78, 0612345678, +33612345678, 33612345678
 */
function normalizePhone(input) {
    // Retirer espaces, points, tirets
    let cleaned = input.replace(/[\s.\-()]/g, '');

    // 06... ou 07... → +33...
    if (/^0[67]\d{8}$/.test(cleaned)) {
        return '+33' + cleaned.substring(1);
    }

    // 336... ou 337... (sans le +)
    if (/^33[67]\d{8}$/.test(cleaned)) {
        return '+' + cleaned;
    }

    // Déjà au bon format
    if (/^\+33[67]\d{8}$/.test(cleaned)) {
        return cleaned;
    }

    return null; // Format non reconnu
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
        textEl.textContent = `Dernière mise à jour : ${genDate.toLocaleDateString('fr-FR', {timeZone: 'Europe/Paris'})} ` +
            `à ${genDate.toLocaleTimeString('fr-FR', opts)}`;
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
// HAMBURGER MENU (mobile)
// ================================================================

function setupHamburger() {
    const btn = document.getElementById('hamburger-btn');
    const nav = document.getElementById('main-nav');
    if (!btn || !nav) return;

    btn.addEventListener('click', () => {
        const open = nav.classList.toggle('open');
        btn.setAttribute('aria-expanded', open);
    });

    nav.querySelectorAll('a').forEach(link => {
        link.addEventListener('click', () => {
            nav.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
        });
    });

    document.addEventListener('click', (e) => {
        if (!btn.contains(e.target) && !nav.contains(e.target)) {
            nav.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
        }
    });
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
                'Numéro non reconnu. Tapez votre numéro au format 06 12 34 56 78');
            return;
        }

        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Désinscription...';

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
            showFormResult(resultEl, 'error', 'Erreur de connexion au serveur');
        } finally {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    });
}

// Modal is now in the shared _subscribe_modal.html partial (included in all pages)
