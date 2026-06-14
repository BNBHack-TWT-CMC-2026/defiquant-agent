from __future__ import annotations

from defiquant.execution.twak_cli import TwakCliExecutionAdapter
from defiquant.models import Order


def test_twak_dry_run_builds_swap_commands() -> None:
    adapter = TwakCliExecutionAdapter(
        dry_run=True,
        cli_path="twak",
        chain="bsc",
        stable_symbol="USDT",
        slippage_percent=0.5,
        quote_only=True,
        token_addresses={
            "AAVE": "0xAave",
            "CAKE": "0xCake",
            "USDT": "0xUsdt",
        },
    )

    results = adapter.execute(
        [
            Order("CAKE", "buy", 100.0, 0.1, "rebalance", source_amount=100.0),
            Order("AAVE", "sell", 80.0, 0.0, "rebalance", source_amount=0.25),
        ]
    )

    assert results == [
        'twak-dry-run:["twak","swap","100","0xUsdt","0xCake","--chain","bsc",'
        '"--slippage","0.5","--json","--quote-only"]',
        'twak-dry-run:["twak","swap","0.25","0xAave","0xUsdt","--chain","bsc",'
        '"--slippage","0.5","--json","--quote-only"]',
    ]


def test_twak_competition_registration_dry_run() -> None:
    adapter = TwakCliExecutionAdapter(dry_run=True, cli_path="twak")

    assert adapter.register_competition() == 'twak-dry-run:["twak","compete","register"]'


def test_twak_cli_prefix_accepts_npx_command() -> None:
    adapter = TwakCliExecutionAdapter(
        dry_run=True,
        cli_path="npx @trustwallet/cli",
        chain="bsc",
    )

    assert (
        adapter.wallet_address() == 'twak-dry-run:["npx","@trustwallet/cli","wallet","address",'
        '"--chain","bsc","--json"]'
    )


def test_twak_bsc_swap_requires_token_addresses() -> None:
    adapter = TwakCliExecutionAdapter(
        dry_run=True,
        cli_path="twak",
        chain="bsc",
        stable_symbol="USDT",
        token_addresses={"USDT": "0xUsdt"},
    )

    try:
        adapter.execute([Order("CAKE", "buy", 100.0, 0.1, "rebalance")])
    except ValueError as exc:
        assert str(exc) == "Missing BSC token address for CAKE"
    else:
        raise AssertionError("Expected missing token address to fail closed")
