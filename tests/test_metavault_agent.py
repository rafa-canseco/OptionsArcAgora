import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.metavault.models import CapitalIntent, ExposureSnapshot, Opportunity
from agent.metavault.runner import run_once
from agent.metavault.scoring import AgentConfig, PatientWheelAgent


def intent(amount=10_000_000_000):
    return CapitalIntent(
        id="intent-1",
        amount_usdc=amount,
        status="waiting_to_be_deployed",
    )


def quote(
    *,
    quote_id="q1",
    asset="eth",
    chain="base",
    option_type="PUT",
    strike=2200,
    spot=2500,
    premium=80,
    expiry_days=14,
    available_amount=10,
    ttl_seconds=600,
):
    return Opportunity(
        quote_id=quote_id,
        chain=chain,
        asset=asset,
        option_type=option_type,
        strike=strike,
        spot=spot,
        premium=premium,
        expiry_days=expiry_days,
        ttl_seconds=ttl_seconds,
        available_amount=available_amount,
    )


class PatientWheelAgentTests(unittest.TestCase):
    def test_scores_explicit_components(self):
        agent = PatientWheelAgent()
        score = agent.score_opportunity(quote(), intent(), ExposureSnapshot())

        self.assertTrue(score.eligible)
        self.assertGreater(score.premium_apr, 0)
        self.assertGreater(score.premium_component, 0)
        self.assertGreater(score.expiry_component, 0)
        self.assertGreater(score.distance_component, 0)
        self.assertLess(score.assignment_risk, 1)
        self.assertGreater(score.total_score, 58)

    def test_no_op_when_no_eligible_capital(self):
        agent = PatientWheelAgent()
        decision = agent.decide([], [quote()])

        self.assertEqual(decision.status.value, "wait")
        self.assertIsNone(decision.quote_id)
        self.assertIn("No capital_movement_intents", decision.reasoning_trace[0])

    def test_no_op_when_no_eligible_quote(self):
        agent = PatientWheelAgent()
        decision = agent.decide([intent()], [quote(premium=0)])

        self.assertEqual(decision.status.value, "wait")
        self.assertIsNone(decision.quote_id)
        self.assertIn("No eligible b1nary quote", decision.reasoning_trace[0])

    def test_base_selection_prepares_execution(self):
        agent = PatientWheelAgent()
        decision = agent.decide(
            [intent()],
            [
                quote(quote_id="weak", premium=5),
                quote(quote_id="base-best", chain="base", premium=90),
            ],
        )

        self.assertEqual(decision.quote_id, "base-best")
        self.assertEqual(decision.selected_chain, "base")
        self.assertEqual(decision.status.value, "prepared_base_execution")
        self.assertEqual(decision.strategy_type, "CSP")
        self.assertGreater(decision.expected_premium_usdc, 0)

    def test_solana_selection_remains_pending_execution(self):
        agent = PatientWheelAgent(AgentConfig(solana_execution_ready=False))
        decision = agent.decide(
            [intent()],
            [
                quote(quote_id="base", chain="base", premium=1),
                quote(
                    quote_id="solana-best",
                    chain="solana",
                    asset="sol",
                    strike=140,
                    spot=160,
                    premium=12,
                    available_amount=100,
                ),
            ],
        )

        self.assertEqual(decision.quote_id, "solana-best")
        self.assertEqual(decision.selected_chain, "solana")
        self.assertEqual(decision.status.value, "pending_execution")

    def test_status_transition_execute_maps_to_backend(self):
        class FakeSource:
            def __init__(self):
                self.applied = []

            def list_waiting_intents(self):
                return [intent()]

            def list_opportunities(self):
                return [quote(quote_id="base-best")]

            def read_exposure(self):
                return ExposureSnapshot()

            def apply_decision_status(self, intent_id, status):
                self.applied.append((intent_id, status))

        fake = FakeSource()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("agent.metavault.runner.source_from_env", return_value=fake):
                result = run_once(execute=True, decisions_dir=Path(tmp))

            self.assertEqual(fake.applied, [("intent-1", "deployment_in_flight")])
            latest = Path(result["decision_path"])
            self.assertTrue(latest.exists())
            saved = json.loads(latest.read_text())
            self.assertEqual(saved["quote_id"], "base-best")
            self.assertIn("decision_hash", saved)

    def test_exposure_limit_rejects_overallocated_asset(self):
        agent = PatientWheelAgent()
        exposure = ExposureSnapshot(
            total_usd=10_000,
            by_asset={"eth": 9_000},
            by_chain={"base": 9_000},
            by_strategy={"CSP": 9_000},
        )
        decision = agent.decide([intent(amount=1_000_000_000)], [quote()], exposure)

        self.assertEqual(decision.status.value, "wait")
        self.assertTrue(
            any("exposure limit" in item for item in decision.reasoning_trace)
        )


if __name__ == "__main__":
    unittest.main()
