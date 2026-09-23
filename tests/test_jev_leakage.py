"""Point-in-time integrity for Jev context: must never receive future info."""
from jev_trading.jev.adapter import AdapterJev
from jev_trading.jev.client import Question


def test_adapter_rejects_future_state():
    adapter = AdapterJev()
    bad = {"trend_score": 0.5, "future_return_15": 0.02}
    try:
        adapter.ask(bad, [Question(name="breakout", kind="yesno", instructions="?")])
        assert False, "should have rejected future label"
    except ValueError as e:
        assert "leak" in str(e).lower() or "future" in str(e).lower()


def test_adapter_accepts_valid_context():
    adapter = AdapterJev()
    good = {
        "trend_score": 0.5, "volume_z": 1.0, "funding_z": 0.3,
        "vol_regime": 0.1, "p_up_15": 0.65, "p_dn_15": 0.35,
        "expected_return_15": 0.003, "candidate_side": "long",
    }
    answers = adapter.ask(good, [
        Question(name="breakout", kind="yesno", instructions="?"),
        Question(name="trade_ok", kind="yesno", instructions="?"),
    ])
    assert len(answers) == 2
    for a in answers:
        assert 0.0 <= a.value <= 1.0
