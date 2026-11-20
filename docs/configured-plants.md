# Configuration-based thermal plants

`Market.add_configured_thermal()` registers **one physical plant, one thermal
asset and one market participant**. Its configurations are internal alternatives,
not separately registered assets or meters. Existing `add_thermal()` is unchanged.

See [the complete example](../examples/configured_plant.py):

```sh
python examples/configured_plant.py --rtbm-interval-minutes 5 --output ccgt5
python examples/configured_plant.py --rtbm-interval-minutes 30 --output ccgt30
```

The example parameters are synthetic, not a calibration of an actual Cyprus plant.
Both commands export `raw/`, `readable_results.json` and shared/private readable
files, plus `verification.json`. The example includes a separate flexible thermal
unit to balance the combined-cycle plant's transition trajectory. It represents
another physical generator, not another configuration of the same plant.

## Registration and inputs

```python
from emst import PlantState

plant = market.add_configured_thermal(
    "ccgt", "plant_owner",
    components=components,
    configurations=configurations,
    transitions=transitions,
    initial_state=PlantState(
        configuration="GT1_ST",
        power_mw=60,
        component_histories={
            "GT1": {"on": True, "duration_minutes": 600},
            "GT2": {"on": False, "duration_minutes": 600},
            "ST": {"on": True, "duration_minutes": 600},
        },
    ),
)
```

`components` maps component IDs to `minimum_up_minutes` and
`minimum_down_minutes` (default zero). Durations must be whole half-hours.
Component clocks describe physical online/offline status, including time spent
in transitions. They do not restart when a configuration name changes.

`configurations` maps names to:

| Field | Meaning / default |
|---|---|
| `components` | Online component IDs; required for operating configurations |
| `minimum_mw`, `maximum_mw` | Net plant output range; default zero |
| `marginal_cost` | Physical variable energy cost, EUR/MWh; default zero |
| `ramp_mw_hour` | Stable-configuration ramp limit; default 1000 |
| `no_load_cost_per_hour` | Online cost, EUR/hour; default zero |
| `reserve_limit_mw` | Per-product capability, also bounded by joint headroom and response ramp; default zero |

An `OFF` configuration with no components, output, reserves or no-load cost is
required. Configuration net capacities already include the appropriate shared
steam contribution. The model never adds alternative configuration capacities.

`transitions` is a list of directed edges:

```python
{
    "id": "add2",
    "source": "GT1_ST",
    "target": "GT1_GT2_ST",
    "profile_mw": [100, 120],
    "components_by_step": [["GT1", "GT2", "ST"], ["GT1", "GT2", "ST"]],
    "cost_eur": 250,
    "ramp_mw_hour": 400,
}
```

`profile_mw` contains **terminal net plant MW**, one value per half-hour. This
edge takes 60 minutes. Output moves linearly from the plant's entry power to the
first endpoint, then between subsequent endpoints. ISP uses the corresponding
average energy per MTU. `components_by_step` explicitly declares the components
online throughout each step; its final set must match the target configuration.
This avoids inventing assumptions about when a gas/steam turbine becomes online.
The transition ramp limit defaults to the larger source/target ramp limit.
Transition reserves default to zero. Eligible products and directions can be
enabled explicitly on each edge; see the next section. Optional transition
`marginal_cost` and `no_load_cost_per_hour` override operating costs during the
trajectory; they default to the target configuration, or the source for shutdown
to OFF. These operating costs are separate from the once-only `cost_eur`.

The edge cost is incremental: starting GT2 while GT1 remains online can cost
EUR250, rather than the EUR1000 cost of starting the plant from OFF. A transition
is charged when it begins, not once per step or again after an ISP rerun/resume.
Permitted transitions are exactly the supplied edges. There are no implicit
shortcuts, interruptions or instantaneous configuration changes. A completed
transition can be followed immediately by another supplied permitted edge; no
extra stable MTU is inserted. Component minimum-time obligations still apply.

### Reserve capacity during transitions

To declare technical eligibility, add these fields to an edge before registering
the plant (all arrays have one value per `profile_mw` step):

```python
edge.update(
    reserve_eligible_products=["FCR_up", "FCR_down", "aFRR_up", "mFRR_up"],
    reserve_minimum_mw=[40, 40],
    reserve_maximum_mw=[160, 160],
)
```

The default `reserve_eligible_products=[]` prohibits all transition capacity.
Known product/direction names are `FCR_up`, `FCR_down`, `aFRR_up`, `aFRR_down`,
`mFRR_up`, `mFRR_down`, and `RR_up`. Eligibility requires explicit minimum and
maximum MW envelopes covering the whole linear trajectory within each step,
including its entry power. Bounds must contain the declared profile endpoints;
the ISP also checks actual entry-power compatibility. These bounds constrain
reserve deliverability, not a second independent dispatch schedule.

Per-product capability is capped by the smaller source/destination
`reserve_limit_mw` and the response-time ramp limit. Total upward/downward
reserves across products share physical headroom/footroom at both the entry and
terminal power. For linear steps this protects every point on the trajectory.
Response ramps account for the baseline scheduled ramp: for response time tau
and baseline slope v, upward capability is at most tau*(R-v), downward at most
tau*(R+v), where R is the declared transition ramp limit. Static response
capability is additionally capped by the smallest transition/source/destination
ramp limit. This keeps a technically permitted transition feasible even if a
stable configuration has a lower response rate. This is a conservative fixed-step capability calculation, not a
multi-step dynamic reserve deployment simulation.

The existing continuous reserve variables are reused. No new binary or
continuous decision variables are introduced. All configured ISP formulations
share these constraints. RTBM still has no reserve-capacity products and retains
its existing prescribed transition trajectory behaviour; this change concerns
ISP procurement and does not implement a new RTBM activation policy.

Only technically supported transitions should be enabled. Paths involving OFF
have zero capacity under the source/destination minimum-capability rule. The
synthetic hourly comparison is reproducible with:

```console
python tools/benchmark_cc_transition_reserves.py
```

The full daily example also accepts `--transition-reserves`, independently of
`--configuration-formulation compact` and `--rtbm-interval-minutes 5|30`:

```console
python examples/configured_plant.py --configuration-formulation compact --transition-reserves --rtbm-interval-minutes 30 --output ccgt-reserves
```

This enables the example's synthetic internal-transition eligibility. It does
not infer technical capability for a real plant. Its normal daily run retains
the half-hour ISP and exports raw/readable results and verification JSON.

See [the experimental hourly CC benchmark](hourly-virtual-cc-experiment.md) for
the hourly approximation, synthetic assumptions and initial-ISP-only scope.

An unfinished boundary state additionally supplies
`transition={"id": "add2", "elapsed_minutes": 30}` and the actual boundary power.
`configuration` remains the source until completion. Initial component histories
must cover every component and agree with the previous completed transition step.
ISP boundaries must be half-hour aligned. RTBM internally advances five-minute
or 30-minute progress and carries unfinished transitions automatically.

## DAM, ISP and RTBM

Forward contracts and simple DAM energy orders belong to the plant asset.
The DAM sell envelope is the **maximum single-configuration capacity**, not the
sum. Default DAM price is the lowest operating configuration marginal cost;
`dam_price` optionally overrides it via a fixed markup. Existing thermal strategy,
energy-offer and reserve-price arguments are also available.

ISP uses mutually exclusive binary occupancy of stable configurations and
transition steps, legal graph flows, and physical component start/stop clocks.
It enforces configuration output/ramp limits, reserve headroom and response
capability, transition profiles, and minimum component on/off times. Inherited
transitions must finish. No next-day lookahead is introduced; outstanding
obligations remain in the carried state.

One upward/downward energy offer pair is retained at plant level. A configuration
cost adjustment `(configuration marginal cost - plant base cost) * energy`, plus
configuration no-load costs and incremental transition costs, enters the
objective. This makes configuration selection sensitive to physical costs
without multiplying a variable power by a binary: power is split across mutually
exclusive internal states. Published private directional offer prices include
the selected marginal-cost adjustment. ISP objective components expose the
operating adjustment and transition cost separately for reconciliation.

RTBM fixes the configuration/transition from the effective ISP run. It does not
independently recommit the plant. Five-minute RTBM retains physical thermal ramp
trajectories. Thirty-minute mode allows immediate changes **within stable
configurations**, but still follows scheduled transition duration and linear
output trajectories. Transition profiles may require other resources to balance
within a half-hour even if their average energy is balanced in ISP.

Block and linked-block orders can express useful commercial scheduling
conditions, but do not automatically enforce the configuration graph, shared
component histories, ramp trajectories or reserves. They do not guarantee
physical feasibility. The public configured-plant example retains simple energy
orders; it does not automatically construct configuration-dependent DAM blocks.

## Settlement, results and carryover

Settlement uses one plant-level commercial schedule, delivered quantity,
instructed balancing quantity and reserve award per product. Configuration rows
are explanatory private details; they never receive a second energy or reserve
payment. Normal plant energy, reserve, tariff and UA1/UA2 accounting is retained.

Each ISP optimization includes the costs of future transitions in its own
horizon. If a transition is still in the future at the rerun, it can appear in
both optimization objectives; those objectives must not be added as daily
costs. Executed daily costs come from RTBM events only. Inherited transitions
already underway have no new entry cost.

Transition/start costs are physical costs, not automatically extra cashflows.
Configured plants expose `transition_cost_eur` once in executed RTBM and plant
asset economics. The existing generic S13 start-payment calculation is not
applied to these plants: no new regulatory entitlement to configuration-change
reimbursement is assumed. Their supplied edge costs must include the relevant
component startup/transition costs; no second whole-plant startup cost is added.
A market-specific payment policy would need a separate explicit specification.

Private ISP/RTBM readable results contain `configuration`, `configuration_state`,
exclusive state binaries, transition ID/source/target/step, online components,
transition costs, operating-cost adjustments and configuration endpoints.
Settlement includes plant asset economics and the existing financial positions.
Public shared summaries remain free of plant-private configuration details.

Ending state includes the current configuration, power, each component's status
and elapsed on/off time, and any unfinished transition's ID/elapsed time.
`run_period()` carries this state into the next day. The 11:00 ISP bridge carries
it into the noon-effective rerun. Checkpoints serialize and verify this same
state, including transitions that straddle midnight.

## Current scope

This is an explicit configuration graph, not a thermodynamic plant simulator.
No automatic steam-cycle efficiency curves, component outages, minimum steam
warm-up temperatures, hot/warm/cold-dependent transition costs, or separate
component electricity meters are inferred. Net configuration/profile data must
be supplied and calibrated by the user. Transition durations and component
minimum times use the half-hour ISP grid. Stable operation is continuous within
its declared limits; heat-rate curves remain constant marginal costs.
Configured plants are not enrolled automatically in the simplified MW must-run
auction. That auction still requires an explicit eligible minimum stable output
on the existing simple thermal model.

## Validation evidence

Publication validation on 20 November 2026 passed **196 tests**, with no skips,
failures, errors or collection errors (Python 3.14.10, HiGHS 1.16.0). This includes
a full public daily run using compact commitment and positive transition-capacity
awards, verified RTBM clearing and reconciled plant-level settlement. Local
evidence is in `output/publish-cc-validation/`; rerun `python tools/run_tests.py`
from a checkout to generate a new validation report.

The original configured-plant regression suite passed **153 tests** with HiGHS 1.16.0, without skips,
failures, errors or collection errors. All **12 dedicated configured-plant tests**
also passed with HiGHS 1.15.1. They check mutually exclusive capacity, illegal
jumps, component minimum up/down times, reserve headroom, default zero transition
reserves, back-to-back transitions, explicit operating-cost reconciliation,
both RTBM modes, one plant meter/settlement position, noon continuation, and
exact continuous-versus-checkpoint/resume equality with an unfinished midnight
transition. The incremental GT2 transition costs EUR250 once; GT1/ST histories
continue without a whole-plant restart.

Local evidence: `output/configured-plant-final-tests16/`,
`tmp/configured-plant-final15.log`, and the raw/readable daily example exports in
`output/configuration5/` and `output/configuration30/`. Both example exports passed
every stage audit, used no security slack, and reconciled participant cashflows.

## Optional compact commitment

The expanded reference formulation remains the default. Pass
`configuration_formulation="compact"` to `add_configured_thermal()` for the exact
transition-start formulation. It preserves the physical model and derives
transition progression without a binary for every transition step. See the
[equations, equivalence proof and reproduction instructions](compact-configurations.md)
and [matched synthetic benchmark results](compact-configurations-benchmarks.md).
