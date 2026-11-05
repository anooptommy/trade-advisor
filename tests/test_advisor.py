import json
import asyncio
from types import SimpleNamespace
import pytest
from app.advisor import attach_plan_identity, format_advisor_message
from app.agents.gemini import ADVISOR_RESPONSE_SCHEMA, assess_market
from app.advisor_persistence import get_latest_advisor_assessment, persist_advisor_assessment
from app.scan_persistence import begin_scan_run


def sample_review(status="new_plan"):
    return {
        "status": status, "market_situation": "Daily and 4H rising; near resistance.",
        "trend": "Daily and 4H bullish; 1H consolidating.",
        "bias": "Bullish only after a confirmed resistance reclaim.",
        "momentum": "Positive but flattening on 1H.",
        "key_levels": "Resistance is near the supplied 4H swing high.",
        "data_gaps": "Volume, funding, and open interest unavailable.",
        "plan_thesis": "Wait for the stated breakout and retest.",
        "scenarios": [{
            "name": "Breakout and retest", "direction": "long",
            "trigger": "A closed 1H candle reclaims supplied resistance, then retests it.",
            "action": "Reassess for a long after confirmation.", "entry_reference": "unavailable",
            "stop_or_invalidation": "A closed 1H candle below the reclaimed level.",
            "target_reference": "Next supplied HTF resistance; price unavailable.",
            "risk_reward": "unavailable",
        }],
        "plan_invalidation": "A sustained move below supplied 4H support.",
        "wait_conditions": "Wait for a closed 1H reclaim and successful retest.",
    }


def test_gemini_uses_json_schema_and_passes_previous_assessment():
    captured = {}
    class Interactions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text=json.dumps(sample_review()))
    previous = {"status": "wait", "plan_id": "p1", "plan_active": True}
    result = assess_market({"symbol": "BTC/USD", "market_metrics": {"price": 100}},
                           previous, client=SimpleNamespace(interactions=Interactions()), model="test")
    assert result["status"] == "new_plan"
    assert captured["response_format"]["mime_type"] == "application/json"
    assert captured["response_format"]["schema"] == ADVISOR_RESPONSE_SCHEMA
    assert json.loads(captured["input"])["previous_gemini_assessment"] == previous
    assert captured["store"] is False


def test_plan_lifecycle_reuses_and_revises_identity():
    first = attach_plan_identity(sample_review(), None)
    waiting = attach_plan_identity(sample_review("wait"), first)
    revised = attach_plan_identity(sample_review("revise"), waiting)
    assert waiting["plan_id"] == first["plan_id"] and waiting["plan_revision"] == 1
    assert revised["plan_id"] == first["plan_id"] and revised["plan_revision"] == 2


def test_actionable_requires_an_active_previous_plan():
    with pytest.raises(ValueError, match="without an active prior plan"):
        attach_plan_identity(sample_review("actionable"), None)


def test_telegram_message_includes_assessment_and_plan():
    assessment = attach_plan_identity(sample_review(), None)
    message = format_advisor_message({
        "symbol": "BTC/USD", "data_status": "current",
        "snapshot": {"confidence": {"score_fraction": .72, "observed_points": 18,
                                    "assessed_points": 25, "coverage_pct": 65}},
    }, assessment)
    assert "Trend:" in message and "Bias:" in message and "Momentum:" in message
    assert "Key levels:" in message and "Breakout and retest" in message
    assert "7.2/10 (not a probability" in message
    assert "You decide whether to trade" in message


def test_assessment_history_round_trips(tmp_path):
    database = tmp_path / "advisor.sqlite3"
    config = {"quote_currency": "usd", "daily_days": 180, "hourly_days": 90,
              "assets": [{"symbol": "BTC", "coin_id": "bitcoin"}]}
    scan_id = begin_scan_run(config, database_path=database)
    metrics = {"symbol": "BTC/USD", "market_metrics": {"price": 100}}
    assessment = attach_plan_identity(sample_review(), None)
    history_id = persist_advisor_assessment(
        scan_id, "bitcoin", metrics=metrics, previous_assessment=None,
        assessment=assessment, database_path=database)
    saved = get_latest_advisor_assessment("bitcoin", database_path=database)
    assert saved["history_id"] == history_id
    assert saved["plan_id"] == assessment["plan_id"] and saved["plan_active"] is True


def test_run_once_assesses_non_candidate_and_saves_before_telegram(monkeypatch):
    from app import hourly_recommender as recommender
    snapshot = {"setup": "no_setup", "confidence": {"score_fraction": .7}}
    asset = {"symbol": "BTC/USD", "coin_id": "bitcoin", "status": "analyzed",
             "data_status": "current", "snapshot": snapshot}
    events, received = [], []
    async def fake_scan(_config):
        return [asset]
    async def sender(message):
        events.append("send")
        assert "Trend:" in message and "no qualifying" not in message
        return 4
    def advisor(metrics, previous):
        received.append((metrics, previous))
        return sample_review()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr(recommender, "load_watchlist", lambda: {"assets": [], "quote_currency": "usd"})
    monkeypatch.setattr(recommender, "begin_scan_run", lambda *_a, **_k: 10)
    monkeypatch.setattr(recommender, "scan_watchlist", fake_scan)
    monkeypatch.setattr(recommender, "persist_scan_results", lambda *_a: None)
    monkeypatch.setattr(recommender, "get_latest_advisor_assessment", lambda _coin: None)
    def persist(*_args, **_kwargs):
        events.append("persist")
        return 12
    monkeypatch.setattr(recommender, "persist_advisor_assessment", persist)
    result = asyncio.run(recommender.run_once(sender=sender, advisor=advisor))
    assert result["sent"] == 1 and result["plan_status_counts"] == {"new_plan": 1}
    assert received[0][0]["market_metrics"] == snapshot and received[0][1] is None
    assert events == ["persist", "send"]
