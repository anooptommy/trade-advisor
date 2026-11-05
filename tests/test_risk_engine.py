from risk.engine import calculate_candidate_position_size
from risk.governor import evaluate_risk_governor


def _sizing(**overrides):
    values = {
        "side": "long",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "account_equity": 1000.0,
        "risk_fraction": 0.01,
        "max_position_notional_fraction": 0.2,
        "max_leverage": 3.0,
        "liquidation_price": 95.0,
    }
    values.update(overrides)
    return calculate_candidate_position_size(**values)


def test_position_size_obeys_risk_budget_and_explicit_notional_cap():
    result = _sizing()

    assert result["status"] == "assessed"
    assert result["quantity"] == 2.0
    assert result["notional"] == 200.0
    assert result["effective_risk_amount"] == 2.0
    assert result["effective_risk_amount"] <= result["risk_budget"]
    assert result["execution_authorized"] is False


def test_position_size_rejects_stop_beyond_supplied_liquidation_price():
    result = _sizing(liquidation_price=99.5)

    assert result["status"] == "rejected"
    assert result["reason"] == "stop_must_be_reached_before_liquidation"


def test_position_size_requires_a_liquidation_input_and_no_limits_are_defaulted():
    result = _sizing(liquidation_price=None)

    assert result["status"] == "unavailable"
    assert result["reason"] == "finite_account_price_limit_and_liquidation_inputs_required"


def test_position_size_applies_leverage_cap_and_rejects_wrong_side_stop():
    leveraged = _sizing(
        stop_price=90.0,
        risk_fraction=0.5,
        max_position_notional_fraction=10.0,
        max_leverage=2.0,
        liquidation_price=80.0,
    )
    wrong_stop = _sizing(stop_price=101.0)

    assert leveraged["quantity"] == 20.0
    assert leveraged["effective_leverage"] == 2.0
    assert wrong_stop["status"] == "rejected"
    assert wrong_stop["reason"] == "stop_must_be_beyond_entry_in_the_loss_direction"


def _governor_inputs():
    return {
        "candidate_eligibility": {"status": "assessed", "matrix_result": "eligible"},
        "position_size": {"status": "assessed"},
        "portfolio_state": {
            "account_equity": 1000.0,
            "daily_realized_pnl": -5.0,
            "open_position_count": 1,
            "consecutive_losses": 1,
        },
        "controls": {
            "max_daily_loss_fraction": 0.02,
            "max_open_positions": 2,
            "max_consecutive_losses": 3,
            "setup_ttl_minutes": 60,
        },
        "setup_age_minutes": 20,
    }


def test_risk_governor_allows_only_later_review_when_configured_gates_pass():
    result = evaluate_risk_governor(**_governor_inputs())

    assert result["status"] == "approved_for_later_risk_review"
    assert result["approved_for_later_risk_review"] is True
    assert result["execution_authorized"] is False


def test_risk_governor_vetoes_stale_data_and_daily_loss_limit():
    stale = evaluate_risk_governor(**_governor_inputs(), market_data_stale=True)
    inputs = _governor_inputs()
    inputs["portfolio_state"]["daily_realized_pnl"] = -20.0
    loss_limit = evaluate_risk_governor(**inputs)

    assert stale["veto_reasons"] == ["stale_market_data"]
    assert "daily_loss_limit_reached" in loss_limit["veto_reasons"]
    assert loss_limit["approved_for_later_risk_review"] is False


def test_risk_governor_enforces_position_loss_streak_ttl_and_cooldown_gates():
    position_inputs = _governor_inputs()
    position_inputs["portfolio_state"]["open_position_count"] = 2
    loss_inputs = _governor_inputs()
    loss_inputs["portfolio_state"]["consecutive_losses"] = 3
    ttl_inputs = _governor_inputs()
    ttl_inputs["setup_age_minutes"] = 61
    cooldown_inputs = _governor_inputs()

    assert "maximum_open_positions_reached" in evaluate_risk_governor(**position_inputs)["veto_reasons"]
    assert "maximum_consecutive_losses_reached" in evaluate_risk_governor(**loss_inputs)["veto_reasons"]
    assert "setup_expired" in evaluate_risk_governor(**ttl_inputs)["veto_reasons"]
    assert "same_direction_cooldown_active" in evaluate_risk_governor(
        **cooldown_inputs, same_direction_cooldown_active=True
    )["veto_reasons"]


def test_risk_governor_stays_unavailable_without_explicit_unresolved_policy():
    inputs = _governor_inputs()
    del inputs["controls"]["max_daily_loss_fraction"]
    result = evaluate_risk_governor(**inputs)

    assert result["status"] == "unavailable"
    assert result["reason"] == "explicit_portfolio_state_and_governor_limits_required"
