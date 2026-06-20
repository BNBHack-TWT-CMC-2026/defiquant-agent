from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta

from track1_volume_impulse_lab.cmc_dex import (
    FiveMinuteCandle,
    aggregate_5m_to_10m,
    load_pairs_config,
    parse_dex_ohlcv_5m,
    parse_kline_candles_5m,
)
from track1_volume_impulse_lab.strategy import (
    LabConfig,
    ParameterSet,
    TenMinuteCandle,
    fixture_market,
    load_10m_csv,
    optimize_weekly_periods,
    parameter_grid,
    run_backtest,
    signal_for_candle,
    summary_markdown,
    weekly_periods,
    write_volume_baselines,
)


def test_aggregates_two_5m_candles_to_one_10m_candle() -> None:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    market = {
        "TEST": [
            FiveMinuteCandle("TEST", start, 10, 11, 9, 10.5, 100),
            FiveMinuteCandle("TEST", start + timedelta(minutes=5), 10.5, 12, 10, 11.5, 125),
        ]
    }

    result = aggregate_5m_to_10m(market)

    assert len(result["TEST"]) == 1
    candle = result["TEST"][0]
    assert candle.open == 10
    assert candle.high == 12
    assert candle.low == 9
    assert candle.close == 11.5
    assert candle.volume == 225


def test_parse_dex_ohlcv_5m_accepts_cmc_list_payload() -> None:
    payload = [
        {
            "quotes": [
                {
                    "time_open": "2026-06-01T00:00:00Z",
                    "quote": [
                        {
                            "open": 1,
                            "high": 1.2,
                            "low": 0.9,
                            "close": 1.1,
                            "volume": 123,
                        }
                    ],
                }
            ]
        }
    ]

    candles = parse_dex_ohlcv_5m("cake", payload)

    assert candles[0].symbol == "CAKE"
    assert candles[0].close == 1.1
    assert candles[0].timestamp.tzinfo is not None


def test_parse_kline_candles_5m_accepts_cmc_array_payload() -> None:
    payload = {
        "data": [
            [1.0, 1.2, 0.9, 1.1, 123.0, 1_717_200_000_000, 7],
        ]
    }

    candles = parse_kline_candles_5m("cake", payload)

    assert candles[0].symbol == "CAKE"
    assert candles[0].close == 1.1
    assert candles[0].volume == 123
    assert candles[0].timestamp.tzinfo is not None


def test_load_pairs_config_accepts_network_id_and_platform(tmp_path) -> None:
    path = tmp_path / "pairs.json"
    path.write_text(
        """
        {
          "pairs": [
            {
              "symbol": "test",
              "contract_address": "0x123",
              "network_slug": "bsc",
              "network_id": "14",
              "platform": "bsc"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    pair = load_pairs_config(path)[0]

    assert pair.symbol == "TEST"
    assert pair.network_id == "14"
    assert pair.platform == "bsc"


def test_signal_uses_prior_30_day_average_and_excludes_current_candle() -> None:
    config = LabConfig(baseline_days=1)
    params = ParameterSet(volume_spike_multiple=2.0, leverage=3.0, exit_volume_decreases=2)
    start = datetime(2026, 6, 1, tzinfo=UTC)
    history = [
        TenMinuteCandle("TEST", start + timedelta(minutes=10 * index), 1, 1, 1, 1, 100)
        for index in range(config.baseline_window)
    ]
    current = TenMinuteCandle(
        "TEST",
        start + timedelta(minutes=10 * config.baseline_window),
        1,
        1.2,
        0.9,
        1.1,
        300,
    )

    signal = signal_for_candle(current, history, params, config)

    assert signal is not None
    assert signal.baseline_volume == 100
    assert signal.volume_multiple == 3
    assert signal.side == "long"


def test_simultaneous_spike_selects_highest_volume_multiple() -> None:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    market = {
        "LOW": _market_with_spike("LOW", start, spike_volume=250, spike_close=11),
        "HIGH": _market_with_spike("HIGH", start, spike_volume=500, spike_close=9),
    }

    result = run_backtest(
        market,
        ParameterSet(volume_spike_multiple=2.0, leverage=2.0, exit_volume_decreases=2),
        LabConfig(seed=1000, baseline_days=1),
    )

    assert result.trades
    assert result.trades[0].symbol == "HIGH"
    assert result.trades[0].side == "short"


def test_exits_after_configured_consecutive_volume_decreases() -> None:
    result = run_backtest(
        {"TEST": _market_with_volume_decline("TEST")},
        ParameterSet(volume_spike_multiple=2.0, leverage=2.0, exit_volume_decreases=3),
        LabConfig(seed=1000, baseline_days=1),
    )

    assert result.trades
    assert result.trades[0].exit_reason == "volume_decrease_exit"


def test_switch_uses_last_position_price_when_current_symbol_candle_is_missing() -> None:
    config = LabConfig(seed=1000, baseline_days=1, fee_bps=0, slippage_bps=0)
    params = ParameterSet(volume_spike_multiple=2.0, leverage=1.0, exit_volume_decreases=2)
    start = datetime(2026, 6, 1, tzinfo=UTC)
    baseline_window = config.baseline_window
    alpha: list[TenMinuteCandle] = []
    beta: list[TenMinuteCandle] = []
    for index in range(baseline_window):
        timestamp = start + timedelta(minutes=10 * index)
        alpha.append(TenMinuteCandle("ALPHA", timestamp, 10, 10, 10, 10, 100))
        beta.append(TenMinuteCandle("BETA", timestamp, 50, 50, 50, 50, 100))
    alpha.append(
        TenMinuteCandle(
            "ALPHA",
            start + timedelta(minutes=10 * baseline_window),
            10,
            11,
            10,
            11,
            300,
        )
    )
    beta.extend(
        [
            TenMinuteCandle(
                "BETA",
                start + timedelta(minutes=10 * baseline_window),
                50,
                50,
                50,
                50,
                100,
            ),
            TenMinuteCandle(
                "BETA",
                start + timedelta(minutes=10 * (baseline_window + 1)),
                50,
                55,
                50,
                55,
                400,
            ),
        ]
    )

    result = run_backtest({"ALPHA": alpha, "BETA": beta}, params, config)

    assert result.trades[0].symbol == "ALPHA"
    assert result.trades[0].exit_reason == "switch"
    assert result.trades[0].exit_price == 11


def test_weekly_optimizer_filters_liquidation_and_reports_best() -> None:
    params = parameter_grid(
        volume_spike_multiples=(2.0,),
        leverages=(2.0, 100.0),
        exit_volume_decreases=(2,),
    )

    report = optimize_weekly_periods(
        fixture_market(),
        params,
        LabConfig(seed=1000, baseline_days=30),
        progress=False,
    )

    assert report.periods
    assert any(period.best is not None for period in report.periods)
    assert report.overall_best_parameters is not None
    assert "Weekly Best" in summary_markdown(report)


def test_weekly_periods_ignore_symbols_without_baseline_window() -> None:
    config = LabConfig(baseline_days=1, period_days=1)
    sparse_start = datetime(2026, 1, 1, tzinfo=UTC)
    ready_start = datetime(2026, 2, 1, tzinfo=UTC)
    market = {
        "SPARSE": [
            TenMinuteCandle("SPARSE", sparse_start, 1, 1, 1, 1, 100),
        ],
        "READY": [
            TenMinuteCandle(
                "READY",
                ready_start + timedelta(minutes=10 * index),
                1,
                1,
                1,
                1,
                100,
            )
            for index in range(config.baseline_window + 2)
        ],
    }

    periods = weekly_periods(market, config)

    assert periods[0][0] == ready_start + timedelta(minutes=10 * config.baseline_window)


def test_csv_loader_round_trips_10m_candles(tmp_path) -> None:
    path = tmp_path / "candles.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["timestamp", "symbol", "open", "high", "low", "close", "volume"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": "2026-06-01T00:00:00Z",
                "symbol": "test",
                "open": "1",
                "high": "1.2",
                "low": "0.8",
                "close": "1.1",
                "volume": "100",
            }
        )

    market = load_10m_csv(path)

    assert market["TEST"][0].timestamp.tzinfo is not None
    assert market["TEST"][0].volume == 100


def test_writes_volume_baselines_with_current_candle_excluded(tmp_path) -> None:
    config = LabConfig(baseline_days=1)
    start = datetime(2026, 6, 1, tzinfo=UTC)
    candles = [
        TenMinuteCandle("TEST", start + timedelta(minutes=10 * index), 1, 1, 1, 1, 100)
        for index in range(config.baseline_window)
    ]
    candles.append(
        TenMinuteCandle(
            "TEST",
            start + timedelta(minutes=10 * config.baseline_window),
            1,
            1.2,
            1,
            1.1,
            300,
        )
    )
    output = tmp_path / "baselines.csv"

    write_volume_baselines({"TEST": candles}, config, output)

    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert len(rows) == 1
    assert float(rows[0]["baseline_volume"]) == 100
    assert float(rows[0]["volume_multiple"]) == 3


def _market_with_spike(
    symbol: str,
    start: datetime,
    *,
    spike_volume: float,
    spike_close: float,
) -> list[TenMinuteCandle]:
    candles: list[TenMinuteCandle] = []
    baseline_window = LabConfig(baseline_days=1).baseline_window
    for index in range(baseline_window + 5):
        timestamp = start + timedelta(minutes=10 * index)
        if index == baseline_window:
            open_price = 10.0
            close = spike_close
            volume = spike_volume
        else:
            open_price = 10.0
            close = 10.0
            volume = 100.0
        candles.append(
            TenMinuteCandle(
                symbol=symbol,
                timestamp=timestamp,
                open=open_price,
                high=max(open_price, close) * 1.001,
                low=min(open_price, close) * 0.999,
                close=close,
                volume=volume,
            )
        )
    return candles


def _market_with_volume_decline(symbol: str) -> list[TenMinuteCandle]:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    candles = _market_with_spike(symbol, start, spike_volume=500, spike_close=11)
    baseline_window = LabConfig(baseline_days=1).baseline_window
    replacements = [
        TenMinuteCandle(symbol, candles[baseline_window + 1].timestamp, 11, 11.2, 10.9, 11.1, 400),
        TenMinuteCandle(symbol, candles[baseline_window + 2].timestamp, 11.1, 11.3, 11, 11.2, 300),
        TenMinuteCandle(
            symbol,
            candles[baseline_window + 3].timestamp,
            11.2,
            11.4,
            11.1,
            11.3,
            200,
        ),
    ]
    return candles[: baseline_window + 1] + replacements + candles[baseline_window + 4 :]
