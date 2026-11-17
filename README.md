# Trade Advisor

An hourly crypto market advisor. It reuses the CoinGecko MCP OHLC adapter and deterministic scanner, then asks Gemini for conditional plans for every watchlist asset once per local day when no active plan exists. Each scenario stores structured price assumptions and invalidation conditions; later runs compare them against completed provider candles and the existing deterministic setup rules.

## Run

Set GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID in a local .env or the process environment. Install runtime and chart dependencies with `python -m pip install -r requirements-visualization.txt`. GitHub Actions installs the same file. Run one cycle with `python -m app.hourly_recommender --once`; omit `--once` for the UTC hourly loop.

The full scanner snapshot, data freshness, and existing analysis-only risk context are passed to Gemini. Each successful response is saved to data/trading_agent.sqlite3 before Telegram delivery. The advisor_assessments table is append-only and stores metrics, prior assessment, plan identity/revision, and response. A delivery failure does not erase history.

Each watchlist asset runs end-to-end sequentially. Gemini creates missing plans during the first daily scan and is called again only to migrate an older plan or revise a plan after its invalidation conditions are met. Ordinary hourly assumption checks are local, and a threshold requires both every scenario assumption and the matching deterministic setup to be confirmed on closed candles. Calls pause 30 seconds by default; set `GEMINI_REQUEST_DELAY_SECONDS` to tune that spacing. A Gemini 429 triggers a 60-second cooldown by default (`GEMINI_RATE_LIMIT_COOLDOWN_SECONDS`). The SDK retry policy is bounded and respects `Retry-After` when supplied. Cache-only CoinGecko assets do not incur the provider spacing delay. These settings smooth request bursts; they cannot override project/model quota limits.

The included GitHub Actions workflow runs hourly. The current plan per token, machine-checkable price assumptions, assumption-state transitions, plan revisions, and the Kolkata-local daily digest date live in `runtime/trade_plans.json`. Actions commits this small file to the checked-out branch when its contents change; this is more reliable for plan continuity than relying on the expiring Actions cache. The `GITHUB_TOKEN` needs repository contents write permission, and branch protection must permit the Actions bot to push. The SQLite scan/history database remains in the Actions cache for convenience, but that cache is best-effort and is not the source of truth for active plans.

On the first successful run each day (Asia/Kolkata), Telegram receives one short summary listing the plan/assumptions for every token. Later runs check only completed OHLC candles against each plan's structured assumptions. A token alert is sent when all assumptions in a directional scenario are met; invalidation conditions prompt Gemini to revise that plan. If no token crosses its threshold, one concise all-token status message is sent. Actionability checks are local and do not call Gemini every hour. The attached PNG shows real OHLC candles, available moving averages, existing HTF levels, and the current price action to watch.

This is analysis only. There is no exchange connection, order placement, position sizing, portfolio risk evaluation, or position management. You make all trading decisions. Confidence is not a probability.

CoinGecko OHLC has no volume. Volume, funding, and open interest remain unavailable unless separately sourced. See PROJECT_CONTEXT.md for data-source limitations.

Run tests with python -m pytest tests -q.
