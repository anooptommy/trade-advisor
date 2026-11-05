# Trade Advisor

An hourly crypto market advisor. It reuses the CoinGecko MCP OHLC adapter and deterministic scanner, then sends every successfully analyzed asset's complete metrics to Gemini for an evidence-based market read and conditional scenario plan. On later runs Gemini receives its latest saved assessment for that asset and reports whether the plan is actionable, should wait, needs revision, or is no longer supported.

## Run

Set GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID in a local .env or the process environment. Install dependencies with python -m pip install -r requirements.txt. Run one cycle with python -m app.hourly_recommender --once; omit --once for the UTC hourly loop.

The full scanner snapshot, data freshness, and existing analysis-only risk context are passed to Gemini. Each successful response is saved to data/trading_agent.sqlite3 before Telegram delivery. The advisor_assessments table is append-only and stores metrics, prior assessment, plan identity/revision, and response. A delivery failure does not erase history.

Each watchlist asset now runs end-to-end in order: fetch and analyze its market data, load its previous plan, call Gemini, save the response, send Telegram, then continue to the next asset. Gemini calls pause 30 seconds by default; set `GEMINI_REQUEST_DELAY_SECONDS` to tune that spacing. A Gemini 429 triggers a 60-second cooldown by default (`GEMINI_RATE_LIMIT_COOLDOWN_SECONDS`). The SDK retry policy is bounded and respects `Retry-After` when supplied. These settings smooth request bursts; they cannot override project/model quota limits.

The included GitHub Actions workflow runs hourly and restores the data directory through the Actions cache so plans can be followed across runs. Cache retention is best-effort; for a durable local audit trail, run the advisor where the SQLite data directory remains on persistent storage.

Telegram messages cover situation, trend, bias, momentum, HTF levels, data gaps, confidence context, conditional scenarios, invalidation, and wait conditions. Numeric levels are included only when supported by supplied evidence; otherwise they are marked unavailable.

This is analysis only. There is no exchange connection, order placement, position sizing, portfolio risk evaluation, or position management. You make all trading decisions. Confidence is not a probability.

CoinGecko OHLC has no volume. Volume, funding, and open interest remain unavailable unless separately sourced. See PROJECT_CONTEXT.md for data-source limitations.

Run tests with python -m pytest tests -q.
