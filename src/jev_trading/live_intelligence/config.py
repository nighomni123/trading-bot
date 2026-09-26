"""Validated configuration for the active paper-only system."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import CostAssumptions


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class MarketConfig(StrictConfig):
    instrument: str = "BTCUSDT_PERP"
    venue: str = "binance-futures"
    market_type: Literal["PERPETUAL"] = "PERPETUAL"
    primary_source: str = "binance"
    primary_source_role: str = "primary"
    secondary_source: Literal["bybit"] | None = "bybit"
    required_source_roles: tuple[str, ...] = ("primary",)
    warmup_bars: int = Field(default=3000, ge=300)
    poll_seconds: float = Field(default=15.0, gt=0)


class ProviderConfig(StrictConfig):
    provider: Literal["disabled", "openai_compatible", "replay"] = "disabled"
    base_url: str | None = None
    model: str = "configured-at-deployment"
    api_key_env: str = "OPENROUTER_API_KEY"
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_output_tokens: int = Field(default=1800, gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    max_retry_delay_seconds: float = Field(default=30.0, gt=0, le=300)
    supports_response_format: bool = False
    supports_tool_calling: bool = False
    supports_reasoning: bool = False
    supports_vision: bool = False


class FrontierConfig(StrictConfig):
    prompt_version: str = "frontier-strategist-v1"
    prompt_file: str = "frontier/prompts/strategist_v1.txt"
    periodic_seconds: int = Field(default=900, gt=0)
    min_call_interval_seconds: int = Field(default=900, ge=0)
    provider: ProviderConfig = ProviderConfig()
    event_severity_threshold: float = Field(default=0.6, ge=0, le=1)
    max_calls_per_hour: int = Field(default=12, ge=0)


class JevConfig(StrictConfig):
    prompt_version: str = "jev-evaluator-v1"
    prompt_file: str = "jev/prompts/evaluator_v1.txt"
    provider: ProviderConfig = ProviderConfig()
    validity_seconds: int = Field(default=60, gt=0)
    min_call_interval_seconds: int = Field(default=0, ge=0)
    max_calls_per_hour: int = Field(default=60, ge=0)
    minimum_target_probability: float = Field(default=0.55, ge=0, le=1)
    maximum_stop_probability: float = Field(default=0.45, ge=0, le=1)
    minimum_entry_quality: float = Field(default=0.55, ge=0, le=1)
    maximum_failure_probability: float = Field(default=0.45, ge=0, le=1)
    minimum_liquidity_quality: float = Field(default=0.0, ge=0, le=1)


class PolicyThresholds(StrictConfig):
    minimum_net_expected_value: float = Field(default=0.0)
    minimum_path_sample_size: int = Field(default=30, ge=1)
    minimum_liquidity_notional: float = Field(default=100.0, ge=0)
    maximum_failure_probability: float = Field(default=0.45, ge=0, le=1)
    target_atr_multiple: float = Field(default=2.0, gt=0)
    stop_atr_multiple: float = Field(default=1.0, gt=0)
    minimum_distance_bps: float = Field(default=5.0, gt=0)
    maximum_holding_seconds: int = Field(default=900, gt=0)


class QuantConfig(StrictConfig):
    volatility_expansion_ratio: float = Field(default=1.5, gt=1)
    volume_shock_z: float = Field(default=2.0, gt=0)
    return_shock_sigma: float = Field(default=3.0, gt=0)
    oi_shock_fraction: float = Field(default=0.03, gt=0)
    funding_extreme: float = Field(default=0.0005, ge=0)
    trend_acceleration_threshold: float = Field(default=0.001, gt=0)
    path_minimum_samples: int = Field(default=30, ge=1)


class RiskLimits(StrictConfig):
    maximum_position_notional_usd: float = Field(default=1000.0, gt=0)
    maximum_leverage: float = Field(default=2.0, gt=0)
    maximum_capital_at_risk_usd: float = Field(default=50.0, gt=0)
    maximum_trade_loss_usd: float = Field(default=20.0, gt=0)
    maximum_daily_loss_usd: float = Field(default=50.0, gt=0)
    maximum_drawdown_pct: float = Field(default=10.0, ge=0, le=100)
    maximum_open_positions: int = Field(default=1, ge=1)
    maximum_correlated_exposure_usd: float = Field(default=1000.0, gt=0)
    maximum_spread_bps: float = Field(default=5.0, ge=0)
    maximum_slippage_bps: float = Field(default=5.0, ge=0)
    minimum_liquidity_notional: float = Field(default=100.0, ge=0)
    maximum_orders_per_minute: int = Field(default=12, ge=1)
    stale_data_ms: int = Field(default=15_000, gt=0)
    cooldown_seconds: int = Field(default=0, ge=0)
    kill_switch: bool = False
    risk_per_trade_pct: float = Field(default=0.5, gt=0, le=100)


class CostsConfig(StrictConfig):
    fee_bps_per_side: float = Field(default=5.0, ge=0)
    slippage_bps_per_side: float = Field(default=2.0, ge=0)
    latency_bps: float = Field(default=1.0, ge=0)
    fee_multiplier: float = Field(default=1.0, gt=0)

    def assumptions(self, holding_seconds: int, funding_rate: float = 0.0) -> CostAssumptions:
        return CostAssumptions(
            fee_bps_per_side=self.fee_bps_per_side * self.fee_multiplier,
            slippage_bps_per_side=self.slippage_bps_per_side,
            latency_bps=self.latency_bps,
            funding_rate_per_8h=funding_rate,
            holding_seconds=holding_seconds,
        )


class PaperConfig(StrictConfig):
    capital_usd: float = Field(default=10_000.0, gt=0)
    funding_interval_hours: int = Field(default=8, gt=0)
    # DISABLED means funding is explicitly not charged and the manifest reports
    # so; LIVE charges only when the position spans a funding interval.
    funding_model: Literal["DISABLED", "LIVE"] = "DISABLED"


class ResearchConfig(StrictConfig):
    root: str = "research"
    hourly: bool = True
    daily: bool = True
    weekly: bool = True
    strategy_registry: str = "research/strategies/registry.json"
    hypothesis_registry: str = "research/hypotheses"
    observations_file: str = "research/observations.jsonl"
    postmortems: str = "research/postmortems"


class ObservabilityConfig(StrictConfig):
    log_file: str = "research/runtime/components.jsonl"
    metrics_file: str = "research/runtime/metrics.json"
    retain_component_logs_days: int = Field(default=30, ge=1)


class LiveSettings(StrictConfig):
    schema_version: Literal[1] = 1
    experiment_id: str = "SHADOW-BTCUSDT-001"
    execution_mode: Literal["PAPER"] = "PAPER"
    code_version: str = "live-intelligence-v1"
    market: MarketConfig = Field(default_factory=MarketConfig)
    frontier: FrontierConfig = Field(default_factory=FrontierConfig)
    jev: JevConfig = Field(default_factory=JevConfig)
    quant: QuantConfig = Field(default_factory=QuantConfig)
    policy: PolicyThresholds = Field(default_factory=PolicyThresholds)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    costs: CostsConfig = Field(default_factory=CostsConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    ledger_root: str = "research/runtime/ledger"
    pending_intent_ttl_seconds: int = Field(default=120, gt=0)
    strategy_registry_version: str = "strategy-registry-v1"

    @model_validator(mode="after")
    def validate_paper_only(self) -> "LiveSettings":
        if self.execution_mode != "PAPER":
            raise ValueError("execution_mode must remain PAPER")
        if self.market.instrument != "BTCUSDT_PERP":
            raise ValueError("the first live experiment is fixed to BTCUSDT_PERP")
        if "primary" not in self.market.required_source_roles:
            raise ValueError("required_source_roles must include primary")
        if self.market.market_type != "PERPETUAL":
            raise ValueError("the first experiment requires perpetual market data")
        openrouter_base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        if self.frontier.provider.provider == "openai_compatible" and not (self.frontier.provider.base_url or openrouter_base):
            raise ValueError("openai_compatible Frontier requires base_url")
        if self.jev.provider.provider == "openai_compatible" and not (self.jev.provider.base_url or openrouter_base):
            raise ValueError("openai_compatible Jev requires base_url")
        if self.risk.maximum_trade_loss_usd > self.risk.maximum_daily_loss_usd:
            raise ValueError("trade-loss limit cannot exceed daily-loss limit")
        return self


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_project_env(path: str | Path | None = None) -> dict[str, str]:
    """Load a small project-root .env without overriding the real environment."""
    candidate = Path(path) if path is not None else project_root() / ".env"
    if not candidate.is_absolute():
        candidate = project_root() / candidate
    if not candidate.is_file():
        return {}
    loaded: dict[str, str] = {}
    for line_number, raw_line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid .env assignment at {candidate}:{line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not all(character.isalnum() or character == "_" for character in key):
            raise ValueError(f"invalid .env variable name at {candidate}:{line_number}")
        if key not in os.environ:
            os.environ[key] = _unquote_env_value(value)
            loaded[key] = os.environ[key]
    return loaded


def _apply_provider_env(payload: dict) -> dict:
    shared_base = os.getenv("OPENROUTER_BASE_URL")
    for component, prefix in (("frontier", "FRONTIER"), ("jev", "JEV")):
        section = payload.setdefault(component, {})
        provider = section.setdefault("provider", {})
        if value := os.getenv(f"{prefix}_PROVIDER"):
            provider["provider"] = value
        if value := os.getenv(f"{prefix}_MODEL"):
            provider["model"] = value
        if value := os.getenv(f"{prefix}_BASE_URL") or shared_base:
            provider["base_url"] = value
        if value := os.getenv(f"{prefix}_API_KEY_ENV"):
            provider["api_key_env"] = value
        for capability in ("RESPONSE_FORMAT", "TOOL_CALLING", "REASONING", "VISION"):
            name = f"{prefix}_SUPPORTS_{capability}"
            if name in os.environ:
                provider[f"supports_{capability.lower()}"] = env_bool(name)
    return payload


def load_settings(path: str | Path = "configs/live.json") -> LiveSettings:
    load_project_env()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = project_root() / candidate
    payload = _apply_provider_env(json.loads(candidate.read_text()))
    return LiveSettings.model_validate(payload)
