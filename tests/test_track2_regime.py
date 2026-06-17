from __future__ import annotations

from datetime import UTC, datetime, timedelta

from defiquant.config import load_config
from defiquant.data.fixtures import fixture_market
from defiquant.models import Candle
from defiquant.track2_regime import build_track2_regime_spec


def test_track2_regime_spec_is_non_executing_and_reports_lanes() -> None:
    config = load_config("configs/strategy.json")
    payload = build_track2_regime_spec(config, fixture_market(config.universe_symbols), top=5)

    assert payload["mode"] == "track2_regime_strategy_spec"
    assert payload["execution"] == "disabled"
    assert payload["safety"] == {
        "wallet_access": "none",
        "transaction_signing": "disabled",
        "orders": "not emitted",
        "output_use": "strategy research and CMC Skill rationale only",
    }
    assert [lane["name"] for lane in payload["strategy_lanes"]] == [
        "up_channel_long_bias",
        "down_channel_short_bias",
    ]

    first_signal = payload["signals"][0]
    reason_keys = {reason.split("=", maxsplit=1)[0] for reason in first_signal["reasons"]}
    assert {
        "support_line",
        "support_distance",
        "support_break",
        "trend_angle",
        "supertrend_alignment",
        "cloud_bias",
        "volume_impulse",
        "long_score",
        "short_score",
    } == reason_keys


def test_track2_regime_identifies_up_and_down_channels() -> None:
    config = load_config("configs/strategy.json")
    market = {
        "CAKE": _trend_market("CAKE", start_price=10.0, daily_return=0.018),
        "TWT": _trend_market("TWT", start_price=20.0, daily_return=-0.018),
        "USDT": _trend_market("USDT", start_price=1.0, daily_return=0.0),
    }

    payload = build_track2_regime_spec(config, market, top=5)
    by_symbol = {signal["symbol"]: signal for signal in payload["signals"]}

    assert by_symbol["CAKE"]["regime"] == "up_channel"
    assert by_symbol["CAKE"]["directional_bias"] == "long"
    assert by_symbol["TWT"]["regime"] == "down_channel"
    assert by_symbol["TWT"]["directional_bias"] == "short"


def _trend_market(
    symbol: str,
    *,
    start_price: float,
    daily_return: float,
    days: int = 70,
) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = start_price
    candles: list[Candle] = []
    for day in range(days):
        open_price = price
        close = max(0.05, open_price * (1.0 + daily_return))
        high = max(open_price, close) * 1.01
        low = min(open_price, close) * 0.99
        volume = 1_000_000 + (day * 10_000)
        candles.append(
            Candle(
                symbol=symbol,
                timestamp=start + timedelta(days=day),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
        price = close
    return candles
