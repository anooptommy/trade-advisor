# Trading Agent — Trading Strategy Specification

## 1. Purpose

This document captures the trading-analysis framework that the software is intended to implement.

The strategy layer should remain deterministic wherever possible. Gemini is an explanation/reasoning layer, not a replacement for explicit technical rules.

---

## 2. Higher-timeframe-first methodology

The primary framework is:

```text
Daily → 4H → 1H
```

The purpose is to avoid taking lower-timeframe signals without understanding the higher-timeframe context.

### Daily

Determine:

- overall directional context
- major support/resistance
- major swing highs/lows
- major supply/demand zones
- significant momentum/divergence context

### 4H

Determine:

- intermediate trend
- market structure
- actionable supply/demand
- swing structure
- relationship to Daily zones

### 1H

Determine:

- entry structure
- moving-average interaction
- confirmation
- timing

---

## 3. Decision-zone principle

Higher-timeframe levels are **decision zones**, not automatic entries.

A price reaching support does not automatically mean long.

A price reaching resistance does not automatically mean short.

The lower timeframe should provide the confirmation/entry structure required by the strategy rules.

---

## 4. Moving averages

The main moving averages discussed for the strategy include:

- 9 EMA
- 21 EMA
- 50 SMA

Additional combinations previously discussed include:

- 7 EMA / 30 EMA
- 20 EMA

The primary current framework should prefer the 9/21 EMA and 50 SMA unless the implementation explicitly specifies another combination.

---

## 5. Momentum

### RSI

RSI can be used for:

- momentum context
- overbought/oversold context
- divergence detection

RSI should not be treated as a standalone buy/sell signal.

### MACD

MACD can be used as supporting momentum/trend information.

It should be combined with price structure and the higher-timeframe context.

---

## 6. Market structure

Where genuine OHLC data is available, the strategy can identify:

- swing highs
- swing lows
- higher highs
- higher lows
- lower highs
- lower lows
- trend transitions
- breaks of structure

Market structure should be calculated from actual candle high/low data.

If the available data source does not contain true high/low values, the implementation must not fabricate them.

---

## 7. Support and resistance

Support/resistance should be represented as zones where appropriate rather than assuming every level is a precise single price.

Higher-timeframe zones have greater contextual importance than isolated lower-timeframe levels.

---

## 8. Supply and demand

Supply/demand zones are intended to be part of the market-structure layer.

They should not be generated from invented OHLC highs/lows.

The implementation should clearly document the detection algorithm once it is introduced.

---

## 9. Volume

Volume can provide supporting confirmation.

Potential uses include:

- expansion/contraction
- breakout confirmation
- momentum confirmation
- divergence/context

Volume should not override price structure by itself.

---

## 10. Open Interest and funding

For crypto perpetuals, Open Interest and funding can eventually be incorporated into the analysis.

They are additional context, not automatic signals.

The system should only calculate/use them when reliable data is available.

---

## 11. Signal construction

A future signal should be structured rather than represented only as free-form text.

Conceptually:

```text
Signal
├── symbol
├── timeframe
├── direction
├── market_context
├── support_resistance
├── market_structure
├── indicators
├── confirmations
├── invalidation
├── entry_zone
├── stop_loss
├── take_profit
└── confidence / rationale
```

The exact schema should be designed during the appropriate implementation milestone rather than prematurely hard-coded.

---

## 12. Risk

Risk management is a separate layer.

Historical user parameters have included:

- high leverage
- a maximum portfolio allocation per trade
- a stop loss
- a take profit

These should not automatically become live-trading constants.

The eventual risk engine should explicitly define:

- maximum position size
- maximum account risk per trade
- leverage constraints
- stop-loss behavior
- take-profit behavior
- maximum simultaneous positions
- daily loss limits
- liquidation-distance constraints
- emergency shutdown behavior

---

## 13. Live execution

The strategy layer must never directly place exchange orders.

The intended separation is:

```text
Strategy
   ↓
Signal
   ↓
Risk Engine
   ↓
Execution Adapter
   ↓
Hyperliquid
```

Live execution is a future milestone.

---

## 14. Validation philosophy

Before live trading:

1. Unit-test indicator calculations.
2. Unit-test rule evaluation.
3. Validate market-structure detection.
4. Test against historical data.
5. Backtest.
6. Paper trade.
7. Log every signal and decision.
8. Validate risk constraints.
9. Only then consider exchange execution.

---

## 15. HTF setup and confidence specification (V1 candidate)

The analysis flow is:

```text
Market scan
  -> Daily/4H trend and structure
  -> HTF levels and location
  -> Setup quality
  -> 1H entry confirmation
  -> Stop/target and R:R
  -> Confidence
  -> Position size
  -> Risk Governor approval
  -> Paper/live execution layer
  -> Position management and trade audit
```

`NO TRADE` is a valid and expected result. Do not force a directional setup when the market is mixed, the price is between meaningful HTF levels, confirmation is missing, or any risk gate fails.

### 15.1 Market regime and location

- Analyze Daily and 4H before considering a trade.
- Bullish context requires higher highs/higher lows, price above important support, supportive 9/21 EMA and 50 SMA structure, and no immediate major resistance.
- Bearish context requires lower highs/lower lows, price below important resistance, bearish 9/21 EMA structure, 50 SMA acting as resistance, and no immediate major support.
- Neutral/mixed context does not permit a directional trade unless a separately defined range setup qualifies.
- Long locations may include HTF support, a sweep below support, a 9/21 EMA pullback, a broken-resistance retest, or a defined demand zone. Short locations are the inverse around resistance, sweeps, EMA rejection, broken-support retests, or supply zones.
- The source candidate HTF level-quality score includes Daily level +3; 4H level +2; multiple touches +2; strong rejection +2; prior liquidity sweep +2; major swing high/low +2; volume confirmation +1; EMA/SMA confluence +1; fresh/unmitigated zone +2. Volume confirmation is currently a commented optional placeholder and excluded from active scoring; active HTF level quality is 16 points. Candidate labels: strong at 6+, very strong at 8+.

#### Candidate strong-rejection rule (2 points)

Evaluate the latest fully closed 1H candle against the relevant Daily/4H decision area. For a bullish rejection, the candle must test support/demand and close back above it, have a lower wick at least 1.5 times its body, and close in the upper 35% of its full high-low range. For a bearish rejection, reverse the conditions: test resistance/supply and close back below it, have an upper wick at least 1.5 times its body, and close in the lower 35% of its range. A zero-range or directionless/doji candle does not qualify. Award the 2 points only when all applicable conditions pass. These thresholds are provisional candidate values and must be reviewed and potentially adjusted through backtesting; they are not calibrated or validated trading parameters.

#### Candidate major-swing rule (2 points)

Identify major pivots on the Daily or 4H timeframe using the existing confirmed swing-pivot detector. A swing high is major when price subsequently moves down to the next confirmed swing low by at least 2 × ATR(14), with ATR measured on that same timeframe at the swing-high pivot. A swing low is major when price subsequently moves up to the next confirmed swing high by at least 2 × ATR(14), with ATR measured at the swing-low pivot. The existing detector confirms a pivot only after its five right-side candles are available. Award the 2 points only when the latest fully closed 1H candle interacts with a Daily/4H level or zone associated with a qualifying major pivot. These are provisional candidate conditions: the 2 × ATR threshold and the rule's behavior must be reviewed and potentially adjusted through backtesting; they are not calibrated or validated trading parameters. If required pivot, ATR, or level-interaction evidence is unavailable, leave this factor unavailable rather than treating it as a failed or satisfied observation.

#### Candidate EMA/SMA confluence-at-level rule (1 point)

Evaluate the 9 EMA, 21 EMA, and 50 SMA on the same timeframe as the relevant HTF level: use 4H averages for a 4H level and Daily averages for a Daily level. Normalize proximity using ATR(14) from that same timeframe. An average is near a price level when its absolute distance from the level is no greater than 0.25 × ATR(14). For a zone, distance is zero when the average falls inside the zone; otherwise use the distance to the nearest zone edge. Award the 1 point once if at least one same-timeframe average is within this threshold of relevant support/demand for a long or resistance/supply for a short. Do not award additional points for multiple nearby averages. Use the 1H candle only to establish the interaction with the HTF area; 1H moving averages are not used for this level-quality factor. The 0.25 × ATR threshold is provisional and may be adjusted based on backtesting (for example, compare with 0.5 × ATR); it is not a calibrated or validated parameter. If the required moving-average or ATR data is unavailable, leave this factor unavailable.

These are V1 scoring candidates. Do not score a missing input as if it were observed. Volume confirmation and the overall Volume/OI component are excluded from active inputs, scoring, coverage, and confidence-band calculations. Keep commented placeholders for the 1-point HTF volume-confirmation factor and optional overall Volume/OI component in code; do not expose them as missing inputs. Reintroduce them only after a genuine data source and scoring rules are approved and implemented. Funding and news availability must still be represented explicitly if a future rule uses them.

For every factor, distinguish absent evidence from unavailable inputs. When the relevant OHLC history is available and a factor is evaluated but not confirmed, award 0 points and include its weight in the assessable denominator. When required inputs are missing or unusable, omit that factor from both numerator and partial-score denominator, list the missing input, and keep its weight in the full potential budget used to calculate coverage. Thus no-level/no-sweep results with usable OHLC are zero-point observations; missing OHLC/level data that prevents evaluation is unavailable.

Fresh-zone lifecycle is reconstructed deterministically from each zone's confirmed swing-pivot origin and the available 1H candle history. Treat a zone as activated after price moves fully away from it; the first later candle whose range overlaps it is a retest. Evaluate the current 1H interaction before counting that candle as prior mitigation, so the first retest can qualify as fresh. If the available hourly history begins after the zone's confirmed origin, prior mitigation cannot be ruled out and the factor is unavailable. Stable identity is derived from timeframe, origin timestamp, and zone bounds; no separate mutable store is required while the relevant source candles remain available. These candidate conditions and the 31-day hourly-history limit should be reviewed with backtest results.

The active 91-point confidence table allocates 16 points to HTF level quality and excludes volume/OI. Confidence bands and the risk/reward matrix use the normalized score percentage (`score_fraction × 100`), while output retains raw observed points out of assessed active weights. The analysis snapshot reports the candidate HTF factor subtotal and data coverage, but suppresses a strong/very-strong rating while an active factor is unavailable. Its current multiple-touch evidence counts clustered swing pivots, not candle-by-candle retests. Strong rejection, major swing, same-timeframe EMA/SMA confluence, and fresh-zone lifecycle are implemented as the candidate formulas above.

### 15.2 1H entry confirmation

Require at least two of the following four confirmations for a long: liquidity sweep below support, bullish structure break, bullish rejection candle, or 9 EMA reclaim / bullish 9-21 EMA structure. Reverse the conditions for a short. RSI divergence, volume expansion, a broken-level retest, or strong displacement can provide additional confirmation but are not standalone signals.

For the current deterministic candidate filter, use these observable definitions: a sweep trades beyond an identified 4H support/resistance level and closes back across it; a structure break closes beyond the latest confirmed 1H swing high/low; a rejection candle closes directionally with the rejection wick longer than its body; EMA confirmation is a close on the directional side of EMA9 with EMA9/EMA21 aligned, or a close crossing back through EMA9. Evaluate these confirmations only on fully closed hourly candles. These definitions are initial analysis heuristics and require backtesting before paper execution. An HTF location is confirmed only when the latest closed 1H candle interacts with a Daily/4H support or resistance level, a supply/demand zone, or crosses a 4H EMA9/EMA21 and closes back on the directional side. Missing levels/pivots/indicators are marked unavailable and do not count as confirmations.

### 15.3 Confidence and R:R gates

Candidate 91-point active confidence composition:

| Component | Candidate points |
|---|---:|
| Daily trend | 15 |
| 4H trend | 15 |
| HTF level quality | 16 |
| Liquidity sweep | 10 |
| 1H structure confirmation | 15 |
| EMA/SMA confluence | 10 |
| RSI/MACD confirmation | 5 |
| Market regime | 5 |
| **Total active weight** | **91** |

The initial deterministic mapping awards each trend component's full weight when that timeframe agrees with the candidate direction, and awards the market-regime component when Daily and 4H swing structures both agree. Liquidity sweep and 1H structure components use the corresponding observed confirmation; EMA/SMA confluence requires directional 9/21 EMA and 50 SMA alignment on both Daily and 4H. The 16-point HTF subtotal uses the active factor table above. These are candidate scoring rules to evaluate, not calibrated probabilities. The 5-point RSI/MACD component is scored as follows:

- RSI: 2 points when Daily and 4H RSI(14) both agree with the candidate direction (at or above 50 for bullish; below 50 for bearish). If either RSI is unavailable, exclude these 2 points from the partial denominator.
- MACD: 3 points when the Daily and 4H MACD lines are on the candidate's side of their signal lines and the 4H histogram has the candidate's sign and is expanding versus the previous completed 4H bar. If any required MACD value is unavailable, exclude these 3 points from the partial denominator.

MACD uses close-only EMA(12), EMA(26), and signal EMA(9), with values withheld until the standard slow/signal warmup is complete. These RSI/MACD thresholds are initial deterministic candidates that require backtesting; they are supporting confirmations and cannot create or override a setup on their own.

Candidate bands use normalized score percentage: below 55 no trade; 55-64 watch; 65-74 small; 75-84 normal; 85+ maximum permitted size. For a deterministic qualifying setup, report raw observed points out of currently assessable active weights (for example, `66/91`), `score_fraction` (`observed_points / assessed_points`), `score_pct` (`score_fraction × 100`), coverage against the active 91-point budget, and missing active inputs. Do not treat the fraction as a calibrated probability. Any fraction-based filter must impose a minimum coverage requirement and any required critical-input checks; set thresholds through backtesting before using the fraction for candidate filtering. Emit a confidence band only when all 91 active points are assessable; otherwise keep the band null. A no-setup result has no confidence score or band. Confidence may adjust size only within hard risk limits. The R:R eligibility matrix uses normalized score percentage: its `<60` row rejects risk review, even though scores 55-59 fall in the descriptive watch band.

R:R qualification from the source matrix remains a candidate policy, subject to this hard gate: **reject execution below 1.5R at every confidence score**. Higher confidence cannot rescue inadequate R:R. At or above 1.5R, confidence determines whether a setup is watch-only or eligible under the tested matrix. The exact candidate matrix is implemented in `risk/eligibility.py` as analysis-only risk-review classification; `eligible` never authorizes execution.

Candidate matrix after applying the hard 1.5R rejection gate:

| Confidence | 1.5R to <2R | 2R to 3R | >3R |
|---|---|---|---|
| <60 | Reject | Reject | Reject |
| 60-69 | Reject | Watch | Watch |
| 70-79 | Watch | Eligible | Eligible |
| 80-89 | Eligible | Eligible | Eligible |
| 90+ | Eligible | Eligible | Eligible |

---

## 16. Risk, execution, and position-management specification (V1 candidate)

### 16.1 Structural stop, sizing, and leverage

The market determines invalidation; the risk engine determines size. Apply this order:

1. Identify the setup and structural invalidation.
2. Place the stop beyond that invalidation, with a volatility buffer.
3. Calculate stop distance.
4. Calculate the account-risk budget and theoretical notional.
5. Apply the notional cap.
6. Check aggregate risk, concentration, leverage, and liquidation distance.
7. Execute only if every gate passes.

For a long, the structural stop belongs below strong support, the prior sweep low, or a recently respected support zone; for a short, above resistance, the prior sweep high, or a recently respected resistance zone. Do not set the stop as a fixed percentage from entry. A candidate buffer is 0.5-1.0 x 1H ATR(14), applied beyond structural invalidation and validated through backtesting. The analysis-only planner reports separate 0.5x and 1.0x scenarios; neither is selected as a default or live parameter.

Candidate sizing limits are maximum position notional of 10% of portfolio value and maximum portfolio risk of 1% per trade. The sizing relationship is `risk budget / stop-distance percentage`, then capped by maximum notional. Leverage is an execution parameter, not a reason to increase size. Check liquidation price and reject a trade if liquidation could reasonably occur before the intended stop.

These values are research parameters, not approved live defaults. They refine the older historical allocation/stop/target figures recorded elsewhere in project context; those historical figures are not to be silently combined with this specification.

The analysis-only implementation in `risk/engine.py` requires callers to supply all sizing limits and a liquidation-price estimate; it will not derive venue liquidation rules. `risk/governor.py` requires explicit daily-loss, open-position, consecutive-loss, and setup-TTL limits plus current portfolio state. Missing policy/state yields unavailable or vetoed results. Neither module is connected to scanner execution or sends orders; integration waits for a durable paper-portfolio state source and validated settings.

### 16.2 Targets and position management

- Set targets at the nearest meaningful HTF supply/resistance for longs or demand/support for shorts, with later targets at subsequent HTF levels. Reject if the nearest realistic target fails the 1.5R hard minimum; prefer 2R+ where the setup permits.
- Candidate partial exits: TP1 at 1.5R close 25%; TP2 at 2.5R close 35%; TP3 at a major HTF target close the remaining 40%. Validate these in backtesting.
- Do not automatically move the stop to breakeven after TP1; require favorable 1H structure or another predefined condition.
- Trail longs below 1H higher lows, the 21 EMA, or prior breakout structure; trail shorts above 1H lower highs, the 21 EMA, or prior breakdown structure.
- Candidate maximum: three simultaneous positions, with separate aggregate-risk and correlated-exposure caps. Correlated crypto positions must not be treated as independent by default.
- Funding is a cost/risk modifier, not a standalone direction signal. Predefine thresholds for reducing confidence/size or rejecting a setup under extremely unfavorable funding. Open-interest/price combinations may modify confidence but must not become standalone trade signals: price up/OI up may indicate new positions; price up/OI down may indicate short covering; price down/OI up may indicate new shorts; price down/OI down may indicate long liquidation or position reduction. Do not use either input until a reliable data source is available.
- Measure extreme volatility, for example ATR/price or deviation from normal volatility; apply a predefined size reduction or no-entry rule. Candidate example: volatility more than two standard deviations above normal.
- A scheduled-event filter may block new entries around defined high-impact events. Existing positions remain controlled by their preplanned stops, targets, and risk rules. No news/event source is currently configured.

### 16.3 Risk Governor and setup lifecycle

The Risk Governor has hard veto authority and cannot be overridden by the strategy or Gemini. Candidate controls include a daily realized-loss kill switch, a consecutive-loss response, a post-stop cooldown, and setup expiry:

- Daily loss: the source gives >2% as an example to stop opening trades for the rest of the day and also mentions >3% as a further disable threshold. The threshold, reset boundary, and escalation behavior are unresolved; do not implement either as a default until specified and evaluated.
- Consecutive losses: after three, pause or reduce risk; a 50% risk reduction until a fresh qualifying setup is a candidate response. Never increase risk to recover losses.
- After a stop, require a new 1H confirmation before same-direction re-entry. Treat it as a new trade with a new trade ID, confidence, stop, and R:R.
- Every setup has a TTL. Four hours is only an example; expiration must invalidate stale levels, entry zones, confidence, and R:R.

### 16.4 Paper execution and audit trail

The paper engine should model a qualified limit entry, protective stop-market behavior, reduce-only take-profit orders, cancellation/order state, slippage, partial exits, and the anti-chase rule. Never place an impulsive market entry after a missed limit. A candidate example is to invalidate/recalculate when price moves more than 1R from the planned entry before fill; freeze the exact distance rule before implementation.

Before a simulated or live execution decision, persist an audit record containing trade ID, direction, Daily/4H/1H bias, HTF levels, entry zone, setup reasons, confidence, entry, structural stop, risk, targets, R:R, size, leverage, funding/portfolio exposure when available, and EXECUTE/REJECT decision with reasons. The analysis, risk, execution, and Risk Governor responsibilities must remain separate modules/interfaces.

All numerical thresholds and execution behavior in this section are for backtesting and paper trading first. Validate them historically and in simulation before any live use. Live trading remains a separate future milestone requiring explicit authorization.
