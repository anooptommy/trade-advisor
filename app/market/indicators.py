import pandas as pd
import logging


logger = logging.getLogger(__name__)


def calculate_ema(
    series: pd.Series,
    period: int,
) -> pd.Series:
    """Calculate Exponential Moving Average."""

    return series.ewm(
        span=period,
        adjust=False,
    ).mean()


def calculate_sma(
    series: pd.Series,
    period: int,
) -> pd.Series:
    """Calculate Simple Moving Average."""

    return series.rolling(
        window=period
    ).mean()


def calculate_rsi(
    series: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Calculate Relative Strength Index."""

    delta = series.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


def calculate_atr(
    df: pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    """Calculate Average True Range."""

    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (
                df["high"] - previous_close
            ).abs(),
            (
                df["low"] - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()


def calculate_swing_high(
    df: pd.DataFrame,
    lookback: int = 5,
) -> pd.Series:
    """Calculate rolling swing high."""

    return (
        df["high"]
        .rolling(
            window=lookback * 2 + 1,
            center=True,
        )
        .max()
    )


def calculate_swing_low(
    df: pd.DataFrame,
    lookback: int = 5,
) -> pd.Series:
    """Calculate rolling swing low."""

    return (
        df["low"]
        .rolling(
            window=lookback * 2 + 1,
            center=True,
        )
        .min()
    )


def calculate_crossover(
    fast: pd.Series,
    slow: pd.Series,
) -> pd.Series:
    """
    Detect moving-average crossovers.

    Returns:

        bullish_cross
        bearish_cross
        no_cross
    """

    previous_fast = fast.shift(1)
    previous_slow = slow.shift(1)

    bullish = (
        (previous_fast <= previous_slow)
        & (fast > slow)
    )

    bearish = (
        (previous_fast >= previous_slow)
        & (fast < slow)
    )

    result = pd.Series(
        "no_cross",
        index=fast.index,
    )

    result.loc[bullish] = "bullish_cross"
    result.loc[bearish] = "bearish_cross"

    return result


def calculate_macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """Calculate standard MACD, signal, and histogram from closing prices."""
    if fast_period <= 0 or slow_period <= fast_period or signal_period <= 0:
        raise ValueError("MACD periods must satisfy 0 < fast < slow and signal > 0.")
    fast = series.ewm(span=fast_period, min_periods=fast_period, adjust=False).mean()
    slow = series.ewm(span=slow_period, min_periods=slow_period, adjust=False).mean()
    macd = fast - slow
    signal = macd.ewm(span=signal_period, min_periods=signal_period, adjust=False).mean()
    return pd.DataFrame({"macd": macd, "macd_signal": signal, "macd_histogram": macd - signal}, index=series.index)


def calculate_indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate all indicators required by
    the trading rules engine.
    """

    market_meta = df.attrs.get("market_data", {})
    logger.debug(
        "Indicator calculation started coin_id=%s timeframe=%s rows=%d",
        market_meta.get("coin_id", "unknown"), market_meta.get("timeframe", "unknown"), len(df),
    )
    required_columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required columns: {missing_columns}"
        )

    result = (
        df.copy()
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # ---------------------------------------------------------
    # Trend indicators
    # ---------------------------------------------------------

    result["ema9"] = calculate_ema(
        result["close"],
        9,
    )

    result["ema21"] = calculate_ema(
        result["close"],
        21,
    )

    result["sma50"] = calculate_sma(
        result["close"],
        50,
    )

    # ---------------------------------------------------------
    # Crossovers
    # ---------------------------------------------------------

    result["ema9_21_cross"] = calculate_crossover(
        result["ema9"],
        result["ema21"],
    )

    result["ema21_sma50_cross"] = calculate_crossover(
        result["ema21"],
        result["sma50"],
    )

    # ---------------------------------------------------------
    # Momentum
    # ---------------------------------------------------------

    result["rsi14"] = calculate_rsi(
        result["close"],
        14,
    )

    macd = calculate_macd(result["close"])
    result[["macd", "macd_signal", "macd_histogram"]] = macd

    # ---------------------------------------------------------
    # Volatility
    # ---------------------------------------------------------

    result["atr14"] = calculate_atr(
        result,
        14,
    )

    # ---------------------------------------------------------
    # Price structure
    # ---------------------------------------------------------

    result["swing_high"] = calculate_swing_high(
        result
    )

    result["swing_low"] = calculate_swing_low(
        result
    )

    result.attrs.update(df.attrs)
    logger.debug(
        "Indicator calculation completed coin_id=%s timeframe=%s rows=%d",
        market_meta.get("coin_id", "unknown"), market_meta.get("timeframe", "unknown"), len(result),
    )

    return result
