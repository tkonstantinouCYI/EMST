# Quickstart

Install EMST in editable mode:

```bash
pip install -e ".[dev]"
```

Run a single-day Cyprus FM-DAM-IDA simulation:

```python
from mst.systems.cyprus import CyprusMarketConfig
from mst.core.simulation import SequentialMarketSimulation

config = CyprusMarketConfig.default()
scenario = config.load_scenario("high_solar_day")
participants = config.load_participants()

simulation = SequentialMarketSimulation(
    config=config,
    participants=participants,
    scenario=scenario,
)

results = simulation.run()

print("Prices")
for market, prices in results.price_summary.items():
    print(market, prices)

print("Final positions")
for participant_id, positions in results.final_positions.items():
    print(participant_id, positions)
```

## Add A Participant

Participants are regular Python dataclasses:

```python
from mst.core.participants import Asset, Participant

new_solar = Participant(
    participant_id="USER_SOLAR",
    name="User Solar Portfolio",
    participant_type="production",
    assets=[
        Asset(
            asset_id="user_solar_asset",
            name="User Solar Asset",
            asset_type="solar",
            capacity=50.0,
            marginal_cost=0.0,
        )
    ],
)

participants = config.load_participants(additional_participants=[new_solar])
```

## Configure MTU Length

```python
config = CyprusMarketConfig.default(mtu_minutes=30)
```

Supported MTU lengths are any positive number of minutes that divides a 24-hour day exactly. Common examples:

```python
CyprusMarketConfig.default(mtu_minutes=60)  # 24 periods
CyprusMarketConfig.default(mtu_minutes=30)  # 48 periods
CyprusMarketConfig.default(mtu_minutes=15)  # 96 periods
```

Cyprus default profiles are expanded from hourly source values into MTU-energy quantities.

## Define Bilateral Forward Contracts

Forward contracts are declared between a delivery participant and an offtake participant. Positive quantities add to the delivery participant schedule and subtract from the offtake participant schedule.

```python
from mst.core.contracts import ForwardContract

contract = ForwardContract.fixed_quantity(
    contract_id="FM-USER-SOLAR-DEMAND",
    delivery_participant_id="USER_SOLAR",
    offtake_participant_id="DEMAND",
    quantity=15.0,
    periods=config.market_time.periods,
    price=70.0,
)

contracts = config.load_forward_contracts(additional_contracts=[contract])
```

For period-specific quantities:

```python
profile_contract = ForwardContract.from_period_quantities(
    contract_id="FM-USER-SOLAR-DEMAND-PROFILE",
    delivery_participant_id="USER_SOLAR",
    offtake_participant_id="DEMAND",
    period_quantities={9: 5.0, 10: 10.0, 11: 15.0, 12: 15.0},
    price=70.0,
)
```

Or run the packaged example:

```bash
python examples/cyprus_single_day_fm_dam_ida.py
```
