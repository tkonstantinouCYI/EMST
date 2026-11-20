# Exact compact configuration commitment

The optional `configuration_formulation="compact"` in
`Market.add_configured_thermal()` replaces only the configured plant's ISP
commitment automaton and component-event declarations. The default remains
`"reference"`; both formulations are available for validation and local studies.
Power, actual entry-power linkage, endpoint profiles, ramping, reserve envelopes,
operating-cost expressions, RTBM, metering, settlement and state carryover share
the same implementation. No transition/configuration is deleted or shortened.
Optimality gaps, time limits and physical slack penalties are not changed.

## Equations

Index half-hours by t=0,...,T-1. A directed transition e has source a(e), target
b(e), and fixed duration L(e)>=1. Let q[c,t] be a binary stable-configuration
occupancy and x[e,t] a binary transition start. There are no binary transition-step
occupancies. For k=0,...,L(e)-1, define the affine expression:

```
z[e,k,t] = x[e,t-k] + I[e,k,t]
```

The start term is zero for a negative index. I is a fixed 0/1 inherited occupancy:
if transition e has already executed m half-hours at the boundary, then
I[e,k,t]=1 exactly when k=m+t<L(e). It has R=L(e)-m remaining intervals and a
fixed completion at t=R. An inherited transition has no new start/cost at t=0,
including an explicitly inherited transition with zero elapsed time.

Stable boundary conservation is:

```
q[c,t] + sum(x[e,t] for source(e)=c)
  = previous_stable[c,t]
  + sum(x[e,t-L(e)] for target(e)=c and t>=L(e))
  + inherited_completion[c,t]
```

At t=0, previous_stable is 1 only for the initial stable configuration when there
is no inherited transition; otherwise zero. For t>0 it is q[c,t-1]. The inherited
completion term is 1 for its target at t=R, and zero elsewhere. Add:

```
sum(q[c,t]) + sum(z[e,k,t]) = 1
```

All quantities are nonnegative. These equalities prevent overlap, departure from
a nonexistent source, early/late completion, interruption, and simultaneous
configurations. At a completion boundary, conservation permits starting a new
outgoing transition immediately; an extra stable half-hour is not required.
Starts near the horizon end are allowed, with unfinished progress carried out.
A transition that finishes outside the horizon supplies no premature completion.

The original per-state power variables and physical constraints now refer to q
or the affine z expressions. In particular the selected transition step still
has its specified endpoint E and average power `(actual_or_previous_endpoint+E)/2`.
The entry power remains a variable linked to the preceding actual/boundary power;
it is not substituted with zero, a nominal MW level, or a configuration minimum.

### Exact continuous component events

Let u[g,t] be the sum of occupancies whose declared online set contains component
g. This status is integer at every integer q/x solution. Let v be its previous
status (the supplied history at t=0), and use continuous s,d in [0,1]:

```
u - v = s - d
s <= u;  s <= 1-v
d <= v;  d <= 1-u
```

For (v,u)=(0,0),(0,1),(1,0),(1,1), the unique (s,d) values are respectively
(0,0),(1,0),(0,1),(0,0). Thus event integrality is guaranteed rather than assumed.
Only keeping `u-v=s-d` and `s+d<=1` would permit false fractional events and is
not the compact implementation. Original rolling minimum-up/down constraints
and inherited residual obligations use these same exact events.

### Cost and reporting

New transition entry costs are `cost[e]*x[e,t]`; inherited occupancy adds no
entry cost. Stable/transition operating costs and reserve prices are unchanged.
Private output still expands derived occupancies into the existing 0/1
`configuration_binaries` field for compatibility; that field does not imply
that every reported occupancy was declared a binary variable. Actual declared
counts are reported separately: occupancy, transition-start and component-event
binary counts. Physical component clocks and unfinished transition state use the
same `PlantState` serialization in both variants.

## Integer equivalence argument

**Reference to compact.** An integer reference solution has exactly one occupied
state per interval. Its legal flows force each transition through every fixed
step without waiting or interruption. Set q to its stable occupancy and x=1
exactly on new entries into a transition's first step (excluding inherited
occupancy). Every later step is exactly its shifted start or inherited I. Stable
inflow equals previous stable occupancy plus completed transitions, and is either
retained or used for a departure: the conservation/exclusivity equations hold.
Original binary component events are the unique events for these statuses, so
the continuous hull admits exactly their values.

**Compact to reference.** Nonnegative exclusive occupancies and integer q/x
select one physical state. A start has source availability by conservation;
shifted occupancies execute the complete transition for exactly L steps.
Inherited steps execute to completion with their original progress. Create the
reference flow along each consecutive selected state change: stable-to-entry,
step-to-next-step, last-step-to-target, or last-step-to-new-entry for an immediate
back-to-back transition. Stable persistence needs no flow, and the inherited
first interval has the original fixed zero flows. These are exactly permitted
reference edges. Component events from the hull are 0/1 and satisfy the original
binary-event equations/minimum times.

In both mappings keep every power, endpoint, energy activation and reserve
variable unchanged. Their coefficients, bounds and occupancy values are the same.
Consequently all physical constraints, histories, transition/operating costs and
objectives are identical. This also covers partially inherited transitions and
truncated horizon tails. Actual carried histories and once-only executed costs
remain unchanged for the same realized schedule. RTBM and settlement receive the
same commitment/physical data and have no separate compact physical model.

### Relaxation strength

Every compact relaxed path flow can be disaggregated among previous stable
occupancy and transitions completing at the same logical boundary. This is a
transportation assignment with total outgoing departures plus stable persistence
equal to the available mass. The original graph contains every corresponding
last-step-to-entry edge, so these flows satisfy its individual source/destination
bounds and state conservation. Fixed-step flows carry their shifted mass.
The continuous event hull also implies the reference relaxed difference and
`s+d<=1` constraints. With the same physical variables this embeds the compact
LP relaxation into the reference LP relaxation. Thus its minimization relaxation
cannot be weaker (up to numerical solver tolerance). A smaller binary count is
not a guarantee of faster presolve or branch-and-bound.

## Reference preservation and validation

`configurations_reference.py` retains the expanded state/flow and binary-event
equations from commit `aff3574`. The tool below rebuilds that commit in an isolated
local temporary package and hashes variables/bounds/types, objective coefficients
and all constraint coefficients/RHS/senses against the selectable reference.

```sh
python tools/verify_configuration_reference.py --output output/reference-parity
python tools/run_tests.py --output output/compact-tests
```

The equivalence tests independently enumerate 648 short state sequences with
mature/immature component histories and an inherited transition. Both formulations
must agree with the independent automaton on feasibility and entry cost.
Additional tests lift solved power/reserve/endpoints in both directions, test
13-step trajectories beginning at the last horizon interval, propagate their
remaining steps across a day boundary in both RTBM modes, and run the existing
configured-plant tests against both variants (including noon reruns, settlement
reconciliation and exact checkpoint/resume).

The final HiGHS 1.16.0 regression run passed **171 tests**, without skips,
failures, errors or collection errors. Evidence:
`output/compact-final-tests/unit-tests-highs.json`. All 11 directed benchmark
physical-point lifts passed (`output/compact-final-lifts.json`). The final
reference parity check is in `output/compact-reference-parity-final/`.

Different optimal configurations, component permutations, reserve allocations
or dual prices are not automatically inequivalence. Compare certified objectives,
validate each full primal solution, and fix/lift a common physical solution when
investigating alternatives. Fixed-discrete shadow prices may differ under dual
degeneracy; settlement must still reconcile separately for each cleared outcome.

## Fully synthetic benchmarks

`examples/synthetic_configured_fleet.py` generates explicit JSON with configuration
limits/costs, every transition endpoint and component set, initial histories,
demand/RES and reserve arrays. There is no dependency on private Cyprus data.
Cases include short transitions, 13-half-hour starts, inherited starts, alternative
paths, economically selected transitions, and an explicitly forced long start.
The fleet case has four two-GT/shared-ST plants, one three-GT/shared-ST plant and
eight conventional units, across 48 half-hours.

For each two-GT plant, four stable states + 56 transition steps + six component
event binaries give 66 binaries/interval (3,168/day). Compact uses four stable
plus eight transition-start binaries (576/day). The synthetic fleet deliberately
matches the reported 22,176 **reference binary count**, but is not the private
Cyprus model and does not reproduce its other dimensions or solver difficulty.

```sh
python examples/synthetic_configured_fleet.py --case fleet --output fleet.json
python tools/benchmark_configurations.py --inputs fleet.json --formulation reference --solver highs --threads 4 --time-limit 120 --output bench-reference
python tools/benchmark_configurations.py --inputs fleet.json --formulation compact --solver highs --threads 4 --time-limit 120 --output bench-compact
```

Use `--solver cplex` when the CLI is installed (CPLEX production dispatch support
is not added by this benchmark). Each worker exports LP, MPS, inputs, full native
logs, solution values when available, and JSON metrics. A time-limited incumbent
is benchmark evidence only; it never bypasses EMST's production optimal-solution
checks or enters settlement.

Matched workers use fresh processes, the same inputs/solver version/thread count,
120-second MIP budgets and zero relative/absolute gap targets, with no MIP starts,
transferred basis or solution. CPLEX uses `advance 0`, deterministic parallel mode
and seed zero. HiGHS uses seed zero.
CPLEX repeated presolve is disabled (`repeatpresolve 0`) in both variants.
The same strict-tolerance reference LP received a contradictory optimality claim
with automatic repeated presolve: EUR13,303.72306 versus a HiGHS-certified and
feasibly lifted EUR12,480.29379. Disabling repeated presolve restored agreement.
Those automatic-presolve runs remain diagnostic evidence, excluded from primary
CPLEX comparisons. This is an observed environment-specific inconsistency,
not a claim about a generally established CPLEX defect.

Model construction is measured separately
from solver/model-file loading. Native solve time includes presolve/search.
The initial continuous relaxation is solved separately, with no solution/basis
reuse; its bound/time are distinguished from root-search/cut bounds.

Native logs supply presolved dimensions/presolve time. An interrupted presolve
has no completed presolved size, reported as unavailable rather than zero.
HiGHS callback timestamps give first-incumbent time. CPLEX CLI first-incumbent
arrival timestamps are approximate and include console buffering; native exact
callback timing is unavailable through this CLI. Full logs retain the underlying
root progress and elapsed-time information. Solver precision and missing metrics
are explicitly retained rather than fabricated.


### Installation and complete replay

The benchmark baseline requires HiGHS 1.16.0 (the library itself also supports
its existing CBC path). Install the repository and audit extra, then pin the
measured version if reproducing these timings:

```sh
python -m pip install ".[audit]" "highspy==1.16.0"
python examples/configured_plant.py --configuration-formulation compact --rtbm-interval-minutes 30 --output compact-day
python tools/run_configuration_benchmarks.py --output output/compact-replay --threads 4 --time-limit 120
python tools/validate_configuration_lifts.py --benchmark output/compact-replay --output output/compact-replay-lifts.json
python tools/validate_compact_day.py --output output/compact-day-validation
python tools/validate_compact_fleet_day.py --output output/compact-fleet-day-validation
python tools/report_configuration_benchmarks.py --benchmark output/compact-replay --output output/compact-replay-report.md
```

The benchmark matrix runs HiGHS and, if available, CPLEX sequentially. It uses
three short-case repetitions, two long-case repetitions, and one forced-long
and one fleet comparison per solver. Specify `--solvers highs` for a fully
open-source replay. Reference parity verification additionally requires a Git
clone containing commit aff3574.

Primary benchmarks explicitly set primal feasibility, dual/optimality and
integrality tolerances to **1e-9** in both solvers and variants. An initial
matched default-tolerance trial exposed raw CPLEX trickle generation of about
0.0001 MW in an OFF state and a 0.0002 constraint residual in the reference.
It failed the unchanged physical checker. Those runs are retained as diagnostic
evidence and are not the primary benchmark. Tightening numerical precision does
not alter physical constraints or gap targets; no dispatch is rounded into
apparent feasibility. Production retains its existing fixed-discrete LP solve
and optimum/physical validation. Native benchmark results are validated directly.

Post-benchmark point lifting fixes the same integer occupancy and common
physical-variable point in the other model. Its small 1e-6 variable neighborhood
and EUR0.001 objective band account for exported-solution floating-point precision;
all original physical constraints remain enforced, with no added physical slack.
This is a constrained LP feasibility/equality check, not a looser MIP gap or a
new global optimality certificate for a time-limited incumbent.

The additional forced fleet replay explicitly supplies legal configuration paths
and holds conventional units in dispatch in both variants. Three plants include
a 13-step cold start beginning at 06:00 and finishing after noon, an inherited
start, and immediate two-to-three-GT progression. Demand includes an explicit
synthetic offset and conventional initial powers are declared in its saved
`inputs.json`. Power, ramps, reserves and all other physical constraints remain
active; these forced paths define a separate validation scenario, not a change
to the unconstrained timing cases.

The forced replay passed with identical dispatch and matching ISP objectives in
both RTBM modes, no security slack, reconciled participant cashflows and exactly
EUR11,000 of executed transition costs. See
`output/compact-fleet-day-validation/verification.json` for the noon transition
and component-clock checks. The [complete benchmark report](compact-configurations-benchmarks.md)
records every repeated run, including time limits and numerical diagnostics.
