from emst.markets.dam import DAMMarket
from emst.markets.dam.model import generate_energy_exchange_bids
from emst.systems.cyprus import CyprusMarketConfig


def test_dam_market_returns_prices_and_positions():
    config = CyprusMarketConfig.default()
    scenario = config.load_scenario("high_solar_day")
    participants = config.load_participants()
    previous = {p.participant_id: {period: 0.0 for period in config.market_time.periods} for p in participants}

    bids, block_bids, circular_block_bids = generate_energy_exchange_bids(
        market_name="DAM",
        participants=participants,
        scenario=scenario,
        previous_positions=previous,
        allow_block_orders=True,
    )
    result = DAMMarket(config=config.dam, market_time=config.market_time).clear(
        bids=bids,
        block_bids=block_bids,
        circular_block_bids=circular_block_bids,
    )

    assert result.prices
    assert result.participant_positions
    assert len(result.prices) == 24
    assert result.metadata["price_method"] == "fixed_binary_lp_shadow_prices"
