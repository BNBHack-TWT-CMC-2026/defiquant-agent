from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from track1_volume_impulse_lab.cmc_dex import (
    fetch_cmc_dex_10m_market,
    fetch_cmc_kline_10m_market,
    load_pairs_config,
)
from track1_volume_impulse_lab.strategy import (
    LabConfig,
    fixture_market,
    load_10m_csv,
    optimize_weekly_periods,
    parameter_grid,
    report_to_jsonable,
    write_10m_csv,
    write_report,
    write_volume_baselines,
)

DEFAULT_VOLUME_SPIKES = "2,3,4,5,7.5,10,12.5,15,20,30"
DEFAULT_LEVERAGES = "1,2,3,5,10,15,20,30,50,80,100"
DEFAULT_EXIT_DECREASES = "1,2,3,4,5,6"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimize the Track 1 research-only 10-minute volume impulse strategy."
    )
    parser.add_argument("--config", default="track1_volume_impulse_lab/config.example.json")
    parser.add_argument("--csv-10m", default="")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--output-dir", default="artifacts/track1-volume-impulse")
    parser.add_argument("--time-start", default="")
    parser.add_argument("--time-end", default="")
    parser.add_argument(
        "--cmc-source",
        choices=("dex-ohlcv", "kline", "kline-latest"),
        default="dex-ohlcv",
    )
    parser.add_argument("--kline-limit", type=int, default=600)
    parser.add_argument("--volume-spikes", default=DEFAULT_VOLUME_SPIKES)
    parser.add_argument("--leverages", default=DEFAULT_LEVERAGES)
    parser.add_argument("--exit-decreases", default=DEFAULT_EXIT_DECREASES)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    lab_config = _load_lab_config(args.config)
    market = _load_market(args, lab_config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_10m_csv(market, output_dir / "candles_10m.csv")
    write_volume_baselines(market, lab_config, output_dir / "volume_baselines.csv")

    params = parameter_grid(
        volume_spike_multiples=_parse_float_list(args.volume_spikes),
        leverages=_parse_float_list(args.leverages),
        exit_volume_decreases=_parse_int_list(args.exit_decreases),
    )
    report = optimize_weekly_periods(
        market,
        params,
        lab_config,
        top=max(1, args.top),
        progress=not args.no_progress,
    )
    write_report(report, output_dir)
    print(json.dumps(report_to_jsonable(report), indent=2))


def _load_market(args: argparse.Namespace, lab_config: LabConfig):
    if args.fixture:
        return fixture_market()
    if args.csv_10m:
        return load_10m_csv(args.csv_10m)

    pairs = load_pairs_config(args.config)
    if args.cmc_source == "kline-latest":
        return fetch_cmc_kline_10m_market(
            pairs,
            limit=args.kline_limit,
            cache_dir=Path(args.output_dir) / "raw",
        )

    if not args.time_end:
        raise SystemExit("Use --fixture, --csv-10m, or provide --time-end for CMC DEX loading")
    time_end = _parse_datetime_arg(args.time_end)
    time_start = (
        _parse_datetime_arg(args.time_start)
        if args.time_start
        else time_end - timedelta(days=lab_config.baseline_days + (lab_config.period_days * 4))
    )
    if args.cmc_source == "kline":
        return fetch_cmc_kline_10m_market(
            pairs,
            time_start=time_start.isoformat(),
            time_end=time_end.isoformat(),
            limit=args.kline_limit,
            cache_dir=Path(args.output_dir) / "raw",
        )
    return fetch_cmc_dex_10m_market(
        pairs,
        time_start=time_start.isoformat(),
        time_end=time_end.isoformat(),
        cache_dir=Path(args.output_dir) / "raw",
    )


def _load_lab_config(path: str) -> LabConfig:
    raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    lab = raw.get("lab", {})
    if not isinstance(lab, dict):
        raise ValueError("config lab section must be an object")
    return LabConfig(
        seed=float(lab.get("seed", 1000.0)),
        baseline_days=int(lab.get("baseline_days", 30)),
        period_days=int(lab.get("period_days", 7)),
        max_drawdown=float(lab.get("max_drawdown", 0.30)),
        fee_bps=float(lab.get("fee_bps", 5.0)),
        slippage_bps=float(lab.get("slippage_bps", 10.0)),
    )


def _parse_float_list(value: str) -> tuple[float, ...]:
    return tuple(float(item.strip()) for item in value.split(",") if item.strip())


def _parse_int_list(value: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def _parse_datetime_arg(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


if __name__ == "__main__":
    main()
