# EMST Library Manual

This manual describes the current EMST v0.1 Python library.

EMST, the Electricity Market Simulation Tool, is a modular library for sequential energy-exchange market simulation. The current version focuses on:

- Forward Market (FM)
- Day-Ahead Market (DAM)
- Intraday Auctions (IDA1, IDA2, IDA3)

Balancing markets, reserve procurement, integrated scheduling processes, real-time balancing, and redispatch are intentionally outside the v0.1 scope.

## 1. Installation

Create and activate a clean environment:

```powershell
conda create -n emst python=3.11 -y
conda activate emst
```

Install the library from the project root:

```powershell
python -m pip install -e .
```

Install with development and test tools:

```powershell
python -m pip install -e .[dev]
```

Run tests:

```powershell
python -m pytest
```

Runtime dependency:

```text
pulp>=2.7
```

Development dependency:

```text
pytest>=8
```

## 2. Package Structure

```text
mst/
  core/
  markets/
    forward/
    dam/
    intraday/
  systems/
    cyprus/
  solvers/
examples/
tests/
docs/
```

The main design principle is separation between generic market logic and system-specific configuration.

Generic modules live in:

```text
mst/core
mst/markets
mst/solvers
```

Cyprus-specific defaults live in:

```text
mst/systems/cyprus
```

## 3. Core Concepts

### Participant

A participant represents a market actor.

```python
from mst.core.participants import Participant

participant = Participant(
    participant_id="GEN_A",
    name="Generator A",
    participant_type="production",
    assets=[],
)
```

### Asset

An asset is a physical or commercial resource owned by a participant.

```python
from mst.core.participants import Asset

asset = Asset(
    asset_id="ccgt_a",
    name="CCGT A",
    asset_type="thermal",
    capacity=200.0,
    marginal_cost=85.0,
    metadata={"technical_minimum": 50.0},
)
```

Supported asset types in the current bid-generation strategy:

```text
thermal
solar
wind
storage
demand
```

### MarketTime

`MarketTime` defines the single-day MTU structure.

```python
from datetime import date
from mst.core.time import MarketTime

market_time = MarketTime.single_day(date(2026, 6, 1), mtu_minutes=30)
```

Common configurations:

```python
MarketTime.single_day(date(2026, 6, 1), mtu_minutes=60)  # 24 MTUs
MarketTime.single_day(date(2026, 6, 1), mtu_minutes=30)  # 48 MTUs
MarketTime.single_day(date(2026, 6, 1), mtu_minutes=15)  # 96 MTUs
```

The MTU length must divide a 24-hour day exactly.

### Scenario

A scenario stores demand, solar, and wind profiles.

```python
from mst.core.scenarios import Scenario

scenario = Scenario(
    scenario_id="example",
    name="Example Day",
    demand_profile={1: 500.0},
    solar_profile={1: 0.5},
    wind_profile={1: 0.3},
    forecast_version="DAM",
)
```

Scenarios can also hold market-specific forecasts:

```python
scenario.forecast_for("DAM")
scenario.forecast_for("IDA1")
```

## 4. Cyprus Configuration

The predefined Cyprus system is loaded with:

```python
from mst.systems.cyprus import CyprusMarketConfig

config = CyprusMarketConfig.default()
```

Default hourly configuration:

```python
config = CyprusMarketConfig.default(mtu_minutes=60)
```

Half-hourly configuration:

```python
config = CyprusMarketConfig.default(mtu_minutes=30)
```

Quarter-hourly configuration:

```python
config = CyprusMarketConfig.default(mtu_minutes=15)
```

Load default participants:

```python
participants = config.load_participants()
```

Load a predefined scenario:

```python
scenario = config.load_scenario("high_solar_day")
```

The default Cyprus market sequence is:

```text
FM -> DAM -> IDA1 -> IDA2 -> IDA3
```

## 5. User-Defined Participants

Users can extend the Cyprus default participant set:

```python
from mst.core.participants import Asset, Participant

my_unit = Participant(
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

participants = config.load_participants(
    additional_participants=[my_unit]
)
```

Users can also use only their own participants:

```python
participants = config.load_participants(
    additional_participants=[my_unit],
    include_defaults=False,
)
```

Participant IDs must be unique. Asset IDs must be unique within each participant.

## 6. Forward Market

The v0.1 Forward Market is exogenous. It does not solve an optimization problem.

Forward positions are represented as bilateral contracts.

### Fixed-Quantity Forward Contract

```python
from mst.core.contracts import ForwardContract

contract = ForwardContract.fixed_quantity(
    contract_id="FM-GEN-DEMAND",
    delivery_participant_id="GEN_A",
    offtake_participant_id="DEMAND",
    quantity=20.0,
    periods=config.market_time.periods,
    price=92.0,
)
```

### Period-Specific Forward Contract

```python
contract = ForwardContract.from_period_quantities(
    contract_id="FM-GEN-DEMAND-PROFILE",
    delivery_participant_id="GEN_A",
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

Interpretation:

- The delivery participant receives a positive scheduled position.
- The offtake participant receives a negative scheduled position.
- Forward schedules affect residual positions in DAM and IDAs.

Clear the Forward Market directly:

```python
from mst.markets.forward import ForwardMarket

fm_result = ForwardMarket(config.market_time).clear(
    contracts=[contract],
    participants=participants,
)
```

## 7. DAM Orders

DAM supports:

- simple single-period bids
- block orders
- linked block orders
- circular block orders
- minimum acceptance ratios
- paradoxically accepted block rejection

### Simple Bid

```python
from mst.core.bids import Bid

bid = Bid(
    bid_id="DAM-DEMAND-1",
    participant_id="DEMAND",
    market="DAM",
    period=1,
    side="buy",
    quantity=100.0,
    price=500.0,
)
```

`side` can be:

```text
buy
sell
```

Simple bids are continuously divisible between zero and their submitted quantity.

### Block Bid

```python
from mst.core.bids import BlockBid

block = BlockBid(
    bid_id="DAM-BLOCK-1",
    participant_id="GEN_A",
    market="DAM",
    periods=(1, 2, 3),
    quantities=(50.0, 50.0, 50.0),
    prices=(80.0, 80.0, 80.0),
    side="sell",
)
```

By default, block orders are binary:

```python
acceptance_binary=True
minimum_acceptance_ratio=1.0
```

### Block Bid With Minimum Acceptance Ratio

```python
block = BlockBid(
    bid_id="DAM-PARTIAL-BLOCK",
    participant_id="GEN_A",
    market="DAM",
    periods=(1, 2, 3),
    quantities=(50.0, 50.0, 50.0),
    prices=(80.0, 80.0, 80.0),
    side="sell",
    acceptance_binary=False,
    minimum_acceptance_ratio=0.5,
)
```

If active, this block must clear at least 50 percent and at most 100 percent.

### Linked Block Order

Linked blocks use `parent_bid_id`.

```python
parent = BlockBid(
    bid_id="PARENT",
    participant_id="GEN_A",
    market="DAM",
    periods=(1, 2, 3),
    quantities=(50.0, 50.0, 50.0),
    prices=(80.0, 80.0, 80.0),
    side="sell",
)

child = BlockBid(
    bid_id="CHILD",
    participant_id="GEN_A",
    market="DAM",
    periods=(4, 5),
    quantities=(40.0, 40.0),
    prices=(75.0, 75.0),
    side="sell",
    parent_bid_id="PARENT",
)
```

The child can only clear if the parent clears. The child acceptance ratio cannot exceed the parent acceptance ratio.

### Circular Block Order

Circular blocks are used for storage-style approximations.

```python
from mst.core.bids import CircularBlockBid

circular = CircularBlockBid(
    family_id="BESS-CYCLE",
    participant_id="BESS_1",
    linked_blocks=[charge_block, discharge_block],
    net_energy_balance_rule="zero_sum",
)
```

The current supported net-energy rule is:

```text
zero_sum
```

## 8. DAM Clearing

DAM is cleared as a welfare-maximizing MILP.

```python
from mst.markets.dam import DAMMarket

dam = DAMMarket(config=config.dam, market_time=config.market_time)

dam_result = dam.clear(
    bids=dam_bids,
    block_bids=dam_blocks,
    circular_block_bids=circular_blocks,
)
```

The solver:

1. Solves the welfare-maximizing MILP.
2. Fixes accepted binary block variables.
3. Re-solves the resulting LP.
4. Calculates clearing prices from LP balance-constraint shadow prices.
5. Detects paradoxically accepted blocks.
6. Rejects paradoxically accepted blocks.
7. Repeats until no paradoxically accepted blocks remain.

The result metadata includes:

```python
dam_result.metadata["price_method"]
# "fixed_binary_lp_shadow_prices"
```

and:

```python
dam_result.metadata["rejected_paradoxical_blocks"]
```

## 9. Intraday Auctions

IDA1, IDA2, and IDA3 use the same clearing engine as DAM, but in v0.1 they are simple adjustment auctions.

Current IDA behavior:

- simple orders only
- no block orders
- no linked block orders
- no circular block orders

Example:

```python
from mst.markets.intraday import IntradayAuctionMarket

ida1 = IntradayAuctionMarket(
    name="IDA1",
    config=config.markets["IDA1"],
    market_time=config.market_time,
)

ida1_result = ida1.clear(bids=ida1_bids)
```

IDA bids adjust positions relative to previous market outcomes for the same MTU.

## 10. Sequential Simulation

The high-level API runs FM, DAM, IDA1, IDA2, and IDA3:

```python
from mst.core.simulation import SequentialMarketSimulation
from mst.systems.cyprus import CyprusMarketConfig

config = CyprusMarketConfig.default()
participants = config.load_participants()
scenario = config.load_scenario("high_solar_day")

simulation = SequentialMarketSimulation(
    config=config,
    participants=participants,
    scenario=scenario,
)

results = simulation.run()
```

Access prices:

```python
results.price_summary
```

Access final positions:

```python
results.final_positions
```

Access individual market results:

```python
results.fm_result
results.dam_result
results.ida_results["IDA1"]
```

## 11. Results

Each market returns a `MarketResult`.

```python
MarketResult(
    market_name="DAM",
    prices={...},
    accepted_bids={...},
    participant_positions={...},
    social_welfare=...,
    metadata={...},
)
```

Fields:

```text
market_name
prices
accepted_bids
participant_positions
social_welfare
metadata
```

Participant positions use the sign convention:

```text
positive = net delivery / sale
negative = net offtake / purchase
```

## 12. Examples

Run the default Cyprus sequential example:

```powershell
python examples\cyprus_single_day_fm_dam_ida.py
```

Run the full user-defined workflow example:

```powershell
python examples\cyprus_user_defined_workflow.py
```

The user-defined workflow demonstrates:

- user-defined participants
- a user-defined bilateral forward contract
- user-defined DAM simple bids
- a DAM block order
- a DAM linked block order
- user-defined IDA1 adjustment bids
- HTML visualization

It writes:

```text
examples/output/cyprus_user_defined_results.html
```

## 13. Testing

Run all tests:

```powershell
python -m pytest
```

Current test coverage includes:

- Cyprus config loading
- participant loading
- scenario loading
- MTU resolution for 60, 30, and 15 minutes
- forward market contracts
- period-specific contracts
- DAM clearing
- block minimum acceptance ratio
- linked block behavior
- paradoxically accepted block rejection
- IDA no-block-order behavior
- sequential simulation

## 14. Current Limitations

EMST v0.1 is a clean foundation, not yet a fully calibrated market replica.

Current limitations:

- All input data is code-defined.
- No Excel, CSV, database, API, or GUI imports.
- No balancing market.
- No reserve procurement.
- No ISP.
- No RTBM.
- No redispatch.
- No annual time-series simulation.
- No full Cyprus market-rule calibration.
- No exclusive block groups.
- No complex network constraints.
- No unit commitment constraints beyond simplified block-order behavior.

## 15. Recommended Extension Path

Suggested next steps:

1. Add structured import/export helpers.
2. Add richer scenario builders.
3. Add settlement summaries.
4. Add stronger DAM price validation cases.
5. Add calibrated Cyprus participant data.
6. Add optional external solver configuration.
7. Add future balancing-market modules outside the v0.1 energy-exchange core.

