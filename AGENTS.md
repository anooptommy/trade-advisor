# Trade Advisor — project instructions

## Purpose
This independent copy scans the configured crypto watchlist, passes every successfully analyzed asset's full snapshot to Gemini, and sends an evidence-based market overview plus conditional scenario plan to Telegram. Later runs pass that asset's saved prior assessment so Gemini can classify the plan as actionable, waiting, revised, or unsupported.

## Architecture
CoinGecko MCP → normalized UTC OHLC and cache → indicators → market structure/rule evidence → full snapshot → Gemini structured assessment and scenarios → append-only SQLite history → Telegram.

Keep ingestion, indicators, structure/rules, Gemini, persistence, and delivery separate. Preserve sequential MCP requests, UTC metadata, complete-bucket resampling, and missing-input markers. Never fabricate market data.

## Gemini contract
Gemini receives the full snapshot, freshness, and existing analysis-only risk context for every successful asset scan; deterministic candidate qualification is not a gate. It also receives the previous assessment and plan. Require JSON-schema-constrained responses covering situation, trend, bias, momentum, HTF levels and their nearest distances, gaps, conditional scenarios, invalidation, wait conditions, and status. The deterministic `no_setup` result means current confirmation is absent; it does not prevent a non-actionable conditional watch plan. Unaligned HTF context, unconfirmed location, partial level-quality rating, unavailable volume, and `not_applicable` candidate risk context must be explained, not used alone to suppress a watch plan. Numeric levels must be supported by supplied metrics or marked unavailable. Stale data cannot be presented as actionable. Confidence is a score, never a probability.

Persist each successful assessment as immutable history before Telegram delivery. Plan IDs stay stable while waiting/actionable; a supported revision increments the revision.

## Safety
- Analysis and conditional planning only; no exchange connection or order placement.
- No execution approval, portfolio risk evaluation, sizing, or position management.
- The user decides whether to trade.
- Keep credentials in environment variables or local .env; never log secrets.
- Preserve reproducibility and tests.

## Workflow
Inspect before changes. For substantial work, update PROJECT_CONTEXT.md and IMPLEMENTATION_PLAN.md, test non-trivial behavior, and report changed files and limitations.
