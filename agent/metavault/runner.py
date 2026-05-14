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


BACKEND_STATUS_BY_DECISION = {
    DecisionStatus.PREPARED_BASE_EXECUTION: "deployment_in_flight",
    DecisionStatus.EXECUTION_REQUESTED: "deployment_in_flight",
    DecisionStatus.PENDING_EXECUTION: "deployment_in_flight",
}


def run_once(*, execute: bool = False, decisions_dir: Path | None = None) -> dict:
    source = source_from_env()
    config = AgentConfig(
        min_score=float(os.getenv("ARC_AGENT_MIN_SCORE", "58")),
        base_execution_ready=os.getenv("ARC_AGENT_BASE_EXECUTION_READY", "true").lower()
        == "true",
        solana_execution_ready=os.getenv(
            "ARC_AGENT_SOLANA_EXECUTION_READY", "false"
        ).lower()
        == "true",
    )
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
