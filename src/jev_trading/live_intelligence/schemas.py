"""Canonical immutable schemas for the active live-intelligence loop."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def require_utc_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware UTC datetimes")
            return value.astimezone(timezone.utc)
        return value


class MarketType(str, Enum):
    PERPETUAL = "PERPETUAL"
    SPOT = "SPOT"
    FUTURES = "FUTURES"
    OPTION = "OPTION"
    EVENT = "EVENT"


class DataEventType(str, Enum):
    TICK = "TICK"
    TRADE = "TRADE"
    BAR = "BAR"
    ORDER_BOOK = "ORDER_BOOK"
    FUNDING = "FUNDING"
    OPEN_INTEREST = "OPEN_INTEREST"
    LIQUIDATION = "LIQUIDATION"
    EXTERNAL = "EXTERNAL"


class Side(str, Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class PolicyAction(str, Enum):
    NO_TRADE = "NO_TRADE"
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    DATA_UNSAFE = "DATA_UNSAFE"


class JevState(str, Enum):
    ENTER = "ENTER"
    WAIT = "WAIT"
    EXIT = "EXIT"
    REDUCE = "REDUCE"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"


class ExecutionMode(str, Enum):
    PAPER = "PAPER"


class RiskStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class MarketTick(FrozenModel):
    source: str = Field(min_length=1)
    source_role: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    venue: str = Field(min_length=1)
    market_type: MarketType
    event_timestamp: datetime
    received_timestamp: datetime
    decision_timestamp: datetime | None = None
    execution_timestamp: datetime | None = None
    event_type: DataEventType
    sequence: int | None = None
    last: float | None = Field(default=None, gt=0)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    mid: float | None = Field(default=None, gt=0)
    mark: float | None = Field(default=None, gt=0)
    index: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0)
    buy_volume: float | None = Field(default=None, ge=0)
    sell_volume: float | None = Field(default=None, ge=0)
    bid_depth: float | None = Field(default=None, ge=0)
    ask_depth: float | None = Field(default=None, ge=0)
    open_interest: float | None = Field(default=None, ge=0)
    funding_rate: float | None = None
    liquidation_long: float | None = Field(default=None, ge=0)
    liquidation_short: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timing_and_prices(self) -> "MarketTick":
        if self.received_timestamp < self.event_timestamp:
            raise ValueError("received_timestamp cannot precede event_timestamp")
        if self.decision_timestamp is not None and self.decision_timestamp < self.received_timestamp:
            raise ValueError("decision_timestamp cannot precede received_timestamp")
        if self.execution_timestamp is not None and self.decision_timestamp is not None and self.execution_timestamp < self.decision_timestamp:
            raise ValueError("execution_timestamp cannot precede decision_timestamp")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")
        if self.mid is not None and self.bid is not None and self.ask is not None:
            expected_mid = (self.bid + self.ask) / 2
            if not math.isclose(self.mid, expected_mid, rel_tol=1e-6, abs_tol=1e-9):
                raise ValueError("mid must equal the bid/ask midpoint")
        return self

    @property
    def spread_fraction(self) -> float | None:
        if self.bid is None or self.ask is None or self.bid <= 0:
            return None
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2)


class SourceHealth(FrozenModel):
    source: str
    role: str
    healthy: bool
    last_event_timestamp: datetime | None = None
    last_received_timestamp: datetime | None = None
    age_ms: int | None = Field(default=None, ge=0)
    receive_age_ms: int | None = Field(default=None, ge=0)
    sequence_problems: tuple[str, ...] = ()
    duplicate_events: int = Field(default=0, ge=0)
    gaps: int = Field(default=0, ge=0)
    reason: str = "ok"


class DataQuality(FrozenModel):
    safe_for_trading: bool
    stale: bool
    missing_sources: tuple[str, ...] = ()
    timestamp_lag_ms: int | None = Field(default=None, ge=0)
    sequence_problems: tuple[str, ...] = ()
    duplicate_events: int = Field(default=0, ge=0)
    # A repeated observation of the same event is a benign retransmission, not
    # corruption; it is counted for telemetry but never blocks trading.
    historical_bar_gaps: int = Field(default=0, ge=0)
    gaps: int = Field(default=0, ge=0)
    inconsistent_prices: tuple[str, ...] = ()
    event_time_problems: tuple[str, ...] = ()
    bar_timestamp_problems: tuple[str, ...] = ()
    incoherent_data: tuple[str, ...] = ()
    source_health: dict[str, SourceHealth] = Field(default_factory=dict)


class PriceState(FrozenModel):
    source: str
    venue: str
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    mark: float | None = None
    index: float | None = None
    spread_fraction: float | None = Field(default=None, ge=0)


class TimeframeState(FrozenModel):
    timeframe: str
    # Canonical bucket contract: an observation is always one *completed*
    # bucket, so consumers never have to guess whether it is in progress.
    bucket_start: datetime
    bucket_end: datetime
    completed: bool
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)
    return_fraction: float | None = None
    realized_volatility: float | None = Field(default=None, ge=0)
    atr_fraction: float | None = Field(default=None, ge=0)
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    vwap: float | None = None
    distance_from_vwap: float | None = None
    range_position: float | None = Field(default=None, ge=0, le=1)
    previous_high: float | None = Field(default=None, gt=0)
    previous_low: float | None = Field(default=None, gt=0)
    volume_z: float | None = None
    trend_direction: str
    previous_trend_direction: str | None = None
    trend_persistence: float | None = Field(default=None, ge=0, le=1)
    trend_acceleration: float | None = None

    @model_validator(mode="after")
    def validate_bucket(self) -> "TimeframeState":
        if self.bucket_start >= self.bucket_end:
            raise ValueError("timeframe bucket_start must precede bucket_end")
        if self.timestamp != self.bucket_end:
            raise ValueError("timeframe timestamp must equal the bucket end")
        if not self.completed:
            raise ValueError("only completed timeframe buckets may be materialized")
        return self


class MarketStructureState(FrozenModel):
    trend_1m: str
    trend_5m: str
    trend_15m: str
    trend_1h: str
    trend_4h: str
    range_position_15m: float | None = Field(default=None, ge=0, le=1)
    distance_from_vwap_15m: float | None = None
    breakout_state: str = "UNKNOWN"
    compression_state: str = "UNKNOWN"


class VolatilityState(FrozenModel):
    realized_1m: float | None = Field(default=None, ge=0)
    realized_5m: float | None = Field(default=None, ge=0)
    realized_15m: float | None = Field(default=None, ge=0)
    realized_1h: float | None = Field(default=None, ge=0)
    atr_15m: float | None = Field(default=None, ge=0)
    percentile_15m: float | None = Field(default=None, ge=0, le=1)
    regime: str = "UNKNOWN"


class FlowState(FrozenModel):
    volume: float | None = Field(default=None, ge=0)
    buy_volume: float | None = Field(default=None, ge=0)
    sell_volume: float | None = Field(default=None, ge=0)
    imbalance: float | None = Field(default=None, ge=-1, le=1)
    trade_intensity: float | None = Field(default=None, ge=0)


class LiquidityState(FrozenModel):
    spread: float | None = Field(default=None, ge=0)
    bid_depth: float | None = Field(default=None, ge=0)
    ask_depth: float | None = Field(default=None, ge=0)
    depth: float | None = Field(default=None, ge=0)
    imbalance: float | None = Field(default=None, ge=-1, le=1)
    top_level_notional: float | None = Field(default=None, ge=0)
    estimated_slippage: float | None = Field(default=None, ge=0)


class DerivativesState(FrozenModel):
    open_interest: float | None = Field(default=None, ge=0)
    oi_change: float | None = None
    funding: float | None = None
    funding_change: float | None = None
    basis: float | None = None
    mark_index_divergence: float | None = None
    price_oi_relationship: str = "UNKNOWN"
    liquidations: dict[str, float] = Field(default_factory=dict)


class CrossMarketState(FrozenModel):
    spot_price: float | None = Field(default=None, gt=0)
    perp_basis: float | None = None
    correlation_24h: float | None = Field(default=None, ge=-1, le=1)
    observations: dict[str, float] = Field(default_factory=dict)


class MarketEvent(FrozenModel):
    event_type: str
    event_timestamp: datetime
    detection_timestamp: datetime
    instrument: str
    source: str
    severity: float = Field(ge=0, le=1)
    features_at_detection: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_detection_time(self) -> "MarketEvent":
        if self.detection_timestamp < self.event_timestamp:
            raise ValueError("detection cannot precede event time")
        return self


class PositionState(FrozenModel):
    side: Side = Side.FLAT
    quantity: float = Field(default=0.0, ge=0)
    entry_price: float | None = Field(default=None, gt=0)
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    funding_pnl_usd: float = 0.0
    time_in_position_seconds: int = Field(default=0, ge=0)
    current_stop: float | None = Field(default=None, gt=0)
    current_target: float | None = Field(default=None, gt=0)
    strategy_id: str | None = None
    strategy_version: str | None = None
    frontier_thesis: str | None = None
    jev_state: JevState = JevState.NO_TRADE
    opened_at: datetime | None = None

    @model_validator(mode="after")
    def validate_flat_state(self) -> "PositionState":
        if self.side == Side.FLAT and self.quantity != 0:
            raise ValueError("flat position must have zero quantity")
        if self.side != Side.FLAT and self.quantity <= 0:
            raise ValueError("open position requires positive quantity")
        return self


class MarketEnvironment(FrozenModel):
    timestamp: datetime
    decision_timestamp: datetime
    instrument: str
    venue: str
    market_type: MarketType
    price: PriceState
    timeframes: dict[str, TimeframeState]
    structure: MarketStructureState
    volatility: VolatilityState
    flow: FlowState
    liquidity: LiquidityState
    derivatives: DerivativesState
    events: tuple[MarketEvent, ...] = ()
    cross_market: CrossMarketState = CrossMarketState()
    position: PositionState = PositionState()
    data_quality: DataQuality

    @model_validator(mode="after")
    def validate_required_timeframes(self) -> "MarketEnvironment":
        missing = {"1m", "5m", "15m", "1h", "4h"} - set(self.timeframes)
        if missing:
            raise ValueError(f"missing required timeframes: {sorted(missing)}")
        if self.decision_timestamp < self.timestamp:
            raise ValueError("decision timestamp cannot precede market event time")
        future_timeframes = sorted(
            name for name, state in self.timeframes.items() if state.timestamp > self.decision_timestamp
        )
        if future_timeframes:
            raise ValueError(f"timeframe observations are in the future: {future_timeframes}")
        return self


class QuantQuestion(FrozenModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    question: str = Field(min_length=1)
    required: bool = True


class JevQuestion(QuantQuestion):
    pass


class StrategyHypothesis(FrozenModel):
    hypothesis_id: str
    timestamp: datetime
    regime: str
    regime_confidence: float = Field(ge=0, le=1)
    primary_strategy: str | None = None
    alternative_strategies: tuple[str, ...] = ()
    direction: Direction = Direction.NONE
    horizon_seconds: int | None = Field(default=None, gt=0)
    thesis: str
    supporting_evidence: tuple[str, ...] = ()
    required_quant_questions: tuple[QuantQuestion, ...] = ()
    entry_conditions: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    target_logic: str | None = None
    stop_logic: str | None = None
    maximum_holding_seconds: int | None = Field(default=None, gt=0)
    abandon_conditions: tuple[str, ...] = ()
    jev_questions: tuple[JevQuestion, ...] = ()
    abstain: bool = True
    reason: str
    conviction: float = Field(default=0.0, ge=0, le=1)
    model_version: str
    prompt_version: str
    prompt_hash: str | None = None
    provider: str | None = None
    model: str | None = None
    schema_version: str = "strategy-hypothesis-v1"
    temperature: float | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    interface_mode: str | None = None
    tool_name: str | None = None

    @model_validator(mode="after")
    def validate_authority_boundary(self) -> "StrategyHypothesis":
        forbidden = {"size", "quantity", "notional", "leverage", "order", "buy", "sell", "size_pct"}
        payload = self.model_dump()
        if forbidden & set(payload):
            raise ValueError("Frontier output contains execution-authority fields")
        if not self.abstain and not self.primary_strategy:
            raise ValueError("non-abstaining hypothesis requires a primary strategy")
        return self


class CandidateTrade(FrozenModel):
    side: Side
    entry_reference: float = Field(gt=0)
    target: float = Field(gt=0)
    stop: float = Field(gt=0)
    max_holding_seconds: int = Field(gt=0)
    strategy_id: str
    strategy_version: str
    entry_condition: str

    @model_validator(mode="after")
    def validate_side_levels(self) -> "CandidateTrade":
        if self.side == Side.FLAT:
            raise ValueError("candidate cannot be FLAT")
        if self.side == Side.LONG and not (self.stop < self.entry_reference < self.target):
            raise ValueError("long candidate requires stop < entry < target")
        if self.side == Side.SHORT and not (self.target < self.entry_reference < self.stop):
            raise ValueError("short candidate requires target < entry < stop")
        return self


class CostAssumptions(FrozenModel):
    fee_bps_per_side: float = Field(ge=0)
    slippage_bps_per_side: float = Field(ge=0)
    latency_bps: float = Field(ge=0)
    funding_rate_per_8h: float = 0.0
    holding_seconds: int = Field(gt=0)


class PathSample(FrozenModel):
    timestamp: datetime
    side: Side
    entry_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_fraction: float = Field(gt=0)
    stop_fraction: float = Field(gt=0)
    horizon_minutes: int = Field(gt=0)
    target_first: bool
    stop_first: bool
    timeout: bool
    return_fraction: float
    favorable_excursion: float
    adverse_excursion: float
    duration_seconds: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_outcome(self) -> "PathSample":
        if sum((self.target_first, self.stop_first, self.timeout)) != 1:
            raise ValueError("path sample must have exactly one outcome")
        if self.side == Side.FLAT:
            raise ValueError("path sample cannot be flat")
        if self.side == Side.LONG and not (self.stop_price < self.entry_price < self.target_price):
            raise ValueError("long path barriers must bracket entry")
        if self.side == Side.SHORT and not (self.target_price < self.entry_price < self.stop_price):
            raise ValueError("short path barriers must bracket entry")
        if self.favorable_excursion < 0 or self.adverse_excursion < 0:
            raise ValueError("path sample excursions must be non-negative")
        if self.target_first and self.return_fraction <= 0:
            raise ValueError("long/short target outcome must be positive")
        if self.stop_first and self.return_fraction >= 0:
            raise ValueError("stop outcome must be negative")
        return self


class QuantAnalysisResult(FrozenModel):
    analysis_name: str
    analyzer_version: str
    timestamp: datetime
    strategy_context: str = "GENERAL"
    sample_size: int | None = Field(default=None, ge=0)
    estimated_probability: float | None = Field(default=None, ge=0, le=1)
    expected_payoff: float | None = None
    expected_downside: float | None = None
    expected_return: float | None = None
    expected_mfe: float | None = None
    expected_mae: float | None = None
    expected_timeout_return: float | None = None
    expected_duration_seconds: float | None = Field(default=None, ge=0)
    uncertainty: float | None = Field(default=None, ge=0, le=1)
    horizon_seconds: int | None = Field(default=None, gt=0)
    cost_assumptions: CostAssumptions | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    path_probabilities: dict[str, float] | None = None
    empirical_sample_size: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_path_probabilities(self) -> "QuantAnalysisResult":
        if self.path_probabilities is not None:
            if set(self.path_probabilities) != {"target", "stop", "timeout"}:
                raise ValueError("path probabilities must contain target, stop, timeout")
            if any(not math.isfinite(value) or not 0 <= value <= 1 for value in self.path_probabilities.values()):
                raise ValueError("path probabilities must be finite and in [0, 1]")
            if abs(sum(self.path_probabilities.values()) - 1.0) > 1e-8:
                raise ValueError("path probabilities must sum to one")
        return self


class EconomicValue(FrozenModel):
    side: Side
    sample_size: int = Field(default=0, ge=0)
    probabilities: dict[str, float]
    gross_expected_payoff: float
    fees: float
    slippage: float
    funding: float
    latency: float
    net_expected_value: float
    expected_downside: float
    risk_reward: float | None = None
    expected_duration_seconds: float | None = Field(default=None, ge=0)
    cost_assumptions: CostAssumptions

    @model_validator(mode="after")
    def validate_probabilities(self) -> "EconomicValue":
        if set(self.probabilities) != {"target", "stop", "timeout"}:
            raise ValueError("probabilities must contain target, stop, timeout")
        for name, value in self.probabilities.items():
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} probability must be finite and in [0, 1]")
        if abs(sum(self.probabilities.values()) - 1.0) > 1e-8:
            raise ValueError("path probabilities must sum to one")
        return self


class QuantEvidence(FrozenModel):
    timestamp: datetime
    version: str = "quant-evidence-v1"
    regime: str
    regime_confidence: float = Field(ge=0, le=1)
    analyzer_results: tuple[QuantAnalysisResult, ...] = ()
    path: QuantAnalysisResult | None = None
    probabilities: dict[str, float] | None = None
    economic_value: EconomicValue | None = None
    cost_assumptions: CostAssumptions | None = None
    uncertainty: float | None = Field(default=None, ge=0, le=1)
    sample_size: int = Field(default=0, ge=0)
    limitations: tuple[str, ...] = ()
    analyzer_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence(self) -> "QuantEvidence":
        names = [result.analysis_name for result in self.analyzer_results]
        if len(names) != len(set(names)):
            raise ValueError("quant evidence cannot contain duplicate analyzer results")
        if self.path is not None and self.path.analysis_name != "path":
            raise ValueError("path evidence must be a path analysis")
        if self.probabilities is not None and set(self.probabilities) != {"target", "stop", "timeout"}:
            raise ValueError("quant evidence probabilities must contain target, stop, timeout")
        if self.path is not None and self.path.path_probabilities != self.probabilities:
            raise ValueError("quant evidence probabilities must match path evidence")
        if self.economic_value is not None and self.probabilities != self.economic_value.probabilities:
            raise ValueError("quant evidence probabilities must match economic value")
        return self


class JevRequest(FrozenModel):
    request_id: str
    timestamp: datetime
    environment: MarketEnvironment
    frontier_hypothesis: StrategyHypothesis
    quant_evidence: QuantEvidence
    candidate_trade: CandidateTrade
    questions: tuple[JevQuestion, ...]
    prompt_version: str


class JevEvaluation(FrozenModel):
    decision_id: str
    request_id: str
    timestamp: datetime
    valid_until: datetime
    probabilities: dict[str, float] = Field(default_factory=dict)
    target_probability: float | None = Field(default=None, ge=0, le=1)
    entry_quality: float | None = Field(default=None, ge=0, le=1)
    failure_risk: float | None = Field(default=None, ge=0, le=1)
    liquidity_quality: float | None = Field(default=None, ge=0, le=1)
    ratings: dict[str, float] = Field(default_factory=dict)
    answers: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    recommended_state: JevState
    reason: str
    model_version: str
    prompt_version: str
    prompt_hash: str | None = None
    provider: str | None = None
    model: str | None = None
    schema_version: str = "jev-evaluation-v1"
    temperature: float | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    interface_mode: str | None = None
    tool_name: str | None = None

    @model_validator(mode="after")
    def validate_evaluation(self) -> "JevEvaluation":
        if self.valid_until <= self.timestamp:
            raise ValueError("valid_until must be later than timestamp")
        for name, value in {**self.probabilities, **self.ratings}.items():
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be finite and in [0, 1]")
        return self


class PolicyDecision(FrozenModel):
    decision_id: str
    timestamp: datetime
    action: PolicyAction
    confidence: float = Field(default=0.0, ge=0, le=1)
    reasons: tuple[str, ...]
    candidate: CandidateTrade | None = None
    policy_version: str
    hypothesis_id: str | None = None

    @model_validator(mode="after")
    def validate_candidate_action(self) -> "PolicyDecision":
        entry = self.action in (PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT)
        if entry and self.candidate is None:
            raise ValueError("entry policy decision requires a candidate")
        if not entry and self.candidate is not None:
            raise ValueError("only entry decisions may carry a candidate")
        if self.action == PolicyAction.ENTER_LONG and (self.candidate is None or self.candidate.side != Side.LONG):
            raise ValueError("ENTER_LONG requires a LONG candidate")
        if self.action == PolicyAction.ENTER_SHORT and (self.candidate is None or self.candidate.side != Side.SHORT):
            raise ValueError("ENTER_SHORT requires a SHORT candidate")
        return self


class AccountState(FrozenModel):
    capital_usd: float = Field(gt=0)
    daily_realized_pnl_usd: float = 0.0
    peak_equity_usd: float | None = Field(default=None, gt=0)
    open_positions: int = Field(default=0, ge=0)
    correlated_exposure_usd: float = Field(default=0, ge=0)
    pending_orders: int = Field(default=0, ge=0)
    kill_switch: bool = False


class ExecutionState(FrozenModel):
    mode: ExecutionMode = ExecutionMode.PAPER
    orders_last_minute: int = Field(default=0, ge=0)
    cooldown_until: datetime | None = None
    feed_healthy: bool = True

    @model_validator(mode="after")
    def paper_only(self) -> "ExecutionState":
        if self.mode != ExecutionMode.PAPER:
            raise ValueError("live execution is disabled in this architecture")
        return self


class RiskDecision(FrozenModel):
    decision_id: str
    timestamp: datetime
    status: RiskStatus
    approved_quantity: float = Field(default=0.0, ge=0)
    approved_notional: float = Field(default=0.0, ge=0)
    maximum_loss_usd: float = Field(default=0.0, ge=0)
    reasons: tuple[str, ...]
    risk_config_version: str

    @model_validator(mode="after")
    def validate_status_values(self) -> "RiskDecision":
        if self.status == RiskStatus.APPROVED and (self.approved_quantity <= 0 or self.approved_notional <= 0):
            raise ValueError("approved risk decisions require positive quantity and notional")
        if self.status == RiskStatus.REJECTED and (self.approved_quantity != 0 or self.approved_notional != 0):
            raise ValueError("rejected risk decisions cannot approve quantity/notional")
        return self


class ExecutionIntent(FrozenModel):
    intent_id: str
    decision_id: str
    mode: ExecutionMode
    action: PolicyAction
    side: Side
    quantity: float = Field(gt=0)
    reference_price: float = Field(gt=0)
    stop: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)
    created_at: datetime
    earliest_execution_at: datetime
    expires_at: datetime | None = None
    strategy_id: str
    strategy_version: str = "unknown"

    @model_validator(mode="after")
    def validate_intent(self) -> "ExecutionIntent":
        if self.mode != ExecutionMode.PAPER:
            raise ValueError("paper execution is the only permitted mode")
        if self.earliest_execution_at <= self.created_at:
            raise ValueError("execution must occur strictly after decision")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("intent expiry must be after decision")
        if self.action == PolicyAction.ENTER_LONG and self.side != Side.LONG:
            raise ValueError("ENTER_LONG requires LONG side")
        if self.action == PolicyAction.ENTER_SHORT and self.side != Side.SHORT:
            raise ValueError("ENTER_SHORT requires SHORT side")
        if self.action in (PolicyAction.EXIT, PolicyAction.REDUCE) and self.side == Side.FLAT:
            raise ValueError("exit/reduce requires a non-flat side")
        if self.action in (PolicyAction.NO_TRADE, PolicyAction.DATA_UNSAFE, PolicyAction.HOLD):
            raise ValueError("non-execution actions cannot create an execution intent")
        return self


class PendingIntentState(FrozenModel):
    intent: ExecutionIntent
    risk_decision: RiskDecision
    # The policy that authorised the intent, so a pending order can be
    # revalidated against current risk without re-running intelligence.
    policy: PolicyDecision
    created_at: datetime

    @model_validator(mode="after")
    def validate_pending_state(self) -> "PendingIntentState":
        if self.created_at != self.intent.created_at:
            raise ValueError("pending state timestamp must match its intent")
        if self.intent.decision_id != self.risk_decision.decision_id:
            raise ValueError("pending intent and risk decision IDs must match")
        if self.policy.decision_id != self.intent.decision_id:
            raise ValueError("pending state policy must belong to the same decision")
        if self.policy.action != self.intent.action:
            raise ValueError("pending state policy action must match its intent")
        if self.risk_decision.status != RiskStatus.APPROVED:
            raise ValueError("pending state requires an approved risk decision")
        return self


class PaperFill(FrozenModel):
    fill_id: str
    intent_id: str
    decision_id: str
    execution_timestamp: datetime
    intended_price: float = Field(gt=0)
    fill_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    fee_usd: float = Field(ge=0)
    slippage_usd: float = Field(ge=0)
    funding_usd: float = 0.0
    status: str
    mode: ExecutionMode = ExecutionMode.PAPER
    # Provenance: which experiment, which instrument, which side, and which
    # market observation actually produced this price.
    experiment_id: str = ""
    symbol: str = ""
    side: Side = Side.FLAT
    price_source: str = "unknown"
    bar_timestamp: datetime | None = None
    execution_model_version: str = "paper-next-open-v1"
    funding_model: str = "DISABLED"


class TradeRecord(FrozenModel):
    trade_id: str
    strategy_id: str
    strategy_version: str
    side: Side
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    opened_at: datetime
    closed_at: datetime
    gross_pnl_usd: float
    fees_usd: float = Field(ge=0)
    slippage_usd: float = Field(ge=0)
    funding_usd: float
    net_pnl_usd: float
    entry_decision_id: str
    exit_decision_id: str
    entry_fill_id: str | None = None
    exit_fill_id: str | None = None


class Versions(FrozenModel):
    code_version: str
    experiment_id: str
    frontier_model: str
    frontier_prompt: str
    jev_model: str
    jev_prompt: str
    quant_analyzers: dict[str, str]
    policy: str
    risk: str
    strategy_registry: str
    frontier_prompt_hash: str = ""
    jev_prompt_hash: str = ""
    experiment_arm: str = "C"
    git_commit: str = ""
    config_hash: str = ""
    policy_hash: str = ""
    risk_hash: str = ""
    strategy_registry_hash: str = ""
    data_schema_version: str = "live-intelligence-v1"
    frontier_provider: str = ""
    jev_provider: str = ""
    frontier_capabilities: dict[str, bool] = Field(default_factory=dict)
    jev_capabilities: dict[str, bool] = Field(default_factory=dict)
    frontier_temperature: float | None = None
    frontier_max_output_tokens: int | None = Field(default=None, gt=0)
    jev_temperature: float | None = None
    jev_max_output_tokens: int | None = Field(default=None, gt=0)
    intelligence_schema_version: str = "live-intelligence-v1"


class ProviderFailureRecord(FrozenModel):
    component: str
    provider: str
    model: str
    category: str
    http_status: int | None = None
    retry_count: int = Field(default=0, ge=0)
    request_id: str | None = None
    message: str

    @classmethod
    def from_exception(cls, component: str, provider: str, model: str, exc: Exception) -> "ProviderFailureRecord":
        return cls(
            component=component, provider=provider, model=model,
            category=str(getattr(exc, "category", type(exc).__name__)),
            http_status=getattr(exc, "http_status", None),
            retry_count=int(getattr(exc, "retry_count", 0) or 0),
            request_id=getattr(exc, "request_id", None),
            message=type(exc).__name__,
        )


class DecisionRecord(FrozenModel):
    decision_id: str
    experiment_id: str
    timestamp: datetime
    market_environment: MarketEnvironment
    frontier_hypothesis: StrategyHypothesis | None = None
    quant_analyses: tuple[QuantAnalysisResult, ...] = ()
    quant_evidence: QuantEvidence | None = None
    jev_request: JevRequest | None = None
    jev_evaluation: JevEvaluation | None = None
    economic_value: EconomicValue | None = None
    policy_decision: PolicyDecision
    risk_decision: RiskDecision
    execution_intent: ExecutionIntent | None = None
    position_before: PositionState
    position_after: PositionState | None = None
    paper_fill: PaperFill | None = None
    eventual_outcome: dict[str, Any] | None = None
    counterfactual_without_jev: PolicyAction | None = None
    provider_failures: tuple[ProviderFailureRecord, ...] = ()
    # Why an outstanding paper intent was dropped instead of filled, so a
    # missing fill is always explainable from the ledger alone.
    pending_cancellation: str | None = None
    # Committed runtime state, so a checkpoint that trails the ledger can be
    # rebuilt from the ledger suffix instead of guessing.
    runtime_state: dict[str, Any] | None = None
    versions: Versions

    @model_validator(mode="after")
    def validate_pipeline_links(self) -> "DecisionRecord":
        if self.jev_request is not None and self.quant_evidence is not None and self.jev_request.quant_evidence != self.quant_evidence:
            raise ValueError("Jev request and decision Quant evidence must match")
        if self.jev_evaluation is not None:
            if self.jev_request is None:
                raise ValueError("Jev evaluation requires a Jev request")
            if self.jev_evaluation.request_id != self.jev_request.request_id:
                raise ValueError("Jev request/evaluation ids do not match")
        if self.execution_intent is not None:
            if self.risk_decision.status != RiskStatus.APPROVED:
                raise ValueError("execution intent requires approved risk")
            if self.execution_intent.decision_id != self.decision_id:
                raise ValueError("execution intent decision id mismatch")
            if self.policy_decision.action not in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT, PolicyAction.EXIT, PolicyAction.REDUCE}:
                raise ValueError("execution intent requires an execution policy action")
        if self.paper_fill is not None:
            if self.execution_intent is None or self.paper_fill.decision_id != self.execution_intent.decision_id:
                raise ValueError("paper fill requires matching execution intent")
        if self.economic_value is not None and self.economic_value.sample_size < 0:
            raise ValueError("economic value sample size cannot be negative")
        return self


class ResearchObservation(FrozenModel):
    observation_id: str
    timestamp: datetime
    kind: str
    decision_id: str | None = None
    experiment_id: str
    data: dict[str, Any]
    source_versions: dict[str, str] = Field(default_factory=dict)


class ResearchHypothesis(FrozenModel):
    hypothesis_id: str
    version: str = "v1"
    date_created: datetime
    author: str
    market: str
    regime: str
    strategy: str
    rationale: str
    required_evidence: tuple[str, ...]
    success_criteria: tuple[str, ...]
    failure_criteria: tuple[str, ...]
    status: str = Field(default="PROPOSED", pattern="^(PROPOSED|TESTING|SUPPORTED|REJECTED|DEFERRED|PROMOTED)$")
    revision: int = Field(default=1, ge=1)
    transition_evidence: tuple[str, ...] = ()
    supersedes: str | None = None
