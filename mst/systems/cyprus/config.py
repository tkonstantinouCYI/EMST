from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from mst.core.config import MarketStageConfig
from mst.core.contracts import ForwardContract, contracts_to_schedules
from mst.core.participants import Participant
from mst.core.scenarios import Scenario
from mst.core.time import MarketTime
from mst.systems.cyprus.participants import default_participants
from mst.systems.cyprus.rules import (
    DEFAULT_DEMAND_BID_PRICE,
    DEFAULT_PRICE_CAP,
    DEFAULT_PRICE_FLOOR,
    MARKET_SEQUENCE,
)
from mst.systems.cyprus.scenarios import predefined_scenarios


@dataclass(frozen=True)
class CyprusMarketConfig:
    """First predefined system configuration for Cyprus."""

    market_time: MarketTime
    market_sequence: tuple[str, ...] = MARKET_SEQUENCE
    markets: dict[str, MarketStageConfig] = field(default_factory=dict)

    @classmethod
    def default(cls, *, mtu_minutes: int = 60) -> "CyprusMarketConfig":
        markets = {
            name: MarketStageConfig(
                name=name,
                price_floor=DEFAULT_PRICE_FLOOR,
                price_cap=DEFAULT_PRICE_CAP,
                demand_bid_price=DEFAULT_DEMAND_BID_PRICE,
                allow_blocks=name == "DAM",
            )
            for name in MARKET_SEQUENCE
            if name != "FM"
        }
        return cls(
            market_time=MarketTime.single_day(date(2026, 6, 1), mtu_minutes=mtu_minutes),
            markets=markets,
        )

    @property
    def dam(self) -> MarketStageConfig:
        return self.markets["DAM"]

    def load_participants(
        self,
        *,
        additional_participants: list[Participant] | None = None,
        include_defaults: bool = True,
    ) -> list[Participant]:
        """Load Cyprus participants, optionally extended with user-defined participants."""
        participants = default_participants(self.market_time) if include_defaults else []
        participants = [*participants, *(additional_participants or [])]
        self._validate_participants(participants)
        return participants

    def load_scenario(self, scenario_id: str) -> Scenario:
        scenarios = predefined_scenarios(self.market_time)
        if scenario_id not in scenarios:
            raise KeyError(f"Unknown Cyprus scenario '{scenario_id}'. Available: {sorted(scenarios)}")
        return scenarios[scenario_id]

    def load_forward_contracts(
        self,
        *,
        additional_contracts: list[ForwardContract] | None = None,
        include_defaults: bool = True,
    ) -> list[ForwardContract]:
        """Load bilateral forward contracts for the Cyprus example system."""
        contracts = self.default_forward_contracts() if include_defaults else []
        contracts = [*contracts, *(additional_contracts or [])]
        self._validate_forward_contracts(contracts)
        return contracts

    def default_forward_contracts(self) -> list[ForwardContract]:
        """Return default bilateral contracts used by the Cyprus v0.1 scenario."""
        return [
            ForwardContract.fixed_quantity(
                contract_id="FM-THERMAL-A-DEMAND-BASE",
                delivery_participant_id="THERMAL_A",
                offtake_participant_id="DEMAND",
                quantity=75.0,
                periods=self.market_time.periods,
                price=95.0,
            )
        ]

    def forward_schedules(self, scenario: Scenario) -> dict[str, dict[int, float]]:
        """Return exogenous bilateral forward positions for the scenario."""
        del scenario
        return contracts_to_schedules(self.default_forward_contracts())

    def _validate_participants(self, participants: list[Participant]) -> None:
        participant_ids = [participant.participant_id for participant in participants]
        duplicates = sorted({pid for pid in participant_ids if participant_ids.count(pid) > 1})
        if duplicates:
            raise ValueError(f"Duplicate participant IDs are not allowed: {duplicates}")

        for participant in participants:
            if not participant.assets:
                raise ValueError(f"Participant '{participant.participant_id}' must have at least one asset.")
            asset_ids = [asset.asset_id for asset in participant.assets]
            duplicate_assets = sorted({aid for aid in asset_ids if asset_ids.count(aid) > 1})
            if duplicate_assets:
                raise ValueError(
                    f"Duplicate asset IDs for participant '{participant.participant_id}': {duplicate_assets}"
                )

    def _validate_forward_contracts(self, contracts: list[ForwardContract]) -> None:
        contract_ids = [contract.contract_id for contract in contracts]
        duplicates = sorted({cid for cid in contract_ids if contract_ids.count(cid) > 1})
        if duplicates:
            raise ValueError(f"Duplicate forward contract IDs are not allowed: {duplicates}")
        for contract in contracts:
            if contract.delivery_participant_id == contract.offtake_participant_id:
                raise ValueError(
                    f"Forward contract '{contract.contract_id}' has the same delivery and offtake participant."
                )
            unknown_periods = sorted(set(contract.period_quantities) - set(self.market_time.periods))
            if unknown_periods:
                raise ValueError(
                    f"Forward contract '{contract.contract_id}' uses periods outside the market time: {unknown_periods}"
                )
