from __future__ import annotations

from dataclasses import dataclass

from mst.core.bids import Bid, BlockBid, CircularBlockBid


@dataclass(frozen=True)
class MarketData:
    """Input bundle for a market-clearing run."""

    bids: list[Bid]
    block_bids: list[BlockBid] | None = None
    circular_block_bids: list[CircularBlockBid] | None = None
