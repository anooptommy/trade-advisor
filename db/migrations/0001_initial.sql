CREATE TABLE data_sources (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('market_data', 'paper_trading', 'internal')),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE instruments (
    id INTEGER PRIMARY KEY,
    canonical_symbol TEXT NOT NULL,
    display_name TEXT NOT NULL,
    base_currency TEXT,
    asset_type TEXT NOT NULL DEFAULT 'crypto',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_instruments_symbol ON instruments(canonical_symbol);

CREATE TABLE instrument_source_mappings (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    data_source_id INTEGER NOT NULL REFERENCES data_sources(id) ON DELETE RESTRICT,
    external_asset_id TEXT NOT NULL,
    external_symbol TEXT,
    quote_currency TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (data_source_id, external_asset_id)
);

CREATE TABLE market_data_runs (
    id INTEGER PRIMARY KEY,
    data_source_id INTEGER NOT NULL REFERENCES data_sources(id) ON DELETE RESTRICT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    timeframe TEXT NOT NULL,
    requested_start_ms INTEGER,
    requested_end_ms INTEGER,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'stale_fallback')),
    stale_cache_used INTEGER NOT NULL DEFAULT 0 CHECK (stale_cache_used IN (0, 1)),
    rows_received INTEGER NOT NULL DEFAULT 0 CHECK (rows_received >= 0),
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_market_data_runs_instrument_time ON market_data_runs(instrument_id, started_at_utc);

CREATE TABLE candles (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    data_source_id INTEGER NOT NULL REFERENCES data_sources(id) ON DELETE RESTRICT,
    first_ingestion_run_id INTEGER REFERENCES market_data_runs(id) ON DELETE SET NULL,
    last_ingestion_run_id INTEGER REFERENCES market_data_runs(id) ON DELETE SET NULL,
    timeframe TEXT NOT NULL,
    open_time_ms INTEGER NOT NULL,
    close_time_ms INTEGER NOT NULL,
    open_price TEXT NOT NULL,
    high_price TEXT NOT NULL,
    low_price TEXT NOT NULL,
    close_price TEXT NOT NULL,
    volume TEXT,
    volume_available INTEGER NOT NULL DEFAULT 0 CHECK (volume_available IN (0, 1)),
    is_closed INTEGER NOT NULL CHECK (is_closed IN (0, 1)),
    origin_kind TEXT NOT NULL DEFAULT 'provider' CHECK (origin_kind IN ('provider', 'derived')),
    derivation_version TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (close_time_ms > open_time_ms),
    CHECK (origin_kind != 'derived' OR derivation_version IS NOT NULL),
    UNIQUE (instrument_id, data_source_id, timeframe, open_time_ms)
);
CREATE INDEX idx_candles_time_lookup ON candles(instrument_id, timeframe, open_time_ms);
CREATE INDEX idx_candles_ingestion ON candles(last_ingestion_run_id);

CREATE TABLE price_observations (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    data_source_id INTEGER NOT NULL REFERENCES data_sources(id) ON DELETE RESTRICT,
    observed_at_utc TEXT NOT NULL,
    price TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    price_kind TEXT NOT NULL CHECK (price_kind IN ('last_trade', 'mark', 'index', 'mid', 'candle_close')),
    candle_id INTEGER REFERENCES candles(id) ON DELETE RESTRICT,
    ingestion_run_id INTEGER REFERENCES market_data_runs(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK ((price_kind = 'candle_close' AND candle_id IS NOT NULL) OR price_kind != 'candle_close'),
    UNIQUE (instrument_id, data_source_id, observed_at_utc, price_kind, quote_currency)
);
CREATE INDEX idx_price_observations_latest ON price_observations(instrument_id, quote_currency, observed_at_utc DESC);

CREATE TABLE strategy_versions (
    id INTEGER PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    version TEXT NOT NULL,
    code_revision TEXT,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (strategy_key, version, config_hash)
);

CREATE TABLE scan_runs (
    id INTEGER PRIMARY KEY,
    strategy_version_id INTEGER REFERENCES strategy_versions(id) ON DELETE RESTRICT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    app_revision TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT
);
CREATE INDEX idx_scan_runs_started ON scan_runs(started_at_utc DESC);

CREATE TABLE scan_results (
    id INTEGER PRIMARY KEY,
    scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE RESTRICT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('analyzed', 'failed', 'skipped')),
    data_status TEXT CHECK (data_status IN ('current', 'stale_cache_fallback', 'unavailable')),
    latest_daily_candle_ms INTEGER,
    latest_four_hour_candle_ms INTEGER,
    latest_hourly_candle_ms INTEGER,
    setup TEXT,
    direction TEXT CHECK (direction IN ('long', 'short') OR direction IS NULL),
    confidence_observed_points INTEGER,
    confidence_assessed_points INTEGER,
    confidence_total_points INTEGER,
    confidence_fraction REAL,
    confidence_pct REAL,
    confidence_coverage_pct REAL,
    confidence_band TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (scan_run_id, instrument_id)
);
CREATE INDEX idx_scan_results_instrument_created ON scan_results(instrument_id, created_at_utc DESC);

CREATE TABLE trade_suggestions (
    id INTEGER PRIMARY KEY,
    scan_result_id INTEGER NOT NULL REFERENCES scan_results(id) ON DELETE RESTRICT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    setup_candle_time_ms INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('suggested', 'risk_rejected', 'paper_approved', 'expired', 'closed')),
    entry_reference_price TEXT,
    confidence_snapshot_json TEXT NOT NULL,
    setup_snapshot_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at_utc TEXT,
    UNIQUE (scan_result_id, direction, setup_candle_time_ms)
);
CREATE INDEX idx_trade_suggestions_instrument_created ON trade_suggestions(instrument_id, created_at_utc DESC);

CREATE TABLE agent_reviews (
    id INTEGER PRIMARY KEY,
    trade_suggestion_id INTEGER NOT NULL REFERENCES trade_suggestions(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    input_snapshot_json TEXT NOT NULL,
    response_text TEXT,
    error_message TEXT,
    reviewed_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE risk_policy_versions (
    id INTEGER PRIMARY KEY,
    policy_key TEXT NOT NULL,
    version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    approved_for_paper INTEGER NOT NULL DEFAULT 0 CHECK (approved_for_paper IN (0, 1)),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (policy_key, version, config_hash)
);

CREATE TABLE risk_assessments (
    id INTEGER PRIMARY KEY,
    trade_suggestion_id INTEGER NOT NULL REFERENCES trade_suggestions(id) ON DELETE RESTRICT,
    risk_policy_version_id INTEGER REFERENCES risk_policy_versions(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('assessed', 'unavailable', 'vetoed')),
    input_snapshot_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    assessed_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE risk_scenarios (
    id INTEGER PRIMARY KEY,
    risk_assessment_id INTEGER NOT NULL REFERENCES risk_assessments(id) ON DELETE RESTRICT,
    scenario_key TEXT NOT NULL,
    atr_buffer_multiple REAL,
    entry_reference_price TEXT,
    stop_price TEXT,
    target_price TEXT,
    gross_risk_reward REAL,
    position_size TEXT,
    notional TEXT,
    risk_amount TEXT,
    liquidation_price TEXT,
    status TEXT NOT NULL CHECK (status IN ('assessed', 'unavailable', 'vetoed')),
    result_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (risk_assessment_id, scenario_key)
);

CREATE TABLE paper_accounts (
    id INTEGER PRIMARY KEY,
    account_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'closed')),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at_utc TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE trade_decisions (
    id INTEGER PRIMARY KEY,
    trade_suggestion_id INTEGER NOT NULL REFERENCES trade_suggestions(id) ON DELETE RESTRICT,
    risk_assessment_id INTEGER REFERENCES risk_assessments(id) ON DELETE RESTRICT,
    risk_scenario_id INTEGER REFERENCES risk_scenarios(id) ON DELETE RESTRICT,
    paper_account_id INTEGER REFERENCES paper_accounts(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('approve_paper_order', 'reject', 'defer')),
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    input_snapshot_json TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE positions (
    id INTEGER PRIMARY KEY,
    paper_account_id INTEGER NOT NULL REFERENCES paper_accounts(id) ON DELETE RESTRICT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    trade_suggestion_id INTEGER REFERENCES trade_suggestions(id) ON DELETE RESTRICT,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    status TEXT NOT NULL CHECK (status IN ('open', 'partially_closed', 'closed')),
    opened_at_utc TEXT NOT NULL,
    closed_at_utc TEXT,
    quantity TEXT NOT NULL,
    average_entry_price TEXT NOT NULL,
    realized_pnl TEXT NOT NULL DEFAULT '0',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK ((status = 'closed' AND closed_at_utc IS NOT NULL) OR (status != 'closed' AND closed_at_utc IS NULL))
);
CREATE INDEX idx_positions_account_status ON positions(paper_account_id, status);
CREATE INDEX idx_positions_instrument_status ON positions(instrument_id, status);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    paper_account_id INTEGER NOT NULL REFERENCES paper_accounts(id) ON DELETE RESTRICT,
    trade_suggestion_id INTEGER REFERENCES trade_suggestions(id) ON DELETE RESTRICT,
    trade_decision_id INTEGER NOT NULL REFERENCES trade_decisions(id) ON DELETE RESTRICT,
    position_id INTEGER REFERENCES positions(id) ON DELETE RESTRICT,
    client_order_id TEXT NOT NULL UNIQUE,
    execution_mode TEXT NOT NULL DEFAULT 'paper' CHECK (execution_mode = 'paper'),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL CHECK (order_type IN ('market', 'limit', 'stop_market', 'take_profit_market')),
    time_in_force TEXT,
    quantity TEXT NOT NULL,
    limit_price TEXT,
    trigger_price TEXT,
    reduce_only INTEGER NOT NULL DEFAULT 0 CHECK (reduce_only IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('created', 'submitted', 'partially_filled', 'filled', 'cancelled', 'rejected', 'expired')),
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_orders_account_status ON orders(paper_account_id, status, created_at_utc);

CREATE TABLE order_events (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT,
    event_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_order_events_order_time ON order_events(order_id, event_at_utc);

CREATE TABLE fills (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    position_id INTEGER REFERENCES positions(id) ON DELETE RESTRICT,
    external_fill_id TEXT,
    filled_at_utc TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    fee_amount TEXT NOT NULL DEFAULT '0',
    fee_currency TEXT,
    slippage_amount TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (order_id, external_fill_id)
);
CREATE INDEX idx_fills_position_time ON fills(position_id, filled_at_utc);

CREATE TABLE portfolio_ledger (
    id INTEGER PRIMARY KEY,
    paper_account_id INTEGER NOT NULL REFERENCES paper_accounts(id) ON DELETE RESTRICT,
    fill_id INTEGER REFERENCES fills(id) ON DELETE RESTRICT,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('initial_funding', 'realized_pnl', 'fee', 'funding_payment', 'adjustment')),
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    effective_at_utc TEXT NOT NULL,
    description TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_portfolio_ledger_account_time ON portfolio_ledger(paper_account_id, effective_at_utc);

CREATE TABLE portfolio_snapshots (
    id INTEGER PRIMARY KEY,
    paper_account_id INTEGER NOT NULL REFERENCES paper_accounts(id) ON DELETE RESTRICT,
    data_source_id INTEGER REFERENCES data_sources(id) ON DELETE RESTRICT,
    captured_at_utc TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    cash_balance TEXT,
    available_balance TEXT,
    equity TEXT,
    realized_pnl TEXT,
    unrealized_pnl TEXT,
    margin_used TEXT,
    gross_notional TEXT,
    provider_payload_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (paper_account_id, captured_at_utc)
);
CREATE INDEX idx_portfolio_snapshots_latest ON portfolio_snapshots(paper_account_id, captured_at_utc DESC);

CREATE TABLE portfolio_marks (
    id INTEGER PRIMARY KEY,
    portfolio_snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshots(id) ON DELETE RESTRICT,
    position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE RESTRICT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    candle_id INTEGER REFERENCES candles(id) ON DELETE RESTRICT,
    price_observation_id INTEGER REFERENCES price_observations(id) ON DELETE RESTRICT,
    quantity TEXT NOT NULL,
    mark_price TEXT NOT NULL,
    market_value TEXT NOT NULL,
    unrealized_pnl TEXT,
    valuation_at_utc TEXT NOT NULL,
    CHECK ((candle_id IS NOT NULL) != (price_observation_id IS NOT NULL)),
    UNIQUE (portfolio_snapshot_id, position_id)
);
