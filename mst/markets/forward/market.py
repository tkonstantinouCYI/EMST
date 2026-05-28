from __future__ import annotations

from collections import defaultdict

from mst.core.contracts import ForwardContract, contracts_to_schedules
from mst.core.participants import Participant
from mst.core.results import MarketResult
from mst.core.time import MarketTime


class ForwardMarket:
    """Loads exogenous forward schedules and converts them to positions."""

    def __init__(self, market_time: MarketTime) -> None:
        self.market_time = market_time

    def clear(
        self,
        *,
        schedules: dict[str, dict[int, float]] | None = None,
        contracts: list[ForwardContract] | None = None,
        participants: list[Participant],
    ) -> MarketResult:
        schedules = schedules or contracts_to_schedules(contracts or [])
        positions = {
            participant.participant_id: {period: 0.0 for period in self.market_time.periods}
            for participant in participants
        }
        for participant_id, period_values in schedules.items():
            positions.setdefault(participant_id, {period: 0.0 for period in self.market_time.periods})
            for period, quantity in period_values.items():
                positions[participant_id][period] = float(quantity)

        accepted = defaultdict(float)
        for participant_id, period_values in schedules.items():
            for period, quantity in period_values.items():
                accepted[f"FM-{participant_id}-{period}"] += float(quantity)

        return MarketResult(
            market_name="FM",
            prices={period: 0.0 for period in self.market_time.periods},
            accepted_bids=dict(accepted),
            participant_positions=positions,
            social_welfare=0.0,
            metadata={
                "description": "Exogenous bilateral forward schedules",
                "contracts": [contract.contract_id for contract in contracts or []],
            },
        )
