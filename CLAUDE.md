# TempoForecast - Project Memory

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
3. **Classical scoring** (`predictor.py`): 7 weighted sub-scores (temperature 40%, budget 12%, weekday 10%, gradient 8%, clustering 12%, RTE 10%, pressure 8%)
4. **ML scoring** (`ml_scorer.py`): GradientBoosting with 33 features, probability thresholds
5. **Ensemble**: Classical + ML combined for final prediction

### Key Data Flow
- `scheduler.py` orchestrates daily refresh → calls weather, RTE, predictor
- `rte_client.py` → `_store_rte_daily()` in scheduler stores RTE data for ML lag features
- `ml_scorer.py` reads from `rte_daily` table for D-1 lag features (no same-day data = no leakage)
- `predictor.py` stores multi-horizon predictions (J+1 through J+5)

### Database
- SQLite (`tempo.db`), currently at migration version 15
- Key tables: `predictions`, `actuals`, `weather`, `rte_daily`, `weights`, `subscribers`

### ML Model
- GradientBoosting, 33 features, trained on 1827 samples (seasons 2019-2026)
- Cost-sensitive: ROUGE weight=25, BLANC=3, BLEU=1
- Thresholds: rouge_thresh=0.07, blanc_thresh=0.15
- Test accuracy: 83.4%, ROUGE recall: 23.3%
- Model file: `ml_model.pkl`

### Configuration
- `config.py`: All thresholds, weights, city definitions, API keys
- Dynamic RED threshold: 65 normally, 55 if temp<7°C, 50 if temp<4°C (requires budget_score>=50)

### Budget Pressure Mechanics (CRITICAL)
- `predict_range` iterates J+2 → J+15 chronologically with a `sim_remaining` counter
- Each predicted ROUGE/BLANC decrements `sim_remaining` → pressure increases for subsequent days
- BLEU predictions do NOT decrement, but eligible days shrink (deadline approaches) → density rises naturally
- `_score_budget_v2` recalculates `red_d_left` / `white_d_left` from **target_date** (not today) → correct per-day pressure
- Budget weight is only 12% → max 12 points contribution even at budget_score=100
- **Exact eligible day counting** (`_count_eligible_days`): replaces the old `* 5/7` and `* 6/7` approximations. Iterates day by day from `start` to `end`, checking weekday + holiday status. ROUGE mode: weekdays excluding French holidays (R2). BLANC mode: all days except Sundays (R3). Accounts for ~3-4 weekday holidays in RED season (Toussaint, Armistice, Noël, Jour de l'an) that the 5/7 formula ignored.
- **Density override** (slack <= 1): when eligible - remaining <= 1, temperature is irrelevant — EDF MUST place these days. Override forces ROUGE/BLANC BEFORE EDF rules (which still enforce weekends/holidays/R1-R4). Slack adapts naturally to any eligible count (avoids fixed-threshold bugs where density dips mid-sequence). Uses `_count_eligible_days` for exact slack calculation.
- **ML budget guard**: ML BLANC→BLEU filter disabled when budget_score >= 50 (prevents ML from overriding budget-driven BLANC predictions)

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

## Common Pitfalls
- **Data leakage**: Never use same-day RTE consumption for predictions (only lag features D-1+)
- **Multi-horizon storage**: `store_prediction` must only block same-horizon confirmed predictions, not all horizons
- **Orphan cleanup**: Must preserve multi-horizon prediction history (clean per-horizon, not per-date)
- **Weather insert**: `fetched_at` column is NOT NULL — always include it in INSERT statements
- **DB migrations**: Always update version assertions in tests when adding new migrations
