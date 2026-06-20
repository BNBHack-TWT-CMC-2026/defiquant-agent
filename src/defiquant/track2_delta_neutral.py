from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt
from typing import Any

from defiquant.config import AppConfig
from defiquant.indicators import max_drawdown, returns, trend_angle, volatility
from defiquant.models import Candle, MarketData


@dataclass(frozen=True, order=True)
class DeltaNeutralParams:
    variant: str
    lookback_days: int
    basket_size: int
    gross_exposure: float
    min_abs_angle: float
    max_abs_net_beta: float


@dataclass(frozen=True)
class CoinRegimeScore:
    symbol: str
    regime: str
    angle: float
    momentum: float
    beta: float
    volatility: float
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DeltaNeutralBook:
    timestamp: datetime
    market_regime: str
    weights: dict[str, float]
    long_symbols: tuple[str, ...]
    short_symbols: tuple[str, ...]
    gross_exposure: float
    net_exposure: float
    net_beta: float
    reasons: tuple[str, ...]
    coin_regimes: tuple[CoinRegimeScore, ...]


@dataclass(frozen=True)
class DeltaNeutralBacktestResult:
    parameters: DeltaNeutralParams
    initial_value: float
    final_value: float
    total_return: float
    max_drawdown: float
    sharpe: float
    trades: int
    rebalances: int
    average_abs_net_beta: float
    turnover: float
    equity_curve: tuple[float, ...]
    risk_stopped: bool

    @property
    def eligible(self) -> bool:
        return (
            self.trades > 0
            and not self.risk_stopped
            and self.max_drawdown <= 0.30
            and self.average_abs_net_beta <= self.parameters.max_abs_net_beta
        )


@dataclass(frozen=True)
class DeltaNeutralWalkForwardPeriod:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_best: DeltaNeutralBacktestResult | None
    test_result: DeltaNeutralBacktestResult | None
    train_case_count: int
    train_eligible_count: int


@dataclass(frozen=True)
class DeltaNeutralLabReport:
    parameters_tested: int
    loop_count: int
    train_days: int
    test_days: int
    step_days: int
    periods: tuple[DeltaNeutralWalkForwardPeriod, ...]
    best_candidate: dict[str, Any] | None
    latest_book: DeltaNeutralBook | None
    test_summary: dict[str, Any]


DEFAULT_VARIANTS = ("angle_momentum", "vol_adjusted", "regime_adaptive")
DEFAULT_LOOKBACK_DAYS = (7, 14, 21)
DEFAULT_BASKET_SIZES = (1, 2, 3)
DEFAULT_GROSS_EXPOSURES = (0.50, 1.00, 1.50)
DEFAULT_MIN_ABS_ANGLES = (0.02, 0.05, 0.08)
DEFAULT_MAX_ABS_NET_BETAS = (0.10, 0.20)


def build_track2_delta_neutral_lab(
    config: AppConfig,
    market: MarketData,
    *,
    train_days: int = 28,
    test_days: int = 7,
    step_days: int = 7,
    max_candidates: int = 200,
    top: int = 10,
) -> dict[str, Any]:
    candidates = generate_delta_neutral_candidates(max_candidates)
    report = walk_forward_delta_neutral(
        config,
        market,
        candidates,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
    )
    return report_to_jsonable(report, top=top)


def generate_delta_neutral_candidates(max_candidates: int) -> tuple[DeltaNeutralParams, ...]:
    if max_candidates < 1:
        raise ValueError("max_candidates must be at least 1")
    candidates = [
        DeltaNeutralParams(
            variant=variant,
            lookback_days=lookback,
            basket_size=basket_size,
            gross_exposure=gross,
            min_abs_angle=min_angle,
            max_abs_net_beta=max_beta,
        )
        for variant in DEFAULT_VARIANTS
        for lookback in DEFAULT_LOOKBACK_DAYS
        for basket_size in DEFAULT_BASKET_SIZES
        for gross in DEFAULT_GROSS_EXPOSURES
        for min_angle in DEFAULT_MIN_ABS_ANGLES
        for max_beta in DEFAULT_MAX_ABS_NET_BETAS
    ]
    if max_candidates >= len(candidates):
        return tuple(candidates)
    return tuple(_sample_evenly(candidates, max_candidates))


def walk_forward_delta_neutral(
    config: AppConfig,
    market: MarketData,
    candidates: Sequence[DeltaNeutralParams],
    *,
    train_days: int,
    test_days: int,
    step_days: int,
) -> DeltaNeutralLabReport:
    if not candidates:
        raise ValueError("delta-neutral lab requires at least one candidate")
    if train_days < 1 or test_days < 1 or step_days < 1:
        raise ValueError("train_days, test_days, and step_days must be positive")

    periods = _walk_forward_periods(
        market,
        warmup_days=max(candidate.lookback_days for candidate in candidates),
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
    )
    results: list[DeltaNeutralWalkForwardPeriod] = []
    selected: dict[DeltaNeutralParams, list[DeltaNeutralBacktestResult]] = defaultdict(list)
    loop_count = 0

    for train_start, train_end, test_start, test_end in periods:
        train_results = [
            run_delta_neutral_backtest(
                config,
                market,
                params,
                period_start=train_start,
                period_end=train_end,
            )
            for params in candidates
        ]
        loop_count += len(train_results)
        eligible = [result for result in train_results if result.eligible]
        train_best = _best_result(eligible)
        test_result = None
        if train_best is not None:
            test_result = run_delta_neutral_backtest(
                config,
                market,
                train_best.parameters,
                period_start=test_start,
                period_end=test_end,
            )
            selected[train_best.parameters].append(test_result)
            loop_count += 1
        results.append(
            DeltaNeutralWalkForwardPeriod(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                train_best=train_best,
                test_result=test_result,
                train_case_count=len(train_results),
                train_eligible_count=len(eligible),
            )
        )

    best_candidate = _best_candidate(selected)
    latest_book = None
    if best_candidate is not None:
        params = _params_from_jsonable(best_candidate["parameters"])
        latest_book = build_delta_neutral_book(config, market, params)

    return DeltaNeutralLabReport(
        parameters_tested=len(candidates),
        loop_count=loop_count,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        periods=tuple(results),
        best_candidate=best_candidate,
        latest_book=latest_book,
        test_summary=_test_summary(tuple(results)),
    )


def run_delta_neutral_backtest(
    config: AppConfig,
    market: MarketData,
    parameters: DeltaNeutralParams,
    *,
    period_start: datetime,
    period_end: datetime,
) -> DeltaNeutralBacktestResult:
    _validate_parameters(parameters)
    prepared = _prepare_market(market, stable_symbol=config.strategy.stable_symbol)
    timestamps = sorted({candle.timestamp for candles in prepared.values() for candle in candles})
    if not timestamps:
        raise ValueError("market data is empty")
    prices_by_time = _prices_by_time(prepared)
    history_by_time = _history_by_time(prepared)
    equity = config.backtest.initial_cash
    high_watermark = equity
    equity_curve: list[float] = []
    current_weights: dict[str, float] = {}
    previous_prices: dict[str, float] = {}
    trades = 0
    rebalances = 0
    turnover_total = 0.0
    abs_net_betas: list[float] = []
    risk_stopped = False
    cost_rate = (config.risk.fee_bps + config.risk.slippage_bps) / 10_000.0

    for timestamp in timestamps:
        prices = prices_by_time[timestamp]
        in_period = period_start <= timestamp < period_end
        if in_period and previous_prices and current_weights:
            daily_return = _weighted_return(current_weights, previous_prices, prices)
            equity = max(0.0, equity * (1.0 + daily_return))
            high_watermark = max(high_watermark, equity)
            equity_curve.append(equity)
            if _drawdown(equity, high_watermark) > config.risk.max_drawdown:
                risk_stopped = True
                break
        elif in_period:
            equity_curve.append(equity)

        if in_period and _should_rebalance(timestamp, period_start, config):
            book = build_delta_neutral_book(
                config,
                history_by_time[timestamp],
                parameters,
                timestamp=timestamp,
            )
            target_weights = book.weights
            turnover = _turnover(current_weights, target_weights)
            if turnover > 0:
                equity = max(0.0, equity * (1.0 - (turnover * cost_rate)))
                trades += _trade_count(current_weights, target_weights)
                rebalances += 1
                turnover_total += turnover
                abs_net_betas.append(abs(book.net_beta))
                if equity_curve:
                    equity_curve[-1] = equity
            current_weights = target_weights

        previous_prices = prices

    if not equity_curve:
        equity_curve = [config.backtest.initial_cash]
    return DeltaNeutralBacktestResult(
        parameters=parameters,
        initial_value=config.backtest.initial_cash,
        final_value=equity_curve[-1],
        total_return=(equity_curve[-1] / config.backtest.initial_cash) - 1.0,
        max_drawdown=max_drawdown(equity_curve),
        sharpe=_sharpe(equity_curve),
        trades=trades,
        rebalances=rebalances,
        average_abs_net_beta=sum(abs_net_betas) / len(abs_net_betas) if abs_net_betas else 0.0,
        turnover=turnover_total,
        equity_curve=tuple(equity_curve),
        risk_stopped=risk_stopped,
    )


def build_delta_neutral_book(
    config: AppConfig,
    market: MarketData,
    parameters: DeltaNeutralParams,
    *,
    timestamp: datetime | None = None,
) -> DeltaNeutralBook:
    _validate_parameters(parameters)
    prepared = _prepare_market(market, stable_symbol=config.strategy.stable_symbol)
    raw_scores = _coin_scores(prepared, parameters)
    if not raw_scores:
        return DeltaNeutralBook(
            timestamp=timestamp or _latest_timestamp(prepared),
            market_regime="insufficient_data",
            weights={},
            long_symbols=(),
            short_symbols=(),
            gross_exposure=0.0,
            net_exposure=0.0,
            net_beta=0.0,
            reasons=("insufficient_data=true",),
            coin_regimes=(),
        )

    market_regime = _market_regime(prepared, parameters)
    scores = [
        _replace_score(
            score,
            _score_coin(
                variant=parameters.variant,
                angle=score.angle,
                momentum=score.momentum,
                vol=score.volatility,
                beta=score.beta,
                market_regime=market_regime,
            ),
        )
        for score in raw_scores
    ]
    longs, shorts = _select_long_short(scores, parameters, market_regime)
    weights = _beta_neutral_weights(longs, shorts, parameters.gross_exposure)
    gross = sum(abs(weight) for weight in weights.values())
    net = sum(weights.values())
    net_beta = _net_beta(weights, {score.symbol: score.beta for score in scores})
    reasons = (
        f"market_regime={market_regime}",
        f"variant={parameters.variant}",
        f"lookback_days={parameters.lookback_days}",
        f"basket_size={parameters.basket_size}",
        f"gross_exposure={gross:.4f}",
        f"net_exposure={net:.4f}",
        f"net_beta={net_beta:.4f}",
    )
    return DeltaNeutralBook(
        timestamp=timestamp or _latest_timestamp(prepared),
        market_regime=market_regime,
        weights=weights,
        long_symbols=tuple(score.symbol for score in longs),
        short_symbols=tuple(score.symbol for score in shorts),
        gross_exposure=gross,
        net_exposure=net,
        net_beta=net_beta,
        reasons=reasons,
        coin_regimes=tuple(scores),
    )


def report_to_jsonable(report: DeltaNeutralLabReport, *, top: int) -> dict[str, Any]:
    return {
        "mode": "track2_delta_neutral_lab",
        "execution": "disabled",
        "methodology": {
            "objective": (
                "Walk-forward search over non-executing delta-neutral CMC strategy specs"
            ),
            "regime_model": (
                "Coin trend angles classify up_channel, down_channel, and transition regimes; "
                "equal-weight market angle classifies bull, bear, or mixed market state."
            ),
            "portfolio_construction": (
                "Long strongest rising-angle basket and short weakest falling-angle basket; "
                "short leg is scaled to reduce market beta."
            ),
            "cost_model": "Configured fee_bps plus slippage_bps applied to turnover.",
            "selection": (
                "Train-period best risk-adjusted score is evaluated only on the next "
                "out-of-sample test period."
            ),
        },
        "safety": {
            "wallet_access": "none",
            "transaction_signing": "disabled",
            "orders": "not emitted",
            "output_use": "Track 2 strategy research and CMC Skill rationale only",
        },
        "parameters_tested": report.parameters_tested,
        "loop_count": report.loop_count,
        "walk_forward": {
            "train_days": report.train_days,
            "test_days": report.test_days,
            "step_days": report.step_days,
        },
        "test_summary": report.test_summary,
        "best_candidate": report.best_candidate,
        "latest_strategy_spec": _book_to_jsonable(report.latest_book),
        "periods": [_period_to_jsonable(period) for period in report.periods[: max(1, top)]],
    }


def _coin_scores(
    market: MarketData,
    parameters: DeltaNeutralParams,
) -> list[CoinRegimeScore]:
    index_returns = _market_index_returns(market)
    scores: list[CoinRegimeScore] = []
    for symbol, candles in sorted(market.items()):
        clean = _sorted_positive(candles)
        if len(clean) < parameters.lookback_days + 2:
            continue
        prices = [candle.close for candle in clean]
        sample = prices[-parameters.lookback_days - 1 :]
        momentum = _safe_return(sample[0], sample[-1])
        angle = trend_angle(prices, parameters.lookback_days)
        vol = volatility(sample)
        beta = _beta(returns(sample), index_returns[-parameters.lookback_days :])
        regime = _coin_regime(angle, momentum, parameters.min_abs_angle)
        score = _score_coin(
            variant=parameters.variant,
            angle=angle,
            momentum=momentum,
            vol=vol,
            beta=beta,
            market_regime="",
        )
        scores.append(
            CoinRegimeScore(
                symbol=symbol,
                regime=regime,
                angle=angle,
                momentum=momentum,
                beta=beta,
                volatility=vol,
                score=score,
                reasons=(
                    f"regime={regime}",
                    f"trend_angle={angle:.4f}",
                    f"momentum={momentum:.4f}",
                    f"beta={beta:.4f}",
                    f"volatility={vol:.4f}",
                    f"score={score:.4f}",
                ),
            )
        )
    return scores


def _select_long_short(
    scores: list[CoinRegimeScore],
    parameters: DeltaNeutralParams,
    market_regime: str,
) -> tuple[list[CoinRegimeScore], list[CoinRegimeScore]]:
    if market_regime == "bull":
        long_pool = [score for score in scores if score.regime != "down_channel"]
        short_pool = [score for score in scores if score.regime != "up_channel"]
    elif market_regime == "bear":
        long_pool = [score for score in scores if score.regime != "down_channel"]
        short_pool = [score for score in scores if score.regime == "down_channel"]
    else:
        long_pool = scores
        short_pool = scores

    longs = sorted(long_pool, key=lambda item: item.score, reverse=True)[: parameters.basket_size]
    long_symbols = {score.symbol for score in longs}
    shorts = [
        score
        for score in sorted(short_pool, key=lambda item: item.score)
        if score.symbol not in long_symbols
    ][: parameters.basket_size]

    if not longs or not shorts:
        fallback = sorted(scores, key=lambda item: item.score, reverse=True)
        half = max(1, min(parameters.basket_size, len(fallback) // 2))
        longs = fallback[:half]
        shorts = list(reversed(fallback[-half:]))
    return longs, shorts


def _beta_neutral_weights(
    longs: Sequence[CoinRegimeScore],
    shorts: Sequence[CoinRegimeScore],
    gross_exposure: float,
) -> dict[str, float]:
    if not longs or not shorts:
        return {}
    long_beta = sum(max(0.05, abs(score.beta)) for score in longs) / len(longs)
    short_beta = sum(max(0.05, abs(score.beta)) for score in shorts) / len(shorts)
    short_scale = long_beta / short_beta if short_beta > 0 else 1.0
    long_gross = gross_exposure / (1.0 + short_scale)
    short_gross = gross_exposure - long_gross
    weights: dict[str, float] = {}
    for score in longs:
        weights[score.symbol] = long_gross / len(longs)
    for score in shorts:
        weights[score.symbol] = -(short_gross / len(shorts))
    return weights


def _market_regime(market: MarketData, parameters: DeltaNeutralParams) -> str:
    index = _market_index(market)
    if len(index) < parameters.lookback_days + 2:
        return "mixed"
    prices = [candle.close for candle in index]
    angle = trend_angle(prices, parameters.lookback_days)
    momentum = _safe_return(prices[-parameters.lookback_days - 1], prices[-1])
    vol = volatility(prices[-parameters.lookback_days - 1 :])
    if angle > parameters.min_abs_angle and momentum > 0 and vol < 0.12:
        return "bull"
    if angle < -parameters.min_abs_angle and momentum < 0:
        return "bear"
    return "mixed"


def _coin_regime(angle: float, momentum: float, min_abs_angle: float) -> str:
    if angle >= min_abs_angle and momentum > 0:
        return "up_channel"
    if angle <= -min_abs_angle and momentum < 0:
        return "down_channel"
    return "range_or_transition"


def _score_coin(
    *,
    variant: str,
    angle: float,
    momentum: float,
    vol: float,
    beta: float,
    market_regime: str,
) -> float:
    if variant == "angle_momentum":
        return (0.60 * angle) + (0.40 * momentum) - (0.20 * vol)
    if variant == "vol_adjusted":
        return ((0.50 * angle) + (0.50 * momentum)) / max(vol, 0.01)
    if variant == "regime_adaptive":
        regime_bias = 0.10 if market_regime == "bull" else -0.10 if market_regime == "bear" else 0.0
        return (0.45 * angle) + (0.35 * momentum) - (0.15 * vol) - (0.05 * abs(beta)) + regime_bias
    raise ValueError(f"unknown delta-neutral variant: {variant}")


def _prepare_market(market: MarketData, *, stable_symbol: str) -> MarketData:
    prepared: MarketData = {}
    for symbol, candles in market.items():
        if symbol == stable_symbol:
            continue
        clean = _sorted_positive(candles)
        if clean:
            prepared[symbol] = clean
    return prepared


def _history_by_time(market: MarketData) -> dict[datetime, MarketData]:
    timestamps = sorted({candle.timestamp for candles in market.values() for candle in candles})
    output: dict[datetime, MarketData] = {}
    for timestamp in timestamps:
        output[timestamp] = {
            symbol: [candle for candle in candles if candle.timestamp <= timestamp]
            for symbol, candles in market.items()
        }
    return output


def _prices_by_time(market: MarketData) -> dict[datetime, dict[str, float]]:
    timestamps = sorted({candle.timestamp for candles in market.values() for candle in candles})
    output: dict[datetime, dict[str, float]] = {}
    for timestamp in timestamps:
        prices: dict[str, float] = {}
        for symbol, candles in market.items():
            prior = [candle for candle in candles if candle.timestamp <= timestamp]
            if prior:
                prices[symbol] = prior[-1].close
        output[timestamp] = prices
    return output


def _walk_forward_periods(
    market: MarketData,
    *,
    warmup_days: int,
    train_days: int,
    test_days: int,
    step_days: int,
) -> tuple[tuple[datetime, datetime, datetime, datetime], ...]:
    timestamps = sorted({candle.timestamp for candles in market.values() for candle in candles})
    if len(timestamps) <= warmup_days + train_days + test_days:
        return ()
    cursor = timestamps[min(warmup_days, len(timestamps) - 1)]
    end = timestamps[-1] + timedelta(days=1)
    periods: list[tuple[datetime, datetime, datetime, datetime]] = []
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        periods.append((train_start, train_end, test_start, test_end))
        cursor += timedelta(days=step_days)
    return tuple(periods)


def _weighted_return(
    weights: Mapping[str, float],
    previous_prices: Mapping[str, float],
    prices: Mapping[str, float],
) -> float:
    total = 0.0
    for symbol, weight in weights.items():
        previous = previous_prices.get(symbol)
        current = prices.get(symbol)
        if previous and current and previous > 0:
            total += weight * ((current / previous) - 1.0)
    return total


def _market_index(market: MarketData) -> list[Candle]:
    timestamps = sorted({candle.timestamp for candles in market.values() for candle in candles})
    rows: list[Candle] = []
    for timestamp in timestamps:
        prices = []
        volumes = []
        for candles in market.values():
            prior = [candle for candle in candles if candle.timestamp <= timestamp]
            if prior:
                prices.append(prior[-1].close)
                volumes.append(prior[-1].volume)
        if prices:
            close = sum(prices) / len(prices)
            rows.append(
                Candle(
                    symbol="MARKET",
                    timestamp=timestamp,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=sum(volumes),
                )
            )
    return rows


def _market_index_returns(market: MarketData) -> list[float]:
    return returns([candle.close for candle in _market_index(market)])


def _beta(asset_returns: Sequence[float], index_returns: Sequence[float]) -> float:
    count = min(len(asset_returns), len(index_returns))
    if count < 2:
        return 1.0
    x = list(index_returns[-count:])
    y = list(asset_returns[-count:])
    x_mean = sum(x) / count
    y_mean = sum(y) / count
    variance = sum((value - x_mean) ** 2 for value in x)
    if variance <= 0:
        return 1.0
    covariance = sum(
        (x_item - x_mean) * (y_item - y_mean) for x_item, y_item in zip(x, y, strict=True)
    )
    return covariance / variance


def _net_beta(weights: Mapping[str, float], betas: Mapping[str, float]) -> float:
    return sum(weight * betas.get(symbol, 1.0) for symbol, weight in weights.items())


def _turnover(current: Mapping[str, float], target: Mapping[str, float]) -> float:
    symbols = set(current) | set(target)
    return sum(abs(target.get(symbol, 0.0) - current.get(symbol, 0.0)) for symbol in symbols)


def _trade_count(current: Mapping[str, float], target: Mapping[str, float]) -> int:
    symbols = set(current) | set(target)
    return sum(
        1 for symbol in symbols if abs(target.get(symbol, 0.0) - current.get(symbol, 0.0)) > 1e-9
    )


def _should_rebalance(timestamp: datetime, period_start: datetime, config: AppConfig) -> bool:
    elapsed_days = max(0, (timestamp.date() - period_start.date()).days)
    return elapsed_days % max(1, config.backtest.rebalance_every_days) == 0


def _best_result(
    results: Sequence[DeltaNeutralBacktestResult],
) -> DeltaNeutralBacktestResult | None:
    return max(
        results,
        key=lambda result: (
            _risk_adjusted_score(result),
            result.total_return,
            -result.max_drawdown,
            -result.average_abs_net_beta,
            result.trades,
        ),
        default=None,
    )


def _best_candidate(
    selected: Mapping[DeltaNeutralParams, Sequence[DeltaNeutralBacktestResult]],
) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for params, results in selected.items():
        if not results:
            continue
        returns_ = [result.total_return for result in results]
        eligible = [result for result in results if result.eligible]
        rows.append(
            {
                "parameters": _params_to_jsonable(params),
                "selected_periods": len(results),
                "eligible_test_periods": len(eligible),
                "average_test_return": round(sum(returns_) / len(returns_), 8),
                "minimum_test_return": round(min(returns_), 8),
                "worst_test_max_drawdown": round(max(result.max_drawdown for result in results), 8),
                "average_abs_net_beta": round(
                    sum(result.average_abs_net_beta for result in results) / len(results),
                    8,
                ),
                "total_test_trades": sum(result.trades for result in results),
            }
        )
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            row["eligible_test_periods"],
            row["average_test_return"],
            row["minimum_test_return"],
            -row["worst_test_max_drawdown"],
            -row["average_abs_net_beta"],
        ),
    )


def _test_summary(periods: Sequence[DeltaNeutralWalkForwardPeriod]) -> dict[str, Any]:
    tests = [period.test_result for period in periods if period.test_result is not None]
    returns_ = [result.total_return for result in tests]
    if not tests:
        return {
            "period_count": len(periods),
            "tested_period_count": 0,
            "average_test_return": 0.0,
            "median_test_return": 0.0,
            "test_return_t_stat": 0.0,
            "profitable_test_pct": 0.0,
            "eligible_test_pct": 0.0,
            "worst_test_max_drawdown": 0.0,
            "average_abs_net_beta": 0.0,
            "total_test_trades": 0,
        }
    return {
        "period_count": len(periods),
        "tested_period_count": len(tests),
        "average_test_return": round(sum(returns_) / len(returns_), 8),
        "median_test_return": round(_median(returns_), 8),
        "test_return_t_stat": round(_t_stat(returns_), 8),
        "profitable_test_pct": round(sum(1 for value in returns_ if value > 0) / len(tests), 8),
        "eligible_test_pct": round(sum(1 for result in tests if result.eligible) / len(tests), 8),
        "worst_test_max_drawdown": round(max(result.max_drawdown for result in tests), 8),
        "average_abs_net_beta": round(
            sum(result.average_abs_net_beta for result in tests) / len(tests),
            8,
        ),
        "total_test_trades": sum(result.trades for result in tests),
    }


def _period_to_jsonable(period: DeltaNeutralWalkForwardPeriod) -> dict[str, Any]:
    return {
        "train_start": period.train_start.isoformat(),
        "train_end": period.train_end.isoformat(),
        "test_start": period.test_start.isoformat(),
        "test_end": period.test_end.isoformat(),
        "train_case_count": period.train_case_count,
        "train_eligible_count": period.train_eligible_count,
        "train_best": _result_to_jsonable(period.train_best),
        "test_result": _result_to_jsonable(period.test_result),
    }


def _result_to_jsonable(result: DeltaNeutralBacktestResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "parameters": _params_to_jsonable(result.parameters),
        "initial_value": round(result.initial_value, 8),
        "final_value": round(result.final_value, 8),
        "total_return": round(result.total_return, 8),
        "max_drawdown": round(result.max_drawdown, 8),
        "sharpe": round(result.sharpe, 8),
        "trades": result.trades,
        "rebalances": result.rebalances,
        "average_abs_net_beta": round(result.average_abs_net_beta, 8),
        "turnover": round(result.turnover, 8),
        "risk_stopped": result.risk_stopped,
        "eligible": result.eligible,
    }


def _book_to_jsonable(book: DeltaNeutralBook | None) -> dict[str, Any] | None:
    if book is None:
        return None
    return {
        "timestamp": book.timestamp.isoformat(),
        "market_regime": book.market_regime,
        "weights": {symbol: round(weight, 8) for symbol, weight in sorted(book.weights.items())},
        "long_symbols": list(book.long_symbols),
        "short_symbols": list(book.short_symbols),
        "gross_exposure": round(book.gross_exposure, 8),
        "net_exposure": round(book.net_exposure, 8),
        "net_beta": round(book.net_beta, 8),
        "reasons": list(book.reasons),
        "coin_regimes": [
            {
                "symbol": score.symbol,
                "regime": score.regime,
                "angle": round(score.angle, 8),
                "momentum": round(score.momentum, 8),
                "beta": round(score.beta, 8),
                "volatility": round(score.volatility, 8),
                "score": round(score.score, 8),
                "reasons": list(score.reasons),
            }
            for score in book.coin_regimes
        ],
    }


def _params_to_jsonable(params: DeltaNeutralParams) -> dict[str, Any]:
    return {
        "variant": params.variant,
        "lookback_days": params.lookback_days,
        "basket_size": params.basket_size,
        "gross_exposure": params.gross_exposure,
        "min_abs_angle": params.min_abs_angle,
        "max_abs_net_beta": params.max_abs_net_beta,
    }


def _params_from_jsonable(payload: Mapping[str, Any]) -> DeltaNeutralParams:
    return DeltaNeutralParams(
        variant=str(payload["variant"]),
        lookback_days=int(payload["lookback_days"]),
        basket_size=int(payload["basket_size"]),
        gross_exposure=float(payload["gross_exposure"]),
        min_abs_angle=float(payload["min_abs_angle"]),
        max_abs_net_beta=float(payload["max_abs_net_beta"]),
    )


def _validate_parameters(parameters: DeltaNeutralParams) -> None:
    if parameters.variant not in DEFAULT_VARIANTS:
        raise ValueError(f"unsupported variant: {parameters.variant}")
    if parameters.lookback_days < 2:
        raise ValueError("lookback_days must be at least 2")
    if parameters.basket_size < 1:
        raise ValueError("basket_size must be positive")
    if parameters.gross_exposure <= 0:
        raise ValueError("gross_exposure must be positive")
    if parameters.min_abs_angle < 0:
        raise ValueError("min_abs_angle cannot be negative")
    if parameters.max_abs_net_beta <= 0:
        raise ValueError("max_abs_net_beta must be positive")


def _risk_adjusted_score(result: DeltaNeutralBacktestResult) -> float:
    return (
        result.total_return
        - (1.5 * result.max_drawdown)
        + (0.03 * result.sharpe)
        - (0.25 * result.average_abs_net_beta)
    )


def _sharpe(equity_curve: Sequence[float]) -> float:
    samples = returns(list(equity_curve))
    if len(samples) < 2:
        return 0.0
    avg = sum(samples) / len(samples)
    variance = sum((sample - avg) ** 2 for sample in samples) / (len(samples) - 1)
    if variance <= 0:
        return 0.0
    return (avg / sqrt(variance)) * sqrt(365)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _t_stat(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    if variance <= 0:
        return 0.0
    return avg / (sqrt(variance) / sqrt(len(values)))


def _drawdown(equity: float, high_watermark: float) -> float:
    return 1.0 - (equity / high_watermark) if high_watermark > 0 else 0.0


def _safe_return(previous: float, current: float) -> float:
    return (current / previous) - 1.0 if previous > 0 else 0.0


def _sorted_positive(candles: Sequence[Candle]) -> list[Candle]:
    return sorted(
        [
            candle
            for candle in candles
            if candle.close > 0 and candle.high > 0 and candle.low > 0 and candle.volume >= 0
        ],
        key=lambda candle: candle.timestamp,
    )


def _latest_timestamp(market: MarketData) -> datetime:
    return max(candle.timestamp for candles in market.values() for candle in candles)


def _replace_score(score: CoinRegimeScore, value: float) -> CoinRegimeScore:
    return CoinRegimeScore(
        symbol=score.symbol,
        regime=score.regime,
        angle=score.angle,
        momentum=score.momentum,
        beta=score.beta,
        volatility=score.volatility,
        score=value,
        reasons=(
            *score.reasons[:-1],
            f"score={value:.4f}",
        ),
    )


def _sample_evenly(
    candidates: Sequence[DeltaNeutralParams],
    count: int,
) -> list[DeltaNeutralParams]:
    if count <= 0:
        return []
    if count >= len(candidates):
        return list(candidates)
    if count == 1:
        return [candidates[0]]
    last_index = len(candidates) - 1
    return [candidates[(index * last_index) // (count - 1)] for index in range(count)]
