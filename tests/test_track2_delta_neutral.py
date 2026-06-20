from __future__ import annotations

from datetime import UTC, datetime, timedelta

from defiquant.config import load_config
from defiquant.data.fixtures import fixture_market
from defiquant.models import Candle
from defiquant.track2_delta_neutral import (
    DeltaNeutralParams,
    build_delta_neutral_book,
    build_track2_delta_neutral_lab,
    generate_delta_neutral_candidates,
    run_delta_neutral_backtest,
)


def test_delta_neutral_book_builds_long_short_beta_neutral_spec() -> None:
    config = load_config("configs/strategy.json")
    params = DeltaNeutralParams(
        variant="angle_momentum",
        lookback_days=14,
        basket_size=2,
        gross_exposure=1.0,
        min_abs_angle=0.02,
        max_abs_net_beta=0.20,
    )

    book = build_delta_neutral_book(config, _mixed_market(), params)

    assert book.market_regime in {"bull", "bear", "mixed"}
    assert book.long_symbols
    assert book.short_symbols
    assert any(weight > 0 for weight in book.weights.values())
    assert any(weight < 0 for weight in book.weights.values())
    assert round(book.gross_exposure, 6) == 1.0
    assert abs(book.net_beta) <= params.max_abs_net_beta
    assert "trend_angle" in " ".join(
        reason for score in book.coin_regimes for reason in score.reasons
    )


def test_delta_neutral_backtest_includes_turnover_costs_and_trades() -> None:
    config = load_config("configs/strategy.json")
    params = DeltaNeutralParams(
        variant="regime_adaptive",
        lookback_days=7,
        basket_size=1,
        gross_exposure=1.0,
        min_abs_angle=0.02,
        max_abs_net_beta=0.25,
    )
    market = fixture_market(config.universe_symbols, days=60)
    start = market["CAKE"][14].timestamp
    end = start + timedelta(days=21)

    result = run_delta_neutral_backtest(config, market, params, period_start=start, period_end=end)

    assert result.trades > 0
    assert result.rebalances > 0
    assert result.turnover > 0
    assert result.max_drawdown >= 0
    assert result.average_abs_net_beta <= params.max_abs_net_beta


def test_delta_neutral_lab_reports_walk_forward_oos_and_no_execution() -> None:
    config = load_config("configs/strategy.json")
    payload = build_track2_delta_neutral_lab(
        config,
        fixture_market(config.universe_symbols, days=60),
        train_days=21,
        test_days=7,
        step_days=7,
        max_candidates=18,
        top=3,
    )

    assert payload["mode"] == "track2_delta_neutral_lab"
    assert payload["execution"] == "disabled"
    assert payload["safety"] == {
        "wallet_access": "none",
        "transaction_signing": "disabled",
        "orders": "not emitted",
        "output_use": "Track 2 strategy research and CMC Skill rationale only",
    }
    assert payload["parameters_tested"] == 18
    assert payload["loop_count"] >= 18
    assert payload["test_summary"]["tested_period_count"] >= 1
    assert payload["latest_strategy_spec"]["weights"]


def test_candidate_generation_samples_deterministically() -> None:
    first = generate_delta_neutral_candidates(5)
    second = generate_delta_neutral_candidates(5)

    assert first == second
    assert len(first) == 5
    assert {candidate.variant for candidate in first} <= {
        "angle_momentum",
        "vol_adjusted",
        "regime_adaptive",
    }


def _mixed_market() -> dict[str, list[Candle]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "UP1": _trend("UP1", start, 10.0, 0.012),
        "UP2": _trend("UP2", start, 8.0, 0.009),
        "DOWN1": _trend("DOWN1", start, 9.0, -0.010),
        "DOWN2": _trend("DOWN2", start, 7.0, -0.008),
        "USDT": _trend("USDT", start, 1.0, 0.0),
    }


def _trend(symbol: str, start: datetime, start_price: float, daily_return: float) -> list[Candle]:
    price = start_price
    candles: list[Candle] = []
    for day in range(50):
        timestamp = start + timedelta(days=day)
        open_price = price
        close = max(0.05, open_price * (1.0 + daily_return))
        high = max(open_price, close) * 1.01
        low = min(open_price, close) * 0.99
        candles.append(
            Candle(
                symbol=symbol,
                timestamp=timestamp,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=1_000_000 + (day * 1_000),
            )
        )
        price = close
    return candles
