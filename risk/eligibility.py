"""Confidence and reward-to-risk policy; this module never authorizes execution."""

from __future__ import annotations

import math
from typing import Any


HARD_MINIMUM_RISK_REWARD = 1.5


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def evaluate_rr_eligibility(confidence: dict, risk_reward: float | None) -> dict:
    """Classify a candidate under the documented confidence/R:R matrix.

    ``eligible`` means eligible to proceed to a later deterministic risk review.
    It is never an order authorization. Confidence bands remain unavailable for
    partial scores, so this matrix also requires the complete active confidence budget.
    """
    if not isinstance(confidence, dict):
        confidence = {}

    if confidence.get("status") in {
        "not_applicable_no_qualifying_setup",
        "not_applicable_no_aligned_direction",
    }:
        return {
            "status": "not_applicable",
            "matrix_result": "not_applicable",
            "eligible_for_risk_review": False,
            "execution_authorized": False,
            "reason": "no_deterministic_candidate_setup",
        }

    rr = _finite_number(risk_reward)
    if rr is None or rr < 0:
        return {
            "status": "unavailable",
            "matrix_result": "unavailable",
            "eligible_for_risk_review": False,
            "execution_authorized": False,
            "reason": "valid_risk_reward_input_required",
        }

    if rr < HARD_MINIMUM_RISK_REWARD:
        return {
            "status": "assessed",
            "matrix_result": "reject",
            "eligible_for_risk_review": False,
            "execution_authorized": False,
            "confidence_score": confidence.get("score"),
            "risk_reward": rr,
            "reason": "risk_reward_below_1_5r_hard_floor",
        }

    score = _finite_number(confidence.get("score_pct"))
    if score is None:
        fraction = _finite_number(confidence.get("score_fraction"))
        score = fraction * 100 if fraction is not None else None
    assessed_points = _finite_number(confidence.get("assessed_points"))
    total_points = _finite_number(confidence.get("total_points"))
    complete = (
        score is not None
        and 0 <= score <= 100
        and total_points is not None
        and total_points > 0
        and assessed_points == total_points
        and confidence.get("band") is not None
    )
    if not complete:
        return {
            "status": "unavailable",
            "matrix_result": "unavailable",
            "eligible_for_risk_review": False,
            "execution_authorized": False,
            "risk_reward": rr,
            "reason": "complete_active_confidence_budget_required",
        }

    if score < 60 or (score < 70 and rr < 2.0):
        matrix_result = "reject"
    elif score < 70 or (score < 80 and rr < 2.0):
        matrix_result = "watch"
    else:
        matrix_result = "eligible"

    return {
        "status": "assessed",
        "matrix_result": matrix_result,
        "eligible_for_risk_review": matrix_result == "eligible",
        "execution_authorized": False,
        "confidence_score": score,
        "risk_reward": rr,
        "reason": "documented_confidence_and_risk_reward_matrix",
    }
