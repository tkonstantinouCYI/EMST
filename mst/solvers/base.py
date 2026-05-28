from __future__ import annotations

from abc import ABC, abstractmethod

from mst.core.bids import Bid, BlockBid, CircularBlockBid


class MarketClearingSolver(ABC):
    """Abstract optimization backend for welfare-based market clearing."""

    @abstractmethod
    def clear(
        self,
        *,
        market_name: str,
        periods: tuple[int, ...],
        bids: list[Bid],
        block_bids: list[BlockBid] | None = None,
        circular_block_bids: list[CircularBlockBid] | None = None,
    ) -> dict:
        """Clear a market and return primitive result data."""
