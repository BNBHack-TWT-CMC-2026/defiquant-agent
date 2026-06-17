from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from defiquant.alpha import AlphaMode, latest_quote_prices, latest_quote_signals, scan_alpha_quotes
from defiquant.config import AppConfig, to_jsonable
from defiquant.execution.twak_cli import TwakCliExecutionAdapter
from defiquant.models import PortfolioState
from defiquant.risk import RiskManager

ALPHA_EVIDENCE_MODES = ("auto", "aggressive", "balanced", "defensive")


def alpha_mode_config_path(config_dir: str | Path, mode_name: str) -> Path:
    if mode_name not in ALPHA_EVIDENCE_MODES or mode_name == "auto":
        raise ValueError(f"alpha evidence mode config requires concrete mode: {mode_name}")
    return Path(config_dir) / f"strategy.{mode_name}.json"


def choose_alpha_evidence_mode(requested_mode: str, scan: dict[str, Any]) -> str:
    if requested_mode != "auto":
        if requested_mode not in ALPHA_EVIDENCE_MODES:
            raise ValueError(f"unsupported alpha evidence mode: {requested_mode}")
        return requested_mode

    recommended = scan.get("recommended_mode")
    if not isinstance(recommended, dict):
        raise ValueError("alpha scan result is missing recommended_mode")
    mode = recommended.get("mode")
    if not isinstance(mode, str) or mode not in ALPHA_EVIDENCE_MODES or mode == "auto":
        raise ValueError("alpha scan result has invalid recommended mode")
    return mode


def build_alpha_evidence(
    *,
    base_config: AppConfig,
    selected_config: AppConfig,
    selected_config_path: str | Path,
    quotes: dict[str, dict[str, Any]],
    token_addresses: dict[str, str],
    modes: dict[str, AlphaMode],
    requested_mode: str,
    selected_mode: str,
    top: int,
    portfolio_cash: float | None = None,
    generated_at: datetime | None = None,
    twak_cli_path: str | None = None,
) -> dict[str, Any]:
    scan = scan_alpha_quotes(
        quotes,
        token_addresses=token_addresses,
        top=top,
        modes=modes,
    )
    prices = latest_quote_prices(
        quotes,
        stable_symbol=selected_config.strategy.stable_symbol,
    )
    raw_signals = latest_quote_signals(
        quotes,
        token_addresses=token_addresses,
        config=selected_config.strategy,
    )
    cash = portfolio_cash if portfolio_cash is not None else selected_config.backtest.initial_cash
    portfolio = PortfolioState(cash=cash, high_watermark=cash)
    risk = RiskManager(selected_config.risk, selected_config.strategy.stable_symbol)
    signals = risk.apply(raw_signals, portfolio, prices)
    orders = risk.build_orders(signals, portfolio, prices)
    adapter = TwakCliExecutionAdapter(
        dry_run=True,
        cli_path=twak_cli_path,
        stable_symbol=selected_config.strategy.stable_symbol,
        quote_only=True,
        token_addresses=token_addresses,
    )
    dry_run_commands = adapter.execute(orders)
    timestamp = generated_at or datetime.now(UTC)

    return {
        "generated_at_utc": timestamp.astimezone(UTC).isoformat(),
        "alpha_source": "coinmarketcap_latest_quotes",
        "requested_mode": requested_mode,
        "selected_mode": selected_mode,
        "selected_config_path": str(selected_config_path),
        "universe_symbols": base_config.universe_symbols,
        "recommended_mode": scan["recommended_mode"],
        "market_breadth": scan["market_breadth"],
        "tradable_count": scan["tradable_count"],
        "top_tradable": scan["top_tradable"],
        "signals": to_jsonable(signals),
        "orders": to_jsonable(orders),
        "twak_dry_run": {
            "chain": adapter.chain,
            "quote_only": True,
            "commands": dry_run_commands,
        },
        "safety": {
            "live_transaction": False,
            "wallet_read": False,
            "token_address_allowlist": True,
            "risk_manager_applied": True,
            "stable_symbol": selected_config.strategy.stable_symbol,
        },
    }
