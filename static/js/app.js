/**
 * TempoForecast — JavaScript principal du dashboard.
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

async function loadRemaining() {
    try {
        const resp = await fetch('/api/remaining');
        const data = await resp.json();
        if (data.status !== 'ok') return;

        const r = data.remaining;
        setText('count-rouge', r.ROUGE);
        setText('count-blanc', r.BLANC);
        setText('count-bleu', r.BLEU);
        setText('days-left', `${data.days_left_in_season} jours restants dans la saison`);
    } catch (e) {
        console.error('Erreur chargement compteurs:', e);
    }
}

async function loadPredictions() {
    const grid = document.getElementById('forecast-grid');
    if (!grid) return;
    grid.innerHTML = '<div class="loading-state"><div class="loader"></div><p>Chargement des prévisions...</p></div>';

    try {
        const resp = await fetch('/api/predictions');
        const data = await resp.json();
        if (data.status !== 'ok') {
            grid.innerHTML = '<p class="loading-state">Erreur lors du chargement</p>';
            return;
        }

        grid.innerHTML = '';
        const alertEl = document.getElementById('alert-rouge');
        let hasRouge = false;

        data.predictions.forEach(pred => {
            const card = createForecastCard(pred);
            grid.appendChild(card);
            if (pred.couleur_predite === 'ROUGE') hasRouge = true;
        });

        // Afficher alerte rouge si nécessaire
        if (alertEl && hasRouge) {
            const rougePreds = data.predictions.filter(p => p.couleur_predite === 'ROUGE');
            const first = rougePreds[0];
            const d = new Date(first.date);
            alertEl.querySelector('.alert-text').textContent =
                `Jour ROUGE prévu le ${formatDateFr(first.date)} ! Score de risque: ${first.score_risque}. Anticipez votre consommation.`;
            alertEl.classList.add('visible');
        }
    } catch (e) {
        grid.innerHTML = '<p class="loading-state">Erreur de connexion au serveur</p>';
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
        raisonHtml = `<div class="fc-raison">${pred.raison}</div>`;
    }

    card.innerHTML = `
        <div class="fc-day">${dow}</div>
        <div class="fc-date">${dayNum} ${month}</div>
        <span class="fc-couleur ${pred.couleur_predite}">${pred.couleur_predite}</span>
        ${tempHtml}
        <div class="fc-confidence">Confiance: <strong>${confidence}%</strong></div>
        ${raisonHtml}
    `;

    return card;
}

// ================================================================
// FORMULAIRE INSCRIPTION SMS
// ================================================================

function setupSubscribeForm() {
    const form = document.getElementById('subscribe-form');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const resultEl = document.getElementById('form-result');
        resultEl.className = 'form-result';
        resultEl.style.display = 'none';

        const formData = new FormData(form);

        // Validation basique du numéro
        const phone = formData.get('phone');
        if (!phone.match(/^\+33[0-9]{9}$/)) {
            showFormResult(resultEl, 'error', 'Format invalide. Utilisez +33 suivi de 9 chiffres (ex: +33612345678)');
            return;
        }

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
                showFormResult(resultEl, 'error', data.detail || 'Erreur lors de l\'inscription');
            }
        } catch {
            showFormResult(resultEl, 'error', 'Erreur de connexion au serveur');
        }
    });
}

function showFormResult(el, type, message) {
    el.className = `form-result ${type}`;
    el.textContent = message;
    el.style.display = 'block';
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
