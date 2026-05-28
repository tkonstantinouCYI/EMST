from __future__ import annotations

from mst.core.participants import Asset, Participant
from mst.core.time import MarketTime


def default_participants(market_time: MarketTime | None = None) -> list[Participant]:
    """Return a compact Cyprus participant set for v0.1 examples and tests."""
    charge_hours = (11, 12, 13, 14)
    discharge_hours = (18, 19, 20, 21)
    charge_periods = market_time.periods_for_mtu_hours(charge_hours) if market_time else charge_hours
    discharge_periods = market_time.periods_for_mtu_hours(discharge_hours) if market_time else discharge_hours
    return [
        Participant(
            participant_id="THERMAL_A",
            name="Thermal Producer A",
            participant_type="production",
            assets=[
                Asset(
                    asset_id="ccgt_a",
                    name="CCGT A",
                    asset_type="thermal",
                    capacity=220.0,
                    marginal_cost=82.0,
                    metadata={"technical_minimum": 60.0},
                )
            ],
        ),
        Participant(
            participant_id="THERMAL_B",
            name="Thermal Producer B",
            participant_type="production",
            assets=[
                Asset(
                    asset_id="steam_b",
                    name="Steam Unit B",
                    asset_type="thermal",
                    capacity=540.0,
                    marginal_cost=105.0,
                    metadata={"technical_minimum": 40.0},
                )
            ],
        ),
        Participant(
            participant_id="SOLAR_AGG",
            name="Solar Aggregator",
            participant_type="production",
            assets=[
                Asset(
                    asset_id="solar_portfolio",
                    name="Solar Portfolio",
                    asset_type="solar",
                    capacity=260.0,
                    marginal_cost=0.0,
                )
            ],
        ),
        Participant(
            participant_id="WIND_AGG",
            name="Wind Aggregator",
            participant_type="production",
            assets=[
                Asset(
                    asset_id="wind_portfolio",
                    name="Wind Portfolio",
                    asset_type="wind",
                    capacity=160.0,
                    marginal_cost=5.0,
                )
            ],
        ),
        Participant(
            participant_id="BESS_1",
            name="Battery Storage 1",
            participant_type="storage",
            assets=[
                Asset(
                    asset_id="bess_1",
                    name="BESS 1",
                    asset_type="storage",
                    capacity=45.0,
                    marginal_cost=95.0,
                    metadata={
                        "energy_capacity": 180.0,
                        "charge_hours": charge_periods,
                        "discharge_hours": discharge_periods,
                        "charge_bid_price": 38.0,
                    },
                )
            ],
        ),
        Participant(
            participant_id="DEMAND",
            name="Cyprus Demand",
            participant_type="demand",
            assets=[
                Asset(
                    asset_id="system_load",
                    name="System Load",
                    asset_type="demand",
                    capacity=900.0,
                    metadata={"demand_share": 1.0},
                )
            ],
        ),
    ]
