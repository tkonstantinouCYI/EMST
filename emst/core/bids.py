from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BidSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class Bid:
    """Simple single-period energy bid or offer."""

    bid_id: str
    participant_id: str
    market: str
    period: int
    side: BidSide
    quantity: float
    price: float


@dataclass(frozen=True)
class BlockBid:
    """Multi-period block order accepted with one binary decision."""

    bid_id: str
    participant_id: str
    market: str
    periods: tuple[int, ...]
    quantities: tuple[float, ...]
    prices: tuple[float, ...]
    acceptance_binary: bool = True
    side: BidSide = "sell"
    minimum_acceptance_ratio: float = 1.0
    parent_bid_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.periods) != len(self.quantities) or len(self.periods) != len(self.prices):
            raise ValueError("BlockBid periods, quantities, and prices must have the same length.")
        if not 0.0 <= self.minimum_acceptance_ratio <= 1.0:
            raise ValueError("minimum_acceptance_ratio must be between 0 and 1.")


@dataclass(frozen=True)
class CircularBlockBid:
    """Linked block-order family used for storage-style approximations."""

    family_id: str
    participant_id: str
    linked_blocks: list[BlockBid] = field(default_factory=list)
    net_energy_balance_rule: str = "zero_sum"
