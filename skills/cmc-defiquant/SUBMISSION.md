# Track 2 CMC Skill Submission Notes

## Package

Submit the `skills/cmc-defiquant/` directory for Track 2.

Included files:

- `skill.json`: package metadata and safety declaration.
- `SKILL.md`: executable skill instructions.
- `README.md`: package overview.
- `examples/input.fixture.json`: deterministic review input.
- `examples/output.fixture.json`: deterministic target-weight output.
- `examples/regime-output.fixture.json`: deterministic up-channel/down-channel
  regime strategy spec output.
- `examples/delta-neutral-output.fixture.json`: deterministic delta-neutral
  walk-forward lab output.

## Purpose

The skill turns CoinMarketCap OHLCV data into BNB Chain target weights with
transparent rationale and risk controls. It is intended for CMC Agent Hub /
Skills Marketplace style routing as a read-only analysis pipeline.

It reuses the same alpha pool as Track 1:

- `medium_momentum`
- `trend_strength`
- `volume_impulse`
- `liquidity_depth`
- `short_reversal_guard`
- `trend_angle`
- `supertrend_alignment`
- `volatility`

It also includes a Track 2-only non-executing regime spec:

- `up_channel_long_bias`: support holds, trend angle is positive, Supertrend is
  positive, price is above the Ichimoku-lite cloud, and volume confirms
  participation.
- `down_channel_short_bias`: support breaks or fails to reclaim, trend angle is
  negative, Supertrend is negative or cloud is bearish, and volume confirms
  distribution.
- `range_or_transition`: factors disagree, so the skill records the regime and
  does not express a directional preference.

It adds a Track 2-only non-executing delta-neutral lab:

- `bull`, `bear`, and `mixed` market regimes from the equal-weight index trend
  angle.
- Per-coin up-channel/down-channel/transition classification from trend angle
  and lookback momentum.
- Long/short basket variants that scale the short leg to reduce market beta.
- Walk-forward train/test loops with transaction costs and out-of-sample
  reporting.

## Non-Execution Proof

This package does not include:

- TWAK CLI commands,
- private key or wallet password handling,
- transaction signing,
- order submission,
- funding logic,
- mainnet mutation.

The only local commands documented for this package are:

```powershell
uv run defiquant signal --fixture --config configs/strategy.json
uv run defiquant signal --config configs/strategy.json --cmc-days 90
uv run defiquant track2-regime-spec --fixture --config configs/strategy.json
uv run defiquant track2-regime-spec --config configs/strategy.json --cmc-days 90
uv run defiquant track2-delta-neutral-lab --fixture --config configs/strategy.json --max-candidates 50
uv run defiquant track2-delta-neutral-lab \
  --config configs/strategy.json \
  --cmc-plan startup \
  --cmc-days 90 \
  --cmc-cache-dir artifacts/cmc-cache \
  --cmc-max-credits-per-run 100 \
  --max-candidates 200
uv run defiquant backtest --config configs/strategy.json --cmc-days 90 --cmc-end-date 2026-06-12
uv run defiquant research-report --windows 90,180,365
uv run defiquant alpha-lab --windows 90,180,365 --max-candidates 1000 --top 5
```

CMC Startup plan usage is guarded by local OHLCV caching plus a per-run
estimated credit ceiling. The Skill can keep generating candidate strategies
from cached OHLCV data without spending additional CMC credits.

Track 1 execution remains outside this package under the repository execution
adapters and live-operation guardrails.

Track 1 `frontier-evidence`, TWAK dry-run, quote validation, registration, and
live swaps are intentionally excluded from the Track 2 Skill package.

## Review Checklist

- Confirm `skill.json` parses as JSON.
- Confirm examples parse as JSON.
- Run fixture signal and compare the shape to `examples/output.fixture.json`.
- Confirm `examples/input.fixture.json` matches `configs/strategy.json`.
- Confirm each non-stable fixture output includes the alpha pool reason keys.
- Confirm `examples/regime-output.fixture.json` includes both regime lanes and
  reason keys for support, trend angle, Supertrend, cloud, and volume.
- Confirm `examples/delta-neutral-output.fixture.json` includes no orders, no
  wallet access, train/test split fields, and the selected long/short baskets.
- Confirm `SKILL.md` contains no wallet mutation instructions.
- Confirm `.env` and API keys are not included.
