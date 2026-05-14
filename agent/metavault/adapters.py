"""IO adapters for the standalone Patient Wheel agent."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent.metavault.models import CapitalIntent, ExposureSnapshot, Opportunity

STAGING_API_URL = "https://optionsprotocolbackend-staging.up.railway.app"


class AgentSource(Protocol):
    def list_waiting_intents(self) -> list[CapitalIntent]: ...
    def list_opportunities(self) -> list[Opportunity]: ...
    def read_exposure(self) -> ExposureSnapshot: ...


class AgentExecutor(Protocol):
    def apply_decision_status(self, intent_id: str, status: str) -> None: ...


@dataclass(frozen=True)
class BackendApiClient:
    base_url: str
    assets: tuple[str, ...] = ("eth", "btc", "sol", "tslax")
    timeout_seconds: float = 10.0

    def list_waiting_intents(self) -> list[CapitalIntent]:
        rows = self._get_json(
            "/api/capital-intents",
            {"status": "waiting_to_be_deployed", "limit": "100"},
        )
        return [CapitalIntent.from_backend_row(row) for row in rows]

    def list_opportunities(self) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        for asset in self.assets:
            try:
                rows = self._get_json("/prices", {"asset": asset})
            except RuntimeError:
                continue
            opportunities.extend(
                Opportunity.from_backend_price(asset, row)
                for row in rows
                if row.get("quote_id")
            )
        return opportunities

    def read_exposure(self) -> ExposureSnapshot:
        return ExposureSnapshot()

    def apply_decision_status(self, intent_id: str, status: str) -> None:
        self._patch_json(f"/api/capital-intents/{intent_id}", {"status": status})

    def _get_json(self, path: str, query: dict[str, str]) -> Any:
        url = self._url(path, query)
        request = urllib.request.Request(url, method="GET")
        return self._send(request)

    def _patch_json(self, path: str, body: dict[str, Any]) -> Any:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._url(path, {}),
            data=data,
            method="PATCH",
            headers={"content-type": "application/json"},
        )
        return self._send(request)

    def _url(self, path: str, query: dict[str, str]) -> str:
        base = self.base_url.rstrip("/")
        suffix = path if path.startswith("/") else f"/{path}"
        encoded = urllib.parse.urlencode(query)
        return f"{base}{suffix}" + (f"?{encoded}" if encoded else "")

    def _send(self, request: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"backend API {exc.code}: {detail}") from exc


@dataclass(frozen=True)
class StagingApiClient(BackendApiClient):
    base_url: str = STAGING_API_URL


@dataclass(frozen=True)
class SupabaseRestClient:
    url: str
    service_role_key: str
    backend_url: str | None = None
    timeout_seconds: float = 10.0

    def list_waiting_intents(self) -> list[CapitalIntent]:
        rows = self._get_table(
            "capital_movement_intents",
            {
                "select": "*",
                "status": "eq.waiting_to_be_deployed",
                "order": "created_at.asc",
            },
        )
        return [CapitalIntent.from_backend_row(row) for row in rows]

    def list_opportunities(self) -> list[Opportunity]:
        rows = self._get_table(
            "mm_quotes",
            {
                "select": "*",
                "is_active": "eq.true",
                "deadline": f"gt.{int(__import__('time').time()) + 60}",
            },
        )
        opportunities = []
        for row in rows:
            asset = str(row.get("asset", "eth")).lower()
            spot = _optional_float(row.get("spot") or row.get("underlying_price"))
            if not spot and self.backend_url:
                spot = self._read_spot_from_backend(asset)
            if not spot:
                continue
            opportunities.append(
                Opportunity(
                    quote_id=str(row.get("quote_id") or row.get("id")),
                    chain=str(row.get("chain", "base")),
                    asset=asset,
                    option_type="PUT" if row.get("is_put", True) else "CALL",
                    strike=float(row.get("strike_price") or 0) / 1e8,
                    spot=spot,
                    premium=float(row.get("bid_price") or 0) / 1_000_000,
                    expiry_days=max(
                        0,
                        round(
                            (int(row.get("expiry") or 0) - int(__import__('time').time()))
                            / 86400
                        ),
                    ),
                    ttl_seconds=max(
                        0,
                        int(row.get("deadline") or 0) - int(__import__('time').time()),
                    ),
                    available_amount=float(row.get("max_amount") or 0) / 1e8,
                    mm_address=row.get("mm_address"),
                    otoken_address=row.get("otoken_address"),
                    raw=row,
                )
            )
        return opportunities

    def _read_spot_from_backend(self, asset: str) -> float | None:
        query = urllib.parse.urlencode({"asset": asset})
        request = urllib.request.Request(
            f"{self.backend_url.rstrip('/')}/spot?{query}",
            method="GET",
        )
        try:
            data = self._send(request)
        except RuntimeError:
            return None
        return _optional_float(data.get("spot"))

    def read_exposure(self) -> ExposureSnapshot:
        rows = self._get_table(
            "agent_deployment_decisions",
            {"select": "asset,selected_chain,strategy_type,size,status"},
        )
        exposure = ExposureSnapshot()
        for row in rows:
            if row.get("status") not in {"selected", "prepared_base_execution", "pending_execution", "execution_requested"}:
                continue
            size = float(row.get("size") or row.get("size_usdc") or 0) / 1_000_000
            exposure = exposure.after(
                asset=str(row.get("asset")),
                chain=str(row.get("selected_chain")),
                strategy=str(row.get("strategy_type")),
                size_usd=size,
            )
        return exposure

    def apply_decision_status(self, intent_id: str, status: str) -> None:
        self._patch_table("capital_movement_intents", {"status": status}, {"id": f"eq.{intent_id}"})

    def _get_table(self, table: str, query: dict[str, str]) -> list[dict[str, Any]]:
        encoded = urllib.parse.urlencode(query)
        request = urllib.request.Request(
            f"{self.url.rstrip('/')}/rest/v1/{table}?{encoded}",
            headers=self._headers(),
            method="GET",
        )
        return self._send(request)

    def _patch_table(
        self,
        table: str,
        body: dict[str, Any],
        query: dict[str, str],
    ) -> Any:
        encoded = urllib.parse.urlencode(query)
        request = urllib.request.Request(
            f"{self.url.rstrip('/')}/rest/v1/{table}?{encoded}",
            data=json.dumps(body).encode("utf-8"),
            headers={**self._headers(), "content-type": "application/json", "prefer": "return=representation"},
            method="PATCH",
        )
        return self._send(request)

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "authorization": f"Bearer {self.service_role_key}",
        }

    def _send(self, request: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"supabase REST {exc.code}: {detail}") from exc


@dataclass(frozen=True)
class FixtureSource:
    path: Path

    def _data(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def list_waiting_intents(self) -> list[CapitalIntent]:
        return [
            CapitalIntent.from_backend_row(row)
            for row in self._data().get("capital_movement_intents", [])
        ]

    def list_opportunities(self) -> list[Opportunity]:
        return [
            Opportunity.from_backend_price(str(row.get("asset", "eth")), row)
            for row in self._data().get("opportunities", [])
        ]

    def read_exposure(self) -> ExposureSnapshot:
        exposure = ExposureSnapshot()
        for row in self._data().get("exposure", []):
            exposure = exposure.after(
                asset=str(row["asset"]),
                chain=str(row["chain"]),
                strategy=str(row["strategy_type"]),
                size_usd=float(row["size_usd"]),
            )
        return exposure

    def apply_decision_status(self, intent_id: str, status: str) -> None:
        raise RuntimeError("fixture source is read-only")


def source_from_env() -> AgentSource:
    source = os.getenv("ARC_AGENT_SOURCE", "backend").lower()
    if source == "fixture":
        return FixtureSource(
            Path(os.getenv("ARC_AGENT_FIXTURE", "config/demo_fixture.json"))
        )
    if source == "staging_api":
        assets = _assets_from_env()
        return StagingApiClient(assets=assets)
    if source == "supabase":
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        return SupabaseRestClient(
            url=url,
            service_role_key=key,
            backend_url=os.getenv("BACKEND_API_URL") or STAGING_API_URL,
        )
    backend_url = os.environ["BACKEND_API_URL"]
    return BackendApiClient(base_url=backend_url, assets=_assets_from_env())


def _assets_from_env() -> tuple[str, ...]:
    return tuple(
        item.strip().lower()
        for item in os.getenv("ARC_AGENT_ASSETS", "eth,btc,sol,tslax").split(",")
        if item.strip()
    )


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
