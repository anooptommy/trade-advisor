import pandas as pd

from app.backtest import _as_of, _forward_metrics, _score_filter_summary


def test_as_of_includes_only_bars_closed_by_replay_cutoff():
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2026-09-01", periods=3, freq="h", tz="UTC"),
        "close": [10, 11, 12],
    })
    result = _as_of(frame, pd.Timestamp("2026-09-01 02:00", tz="UTC"), pd.Timedelta(hours=1))
    assert result["close"].tolist() == [10, 11]


def test_forward_metrics_use_only_the_requested_future_window():
    hourly = pd.DataFrame({
        "timestamp": pd.date_range("2026-09-01", periods=4, freq="h", tz="UTC"),
        "open": [100, 100, 103, 96],
        "high": [101, 105, 110, 98],
        "low": [99, 99, 98, 95],
        "close": [100, 103, 95, 97],
    })
    result = _forward_metrics(hourly, 0, "potential_long", 2)
    assert result["forward_2h_return_pct"] == -5.0
    assert result["favorable_excursion_2h_pct"] == 10.0
    assert result["adverse_excursion_2h_pct"] == 2.0


def test_forward_metrics_are_unavailable_when_future_hourly_data_has_a_gap():
    hourly = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-01T00:00:00Z", "2026-09-01T01:00:00Z", "2026-09-01T03:00:00Z"]),
        "open": [100, 100, 103], "high": [101, 105, 110],
        "low": [99, 99, 98], "close": [100, 103, 105],
    })
    result = _forward_metrics(hourly, 0, "potential_long", 2)
    assert result["forward_2h_return_pct"] is None


def test_short_forward_return_uses_entry_price_as_the_percentage_base():
    hourly = pd.DataFrame({
        "timestamp": pd.date_range("2026-09-01", periods=2, freq="h", tz="UTC"),
        "open": [100, 100], "high": [101, 102], "low": [99, 89], "close": [100, 90],
    })
    result = _forward_metrics(hourly, 0, "potential_short", 1)
    assert result["forward_1h_return_pct"] == 10.0
    assert result["favorable_excursion_1h_pct"] == 11.0
    assert result["adverse_excursion_1h_pct"] == 2.0


def test_score_filter_sweeps_jointly_apply_minimum_fraction_and_coverage():
    events = [
        {"score_fraction": 0.80, "coverage_pct": 91, "forward_24h_return_pct": 1.0},
        {"score_fraction": 0.80, "coverage_pct": 88, "forward_24h_return_pct": -1.0},
        {"score_fraction": 0.70, "coverage_pct": 95, "forward_24h_return_pct": 2.0},
    ]

    result = _score_filter_summary(
        events,
        (24,),
        cutoffs=(0.75,),
        coverage_floors=(85, 90, 95),
    )

    assert result["coverage_gte_85_score_fraction_gte_0.75"]["candidate_count"] == 2
    assert result["coverage_gte_90_score_fraction_gte_0.75"]["candidate_count"] == 1
    assert result["coverage_gte_95_score_fraction_gte_0.75"]["candidate_count"] == 0
