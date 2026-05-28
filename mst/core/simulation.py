from __future__ import annotations

from copy import deepcopy

from mst.core.participants import Participant
from mst.core.results import MarketResult, Positions, SequentialSimulationResult
from mst.core.scenarios import Scenario
from mst.markets.dam import DAMMarket
from mst.markets.dam.model import generate_energy_exchange_bids
from mst.markets.forward import ForwardMarket
from mst.markets.intraday import IntradayAuctionMarket


class SequentialMarketSimulation:
    """Run FM, DAM, IDA1, IDA2, and IDA3 for one delivery day."""

    def __init__(self, *, config, participants: list[Participant], scenario: Scenario) -> None:
        self.config = config
        self.participants = participants
        self.scenario = scenario
        self.market_time = config.market_time

    def run(self) -> SequentialSimulationResult:
        fm = ForwardMarket(self.market_time)
        fm_result = fm.clear(
            contracts=self.config.load_forward_contracts(),
            participants=self.participants,
        )

        cumulative = deepcopy(fm_result.participant_positions)
        dam_result = self._clear_energy_market("DAM", cumulative)
        cumulative = self._add_positions(cumulative, dam_result.participant_positions)

        ida_results: dict[str, MarketResult] = {}
        for market_name in ("IDA1", "IDA2", "IDA3"):
            ida_results[market_name] = self._clear_energy_market(market_name, cumulative)
            cumulative = self._add_positions(cumulative, ida_results[market_name].participant_positions)

        return SequentialSimulationResult(
            fm_result=fm_result,
            dam_result=dam_result,
            ida_results=ida_results,
            final_positions=cumulative,
            price_summary={
                "FM": fm_result.prices,
                "DAM": dam_result.prices,
                **{name: result.prices for name, result in ida_results.items()},
            },
        )

    def _clear_energy_market(self, market_name: str, previous_positions: Positions) -> MarketResult:
        bids, block_bids, circular_block_bids = generate_energy_exchange_bids(
            market_name=market_name,
            participants=self.participants,
            scenario=self.scenario,
            previous_positions=previous_positions,
            demand_bid_price=self.config.markets[market_name].demand_bid_price,
            allow_block_orders=self.config.markets[market_name].allow_blocks,
            market_time=self.market_time,
        )

        if market_name == "DAM":
            market = DAMMarket(config=self.config.markets[market_name], market_time=self.market_time)
        else:
            market = IntradayAuctionMarket(
                name=market_name,
                config=self.config.markets[market_name],
                market_time=self.market_time,
            )
        return market.clear(
            bids=bids,
            block_bids=block_bids,
            circular_block_bids=circular_block_bids,
        )

    def _add_positions(self, left: Positions, right: Positions) -> Positions:
        merged = deepcopy(left)
        for participant_id, period_values in right.items():
            merged.setdefault(participant_id, {period: 0.0 for period in self.market_time.periods})
            for period, quantity in period_values.items():
                merged[participant_id][period] = merged[participant_id].get(period, 0.0) + quantity
        return merged
