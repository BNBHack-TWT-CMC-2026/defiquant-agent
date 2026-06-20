from __future__ import annotations

import csv
import json
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from defiquant.indicators import max_drawdown

TEN_MINUTES = timedelta(minutes=10)
DEFAULT_BASELINE_DAYS = 30


@dataclass(frozen=True)
class TenMinuteCandle:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class LabConfig:
    seed: float = 1000.0
    baseline_days: int = DEFAULT_BASELINE_DAYS
    period_days: int = 7
    max_drawdown: float = 0.30
    fee_bps: float = 5.0
    slippage_bps: float = 10.0

    @property
    def baseline_window(self) -> int:
        return self.baseline_days * 24 * 6


@dataclass(frozen=True, order=True)
class ParameterSet:
    volume_spike_multiple: float
    leverage: float
    exit_volume_decreases: int


@dataclass(frozen=True)
class VolumeImpulseSignal:
    symbol: str
    timestamp: datetime
    side: str
    price: float
    candle_return: float
    volume: float
    baseline_volume: float
    volume_multiple: float


@dataclass(frozen=True)
class Position:
    symbol: str
    side: str
    entry_time: datetime
    entry_price: float
    margin: float
    notional: float
    entry_cost: float
    entry_volume_multiple: float
    volume_decrease_streak: int = 0


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    entry_volume_multiple: float
    exit_reason: str
    pnl: float
    fees_and_slippage: float
    return_on_margin: float


@dataclass(frozen=True)
class BacktestResult:
    parameters: ParameterSet
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    trades: tuple[Trade, ...]
    equity_curve: tuple[tuple[datetime, float], ...]
    liquidated: bool
    risk_stopped: bool

    @property
    def eligible(self) -> bool:
        return (
            not self.liquidated
            and not self.risk_stopped
            and self.max_drawdown <= 0.30
            and len(self.trades) > 0
        )


@dataclass(frozen=True)
class PeriodResult:
    period_start: datetime
    period_end: datetime
    best: BacktestResult | None
    top: tuple[BacktestResult, ...]
    case_count: int
    eligible_case_count: int
    liquidated_case_count: int
    risk_stopped_case_count: int


@dataclass(frozen=True)
class OptimizationReport:
    config: LabConfig
    periods: tuple[PeriodResult, ...]
    overall_best_parameters: dict[str, Any] | None


Market10m = dict[str, list[TenMinuteCandle]]


def load_10m_csv(path: str | Path) -> Market10m:
    market: dict[str, list[TenMinuteCandle]] = defaultdict(list)
    with Path(path).open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            candle = TenMinuteCandle(
                symbol=row["symbol"].strip().upper(),
                timestamp=parse_timestamp(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            market[candle.symbol].append(candle)
    return sort_market(market)


def write_10m_csv(market: Market10m, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["timestamp", "symbol", "open", "high", "low", "close", "volume"],
        )
        writer.writeheader()
        for candle in iter_candles(market):
            writer.writerow(
                {
                    "timestamp": candle.timestamp.isoformat(),
                    "symbol": candle.symbol,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
            )


def write_volume_baselines(market: Market10m, config: LabConfig, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "symbol",
                "baseline_window",
                "baseline_volume",
                "volume",
                "volume_multiple",
            ],
        )
        writer.writeheader()
        for symbol, candles in sort_market(dict(market)).items():
            window: deque[float] = deque()
            total = 0.0
            for candle in candles:
                if len(window) >= config.baseline_window:
                    baseline = total / config.baseline_window
                    writer.writerow(
                        {
                            "timestamp": candle.timestamp.isoformat(),
                            "symbol": symbol,
                            "baseline_window": config.baseline_window,
                            "baseline_volume": baseline,
                            "volume": candle.volume,
                            "volume_multiple": candle.volume / baseline if baseline > 0 else 0.0,
                        }
                    )
                window.append(candle.volume)
                total += candle.volume
                if len(window) > config.baseline_window:
                    total -= window.popleft()


def fixture_market() -> Market10m:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    symbols = ("ALPHA", "BETA", "GAMMA")
    market: Market10m = {}
    total = (DEFAULT_BASELINE_DAYS + 21) * 24 * 6
    for offset, symbol in enumerate(symbols):
        candles: list[TenMinuteCandle] = []
        price = 100.0 - (offset * 12.0)
        for index in range(total):
            timestamp = start + TEN_MINUTES * index
            base_volume = 1000.0 + (offset * 150.0)
            volume = base_volume + ((index % 12) * 15.0)
            drift = 0.0002 * (offset + 1)
            shock = 0.0
            if index in {DEFAULT_BASELINE_DAYS * 24 * 6 + 12, DEFAULT_BASELINE_DAYS * 24 * 6 + 450}:
                volume *= 12.0 + offset
                shock = 0.025 if offset != 1 else -0.03
            if index in {
                DEFAULT_BASELINE_DAYS * 24 * 6 + 1020,
                DEFAULT_BASELINE_DAYS * 24 * 6 + 1600,
            }:
                volume *= 18.0 - offset
                shock = -0.025 if offset == 0 else 0.028
            open_price = price
            close = max(0.01, open_price * (1.0 + drift + shock))
            high = max(open_price, close) * 1.001
            low = min(open_price, close) * 0.999
            candles.append(
                TenMinuteCandle(
                    symbol=symbol,
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
            price = close
        market[symbol] = candles
    return market


def parameter_grid(
    *,
    volume_spike_multiples: Sequence[float],
    leverages: Sequence[float],
    exit_volume_decreases: Sequence[int],
) -> tuple[ParameterSet, ...]:
    return tuple(
        ParameterSet(float(spike), float(leverage), int(exit_decreases))
        for spike in volume_spike_multiples
        for leverage in leverages
        for exit_decreases in exit_volume_decreases
    )


def run_backtest(
    market: Market10m,
    parameters: ParameterSet,
    config: LabConfig,
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> BacktestResult:
    validate_inputs(market, parameters, config)
    candles_by_time = candles_by_timestamp(market)
    history: dict[str, list[TenMinuteCandle]] = defaultdict(list)
    volume_windows: dict[str, deque[float]] = defaultdict(deque)
    volume_totals: dict[str, float] = defaultdict(float)
    equity = config.seed
    high_watermark = config.seed
    position: Position | None = None
    trades: list[Trade] = []
    curve: list[tuple[datetime, float]] = []
    liquidated = False
    risk_stopped = False

    for timestamp in sorted(candles_by_time):
        candles = candles_by_time[timestamp]
        current_by_symbol = {candle.symbol: candle for candle in candles}
        in_period = (period_start is None or timestamp >= period_start) and (
            period_end is None or timestamp < period_end
        )

        if not in_period:
            for candle in candles:
                _append_history(candle, history, volume_windows, volume_totals, config)
            continue

        if position is not None and position.symbol in current_by_symbol:
            candle = current_by_symbol[position.symbol]
            liquidation_price = liquidation_price_for(position, parameters)
            if hit_liquidation(position, candle, liquidation_price):
                trade, equity = close_position(
                    position,
                    exit_time=timestamp,
                    exit_price=liquidation_price,
                    exit_reason="liquidation",
                    config=config,
                    liquidated=True,
                )
                trades.append(trade)
                position = None
                liquidated = True
            else:
                position = update_volume_decrease(position, history[position.symbol], candle)
                if position.volume_decrease_streak >= parameters.exit_volume_decreases:
                    trade, equity = close_position(
                        position,
                        exit_time=timestamp,
                        exit_price=candle.close,
                        exit_reason="volume_decrease_exit",
                        config=config,
                    )
                    trades.append(trade)
                    position = None

        if equity <= 0 or liquidated:
            curve.append((timestamp, 0.0))
            break

        signal = strongest_signal(
            candles,
            history,
            parameters,
            config,
            baseline_volumes=_baseline_volumes(volume_windows, volume_totals, config),
        )
        if signal is not None:
            if position is None:
                position = open_position(signal, equity, parameters, config)
                equity = position.margin
            elif signal.symbol != position.symbol or signal.side != position.side:
                exit_candle = current_by_symbol.get(position.symbol)
                exit_price = exit_candle.close if exit_candle is not None else signal.price
                trade, equity = close_position(
                    position,
                    exit_time=timestamp,
                    exit_price=exit_price,
                    exit_reason="switch",
                    config=config,
                )
                trades.append(trade)
                position = open_position(signal, equity, parameters, config) if equity > 0 else None
                equity = position.margin if position is not None else equity

        for candle in candles:
            _append_history(candle, history, volume_windows, volume_totals, config)

        marked_equity = mark_to_market(position, current_by_symbol, config) if position else equity
        high_watermark = max(high_watermark, marked_equity)
        drawdown = 1.0 - (marked_equity / high_watermark) if high_watermark > 0 else 0.0
        curve.append((timestamp, marked_equity))
        if drawdown > config.max_drawdown:
            risk_stopped = True
            if position is not None:
                exit_candle = current_by_symbol.get(position.symbol)
                exit_price = exit_candle.close if exit_candle is not None else position.entry_price
                trade, equity = close_position(
                    position,
                    exit_time=timestamp,
                    exit_price=exit_price,
                    exit_reason="mdd_stop",
                    config=config,
                )
                trades.append(trade)
                curve[-1] = (timestamp, equity)
                position = None
            break

    if position is not None and curve:
        final_time = curve[-1][0]
        final_candle = _last_candle_at_or_before(market[position.symbol], final_time)
        trade, equity = close_position(
            position,
            exit_time=final_time,
            exit_price=final_candle.close,
            exit_reason="end_of_period",
            config=config,
        )
        trades.append(trade)
        curve[-1] = (final_time, equity)

    final_equity = curve[-1][1] if curve else config.seed
    curve_values = [value for _, value in curve] or [config.seed]
    return BacktestResult(
        parameters=parameters,
        initial_equity=config.seed,
        final_equity=final_equity,
        total_return=(final_equity / config.seed) - 1.0 if config.seed > 0 else 0.0,
        max_drawdown=max_drawdown(curve_values),
        trades=tuple(trades),
        equity_curve=tuple(curve),
        liquidated=liquidated,
        risk_stopped=risk_stopped,
    )


def optimize_weekly_periods(
    market: Market10m,
    parameters: Sequence[ParameterSet],
    config: LabConfig,
    *,
    top: int = 10,
    progress: bool = True,
) -> OptimizationReport:
    periods = weekly_periods(market, config)
    results: list[PeriodResult] = []
    total_cases = len(periods) * len(parameters)
    iterator = _progress(range(total_cases), enabled=progress, desc="weekly sweep")
    case_index = 0
    all_results_by_params: dict[ParameterSet, list[BacktestResult]] = defaultdict(list)

    for period_start, period_end in periods:
        period_results: list[BacktestResult] = []
        period_best_return: float | None = None
        for params in parameters:
            result = run_backtest(
                market,
                params,
                config,
                period_start=period_start,
                period_end=period_end,
            )
            period_results.append(result)
            all_results_by_params[params].append(result)
            if result.eligible and (
                period_best_return is None or result.total_return > period_best_return
            ):
                period_best_return = result.total_return
                _set_progress_postfix(
                    iterator,
                    period=period_start.date().isoformat(),
                    best_return=f"{period_best_return:.4f}",
                )
            case_index += 1
            _advance(iterator, case_index)

        ranked = sorted(
            period_results,
            key=lambda item: (
                item.eligible,
                item.total_return,
                -item.max_drawdown,
                len(item.trades),
            ),
            reverse=True,
        )
        eligible = [result for result in ranked if result.eligible]
        results.append(
            PeriodResult(
                period_start=period_start,
                period_end=period_end,
                best=eligible[0] if eligible else None,
                top=tuple(eligible[: max(1, top)]),
                case_count=len(period_results),
                eligible_case_count=len(eligible),
                liquidated_case_count=sum(1 for result in period_results if result.liquidated),
                risk_stopped_case_count=sum(1 for result in period_results if result.risk_stopped),
            )
        )

    _close_progress(iterator)
    return OptimizationReport(
        config=config,
        periods=tuple(results),
        overall_best_parameters=overall_best_parameters(all_results_by_params),
    )


def weekly_periods(market: Market10m, config: LabConfig) -> tuple[tuple[datetime, datetime], ...]:
    timestamps = [candle.timestamp for candle in iter_candles(market)]
    if not timestamps:
        return ()
    start = min(timestamps) + timedelta(days=config.baseline_days)
    end = max(timestamps) + TEN_MINUTES
    step = timedelta(days=config.period_days)
    periods: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        period_end = min(cursor + step, end)
        if period_end > cursor:
            periods.append((cursor, period_end))
        cursor = period_end
    return tuple(periods)


def write_report(report: OptimizationReport, output_dir: str | Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "weekly_results.json").write_text(
        json.dumps(report_to_jsonable(report), indent=2),
        encoding="utf-8",
    )
    (output / "summary.md").write_text(summary_markdown(report), encoding="utf-8")


def report_to_jsonable(report: OptimizationReport) -> dict[str, Any]:
    return {
        "config": {
            "seed": report.config.seed,
            "baseline_days": report.config.baseline_days,
            "period_days": report.config.period_days,
            "max_drawdown": report.config.max_drawdown,
            "fee_bps": report.config.fee_bps,
            "slippage_bps": report.config.slippage_bps,
        },
        "overall_best_parameters": report.overall_best_parameters,
        "periods": [
            {
                "period_start": period.period_start.isoformat(),
                "period_end": period.period_end.isoformat(),
                "case_count": period.case_count,
                "eligible_case_count": period.eligible_case_count,
                "liquidated_case_count": period.liquidated_case_count,
                "risk_stopped_case_count": period.risk_stopped_case_count,
                "best": result_to_jsonable(period.best) if period.best else None,
                "top": [result_to_jsonable(result, include_trades=False) for result in period.top],
            }
            for period in report.periods
        ],
    }


def result_to_jsonable(result: BacktestResult, *, include_trades: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parameters": {
            "volume_spike_multiple": result.parameters.volume_spike_multiple,
            "leverage": result.parameters.leverage,
            "exit_volume_decreases": result.parameters.exit_volume_decreases,
        },
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "total_return": result.total_return,
        "max_drawdown": result.max_drawdown,
        "trade_count": len(result.trades),
        "liquidated": result.liquidated,
        "risk_stopped": result.risk_stopped,
        "eligible": result.eligible,
    }
    if include_trades:
        payload["trades"] = [
            {
                "symbol": trade.symbol,
                "side": trade.side,
                "entry_time": trade.entry_time.isoformat(),
                "exit_time": trade.exit_time.isoformat(),
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "entry_volume_multiple": trade.entry_volume_multiple,
                "exit_reason": trade.exit_reason,
                "pnl": trade.pnl,
                "fees_and_slippage": trade.fees_and_slippage,
                "return_on_margin": trade.return_on_margin,
            }
            for trade in result.trades
        ]
    return payload


def summary_markdown(report: OptimizationReport) -> str:
    lines = [
        "# Track 1 10m Volume Impulse Weekly Optimization",
        "",
        f"- Baseline volume: previous {report.config.baseline_days} days of 10-minute candles",
        f"- Risk gate: MDD <= {report.config.max_drawdown:.0%}",
        f"- Fee + slippage: {report.config.fee_bps + report.config.slippage_bps:.1f} bps per side",
        "",
    ]
    if report.overall_best_parameters:
        best = report.overall_best_parameters
        lines.extend(
            [
                "## Overall Robust Parameter",
                "",
                (
                    f"- volume_spike_multiple={best['volume_spike_multiple']}, "
                    f"leverage={best['leverage']}, "
                    f"exit_volume_decreases={best['exit_volume_decreases']}"
                ),
                (
                    f"- eligible_periods={best['eligible_periods']}, "
                    f"average_return={best['average_return']:.4f}, "
                    f"worst_mdd={best['worst_max_drawdown']:.4f}"
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Weekly Best",
            "",
            "| Period | Return | MDD | Trades | Spike N | Leverage | Exit Decreases |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for period in report.periods:
        if period.best is None:
            lines.append(
                f"| {period.period_start.date()} to {period.period_end.date()} | "
                "n/a | n/a | 0 | n/a | n/a | n/a |"
            )
            continue
        best = period.best
        params = best.parameters
        lines.append(
            f"| {period.period_start.date()} to {period.period_end.date()} | "
            f"{best.total_return:.4f} | {best.max_drawdown:.4f} | {len(best.trades)} | "
            f"{params.volume_spike_multiple:g} | {params.leverage:g} | "
            f"{params.exit_volume_decreases} |"
        )
    lines.append("")
    return "\n".join(lines)


def overall_best_parameters(
    results_by_params: dict[ParameterSet, list[BacktestResult]],
) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for params, results in results_by_params.items():
        if not results:
            continue
        eligible = [result for result in results if result.eligible]
        if not eligible:
            continue
        rows.append(
            {
                "volume_spike_multiple": params.volume_spike_multiple,
                "leverage": params.leverage,
                "exit_volume_decreases": params.exit_volume_decreases,
                "eligible_periods": len(eligible),
                "periods_tested": len(results),
                "average_return": sum(result.total_return for result in eligible) / len(eligible),
                "minimum_return": min(result.total_return for result in eligible),
                "worst_max_drawdown": max(result.max_drawdown for result in eligible),
                "total_trade_count": sum(len(result.trades) for result in eligible),
            }
        )
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            row["eligible_periods"],
            row["average_return"],
            row["minimum_return"],
            -row["worst_max_drawdown"],
            row["total_trade_count"],
        ),
    )


def strongest_signal(
    candles: Sequence[TenMinuteCandle],
    history: dict[str, list[TenMinuteCandle]],
    parameters: ParameterSet,
    config: LabConfig,
    *,
    baseline_volumes: dict[str, float] | None = None,
) -> VolumeImpulseSignal | None:
    signals = [
        signal
        for candle in candles
        if (
            signal := signal_for_candle(
                candle,
                history[candle.symbol],
                parameters,
                config,
                baseline_volume=(baseline_volumes or {}).get(candle.symbol),
            )
        )
        is not None
    ]
    return max(
        signals,
        key=lambda item: (item.volume_multiple, abs(item.candle_return), item.symbol),
        default=None,
    )


def signal_for_candle(
    candle: TenMinuteCandle,
    history: list[TenMinuteCandle],
    parameters: ParameterSet,
    config: LabConfig,
    *,
    baseline_volume: float | None = None,
) -> VolumeImpulseSignal | None:
    if len(history) < config.baseline_window or candle.close == candle.open:
        return None
    baseline = baseline_volume
    if baseline is None:
        baseline = (
            sum(item.volume for item in history[-config.baseline_window :]) / config.baseline_window
        )
    if baseline <= 0:
        return None
    volume_multiple = candle.volume / baseline
    if volume_multiple < parameters.volume_spike_multiple:
        return None
    candle_return = (candle.close / candle.open) - 1.0 if candle.open > 0 else 0.0
    side = "long" if candle.close > candle.open else "short"
    return VolumeImpulseSignal(
        symbol=candle.symbol,
        timestamp=candle.timestamp,
        side=side,
        price=candle.close,
        candle_return=candle_return,
        volume=candle.volume,
        baseline_volume=baseline,
        volume_multiple=volume_multiple,
    )


def open_position(
    signal: VolumeImpulseSignal,
    equity: float,
    parameters: ParameterSet,
    config: LabConfig,
) -> Position:
    raw_notional = equity * parameters.leverage
    entry_cost = raw_notional * cost_rate(config)
    margin = max(0.0, equity - entry_cost)
    return Position(
        symbol=signal.symbol,
        side=signal.side,
        entry_time=signal.timestamp,
        entry_price=signal.price,
        margin=margin,
        notional=margin * parameters.leverage,
        entry_cost=entry_cost,
        entry_volume_multiple=signal.volume_multiple,
    )


def close_position(
    position: Position,
    *,
    exit_time: datetime,
    exit_price: float,
    exit_reason: str,
    config: LabConfig,
    liquidated: bool = False,
) -> tuple[Trade, float]:
    if liquidated:
        pnl = -position.margin
        exit_cost = 0.0
        final_equity = 0.0
    else:
        exit_cost = position.notional * cost_rate(config)
        pnl = (position.notional * directional_return(position, exit_price)) - exit_cost
        final_equity = max(0.0, position.margin + pnl)
    fees = position.entry_cost + exit_cost
    return_on_margin = pnl / position.margin if position.margin > 0 else 0.0
    return (
        Trade(
            symbol=position.symbol,
            side=position.side,
            entry_time=position.entry_time,
            exit_time=exit_time,
            entry_price=position.entry_price,
            exit_price=exit_price,
            entry_volume_multiple=position.entry_volume_multiple,
            exit_reason=exit_reason,
            pnl=pnl,
            fees_and_slippage=fees,
            return_on_margin=return_on_margin,
        ),
        final_equity,
    )


def update_volume_decrease(
    position: Position,
    history: list[TenMinuteCandle],
    candle: TenMinuteCandle,
) -> Position:
    if not history:
        return position
    streak = position.volume_decrease_streak + 1 if candle.volume < history[-1].volume else 0
    return Position(
        symbol=position.symbol,
        side=position.side,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        margin=position.margin,
        notional=position.notional,
        entry_cost=position.entry_cost,
        entry_volume_multiple=position.entry_volume_multiple,
        volume_decrease_streak=streak,
    )


def mark_to_market(
    position: Position | None,
    current_by_symbol: dict[str, TenMinuteCandle],
    config: LabConfig,
) -> float:
    if position is None:
        return 0.0
    candle = current_by_symbol.get(position.symbol)
    if candle is None:
        return position.margin
    exit_cost = position.notional * cost_rate(config)
    value = position.margin + (position.notional * directional_return(position, candle.close))
    return max(0.0, value - exit_cost)


def liquidation_price_for(position: Position, parameters: ParameterSet) -> float:
    threshold = 1.0 / parameters.leverage
    if position.side == "long":
        return position.entry_price * (1.0 - threshold)
    return position.entry_price * (1.0 + threshold)


def hit_liquidation(position: Position, candle: TenMinuteCandle, liquidation_price: float) -> bool:
    if position.side == "long":
        return candle.low <= liquidation_price
    return candle.high >= liquidation_price


def directional_return(position: Position, exit_price: float) -> float:
    raw = (exit_price / position.entry_price) - 1.0 if position.entry_price > 0 else -1.0
    return raw if position.side == "long" else -raw


def cost_rate(config: LabConfig) -> float:
    return (config.fee_bps + config.slippage_bps) / 10_000.0


def validate_inputs(market: Market10m, parameters: ParameterSet, config: LabConfig) -> None:
    if config.seed <= 0:
        raise ValueError("seed must be positive")
    if config.baseline_days < 1:
        raise ValueError("baseline_days must be positive")
    if config.period_days < 1:
        raise ValueError("period_days must be positive")
    if not 0 < config.max_drawdown < 1:
        raise ValueError("max_drawdown must be between 0 and 1")
    if parameters.volume_spike_multiple <= 1:
        raise ValueError("volume_spike_multiple must be greater than 1")
    if parameters.leverage <= 0:
        raise ValueError("leverage must be positive")
    if parameters.exit_volume_decreases < 1:
        raise ValueError("exit_volume_decreases must be positive")
    if not market:
        raise ValueError("market must include at least one symbol")


def _append_history(
    candle: TenMinuteCandle,
    history: dict[str, list[TenMinuteCandle]],
    volume_windows: dict[str, deque[float]],
    volume_totals: dict[str, float],
    config: LabConfig,
) -> None:
    history[candle.symbol].append(candle)
    window = volume_windows[candle.symbol]
    window.append(candle.volume)
    volume_totals[candle.symbol] += candle.volume
    if len(window) > config.baseline_window:
        volume_totals[candle.symbol] -= window.popleft()


def _baseline_volumes(
    volume_windows: dict[str, deque[float]],
    volume_totals: dict[str, float],
    config: LabConfig,
) -> dict[str, float]:
    return {
        symbol: volume_totals[symbol] / config.baseline_window
        for symbol, window in volume_windows.items()
        if len(window) >= config.baseline_window
    }


def candles_by_timestamp(market: Market10m) -> dict[datetime, list[TenMinuteCandle]]:
    by_time: dict[datetime, list[TenMinuteCandle]] = defaultdict(list)
    for candle in iter_candles(market):
        by_time[candle.timestamp].append(candle)
    return dict(by_time)


def iter_candles(market: Market10m) -> Iterable[TenMinuteCandle]:
    for symbol in sorted(market):
        yield from sorted(market[symbol], key=lambda candle: candle.timestamp)


def sort_market(market: dict[str, list[TenMinuteCandle]]) -> Market10m:
    return {
        symbol: sorted(candles, key=lambda candle: candle.timestamp)
        for symbol, candles in market.items()
    }


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _last_candle_at_or_before(
    candles: Sequence[TenMinuteCandle],
    timestamp: datetime,
) -> TenMinuteCandle:
    candidates = [candle for candle in candles if candle.timestamp <= timestamp]
    return candidates[-1] if candidates else candles[-1]


class _NullProgress:
    def update(self, value: int = 1) -> None:
        _ = value

    def close(self) -> None:
        return None


def _progress(items: range, *, enabled: bool, desc: str) -> Any:
    if not enabled:
        return _NullProgress()
    try:
        from tqdm import tqdm
    except ImportError:
        return _NullProgress()
    return tqdm(items, total=len(items), desc=desc, unit="case")


def _advance(progress: Any, case_index: int) -> None:
    if isinstance(progress, _NullProgress):
        return
    progress.n = case_index
    progress.refresh()


def _set_progress_postfix(progress: Any, **values: str) -> None:
    if isinstance(progress, _NullProgress) or not hasattr(progress, "set_postfix"):
        return
    progress.set_postfix(values)


def _close_progress(progress: Any) -> None:
    progress.close()
