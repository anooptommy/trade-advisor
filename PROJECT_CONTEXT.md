# Trading Agent — Project Context

## Current Trade Advisor objective

This independent copy scans the configured crypto watchlist hourly, passes every successfully analyzed asset's full scanner snapshot and freshness to Gemini, and sends a market overview with conditional scenarios to Telegram. Each subsequent run includes the latest saved assessment and plan so Gemini can report actionable, wait, revise, or no_plan. The full prompt evidence and each successful response are appended to SQLite before delivery.

Gemini's numeric levels must be supported by the supplied metrics or marked unavailable. Stale data cannot support an actionable plan. The user decides whether to trade. The service does not connect to exchanges, place orders, seek execution approval, size positions, assess portfolio exposure, or manage positions. Older candidate-only recommendation objectives below are historical and superseded for this copy.

## Current HTF and conditional-plan semantics

- The deterministic `no_setup` gate remains a strict current-confirmation test: Daily and 4H EMA/SMA trends must align, Daily and 4H swing structures must align with that direction, the latest closed 1H candle must interact with the directional HTF area (or reclaim/reject a 4H EMA), and at least two 1H confirmations must pass. This gate describes whether a setup is confirmed now; it is not a prerequisite for a non-actionable conditional watch plan.
- HTF location confirmation is deliberately strict and evaluates only the latest completed 1H candle. The snapshot now also reports the nearest directional Daily/4H levels, zones and 4H EMA references with close-distance in price, percent and 1H ATR units. Proximity is descriptive only and does not alter setup eligibility.
- Trend or structure alignment is reported as not evaluated when an HTF trend is neutral/unknown; this must not be described as a failed location test.
- CoinGecko OHLC has no volume. Volume remains explicitly unavailable, is not a negative signal, and is not a deterministic setup gate. Partial/unrated HTF level quality and a `not_applicable` candidate risk plan are context limitations, not standalone reasons to suppress a conditional watch plan.
- Gemini may create a non-actionable watch plan when deterministic setup gates are unconfirmed, but it must state the unmet conditions and use only supplied levels/indicators. Missing numeric entry, invalidation, target, or R:R remains unavailable. A previous plan is actionable only when its trigger is present in the updated completed-candle evidence; no alert authorizes execution.

The included GitHub Actions workflow runs once per UTC hour and restores the data directory through Actions cache. Cache retention is best-effort; persistent local storage is needed for a durable, long-term SQLite audit trail.

Watchlist processing is sequential end-to-end per asset: fetch and derive metrics, load that asset's prior plan, call Gemini, persist the response, deliver Telegram, then move on. `GEMINI_REQUEST_DELAY_SECONDS` defaults to 30 seconds between attempted model calls; `GEMINI_RATE_LIMIT_COOLDOWN_SECONDS` defaults to 60 seconds after a Gemini 429. The SDK retry count is bounded and honors a server `Retry-After` header. Spacing can reduce burstiness but cannot bypass account/model quota.

## 1. Project objective

Build a modular crypto market recommendation agent that can:

1. Obtain market data.
2. Calculate deterministic technical indicators.
3. Identify market structure and higher-timeframe context.
4. Apply explicit trading rules.
5. Use Gemini to explain the deterministic analysis.
6. Send qualified recommendations to the user's Telegram bot on an hourly schedule.

The user decides whether to trade, sets position size, and manages positions. This agent does not evaluate portfolio risk, size positions, approve or execute trades, connect to an exchange, or monitor/manage positions.

The system should be built incrementally and remain testable at every stage.

prior work execution and discussion on chatgpt project can be found here for more context:

chat 1 link : https://chatgpt.com/share/6abab9b9-d49c-83e8-97e7-6008744b445f
chat 2 link : https://chatgpt.com/share/6abab9f4-a4c8-83e9-9a4d-7a2fa3b9a9b3
chat 3 link: https://chatgpt.com/share/6ababa0d-2190-83e8-900c-c942d53c62eb
---

## 2. Current provider choices

### Agent / LLM

The project uses **Google Gemini**, not OpenAI, for the agent layer.

The Python implementation uses the Google Gemini Python SDK (`google-genai`) and a `GEMINI_API_KEY` stored in environment configuration.

### Market data

The project is intentionally using the **free CoinGecko MCP** for market data.

The reason for this decision is to avoid making the first implementation dependent on a paid market-data provider.

### Execution

Hyperliquid is the intended eventual venue for crypto perpetual execution.

Live execution is a later milestone and is not part of the current Step 2 implementation.

---

## 3. Current milestone

The project has completed its Step 2 market-data validation and is progressing through Phases 5–6. HTF location/level evidence and availability-aware confidence assessment are in the deterministic analysis layer; confidence reports raw points out of assessable weights, a normalized score fraction, and coverage. Filters must consider fraction and coverage together; the band remains unavailable when required inputs are missing.

### Step 1 — Initial foundation

Step 1 was completed before the current work.

The project already established the initial application/agent foundation and deterministic technical-analysis components.

The user confirmed that the application folder remains:

```text
app/
```

### Step 2 — Real market data

Steps 2A, 2B, and 2C are complete. Step 2D includes configurable multi-asset scanning and persistent CoinGecko candle caching. Live CoinGecko OHLC retrieval and cache reuse have been validated for all six configured assets. Gemini live review remains pending because no asset qualified during validation.

The application connects to the official CoinGecko MCP endpoint from `app/market/data.py` using Python's MCP Streamable HTTP client and calls the server's `execute` tool. The code requests daily and hourly OHLC range data, then resamples complete hourly groups into UTC-aligned 4H bars. The normalized candle frames use UTC timestamps and numeric OHLCV columns, with source, coin ID, quote currency, timeframe, candle origin, and volume availability metadata attached. The OHLC response does not include volume; missing volume stays `NaN`, including after resampling.

The previous implementation plan explicitly stated that the next step was to replace `generate_test_data()` with real market candles/data while preserving the separation of `indicators.py` and `rules.py`.

An architectural correction was then made: CoinGecko market-chart data does not constitute native TradingView-style OHLC candles.

Therefore Step 2 should be implemented carefully rather than pretending that the CoinGecko series is true OHLC.

---

## 4. CoinGecko MCP limitation

The CoinGecko market-chart capability returns timestamped:

- price
- market-cap
- volume

series.

That market-chart endpoint does not provide native candle high/low values. The current adapter instead requests CoinGecko's separate OHLC range endpoint; its response is parsed as genuine OHLC and should remain clearly distinguished from market-chart data. Volume is absent from the OHLC response.

This matters because:

- close/price-based indicators can be calculated from the price series;
- volume-based calculations can use the available volume series;
- true swing-high/swing-low detection requires genuine high/low information;
- supply/demand and some market-structure logic should not be based on invented highs/lows.

The implemented market data path is therefore:

```text
CoinGecko MCP OHLC range endpoint
      ↓
normalized UTC OHLCV-shaped frame (volume unavailable)
      ↓
complete-bucket 4H resampling from hourly OHLC
      ↓
Indicators
      ↓
Market structure
      ↓
Rules
```

Do not silently fabricate OHLC or volume values. The 4H transformation is documented in each derived frame's `attrs["market_data"]["resampling"]`; it drops incomplete target buckets and describes each OHLC aggregation rule.

If an actual OHLC source becomes necessary for market structure, introduce it as a separate data-source abstraction rather than coupling the strategy layer directly to one provider.

---

## 5. Intended architecture

The long-term architecture is:

```text
                    ┌──────────────────┐
                    │  CoinGecko MCP   │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │   Market Data    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │   Indicators     │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Market Structure │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Trading Rules    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Gemini Reasoning │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │   Risk Engine    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │   Hyperliquid    │
                    │    (future)      │
                    └──────────────────┘
```

The boundaries are intentional.

The deterministic technical-analysis layer should not depend on Gemini.

Gemini should not be responsible for inventing indicators or overriding deterministic risk rules.

---

## 6. Existing intended Python structure

The project currently uses:

```text
app/
├── main.py
├── market/
│   ├── __init__.py
│   ├── data.py
│   └── indicators.py
├── strategy/
│   ├── __init__.py
│   ├── market_structure.py
│   └── rules.py
└── test_gemini.py
```

The exact current contents must be inspected before modifications.

---

## 7. Trading analysis framework

The user's preferred framework is higher-timeframe-first.

For the current perpetual-trading framework:

```text
Daily → 4H → 1H
```

### Daily

Used for:

- broad trend
- major support/resistance
- major supply/demand areas
- swing structure
- higher-timeframe context
- divergences

### 4H

Used for:

- intermediate market structure
- trend confirmation
- supply/demand
- swing structure
- locating actionable zones

### 1H

Used more heavily for:

- entry structure
- EMA/SMA interaction
- confirmation
- timing

Higher-timeframe levels are decision zones, not automatic entry signals.

---

## 8. Indicators and inputs

The project has discussed/used:

- 9 EMA
- 21 EMA
- 50 SMA
- 7/30 EMA in some earlier analysis
- 20 EMA
- RSI
- RSI divergence
- MACD
- Volume
- Open Interest
- Funding
- support/resistance
- supply/demand
- swing highs/lows

For the crypto trading agent, only inputs actually available from the selected data source and the ones we calculated in the project codes should be used.

Do not invent missing data.

---

## 9. Target assets / trading context

The project has discussed crypto perpetual trading on Hyperliquid involving assets such as:

- ETH
- BTC
- HYPE
- BNB
- SOL
- AERO

These are context/examples, not an instruction to hard-code the asset universe.

The scan list is configured in `config/watchlist.json`. Each entry specifies a CoinGecko coin ID and display symbol; quote currency and requested daily/hourly history windows are configured in the same file. The current active list is BTC, ETH, SOL, HYPE, and BNB. AERO was removed from active analysis on 2026-09-30 because its small sample had a disproportionate effect on aggregate replay returns; its cached data remains available for future research.

---

## 10. Trading-risk context

The user has previously discussed a Hyperliquid perpetual setup involving:

- leverage around 10x
- maximum trade allocation around 25% of portfolio
- stop-loss around 15%
- take-profit around 35%

more discussion on the risk context can be found in this chat URL: https://chatgpt.com/share/6abab9b9-d49c-83e8-97e7-6008744b445f

These are historical strategy parameters and should **not** be treated as immutable code requirements unless they are explicitly incorporated into the current risk specification.

Live trading should not be enabled based solely on these historical numbers.

---

## 11. Gemini's role

Gemini is intended to operate as the reasoning/explanation layer around deterministic analysis.

A preferred conceptual flow is:

```text
Raw market data
    ↓
Deterministic indicators
    ↓
Deterministic market structure
    ↓
Deterministic rule evaluation
    ↓
Structured analysis object
    ↓
Gemini
    ↓
Human-readable explanation / synthesis
```

Gemini should receive structured facts rather than being asked to calculate everything from unprocessed data.

This makes the system:

- more deterministic
- easier to test
- easier to debug
- easier to audit
- less dependent on LLM consistency

---

## 12. Future Hyperliquid integration

Hyperliquid is a future execution layer.

The intended architecture is:

```text
Signal
  ↓
Risk validation
  ↓
Order construction
  ↓
Execution adapter
  ↓
Hyperliquid
```

The strategy layer should not directly call exchange-order APIs.

A future exchange adapter should isolate exchange-specific implementation.

---

## 13. Important design decisions already made

1. Keep the project folder named `app`.
2. Use Gemini for the agent layer.
3. Use CoinGecko MCP as the free initial market-data source.
4. Keep `indicators.py` and `rules.py` conceptually separated.
5. Do not pretend CoinGecko market-chart data is native OHLC.
6. Build toward a modular architecture suitable for later Hyperliquid integration.
7. Keep live trading out of the current milestone.

---

## 14. What is unknown and must be inspected

Do not assume the exact contents of:

- `app/main.py`
- `app/market/indicators.py`
- `app/strategy/rules.py`
- Gemini configuration
- CoinGecko MCP configuration
- MCP transport/integration method

Codex should inspect the actual repository before making changes.

If the MCP exposes a tool named differently from the expected CoinGecko market-chart capability, use the actual installed tool/schema rather than assuming a name.

---

## 15. Current handoff point

Step 2B is implemented in `app/strategy/market_structure.py` and `app/strategy/rules.py`. It preserves chronological swing labels, touch-counted clustered levels, merged overlapping zones, explicit Daily → 4H → 1H confirmation, and a strict JSON analysis snapshot marked as analysis-only. Focused regression tests are in `tests/test_step2b.py`.

Step 2C is implemented in `app/market/data.py`. `get_market_data` returns normalized daily/hourly candle frames plus a source-level metadata record. `resample_ohlc` records the exact UTC bucket and aggregation rules in frame metadata, drops incomplete buckets, and does not turn unavailable volume into zero. Step 2C checks are in `tests/test_step2c.py`.

Multi-asset orchestration is in `app/scanner.py`; it scans assets sequentially and isolates failures per asset. Deterministic snapshots include HTF location evidence, four closed-candle 1H confirmations, an availability-aware HTF level-quality subtotal, and a partial confidence assessment. Setup eligibility requires aligned Daily/4H trend and structure, an HTF location, and at least two available confirmations. Confidence reports observed points / assessable active points, normalized `score_fraction`, `score_pct` for band and matrix thresholds, coverage against the 91-point active budget, and missing active inputs. A missing active input is excluded from both score numerator and denominator; an evaluated but unconfirmed condition earns zero and remains in the denominator. Volume confirmation and the overall Volume/OI component are excluded from active inputs, scoring, coverage, and confidence bands; commented placeholders remain in `app/strategy/rules.py` for possible future use after a genuine data source and scoring rule are approved. Fraction-based filters require minimum coverage and critical-input gates whose thresholds remain to be selected through backtesting. The confidence band stays null until all 91 active points are assessable; confidence is not applicable when setup is `no_setup`. Candidate candle/EMA heuristics require historical validation. The level-quality labels are suppressed while their active factor set is incomplete. Active confidence assigns 16 points to HTF level quality. Strong rejection, major swing, same-timeframe EMA/SMA confluence, fresh-zone lifecycle, are implemented as provisional candidates; MACD is calculated in app/market/indicators.py and RSI/MACD scoring is in app/strategy/rules.py. Fresh-zone status is unavailable when retained hourly history does not reach the relevant confirmed pivot origin. A candidate goes to Gemini only after deterministic qualification. `app/agents/gemini.py` reviews only that snapshot and is instructed not to invent facts or issue trades. Configure `GEMINI_API_KEY` and optionally `GEMINI_MODEL` in environment settings. Live CoinGecko OHLC access and cache reuse are verified across all six configured assets. No live Gemini review occurred because no scanner run produced a qualifying candidate. The separate app/backtest.py engine now replays the deterministic rules from local cached candles without network access; an initial 31-day hourly replay recorded nine candidate transitions across HYPE, BNB, and AERO. It reports directional forward movement only, not simulated P&L; longer multi-regime history and risk/execution models are still needed for meaningful backtesting.

CoinGecko raw daily and hourly OHLC responses are persisted as CSV under the git-ignored `data/cache/coingecko/` directory, with JSON refresh metadata alongside each file. The daily cache is checked once per UTC day and the hourly cache once per UTC hour. Incremental requests overlap the newest cached date and merge by timestamp. Hourly range requests end at the current completed UTC hour using an ISO date-time; a date-only `to` value resolves to midnight and omits same-day hourly candles. Daily requests remain date-aligned. A failed refresh can use existing cached rows for up to the next retry interval; the returned market-data metadata identifies stale fallback use. Four-hour data continues to be resampled locally from hourly candles and is not fetched separately. Hourly cache retention is configured for 90 days. `app/backfill_hourly.py` seeds longer ranges using sequential requests of at most 31 calendar days, deduplicates overlapping timestamps, and defaults to a 20-second delay. The user confirmed successful 90-day hourly backfills for BTC, ETH, SOL, HYPE, BNB, and AERO. AERO is no longer in the active watchlist, but its cache is retained.

On 2026-09-30, the offline causal replay was extended to compare the 2-of-4 and 3-of-4 entry-confirmation rules and one-factor HTF threshold variants. On the five active assets, 2-of-4 yielded 28 candidate transitions (24h mean directional return -0.5345%, 36% positive; 72h -1.2499%, 36% positive); 3-of-4 yielded five (24h -0.0961%, 40% positive; 72h +0.2392%, 50% positive with four matured). Initial HTF threshold sweeps did not show a consistent outcome improvement. This 90-day sample is diagnostic only; no thresholds were changed because events are few and clustered and the replay has no execution, stop/target, cost, or P&L model. The latest report is in `data/backtest/historical_replay_20260930T091544Z.json` (git-ignored).

On 2026-10-07, the prior 90-day report was additionally evaluated with joint score-fraction/coverage cutoffs. At score fraction >=0.70, 85%/88% coverage selected 14 events (24h mean +0.0835%, 72h mean -0.7213%; 13 matured at 72h); 90% selected 12 (24h -0.0002%, 72h -0.8135%; 11 matured). At score fraction >=0.75, 85%/88% selected six (24h +0.0752%, 72h -0.8506%); 90% selected five (24h +0.1484%, 72h -0.8070%). These clustered small-sample results do not establish a minimum coverage or critical-component filter; score fractions remain diagnostic only. `app/backtest.py` now emits joint score/coverage sweeps and records core setup-gate plus confidence-component availability per event. A fresh full-watchlist replay before the latest optimization exceeded 15 minutes and was stopped. `app/backtest.py` now advances closed Daily/4H prefixes incrementally, reuses each structure snapshot until its next HTF candle close, and groups outcomes by the causal Daily/4H swing-structure pair. All 28 baseline events in the completed 2026-09-30 report were bullish/bullish, so the report cannot compare outcomes across distinct structure regimes. On 2026-10-07, the replay added a one-pass hourly swing-pivot tracker and bypassed repeated closed-candle filtering on known-closed replay prefixes. The active five-asset run completed in 263.5 seconds with 26 baseline events (24h mean -0.6349%, 30.77% positive; 72h mean -1.4606%, 30.43% positive across 23 matured) and three strict events (24h mean -1.0423%, no positive outcomes; 72h mean -1.1410%, no positive outcomes across two matured). Every baseline event remained bullish/bullish. The report also records 9,575 replayed asset-hour observations across all 10 Daily/4H structure combinations seen in the five assets, confirming regime diversity in the sample window; candidate outcomes in other regimes remain unavailable because deterministic setup gates suppress them. CoinGecko hourly OHLC history is currently limited to 90 days on the selected endpoint/plan, so longer-cycle evaluation requires a separately validated data source or plan change. The generated report is `data/backtest/historical_replay_20261006T213100Z.json`. The user paused longer-history regime evaluation until additional data becomes available. CoinGecko documents hourly OHLC history at up to 90 days and the OHLC range endpoint as paid-plan access, so no longer-range fetch or paid integration was started. The confidence/R:R policy classifier in `risk/eligibility.py` requires the complete 91-point active confidence budget, applies thresholds to normalized score percentage, and labels eligible candidates for later risk review only. Analysis-only structural stop/target and gross R:R scenarios are implemented in `risk/planning.py` and surfaced separately in scanner results; position sizing and portfolio/execution controls remain Phase 7 work.

Runtime logs showed HTTP 404 on the optional MCP event-stream GET and on session DELETE during cleanup. The corresponding tool POST can still return data, so these 404s are transport/termination noise. A prior scan also encountered HTTP 429 responses with `Retry-After: 60`. The adapter fetches due daily/hourly ranges sequentially through a single MCP session per asset and reuses on-disk history to reduce repeated requests. Batch interval typing and MCP Python SDK `is_error` handling were fixed and validated with BTC/ETH; cache reuse completed without further CoinGecko requests.

A 2026-09-30 Windows run showed a TLS-handshake access violation in `truststore`'s Windows certificate-chain cleanup before CoinGecko MCP initialization. `app/market/data.py` uses a verified SSL context built from platform trust roots plus certifi and optional `SSL_CERT_FILE`/`SSL_CERT_DIR` roots; TLS verification remains enabled. Scan results and logs report per-timeframe refresh status, latest candle time and age, and last successful refresh, explicitly marking stale-cache fallback. A 2026-10-06 run reproduced a certificate verification failure when the adapter supplied certifi alone. With the platform-plus-certifi context, the CoinGecko MCP initialize POST returned HTTP 200 with a session ID. A tool execute/fresh-data refresh has not yet been confirmed after this trust change.

The initial 14-phase outline remains the long-term roadmap. Paid TradingView and Exchange MCP connections are explicitly out of scope. Market data uses CoinGecko MCP; portfolio/account data is deferred until paper/live trading integration. Remaining work includes the full decision workflow, paper execution, hourly scheduling, position monitoring, backtesting/evaluation, and a separately authorized live-trading milestone. Current task status is maintained in `IMPLEMENTATION_PLAN.md`.

The user approved the 32-point HTF rules PDF as the V1 candidate specification. Its analysis, confidence, risk, execution, governor, audit, and structural-stop-first rules are mapped to phases in `IMPLEMENTATION_PLAN.md` and detailed in `TRADING_STRATEGY.md`. PDF numeric values are research candidates for backtesting/paper use, not live defaults. A 1.5R minimum is a hard execution gate at every confidence score. The confidence bands and R:R matrix differ for scores 55-59; order eligibility follows the matrix's reject result. The PDF's daily-loss threshold/reset language remains unresolved and must not be encoded as a default until clarified. Volume/OI are excluded from active confidence input and scoring; commented placeholders may be reactivated only after a genuine source and scoring rule are approved. Funding and scheduled-event inputs are not connected and must not be fabricated or counted as confirmation. The paper account will use virtual portfolio state; real portfolio/account data remains deferred to live-venue integration.

The previous ChatGPT handoff notes below are historical context; their stated next action predates the completed Step 2B work.

## Updated product goal — 2026-10-10

This copy is scoped as an hourly recommendation service using the existing multi-asset scanner, deterministic setup rules, and availability-aware confidence evidence. It sends only candidates that qualify under the existing deterministic rules. Each alert reports the token, direction, latest closed 1H price reference, candidate stop/target/R:R when supported, confidence on a 0–10 display scale, raw partial points/fraction/coverage, and an evidence-based reason. Confidence is not a calibrated probability, and the example score 9.2 is not a target or guaranteed output.

Unavailable confidence inputs stay out of both numerator and denominator; evaluated but unconfirmed inputs count as zero in the denominator. The band remains unavailable when required evidence is missing. Do not apply unbacktested coverage or critical-input filters.

The existing planner returns two unvalidated ATR-buffer scenarios. Do not silently select one or label candidate geometry as validated. Where no single supported value exists, expose both scenarios as candidates or state the field is unresolved. Existing sizing, portfolio, governor, execution, and position-monitoring modules are not part of the recommendation runtime. Do not connect to exchanges or place orders.

Next: implement a non-overlapping hourly scheduler and Telegram sender, reading bot token and destination from environment variables. Preserve sequential provider requests, cache freshness, and run logs. Never log secrets.

The previous ChatGPT work stopped immediately before implementing the next part of Step 2. these were the last chat response from chatgpt:
I recommend the next Step 2 sub-step
Before we bring Gemini back into the loop, let's make the deterministic Python engine stronger:
1. Improve swing-based support/resistance detection
2. Cluster nearby levels
3. Merge overlapping supply/demand zones
4. Add market structure classification:
   - HH/HL → bullish
   - LH/LL → bearish
   - mixed → ranging/transition
5. Make the Daily → 4H → 1H rules more explicit.
6. Produce a compact JSON trading snapshot that Gemini can consume later.
We should not add Gemini yet. The deterministic layer should be trustworthy first.
The Session termination failed: 404 messages can also be cleaned up afterward; they aren't affecting the returned data, as demonstrated by the successful 181/745 candle retrieval.
If you want to continue with Step 2, I would next modify market_structure.py, not touch the working CoinGecko code.

The intended next action is:

> Inspect the current repository and continue with the above chatgpt sub-step recommendations while preserving the existing indicator/rule architecture.

Do not jump directly to Hyperliquid execution.

Do not redesign the entire application.

Do not fabricate OHLC data.

## Dynamic Telegram infographic

The hourly recommender sends a dark PNG dashboard for every successfully analyzed watchlist asset, whether or not existing rules qualify it for a trade recommendation. Qualifying candidate text alerts continue through the existing path; no-candidate scans still send their usual text summary. `app/scanner.py` exposes the already-indicated 1D/4H/1H frames as renderer-only `chart_data`; deterministic snapshot and setup eligibility remain unchanged. `app/visualization/market_infographic.py` maps the existing eight-factor evidence and renders OHLC close plus available EMA 9, EMA 21, and SMA 50 lines, levels only from existing structure output, and confidence only from the deterministic score. CoinGecko OHLC volume remains unavailable; there is no synthetic candle or volume display. Market regime is derived conservatively from existing Daily/4H swing structures, and change direction is labeled unknown because no previous factor snapshot is provided. Proximity thresholds, output directory, dimensions, and optional sending are configured through `INFOGRAPHIC_LEVEL_THRESHOLDS_PCT`, `INFOGRAPHIC_OUTPUT_DIR`, `INFOGRAPHIC_WIDTH`, `INFOGRAPHIC_HEIGHT`, and `TELEGRAM_SEND_INFOGRAPHIC`. Matplotlib and Pillow are renderer dependencies. PNGs are held in a temporary subdirectory and deleted after delivery.


On 2026-10-07, added `risk/planning.py` to derive analysis-only structural stop and nearest-forward HTF target candidates from Daily/4H levels and zones, report gross R:R under separate 0.5x and 1.0x 1H ATR buffer scenarios, and pass each result through the confidence/R:R classifier. The scanner surfaces this separately from the Gemini snapshot, rejects stale-cache data for risk planning, and labels every result as non-executable. Added `risk/engine.py` for configurable candidate sizing, capped by explicit risk, position-notional, and leverage inputs; it requires a supplied liquidation-price estimate and refuses to derive venue rules. Added `risk/governor.py` for explicit daily-loss, open-position, loss-streak, cooldown, and setup-TTL controls. On 2026-10-10, added `risk/portfolio.py` to load read-only state from an explicitly named active paper account: fresh equity snapshot, UTC-day realized P&L plus fees/funding from the base-currency ledger, open/partially-closed position count, and consecutive closed-position losses. Snapshot age is caller-bounded and required. The module composes this measured state with sizing and governor checks only when callers supply limits and a liquidation estimate; `app/scan_persistence.py` can store the resulting review and sizing scenario against a trade suggestion. No account, policy, or execution flow is created automatically. It fails closed on missing/stale equity, non-base daily ledger currencies, and ambiguous daily ledger adjustments. No thresholds are defaults; daily-loss policy remains unresolved. Further historical and multi-regime replay is paused because no additional hourly history is available.


On 2026-10-07, initialized `data/trading_agent.sqlite3` using the versioned `db/migrations/0001_initial.sql` schema via `app/database.py`; later additive migrations extend it. The schema has 23 application tables plus `schema_migrations`, covering source candles, scans/suggestions/reviews, risk decisions, paper orders/fills/positions, ledger, and portfolio snapshots. The initial offline cache import populated six instruments (BTC, ETH, SOL, HYPE, BNB active; AERO inactive) and 13,879 daily/hourly OHLC candles; all volume remains unavailable. Portfolio snapshot common columns are nullable and the raw provider payload is preserved so paper API fields can be revised after inspection. Orders are constrained to paper mode. `app/import_cached_ohlc.py` can repeat the idempotent OHLC cache import without network access. Successful new daily/hourly provider refreshes create `market_data_runs` entries and upsert candles into SQLite, including latest-candle revisions returned by the one-day-overlap API request. Each scan now records `scan_runs` and per-asset `scan_results` (including failures); `direction` records a qualifying side or falls back to Daily directional bias, and `confidence_status` explains when scores/bands are not applicable (such as `no_setup`). Closed 1H prices are added as linked `price_observations` when the source candle exists, and complete local 4H resamples are stored as derived candles with a derivation version. Deterministic candidate setups create linked `trade_suggestions`, with current structural risk plans and scenarios saved explicitly as analysis-only; Gemini review outcomes are persisted in `agent_reviews`. Cache reuse does not create provider-ingestion records, and the CSV cache remains the scanner's read source. Paper accounts, trade decisions, orders, fills, positions, ledger, and portfolio snapshots remain untouched because no paper-execution decision or paper API data is available.
