# Optional single-envelope CCGTs and ON/OFF thermal commitment

These are deliberately simplified physical models, not an equivalent
reformulation of staged startup or configured combined-cycle commitment.
Existing staged ordinary thermals remain the default. Configured plants and
their reference/compact formulations remain available at the existing grid.

## Two independent choices

To simplify a CCGT, register it through `add_thermal()` with explicit plant-level
limits, rather than `add_configured_thermal()`. This already gives one commercial
asset, participant attribution, market schedule, meter and settlement position.
It contains no virtual configurations, component histories or incremental
configuration-change costs. Ordinary thermal registration can independently
select the new ON/OFF approximation:

```python
from emst import Market, ThermalState

market = Market()
market.add_participant('plant_owner')
market.add_thermal(
    'ccgt', 'plant_owner',
    capacity_mw=220, minimum_mw=75, marginal_cost=60,
    ramp_mw_hour=720,                    # symmetric fallback, MW/hour
    ramp_up_mw_hour=720, ramp_down_mw_hour=720,  # optional asymmetric overrides
    minimum_up_minutes=120, minimum_down_minutes=120,
    startup_cost=5000, reserve_limit_mw=30,
    initial_state=ThermalState('dispatch', 150, 1440),
    commitment_model='on_off',
    boundary_power_policy='technical_minimum',
    boundary_reserve_policy='none',
)
```

The boundary policies shown are the ON/OFF defaults and the only supported
choices in this version. `commitment_model='staged'` retains existing behaviour;
boundary-policy overrides and asymmetric ramp overrides currently require
`'on_off'`. ON/OFF initial states must be `dispatch` (ON) or `offline` (OFF).
Legacy startup/shutdown duration/profile inputs have no physical effect in the
explicit ON/OFF mode. They do not introduce SYNC/SOAK/DESYNC occupancy variables.

Accept explicit plant envelopes. Aggregating source rows belongs in an adapter:
additive GT-equivalent contributions can be summed when justified, but an
arbitrary table of overlapping configurations must not be summed. In particular,
220 MW maximum, 75 MW minimum and 720 MW/hour ramp are synthetic illustration
parameters, not verified manufacturer capabilities for Vasilikos.

## Decisions and costs

Each thermal has one binary ON indicator per **effective ISP interval**. Exact
continuous startup/stop auxiliaries use the event hull:

```
u[t] - u[t-1] = v[t] - w[t]
0 <= v[t] <= u[t];       v[t] <= 1-u[t-1]
0 <= w[t] <= u[t-1];     w[t] <= 1-u[t]
```

With binary ON indicators, v/w are uniquely integer without event binaries,
including zero startup costs and zero minimum durations. Power is constant MW
within each effective interval, and its market-energy bridge remains
`P * hours = commercial_MWh + energy_up_MWh - energy_down_MWh`.
The commercial schedule is the entering market position, not ISP dispatch.

OFF is zero. ON lies in `[minimum_mw, capacity_mw]`. The first and last ON
intervals are fixed at technical minimum; a one-interval ON episode is minimum
throughout. Startup and shutdown do not create interpolated energy. Startup
cost is charged once for an OFF-to-ON event. An explicit ordinary-unit no-load
cost is included for ON time; no shutdown charge or new startup reimbursement
rule is introduced. Physical costs remain distinct from settlement transfers.

Consecutive ON intervals obey directional ramp limits. OFF/ON jumps are exempt
from those ramps, with the first/last-ON minimum conditions retained. Minimum
up/down times mean consecutive ON/OFF minutes. Non-aligned new duration
obligations use ceiling conversion to the effective ISP grid. Inherited elapsed
minutes are retained, not reset or rounded away.

Normal interior ON intervals retain reserve limits, product response rates and
shared headroom/footroom. Every product/direction is excluded in the whole first
and last ON effective interval, using linear constraints on v/w. This adds no
reserve-boundary binary. The four compatibility phase keys may still appear,
but sync/soak/desync are constant zeros and do not create variables.

## Native hourly daily integration

Commercial forecasts, DAM, forward schedules, meters and settlement remain on
the existing 48 half-hours. Select the effective ISP resolution at run time:

```python
results = market.run(
    time_index=market.time_index('2026-11-05'),
    isp_interval_minutes=60,          # 30 remains the default
    rtbm_interval_minutes=30,
    reserve_requirements=requirements,
    historical_rtbm_prices=history,
)
```

This requires ON/OFF ordinary thermals throughout the thermal fleet. Native
hourly configured/staged plants and hourly storage are not implemented by this
option; they are rejected explicitly. Storage and mixed staged/ON-OFF portfolios
retain the native 30-minute ISP option. The separately documented earlier hourly
configured-plant benchmark is still a standalone experiment.

The hourly model uses real 60-minute intervals directly: 24 intervals initially
and 12 from noon. There are no duplicated half-hour commitment binaries, pairing
equalities or half-hour repair MILPs. Raw ISP rows retain the global starting
half-hour `period` index (0,2,...,46 initially) and one-hour timestamps/durations.
Each hour averages MW demand/RES forecasts, sums entering commercial MWh, and
takes the maximum reserve and mandatory-ON requirements of its half-hour pair.
The energy balance is hourly; half-hour balances need not separately clear in
ISP. Reserve bids and reserve-shortage penalties retain their per-half-hour
monetary basis with duration weighting. There is still one MILP and one
fixed-discrete pricing LP per ISP run.

The run maps each selected hour to its two half-hour RTBM/accounting positions.
Reserve MW stay equal across the pair. Native hourly reserve prices are split
across accounting halves, so payment over both halves equals the hourly award
times its native price. Commercial MWh, instructed quantities, daily settlement,
UA1/UA2 and financial positions retain the half-hour accounting grid. Readable
private ISP market schedules sum the two entering half-hour positions.

The native clock changes optimization decisions; it is not a claim of physical
equivalence with half-hour commitment, even on identical synthetic inputs.
Only complete normal non-DST days are supported, as in the existing public API.

## RTBM, noon and midnight

This ON/OFF delivery policy currently supports **30-minute sequential RTBM**.
Public ON/OFF runs requesting five-minute RTBM are explicitly rejected. Current
interval demand/RES knowledge remains the existing 30-minute assumption.
RTBM follows ISP commitment and holds boundary power at minimum in every
half-hour belonging to the fixed effective interval. Interior ON power can
adjust through balancing energy. Delivery uses rectangular interval energy,
not half-ramp startup/shutdown energy.

To preserve the requested consecutive-ON ramp behaviour, this new model bounds
successive ON RTBM steps by the directional rate times the RTBM duration. This
is more restrictive than the older 30-minute mode's fully unrestricted interior
power jumps for staged units. OFF/ON jumps remain exempt. RTBM prepares for a
known same-day boundary minimum using the current ISP schedule and ramp
reachability constraints; it does not inspect next-day demand or actuals.

An immediate initial shutdown requires preceding executed ON power at minimum
and satisfied minimum up time. A high initial boundary therefore needs one or
more additional ON intervals to ramp down, followed by a full minimum-output
interval, before shutdown. Hourly planning also checks reachability on the
30-minute delivery grid, so a first-hour minimum is not imposed through an
infeasible first RTBM jump. An ON horizon end is not a shutdown; no terminal
minimum is imposed, and unfinished dwell obligations carry into the next day.

At 11:00 the existing forecast bridge constructs the noon state. If the new plan
selects an immediate noon shutdown, the still-unexecuted preceding effective MTU
is prospectively fixed to minimum. The implementation permits this only when
that restriction is ramp-reachable from the current executed state and the
preceding MTU has **no already-procured reserve awards**. Otherwise it keeps the
unit ON into the noon horizon and schedules a later legal shutdown. This is an
explicit conservative integration difference from a pilot that assumes the
preceding boundary is already fixed; no prior power or reserve award is silently
rewritten or withdrawn. First-ON episodes crossing noon retain their elapsed
ON time and incur no duplicate startup cost.

Consecutive-day execution carries ON/OFF status, actual boundary MW and elapsed
minutes through the existing checkpoint machinery. The ISP resolution must be
consistent across a period and cannot change on resume. A new library revision
still changes the implementation fingerprint, as before; old-code checkpoints
are not silently migrated.

## Examples, exports and validation

From an installed checkout:

```console
python examples/onoff_day.py --isp-interval-minutes 30 --output output/onoff30
python examples/onoff_day.py --isp-interval-minutes 60 --output output/onoff60
python tools/benchmark_onoff_isp.py --units 13 --repetitions 3
python -m unittest discover -s tests -p test_onoff.py -v
python tools/run_tests.py
```

Both examples save separate raw and readable JSONs, including financial
positions, plus `verification.json`. Exports identify model, effective duration,
power/reserve boundary policies, actual binary counts and optimization slacks.
The CCGT has one plant schedule/meter/cost/settlement position with no virtual
configuration entries. Inputs are fully synthetic and the private pilot was not
read or imported. The source adapter's asymmetric aggregation and any RES
changes in its cumulative patch are not copied into EMST.

Focused tests enumerate ON/OFF schedules against an independent duration/event
oracle and cover first/last minimum power, one-interval episodes, ramp/jump
rules, exact zero-cost event auxiliaries, inherited obligations, full boundary
hours, noon continuation and shutdown, slow cross-midnight shutdown, costs and
cash, accurate counts, hourly energy/reserve accounting and exact two-day
continuous-versus-checkpoint/resume equality at both grids. These are ON/OFF
equivalence tests, not equivalence claims against the richer staged model.

## Synthetic timing evidence

The 24 November 2026 benchmark used one single-envelope CCGT and 12 ordinary
thermal units, all using ON/OFF commitment, with explicit synthetic demand/RES,
reserve series and a supplied forward contract. Three serial repetitions per
grid alternated execution order. Conditions were Python 3.14.10, PuLP 3.3.2,
HiGHS 1.16.0, four threads, a 90-second limit per solve, identical default
numerical tolerances, zero relative/absolute gaps and no warm starts. RTBM was
30-minute sequential in both cases. These inputs do not reproduce the private
pilot or its historical CPLEX timings.

Median seconds (construction, MILP, pricing and total are measured separately;
independent column medians need not sum exactly):

| ISP grid / run | Binaries | Build | MILP | Pricing LP | ISP total |
|---|---:|---:|---:|---:|---:|
| 30 minutes, initial 48 intervals | 624 | 1.041 | 3.262 | 0.803 | 5.183 |
| 30 minutes, noon 24 intervals | 312 | 0.578 | 1.486 | 0.465 | 2.614 |
| 60 minutes, initial 24 intervals | 312 | 0.555 | 1.490 | 0.482 | 2.562 |
| 60 minutes, noon 12 intervals | 156 | 0.282 | 0.551 | 0.245 | 1.050 |

The median combined initial/noon ISP time was **7.812 versus 3.607 seconds**.
Median full daily calculation time was **10.038 versus 5.946 seconds**, excluding
exports. Full-day ranges were 6.229-11.051 and 5.834-6.172 seconds respectively;
these small repeated samples do not establish a guaranteed speedup. Median
independent in-engine validation time was 0.384 and 0.370 seconds. Each run
records export time separately, as well as full solver records, original inputs,
raw/readable results and slack/residual diagnostics.

All six synthetic days reached optimality, passed stage and participant-cash
checks, and used no optimization security slack. Maximum actual delivery
residual was approximately 1.01e-9 MWh, below the 1e-6 MWh audit tolerance. This
is a deliberately small reproducible case, not an adequacy result or a physical
equivalence claim between grids. Evidence directory:
`output/onoff-isp-benchmark-20261124-162304/`.

The public two-unit examples were also run at both grids with successful audits
and separate raw/readable exports in `output/onoff-public-30/` and
`output/onoff-public-60/`. A built wheel was installed into an isolated target
and ran a full hourly day with 24/12 ISP intervals and reconciled settlement.

Regression validation on 25 November 2026 covered all 213 collected tests with
no failures, errors or skips (Python 3.14.10, HiGHS 1.16.0). The original run
completed 120 passing tests before interruption; a continuation selected the
remaining 93 tests by test ID and passed all of them in 141.44 seconds. This is
combined coverage across two runs, not a single uninterrupted suite execution.
Logs are in `output/onoff-validation-20261125/` and
`output/onoff-validation-remaining-20261125/`.
