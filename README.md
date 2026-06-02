# EMST - Electricity Market Simulation Tool

EMST is a Python library for sequential electricity market simulation. Version 0.1 focuses on energy-exchange price formation and inter-market arbitrage across Forward Market, Day-Ahead Market, and Intraday Auction stages.

The first publishable scope is:

- Forward Market (FM) as exogenous participant schedules.
- Day-Ahead Market (DAM) as social-welfare maximization.
- Intraday Auctions (IDA1, IDA2, IDA3) as sequential residual-position auctions.
- A predefined Cyprus configuration with simple participants, assets, forecasts, and one single-day scenario.

EMST v0.1 focuses on sequential energy-exchange price formation and inter-market arbitrage across Forward Market, Day-Ahead Market, and Intraday Auction markets. Balancing markets, reserve procurement, and real-time redispatch are planned future extensions.

## What Is Included

- Typed participant, asset, bid, scenario, time, and result dataclasses.
- Generic welfare-clearing solver interface with a PuLP implementation.
- Reusable DAM and IDA clearing modules.
- CyprusMarketConfig as the first predefined system configuration.
- A runnable single-day FM-DAM-IDA example.
- Pytest smoke tests for package loading and the sequential simulation.

## What Is Excluded From v0.1

- Balancing markets.
- Integrated scheduling process logic.
- Reserve procurement.
- Real-time balancing market and redispatch.
- External Excel, CSV, database, or GUI inputs.
- Annual time-series simulations.

## Installation

```bash
pip install -e ".[dev]"
```

## Quickstart

```python
from emst.systems.cyprus import CyprusMarketConfig
from emst.core.simulation import SequentialMarketSimulation

config = CyprusMarketConfig.default()
scenario = config.load_scenario("high_solar_day")
participants = config.load_participants()

simulation = SequentialMarketSimulation(
    config=config,
    participants=participants,
    scenario=scenario,
)

results = simulation.run()

print(results.price_summary)
print(results.final_positions)
```

The Cyprus configuration can use different MTU lengths:

```python
hourly = CyprusMarketConfig.default(mtu_minutes=60)      # 24 periods
half_hourly = CyprusMarketConfig.default(mtu_minutes=30) # 48 periods
quarter_hourly = CyprusMarketConfig.default(mtu_minutes=15) # 96 periods
```

Predefined Cyprus profiles are stored as hourly source values and expanded to MTU-energy quantities. For example, a 430 MW hourly demand value becomes two 215 MWh periods when `mtu_minutes=30`.

Users can also add their own participants in code:

```python
from emst.core.participants import Asset, Participant

my_thermal = Participant(
    participant_id="MY_UNIT",
    name="My Thermal Unit",
    participant_type="production",
    assets=[
        Asset(
            asset_id="my_ccgt",
            name="My CCGT",
            asset_type="thermal",
            capacity=100.0,
            marginal_cost=90.0,
            metadata={"technical_minimum": 25.0},
        )
    ],
)

participants = config.load_participants(additional_participants=[my_thermal])
```

Bilateral forward contracts are also defined in code:

```python
from emst.core.contracts import ForwardContract

contract = ForwardContract.fixed_quantity(
    contract_id="FM-MYUNIT-DEMAND",
    delivery_participant_id="MY_UNIT",
    offtake_participant_id="DEMAND",
    quantity=20.0,
    periods=config.market_time.periods,
    price=92.0,
)

contracts = config.load_forward_contracts(additional_contracts=[contract])
```

For period-specific bilateral quantities:

```python
profile_contract = ForwardContract.from_period_quantities(
    contract_id="FM-MYUNIT-DEMAND-PROFILE",
    delivery_participant_id="MY_UNIT",
    offtake_participant_id="DEMAND",
    period_quantities={
        1: 10.0,
        2: 10.0,
        18: 35.0,
        19: 35.0,
    },
    price=92.0,
)
```

DAM block orders support minimum acceptance ratios and parent-child links:

```python
from emst.core.bids import BlockBid

parent = BlockBid(
    bid_id="PARENT",
    participant_id="THERMAL_A",
    market="DAM",
    periods=(1, 2, 3),
    quantities=(50.0, 50.0, 50.0),
    prices=(80.0, 80.0, 80.0),
    side="sell",
)

child = BlockBid(
    bid_id="CHILD",
    participant_id="THERMAL_A",
    market="DAM",
    periods=(4, 5),
    quantities=(40.0, 40.0),
    prices=(75.0, 75.0),
    side="sell",
    parent_bid_id="PARENT",
)
```

The PuLP solver rejects paradoxically accepted blocks before returning the final solution.
For MILP/block-order markets, clearing prices are calculated from the balance-constraint
shadow prices of the LP obtained after fixing the accepted binary block variables.

## Run The Example

```bash
python examples/cyprus_single_day_fm_dam_ida.py
```

## Run Tests

```bash
pytest
```

## Cyprus Configuration

The Cyprus configuration is intentionally small and code-native in v0.1. It includes thermal generation, solar and wind aggregators, one BESS unit, one demand participant, a high-solar single-day scenario, and the market sequence FM -> DAM -> IDA1 -> IDA2 -> IDA3.

## Manual

See [docs/manual.md](docs/manual.md) for the full library manual.

## Roadmap

- Richer order types and settlement reporting.
- External data importers.
- Additional national system configurations.
- Validation against historical or synthetic benchmark cases.
- Future balancing-market, reserve-procurement, and redispatch modules.
