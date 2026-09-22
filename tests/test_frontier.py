import pytest
from pydantic import ValidationError

import polars as pl
from pathlib import Path

from jev_trading.frontier.world_model import Regime, PNLStats, WorldDigest, classify_regime, build_digest
from jev_trading.frontier.strategy import StrategyGenerator, ParameterArtifact, QuantParams, JevParams, PolicyParams
from jev_trading.frontier.overseer import Overseer, KillSignal
from jev_trading.frontier.guardrail import Guardrail
from jev_trading.jev.client import Question
from jev_trading.jev.questions import MVP_QUESTIONS
from jev_trading.contracts import BAR_COLUMNS


def _make_state(**overrides):
    base = {
        "ts": 1_700_000_000_000,
        "close": 50_000.0,
        "ret_15m": 0.001,
        "trend_score": 0.0,
        "funding_z": 0.0,
        "oi_change_1d": 0.02,
        "vol_regime": 0.0,
        "volume_z": 0.0,
        "rv_30m": 0.0008,
    }
    base.update(overrides)
    return base


def _make_bars():
    return pl.DataFrame(
        {
            "timestamp": [1_700_000_000_000],
            "open": [50_000.0],
            "high": [50_100.0],
            "low": [49_900.0],
            "close": [50_000.0],
            "volume": [100.0],
            "funding_rate": [0.0001],
            "open_interest": [1_000_000.0],
        },
        schema={c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS},
    ).with_columns(pl.lit(1_700_000_000_000).cast(pl.Int64).alias("timestamp"))


def test_classify_regime_high_vol_trending():
    state = _make_state(vol_regime=1.0, trend_score=0.5)
    regime = classify_regime(state)
    assert regime.volatility == "high"
    assert regime.trend == "trending"


def test_classify_regime_low_vol_flat():
    state = _make_state(vol_regime=-1.0, trend_score=0.05)
    regime = classify_regime(state)
    assert regime.volatility == "low"
    assert regime.trend == "flat"


def test_classify_regime_funding_extreme_long():
    state = _make_state(funding_z=3.0)
    regime = classify_regime(state)
    assert regime.funding == "extreme_long"


def test_classify_regime_funding_extreme_short():
    state = _make_state(funding_z=-3.0)
    regime = classify_regime(state)
    assert regime.funding == "extreme_short"


def test_classify_regime_funding_extreme_short_2():
    state = _make_state(funding_z=-3.0)
    regime = classify_regime(state)
    assert regime.funding == "extreme_short"


def test_classify_regime_normal():
    state = _make_state(vol_regime=0.1, trend_score=0.05, funding_z=0.5)
    regime = classify_regime(state)
    assert regime.volatility == "normal"
    assert regime.trend == "flat"
    assert regime.funding == "normal"


def test_build_digest_basic():
    bars = _make_bars()
    state_dicts = [
        {
            "ts": 1,
            "close": 50_000.0,
            "ret_15m": 0.001,
            "rv_30m": 0.0008,
            "trend_score": 0.1,
            "funding_z": 0.0,
            "volume_z": 0.0,
            "vol_regime": 0.0,
        }
    ]
    jev_answers = [{"trade_ok": 0.8, "failure_regime": 0.2}]
    p_up_values = [0.5, 0.6, 0.7]
    outcomes = [0, 1, 1]
    fees = [1.0]
    ts = 1_700_000_000_000

    digest = build_digest(bars, state_dicts, jev_answers, p_up_values, outcomes, fees, ts)

    assert isinstance(digest, WorldDigest)
    assert digest.regime.trend == "flat"
    assert isinstance(digest.calibration_drift, float)
    assert isinstance(digest.jev_distributions, dict)
    assert len(digest.jev_distributions) > 0
    assert digest.pnl.n_trades == 1


def test_build_digest_empty_jev_answers():
    bars = _make_bars()
    state_dicts = [_make_state()]
    digest = build_digest(bars, state_dicts, [], [0.5], [1], [0.01], 1_700_000_000_000)
    assert digest.jev_distributions == {}


def _make_digest(**overrides):
    defaults = {
        "ts": 1_700_000_000_000,
        "regime": Regime(volatility="normal", trend="flat", funding="normal", duration_bars=100),
        "calibration_drift": 0.05,
        "jev_distributions": {"trade_ok": [0.0] * 3 + [1.0] * 4 + [0.0] * 3},
        "pnl": PNLStats(gross_per_trade=0.01, net_per_trade=0.0, cost_per_trade=0.001, cost_ratio=0.1, n_trades=10, win_rate=0.5),
        "feature_summary": {"mean_ret_15m": 0.001},
    }
    defaults.update(overrides)
    return WorldDigest(**defaults)


def test_strategy_generator_high_vol_raises_threshold():
    digest = _make_digest(regime=Regime(volatility="high", trend="trending", funding="normal", duration_bars=100))
    artifact = StrategyGenerator().generate(digest)
    assert artifact.quant.p_up_threshold > 0.60


def test_strategy_generator_low_vol_lowers_threshold():
    digest = _make_digest(regime=Regime(volatility="low", trend="flat", funding="normal", duration_bars=100))
    artifact = StrategyGenerator().generate(digest)
    assert artifact.quant.p_up_threshold < 0.60


def test_strategy_generator_funding_extreme_adds_question():
    digest = _make_digest(regime=Regime(volatility="normal", trend="flat", funding="extreme_long", duration_bars=100))
    artifact = StrategyGenerator().generate(digest)
    question_names = [q.name for q in artifact.jev.questions]
    assert "funding_extreme" in question_names
    assert len(question_names) > 5


def test_strategy_generator_funding_normal_no_extra_question():
    digest = _make_digest(regime=Regime(volatility="normal", trend="flat", funding="normal", duration_bars=100))
    artifact = StrategyGenerator().generate(digest)
    question_names = [q.name for q in artifact.jev.questions]
    assert len(question_names) == 5
    assert question_names == [q.name for q in MVP_QUESTIONS]


def test_strategy_generator_save_artifact(tmp_path):
    digest = _make_digest()
    artifact = StrategyGenerator().generate(digest)
    exp_dir = tmp_path / "experiment"
    result = StrategyGenerator().save_artifact(artifact, str(exp_dir))
    assert isinstance(result, Path)
    assert (exp_dir / "frontier_strategy.yaml").exists()


def test_strategy_generator_cost_erosion_raises_edge():
    digest = _make_digest(pnl=PNLStats(gross_per_trade=0.01, net_per_trade=0.0, cost_per_trade=0.01, cost_ratio=0.7, n_trades=10, win_rate=0.5))
    artifact = StrategyGenerator().generate(digest)
    assert artifact.quant.min_edge_over_cost > 2.0


def test_overseer_regime_change():
    digest1 = _make_digest(regime=Regime(volatility="high", trend="trending", funding="normal", duration_bars=100))
    digest2 = _make_digest(regime=Regime(volatility="normal", trend="flat", funding="normal", duration_bars=100))
    overseer = Overseer()
    overseer.review(digest1)
    signals = overseer.review(digest2)
    assert any("regime_change" in s.trigger for s in signals)
    assert any(s.severity == "warning" for s in signals)


def test_overseer_cost_erosion():
    digest = _make_digest(pnl=PNLStats(gross_per_trade=0.01, net_per_trade=0.0, cost_per_trade=0.009, cost_ratio=0.9, n_trades=10, win_rate=0.5))
    overseer = Overseer()
    signals = overseer.review(digest)
    assert any("cost_erosion" in s.trigger for s in signals)
    assert any(s.severity == "warning" for s in signals)


def test_overseer_calibration_drift():
    digest = _make_digest(calibration_drift=0.2)
    overseer = Overseer()
    signals = overseer.review(digest)
    assert any("calibration_drift" in s.trigger for s in signals)
    assert any(s.severity == "warning" for s in signals)


def test_overseer_jev_no_info():
    digest = _make_digest(jev_distributions={"trade_ok": [0.0, 0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.0, 0.0, 0.0]})
    overseer = Overseer()
    signals = overseer.review(digest)
    assert any("jev_no_info" in s.trigger for s in signals)
    assert any(s.severity == "warning" for s in signals)


def test_overseer_negative_edge():
    digest = _make_digest(pnl=PNLStats(gross_per_trade=0.01, net_per_trade=-0.001, cost_per_trade=0.002, cost_ratio=0.6, n_trades=10, win_rate=0.3))
    overseer = Overseer()
    signals = overseer.review(digest)
    assert any(s.severity == "critical" and "negative_edge" in s.trigger for s in signals)
    assert overseer.should_halt(signals) is True


def test_overseer_should_halt_critical():
    signals = [KillSignal(trigger="test", severity="critical", action="halt")]
    assert Overseer().should_halt(signals) is True


def test_overseer_should_halt_no_critical():
    signals = [KillSignal(trigger="test", severity="warning", action="review")]
    assert Overseer().should_halt(signals) is False


def test_guardrail_can_promote_no_gate():
    digest = _make_digest()
    artifact = StrategyGenerator().generate(digest)
    guardrail = Guardrail()
    assert guardrail.can_promote("EXP-TEST", digest, artifact, gate_passed=False) is False


def test_guardrail_can_promote_negative_edge():
    digest = _make_digest(pnl=PNLStats(gross_per_trade=0.01, net_per_trade=-0.001, cost_per_trade=0.002, cost_ratio=0.6, n_trades=10, win_rate=0.3))
    artifact = StrategyGenerator().generate(digest)
    guardrail = Guardrail()
    assert guardrail.can_promote("EXP-TEST", digest, artifact, gate_passed=True) is False


def test_guardrail_can_promote_artifact_missing():
    digest = _make_digest()
    artifact = StrategyGenerator().generate(digest)
    guardrail = Guardrail()
    assert guardrail.can_promote("EXP-NOFILE", digest, artifact, gate_passed=True) is False


def test_guardrail_can_promote_success(tmp_path):
    digest = _make_digest()
    sg = StrategyGenerator()
    artifact = sg.generate(digest)
    exp_dir = tmp_path / "experiments" / "EXP-TEST"
    sg.save_artifact(artifact, str(exp_dir))
    guardrail = Guardrail(exp_base=str(exp_dir.parent))
    assert guardrail.can_promote("EXP-TEST", digest, artifact, gate_passed=True) is True


def test_guardrail_validate_experiment_structure(tmp_path):
    exp_dir = tmp_path / "experiments" / "EXP-TEST"
    exp_dir.mkdir(parents=True)
    (exp_dir / "frontier_strategy.yaml").write_text("test")
    (exp_dir / "experiment.yaml").write_text("test")
    guardrail = Guardrail(exp_base=str(tmp_path / "experiments"))
    assert guardrail.validate_experiment_structure("EXP-TEST") is True


def test_guardrail_validate_experiment_structure_missing():
    guardrail = Guardrail(exp_base="experiments/")
    assert guardrail.validate_experiment_structure("EXP-NOFILE") is False
