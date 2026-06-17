from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def write_submission_evidence_bundle(
    output_root: str | Path,
    payloads: dict[str, dict[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = generated_at or datetime.now(UTC)
    bundle_dir = Path(output_root) / _timestamp_slug(timestamp)
    bundle_dir.mkdir(parents=True, exist_ok=False)

    files: dict[str, str] = {}
    for name, payload in payloads.items():
        filename = f"{name}.json"
        path = bundle_dir / filename
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        files[name] = str(path)

    manifest = {
        "generated_at_utc": timestamp.astimezone(UTC).isoformat(),
        "bundle_dir": str(bundle_dir),
        "files": files,
        "safety": {
            "live_transaction": False,
            "wallet_read": False,
            "funding": False,
            "registration": False,
            "paid_x402": False,
        },
        "manual_gates_not_run": [
            "TWAK live swap",
            "TWAK wallet funding",
            "Track 1 live registration",
            "BNB Agent SDK live registration",
            "DoraHacks external form submission",
            "paid x402 request",
        ],
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["files"] = {"manifest": str(manifest_path), **files}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _timestamp_slug(timestamp: datetime) -> str:
    value = timestamp.astimezone(UTC)
    return value.strftime("%Y%m%dT%H%M%SZ")
