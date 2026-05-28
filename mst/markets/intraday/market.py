from __future__ import annotations

from mst.core.bids import Bid, BlockBid, CircularBlockBid
from mst.core.config import MarketStageConfig
from mst.core.results import MarketResult
from mst.core.time import MarketTime
from mst.markets.dam.market import DAMMarket
from mst.solvers import MarketClearingSolver


class IntradayAuctionMarket:
    """Sequential intraday auction using the same clearing engine as DAM."""

    def __init__(
        self,
        name: str,
        config: MarketStageConfig | None = None,
        market_time: MarketTime | None = None,
        solver: MarketClearingSolver | None = None,
    ) -> None:
        self.name = name
        self.market = DAMMarket(
            config=config or MarketStageConfig(name=name),
            market_time=market_time,
            solver=solver,
        )

    def clear(
        self,
        *,
        bids: list[Bid],
        block_bids: list[BlockBid] | None = None,
        circular_block_bids: list[CircularBlockBid] | None = None,
    ) -> MarketResult:
        result = self.market.clear(
            bids=bids,
            block_bids=block_bids,
            circular_block_bids=circular_block_bids,
        )
        result.market_name = self.name
        result.metadata["auction_type"] = "intraday"
        return result
