import pytest
from pydantic import ValidationError

from jev_trading.jev.client import Answer, Question
from jev_trading.jev.mock import MockJev, RandomJev
from jev_trading.jev.questions import MVP_QUESTIONS, load_questions

NAMES = ["breakout", "liquidity_ok", "vol_risk", "failure_regime", "trade_ok"]

STATE = {
    "ts": 1_700_000_000_000,
    "close": 50_000.0,
    "ret_15m": 0.001,
    "trend_score": 0.7,
    "funding_z": 0.3,
    "oi_change_1d": 0.02,
    "vol_regime": 0.4,
    "volume_z": 1.2,
    "realized_vol_30m": 0.0008,
}


def test_configured_questions_are_five_mvp():
    assert [q.name for q in MVP_QUESTIONS] == NAMES
    assert all(q.kind == "yesno" for q in MVP_QUESTIONS)
    assert all(q.instructions for q in MVP_QUESTIONS)
    assert load_questions() == MVP_QUESTIONS


def test_question_rejects_bad_kind():
    with pytest.raises(ValidationError):
        Question(name="x", kind="maybe", instructions="i")  # type: ignore[arg-type]


def test_answer_rejects_out_of_range():
    with pytest.raises(ValidationError):
        Answer(name="x", value=1.5)
    with pytest.raises(ValidationError):
        Answer(name="x", value=-0.01)


def test_mock_deterministic_pure_function():
    first = MockJev().ask(STATE, MVP_QUESTIONS)
    second = MockJev().ask(dict(STATE), list(MVP_QUESTIONS))
    assert first == second


def test_mock_outputs_in_unit_interval():
    extremes = [
        {},
        STATE,
        {"trend_score": 1e9, "volume_z": -1e9, "vol_regime": 1e9, "funding_z": 1e9},
        {"trend_score": -1e9, "volume_z": 1e9, "vol_regime": -1e9, "funding_z": -1e9},
    ]
    for st in extremes:
        for ans in MockJev().ask(st, MVP_QUESTIONS):
            assert 0.0 <= ans.value <= 1.0


def test_mock_missing_keys_neutral_no_crash():
    answers = MockJev().ask({}, MVP_QUESTIONS)
    assert [a.name for a in answers] == NAMES
    assert [a.value for a in answers] == [0.5] * 5


def test_mock_routes_by_state():
    strong = {a.name: a.value for a in MockJev().ask({"trend_score": 1.0, "volume_z": 3.0}, MVP_QUESTIONS)}
    weak = {a.name: a.value for a in MockJev().ask({"trend_score": -1.0, "volume_z": -3.0}, MVP_QUESTIONS)}
    assert strong["breakout"] > 0.5 > weak["breakout"]
    assert strong["liquidity_ok"] > 0.5 > weak["liquidity_ok"]
    hot = {a.name: a.value for a in MockJev().ask({"funding_z": 5.0}, MVP_QUESTIONS)}
    calm = {a.name: a.value for a in MockJev().ask({"funding_z": 0.0}, MVP_QUESTIONS)}
    assert hot["failure_regime"] > 0.5 > calm["failure_regime"]
    assert hot["trade_ok"] < calm["trade_ok"]


def test_random_jev_same_seed_same_answers():
    r = RandomJev(seed=42)
    first = r.ask(STATE, MVP_QUESTIONS)
    assert r.ask({}, MVP_QUESTIONS) == first  # state ignored; reseeded per ask()
    assert RandomJev(seed=42).ask(STATE, MVP_QUESTIONS) == first  # new instance


def test_random_jev_different_seed_differs():
    assert RandomJev(seed=1).ask({}, MVP_QUESTIONS) != RandomJev(seed=2).ask({}, MVP_QUESTIONS)


def test_random_jev_answers_all_questions_in_order():
    answers = RandomJev(seed=0).ask({}, MVP_QUESTIONS)
    assert [a.name for a in answers] == NAMES
    assert all(0.0 <= a.value <= 1.0 for a in answers)
