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
- SQLite (`tempo.db`), currently at migration version 18
- Key tables: `predictions`, `actuals`, `weather_cache`, `weather_forecast_log`, `rte_daily`, `weights`, `subscribers`

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

### Weather Forecast History (v18)
- **`weather_forecast_log`** table: stores each weather forecast snapshot for J+2..J+5, keyed by (target_date, forecast_date)
- Allows measuring forecast degradation by horizon (J+5 said 7°C, J+2 corrected to 2°C)
- Enables realistic backtests using actual J+N weather forecasts instead of "perfect weather"
- **`temp_moy_prevue`** column added to `predictions` table (v18): stores the 9-city weighted average temperature used for scoring each prediction
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
- `edf_polling` (6h-11h15, every 15min): polls EDF for J+1 color
- `post_startup` (deferred 90s): backfill + ML evaluation + predictions
- `seo_agent_seasonal` (Tuesday 9h, seasonal frequency): autonomous SEO blog agent (requires ANTHROPIC_API_KEY)

## Common Pitfalls
- **Data leakage**: Never use same-day RTE consumption for predictions (only lag features D-1+)
- **Multi-horizon storage**: `store_prediction` blocks ANY new non-confirmed prediction if the date already has a confirmed row (any horizon). This prevents new horizons from shadowing confirmed predictions via `GROUP BY date + MAX(id)` queries
- **Orphan cleanup**: Must preserve multi-horizon prediction history (clean per-horizon, not per-date)
- **Weather insert**: `fetched_at` column is NOT NULL — always include it in INSERT statements
- **DB migrations**: Always update version assertions in tests when adding new migrations
- **Dependencies**: `fastapi` requires `python-multipart` for Form data — ensure both are installed
- **EDF confirmation propagation**: API endpoints (`/api/today`, `/api/tomorrow`) must call `store_actual()` + `confirm_prediction()` when they detect fresh EDF colors — don't rely solely on the 15-min polling scheduler

### EDF Confirmation & Caching Strategy
- **Two-layer confirmation**: Scheduler polls EDF every 15min (6h-11h15) AND API endpoints propagate confirmations on each request via `_propagate_edf_confirmation()` (idempotent INSERT OR REPLACE)
- **In-memory EDF cache**: 2-minute TTL for `/api/today`, `/api/tomorrow`, `/api/remaining` (external EDF API is slow ~200-600ms but data changes 1-2x/day max)
- Cache invalidated by `invalidate_predictions_cache()` when scheduler detects a new confirmation
- **Cold start resilience**: After restart, in-memory cache is empty; API endpoints re-fetch from EDF and propagate confirmations immediately instead of waiting for next polling cycle

### Probability Display & Rule Enforcement
- **Probability bar**: Tricolor bar (BLEU/BLANC/ROUGE) under each forecast card; segments shown only if > 5%, labels only if > 10%; hidden for EDF-confirmed days
- **Hésitation badge**: Shown when gap between top two probabilities < 30% (e.g. "Hésitation Rouge/Blanc")
- **EDF rules in probabilities**: `_compute_probabilities()` receives `edf_impossible` set — Sundays get 0% ROUGE + 0% BLANC (R2+R3), Saturdays/holidays get 0% ROUGE (R2), dates outside Nov-Mar get 0% ROUGE (R1). Mass redistributed to remaining colors before softmax normalization
- **Color-probability coherence**: Predicted color is always guaranteed to be the highest probability; if classical/ML scoring disagrees with probability ranking, probability is adjusted minimally (max+5%) to prevent UI contradictions

### API Performance
- **Frontend parallelization**: 5 API calls (today, tomorrow, remaining, predictions, badge) fire simultaneously via `Promise.all()` instead of sequentially
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
