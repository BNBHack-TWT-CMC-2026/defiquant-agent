from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import ceil
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from defiquant.env import env_value
from defiquant.models import Candle, MarketData

DEFAULT_CMC_HISTORY_DAYS = 90


@dataclass(frozen=True)
class CmcPlanProfile:
    name: str
    monthly_credits: int
    requests_per_minute: int
    conversion_symbols_per_call: int
    default_max_credits_per_run: int
    historical_5m_days: int


@dataclass
class CmcCreditBudget:
    profile: CmcPlanProfile
    max_credits: int
    estimated_credits: int = 0
    requests: int = 0
    cache_hits: int = 0

    def consume(self, credits: int, *, label: str) -> None:
        if credits < 0:
            raise ValueError("credits cannot be negative")
        next_total = self.estimated_credits + credits
        if next_total > self.max_credits:
            raise RuntimeError(
                "CMC credit budget exceeded for "
                f"{label}: {next_total} estimated credits > {self.max_credits} "
                f"allowed on {self.profile.name} profile"
            )
        self.estimated_credits = next_total
        self.requests += 1

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "plan": self.profile.name,
            "max_credits": self.max_credits,
            "estimated_credits": self.estimated_credits,
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "monthly_credits": self.profile.monthly_credits,
            "requests_per_minute": self.profile.requests_per_minute,
        }


CMC_PLAN_PROFILES = {
    "startup": CmcPlanProfile(
        name="startup",
        monthly_credits=450_000,
        requests_per_minute=600,
        conversion_symbols_per_call=40,
        default_max_credits_per_run=2_000,
        historical_5m_days=30,
    ),
    "standard": CmcPlanProfile(
        name="standard",
        monthly_credits=2_000_000,
        requests_per_minute=750,
        conversion_symbols_per_call=40,
        default_max_credits_per_run=10_000,
        historical_5m_days=90,
    ),
}


class CmcClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or env_value("CMC_API_KEY")
        self.base_url = (
            base_url or env_value("CMC_BASE_URL", "https://pro-api.coinmarketcap.com")
        ).rstrip("/")
        if not self.api_key:
            raise ValueError("CMC_API_KEY is required")

    def get_latest_quotes(
        self,
        symbols: tuple[str, ...],
        *,
        skip_invalid: bool = False,
    ) -> dict[str, Any]:
        params = {"symbol": ",".join(symbols), "convert": "USD"}
        if skip_invalid:
            params["skip_invalid"] = "true"
        return self._get("/v3/cryptocurrency/quotes/latest", params)

    def get_historical_ohlcv(
        self,
        symbol: str,
        time_start: str,
        time_end: str,
        interval: str = "daily",
        time_period: str = "daily",
    ) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "time_start": time_start,
            "time_end": time_end,
            "time_period": time_period,
            "interval": interval,
            "convert": "USD",
            "skip_invalid": "false",
        }
        return self._get("/v2/cryptocurrency/ohlcv/historical", params)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urlencode(params)}"
        request = Request(
            url,
            headers={"X-CMC_PRO_API_KEY": self.api_key, "Accept": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        status = payload.get("status", {})
        error_code = _error_code(status.get("error_code", 0))
        if error_code:
            message = status.get("error_message") or "CoinMarketCap API request failed"
            raise RuntimeError(f"CMC API error {error_code}: {message}")
        return payload


def load_cmc_market(
    symbols: tuple[str, ...],
    *,
    days: int = DEFAULT_CMC_HISTORY_DAYS,
    end_date: date | None = None,
    client: CmcClient | None = None,
    cache_dir: str | Path | None = None,
    refresh_cache: bool = False,
    credit_budget: CmcCreditBudget | None = None,
) -> MarketData:
    if days < 1:
        raise ValueError("days must be at least 1")

    effective_end = end_date or (datetime.now(UTC).date() - timedelta(days=1))
    effective_start = effective_end - timedelta(days=days)
    cmc = client or CmcClient()
    market: MarketData = {}
    for symbol in symbols:
        time_start = _to_cmc_timestamp(effective_start)
        time_end = _to_cmc_timestamp(effective_end)
        cache_path = (
            _ohlcv_cache_path(
                Path(cache_dir),
                symbol=symbol,
                time_start=time_start,
                time_end=time_end,
                interval="daily",
                time_period="daily",
            )
            if cache_dir
            else None
        )
        payload = (
            _read_cached_payload(cache_path)
            if cache_path is not None and not refresh_cache
            else None
        )
        if payload is not None:
            if credit_budget is not None:
                credit_budget.record_cache_hit()
        else:
            if credit_budget is not None:
                credit_budget.consume(
                    estimate_historical_ohlcv_credits(days, interval="daily"),
                    label=f"ohlcv:{symbol}:{days}d",
                )
            payload = cmc.get_historical_ohlcv(symbol, time_start=time_start, time_end=time_end)
            if cache_path is not None:
                _write_cached_payload(cache_path, payload)
        candles = parse_ohlcv(symbol, payload).get(symbol, [])
        if not candles:
            raise ValueError(f"CMC returned no OHLCV candles for {symbol}")
        market[symbol] = candles
    return market


def load_cmc_latest_quotes(
    symbols: tuple[str, ...],
    *,
    client: CmcClient | None = None,
    batch_size: int = 40,
) -> dict[str, dict[str, Any]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    cmc = client or CmcClient()
    quotes: dict[str, dict[str, Any]] = {}
    for index in range(0, len(symbols), batch_size):
        batch = symbols[index : index + batch_size]
        if not batch:
            continue
        payload = cmc.get_latest_quotes(batch, skip_invalid=True)
        quotes.update(parse_latest_quotes(payload, requested_symbols=batch))
    return quotes


def cmc_credit_budget(
    plan: str = "startup",
    *,
    max_credits_per_run: int | None = None,
) -> CmcCreditBudget:
    profile = CMC_PLAN_PROFILES.get(plan)
    if profile is None:
        raise ValueError(f"unsupported CMC plan profile: {plan}")
    max_credits = max_credits_per_run or profile.default_max_credits_per_run
    if max_credits < 1:
        raise ValueError("max_credits_per_run must be positive")
    return CmcCreditBudget(profile=profile, max_credits=max_credits)


def estimate_historical_ohlcv_credits(days: int, *, interval: str) -> int:
    if days < 1:
        raise ValueError("days must be at least 1")
    if interval == "daily":
        points = days + 1
    elif interval in {"5m", "5min", "5minute"}:
        points = (days * 24 * 12) + 1
    elif interval in {"hourly", "1h"}:
        points = (days * 24) + 1
    else:
        points = days + 1
    return max(1, ceil(points / 100))


def parse_ohlcv(symbol: str, payload: dict[str, Any]) -> MarketData:
    rows = _extract_ohlcv_rows(symbol, payload)
    candles: list[Candle] = []
    for row in rows:
        quote = row.get("quote", {}).get("USD", {})
        candles.append(
            Candle(
                symbol=symbol,
                timestamp=_parse_datetime(row["time_open"]),
                open=float(quote["open"]),
                high=float(quote["high"]),
                low=float(quote["low"]),
                close=float(quote["close"]),
                volume=float(quote.get("volume", 0.0)),
                market_cap=_optional_float(quote.get("market_cap")),
            )
        )
    return {symbol: sorted(candles, key=lambda candle: candle.timestamp)}


def parse_latest_quotes(
    payload: dict[str, Any],
    *,
    requested_symbols: tuple[str, ...] = (),
) -> dict[str, dict[str, Any]]:
    rows = _extract_latest_quote_rows(payload)
    requested = {symbol.upper() for symbol in requested_symbols}
    parsed: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol or (requested and symbol not in requested):
            continue
        quote = _latest_usd_quote(row.get("quote"))
        platform_payload = row.get("platform")
        platform = platform_payload if isinstance(platform_payload, dict) else {}
        candidate = {
            "symbol": symbol,
            "name": row.get("name", ""),
            "price": _optional_float(quote.get("price")),
            "volume_24h": _optional_float(quote.get("volume_24h")),
            "market_cap": _optional_float(quote.get("market_cap")),
            "percent_change_1h": _optional_float(quote.get("percent_change_1h")),
            "percent_change_24h": _optional_float(quote.get("percent_change_24h")),
            "percent_change_7d": _optional_float(quote.get("percent_change_7d")),
            "platform_name": platform.get("name", ""),
            "platform_symbol": platform.get("symbol", ""),
            "token_address": platform.get("token_address", ""),
        }
        if _latest_quote_rank(candidate) >= _latest_quote_rank(parsed.get(symbol, {})):
            parsed[symbol] = candidate
    return parsed


def _extract_ohlcv_rows(symbol: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {})
    if not isinstance(data, dict):
        return []

    direct_rows = data.get("quotes")
    if isinstance(direct_rows, list):
        return [row for row in direct_rows if isinstance(row, dict)]

    symbol_payload = data.get(symbol)
    if symbol_payload is not None:
        rows = _rows_from_asset_payload(symbol_payload)
        if rows:
            return rows

    for value in data.values():
        rows = _rows_from_asset_payload(value, symbol=symbol)
        if rows:
            return rows
    return []


def _extract_latest_quote_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {})
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []

    rows: list[dict[str, Any]] = []
    for value in data.values():
        if isinstance(value, dict):
            rows.append(value)
        elif isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def _latest_usd_quote(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        quote = value.get("USD", {})
        return quote if isinstance(quote, dict) else {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("symbol") == "USD":
                return item
    return {}


def _latest_quote_rank(row: dict[str, Any]) -> tuple[int, float, float]:
    price = row.get("price")
    market_cap = row.get("market_cap")
    volume_24h = row.get("volume_24h")
    return (
        1 if price is not None else 0,
        float(market_cap) if market_cap is not None else 0.0,
        float(volume_24h) if volume_24h is not None else 0.0,
    )


def _rows_from_asset_payload(value: Any, symbol: str | None = None) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if symbol is not None and value.get("symbol") != symbol:
            return []
        rows = value.get("quotes")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    if isinstance(value, list):
        candidates = [
            _rows_from_asset_payload(item, symbol=symbol)
            for item in value
            if isinstance(item, dict)
        ]
        return max(candidates, key=len, default=[])

    return []


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_cmc_timestamp(value: date) -> str:
    return f"{value.isoformat()}T00:00:00Z"


def _ohlcv_cache_path(
    cache_dir: Path,
    *,
    symbol: str,
    time_start: str,
    time_end: str,
    interval: str,
    time_period: str,
) -> Path:
    name = (
        f"{_cache_token(symbol)}_{_cache_token(interval)}_{_cache_token(time_period)}_"
        f"{_cache_token(time_start)}_{_cache_token(time_end)}.json"
    )
    return cache_dir / "ohlcv" / name


def _read_cached_payload(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cached_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _cache_token(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value.upper())


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _error_code(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(value)
