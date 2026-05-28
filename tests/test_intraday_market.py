from mst.core.simulation import SequentialMarketSimulation
from mst.markets.dam.model import generate_energy_exchange_bids
from mst.systems.cyprus import CyprusMarketConfig


def test_sequential_simulation_runs_all_idas_and_final_positions():
    config = CyprusMarketConfig.default()
    scenario = config.load_scenario("high_solar_day")
    participants = config.load_participants()

    results = SequentialMarketSimulation(
        config=config,
        participants=participants,
        scenario=scenario,
    ).run()

    assert set(results.ida_results) == {"IDA1", "IDA2", "IDA3"}
    assert results.price_summary["DAM"]
    assert results.price_summary["IDA3"]
    assert results.final_positions


def test_ida_bid_generation_has_no_block_orders():
    config = CyprusMarketConfig.default()
    scenario = config.load_scenario("high_solar_day")
    participants = config.load_participants()
    previous = {
        participant.participant_id: {period: 0.0 for period in config.market_time.periods}
        for participant in participants
    }

    bids, block_bids, circular_block_bids = generate_energy_exchange_bids(
        market_name="IDA1",
        participants=participants,
        scenario=scenario,
        previous_positions=previous,
        allow_block_orders=False,
        market_time=config.market_time,
    )

    assert bids
    assert block_bids == []
    assert circular_block_bids == []
