from mst.markets.forward import ForwardMarket
from mst.core.contracts import ForwardContract
from mst.systems.cyprus import CyprusMarketConfig


def test_forward_market_loads_exogenous_positions():
    config = CyprusMarketConfig.default()
    scenario = config.load_scenario("high_solar_day")
    participants = config.load_participants()

    result = ForwardMarket(config.market_time).clear(
        schedules=config.forward_schedules(scenario),
        participants=participants,
    )

    assert result.market_name == "FM"
    assert result.participant_positions["THERMAL_A"][1] == 75.0
    assert result.participant_positions["DEMAND"][1] == -75.0


def test_forward_market_accepts_user_defined_contracts():
    config = CyprusMarketConfig.default()
    participants = config.load_participants()
    contract = ForwardContract.fixed_quantity(
        contract_id="USER-FM-1",
        delivery_participant_id="THERMAL_B",
        offtake_participant_id="DEMAND",
        quantity=10.0,
        periods=(1, 2),
        price=88.0,
    )

    result = ForwardMarket(config.market_time).clear(
        contracts=[contract],
        participants=participants,
    )

    assert result.participant_positions["THERMAL_B"][1] == 10.0
    assert result.participant_positions["DEMAND"][1] == -10.0


def test_forward_market_accepts_period_specific_contract_quantities():
    config = CyprusMarketConfig.default()
    participants = config.load_participants()
    contract = ForwardContract.from_period_quantities(
        contract_id="USER-FM-PROFILE",
        delivery_participant_id="THERMAL_B",
        offtake_participant_id="DEMAND",
        period_quantities={1: 5.0, 2: 15.0, 18: 40.0},
        price=90.0,
    )

    result = ForwardMarket(config.market_time).clear(
        contracts=[contract],
        participants=participants,
    )

    assert result.participant_positions["THERMAL_B"][1] == 5.0
    assert result.participant_positions["THERMAL_B"][2] == 15.0
    assert result.participant_positions["THERMAL_B"][18] == 40.0
    assert result.participant_positions["DEMAND"][18] == -40.0
