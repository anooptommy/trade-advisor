# Trade Advisor — Implementation Plan

## Current status

**Current milestone: Hourly Gemini market assessment and conditional plan history**

This copy preserves the CoinGecko OHLC adapter, UTC metadata, complete 4H resampling, indicators, market structure, and deterministic evidence. It passes every successful per-asset snapshot, freshness information, and existing analysis-only candidate risk context to Gemini, independent of the old candidate gate. JSON-schema-constrained assessments are saved append-only before Telegram delivery. Later cycles include the previous assessment and plan so Gemini can return actionable, wait, revise, or no_plan. Deterministic no_setup remains a strict current-confirmation result but does not suppress a non-actionable conditional watch plan. HTF location confirmation remains strict; nearest level/zone/EMA distances and explicit context-alignment details are now supplied for interpretation. Missing volume, partial level-quality coverage, and not_applicable candidate risk context must be explained as limitations rather than treated as independent watch-plan blockers.

No exchange access, order placement, execution approval, portfolio risk, sizing, or position management is in scope. Unsupported numeric plan levels are unavailable; stale data cannot make a plan actionable.

## Remaining validation
- Review the next real scan for useful, evidence-based conditional watch plans when no_setup holds; confirm that prices absent from the metrics remain unavailable.
- Review Telegram's explicit trend/structure alignment, strict location confirmation, nearest HTF distances, level-quality coverage, and volume limitation.
- Confirm the hourly process in the user's runtime environment.
- Reassess provider freshness if the latest completed 1H candle is still missing.

## Retained research
Historical sections below record work inherited from trade_recommender. Portfolio, execution, and candidate-only milestones are not active objectives for Trade Advisor.

---

# Implemented milestone details (Steps 1–2)

## Step 1A — Project / Gemini foundation
Status: **Completed**

Initial project structure and Gemini integration were established.

Key decision:

- Gemini is the agent/LLM provider.
- Python uses the Google Gemini SDK.
- API credentials are stored outside source code.

## Step 1B — Deterministic indicators and rules
Status: **Completed / existing**

The project established the conceptual separation between:

- market data
- indicators
- rules

Existing files include/are expected to include:

```text
app/market/indicators.py
app/strategy/rules.py
app/main.py
```

Exact contents must be inspected before modification.

---

## Step 2A — Replace generated market data with coingecko data
Status: **Completed / existing**

## Step 2B — Strengthen deterministic Python engine
Status: **Completed**
### Objective

1. Improve swing-based support/resistance detection
2. Cluster nearby levels
3. Merge overlapping supply/demand zones
4. Add market structure classification:
	- HH/HL → bullish
	- LH/LL → bearish
	- mixed → ranging/transition
5. Make the Daily → 4H → 1H rules more explicit.
6. Produce a compact JSON trading snapshot that Gemini can consume later.

### Constraints

- Preserve the indicator/rule architecture.
- Do not redesign working Step 1 unnecessarily.
- Do not fabricate true OHLC highs/lows.
- Keep symbol/asset configuration explicit.
- Keep timeframe/data-resolution assumptions explicit.

### Expected architecture

```text
CoinGecko MCP
      ↓
app/market/data.py
      ↓
normalized market-data structure
      ↓
app/market/indicators.py
      ↓
indicator output
      ↓
app/strategy/rules.py
      ↓
rule evaluation
```

Implemented chronological swing classification, touch-counted level clusters, merged overlapping swing-centered zones, explicit Daily/4H context plus 1H confirmation, and a strict JSON analysis snapshot. Focused deterministic tests cover these behaviors. No market data, indicators, Gemini integration, or execution code was changed.

---

## Step 2C — Data normalization / resampling
Status: **Completed**

The normalized candle DataFrame contract uses UTC `timestamp`, numeric `open`, `high`, `low`, `close`, and `volume` fields. DataFrame metadata records CoinGecko MCP source, coin ID, quote currency, source timeframe, candle origin, and volume availability. The OHLC range endpoint provides OHLC but no volume; volume remains missing throughout.

Hourly OHLC is resampled into UTC epoch-aligned, left-closed/left-labeled bars. Open is the first source open, high the maximum source high, low the minimum source low, and close the last source close. Only complete target buckets are retained. Volume is summed only if present and remains missing otherwise.

No market-chart price samples are being converted to OHLC. The resampler accepts actual provider OHLC fields only.

## Step 2D — Proper OHLC source decision and validation
Status: **CoinGecko OHLC and cache reuse validated across all six configured assets**

The CoinGecko MCP `coins.ohlc.getRange` path returned provider OHLC for BTC, ETH, SOL, HYPE, BNB, and AERO. Cache reuse was also verified. The optional event-stream GET and session DELETE may return 404 during MCP cleanup without invalidating successful tool results. Small-batch validation did not encounter 429s. Gemini has not had a live review because no candidate qualified.

The current CoinGecko approach may be insufficient for:

- true swing highs/lows
- supply/demand
- candlestick patterns
- some market-structure calculations

The application uses CoinGecko MCP's `coins.ohlc.getRange` endpoint. Daily and hourly cache retention is configurable; the hourly target is now 90 days. `app.backfill_hourly` seeds older history in sequential requests no longer than 31 calendar days, merges/deduplicates into the existing cache, and spaces provider requests. The user confirmed successful 90-day hourly backfills for all six configured assets. Reassess data-source sufficiency if the selected MCP access does not support the required range; any new source must sit behind a data-provider boundary.

Keep source semantics explicit and do not fall back to fabricated candles.

## Multi-asset scan and Gemini candidate handoff
Status: **Implemented; all six assets analyzed and cache reuse verified; Gemini live review pending**

- Configure assets by CoinGecko ID and display symbol in `config/watchlist.json`.
- Scan sequentially and isolate provider failures per asset.
- Persist normalized raw daily/hourly OHLC in git-ignored `data/cache/coingecko/`; refresh daily once per UTC day and hourly once per UTC hour, overlapping the latest date to capture revisions.
- For longer replay history on selected assets, run `python -m app.backfill_hourly --assets BTC,ETH --days 90`; this explicit backfill chunks hourly range requests and defaults to a 20-second delay. All six currently configured assets have already been seeded to 90 days.
- Fetch due intervals sequentially in one MCP session per asset. Use cached candles during a 60-second retry cooldown after a failed refresh and mark that fallback in market-data metadata.
- Forward a candidate to Gemini only when the deterministic setup is potential long/short and Daily plus 4H market structure agree with that direction.
- Gemini reviews the structured snapshot; it cannot change the deterministic setup or authorize execution.
- The public CoinGecko MCP has fair-use limits that may vary. A cold scan still requests initial daily and hourly history for each configured asset; the cache reduces repeated history fetches on later runs.
- Validate configured assets in batches of two or three and confirm same-hour rescans reuse local caches. The six-asset validation completed without 429s; all returned `no_setup`, so Gemini has not yet received a live candidate.

---

# Original 14-phase roadmap and current status

## Phase 1 — Development environment
Status: **Completed**

Python application, Gemini foundation, environment-based secrets, and the existing deterministic components are established.

## Phase 2 — TradingView MCP connection
Status: **Intentionally omitted**

The paid TradingView MCP plan was not adopted. TradingView-style analysis is implemented in Python from CoinGecko OHLC instead. Do not treat this MCP as a dependency unless the user later changes the provider decision.

## Phase 3 — Exchange MCP connection
Status: **Intentionally omitted; paid MCP is out of scope**

The paid Exchange MCP plan was not adopted. No account, portfolio, or exchange-order connection is currently required. Paper execution will use an internal simulator. Any later Hyperliquid venue adapter and real account/portfolio data belong to the explicitly scoped live-trading preparation phase, not to this omitted paid MCP phase.

## Phase 4 — Market data and portfolio data layer
Status: **Market-data portion implemented; portfolio deferred**

CoinGecko MCP provides market OHLC, which is cached locally. The current OHLC response has no volume; funding, open interest, and scheduled-event/news inputs are also not currently configured. Track each field's availability and do not fabricate values or count absent inputs as confirmation. Confidence points use a variable denominator containing only assessable component weights; the band remains unavailable until every required component is assessed. Portfolio/account data will be introduced with the paper/live trading stages: paper trading uses an explicit virtual portfolio, and real account data belongs to later venue integration.

## Phase 5 — Higher-timeframe analysis engine
Status: **HTF location, 1H evidence, and all OHLC-computable level-quality factors implemented; provisional thresholds require backtesting**

The deterministic snapshot requires aligned Daily/4H trend and structure, interaction with a Daily/4H decision location, and at least two of four observable 1H confirmations. HTF level quality now evaluates Daily/4H level interaction, clustered-pivot touch count, strong 1H rejection, prior liquidity sweep, major swing (>=2 ATR), same-timeframe EMA/SMA proximity (<=0.25 ATR), and fresh-zone first retest. Volume confirmation and overall Volume/OI are excluded from active inputs, scoring, coverage, and bands; commented placeholders remain for future activation after reliable data and an approved scoring rule exist. Strong/very-strong labels remain withheld until active factor evidence is complete. These are provisional candidate heuristics requiring historical review. The active confidence budget is 91 points, including 16 points for HTF level quality.

## Phase 6 — Trade setup and confidence engine
Status: **Setup screening refined; availability-aware candidate confidence assessment implemented**

Candidate confidence assessment uses the 91-point active component budget. For a qualifying setup it reports raw observed points out of currently assessable weights, the normalized `score_fraction`, `score_pct` for band and matrix thresholds, coverage against the active budget, and missing active inputs. Unavailable active inputs are excluded from the partial-score numerator and denominator; evaluated but unconfirmed factors score zero and remain in the denominator. Any fraction-based filter must also require a minimum coverage and critical-input gates; thresholds need backtesting before use. The replay now reports joint score-fraction/coverage sweeps and records core setup-gate and confidence-component availability for each event. The prior 90-day sample did not support a coverage cutoff: 85% and 88% floors left the 0.70/0.75 score-filter cohorts unchanged; 90% reduced sample sizes without consistent forward-return improvement. No score-fraction filter or new minimum coverage threshold is approved. Existing deterministic setup gates remain authoritative. A confidence band is emitted only when all 91 active points are assessable; volume/OI does not affect completeness. Longer multi-regime outcome replay is paused until additional historical hourly data becomes available. Remaining Phase 6 research includes evaluating provisional RSI/MACD and HTF thresholds and critical-input policies; volume/OI may be reconsidered only after reliable data and an approved scoring rule are available. The policy-only matrix in `risk/eligibility.py` is integrated with analysis-only structural stop/target planning in `risk/planning.py`. The scan result evaluates both documented 0.5x and 1.0x 1H ATR buffer scenarios without choosing a default; it uses the nearest structural invalidation anchor and forward HTF target, calculates gross R:R, and applies the matrix only when confidence is complete. It never sizes or authorizes execution. Enforce 1.5R as a hard execution floor at every confidence score; a matrix "watch" result below 1.5R remains non-executable. The matrix's `<60` confidence row rejects risk review even though the descriptive bands call 55-59 "watch." Confidence must not be presented as a probability unless calibrated against evaluation data.

## Phase 7 — Deterministic risk engine
Status: **Out of scope for recommendation runtime; legacy modules retained**

The copy retains earlier analysis-only modules in `risk/` for provenance, but the recommendation runtime must not invoke candidate sizing, portfolio-state evaluation, or governor checks. The stop/target planner is used only to expose its candidate price geometry; its two ATR-buffer alternatives remain explicitly unvalidated. Do not add risk-policy defaults or wire the legacy portfolio workflow into alerts.

## Phase 8 — Trade decision agent
Status: **Replaced by recommendation formatting; no automated decision or approval**

Preserve four distinct responsibilities: Analysis Engine, Risk Engine, Execution Engine, and Risk Governor. Gemini currently reviews only candidates that pass deterministic setup and HTF structure gates. Define the decision flow and output schema, but keep deterministic eligibility and risk approval authoritative. Gemini must not fabricate data, bypass risk controls, or execute trades; the Risk Governor can veto any strategy or agent recommendation.

## Phase 9 — Paper execution engine
Status: **Out of scope**

Do not build paper orders, fills, simulated positions, or P&L into this service. The user decides whether to trade and handles sizing and position management.

## Phase 10 — Trade database and logging
Status: **File logging and initial SQLite schema implemented; application persistence planned**

The initial versioned SQLite schema is in `db/migrations/0001_initial.sql`; additive changes are in later numbered migrations. `app/database.py` initializes the git-ignored `data/trading_agent.sqlite3`. It defines instruments, source mappings, candle/price observations, ingestion and scan history, strategy/policy versions, deterministic suggestions, agent reviews, risk assessments/scenarios, paper accounts, decisions, orders/order events/fills, positions, ledger entries, and portfolio snapshots/marks. Portfolio snapshots keep nullable common fields plus a versioned, credential-scrubbed raw provider payload so fields can be promoted into columns as the paper-trading API contract becomes known. Never persist API keys or authentication headers in payloads. `app/import_cached_ohlc.py` imports local CoinGecko CSV caches offline and idempotently; the initial import loaded 13,879 provider candles for BTC, ETH, SOL, HYPE, BNB, and inactive cached AERO. Successful daily/hourly provider refreshes are recorded in `market_data_runs` and upserted into `candles`; complete locally resampled 4H candles are separately stored as derived data. Cache-only and stale-fallback checks do not claim a provider refresh. Each application scan records a `scan_runs` entry and per-asset `scan_results`, including failures; scan result `direction` records a qualifying side or falls back to Daily directional bias, while `setup` remains authoritative for candidate eligibility. `confidence_status` identifies cases where a numeric score is not applicable; no-setup score fractions and bands remain NULL. Linked 1H candle-close observations, deterministic `trade_suggestions`, analysis-only risk plans/scenarios, and Gemini reviews are persisted when available. CoinGecko OHLC has no volume, which remains NULL/unavailable. Paper accounts, trade decisions, orders, fills, positions, and portfolio tables remain unpopulated because the current run makes no paper-execution decisions and no paper API is connected. Next, implement persistence only as execution and paper-portfolio milestones define those records. Persist the PDF's mandatory trade audit record before every simulated execution decision: trade ID, direction and timeframe bias, levels, entry zone, setup reasons, confidence, entry/stop/risk/targets/R:R/size/leverage, available funding and portfolio exposure, and EXECUTE/REJECT with reasons. Candle cache files remain provider refresh cache, not the trade database. Persistent ledger and state records will support loss-streak controls, cooldowns, and setup expiry.

## Phase 11 — Hourly scheduler
Status: **Implemented in `app.hourly_recommender`; verify operational deployment separately**

The standalone process aligns runs to UTC hour boundaries and awaits each scan before scheduling the next, preventing overlap. Qualified candidates are delivered through Telegram using environment credentials. Operational deployment, alert delivery failure monitoring, and a single approved stop methodology remain follow-up work.

## Phase 12 — Position monitoring
Status: **Out of scope; user manages positions**

Monitor simulated positions first. Apply structural/trailing stops, target and partial-exit rules, setup TTL, order expiry/cancel/anti-chase behavior, post-stop cooldown/re-entry requirements, loss-streak controls, and emergency conditions. Venue-position monitoring belongs only after explicitly authorized live-venue integration.

## Phase 13 — Backtesting and evaluation
Status: **90-day diagnostic replay and initial threshold/coverage comparisons completed; further historical and multi-regime outcome evaluation paused because additional hourly history is unavailable**

`app/backtest.py` replays the deterministic analysis causally against local cached candles without network calls and compares the current 2-of-4 hourly confirmation rule with 3-of-4. The latest completed 90-day run used five active assets after AERO was removed: 2-of-4 produced 28 transitions (24h mean directional return -0.5345%, 35.71% positive; 72h mean -1.2499%, 36% positive). 3-of-4 produced five transitions (24h mean -0.0961%, 40% positive; 72h mean +0.2392%, 50% positive, with four matured observations). Initial one-factor sweeps covered major-swing 1.5/2.0/2.5 ATR, MA proximity 0.25/0.50 ATR, rejection wick/body 1.0/1.5/2.0, and close-edge fractions 0.25/0.35/0.45. Joint score/coverage diagnostics from this sample: at a 0.70 score fraction, 85% or 88% coverage selected 14 events (24h mean +0.0835%, 72h mean -0.7213%, 13 matured); 90% coverage selected 12 (24h mean -0.0002%, 72h mean -0.8135%, 11 matured). At a 0.75 score fraction, 85%/88% selected six (24h +0.0752%, 72h -0.8506%); 90% selected five (24h +0.1484%, 72h -0.8070%). The coverage floors did not improve both horizons, and samples are too small and clustered to choose a gate. An all-asset replay before the latest optimization was stopped after more than 15 minutes without completing. The optimized replay uses an incremental 1H swing-pivot tracker, avoiding a full hourly-history pivot rescan at every candle, and reuses known-closed candle prefixes. The five active assets completed in 263.5 seconds. The latest run recorded 26 baseline events (24h mean -0.6349%, 30.77% positive; 72h mean -1.4606%, 30.43% positive across 23 matured) and three 3-of-4 events (24h mean -1.0423%, none positive; 72h mean -1.1410%, none positive across two matured). All 26 baseline events remained in the same bullish Daily/bullish 4H structure group; however, the report now records exposure across all 10 observed Daily/4H structure combinations (9,575 replayed asset-hour observations). This confirms regime diversity in the replay window but cannot compare candidate outcomes outside bullish/bullish because deterministic setup gates exclude them. Report: `data/backtest/historical_replay_20261006T213100Z.json`. The replay does not model fills, stops/targets, fees, slippage, sizing, overlapping positions, or P&L. No thresholds or score filters are promoted. Continue with faster multi-regime history and realistic trade simulation before calibration. Backtesting is a hard gate before any candidate settings are promoted for live use; do not describe replay or paper results or candidate confidence as proof of profitability.

## Phase 14 — Live trading with very small capital
Status: **Out of scope for this recommendation service**

Do not add a Hyperliquid adapter, account connection, private-key handling, or order placement to this project copy.

---

# Immediate next action

**Configure Telegram credentials and run the recommender as a persistent hourly process.**

The next task is:

> Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the copy's local environment, then deploy `python -m app.hourly_recommender` as a persistent process. Review message delivery and failure logs. Keep both existing ATR stop-buffer variants explicitly labeled as unvalidated candidates until the user selects a single methodology; do not add sizing, portfolio-risk decisions, orders, exchange connectivity, or position management. Historical replay remains paused unless more data becomes available or the user asks.

Step 2B checks are in `tests/test_step2b.py`; Step 2C checks are in `tests/test_step2c.py`. Do not proceed to Hyperliquid execution.

## Dynamic HTF confluence infographic

Status: **Implemented and verified (72 tests passing)**

The hourly recommender sends a dashboard for every successfully analyzed asset, including those that do not qualify under deterministic setup rules. Existing candidate text recommendations and no-candidate summaries remain unchanged. The renderer consumes scanner-provided indicator-enriched frames and the existing snapshot; it does not participate in eligibility, risk, or execution logic. It shows the eight requested factors, available OHLC close/EMA/SMA series, existing swing levels, freshness context, and existing confidence evidence. CoinGecko volume and unsupported factor calculations remain unavailable. Direction of change is unknown where no prior comparable factor snapshot exists. Telegram photo delivery reuses the configured bot/chat, and image failures do not suppress text alerts. Tests are in `tests/test_market_infographic.py`; run `.\\.venv\\Scripts\\python.exe -m pytest tests -q`.
