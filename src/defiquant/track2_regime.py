from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from defiquant.config import AppConfig
from defiquant.indicators import supertrend_alignment, trend_angle
from defiquant.models import Candle, MarketData


@dataclass(frozen=True)
class Track2RegimeSignal:
    symbol: str
    regime: str
    strategy: str
    directional_bias: str
    confidence: float
    score: float
    reasons: tuple[str, ...]


def build_track2_regime_spec(
    config: AppConfig,
    market: MarketData,
    *,
    top: int = 10,
) -> dict[str, Any]:
    signals: list[Track2RegimeSignal] = []
    for symbol, candles in market.items():
        if symbol == config.strategy.stable_symbol:
            continue
        clean = _sorted_positive(candles)
        if len(clean) < 30:
            continue
        signals.append(_classify_symbol(symbol, clean))

    ranked = sorted(signals, key=lambda signal: signal.confidence, reverse=True)[: max(1, top)]
    return {
        "mode": "track2_regime_strategy_spec",
        "execution": "disabled",
        "strategy_lanes": _strategy_lanes(),
        "signals": [_signal_to_jsonable(signal) for signal in ranked],
        "safety": {
            "wallet_access": "none",
            "transaction_signing": "disabled",
            "orders": "not emitted",
            "output_use": "strategy research and CMC Skill rationale only",
        },
    }


def _classify_symbol(symbol: str, candles: list[Candle]) -> Track2RegimeSignal:
    prices = [candle.close for candle in candles]
    current = prices[-1]
    support = _support_line(candles, window=20)
    support_distance = (current / support) - 1.0 if support > 0 else 0.0
    support_break = current < support * 0.995 if support > 0 else False
    angle = trend_angle(prices, window=10)
    supertrend = supertrend_alignment(candles, period=10, multiplier=3.0)
    cloud = _cloud_bias(candles)
    volume = _volume_impulse(candles, lookback=20)
    support_proximity = _clamp(1.0 - (support_distance / 0.12), 0.0, 1.0)
    support_break_score = 1.0 if support_break else 0.0

    long_score = (
        (0.30 * max(0.0, angle))
        + (0.25 * max(0.0, supertrend))
        + (0.25 * max(0.0, cloud))
        + (0.10 * support_proximity)
        + (0.10 * max(0.0, volume))
    )
    short_score = (
        (0.30 * max(0.0, -angle))
        + (0.25 * max(0.0, -supertrend))
        + (0.25 * max(0.0, -cloud))
        + (0.10 * support_break_score)
        + (0.10 * max(0.0, volume))
    )

    if long_score >= short_score + 0.10 and angle > 0.03 and supertrend > 0 and cloud > 0:
        regime = "up_channel"
        strategy = "up_channel_long_bias"
        directional_bias = "long"
        confidence = long_score
        score = long_score - short_score
    elif (
        short_score >= long_score + 0.10
        and angle < -0.03
        and (supertrend < 0 or cloud < 0 or support_break)
    ):
        regime = "down_channel"
        strategy = "down_channel_short_bias"
        directional_bias = "short"
        confidence = short_score
        score = short_score - long_score
    else:
        regime = "range_or_transition"
        strategy = "neutral_observe"
        directional_bias = "neutral"
        confidence = max(long_score, short_score)
        score = long_score - short_score

    reasons = (
        f"support_line={support:.6f}",
        f"support_distance={support_distance:.4f}",
        f"support_break={str(support_break).lower()}",
        f"trend_angle={angle:.4f}",
        f"supertrend_alignment={supertrend:.4f}",
        f"cloud_bias={cloud:.4f}",
        f"volume_impulse={volume:.4f}",
        f"long_score={long_score:.4f}",
        f"short_score={short_score:.4f}",
    )
    return Track2RegimeSignal(
        symbol=symbol,
        regime=regime,
        strategy=strategy,
        directional_bias=directional_bias,
        confidence=confidence,
        score=score,
        reasons=reasons,
    )


def _strategy_lanes() -> list[dict[str, Any]]:
    return [
        {
            "name": "up_channel_long_bias",
            "directional_bias": "long",
            "regime_trigger": (
                "positive trend angle with Supertrend support and price above the cloud"
            ),
            "confirmation_factors": [
                "support line holds as invalidation reference",
                "trend angle remains positive",
                "Supertrend alignment is positive",
                "price stays above Ichimoku-lite cloud",
                "volume impulse confirms participation",
            ],
            "risk_note": "Track 2 emits research rationale only; no long order is submitted.",
        },
        {
            "name": "down_channel_short_bias",
            "directional_bias": "short",
            "regime_trigger": (
                "negative trend angle with Supertrend resistance, cloud breakdown, "
                "or support-line break"
            ),
            "confirmation_factors": [
                "support line is broken or fails to reclaim",
                "trend angle remains negative",
                "Supertrend alignment is negative",
                "price stays below Ichimoku-lite cloud",
                "volume impulse confirms distribution",
            ],
            "risk_note": "Track 2 emits research rationale only; no short order is submitted.",
        },
    ]


def _signal_to_jsonable(signal: Track2RegimeSignal) -> dict[str, Any]:
    return {
        "symbol": signal.symbol,
        "regime": signal.regime,
        "strategy": signal.strategy,
        "directional_bias": signal.directional_bias,
        "confidence": signal.confidence,
        "score": signal.score,
        "reasons": list(signal.reasons),
    }


def _sorted_positive(candles: list[Candle]) -> list[Candle]:
    return sorted(
        [
            candle
            for candle in candles
            if candle.close > 0 and candle.high > 0 and candle.low > 0 and candle.volume >= 0
        ],
        key=lambda candle: candle.timestamp,
    )


def _support_line(candles: list[Candle], *, window: int) -> float:
    prior = candles[-window - 1 : -1] if len(candles) > window else candles[:-1]
    if not prior:
        return candles[-1].low
    return min(candle.low for candle in prior)


def _cloud_bias(candles: list[Candle]) -> float:
    conversion = _midpoint(candles, window=9)
    base = _midpoint(candles, window=26)
    span_b = _midpoint(candles, window=52)
    if conversion <= 0 or base <= 0 or span_b <= 0:
        return 0.0
    span_a = (conversion + base) / 2.0
    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)
    close = candles[-1].close
    if close > cloud_top * 1.005:
        return 1.0
    if close < cloud_bottom * 0.995:
        return -1.0
    return 0.0


def _midpoint(candles: list[Candle], *, window: int) -> float:
    if not candles:
        return 0.0
    sample = candles[-min(window, len(candles)) :]
    high = max(candle.high for candle in sample)
    low = min(candle.low for candle in sample)
    return (high + low) / 2.0 if high > 0 and low > 0 else 0.0


def _volume_impulse(candles: list[Candle], *, lookback: int) -> float:
    if len(candles) < 4:
        return 0.0
    baseline_sample = candles[-lookback - 3 : -3] if len(candles) > lookback + 3 else candles[:-3]
    recent = candles[-3:]
    baseline = _average([candle.volume for candle in baseline_sample])
    recent_average = _average([candle.volume for candle in recent])
    if baseline <= 0:
        return 0.0
    return _clamp((recent_average / baseline) - 1.0, -0.50, 1.50)


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))
