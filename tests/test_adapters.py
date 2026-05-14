import json
import unittest
from unittest.mock import patch

from agent.metavault.adapters import BackendApiClient, StagingApiClient, source_from_env


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class AdapterTests(unittest.TestCase):
    def test_backend_api_maps_real_intent_shape(self):
        def fake_urlopen(request, timeout):
            self.assertIn("/api/capital-intents", request.full_url)
            return FakeResponse(
                [
                    {
                        "id": "real-intent",
                        "amount_usdc": "999870",
                        "status": "waiting_to_be_deployed",
                        "source_chain": "base",
                        "destination_chain": "arc",
                        "bucket_id": None,
                        "receiver": "0xReceiver",
                    }
                ]
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            intents = BackendApiClient("https://staging.example").list_waiting_intents()

        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].id, "real-intent")
        self.assertEqual(intents[0].amount_usdc, 999870)
        self.assertEqual(intents[0].status, "waiting_to_be_deployed")

    def test_backend_api_maps_real_price_shape(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if "asset=eth" in request.full_url:
                return FakeResponse(
                    [
                        {
                            "option_type": "put",
                            "strike": 2200,
                            "expiry_days": 14,
                            "premium": 80,
                            "spot": 2500,
                            "ttl": 600,
                            "available_amount": 10,
                            "otoken_address": "0xOtoken",
                            "signature": "0xsig",
                            "mm_address": "0xMm",
                            "bid_price_raw": 80000000,
                            "deadline": 1999999999,
                            "quote_id": "quote-real",
                            "max_amount_raw": 1000000000,
                            "maker_nonce": 1,
                            "chain": "base",
                        }
                    ]
                )
            return FakeResponse([])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            opps = BackendApiClient(
                "https://staging.example", assets=("eth", "btc")
            ).list_opportunities()

        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0].quote_id, "quote-real")
        self.assertEqual(opps[0].asset, "eth")
        self.assertEqual(opps[0].option_type, "put")
        self.assertEqual(opps[0].strategy_type.value, "CSP")
        self.assertEqual(len(calls), 2)

    def test_staging_api_source_uses_known_staging_url(self):
        with patch.dict("os.environ", {"ARC_AGENT_SOURCE": "staging_api"}):
            source = source_from_env()

        self.assertIsInstance(source, StagingApiClient)
        self.assertEqual(
            source.base_url, "https://optionsprotocolbackend-staging.up.railway.app"
        )

    def test_price_asset_failure_does_not_abort_other_assets(self):
        def fake_urlopen(request, timeout):
            if "asset=eth" in request.full_url:
                raise RuntimeError("spot unavailable")
            return FakeResponse(
                [
                    {
                        "option_type": "PUT",
                        "strike": 140,
                        "expiry_days": 14,
                        "premium": 12,
                        "spot": 160,
                        "ttl": 600,
                        "available_amount": 100,
                        "quote_id": "sol-quote",
                        "chain": "solana",
                    }
                ]
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            opps = BackendApiClient(
                "https://staging.example", assets=("eth", "sol")
            ).list_opportunities()

        self.assertEqual([opp.quote_id for opp in opps], ["sol-quote"])


if __name__ == "__main__":
    unittest.main()
