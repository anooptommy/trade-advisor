from risk.planning import build_candidate_risk_plan


def _confidence(score=70, *, complete=True):
    return {
        "status": "complete_candidate_score" if complete else "partial_candidate_score_missing_inputs",
        "score": score,
        "score_fraction": score / 100,
        "score_pct": score,
        "assessed_points": 91 if complete else 90,
        "total_points": 91,
        "band": "small" if complete else None,
    }


def _long_snapshot():
    return {
        "setup": "potential_long",
        "price": 100.0,
        "timestamp": "2026-10-07T10:00:00+00:00",
        "one_hour": {"atr14": 2.0},
        "confidence": _confidence(),
        "daily_structure": {
            "support": [{"price": 95.0, "touches": 2}],
            "demand": [{"low": 96.0, "high": 98.0, "touches": 1}],
            "resistance": [{"price": 120.0, "touches": 1}],
            "supply": [{"low": 110.0, "high": 113.0, "touches": 1}],
        },
        "four_hour_structure": {
            "support": [{"price": 97.0, "touches": 1}],
            "demand": [{"low": 96.5, "high": 98.5, "touches": 1}],
            "resistance": [{"price": 108.0, "touches": 1}],
            "supply": [{"low": 107.0, "high": 109.0, "touches": 1}],
        },
    }


def test_long_plan_uses_nearest_structure_and_reports_both_atr_scenarios():
    result = build_candidate_risk_plan(_long_snapshot(), atr14_1h=2.0)

    assert result["status"] == "assessed"
    assert result["entry_reference"]["price"] == 100.0
    assert result["stop_anchor"] == {
        "timeframe": "4h", "kind": "support_level", "price": 97.0, "touches": 1,
    }
    assert result["target_reference"]["price"] == 107.0
    first, second = result["buffer_scenarios"]
    assert (first["stop_price"], first["gross_risk_reward"]) == (96.0, 1.75)
    assert (second["stop_price"], second["gross_risk_reward"]) == (95.0, 1.4)
    assert first["eligibility"]["matrix_result"] == "watch"
    assert second["eligibility"]["matrix_result"] == "reject"
    assert result["execution_authorized"] is False


def test_short_plan_uses_upper_invalidation_and_lower_target():
    snapshot = _long_snapshot()
    snapshot.update({"setup": "potential_short", "confidence": _confidence(85)})
    snapshot["four_hour_structure"]["resistance"] = [{"price": 103.0, "touches": 1}]
    snapshot["four_hour_structure"]["supply"] = [{"low": 101.0, "high": 104.0, "touches": 1}]
    snapshot["four_hour_structure"]["demand"] = [{"low": 97.0, "high": 99.0, "touches": 1}]

    result = build_candidate_risk_plan(snapshot, atr14_1h=2.0)

    assert result["stop_anchor"]["price"] == 103.0
    assert result["target_reference"]["price"] == 99.0
    assert result["buffer_scenarios"][0]["stop_price"] == 104.0
    assert result["buffer_scenarios"][0]["gross_risk_reward"] == 0.25
    assert result["buffer_scenarios"][0]["eligibility"]["matrix_result"] == "reject"


def test_risk_plan_stays_unavailable_for_partial_confidence_and_stale_data():
    snapshot = _long_snapshot()
    snapshot["confidence"] = _confidence(85, complete=False)

    partial = build_candidate_risk_plan(snapshot, atr14_1h=2.0)
    stale = build_candidate_risk_plan(_long_snapshot(), atr14_1h=2.0, market_data_stale=True)

    assert partial["buffer_scenarios"][0]["eligibility"]["matrix_result"] == "unavailable"
    assert partial["buffer_scenarios"][1]["eligibility"]["matrix_result"] == "reject"
    assert stale["status"] == "unavailable"
    assert stale["reason"] == "stale_market_data_not_eligible_for_risk_review"


def test_risk_plan_does_not_invent_missing_structural_levels():
    snapshot = _long_snapshot()
    snapshot["daily_structure"] = {"support": [], "demand": [], "resistance": [], "supply": []}
    snapshot["four_hour_structure"] = {"support": [], "demand": [], "resistance": [], "supply": []}

    result = build_candidate_risk_plan(snapshot, atr14_1h=2.0)

    assert result["status"] == "unavailable"
    assert result["reason"] == "no_structural_invalidation_level_beyond_entry"
    assert result["buffer_scenarios"] == []
