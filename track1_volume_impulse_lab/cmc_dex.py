from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from defiquant.env import env_value
from track1_volume_impulse_lab.strategy import (
    Market10m,
    TenMinuteCandle,
    parse_timestamp,
    sort_market,
)


@dataclass(frozen=True)
class FiveMinuteCandle:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class DexPair:
    symbol: str
    contract_address: str
    network_slug: str = "bsc"
    reverse_order: bool = False


class CmcDexClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or env_value("CMC_API_KEY")
        self.base_url = (
            base_url or env_value("CMC_BASE_URL", "https://pro-api.coinmarketcap.com")
        ).rstrip("/")
        if not self.api_key:
            raise ValueError("CMC_API_KEY is required")

    def get_pair_ohlcv_5m(
        self,
        pair: DexPair,
        *,
        time_start: str,
        time_end: str,
    ) -> dict[str, Any]:
        params = {
            "contract_address": pair.contract_address,
            "network_slug": pair.network_slug,
            "time_period": "5m",
            "interval": "5m",
            "time_start": time_start,
            "time_end": time_end,
            "skip_invalid": "true",
            "reverse_order": "true" if pair.reverse_order else "false",
        }
        return self._get("/v4/dex/pairs/ohlcv/historical", params)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urlencode(params)}"
        request = Request(
            url,
            headers={"X-CMC_PRO_API_KEY": self.api_key, "Accept": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict):
            status = payload.get("status", {})
            error_code = int(status.get("error_code") or 0) if isinstance(status, dict) else 0
            if error_code:
                message = status.get("error_message") or "CoinMarketCap API request failed"
                raise RuntimeError(f"CMC API error {error_code}: {message}")
        return payload


def fetch_cmc_dex_10m_market(
    pairs: list[DexPair],
    *,
    time_start: str,
    time_end: str,
    client: CmcDexClient | None = None,
    cache_dir: str | Path | None = None,
) -> Market10m:
    cmc = client or CmcDexClient()
    market_5m: dict[str, list[FiveMinuteCandle]] = defaultdict(list)
    for pair in pairs:
        payload = cmc.get_pair_ohlcv_5m(pair, time_start=time_start, time_end=time_end)
        if cache_dir is not None:
            cache_path = Path(cache_dir) / f"{pair.symbol.upper()}_5m_raw.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        market_5m[pair.symbol.upper()].extend(parse_dex_ohlcv_5m(pair.symbol, payload))
    return aggregate_5m_to_10m(market_5m)


def parse_dex_ohlcv_5m(symbol: str, payload: Any) -> list[FiveMinuteCandle]:
    rows = _payload_items(payload)
    candles: list[FiveMinuteCandle] = []
    for item in rows:
        for quote_row in item.get("quotes", []):
            if not isinstance(quote_row, dict):
                continue
            quote = _first_quote(quote_row.get("quote"))
            if not quote:
                continue
            candles.append(
                FiveMinuteCandle(
                    symbol=symbol.upper(),
                    timestamp=parse_timestamp(str(quote_row["time_open"])),
                    open=float(quote["open"]),
                    high=float(quote["high"]),
                    low=float(quote["low"]),
                    close=float(quote["close"]),
                    volume=float(quote.get("volume", 0.0)),
                )
            )
    return sorted(candles, key=lambda candle: candle.timestamp)


def aggregate_5m_to_10m(
    market_5m: dict[str, list[FiveMinuteCandle]],
    *,
    require_complete: bool = True,
) -> Market10m:
    market: dict[str, list[TenMinuteCandle]] = defaultdict(list)
    for symbol, candles in market_5m.items():
        buckets: dict[datetime, list[FiveMinuteCandle]] = defaultdict(list)
        for candle in sorted(candles, key=lambda item: item.timestamp):
            buckets[_floor_10m(candle.timestamp)].append(candle)
        for timestamp in sorted(buckets):
            bucket = sorted(buckets[timestamp], key=lambda item: item.timestamp)
            if require_complete and len(bucket) < 2:
                continue
            market[symbol.upper()].append(
                TenMinuteCandle(
                    symbol=symbol.upper(),
                    timestamp=timestamp,
                    open=bucket[0].open,
                    high=max(candle.high for candle in bucket),
                    low=min(candle.low for candle in bucket),
                    close=bucket[-1].close,
                    volume=sum(candle.volume for candle in bucket),
                )
            )
    return sort_market(market)


def load_pairs_config(path: str | Path) -> list[DexPair]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    pairs = raw.get("pairs", [])
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("config must include a non-empty pairs list")
    return [
        DexPair(
            symbol=str(item["symbol"]).upper(),
            contract_address=str(item["contract_address"]),
            network_slug=str(item.get("network_slug", "bsc")),
            reverse_order=bool(item.get("reverse_order", False)),
        )
        for item in pairs
        if isinstance(item, dict)
    ]


def _payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [item for item in data.values() if isinstance(item, dict)]
    return []


def _first_quote(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    if isinstance(value, dict):
        usd = value.get("USD")
        return usd if isinstance(usd, dict) else value
    return {}


def _floor_10m(value: datetime) -> datetime:
    return value.replace(minute=(value.minute // 10) * 10, second=0, microsecond=0)
