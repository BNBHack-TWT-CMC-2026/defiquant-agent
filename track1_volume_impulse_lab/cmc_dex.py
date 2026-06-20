from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
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
    network_id: str | None = None
    platform: str = "bsc"
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
            "time_period": "5m",
            "interval": "5m",
            "time_start": time_start,
            "time_end": time_end,
            "skip_invalid": "true",
            "reverse_order": "true" if pair.reverse_order else "false",
        }
        if pair.network_id:
            params["network_id"] = pair.network_id
        else:
            params["network_slug"] = pair.network_slug
        return self._get("/v4/dex/pairs/ohlcv/historical", params)

    def get_pair_kline_5m(
        self,
        pair: DexPair,
        *,
        time_start: str | None = None,
        time_end: str | None = None,
        limit: int = 600,
    ) -> dict[str, Any]:
        params = {
            "platform": pair.platform or pair.network_slug,
            "address": pair.contract_address,
            "interval": "5min",
            "unit": "usd",
            "limit": str(limit),
            "pm": "p",
        }
        if time_start is not None:
            params["from"] = str(_timestamp_seconds(time_start))
        if time_end is not None:
            params["to"] = str(_timestamp_seconds(time_end))
        return self._get("/v1/k-line/candles", params)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urlencode(params)}"
        request = Request(
            url,
            headers={"X-CMC_PRO_API_KEY": self.api_key, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as decode_exc:
                raise RuntimeError(f"CMC API HTTP {exc.code}: {body[:300]}") from decode_exc
            _raise_cmc_payload_error(payload)
            raise RuntimeError(f"CMC API HTTP {exc.code}") from exc
        if isinstance(payload, dict):
            _raise_cmc_payload_error(payload)
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


def fetch_cmc_kline_10m_market(
    pairs: list[DexPair],
    *,
    time_start: str | None = None,
    time_end: str | None = None,
    limit: int = 600,
    client: CmcDexClient | None = None,
    cache_dir: str | Path | None = None,
) -> Market10m:
    cmc = client or CmcDexClient()
    market_5m: dict[str, list[FiveMinuteCandle]] = defaultdict(list)
    for pair in pairs:
        if time_start and time_end:
            payloads = _fetch_kline_pages(cmc, pair, time_start, time_end, limit=limit)
        else:
            payloads = [cmc.get_pair_kline_5m(pair, limit=limit)]
        for page_index, payload in enumerate(payloads):
            if cache_dir is not None:
                suffix = f"_{page_index:04d}" if len(payloads) > 1 else ""
                cache_path = Path(cache_dir) / f"{pair.symbol.upper()}_kline_5m{suffix}.json"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            market_5m[pair.symbol.upper()].extend(parse_kline_candles_5m(pair.symbol, payload))
    return aggregate_5m_to_10m(_dedupe_5m_market(market_5m))


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


def parse_kline_candles_5m(symbol: str, payload: Any) -> list[FiveMinuteCandle]:
    rows = _kline_rows(payload)
    candles: list[FiveMinuteCandle] = []
    for row in rows:
        if not isinstance(row, list | tuple) or len(row) < 6:
            continue
        candles.append(
            FiveMinuteCandle(
                symbol=symbol.upper(),
                timestamp=_parse_kline_timestamp(row[5]),
                open=float(row[0]),
                high=float(row[1]),
                low=float(row[2]),
                close=float(row[3]),
                volume=float(row[4] or 0.0),
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
            network_id=str(item["network_id"]) if item.get("network_id") else None,
            platform=str(item.get("platform", item.get("network_slug", "bsc"))),
            reverse_order=bool(item.get("reverse_order", False)),
        )
        for item in pairs
        if isinstance(item, dict)
    ]


def _fetch_kline_pages(
    cmc: CmcDexClient,
    pair: DexPair,
    time_start: str,
    time_end: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    start = parse_timestamp(time_start)
    end = parse_timestamp(time_end)
    if end <= start:
        return []
    page_span = timedelta(minutes=5 * max(1, limit))
    cursor = start
    payloads: list[dict[str, Any]] = []
    while cursor < end:
        page_end = min(cursor + page_span, end)
        payloads.append(
            cmc.get_pair_kline_5m(
                pair,
                time_start=cursor.isoformat(),
                time_end=page_end.isoformat(),
                limit=limit,
            )
        )
        cursor = page_end
    return payloads


def _dedupe_5m_market(
    market_5m: dict[str, list[FiveMinuteCandle]],
) -> dict[str, list[FiveMinuteCandle]]:
    deduped: dict[str, list[FiveMinuteCandle]] = {}
    for symbol, candles in market_5m.items():
        by_timestamp = {candle.timestamp: candle for candle in candles}
        deduped[symbol] = [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]
    return deduped


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


def _kline_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, list) else []
    return []


def _raise_cmc_payload_error(payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    status = payload.get("status", {})
    error_code = int(status.get("error_code") or 0) if isinstance(status, dict) else 0
    if not error_code:
        return
    message = status.get("error_message") or "CoinMarketCap API request failed"
    raise RuntimeError(f"CMC API error {error_code}: {message}")


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


def _timestamp_seconds(value: str) -> int:
    return int(parse_timestamp(value).timestamp())


def _parse_kline_timestamp(value: Any) -> datetime:
    timestamp = float(value)
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp, UTC)
