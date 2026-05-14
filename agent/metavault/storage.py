"""Local decision record storage for demo and audit traces."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent.metavault.models import AgentDecision


@dataclass(frozen=True)
class LocalDecisionStore:
    root: Path

    def write(self, decision: AgentDecision, decision_hash: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        record = {**decision.to_dict(), "decision_hash": decision_hash}
        latest = self.root / "latest.json"
        latest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jsonl = self.root / f"{day}.jsonl"
        with jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return latest
