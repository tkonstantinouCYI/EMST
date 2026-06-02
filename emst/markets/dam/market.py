from __future__ import annotations

from datetime import date

from emst.core.bids import Bid, BlockBid, CircularBlockBid
from emst.core.config import MarketStageConfig
from emst.core.results import MarketResult
from emst.core.time import MarketTime
from emst.solvers import MarketClearingSolver, PulpMarketClearingSolver


class DAMMarket:
    """Day-ahead social-welfare market."""

    def __init__(
        self,
        config: MarketStageConfig | None = None,
        market_time: MarketTime | None = None,
        solver: MarketClearingSolver | None = None,
    ) -> None:
        self.config = config or MarketStageConfig(name="DAM")
        self.market_time = market_time or MarketTime.single_day_hourly(date.today())
        self.solver = solver or PulpMarketClearingSolver()

    def clear(
        self,
        *,
        bids: list[Bid],
        block_bids: list[BlockBid] | None = None,
        circular_block_bids: list[CircularBlockBid] | None = None,
    ) -> MarketResult:
        data = self.solver.clear(
            market_name=self.config.name,
            periods=self.market_time.periods,
            bids=bids,
            block_bids=block_bids,
            circular_block_bids=circular_block_bids,
        )
        return MarketResult(
            market_name=self.config.name,
            prices=data["prices"],
            accepted_bids=data["accepted_bids"],
            participant_positions=data["participant_positions"],
            social_welfare=data["social_welfare"],
            metadata={"clearing_engine": "social_welfare", **data.get("metadata", {})},
        )
