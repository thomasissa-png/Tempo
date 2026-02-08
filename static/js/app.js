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
    setupHamburger();
    setupBackToTop();
    setupPhoneValidation();
});

// ================================================================
// CHARGEMENT DES DONNÉES
// ================================================================

async function loadToday() {
    const el = document.getElementById('today-card');
    if (!el) return;
    try {
        const resp = await fetch('/api/today');
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
        const resp = await fetch('/api/tomorrow');
        if (!resp.ok) { el.innerHTML = '<p class="loading-state">Données non disponibles</p>'; return; }
        const data = await resp.json();
        if (data && data.status === 'ok') {
            renderTodayCard(el, data, 'Demain');
        } else {
            renderTodayCard(el, { couleur: 'UNKNOWN', date: '' }, 'Demain');
            const dateEl = el.querySelector('.date-text');
            if (dateEl) dateEl.textContent = 'Disponible après 11h';
        }
    } catch {
        el.innerHTML = '<p class="loading-state">Erreur de connexion<br><button class="retry-btn" onclick="loadTomorrow()">Réessayer</button></p>';
    }
}

// Fix #12 : état vide pour les compteurs
async function loadRemaining() {
    try {
        const resp = await fetch('/api/remaining');
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
        setText('count-rouge', `${r.ROUGE}/${t ? t.ROUGE : 22}`);
        setText('count-blanc', `${r.BLANC}/${t ? t.BLANC : 43}`);
        setText('count-bleu', `${r.BLEU}/${t ? t.BLEU : 208}`);
        setText('days-left', `${data.days_left_in_season} jours restants dans la saison (${data.season_start ? data.season_start.slice(0,4) : ''}–${data.season_end ? data.season_end.slice(0,4) : ''})`);
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

// Fix #6 : prévisions groupées par horizon
// Fix v5 : lecture DB, indicateurs confirmed/simulated/changed
async function loadPredictions() {
    const container = document.getElementById('forecast-container');
    if (!container) return;
    container.innerHTML = '<div class="loading-state"><div class="loader"></div><p>Chargement des prévisions...</p></div>';

    try {
        const resp = await fetch('/api/predictions');
        if (!resp.ok) { throw new Error(`HTTP ${resp.status}`); }
        const data = await resp.json();
        if (!data || data.status !== 'ok') {
            container.innerHTML = '<p class="loading-state">Erreur lors du chargement</p>';
            return;
        }

        container.innerHTML = '';
        const alertEl = document.getElementById('alert-rouge');
        let hasRouge = false;

        const preds = data.predictions || [];

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
            warn.innerHTML = '<strong>Données météo simulées</strong> — Open-Meteo temporairement indisponible. ' +
                'Les prédictions sont basées sur des moyennes saisonnières et sont moins fiables.';
            container.appendChild(warn);
        }

        // Fix v5 #4 + I-7 : afficher la date de dernière mise à jour (avec validation)
        if (data.generated_at) {
            const genDate = new Date(data.generated_at);
            if (!isNaN(genDate.getTime())) {
                const meta = document.createElement('div');
                meta.className = 'forecast-meta';
                meta.textContent = `Dernière mise à jour : ${genDate.toLocaleDateString('fr-FR')} ` +
                    `à ${genDate.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'})}`;
                container.appendChild(meta);
            }
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
        container.innerHTML = '<p class="loading-state">Erreur de connexion au serveur<br><button class="retry-btn" onclick="loadPredictions()">Réessayer</button></p>';
        console.error('Erreur prédictions:', e);
    }
}

async function loadBadge() {
    try {
        const resp = await fetch('/api/performance/badge');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data || data.status !== 'ok') return;

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
    const VALID_COULEURS = ['BLEU', 'BLANC', 'ROUGE', 'UNKNOWN'];
    const couleur = VALID_COULEURS.includes(data.couleur) ? data.couleur : 'UNKNOWN';
    const couleurLabel = couleur === 'UNKNOWN' ? 'Inconnu' : couleur;
    const dateStr = data.date ? formatDateFr(data.date) : '';
    el.innerHTML = `
        <h3>${escapeHtml(label)}</h3>
        <div class="couleur-circle couleur-${couleur}" role="img" aria-label="Couleur ${escapeHtml(couleurLabel)}">${couleur === 'UNKNOWN' ? '?' : couleur[0]}</div>
        <div style="font-size:1.1rem;font-weight:600;margin:4px 0">${escapeHtml(couleurLabel)}</div>
        <div class="date-text">${escapeHtml(dateStr)}</div>
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

    const d = new Date(pred.date);
    const dow = JOURS[d.getDay()];
    const dayNum = d.getDate();
    const month = MOIS[d.getMonth()];

    const probKey = `probabilite_${couleur.toLowerCase()}`;
    const confidence = Math.round((pred[probKey] || 0) * 100);

    let tempHtml = '';
    if (pred.temp_min_prevue != null && pred.temp_max_prevue != null) {
        tempHtml = `<div class="fc-temp">${escapeHtml(String(pred.temp_min_prevue))}° / ${escapeHtml(String(pred.temp_max_prevue))}°</div>`;
    }

    let raisonHtml = '';
    if (pred.raison && pred.raison !== 'Conditions normales') {
        raisonHtml = `<div class="fc-raison">${escapeHtml(pred.raison)}</div>`;
    }

    let confirmedHtml = '';
    if (pred.confirmed) {
        confirmedHtml = '<div class="fc-confirmed">Confirmé EDF</div>';
    }

    // Fix audit v6 : escape couleur_precedente
    let changedHtml = '';
    if (pred.couleur_precedente) {
        changedHtml = `<div class="fc-changed">Était ${escapeHtml(pred.couleur_precedente)}</div>`;
    }

    const confidenceText = pred.confirmed
        ? 'Couleur officielle EDF'
        : `Confiance: <strong>${confidence}%</strong>`;

    card.innerHTML = `
        <div class="fc-day">${escapeHtml(dow)}</div>
        <div class="fc-date">${dayNum} ${escapeHtml(month)}</div>
        <span class="fc-couleur ${couleur}" role="img" aria-label="Couleur prédite : ${escapeHtml(couleur)}">${escapeHtml(couleur)}</span>
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
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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

    // Close menu when clicking a nav link
    nav.querySelectorAll('a').forEach(link => {
        link.addEventListener('click', () => {
            nav.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
        });
    });

    // Close menu when clicking outside
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
// REAL-TIME PHONE VALIDATION
// ================================================================

function setupPhoneValidation() {
    const phoneInput = document.getElementById('phone');
    const feedback = document.getElementById('phone-feedback');
    if (!phoneInput || !feedback) return;

    phoneInput.addEventListener('input', () => {
        const val = phoneInput.value;
        if (!val || val.length < 2) {
            feedback.textContent = '';
            feedback.className = 'phone-feedback';
            return;
        }

        if (/^\+33[0-9]{9}$/.test(val)) {
            feedback.textContent = 'Format valide';
            feedback.className = 'phone-feedback valid';
        } else if (/^\+33/.test(val) && val.length < 12) {
            feedback.textContent = `Encore ${12 - val.length} chiffre(s)`;
            feedback.className = 'phone-feedback';
        } else if (/^\+33/.test(val) && val.length > 12) {
            feedback.textContent = 'Trop de chiffres';
            feedback.className = 'phone-feedback invalid';
        } else if (!val.startsWith('+33')) {
            feedback.textContent = 'Doit commencer par +33';
            feedback.className = 'phone-feedback invalid';
        }
    });
}
