"""Domain models for the standalone Patient Wheel agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


USDC_DECIMALS = 1_000_000


class DecisionStatus(str, Enum):
    WAIT = "wait"
    SELECTED = "selected"
    PREPARED_BASE_EXECUTION = "prepared_base_execution"
    PENDING_EXECUTION = "pending_execution"
    EXECUTION_REQUESTED = "execution_requested"


class StrategyType(str, Enum):
    CSP = "CSP"
    CC = "CC"


@dataclass(frozen=True)
class CapitalIntent:
    id: str
    amount_usdc: int
    status: str
    source_chain: str = "base"
    destination_chain: str = "arc"
    bucket_id: str | None = None
    receiver: str | None = None

    @property
    def amount_usd(self) -> float:
        return self.amount_usdc / USDC_DECIMALS

    @classmethod
    def from_backend_row(cls, row: dict[str, Any]) -> "CapitalIntent":
        return cls(
            id=str(row["id"]),
            amount_usdc=int(row["amount_usdc"]),
            status=str(row["status"]),
            source_chain=str(row.get("source_chain", "base")),
            destination_chain=str(row.get("destination_chain", "arc")),
            bucket_id=row.get("bucket_id"),
            receiver=row.get("receiver"),
        )


@dataclass(frozen=True)
class Opportunity:
    quote_id: str
    chain: str
    asset: str
    option_type: str
    strike: float
    spot: float
    premium: float
    expiry_days: int
    ttl_seconds: int
    available_amount: float
    mm_address: str | None = None
    otoken_address: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.CSP if self.option_type.upper() == "PUT" else StrategyType.CC

    @property
    def collateral_per_contract(self) -> float:
        if self.strategy_type == StrategyType.CSP:
            return max(self.strike, 0.0)
        return max(self.spot, 0.0)

    @property
    def capacity_usd(self) -> float:
        return max(0.0, self.available_amount) * self.collateral_per_contract

    @classmethod
    def from_backend_price(cls, asset: str, row: dict[str, Any]) -> "Opportunity":
        return cls(
            quote_id=str(row.get("quote_id") or row.get("id")),
            chain=str(row.get("chain", "base")),
            asset=asset.lower(),
            option_type=str(row.get("option_type", "")),
            strike=float(row.get("strike", 0)),
            spot=float(row.get("spot", 0)),
            premium=float(row.get("premium", 0)),
            expiry_days=int(row.get("expiry_days", 0)),
            ttl_seconds=int(row.get("ttl", 0)),
            available_amount=float(row.get("available_amount", 0)),
            mm_address=row.get("mm_address"),
            otoken_address=row.get("otoken_address"),
            raw=row,
        )


@dataclass(frozen=True)
class ExposureSnapshot:
    total_usd: float = 0.0
    by_asset: dict[str, float] = field(default_factory=dict)
    by_chain: dict[str, float] = field(default_factory=dict)
    by_strategy: dict[str, float] = field(default_factory=dict)

    def after(self, *, asset: str, chain: str, strategy: str, size_usd: float) -> "ExposureSnapshot":
        by_asset = dict(self.by_asset)
        by_chain = dict(self.by_chain)
        by_strategy = dict(self.by_strategy)
        by_asset[asset] = by_asset.get(asset, 0.0) + size_usd
        by_chain[chain] = by_chain.get(chain, 0.0) + size_usd
        by_strategy[strategy] = by_strategy.get(strategy, 0.0) + size_usd
        return ExposureSnapshot(
            total_usd=self.total_usd + size_usd,
            by_asset=by_asset,
            by_chain=by_chain,
            by_strategy=by_strategy,
        )


@dataclass(frozen=True)
class ScoreBreakdown:
    premium_apr: float
    premium_component: float
    expiry_component: float
    distance_component: float
    assignment_risk: float
    assignment_component: float
    capacity_component: float
    chain_component: float
    exposure_component: float
    total_score: float
    eligible: bool
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentDecision:
    intent_id: str | None
    selected_chain: str | None
    asset: str | None
    strategy_type: str | None
    quote_id: str | None
    size_usdc: int
    expected_premium_usdc: int
    score: float
    reasoning_trace: list[str]
    status: DecisionStatus
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    opportunity: dict[str, Any] | None = None
    score_breakdown: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data
