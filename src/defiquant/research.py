from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from defiquant.backtest import Backtester, BacktestResult
from defiquant.config import AppConfig
from defiquant.models import MarketData
from defiquant.risk import RiskManager
from defiquant.strategy import MomentumLiquidityStrategy


def build_research_report(
    configs: Mapping[str, AppConfig],
    markets_by_window: Mapping[int, MarketData],
) -> dict[str, Any]:
    if not configs:
        raise ValueError("research report requires at least one strategy config")
    if not markets_by_window:
        raise ValueError("research report requires at least one market window")

    window_results = [
        _evaluate_window(config_name, config, window_days, market)
        for window_days, market in sorted(markets_by_window.items())
        for config_name, config in configs.items()
    ]
    summary = [_summarize_config(config_name, window_results) for config_name in configs]
    ranked = sorted(
        summary,
        key=lambda item: (
            item["eligible_windows"],
            item["average_risk_adjusted_score"],
            item["minimum_total_return"],
            -item["worst_max_drawdown"],
        ),
        reverse=True,
    )
    return {
        "methodology": {
            "ranking": (
                "eligible_windows, average_risk_adjusted_score, "
                "minimum_total_return, lowest_worst_drawdown"
            ),
            "risk_adjusted_score": "total_return - 1.5 * max_drawdown + 0.03 * sharpe",
            "eligible": "meets minimum trade days and stays within configured max_drawdown",
        },
        "windows": sorted(markets_by_window),
        "recommended_config": ranked[0]["config"] if ranked else "",
        "summary": ranked,
        "window_results": window_results,
    }


def _evaluate_window(
    config_name: str,
    config: AppConfig,
    window_days: int,
    market: MarketData,
) -> dict[str, Any]:
    result = Backtester(
        MomentumLiquidityStrategy(config.strategy),
        RiskManager(config.risk, config.strategy.stable_symbol),
        config.backtest,
        min_trades_per_day=config.competition.min_trades_per_day,
        min_total_trade_days=config.competition.min_total_trade_days,
    ).run(market)
    eligible = result.meets_min_trade_days and result.max_drawdown <= config.risk.max_drawdown
    return {
        "config": config_name,
        "window_days": window_days,
        "eligible": eligible,
        "risk_adjusted_score": round(_risk_adjusted_score(result), 8),
        "total_return": round(result.total_return, 8),
        "max_drawdown": round(result.max_drawdown, 8),
        "drawdown_cap": config.risk.max_drawdown,
        "sharpe": round(result.sharpe, 8),
        "trades": result.trades,
        "qualified_trade_days": result.qualified_trade_days,
        "meets_min_trade_days": result.meets_min_trade_days,
        "final_value": round(result.final_value, 8),
        "risk": {
            "max_position_weight": config.risk.max_position_weight,
            "min_cash_weight": config.risk.min_cash_weight,
            "max_daily_turnover": config.risk.max_daily_turnover,
        },
        "strategy": {
            "top_n": config.strategy.top_n,
            "min_score": config.strategy.min_score,
        },
    }


def _summarize_config(config_name: str, window_results: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in window_results if row["config"] == config_name]
    if not rows:
        raise ValueError(f"research report has no rows for config {config_name}")
    return {
        "config": config_name,
        "eligible_windows": sum(1 for row in rows if row["eligible"]),
        "total_windows": len(rows),
        "average_risk_adjusted_score": round(
            sum(float(row["risk_adjusted_score"]) for row in rows) / len(rows),
            8,
        ),
        "minimum_total_return": round(min(float(row["total_return"]) for row in rows), 8),
        "worst_max_drawdown": round(max(float(row["max_drawdown"]) for row in rows), 8),
        "total_trades": sum(int(row["trades"]) for row in rows),
        "minimum_qualified_trade_days": min(int(row["qualified_trade_days"]) for row in rows),
    }


def _risk_adjusted_score(result: BacktestResult) -> float:
    return result.total_return - (1.5 * result.max_drawdown) + (0.03 * result.sharpe)
