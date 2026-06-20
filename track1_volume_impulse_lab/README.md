# Track 1 Volume Impulse Lab

Research-only clone of the 10-minute leveraged volume impulse idea. It does not
call TWAK, sign transactions, or submit live orders.

The optimizer uses CMC DEX pair `5m` OHLCV, aggregates two candles into one
`10m` candle, stores the resulting candles, and runs weekly parameter sweeps.

```bash
python -m track1_volume_impulse_lab.optimize --fixture --no-progress
python -m track1_volume_impulse_lab.optimize \
  --config track1_volume_impulse_lab/config.example.json \
  --time-end 2026-06-22T00:00:00Z
```

Outputs are written to `artifacts/track1-volume-impulse/` by default:

- `candles_10m.csv`
- `weekly_results.json`
- `summary.md`

The best candidate for each 7-day period is the highest-return candidate that
does not liquidate, does not trip the 30% MDD gate, and has at least one trade.
