# TempoForecast - Project Memory

## ABSOLUTE RULES
- **NEVER invent or generate synthetic data** (weather, RTE, temperatures, etc.) for backtesting or any analysis. EDF Tempo colors are directly caused by real weather conditions — synthetic data has zero correlation with actual colors and produces meaningless results. If data is missing, identify what's missing and ask the user to provide it (e.g. via Replit with internet access).

## Success Criteria (CRITICAL)

### Primary Success Metric: J+2 to J+5 Predictions
- **Our measure of success is the accuracy of daily predictions for J+2, J+3, J+4, and J+5.**
- J+1 is provided every morning by EDF — we don't need to predict it.
- J+6 and beyond have imprecise weather data — predictions are less reliable.
- Every day, we must produce predictions for J+2 through J+5 with the highest possible accuracy.

### RED Day Accuracy is #1 Priority
- Getting RED days correct is the most important goal (most expensive for users: 0.7562€/kWh peak).
- RED recall (not missing a RED day) is more critical than RED precision (false alarms are less costly than missed REDs).
- Current challenge: 22 RED days in ~180 season days = 12% prevalence, making them hard to predict.

### EDF Tempo Rules (MUST be enforced)
- **R1**: RED days only November 1 to March 31 (NEVER in April-October).
- **R2**: RED days NEVER on weekends or public holidays.
- **R3**: WHITE days NEVER on Sundays.
- **R4**: Maximum 5 consecutive RED days.
- **Budget**: Exactly 22 RED and 43 WHITE days per season (Sep 1 - Aug 31).

## Testing Requirements
- **Tests must be kept up to date with every code change.**
- Run `pytest tests/` after any modification.
- Key test file: `tests/test_qa_fixes.py` — covers DB migrations, predictor logic, EDF rules.
- Ensure DB version assertions match current migration level.

## Architecture Overview

### Prediction Pipeline
1. **Weather data** (`weather_client.py`): 9-city weighted average from Meteo France API
2. **RTE consumption** (`rte_client.py`): National consumption forecast + nuclear availability from RTE API
3. **Classical scoring** (`predictor.py`): 7 weighted sub-scores (temperature 38%, budget 18%, weekday 8%, gradient 6%, clustering 2%, C_nette 18%, pressure 10%)
4. **ML scoring** (`ml_scorer.py`): GradientBoosting with 33 features, probability thresholds
5. **Ensemble**: Classical + ML combined for final prediction

### Key Data Flow
- `scheduler.py` orchestrates daily refresh → calls weather, RTE, predictor
- `rte_client.py` → `_store_rte_daily()` in scheduler stores RTE data for ML lag features
- `ml_scorer.py` reads from `rte_daily` table for D-1 lag features (no same-day data = no leakage)
- `predictor.py` stores multi-horizon predictions (J+1 through J+5)

### Database
- **Dual-mode**: SQLite (`tempo.db`) locally, PostgreSQL on Replit (configured via `DATABASE_URL` env var). Replit migrated to PostgreSQL to avoid SQLite concurrency issues with subscribers. `PgConnectionWrapper` in `database.py` emulates the sqlite3 interface (parameter `?` → `%s`, `AUTOINCREMENT` → `SERIAL`, `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`, `PRAGMA user_version` → `schema_version` table, `executescript` → split and execute). All existing migration code works unmodified on both backends.
- Migration version 19. Key tables: `predictions`, `actuals`, `weather_cache`, `weather_forecast_log`, `rte_daily`, `weights`, `subscribers`
- **SQL compatibility rules** (CRITICAL — must work in both SQLite AND PostgreSQL):
  - NEVER use `strftime()` in SQL queries — use `SUBSTR(date_column, 1, 7)` for month extraction (dates are ISO `YYYY-MM-DD` text)
  - NEVER use `GROUP_CONCAT()` in SQL — do string aggregation in Python
  - NEVER use `typeof()`, `TOTAL()`, `julianday()` in SQL — these are SQLite-only
  - `INSERT OR REPLACE` / `INSERT OR IGNORE` — handled by Replit's DB wrapper, but prefer standard `INSERT ... ON CONFLICT` when possible
  - Day-of-week calculations: do in Python with `date.weekday()`, not SQL `strftime('%w', ...)`
  - `PRAGMA` and `executescript` only in `database.py` migration code (Replit wrapper handles these)

### ML Model
- GradientBoosting, 33 features, trained on 1827 samples (seasons 2019-2026)
- Cost-sensitive: ROUGE weight=25, BLANC=3, BLEU=1
- Thresholds: rouge_thresh=0.07, blanc_thresh=0.15
- Test accuracy: 83.4%, ROUGE recall: 23.3%
- Model file: `ml_model.pkl`

### Configuration
- `config.py`: All thresholds, weights, city definitions, API keys
- Dynamic RED threshold: piecewise-linear curve (42@-5°C→65@10°C), requires budget_score>=40
- Dynamic BLANC threshold: piecewise-linear curve (38@0°C→50@14°C) — stricter when warm to reduce BLEU→BLANC FP
- Density override ROUGE attenuated 60% in Nov-Dec (non-informative early season)

### Budget Pressure Mechanics (CRITICAL)
- `predict_range` iterates J+2 → J+15 chronologically with a `sim_remaining` counter
- Each predicted ROUGE/BLANC decrements `sim_remaining` → pressure increases for subsequent days
- BLEU predictions do NOT decrement, but eligible days shrink (deadline approaches) → density rises naturally
- `_score_budget_v2` recalculates `red_d_left` / `white_d_left` from **target_date** (not today) → correct per-day pressure
- Budget weight is only 12% → max 12 points contribution even at budget_score=100
- **Exact eligible day counting** (`_count_eligible_days`): replaces the old `* 5/7` and `* 6/7` approximations. Iterates day by day from `start` to `end`, checking weekday + holiday status. ROUGE mode: weekdays excluding French holidays (R2). BLANC mode: all days except Sundays (R3). Accounts for ~3-4 weekday holidays in RED season (Toussaint, Armistice, Noël, Jour de l'an) that the 5/7 formula ignored.
- **Density override progressive** : quand la densité RED (remaining/eligible) est élevée, le seuil ROUGE effectif est abaissé proportionnellement. Calibration : densité 20% → pas de réduction, 35% → -3 pts, 50% → -10 pts, 70% → -22 pts. Cela permet de capturer les jours « moyennement froids » (5-7°C) quand la densité l'exige, sans forcer ROUGE sur les jours doux (≥ 8°C).
- **Density override critique** (slack <= 1): when eligible - remaining <= 1, temperature is irrelevant — EDF MUST place these days. Override forces ROUGE/BLANC BEFORE EDF rules (which still enforce weekends/holidays/R1-R4). Slack adapts naturally to any eligible count (avoids fixed-threshold bugs where density dips mid-sequence). Uses `_count_eligible_days` for exact slack calculation.
- **ML budget guard**: ML BLANC→BLEU filter disabled when budget_score >= 50 (prevents ML from overriding budget-driven BLANC predictions)

### Weather Forecast History (v18-v19)
- **`weather_forecast_log`** table: stores each weather forecast snapshot for J+0..J+15, keyed by (target_date, forecast_date)
- Allows measuring forecast degradation by horizon (J+5 said 7°C, J+2 corrected to 2°C)
- J+0 stored as "quasi-observed" reference to measure convergence of earlier forecasts
- Enables realistic backtests using actual J+N weather forecasts instead of "perfect weather"
- **`temp_moy_prevue`** column added to `predictions` table (v18): stores the 9-city weighted average temperature used for scoring each prediction
- **`humidity_prevue`** + **`wind_speed_prevue`** columns added to `predictions` (v19): stores humidity and wind speed used in C_nette proxy scoring
- `weather_cache` continues to store the latest forecast per date (used by current scoring pipeline)

### Weather Fallback Chain
- Primary: Meteo France via meteole (AROME + ARPEGE, 9 cities)
- If meteole fails (Timedelta/Timestamp bugs in v0.2.5): caught by try/except, logged as WARNING
- Fallback: Open-Meteo API (same 9 cities, source confidence 0.80 → score attenuated toward 50)
- If both fail: no predictions generated (empty forecast list)

### RTE Fallback
- Primary: Consumption forecast (v1 API) — seul signal de scoring
- Generation forecast (v3 API, AGGREGATED_FRANCE) — observabilité uniquement, pas de scoring
- **Nuclear unavailable**: NUCLEAR n'est pas un type valide dans l'API Generation Forecast RTE. La disponibilité nucléaire nécessiterait l'API Actual Generation (non implémentée). Le scoring fonctionne correctement sans ce signal.
- If consumption API fails: returns `{"score": 50, "available": False}` → predictor uses 50 (neutral)
- RTE weight is 10% → max 10 points contribution on final score

### Learning System (performance_tracker.py)
- **Weight recalculation**: LogisticRegression multinomial, cost-sensitive (ROUGE=25, BLANC=3, BLEU=1)
- Uses **raw sub-scores** (C-1, before corrections) to avoid feedback loops
- Guards: F1-macro >= 45%, ROUGE recall >= 30%, holdout temporal >= 65%
- Holdout: trains on 80% oldest data, validates on 20% newest (temporal direction)
- Auto-rollback (W-5): if precision drops > 5 points after weight update
- Kill-switch (C-3): disables top 3 corrections if 14-day accuracy < 50%
- Correction validation (A-1): disables all corrections if they degrade accuracy > 3%
- Temporal decay: half-life 45 days on learning corrections

### Scheduler Tasks
- `daily_predictions` (18h): weather + RTE + predict_range + store
- `daily_verification` (11h30): checks EDF official colors, confirms predictions
- `daily_validation` (23h): evaluates prediction accuracy for the day
- `bimonthly_weights` (1st/15th): recalculates scoring weights
- `weekly_recap` (Sunday 20h): sends weekly summary
- `edf_polling` (6h-11h15, every 15min): polls EDF for today/tomorrow colors, evaluates predictions via `evaluate_predictions_for_date()` before `confirm_prediction()` for robustness if 11h30 scheduler fails
- `post_startup` (deferred 90s): backfill + ML evaluation + predictions
- `seo_agent_seasonal` (Tuesday 9h, seasonal frequency): autonomous SEO blog agent (requires ANTHROPIC_API_KEY)

### Admin Dashboard (`/admin`, `performance_tracker.py`, `templates/admin.html`)
- **Authentication**: Bearer token via `Config.ADMIN_PASSWORD`. All API calls include `Authorization: Bearer <pw>`.
- **5 tabs**: Performance (default), SEO, Backlinks, Abonnés, Actions.
- **Performance tab architecture**: Single API call `GET /api/performance?season=YYYY-YYYY` returns all data. Frontend caches in `_perfData` and re-renders sections on filter changes (no additional API calls).

- **Removed sections**: KPI strip (4 cards) and Executive Summary Banner — redundant with analysis tables below. Recap starts directly under season filter.

#### Section 1: Récapitulatif jour par jour
- 15-horizon grid (J-15 to J-1) with colored dots (correct=green border, incorrect=red border, pending=transparent).
- Temperatures shown under all dots (forecast temperature at that horizon).
- Confidence percentage only in tooltip on hover (not visible under dots — user explicitly requested this).
- **Quick month button** (D4): "Ce mois" button auto-selects current month in filter.
- **Month filter persisted** (D5): `_selectedMonth` variable survives season changes.
- **Tool update markers**: Purple dot + description when tool version matches (code updates from `TOOL_UPDATE_DATES` + automatic weight recalculations from `weights_history`).
- **Diagnostic column**: Shows error analysis for J-1→J-5. "Rattrapé J-1 (erreur J-2, J-3)" shown in **orange/warning** (A8) — not green, because failing J-2→J-5 is an anticipation failure even if J-1 is correct.
- **A11**: Past days without EDF actual yet show "en attente EDF" (not misleading "—").
- **D5**: Error diagnostics include ΔT (temperature deviation: observed - forecast) when available.
- **No separate today/tomorrow block**: Removed by user to avoid redundancy with 10-day summary dots.

#### Section 2: Analyse des erreurs
- **Version filter**: Dropdown filters the ENTIRE error section (detection tables, confusion matrix, diagnostic). **Pre-selects latest version** on first load.
- **A5**: Per-version data is **bounded by end_date** — version N's data stops where version N+1 starts. Prevents data contamination.
- **Detection tables** (3 cards: ROUGE/BLANC/BLEU): Recall/precision by horizon J-1→J-10. J-6+ shown at opacity 0.6 marked "(indicatif)". When 0 actual days: shows "Aucun jour réel de cette couleur — rien à évaluer" (not misleading 0%).
- **D9**: Summary row "Total J-2→J-5" inserted after J-5 in each detection table — shows aggregated recall/precision for the value zone.
- **Confusion matrix** (A1/A6): Filtered to **J-2→J-5 only** by default. Scope label visible. Prevents J-1 (trivial) and J-6+ (unreliable) from diluting metrics. 0/0 rows show "—" instead of "0%".
- **Diagnostic** (A3/B6/C1): Precision computed **directly from confusion matrix** (same scope), not from separate `get_accuracy_global()` call. Verdict (bon/moyen/insuffisant) is now coherent. **Zero ROUGE days**: when no ROUGE days exist in the period, diagnostic does NOT say "detection insuffisante" — instead says "non évaluable" and judges only on precision.
- **C4**: All sections display explicit scope labels. Labels ALWAYS include "J-2→J-5" even when version filter is active (e.g., "version 2026-02-20, J-2→J-5" instead of just "version 2026-02-20").
- **D6**: Weather reliability table: Average absolute forecast error and systematic bias per horizon. Helps distinguish "algo wrong" from "weather wrong".
- **Removed sections**: P/R/F1 table (A9), trend chart (B8), data coverage (D12), ROUGE post-mortem (D10), data-range/low-data-banner — removed to simplify dashboard.

#### Section 3: Progresse-t-on ?
- **Version table first** (D8): Displayed BEFORE monthly table (most actionable). Same columns + "Jours" column showing `days_count` (calendar days) and `dates_with_data` (days with evaluations). **Latest version shown first** (reversed order). Helps relativize percentages based on small samples.
- **Monthly table**: Accuracy per horizon per month. **D11**: Clickable rows → filters the recap table to that month and scrolls up.
- **C5**: When version filter is active, shows a note that monthly data covers all versions (use version table above for per-version view).

#### Section 4: Learnings
- **Weights donut chart**: Algorithm weight distribution (Chart.js). Fallback if CDN unavailable (B5).
- **Recommendations** (C4): Scoped explicitly to "Prédictions J-2→J-5 depuis la MAJ [date]". Shows evaluation count in scope (from `get_period_comparison()` with `min_horizon=2, max_horizon=5`). **Based on latest version data** — not polluted by older versions' errors.

#### Backend Design Principles (`performance_tracker.py`)
- **`_enforce_start_date(since)`**: All queries clamp to `PREDICTION_START_DATE` to ignore pre-tool data.
- **B2**: `get_color_recall_by_horizon()` uses single `GROUP BY jours_avance` query (not N individual queries per horizon).
- **B7**: `get_performance_summary()` has 5-minute TTL cache (`_perf_summary_cache`). Invalidated on season change or explicitly via `invalidate_perf_summary_cache()` (called by `invalidate_predictions_cache()` after EDF confirmation/evaluation). Dashboard reflects new evaluations immediately.
- **Version-scoped analysis (A5, CRITICAL)**: Each version's analysis is based ONLY on predictions made WITH that version's code. Uses `pred_since_date` and `pred_end_date` parameters that filter on `date_prediction` (when prediction was MADE), NOT `date_cible` (what date it predicted). This ensures version N's metrics are not contaminated by version N-1's predictions. `get_performance_summary()` builds version boundaries from `_get_all_version_dates()` and passes them as `pred_since_date=version_start, pred_end_date=next_version_start`.
- **`get_confusion_matrix(days, since_date, end_date, min_horizon, max_horizon, pred_since_date, pred_end_date)`**: Fully parameterized. `pred_since_date`/`pred_end_date` filter on `date_prediction` for version scoping. Default in summary: min_horizon=2, max_horizon=5.
- **`get_diagnostic(days, since_date, end_date, min_horizon, max_horizon, pred_since_date, pred_end_date)`**: Computes accuracy from confusion matrix internally (A3/B6). Passes version params through. Returns `has_rouge_days` boolean — when False, `rouge_recall` is None and verdict is based only on precision (prevents false "insuffisante" diagnosis). **Adaptive confusion threshold**: reports confusion pairs with `count >= 2` normally, but `count >= 1` when `total_all < 10` (recent versions with few evaluations) to avoid hiding real errors.
- **`get_color_recall_by_horizon(color, days, max_horizon, since_date, end_date, pred_since_date, pred_end_date)`**: Per-horizon recall with version filtering via `pred_since_date`/`pred_end_date`.
- **`get_period_comparison(days, pivot_date, min_horizon, max_horizon)`**: When `pivot_date` set, compares predictions MADE after vs before that date using `date_prediction` (not `date_cible`). Optional `min_horizon`/`max_horizon` restrict to a horizon range (called with 2-5 in recommendations for J-2→J-5 scope).
- **`get_monthly_performance(season)`**: Uses `SUBSTR(date_cible, 1, 7)` for month grouping (PostgreSQL-compatible, NOT strftime). Season-scoped only, no version filtering.
- **`get_budget_season()`**: Returns `rouge_predicted_confidence` and `blanc_predicted_confidence` (A7: average max probability of future predictions).
- **`get_weather_reliability(days)`**: JOIN weather_forecast_log + weather_cache to compute avg absolute error and bias per horizon.
- **`get_rouge_postmortem(season)`**: Per ROUGE day: predictions at each horizon, caught/missed lists, temperature, version.
- **`get_data_coverage(season)`**: Per horizon: prediction count, evaluation count, coverage percentage.
- **`_get_all_version_dates()`**: Merges `Config.TOOL_UPDATE_DATES` (manual code changes) with successful weight recalculations from `weights_history` DB table. Excludes rejected entries (`REJETE%`). Code versions take precedence on same date. Returns sorted dict.
- **`get_version_performance()`**: Per version: total, accuracy, days_count, dates_with_data, per-horizon accuracy. **Latest version first** (reversed chronological order). Uses `_get_all_version_dates()` so weight recalculations automatically appear as new versions.
- **`get_daily_recap(season)`**: Returns `actual_status` (confirmed/pending/future), `temp_deviation`, `tool_update`.

#### Frontend Design Principles (`templates/admin.html`)
- **Single API call**: All performance data loaded in one `GET /api/performance`. No per-section API calls.
- **Version filter rerenders**: Changes to version filter call `renderErrorSection()`, `renderMonthlyPerfTable()` — all from cached `_perfData`.
- **Chart.js**: Loaded with `onerror` handler on script tag. `chartAvailable` checks both `typeof Chart` and `!window._chartJsFailed`.
- **State variables**: `_perfData` (cached API response), `_selectedVersion` (version filter, auto-selects latest on first load via `_versionFilterInitialized`), `_selectedMonth` (month filter), `_recapData` (recap for filtering).
- **No KPI/exec-summary elements**: KPI strip and executive summary banner removed from performance tab — data available in analysis tables below.
- **No `data-range`/`low-data-banner`**: Removed — redundant with executive summary and section-level scope labels.
- **Season selector**: Only shows seasons with non-simulated predictions after `PREDICTION_START_DATE`.
- **Staircase version boundaries**: `_renderRecapTable()` computes for each cell (date, J-N) which version was active when the prediction was made (`_getActiveVersion(targetDate, horizon)` = latest version deployed ≤ target_date - N days). Borders appear where adjacent cells (above or left) belong to different versions: `border-top` for horizontal steps, `border-left` for vertical connectors. This creates a continuous staircase contour that clearly separates data from each tool version. Color: `#7B1FA2` (purple), 2px solid.

## Common Pitfalls
- **Data leakage**: Never use same-day RTE consumption for predictions (only lag features D-1+)
- **Multi-horizon storage**: `store_prediction` blocks ANY new non-confirmed prediction if the date already has a confirmed row (any horizon). This prevents new horizons from shadowing confirmed predictions via `GROUP BY date + MAX(id)` queries
- **Orphan cleanup**: Must preserve multi-horizon prediction history (clean per-horizon, not per-date)
- **Weather insert**: `fetched_at` column is NOT NULL — always include it in INSERT statements
- **DB migrations**: Always update version assertions in tests when adding new migrations
- **Dependencies**: `fastapi` requires `python-multipart` for Form data — ensure both are installed
- **SQL portability**: Never use SQLite-specific functions (`strftime`, `GROUP_CONCAT`, `typeof`, `julianday`) in SQL queries — see Database section for compatible alternatives
- **Admin password diagnostic**: At startup, the lifespan logs the password source (`ADMIN_PASSWORD` env, `SESSION_SECRET`, or generated random) and length. Failed login attempts log length mismatch. Check Replit logs if login fails after changing the Secret.
- **EDF confirmation propagation**: API endpoints (`/api/today`, `/api/tomorrow`) call `store_actual()` + `evaluate_predictions_for_date()` + `confirm_prediction()` via `_propagate_edf_confirmation()`. Evaluation is called BEFORE confirmation (same order as scheduler) to ensure predictions are evaluated even if the 11h30 scheduler fails. Also invalidates `_perf_summary_cache`.
- **Frontend MUST call /api/today and /api/tomorrow**: Even though today/tomorrow cards were removed from the dashboard, the JS must still call these endpoints to trigger `_propagate_edf_confirmation()`. Without these calls, EDF confirmations are never written to the DB from the frontend path. `loadAllData()` calls them before `loadPredictions()`, and the 5-min auto-refresh also calls them.

### EDF Confirmation & Caching Strategy
- **Three-layer confirmation**: (1) Scheduler polls EDF every 15min (6h-11h15), (2) Frontend JS calls `/api/today` + `/api/tomorrow` on page load and every 5 min, (3) `/api/predictions` cross-checks with EDF live cache
- **In-memory EDF cache**: 2-minute TTL for `/api/today`, `/api/tomorrow`, `/api/remaining` (external EDF API is slow ~200-600ms but data changes 1-2x/day max)
- Cache invalidated by `invalidate_predictions_cache()` when scheduler detects a new confirmation
- **Cold start resilience**: After restart, in-memory cache is empty; API endpoints re-fetch from EDF and propagate confirmations immediately instead of waiting for next polling cycle

### Probability Display & Rule Enforcement
- **Probability bar**: Tricolor bar (BLEU/BLANC/ROUGE) under each forecast card; segments shown only if > 5%, labels only if > 10%; hidden for EDF-confirmed days
- **Hésitation badge**: Shown when gap between top two probabilities < 30% (e.g. "Hésitation Rouge/Blanc")
- **EDF rules in probabilities**: `_compute_probabilities()` receives `edf_impossible` set — Sundays get 0% ROUGE + 0% BLANC (R2+R3), Saturdays/holidays get 0% ROUGE (R2), dates outside Nov-Mar get 0% ROUGE (R1). Mass redistributed to remaining colors before softmax normalization
- **Color-probability coherence**: Predicted color is always guaranteed to be the highest probability; if classical/ML scoring disagrees with probability ranking, probability is adjusted minimally (max+5%) to prevent UI contradictions

### API Performance
- **Frontend load sequence**: `loadAllData()` first calls `/api/today` (blocking, for cold-start detection + EDF propagation), then fire-and-forget `/api/tomorrow` (EDF propagation), then `Promise.all([loadRemaining, loadPredictions, loadBadge])` in parallel. EDF calls MUST precede predictions to propagate confirmations to DB.
- **Cache-Control headers**: `/static/` 1h + stale-while-revalidate; `/api/today|tomorrow|remaining` 2min; `/api/predictions|performance/badge` 5min; `/calendrier` 10min + stale-while-revalidate
- **Cold start UX**: During FastAPI startup, ASGI proxy serves real `dashboard.html` + CSS + JS (not a loading placeholder). JS detects 503 responses and retries with exponential backoff (2-4s) via `loadAllData()`

### SEO & Server-Side Rendering
- **SSR on homepage**: `_get_ssr_data()` pre-loads today/tomorrow colors, remaining counters, first 10 predictions, and last update timestamp from DB. Jinja2 renders real content instead of JS placeholders. JS takes over on client-side. Best-effort: if DB not ready, falls back to empty placeholders.
- **SSR last update**: Uses `MAX(timestamp_prediction)` from predictions table (NOT `created_at` which doesn't exist). Displayed as "Dernière mise à jour" banner above the 10-day summary.
- **No duplicate today/tomorrow block**: Today/tomorrow colors are shown ONLY in the 10-day summary (week-summary dots with "Aujourd'hui"/"Demain" labels). Do NOT add a separate today/tomorrow block above it — the user explicitly removed it to avoid redundancy.
- **Dedicated `/calendrier` page**: Monthly grid of Tempo colors (past = EDF official from `actuals`, future = predictions). Server-side rendered via `page_calendrier()`. Navigation with `#cal` anchor to avoid scroll-to-top on mobile. Targets "calendrier tempo" / "calendrier tempo edf" keywords.
- **Structured Data (JSON-LD)**: Homepage has `SoftwareApplication`, `AggregateRating`, `Organization`, `BreadcrumbList`, `HowTo` (3 steps), `FAQPage` (7 questions), `WebSite`. Calendar page has `Dataset`, `BreadcrumbList`, `FAQPage` (6 questions). Blog articles have `Article` + `BreadcrumbList`. All breadcrumbs include `item` URL on last element.
- **Sitemap**: Dynamic `/sitemap.xml` includes `/` (priority 1.0), `/calendrier` (priority 0.9), `/blog/` (0.7), individual articles (0.6), `/alertes` (0.8), `/mentions-legales` (0.3).
- **Robots.txt**: Explicit `Allow: /calendrier`, `Allow: /blog/`. Disallows `/admin`, `/api/`, `/manage/`. AI bot rules: GPTBot, ChatGPT-User, ClaudeBot, PerplexityBot, Google-Extended allowed on `/`, `/calendrier`, `/blog/`, `/api/today`, `/api/tomorrow`, `/api/predictions`, `/llms.txt`, `/feed.xml`.
- **AI discovery**: `/llms.txt` endpoint for AI crawlers. `/feed.xml` RSS 2.0 feed for blog articles.
- **Internal linking**: "Calendrier" in nav across all templates. Footer links to Calendrier, Blog, Alertes, Mentions légales on every page. Blog articles cross-link to each other (17+ internal links across 7 articles) and to `/calendrier` and `/#subscribe`.
- **Blog SEO articles**: 8 articles total — 6 original + "Tempo EDF 2026 guide complet" (targets "tempo edf") + "Historique calendrier Tempo" (targets "calendrier tempo", expanded to 1655 words).
- **Heading hierarchy**: Only homepage has `<h1>` in header. All other pages use `<span class="header-title">` in header to avoid duplicate H1 (each page has its own content `<h1>`).
- **Google Fonts**: Loaded via `<link rel="preconnect">` + `<link rel="stylesheet">` in HTML (not CSS `@import`).
- **Minified assets**: `style.min.css` (37KB, -31%) and `app.min.js` (18KB, -47%). All templates reference minified versions.

### Alert UX
- **WhatsApp alert framing**: Alerts emphasize **5-day forecast** (J+2 to J+5), not "demain" predictions. EDF confirms J+1 directly, so our value is anticipation.
- **SMS mockup**: All mockups (alertes.html, _subscribe_modal.html) show a weekly forecast with 5 days of colored dots (🔴🔴⚪🔵🔵 format), not a single "jour rouge demain" message.
- **Modal dynamic dates**: `updateSmsDays()` generates 5 upcoming weekdays starting from J+2 and displays them in the modal SMS preview.
- **Subscription CTA**: All CTAs across templates use "Anticipez les 5 prochains jours Tempo" framing instead of "alerte la veille".

### Accessibility
- **Focus trap**: Subscribe modal traps Tab/Shift+Tab focus cycling within modal elements. Focus restored to triggering element on close via `_previouslyFocused`.
- **ARIA**: `aria-live="polite"` on form result elements. Skip link on all pages. Proper `role="banner"`, `role="main"`, `role="contentinfo"` landmarks.
- **Twitter Cards**: Open Graph + Twitter Card meta tags on `/alertes` page.

### Analytics
- **Umami**: Cloud-hosted analytics (`cloud.umami.is`) on all public templates (dashboard, blog_index, blog_article, legal, manage, alertes). Script loaded with `defer`. Website ID: `1d187359-b4a6-4ba8-ba41-c449b356832f`.

### SEO Agent v2 (autonomous seasonal publication)
- **Runner**: `seo_agent.py` — autonomous agent using Anthropic API with tool use (read/write/edit files, glob, grep, web search, bash)
- **Config**: `Config.ANTHROPIC_API_KEY`, `Config.SEO_AGENT_MODEL`, `Config.SEO_AGENT_MAX_TURNS`, `Config.SEO_SEASON_SCHEDULE`
- **Prompt**: `.claude/seo-agent-prompt.md` — complete 8-step workflow for autonomous blog publication
- **Editorial calendar**: `articles/_calendrier_editorial.yaml` (YAML format for reliable machine parsing) — tracks 10+ weeks ahead, updated each Tuesday
- **Publication log**: `articles/_publication_log.md` — persistent log of every publication action
- **Scheduler**: `task_seo_agent` in scheduler.py — CronTrigger every Tuesday at 9h00 Paris time. `_should_publish_today()` gates execution based on seasonal frequency. Silently skipped if `ANTHROPIC_API_KEY` not set.
- **Seasonal schedule** (`Config.SEO_SEASON_SCHEDULE`): Nov-Mar (saison active) = weekly; Sep-Oct (pré-saison) = bimonthly (1er+3e mardi); Apr-May (post-saison) = monthly (1er mardi); Jun-Aug (morte-saison) = off. ~30 articles/an au lieu de 52, concentrés quand le trafic Tempo est actif.
- **API key**: Requires `ANTHROPIC_API_KEY` in environment variables (Replit Secrets). One-time setup. Uses Claude Sonnet for cost efficiency.
- **Manual trigger**: Available via admin panel `/admin` → task `seo_agent`, or `run_task_now("seo_agent")` (bypasses seasonal gate)
- **Schedule**: Each eligible Tuesday, the agent runs the full cycle: SEO monitoring → inventory → calendar → writing → review → bidirectional linking → publication → logging
- **8 steps**: (0) SEO monitoring + self-update, (1) Inventory + competitive analysis + PAA, (2) Calendar update + anti-cannibalization, (3) Writing with 5-title brainstorming + FAQ + featured snippets, (4) SEO review + word count verification, (5) Bidirectional retroactive linking, (6) Publication + error handling + verification, (7) Publication log, (8) Execution report
- **Topic clusters**: 5 clusters (tempo-guide, jours-rouges, calendrier, equipements, preparation). Each has a pillar article + satellites. Satellites must link to pillar, pillar must link to satellites.
- **Anti-cannibalization**: Agent checks search intent (not just keywords) before creating new articles. Prefers refreshing existing articles over creating duplicates.
- **Refresh cycle**: 3 new articles + 1 refresh per 4-week cycle. Refreshes update `updated_date` frontmatter for Google freshness signal.
- **Bidirectional linking**: Step 5 adds links FROM existing articles TO the new one (retroactive mesh). Max 5 articles modified per week.
- **Self-updating**: Step 0 checks for Google algorithm updates and AI search changes. SEO rules split into PERMANENT (E-E-A-T, no keyword stuffing) vs MODIFIABLE (structured data formats, Core Web Vitals thresholds). Guard-fous prevent modifying fundamental principles.
- **Blog article fields**: `updated_date` (optional, for refreshed articles) and `cluster` (topic cluster name) in frontmatter. `blog.py` Article dataclass supports both.
- **Publishing**: Articles auto-appear on `/blog/`, `/sitemap.xml`, `/feed.xml` when `publish_date <= today`
- **Style**: Vouvoiement, expert accessible tone, 1200-2000 words per article
- **SEO requirements**: Min 5 internal links per article (3 blog + /calendrier + /#subscribe + pillar). Keyword in title/description/H1/intro. FAQ section (2-3 PAA questions). 1+ featured snippet element per H2.
- **Existing coverage**: 8 articles through March 24, 2026. Calendar planned through June 2, 2026.
