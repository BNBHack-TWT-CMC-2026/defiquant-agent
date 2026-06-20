from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from defiquant.data.cmc import (
    CmcClient,
    cmc_credit_budget,
    estimate_historical_ohlcv_credits,
    load_cmc_latest_quotes,
    load_cmc_market,
    parse_latest_quotes,
    parse_ohlcv,
)


def test_parse_ohlcv_accepts_direct_cmc_payload() -> None:
    market = parse_ohlcv("CAKE", _payload("CAKE"))

    assert [candle.close for candle in market["CAKE"]] == [2.5, 2.75]
    assert market["CAKE"][0].timestamp.isoformat() == "2026-06-10T00:00:00+00:00"
    assert market["CAKE"][1].market_cap == 2_750_000


def test_parse_ohlcv_accepts_wrapped_cmc_payload() -> None:
    payload = {"status": {"error_code": 0}, "data": {"7186": _payload("CAKE")["data"]}}

    market = parse_ohlcv("CAKE", payload)

    assert len(market["CAKE"]) == 2


def test_parse_ohlcv_accepts_symbol_keyed_asset_list_payload() -> None:
    payload = {
        "status": {"error_code": 0},
        "data": {
            "CAKE": [
                {"id": 999, "name": "Empty Duplicate", "symbol": "CAKE", "quotes": []},
                _payload("CAKE")["data"],
            ]
        },
    }

    market = parse_ohlcv("CAKE", payload)

    assert [candle.close for candle in market["CAKE"]] == [2.5, 2.75]


def test_load_cmc_market_fetches_each_symbol_with_date_window() -> None:
    client = FakeCmcClient()

    market = load_cmc_market(
        ("CAKE", "USDT"),
        days=2,
        end_date=date(2026, 6, 12),
        client=client,
    )

    assert sorted(market) == ["CAKE", "USDT"]
    assert client.calls == [
        ("CAKE", "2026-06-10T00:00:00Z", "2026-06-12T00:00:00Z"),
        ("USDT", "2026-06-10T00:00:00Z", "2026-06-12T00:00:00Z"),
    ]


def test_load_cmc_market_uses_cache_without_spending_budget(tmp_path: Path) -> None:
    client = FakeCmcClient()
    budget = cmc_credit_budget("startup", max_credits_per_run=10)

    first = load_cmc_market(
        ("CAKE",),
        days=2,
        end_date=date(2026, 6, 12),
        client=client,
        cache_dir=tmp_path,
        credit_budget=budget,
    )
    second = load_cmc_market(
        ("CAKE",),
        days=2,
        end_date=date(2026, 6, 12),
        client=client,
        cache_dir=tmp_path,
        credit_budget=budget,
    )

    assert first == second
    assert len(client.calls) == 1
    assert budget.estimated_credits == 1
    assert budget.cache_hits == 1


def test_cmc_credit_budget_blocks_expensive_runs() -> None:
    client = FakeCmcClient()
    budget = cmc_credit_budget("startup", max_credits_per_run=1)

    try:
        load_cmc_market(
            ("CAKE", "USDT"),
            days=120,
            end_date=date(2026, 6, 12),
            client=client,
            credit_budget=budget,
        )
    except RuntimeError as exc:
        assert "CMC credit budget exceeded" in str(exc)
    else:
        raise AssertionError("expected CMC credit budget to block the run")

    assert client.calls == []


def test_estimates_historical_ohlcv_credits_conservatively() -> None:
    assert estimate_historical_ohlcv_credits(90, interval="daily") == 1
    assert estimate_historical_ohlcv_credits(120, interval="daily") == 2
    assert estimate_historical_ohlcv_credits(30, interval="5m") == 87


def test_latest_quotes_uses_current_cmc_endpoint() -> None:
    client = RecordingCmcClient()

    client.get_latest_quotes(("CAKE", "USDT"))

    assert client.calls == [
        ("/v3/cryptocurrency/quotes/latest", {"symbol": "CAKE,USDT", "convert": "USD"})
    ]


def test_client_accepts_string_zero_error_code(monkeypatch: Any) -> None:
    payload = {"status": {"error_code": "0"}, "data": {}}

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        assert timeout == 30
        assert "https://example.test/v3/cryptocurrency/quotes/latest" in request.full_url
        return FakeResponse(payload)

    monkeypatch.setattr("defiquant.data.cmc.urlopen", fake_urlopen)
    client = CmcClient(api_key="test-key", base_url="https://example.test")

    assert client.get_latest_quotes(("CAKE",)) == payload


def test_parse_latest_quotes_extracts_momentum_fields() -> None:
    payload = {
        "status": {"error_code": 0},
        "data": {
            "CAKE": {
                "symbol": "CAKE",
                "name": "PancakeSwap",
                "platform": {
                    "name": "BNB Smart Chain",
                    "symbol": "BNB",
                    "token_address": "0xCake",
                },
                "quote": {
                    "USD": {
                        "price": 2.1,
                        "volume_24h": 1_000_000,
                        "market_cap": 20_000_000,
                        "percent_change_1h": 1.2,
                        "percent_change_24h": 4.5,
                        "percent_change_7d": 9.0,
                    }
                },
            }
        },
    }

    quotes = parse_latest_quotes(payload, requested_symbols=("CAKE",))

    assert quotes["CAKE"]["price"] == 2.1
    assert quotes["CAKE"]["percent_change_24h"] == 4.5
    assert quotes["CAKE"]["token_address"] == "0xCake"


def test_parse_latest_quotes_accepts_v3_quote_list_and_keeps_liquid_duplicate() -> None:
    payload = {
        "status": {"error_code": "0"},
        "data": [
            {
                "symbol": "CAKE",
                "name": "PancakeSwap",
                "quote": [
                    {
                        "symbol": "USD",
                        "price": 1.4,
                        "volume_24h": 23_000_000,
                        "market_cap": 450_000_000,
                        "percent_change_1h": 0.6,
                        "percent_change_24h": -0.4,
                        "percent_change_7d": 7.5,
                    }
                ],
            },
            {
                "symbol": "CAKE",
                "name": "CakeDAO",
                "quote": [
                    {
                        "symbol": "USD",
                        "price": None,
                        "volume_24h": 0,
                        "market_cap": None,
                        "percent_change_1h": 0,
                        "percent_change_24h": 0,
                        "percent_change_7d": 0.2,
                    }
                ],
            },
        ],
    }

    quotes = parse_latest_quotes(payload, requested_symbols=("CAKE",))

    assert quotes["CAKE"]["name"] == "PancakeSwap"
    assert quotes["CAKE"]["price"] == 1.4
    assert quotes["CAKE"]["percent_change_7d"] == 7.5


def test_load_cmc_latest_quotes_batches_and_skips_invalid() -> None:
    client = RecordingCmcClient()

    quotes = load_cmc_latest_quotes(("CAKE", "LINK"), client=client, batch_size=1)

    assert sorted(quotes) == ["CAKE", "LINK"]
    assert client.calls == [
        (
            "/v3/cryptocurrency/quotes/latest",
            {"symbol": "CAKE", "convert": "USD", "skip_invalid": "true"},
        ),
        (
            "/v3/cryptocurrency/quotes/latest",
            {"symbol": "LINK", "convert": "USD", "skip_invalid": "true"},
        ),
    ]


def test_client_reads_dotenv_from_cwd(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("CMC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CMC_API_KEY=dotenv-key\nCMC_BASE_URL=https://example.test\n",
        encoding="utf-8",
    )

    client = CmcClient()

    assert client.api_key == "dotenv-key"
    assert client.base_url == "https://example.test"


class FakeCmcClient(CmcClient):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_historical_ohlcv(
        self,
        symbol: str,
        time_start: str,
        time_end: str,
        interval: str = "daily",
        time_period: str = "daily",
    ) -> dict[str, Any]:
        assert interval == "daily"
        assert time_period == "daily"
        self.calls.append((symbol, time_start, time_end))
        return _payload(symbol)


class RecordingCmcClient(CmcClient):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        self.calls.append((path, params))
        symbol = params.get("symbol", "")
        return {
            "status": {"error_code": 0},
            "data": {
                symbol: {
                    "symbol": symbol,
                    "quote": {
                        "USD": {
                            "price": 1,
                            "volume_24h": 1,
                            "market_cap": 1,
                        }
                    },
                }
            },
        }


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _payload(symbol: str) -> dict[str, Any]:
    return {
        "status": {"error_code": 0},
        "data": {
            "symbol": symbol,
            "quotes": [
                {
                    "time_open": "2026-06-10T00:00:00.000Z",
                    "quote": {
                        "USD": {
                            "open": 2.0,
                            "high": 3.0,
                            "low": 1.5,
                            "close": 2.5,
                            "volume": 100_000,
                            "market_cap": None,
                        }
                    },
                },
                {
                    "time_open": "2026-06-11T00:00:00.000Z",
                    "quote": {
                        "USD": {
                            "open": 2.5,
                            "high": 3.25,
                            "low": 2.25,
                            "close": 2.75,
                            "volume": 120_000,
                            "market_cap": 2_750_000,
                        }
                    },
                },
            ],
        },
    }
