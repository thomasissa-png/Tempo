/**
 * TempoForecast — JavaScript principal du dashboard v2.
 *
 * Charge les données depuis les endpoints FastAPI et anime le dashboard.
 */

const JOURS = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam'];
const JOURS_FULL = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi'];
const MOIS = ['jan', 'fév', 'mar', 'avr', 'mai', 'jun', 'jul', 'aoû', 'sep', 'oct', 'nov', 'déc'];

// ================================================================
// INITIALISATION
// ================================================================

document.addEventListener('DOMContentLoaded', () => {
    loadToday();
    loadTomorrow();
    loadRemaining();
    loadPredictions();
    loadBadge();
    setupSubscribeForm();
    setupWelcomeBanner();
    setupAlertDismiss();
});

// ================================================================
// CHARGEMENT DES DONNÉES
// ================================================================

async function loadToday() {
    const el = document.getElementById('today-card');
    if (!el) return;
    try {
        const resp = await fetch('/api/today');
        const data = await resp.json();
        if (data.status === 'ok') {
            renderTodayCard(el, data, "Aujourd'hui");
        } else {
            el.innerHTML = '<p class="loading-state">Données non disponibles</p>';
        }
    } catch {
        el.innerHTML = '<p class="loading-state">Erreur de connexion</p>';
    }
}

async function loadTomorrow() {
    const el = document.getElementById('tomorrow-card');
    if (!el) return;
    try {
        const resp = await fetch('/api/tomorrow');
        const data = await resp.json();
        if (data.status === 'ok') {
            renderTodayCard(el, data, 'Demain');
        } else {
            renderTodayCard(el, { couleur: 'UNKNOWN', date: '' }, 'Demain');
            el.querySelector('.date-text').textContent = 'Disponible après 11h';
        }
    } catch {
        el.innerHTML = '<p class="loading-state">Erreur de connexion</p>';
    }
}

// Fix #12 : état vide pour les compteurs
async function loadRemaining() {
    try {
        const resp = await fetch('/api/remaining');
        const data = await resp.json();
        if (data.status !== 'ok') {
            setText('count-rouge', '?');
            setText('count-blanc', '?');
            setText('count-bleu', '?');
            setText('days-left', 'Données temporairement indisponibles');
            return;
        }

        const r = data.remaining;
        setText('count-rouge', r.ROUGE);
        setText('count-blanc', r.BLANC);
        setText('count-bleu', r.BLEU);
        setText('days-left', `${data.days_left_in_season} jours restants dans la saison`);
    } catch (e) {
        setText('count-rouge', '?');
        setText('count-blanc', '?');
        setText('count-bleu', '?');
        setText('days-left', 'Erreur de connexion — réessayez plus tard');
        console.error('Erreur chargement compteurs:', e);
    }
}

// Fix #6 : prévisions groupées par horizon
// Fix v5 : lecture DB, indicateurs confirmed/simulated/changed
async function loadPredictions() {
    const container = document.getElementById('forecast-container');
    if (!container) return;
    container.innerHTML = '<div class="loading-state"><div class="loader"></div><p>Chargement des prévisions...</p></div>';

    try {
        const resp = await fetch('/api/predictions');
        const data = await resp.json();
        if (data.status !== 'ok') {
            container.innerHTML = '<p class="loading-state">Erreur lors du chargement</p>';
            return;
        }

        container.innerHTML = '';
        const alertEl = document.getElementById('alert-rouge');
        let hasRouge = false;

        const preds = data.predictions;

        if (preds.length === 0) {
            container.innerHTML = '<p class="loading-state">' +
                escapeHtml(data.message || 'Aucune prédiction disponible. Revenez après 18h.') + '</p>';
            return;
        }

        // Fix v5 #6 : avertissement si données simulées
        const hasSimulated = preds.some(p => p.simulated);
        if (hasSimulated) {
            const warn = document.createElement('div');
            warn.className = 'simulated-warning';
            warn.innerHTML = '<strong>Données météo simulées</strong> — Clé API OpenWeather non configurée. ' +
                'Les prédictions sont basées sur des moyennes saisonnières et sont moins fiables.';
            container.appendChild(warn);
        }

        // Fix v5 #4 : afficher la date de dernière mise à jour
        if (data.generated_at) {
            const meta = document.createElement('div');
            meta.className = 'forecast-meta';
            const genDate = new Date(data.generated_at);
            meta.textContent = `Dernière mise à jour : ${genDate.toLocaleDateString('fr-FR')} ` +
                `à ${genDate.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'})}`;
            container.appendChild(meta);
        }

        // Grouper les prédictions par horizon
        const groupPrimary = preds.slice(0, 3);
        const groupMedium  = preds.slice(3, 7);
        const groupFar     = preds.slice(7);

        if (groupPrimary.length > 0) {
            const label1 = document.createElement('div');
            label1.className = 'forecast-group-label';
            label1.textContent = 'Prévisions fiables (J+1 à J+3)';
            container.appendChild(label1);

            const grid1 = document.createElement('div');
            grid1.className = 'forecast-grid forecast-grid-primary';
            groupPrimary.forEach(pred => {
                grid1.appendChild(createForecastCard(pred));
                if (pred.couleur_predite === 'ROUGE') hasRouge = true;
            });
            container.appendChild(grid1);
        }

        if (groupMedium.length > 0) {
            const label2 = document.createElement('div');
            label2.className = 'forecast-group-label';
            label2.textContent = 'Prévisions moyennes (J+4 à J+7)';
            container.appendChild(label2);

            const grid2 = document.createElement('div');
            grid2.className = 'forecast-grid';
            groupMedium.forEach(pred => {
                grid2.appendChild(createForecastCard(pred));
                if (pred.couleur_predite === 'ROUGE') hasRouge = true;
            });
            container.appendChild(grid2);
        }

        if (groupFar.length > 0) {
            const label3 = document.createElement('div');
            label3.className = 'forecast-group-label';
            label3.textContent = 'Tendances indicatives (J+8 à J+15)';
            container.appendChild(label3);

            const grid3 = document.createElement('div');
            grid3.className = 'forecast-grid';
            groupFar.forEach(pred => {
                grid3.appendChild(createForecastCard(pred));
                if (pred.couleur_predite === 'ROUGE') hasRouge = true;
            });
            container.appendChild(grid3);
        }

        // Afficher alerte rouge si nécessaire (sauf si déjà fermée cette session)
        if (alertEl && hasRouge && !sessionStorage.getItem('alert-rouge-dismissed')) {
            const rougePreds = preds.filter(p => p.couleur_predite === 'ROUGE');
            const first = rougePreds[0];
            alertEl.querySelector('.alert-text').textContent =
                `Jour ROUGE prévu le ${formatDateFr(first.date)} ! Anticipez votre consommation.`;
            alertEl.classList.add('visible');
        }
    } catch (e) {
        container.innerHTML = '<p class="loading-state">Erreur de connexion au serveur</p>';
        console.error('Erreur prédictions:', e);
    }
}

async function loadBadge() {
    try {
        const resp = await fetch('/api/performance/badge');
        const data = await resp.json();
        if (data.status !== 'ok') return;

        const el = document.getElementById('badge-text');
        if (el) {
            if (data.total_predictions > 0) {
                document.getElementById('badge-value').textContent = `${data.precision_30j}%`;
                el.textContent = data.label;
            } else {
                document.getElementById('badge-value').textContent = '—';
                el.textContent = 'Précision en cours de calcul (pas encore assez de données)';
            }
        }
    } catch (e) {
        console.error('Erreur badge:', e);
    }
}

// ================================================================
// RENDU DES COMPOSANTS
// ================================================================

function renderTodayCard(el, data, label) {
    const couleur = data.couleur || 'UNKNOWN';
    const dateStr = data.date ? formatDateFr(data.date) : '';
    el.innerHTML = `
        <h3>${label}</h3>
        <div class="couleur-circle couleur-${couleur}">${couleur === 'UNKNOWN' ? '?' : couleur[0]}</div>
        <div style="font-size:1.1rem;font-weight:600;margin:4px 0">${couleur === 'UNKNOWN' ? 'Inconnu' : couleur}</div>
        <div class="date-text">${dateStr}</div>
    `;
}

function createForecastCard(pred) {
    const card = document.createElement('div');
    card.className = `forecast-card color-${pred.couleur_predite}`;

    // Fix v5 : marquer les cartes confirmées
    if (pred.confirmed) {
        card.classList.add('confirmed');
    }

    const d = new Date(pred.date);
    const dow = JOURS[d.getDay()];
    const dayNum = d.getDate();
    const month = MOIS[d.getMonth()];

    // Confiance = probabilité de la couleur prédite
    const probKey = `probabilite_${pred.couleur_predite.toLowerCase()}`;
    const confidence = Math.round((pred[probKey] || 0) * 100);

    let tempHtml = '';
    if (pred.temp_min_prevue != null && pred.temp_max_prevue != null) {
        tempHtml = `<div class="fc-temp">${pred.temp_min_prevue}° / ${pred.temp_max_prevue}°</div>`;
    }

    let raisonHtml = '';
    if (pred.raison && pred.raison !== 'Conditions normales') {
        raisonHtml = `<div class="fc-raison">${escapeHtml(pred.raison)}</div>`;
    }

    // Fix v5 #5 : badge confirmé EDF
    let confirmedHtml = '';
    if (pred.confirmed) {
        confirmedHtml = '<div class="fc-confirmed">Confirmé EDF</div>';
    }

    // Fix v5 #3 : indicateur de changement
    let changedHtml = '';
    if (pred.couleur_precedente) {
        changedHtml = `<div class="fc-changed">Était ${pred.couleur_precedente}</div>`;
    }

    // Confiance : 100% si confirmé, sinon le score habituel
    const confidenceText = pred.confirmed
        ? 'Couleur officielle EDF'
        : `Confiance: <strong>${confidence}%</strong>`;

    card.innerHTML = `
        <div class="fc-day">${dow}</div>
        <div class="fc-date">${dayNum} ${month}</div>
        <span class="fc-couleur ${pred.couleur_predite}">${pred.couleur_predite}</span>
        ${confirmedHtml}
        ${tempHtml}
        <div class="fc-confidence">${confidenceText}</div>
        ${changedHtml}
        ${raisonHtml}
    `;

    return card;
}

// ================================================================
// FORMULAIRE INSCRIPTION SMS
// ================================================================

// Fix #8 : état de chargement sur le bouton
function setupSubscribeForm() {
    const form = document.getElementById('subscribe-form');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const resultEl = document.getElementById('form-result');
        const btn = document.getElementById('subscribe-btn');
        resultEl.className = 'form-result';
        resultEl.style.display = 'none';

        const formData = new FormData(form);

        // Validation basique du numéro
        const phone = formData.get('phone');
        if (!phone.match(/^\+33[0-9]{9}$/)) {
            showFormResult(resultEl, 'error', 'Format invalide. Utilisez +33 suivi de 9 chiffres (ex: +33612345678)');
            return;
        }

        // Désactiver le bouton + spinner
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.innerHTML = '<span class="btn-spinner"></span> Inscription en cours...';

        try {
            const resp = await fetch('/api/subscribe', {
                method: 'POST',
                body: formData,
            });
            const data = await resp.json();

            if (resp.ok) {
                showFormResult(resultEl, 'success', data.message || 'Inscription réussie !');
                form.reset();
            } else {
                showFormResult(resultEl, 'error', data.detail || "Erreur lors de l'inscription");
            }
        } catch {
            showFormResult(resultEl, 'error', 'Erreur de connexion au serveur');
        } finally {
            // Réactiver le bouton
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    });
}

function showFormResult(el, type, message) {
    el.className = `form-result ${type}`;
    el.textContent = message;
    el.style.display = 'block';
}

// ================================================================
// WELCOME BANNER (collapsible, remember via localStorage)
// ================================================================

function setupWelcomeBanner() {
    const banner = document.getElementById('welcome-banner');
    const closeBtn = document.getElementById('welcome-close');
    if (!banner || !closeBtn) return;

    // Cacher si déjà fermé
    if (localStorage.getItem('welcome-dismissed')) {
        banner.classList.add('hidden');
        return;
    }

    closeBtn.addEventListener('click', () => {
        banner.classList.add('hidden');
        localStorage.setItem('welcome-dismissed', '1');
    });
}

// ================================================================
// Fix #7 : ALERTE ROUGE DISMISSABLE (sessionStorage)
// ================================================================

function setupAlertDismiss() {
    const alertEl = document.getElementById('alert-rouge');
    const closeBtn = document.getElementById('alert-close');
    if (!alertEl || !closeBtn) return;

    closeBtn.addEventListener('click', () => {
        alertEl.classList.remove('visible');
        sessionStorage.setItem('alert-rouge-dismissed', '1');
    });
}

// ================================================================
// UTILITAIRES
// ================================================================

function formatDateFr(dateStr) {
    const d = new Date(dateStr);
    const dow = JOURS_FULL[d.getDay()];
    return `${dow} ${d.getDate()} ${MOIS[d.getMonth()]}`;
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// Protection XSS — échapper le HTML dans les données injectées
function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}
