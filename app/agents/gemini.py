"""Structured Gemini market assessment and conditional plan generation."""
from __future__ import annotations
import json
import logging
import math
import ssl

logger = logging.getLogger(__name__)
ADVISOR_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "status": {"type": "STRING", "enum": ["new_plan", "actionable", "wait", "revise", "no_plan"]},
        "market_situation": {"type": "STRING"}, "trend": {"type": "STRING"},
        "bias": {"type": "STRING"}, "momentum": {"type": "STRING"},
        "key_levels": {"type": "STRING"}, "data_gaps": {"type": "STRING"},
        "plan_thesis": {"type": "STRING"},
        "scenarios": {"type": "ARRAY", "maxItems": 3, "items": {
            "type": "OBJECT", "properties": {
                "name": {"type": "STRING"},
                "direction": {"type": "STRING", "enum": ["long", "short", "stand_aside"]},
                "trigger": {"type": "STRING"}, "action": {"type": "STRING"},
                "entry_reference": {"type": "STRING"}, "stop_or_invalidation": {"type": "STRING"},
                "target_reference": {"type": "STRING"}, "risk_reward": {"type": "STRING"},
                "assumptions": {"type": "ARRAY", "minItems": 1, "items": {
                    "type": "OBJECT", "properties": {
                        "timeframe": {"type": "STRING", "enum": ["1d", "4h", "1h"]},
                        "operator": {"type": "STRING", "enum": ["above", "below", "touches", "crosses_above", "crosses_below"]},
                        "reference_price": {"type": "NUMBER"},
                        "description": {"type": "STRING"},
                    },
                    "required": ["timeframe", "operator", "reference_price", "description"],
                }},
                "invalidation_conditions": {"type": "ARRAY", "items": {
                    "type": "OBJECT", "properties": {
                        "timeframe": {"type": "STRING", "enum": ["1d", "4h", "1h"]},
                        "operator": {"type": "STRING", "enum": ["above", "below", "touches", "crosses_above", "crosses_below"]},
                        "reference_price": {"type": "NUMBER"},
                        "description": {"type": "STRING"},
                    },
                    "required": ["timeframe", "operator", "reference_price", "description"],
                }},
            },
            "required": ["name", "direction", "trigger", "action", "entry_reference",
                         "stop_or_invalidation", "target_reference", "risk_reward", "assumptions",
                         "invalidation_conditions"],
        }},
        "plan_invalidation": {"type": "STRING"}, "wait_conditions": {"type": "STRING"},
    },
    "required": ["status", "market_situation", "trend", "bias", "momentum", "key_levels",
                 "data_gaps", "plan_thesis", "scenarios", "plan_invalidation", "wait_conditions"],
}


def assess_market(metrics: dict, previous_assessment: dict | None = None, *, client=None, model: str | None = None) -> dict:
    """Send a complete market snapshot and prior plan to Gemini."""
    symbol = metrics.get("symbol", "unknown")
    if client is None:
        from app.config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is required for market assessments.")
        from google import genai
        # The packaged Python runtime may use a private CA store (or a host
        # supplied SSLKEYLOGFILE that is not writable). Build a standard
        # verified context from the OS trust store without disabling TLS checks.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options={
                "client_args": {"verify": context},
                # Bound SDK retries. It honors Retry-After when provided, but
                # avoids a long retry loop for every asset in an hourly scan.
                "retry_options": {
                    "attempts": 1,
                    "initial_delay": 1.0,
                    "max_delay": 8.0,
                    "exp_base": 2.0,
                    "jitter": 0.5,
                },
            },
        )
    if model is None:
        from app.config import GEMINI_MODEL
        model = GEMINI_MODEL
    instruction = (
        "Analyze every supplied scanner metric: Daily/4H/1H trend and structure, momentum, indicator "
        "evidence, HTF location and levels, confidence coverage, data freshness, and risk context. Use only "
        "facts in the JSON. Never invent prices, levels, indicators, volume, funding, open interest, or news. "
        "Explain overall situation, trend, bias, momentum, support/resistance interaction, HTF proximity, "
        "and missing/stale data. Provide conditional scenarios, never an order. Numeric entry/stop/target/R:R "
        "must be directly supported by supplied metrics; otherwise say unavailable. Do not select between "
        "unvalidated candidate risk scenarios. Never size positions, assess portfolio exposure, connect to an "
        "exchange, or claim a probability of profit. The user decides whether to trade. Stale data cannot "
        "support an actionable status. This is a conditional watch-plan service: no_setup, unaligned HTF trend/"
        "structure, unconfirmed HTF location, unavailable volume, unrated/partial level quality, and a "
        "not_applicable candidate risk plan describe current limitations; none alone prohibits a new "
        "non-actionable watch plan. Explain which deterministic gates failed and state the observable "
        "conditions that would need to change. If HTF direction is neutral, scenarios may wait for Daily/4H "
        "trend and structure alignment before a closed-1H trigger. Missing volume must not be treated as a "
        "negative volume signal or invented. Candidate risk not_applicable means no deterministic setup, not "
        "that every conditional scenario is impossible. Never mark an untriggered watch plan actionable. "
        "Use only supplied levels/indicators for price triggers and invalidation; say unavailable when they "
        "do not support one. no_plan is appropriate only when stale/empty/contradictory data prevents even a "
        "useful conditional scenario. When there is no active plan choose new_plan or no_plan, even if an "
        "earlier assessment exists but ended. With an active previous plan, choose "
        "actionable only if its trigger is met, wait if valid but untriggered, "
        "revise if assumptions failed or a different plan is justified, and no_plan if nothing is supported. "
        "For each scenario include at least one machine-checkable assumption and zero or more invalidation_conditions. "
        "Each condition must use an exact timeframe (1d/4h/1h), a closed-candle OHLC operator, a numeric reference_price "
        "copied from supplied metrics (never invent one), and a concise description. Assumptions are conjunctive: all "
        "must be met for that scenario to reach its execution threshold; alternative scenarios are independent. "
        "Use only closed candles for conditions. Invalidation conditions describe observable price moves that mean the "
        "scenario should be revised. If no supported numeric reference exists, say no_plan rather than inventing one. "
        "When status is no_plan, return an empty scenarios array. For wait/actionable restate the plan. "
        "For revise include replacement scenarios only when supported. "
        "Be concise and evidence-based."
    )
    payload = json.dumps(
        {"current_metrics": metrics, "previous_gemini_assessment": previous_assessment},
        ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    )
    try:
        interaction = client.interactions.create(
            model=model, system_instruction=instruction, input=payload,
            response_format={"type": "text", "mime_type": "application/json",
                             "schema": ADVISOR_RESPONSE_SCHEMA},
            generation_config={"thinking_level": "low"},
            store=False,
        )
        usage = getattr(interaction, "usage", None)
        if usage is not None:
            logger.info(
                "Gemini token usage symbol=%s model=%s input=%s output=%s thought=%s",
                symbol, model,
                getattr(usage, "total_input_tokens", None),
                getattr(usage, "total_output_tokens", None),
                getattr(usage, "total_thought_tokens", None),
            )
        if not interaction.output_text:
            raise RuntimeError("Gemini returned an empty structured assessment.")
        result = json.loads(interaction.output_text)
        _validate_assessment(result)
    except Exception:
        logger.exception("Gemini advisor assessment failed symbol=%s model=%s", symbol, model)
        raise
    return result


def _validate_assessment(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("Gemini assessment must be a JSON object.")
    required = set(ADVISOR_RESPONSE_SCHEMA["required"])
    missing = required.difference(value)
    if missing:
        raise ValueError(f"Gemini assessment is missing fields: {sorted(missing)}")
    if value["status"] not in {"new_plan", "actionable", "wait", "revise", "no_plan"}:
        raise ValueError("Gemini assessment has an unsupported plan status.")
    if not isinstance(value["scenarios"], list) or len(value["scenarios"]) > 3:
        raise ValueError("Gemini scenarios must be a list of at most three items.")
    if value["status"] == "no_plan" and value["scenarios"]:
        logger.warning("Discarding scenarios returned with no_plan status")
        value["scenarios"] = []
    for field in required.difference({"status", "scenarios"}):
        if not isinstance(value[field], str):
            raise ValueError(f"Gemini assessment field {field!r} must be text.")
    fields = {"name", "direction", "trigger", "action", "entry_reference",
              "stop_or_invalidation", "target_reference", "risk_reward", "assumptions",
              "invalidation_conditions"}
    for scenario in value["scenarios"]:
        if not isinstance(scenario, dict) or not fields.issubset(scenario):
            raise ValueError("Each Gemini scenario must include all required fields.")
        if scenario["direction"] not in {"long", "short", "stand_aside"}:
            raise ValueError("Gemini scenario direction is unsupported.")
        if not isinstance(scenario["assumptions"], list) or not scenario["assumptions"]:
            raise ValueError("Each trade-plan scenario needs machine-checkable assumptions.")
        if not isinstance(scenario["invalidation_conditions"], list):
            raise ValueError("Scenario invalidation_conditions must be a list.")
        for condition in scenario["assumptions"] + scenario["invalidation_conditions"]:
            if not isinstance(condition, dict) or not {"timeframe", "operator", "reference_price", "description"}.issubset(condition):
                raise ValueError("Plan conditions need timeframe, operator, numeric reference_price, and description.")
            if condition["timeframe"] not in {"1d", "4h", "1h"}:
                raise ValueError("Plan condition timeframe is unsupported.")
            if condition["operator"] not in {"above", "below", "touches", "crosses_above", "crosses_below"}:
                raise ValueError("Plan condition operator is unsupported.")
            if not isinstance(condition["reference_price"], (int, float)) or not math.isfinite(float(condition["reference_price"])):
                raise ValueError("Plan condition reference_price must be finite numeric data.")


def review_candidate(snapshot: dict, *, client=None, model: str | None = None) -> str:
    """Keep the legacy console-only reviewer available to app.main --scan-only."""
    if client is None:
        from app.config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is required for a candidate review.")
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
    if model is None:
        from app.config import GEMINI_MODEL
        model = GEMINI_MODEL
    instruction = (
        "Review this deterministic analysis snapshot using only supplied facts. Do not invent market data. "
        "Point out agreement, conflicts, missing evidence, and invalidation. Confidence score_fraction and "
        "coverage_pct are not a calibrated probability. Do not issue a trade or order."
    )
    response = client.interactions.create(
        model=model, system_instruction=instruction,
        input=json.dumps(snapshot, allow_nan=False), store=False,
    )
    return response.output_text or "Gemini returned no text."
