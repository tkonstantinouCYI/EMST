from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ForwardContract:
    """Bilateral forward contract between delivery and offtake participants."""

    contract_id: str
    delivery_participant_id: str
    offtake_participant_id: str
    period_quantities: dict[int, float]
    price: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_period_quantities(
        cls,
        *,
        contract_id: str,
        delivery_participant_id: str,
        offtake_participant_id: str,
        period_quantities: dict[int, float],
        price: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ForwardContract":
        """Create a contract with explicitly defined MWh quantities per period."""
        return cls(
            contract_id=contract_id,
            delivery_participant_id=delivery_participant_id,
            offtake_participant_id=offtake_participant_id,
            period_quantities={int(period): float(quantity) for period, quantity in period_quantities.items()},
            price=price,
            metadata=metadata or {},
        )

    @classmethod
    def fixed_quantity(
        cls,
        *,
        contract_id: str,
        delivery_participant_id: str,
        offtake_participant_id: str,
        quantity: float,
        periods: tuple[int, ...],
        price: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ForwardContract":
        """Create a contract with the same MWh quantity in every period."""
        return cls(
            contract_id=contract_id,
            delivery_participant_id=delivery_participant_id,
            offtake_participant_id=offtake_participant_id,
            period_quantities={period: float(quantity) for period in periods},
            price=price,
            metadata=metadata or {},
        )


def contracts_to_schedules(contracts: list[ForwardContract]) -> dict[str, dict[int, float]]:
    """Convert bilateral contracts into participant net forward schedules."""
    schedules: dict[str, dict[int, float]] = {}
    for contract in contracts:
        for period, quantity in contract.period_quantities.items():
            schedules.setdefault(contract.delivery_participant_id, {})
            schedules.setdefault(contract.offtake_participant_id, {})
            schedules[contract.delivery_participant_id][period] = (
                schedules[contract.delivery_participant_id].get(period, 0.0) + quantity
            )
            schedules[contract.offtake_participant_id][period] = (
                schedules[contract.offtake_participant_id].get(period, 0.0) - quantity
            )
    return schedules
