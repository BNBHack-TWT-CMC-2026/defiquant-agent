# CMC defiQuant Skill

This directory is the Track 2 Skill package for the CMC Agent Hub / Strategy
Skills submission.

DoraHacks supports adding the second track through the Add option, so this
package is prepared alongside the Track 1 live-trading agent.

Expected behavior:

1. Accept an eligible CMC token universe and historical OHLCV data.
2. Run the shared alpha pool strategy scoring.
3. Return target weights, rationale, and risk flags.
4. Optionally return a non-executing regime strategy spec that splits coins into
   up-channel long-bias, down-channel short-bias, and neutral/transition lanes.
5. Optionally run a non-executing delta-neutral lab that searches long/short
   basket variants with bull/bear/mixed market regimes, coin trend angles, beta
   neutralization, transaction costs, and walk-forward out-of-sample reporting.
6. Never execute trades in Track 2 mode.

Alpha pool reasons use the same names as Track 1:

- `medium_momentum`
- `trend_strength`
- `volume_impulse`
- `liquidity_depth`
- `short_reversal_guard`
- `volatility`

Regime strategy reasons add:

- `support_line`
- `support_distance`
- `support_break`
- `trend_angle`
- `supertrend_alignment`
- `cloud_bias`
- `volume_impulse`
- `long_score`
- `short_score`

Local command:

```powershell
uv run defiquant signal --fixture --config configs/strategy.json
uv run defiquant signal --config configs/strategy.json --cmc-days 90
uv run defiquant track2-regime-spec --fixture --config configs/strategy.json
uv run defiquant track2-regime-spec --config configs/strategy.json --cmc-days 90
uv run defiquant track2-delta-neutral-lab --fixture --config configs/strategy.json --max-candidates 50
uv run defiquant track2-delta-neutral-lab --config configs/strategy.json --cmc-days 180 --max-candidates 200
uv run defiquant backtest --config configs/strategy.json --cmc-days 90 --cmc-end-date 2026-06-12
uv run defiquant research-report --windows 90,180,365
uv run defiquant alpha-lab --windows 90,180,365 --max-candidates 1000 --top 5
```

`frontier-evidence` and TWAK dry-run commands are Track 1 evidence paths, not
Track 2 Skill behavior. Track 2 returns target weights only.

The executable skill instructions are in [SKILL.md](SKILL.md). The JSON package
metadata is in [skill.json](skill.json).

Submission support files:

- [SUBMISSION.md](SUBMISSION.md): Track 2 packaging notes and non-execution proof.
- [examples/input.fixture.json](examples/input.fixture.json): deterministic sample input.
- [examples/output.fixture.json](examples/output.fixture.json): deterministic target-weight output.
- [examples/regime-output.fixture.json](examples/regime-output.fixture.json): deterministic
  regime strategy spec output.
- [examples/delta-neutral-output.fixture.json](examples/delta-neutral-output.fixture.json):
  deterministic delta-neutral lab output.
