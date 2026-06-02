import pytest

from emst.core.bids import Bid, BlockBid
from emst.solvers import PulpMarketClearingSolver


class FixedLowPriceSolver(PulpMarketClearingSolver):
    def _prices_from_solution(self, periods, bids, block_bids, accepted_bids, balance_constraints):
        del bids, block_bids, accepted_bids, balance_constraints
        return {period: 40.0 for period in periods}


def test_block_minimum_acceptance_ratio_is_enforced():
    solver = PulpMarketClearingSolver()

    result = solver.clear(
        market_name="DAM",
        periods=(1,),
        bids=[
            Bid("demand", "DEMAND", "DAM", 1, "buy", 60.0, 100.0),
        ],
        block_bids=[
            BlockBid(
                bid_id="block",
                participant_id="GEN_B",
                market="DAM",
                periods=(1,),
                quantities=(100.0,),
                prices=(10.0,),
                side="sell",
                acceptance_binary=False,
                minimum_acceptance_ratio=0.5,
            )
        ],
    )

    assert result["accepted_bids"]["block"] == pytest.approx(0.6)


def test_linked_block_child_requires_parent_acceptance():
    solver = PulpMarketClearingSolver()

    result = solver.clear(
        market_name="DAM",
        periods=(1, 2),
        bids=[
            Bid("demand-1", "DEMAND", "DAM", 1, "buy", 50.0, 100.0),
            Bid("demand-2", "DEMAND", "DAM", 2, "buy", 50.0, 100.0),
        ],
        block_bids=[
            BlockBid(
                bid_id="parent",
                participant_id="GEN_A",
                market="DAM",
                periods=(1,),
                quantities=(50.0,),
                prices=(200.0,),
                side="sell",
            ),
            BlockBid(
                bid_id="child",
                participant_id="GEN_A",
                market="DAM",
                periods=(2,),
                quantities=(50.0,),
                prices=(1.0,),
                side="sell",
                parent_bid_id="parent",
            ),
        ],
    )

    assert result["accepted_bids"]["parent"] == 0.0
    assert result["accepted_bids"]["child"] == 0.0
    assert result["prices"] == {1: 0.0, 2: 0.0}


def test_paradoxically_accepted_sell_block_is_rejected():
    solver = FixedLowPriceSolver()

    result = solver.clear(
        market_name="DAM",
        periods=(1,),
        bids=[
            Bid("demand", "DEMAND", "DAM", 1, "buy", 100.0, 100.0),
        ],
        block_bids=[
            BlockBid(
                bid_id="expensive-block",
                participant_id="GEN_B",
                market="DAM",
                periods=(1,),
                quantities=(100.0,),
                prices=(50.0,),
                side="sell",
            )
        ],
    )

    assert result["accepted_bids"]["expensive-block"] == 0.0
    assert result["metadata"]["rejected_paradoxical_blocks"] == ["expensive-block"]
