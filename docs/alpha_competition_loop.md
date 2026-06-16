# Track 1 Alpha Competition Loop

This loop is the competitive layer above the safe execution scaffolding. It is
read-only until a separate live command is approved.

## Goal

Track 1 is judged on live PnL after the risk gate, so the operating target is:

- survive the drawdown gate,
- keep capital in eligible in-scope tokens,
- trade at least once per day,
- switch risk exposure when live market breadth changes,
- keep every live action small enough to recover from mistakes.

The default config remains defensive. Use the alpha scan to decide whether the
live window should run aggressive, balanced, or defensive parameters.

## Modes

Mode definitions live in `configs/alpha_modes.json`.

- `aggressive`: used only when top tradable momentum and market breadth are
  both strong. It uses the best total-return preset from the CMC 90-day sweep
  that stayed below the local 20% drawdown cap.
- `balanced`: used when the market is constructive but not clearly broad.
- `defensive`: default fallback when breadth is weak, tradable momentum is
  weak, CMC data is incomplete, or TWAK quote validation is noisy.

The matching strategy configs are:

- `configs/strategy.aggressive.json`
- `configs/strategy.balanced.json`
- `configs/strategy.defensive.json`

Do not edit the default `configs/strategy.json` during live operations unless
the mode change has already been verified in dry-run output.

## Daily Read-Only Decision

Run this before any live trading decision:

```powershell
uv run defiquant scan-alpha --symbols-source tradable --top 10
```

The command requests CMC latest quotes for the configured tradable BSC universe
and returns:

- recommended mode,
- market breadth,
- top tradable ranked tokens,
- top discovery tokens when a broader scan is used.

Optional broad discovery scan:

```powershell
uv run defiquant scan-alpha --symbols-source eligible --top 15
```

This is for research only. A token discovered here is not executable until its
BSC contract address is added to `configs/token_addresses.bsc.json`, verified,
tested, and reviewed.

## Mode Application

After the scan, run dry-run execution against the recommended mode:

```powershell
uv run defiquant execute --config configs/strategy.aggressive.json --cmc-days 90 --adapter twak --portfolio twak --validate-quotes --dry-run
uv run defiquant execute --config configs/strategy.balanced.json --cmc-days 90 --adapter twak --portfolio twak --validate-quotes --dry-run
uv run defiquant execute --config configs/strategy.defensive.json --cmc-days 90 --adapter twak --portfolio twak --validate-quotes --dry-run
```

Choose the most aggressive mode only when:

- the scan recommends it,
- quote validation succeeds,
- total notional is inside the approved live cap,
- the wallet has enough BNB for gas,
- the planned symbols are all in `configs/eligible_tokens.json`,
- the current drawdown state is below the halt threshold.

If any check is uncertain, use defensive mode or stop.

## Evidence To Capture

For each live day, store ignored local artifacts under `artifacts/track1-live/`:

- alpha scan JSON,
- selected mode and reason,
- dry-run execution JSON,
- TWAK quote validation JSON,
- live transaction output after approval,
- UTC and KST timestamps.

Do not commit artifacts or secrets.

## Hard Stops

This loop never approves:

- wallet funding,
- Track 1 live registration,
- TWAK live swaps,
- BNB Agent SDK live registration,
- x402 paid calls,
- DoraHacks form submission.

Those remain manual gates that require explicit approval in the current thread.
