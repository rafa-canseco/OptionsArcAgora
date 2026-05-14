"""Command runner for the standalone Patient Wheel agent."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agent.metavault.adapters import AgentExecutor, source_from_env
from agent.metavault.models import DecisionStatus
from agent.metavault.scoring import AgentConfig, PatientWheelAgent
from agent.metavault.storage import LocalDecisionStore

POLICY_PROFILES = {
    "demo": {
        "max_expiry_days": 3,
        "min_premium_apr": 0.08,
        "min_distance_to_strike": 0.02,
        "max_assignment_risk": 0.75,
        "max_size_pct_of_available_capital": 0.50,
    },
    "production": {
        "max_expiry_days": 7,
        "min_premium_apr": 0.10,
        "min_distance_to_strike": 0.04,
        "max_assignment_risk": 0.65,
        "max_size_pct_of_available_capital": 0.35,
    },
}


BACKEND_STATUS_BY_DECISION = {
    DecisionStatus.PREPARED_BASE_EXECUTION: "deployment_in_flight",
    DecisionStatus.EXECUTION_REQUESTED: "deployment_in_flight",
    DecisionStatus.PENDING_EXECUTION: "deployment_in_flight",
}


def run_once(*, execute: bool = False, decisions_dir: Path | None = None) -> dict:
    source = source_from_env()
    config = config_from_env()
    agent = PatientWheelAgent(config)
    decision = agent.decide(
        intents=source.list_waiting_intents(),
        opportunities=source.list_opportunities(),
        exposure=source.read_exposure(),
    )
    decision_hash = agent.decision_hash(decision)
    store = LocalDecisionStore(
        decisions_dir or Path(os.getenv("ARC_AGENT_DECISIONS_DIR", "decisions"))
    )
    output_path = store.write(decision, decision_hash)

    if execute and decision.intent_id and decision.status in BACKEND_STATUS_BY_DECISION:
        executor = source
        _apply_status(executor, decision.intent_id, BACKEND_STATUS_BY_DECISION[decision.status])

    return {
        "decision_hash": decision_hash,
        "decision_path": str(output_path),
        "decision": decision.to_dict(),
    }


def _apply_status(executor: AgentExecutor, intent_id: str, status: str) -> None:
    executor.apply_decision_status(intent_id, status)


def config_from_env() -> AgentConfig:
    profile = os.getenv("ARC_AGENT_POLICY_PROFILE", "demo").lower()
    if profile not in POLICY_PROFILES:
        raise ValueError(f"Unknown ARC_AGENT_POLICY_PROFILE={profile}")
    values = dict(POLICY_PROFILES[profile])
    values.update(
        {
            "policy_profile": profile,
            "min_score": float(os.getenv("ARC_AGENT_MIN_SCORE", "58")),
            "min_quote_ttl_seconds": int(
                os.getenv("ARC_AGENT_MIN_QUOTE_TTL_SECONDS", "60")
            ),
            "allowed_chains": _csv_tuple(
                os.getenv("ARC_AGENT_ALLOWED_CHAINS", "base,solana")
            ),
            "allowed_strategies": _csv_tuple(
                os.getenv("ARC_AGENT_ALLOWED_STRATEGIES", "CSP")
            ),
            "base_execution_ready": os.getenv(
                "ARC_AGENT_BASE_EXECUTION_READY", "true"
            ).lower()
            == "true",
            "solana_execution_ready": os.getenv(
                "ARC_AGENT_SOLANA_EXECUTION_READY", "false"
            ).lower()
            == "true",
        }
    )
    overrides = {
        "max_expiry_days": ("ARC_AGENT_MAX_EXPIRY_DAYS", int),
        "min_premium_apr": ("ARC_AGENT_MIN_PREMIUM_APR", float),
        "min_distance_to_strike": ("ARC_AGENT_MIN_DISTANCE_TO_STRIKE", float),
        "max_assignment_risk": ("ARC_AGENT_MAX_ASSIGNMENT_RISK", float),
        "max_size_pct_of_available_capital": (
            "ARC_AGENT_MAX_SIZE_PCT_OF_AVAILABLE_CAPITAL",
            float,
        ),
    }
    for key, (env_name, caster) in overrides.items():
        if env_name in os.environ:
            values[key] = caster(os.environ[env_name])
    return AgentConfig(**values)


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Patient Wheel deployment agent")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Patch capital_movement_intent status after selecting a deployment.",
    )
    parser.add_argument(
        "--decisions-dir",
        default=None,
        help="Directory for local JSON decision records.",
    )
    args = parser.parse_args()
    result = run_once(
        execute=args.execute,
        decisions_dir=Path(args.decisions_dir) if args.decisions_dir else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
