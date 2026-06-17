from __future__ import annotations

import json
import sys

import pytest

from defiquant.alpha_lab import build_alpha_lab_report, generate_alpha_weight_candidates
from defiquant.config import AlphaWeights, load_config
from defiquant.data.fixtures import fixture_market


def test_strategy_config_uses_default_alpha_weights() -> None:
    config = load_config("configs/strategy.json")

    assert config.strategy.alpha_weights == AlphaWeights()


def test_alpha_lab_generates_at_least_1000_deterministic_candidates() -> None:
    candidates = generate_alpha_weight_candidates(1000)

    assert len(candidates) == 1000
    assert candidates[0].name == "baseline"
    assert candidates[0].weights == AlphaWeights()
    assert len({candidate.name for candidate in candidates}) == 1000


def test_alpha_lab_report_ranks_candidates_on_fixture() -> None:
    config = load_config("configs/strategy.defensive.json")
    market = fixture_market(config.universe_symbols)

    report = build_alpha_lab_report(
        config,
        {30: market, 60: market},
        max_candidates=25,
        top=5,
    )

    assert report["candidate_count"] == 25
    assert report["recommended_candidate"]
    assert report["baseline"]["candidate"] == "baseline"
    assert len(report["top_candidates"]) == 5
    assert all("alpha_weights" in item for item in report["top_candidates"])


def test_alpha_lab_cli_outputs_fixture_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from defiquant.cli import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "defiquant",
            "alpha-lab",
            "--fixture",
            "--windows",
            "30,60",
            "--max-candidates",
            "25",
            "--top",
            "3",
        ],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_count"] == 25
    assert len(payload["top_candidates"]) == 3
