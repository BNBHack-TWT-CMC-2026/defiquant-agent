from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from defiquant.alpha import load_alpha_modes
from defiquant.alpha_evidence import (
    alpha_mode_config_path,
    build_alpha_evidence,
    choose_alpha_evidence_mode,
)
from defiquant.config import load_config


def test_choose_alpha_evidence_mode_uses_scan_recommendation() -> None:
    scan = {"recommended_mode": {"mode": "aggressive"}}

    assert choose_alpha_evidence_mode("auto", scan) == "aggressive"
    assert choose_alpha_evidence_mode("defensive", scan) == "defensive"


def test_alpha_mode_config_path_requires_concrete_mode() -> None:
    assert alpha_mode_config_path("configs", "balanced") == Path("configs/strategy.balanced.json")

    with pytest.raises(ValueError, match="concrete mode"):
        alpha_mode_config_path("configs", "auto")


def test_build_alpha_evidence_outputs_safe_dry_run_packet() -> None:
    base_config = load_config("configs/strategy.json")
    selected_config = load_config("configs/strategy.aggressive.json")
    modes = load_alpha_modes("configs/alpha_modes.json")

    evidence = build_alpha_evidence(
        base_config=base_config,
        selected_config=selected_config,
        selected_config_path="configs/strategy.aggressive.json",
        quotes={
            "CAKE": _quote(change_1h=2.0, change_24h=8.0, change_7d=12.0),
            "LINK": _quote(change_1h=0.2, change_24h=2.0, change_7d=5.0),
            "USDT": _quote(change_1h=0.0, change_24h=0.0, change_7d=0.0),
        },
        token_addresses={"CAKE": "0xCake", "LINK": "0xLink", "USDT": "0xUsdt"},
        modes=modes,
        requested_mode="auto",
        selected_mode="aggressive",
        top=2,
        portfolio_cash=1000.0,
        generated_at=datetime(2026, 6, 17, tzinfo=UTC),
        twak_cli_path="twak",
    )

    assert evidence["generated_at_utc"] == "2026-06-17T00:00:00+00:00"
    assert evidence["selected_mode"] == "aggressive"
    assert evidence["top_tradable"][0]["symbol"] == "CAKE"
    assert evidence["signals"][0]["symbol"] == "CAKE"
    assert evidence["orders"]
    assert evidence["twak_dry_run"]["quote_only"] is True
    assert evidence["twak_dry_run"]["commands"][0].startswith("twak-dry-run:")
    assert evidence["safety"] == {
        "live_transaction": False,
        "wallet_read": False,
        "token_address_allowlist": True,
        "risk_manager_applied": True,
        "stable_symbol": "USDT",
    }


def test_alpha_evidence_cli_uses_latest_quotes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from defiquant.cli import main

    def fake_load_quotes(symbols: tuple[str, ...]) -> dict[str, dict[str, object]]:
        assert symbols == ("CAKE", "TWT", "AAVE", "LINK", "PENDLE", "USDT")
        return {
            "CAKE": _quote(change_1h=2.0, change_24h=8.0, change_7d=12.0),
            "LINK": _quote(change_1h=0.2, change_24h=2.0, change_7d=5.0),
            "USDT": _quote(change_1h=0.0, change_24h=0.0, change_7d=0.0),
        }

    monkeypatch.setattr("defiquant.cli.load_cmc_latest_quotes", fake_load_quotes)
    monkeypatch.setenv("TWAK_CLI", "twak")
    monkeypatch.setattr(
        sys,
        "argv",
        ["defiquant", "alpha-evidence", "--mode", "auto", "--portfolio-cash", "1000"],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["alpha_source"] == "coinmarketcap_latest_quotes"
    assert payload["selected_mode"] == "aggressive"
    assert payload["signals"][0]["symbol"] == "CAKE"
    assert payload["twak_dry_run"]["commands"]
    assert payload["safety"]["live_transaction"] is False


def _quote(*, change_1h: float, change_24h: float, change_7d: float) -> dict[str, object]:
    return {
        "price": 1.0,
        "volume_24h": 10_000_000,
        "market_cap": 100_000_000,
        "percent_change_1h": change_1h,
        "percent_change_24h": change_24h,
        "percent_change_7d": change_7d,
    }
