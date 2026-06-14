from __future__ import annotations

import json
import sys

import pytest

from defiquant.cli import main


def test_register_track1_defaults_to_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["defiquant", "register-track1"])
    monkeypatch.setenv("TWAK_CLI", "twak")

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"registration": 'twak-dry-run:["twak","compete","register"]'}


def test_execute_twak_live_is_blocked_until_wallet_portfolio_is_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["defiquant", "execute", "--fixture", "--adapter", "twak", "--live"],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert "Live TWAK swap submission is disabled" in str(exc.value)
