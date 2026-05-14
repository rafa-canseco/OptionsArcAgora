"""Decision and scoring logic for the Patient Wheel agent."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from agent.metavault.models import (
    AgentDecision,
    CapitalIntent,
    DecisionStatus,
    ExposureSnapshot,
    Opportunity,
    ScoreBreakdown,
    USDC_DECIMALS,
)


@dataclass(frozen=True)
class AgentConfig:
    min_score: float = 58.0
    min_quote_ttl_seconds: int = 60
    min_expiry_days: int = 1
    max_expiry_days: int = 45
    max_asset_exposure_ratio: float = 0.60
    max_chain_exposure_ratio: float = 0.80
    max_strategy_exposure_ratio: float = 0.75
    base_execution_ready: bool = True
    solana_execution_ready: bool = False


class PatientWheelAgent:
    """Rank b1nary quotes and produce a deployment decision."""

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or AgentConfig()

    def decide(
        self,
        intents: list[CapitalIntent],
        opportunities: list[Opportunity],
        exposure: ExposureSnapshot | None = None,
    ) -> AgentDecision:
        exposure = exposure or ExposureSnapshot()
        eligible_intents = [
            i
            for i in intents
            if i.status == "waiting_to_be_deployed" and i.amount_usdc > 0
        ]
        if not eligible_intents:
            return self._wait(
                "No capital_movement_intents are waiting_to_be_deployed."
            )

        intent = max(eligible_intents, key=lambda i: i.amount_usdc)
        scored = [
            (opp, self.score_opportunity(opp, intent, exposure))
            for opp in opportunities
        ]
        eligible = [(opp, score) for opp, score in scored if score.eligible]
        if not eligible:
            trace = ["No eligible b1nary quote passed agent risk filters."]
            for opp, score in scored[:6]:
                trace.append(
                    f"Rejected {opp.asset}/{opp.chain}/{opp.quote_id}: "
                    f"{score.rejection_reason or 'score below threshold'}."
                )
            return self._wait(*trace, intent_id=intent.id)

        selected, score = max(eligible, key=lambda pair: pair[1].total_score)
        size_usd = min(intent.amount_usd, selected.capacity_usd)
        contracts = size_usd / selected.collateral_per_contract
        expected_premium_usd = max(0.0, contracts * selected.premium)

        status = self._selected_status(selected.chain)
        trace = self._reasoning_trace(
            intent=intent,
            selected=selected,
            score=score,
            size_usd=size_usd,
            expected_premium_usd=expected_premium_usd,
            compared=len(opportunities),
        )

        return AgentDecision(
            intent_id=intent.id,
            selected_chain=selected.chain,
            asset=selected.asset,
            strategy_type=selected.strategy_type.value,
            quote_id=selected.quote_id,
            size_usdc=round(size_usd * USDC_DECIMALS),
            expected_premium_usdc=round(expected_premium_usd * USDC_DECIMALS),
            score=round(score.total_score, 4),
            reasoning_trace=trace,
            status=status,
            opportunity=selected.raw or _public_opportunity(selected),
            score_breakdown=score.to_dict(),
        )

    def score_opportunity(
        self,
        opportunity: Opportunity,
        intent: CapitalIntent,
        exposure: ExposureSnapshot,
    ) -> ScoreBreakdown:
        rejection = self._reject_reason(opportunity, intent, exposure)
        if rejection:
            return ScoreBreakdown(
                premium_apr=0.0,
                premium_component=0.0,
                expiry_component=0.0,
                distance_component=0.0,
                assignment_risk=1.0,
                assignment_component=0.0,
                capacity_component=0.0,
                chain_component=0.0,
                exposure_component=0.0,
                total_score=0.0,
                eligible=False,
                rejection_reason=rejection,
            )

        collateral = opportunity.collateral_per_contract
        premium_yield = opportunity.premium / collateral if collateral else 0.0
        premium_apr = premium_yield * 365 / max(opportunity.expiry_days, 1)
        premium_component = min(35.0, premium_apr * 100 * 1.75)

        expiry_component = _triangular_score(
            opportunity.expiry_days,
            low=self.config.min_expiry_days,
            target=14,
            high=self.config.max_expiry_days,
            weight=15.0,
        )
        distance = _distance_to_strike(opportunity)
        distance_component = _triangular_score(
            distance,
            low=0.03,
            target=0.15,
            high=0.35,
            weight=15.0,
        )
        assignment_risk = _assignment_risk_proxy(opportunity, distance)
        assignment_component = (1.0 - assignment_risk) * 15.0
        size_usd = min(intent.amount_usd, opportunity.capacity_usd)
        capacity_component = min(10.0, (size_usd / intent.amount_usd) * 10.0)
        chain_component = 6.0 if opportunity.chain == "base" else 4.0
        exposure_component = self._exposure_component(opportunity, exposure, size_usd)

        total = (
            premium_component
            + expiry_component
            + distance_component
            + assignment_component
            + capacity_component
            + chain_component
            + exposure_component
        )
        eligible = total >= self.config.min_score
        return ScoreBreakdown(
            premium_apr=premium_apr,
            premium_component=premium_component,
            expiry_component=expiry_component,
            distance_component=distance_component,
            assignment_risk=assignment_risk,
            assignment_component=assignment_component,
            capacity_component=capacity_component,
            chain_component=chain_component,
            exposure_component=exposure_component,
            total_score=total,
            eligible=eligible,
            rejection_reason=None if eligible else "score below minimum threshold",
        )

    def decision_hash(self, decision: AgentDecision) -> str:
        payload = json.dumps(decision.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _reject_reason(
        self,
        opportunity: Opportunity,
        intent: CapitalIntent,
        exposure: ExposureSnapshot,
    ) -> str | None:
        if not opportunity.quote_id:
            return "missing quote_id"
        if opportunity.ttl_seconds < self.config.min_quote_ttl_seconds:
            return "quote ttl too short"
        if opportunity.expiry_days < self.config.min_expiry_days:
            return "expiry too soon"
        if opportunity.expiry_days > self.config.max_expiry_days:
            return "expiry too far"
        if opportunity.premium <= 0:
            return "non-positive premium"
        if opportunity.spot <= 0 or opportunity.strike <= 0:
            return "missing spot or strike"
        if opportunity.capacity_usd <= 0:
            return "no quote capacity"
        size_usd = min(intent.amount_usd, opportunity.capacity_usd)
        projected = exposure.after(
            asset=opportunity.asset,
            chain=opportunity.chain,
            strategy=opportunity.strategy_type.value,
            size_usd=size_usd,
        )
        if exposure.total_usd > 0:
            denominator = max(projected.total_usd, 1.0)
            if projected.by_asset[opportunity.asset] / denominator > self.config.max_asset_exposure_ratio:
                return "asset exposure limit"
            if projected.by_chain[opportunity.chain] / denominator > self.config.max_chain_exposure_ratio:
                return "chain exposure limit"
            if projected.by_strategy[opportunity.strategy_type.value] / denominator > self.config.max_strategy_exposure_ratio:
                return "strategy exposure limit"
        return None

    def _exposure_component(
        self,
        opportunity: Opportunity,
        exposure: ExposureSnapshot,
        size_usd: float,
    ) -> float:
        projected = exposure.after(
            asset=opportunity.asset,
            chain=opportunity.chain,
            strategy=opportunity.strategy_type.value,
            size_usd=size_usd,
        )
        if exposure.total_usd <= 0:
            return 10.0
        denominator = max(projected.total_usd, 1.0)
        concentration = max(
            projected.by_asset[opportunity.asset] / denominator,
            projected.by_chain[opportunity.chain] / denominator,
            projected.by_strategy[opportunity.strategy_type.value] / denominator,
        )
        return max(0.0, (1.0 - concentration) * 10.0)

    def _selected_status(self, chain: str) -> DecisionStatus:
        if chain == "base" and self.config.base_execution_ready:
            return DecisionStatus.PREPARED_BASE_EXECUTION
        if chain == "solana" and self.config.solana_execution_ready:
            return DecisionStatus.EXECUTION_REQUESTED
        return DecisionStatus.PENDING_EXECUTION

    def _reasoning_trace(
        self,
        *,
        intent: CapitalIntent,
        selected: Opportunity,
        score: ScoreBreakdown,
        size_usd: float,
        expected_premium_usd: float,
        compared: int,
    ) -> list[str]:
        return [
            f"Compared {compared} live b1nary opportunities for intent {intent.id}.",
            f"Selected {selected.asset.upper()} {selected.strategy_type.value} on {selected.chain} "
            f"because total score {score.total_score:.2f} cleared minimum {self.config.min_score:.2f}.",
            f"Premium APR proxy is {score.premium_apr:.2%}; expected premium is "
            f"${expected_premium_usd:.2f} on ${size_usd:.2f} deployed.",
            f"Expiry is {selected.expiry_days} days; distance to strike is "
            f"{_distance_to_strike(selected):.2%}; assignment risk proxy is "
            f"{score.assignment_risk:.2%}.",
            f"Capacity supports ${selected.capacity_usd:.2f}; selected size is "
            f"${size_usd:.2f}.",
            f"Chain decision: {selected.chain} "
            f"({'existing Base path can be prepared' if selected.chain == 'base' else 'Solana remains pending_execution for v1'}).",
        ]

    def _wait(self, *trace: str, intent_id: str | None = None) -> AgentDecision:
        return AgentDecision(
            intent_id=intent_id,
            selected_chain=None,
            asset=None,
            strategy_type=None,
            quote_id=None,
            size_usdc=0,
            expected_premium_usdc=0,
            score=0.0,
            reasoning_trace=list(trace),
            status=DecisionStatus.WAIT,
        )


def _distance_to_strike(opportunity: Opportunity) -> float:
    return abs(opportunity.strike - opportunity.spot) / opportunity.spot


def _assignment_risk_proxy(opportunity: Opportunity, distance: float) -> float:
    option_type = opportunity.option_type.upper()
    is_itm = (
        option_type == "PUT" and opportunity.strike >= opportunity.spot
    ) or (
        option_type == "CALL" and opportunity.strike <= opportunity.spot
    )
    if is_itm:
        return 1.0
    expiry_pressure = min(0.25, opportunity.expiry_days / 90)
    distance_risk = max(0.0, 1.0 - distance / 0.35)
    return min(1.0, distance_risk * 0.75 + expiry_pressure)


def _triangular_score(
    value: float,
    *,
    low: float,
    target: float,
    high: float,
    weight: float,
) -> float:
    if value <= low or value >= high:
        return 0.0
    if math.isclose(value, target):
        return weight
    if value < target:
        return ((value - low) / (target - low)) * weight
    return ((high - value) / (high - target)) * weight


def _public_opportunity(opportunity: Opportunity) -> dict[str, object]:
    return {
        "quote_id": opportunity.quote_id,
        "chain": opportunity.chain,
        "asset": opportunity.asset,
        "option_type": opportunity.option_type,
        "strike": opportunity.strike,
        "spot": opportunity.spot,
        "premium": opportunity.premium,
        "expiry_days": opportunity.expiry_days,
        "available_amount": opportunity.available_amount,
    }
