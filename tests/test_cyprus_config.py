from mst.systems.cyprus import CyprusMarketConfig
from mst.core.participants import Asset, Participant


def test_load_cyprus_config_participants_and_scenario():
    config = CyprusMarketConfig.default()
    participants = config.load_participants()
    scenario = config.load_scenario("high_solar_day")

    assert config.market_sequence == ("FM", "DAM", "IDA1", "IDA2", "IDA3")
    assert len(participants) >= 5
    assert len(scenario.demand_profile) == 24


def test_can_add_user_defined_participant():
    config = CyprusMarketConfig.default()
    participant = Participant(
        participant_id="USER_SOLAR",
        name="User Solar",
        participant_type="production",
        assets=[
            Asset(
                asset_id="user_solar",
                name="User Solar Asset",
                asset_type="solar",
                capacity=50.0,
                marginal_cost=0.0,
            )
        ],
    )

    participants = config.load_participants(additional_participants=[participant])

    assert any(p.participant_id == "USER_SOLAR" for p in participants)


def test_cyprus_config_supports_30_minute_mtus():
    config = CyprusMarketConfig.default(mtu_minutes=30)
    scenario = config.load_scenario("high_solar_day")
    participants = config.load_participants()

    assert len(config.market_time.periods) == 48
    assert config.market_time.mtu_minutes == 30
    assert len(scenario.demand_profile) == 48
    assert scenario.demand_profile[1] == 215.0
    assert scenario.demand_profile[2] == 215.0
    bess = next(p for p in participants if p.participant_id == "BESS_1")
    assert len(bess.assets[0].metadata["charge_hours"]) == 8


def test_cyprus_config_supports_15_minute_mtus():
    config = CyprusMarketConfig.default(mtu_minutes=15)
    scenario = config.load_scenario("high_solar_day")

    assert len(config.market_time.periods) == 96
    assert len(scenario.demand_profile) == 96
    assert scenario.demand_profile[1] == 107.5
