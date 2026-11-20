# Hourly initial ISP with one three-configuration CC plant

This is an experimental standalone initial-ISP benchmark, not a new hourly
`Market.run` API or a complete implementation of the balancing manual. The
default reference formulation is unchanged. No GitHub publication is implied.

Run from the repository root with the package and HiGHS installed:

```console
python tools/benchmark_hourly_virtual_cc.py
```

The script runs three serial pairs of the no-CC and one-CC cases, alternating
their order. It saves expanded real-hour inputs, exported MILPs, solver logs,
24-row hourly dispatch files and timings under `output/handoff-isp/`.
There is no half-hour reconstruction or second commitment MILP. The scope
excludes fixed-discrete pricing, the noon rerun, RTBM and settlement.

## Synthetic plant and formulation

`examples/synthetic_three_configuration_isp.py` declares one physical plant:

| Operating configuration | Minimum MW | Maximum MW | Marginal EUR/MWh |
|---|---:|---:|---:|
| GT | 20 | 75 | 140 |
| GT_ST | 40 | 110 | 110 |
| TWO_GT_ST (2GT+ST) | 80 | 220 | 105 |

The plant starts in TWO_GT_ST at 220 MW with 24-hour component histories. It has
six directed operating-configuration paths and two additional paths, OFF-to-GT
and GT-to-OFF. All transitions are hot; there are no thermal-type selections.
Adding components takes 13 half-hours in the source assumptions, rounded half-up
to seven real hours. Removing components takes one real hour after rounding.
OFF-to-GT takes one hour. Component minimum up/down times are six hours. All
these numbers are synthetic assumptions, not verified Vasilikos characteristics.

The lead and additional GT slots are canonical physical-history bookkeeping.
There are no alternative GT1/GT2 operating configurations, but this nested
representation does not model arbitrary swapping of identical GT histories.

`emst/configurations_virtual.py` reuses exact transition-start conservation and
continuous component-event hulls from the compact formulation. It replaces the
stable-state binaries with continuous occupancies linked to explicit binary
phase flags. For each operating configuration:

- Sync is zero in this hot-profile prototype; no separate zero-output sync phase
  is declared.
- Soak is active during a physical OFF-to-configuration startup profile.
- Desync is active during a physical configuration-to-OFF shutdown profile.
- Dispatch is active during stable operation and transitions into the
  configuration from another operating configuration.
- One balancing selector is active per plant, and physical commitment is allowed
  only for the selected configuration. When physically offline the cheapest
  configuration is selected for accounting.

The conservation equations, uninterrupted trajectories, actual entry-power
linkage, ramps, component histories and cost kernel are shared with the existing
configured-plant model. The balancing selector is not a second source of power
or market schedule. This experiment retains the existing plant-level simple
energy offers. The original timing evidence below used zero reserve capability
during transitions. Optional transition capacity is now supported through
explicit eligibility and envelopes; see [configured plants](configured-plants.md#reserve-capacity-during-transitions)
and `python tools/benchmark_cc_transition_reserves.py` for a separate paired
comparison. Full manual coverage, including configuration-specific offer books,
is outside this experiment.

The constructed CC binary count per interval is:

| Group | Binaries |
|---|---:|
| Four phase flags for three operating configurations | 12 |
| Balancing selectors | 3 |
| Six internal and two OFF transition starts | 8 |
| Total | 23 |

Across 24 hours this is **552 CC binaries**, before presolve. Component event
indicators and transition-step occupancy are continuous/derived. The stable
occupancies are integral because operating occupancy equals a binary dispatch
flag minus the sum of integer transition occupancies, and OFF equals one minus
the committed phase indicators. Existing mutual exclusivity bounds these
occupancies to [0,1]. The explicit phase/selection flags are intentionally
retained to evaluate the requested representation; this is not a claim that
23 is the minimum number of binaries. The existing compact representation for
this same graph uses 12 binaries per interval, including OFF and transition
starts, with phase/accounting flags omitted or derivable.

The hourly benchmark uses an explicit time/energy unit transformation documented
in `tools/hourly_isp_suggestions.py`. Real MW limits are unchanged, real-hour
commercial MWh sum half-hour pairs, and rates, energy costs, elapsed histories
and reserve response times are transformed consistently. Actual timestamps and
energy quantities in saved hourly dispatch are restored to real-hour units.

## Measured comparison

Evidence directory: `output/handoff-isp/hourly-one-virtual-cc-20261120-140232/`.
Both cases use the same 20 ordinary thermal units, demand/RES and reserve
requirements. The additional plant has a zero commercial schedule; existing
ordinary schedules are unchanged. Both systems still need energy-shortage slack.
Adding a plant changes the feasible/economic problem, so this is an incremental
scenario timing comparison, not an equal-physics formulation speedup claim.

HiGHS 1.16.0, four threads, presolve choose, seed zero, 120-second solver limit,
zero absolute/relative optimality targets, no warm starts, three runs per case:

| Metric | No CC | One virtual CC |
|---|---:|---:|
| Variables before presolve | 8,808 | 10,560 |
| Binaries before presolve | 1,920 | 2,472 |
| Constraints before presolve | 21,417 | 27,753 |
| Median construction seconds | 0.290 | 0.383 |
| Median MILP solve seconds | 5.642 | 11.552 |
| Solve range seconds | 5.488-5.667 | 10.931-25.950 |
| Median preparation/build/export/load/solve seconds | 6.185 | 12.303 |
| Energy shortage MWh | 3,182.933 | 1,052.567 |

All six solves reached optimality and passed native and original-model primal
and integrality checks. The third CC run had the same objective, 19 search nodes
and 22,290 LP iterations as the other CC runs but a longer wall time. Report the
observed range rather than treating the median as a guaranteed runtime. Totals
exclude post-solve validation, reporting and interpreter startup.

The unconstrained optimum stayed in TWO_GT_ST all day. This does not demonstrate
transition-heavy solve performance. A separate forced-transition run dropped to
GT_ST at hour index 1 and started the return transition at index 8. It exercised
eight transition hours, solved optimally in 2.341 seconds (2.951 seconds including
preparation/build/export/load), and charged EUR 2,500 exactly once. Its objective
and shortage MWh matched the existing compact formulation on identical forced
inputs. This faster forced run is not evidence that transitions improve runtime:
fixing decisions reduces the solver's search space.

## Validation

```console
python -m unittest tests.test_virtual_configurations -v
python -m unittest tests.test_compact_configurations -v
python -m unittest discover -s tests -p test_configured_plant.py -v
```

New tests cover the 552-binary count, forced feasible/infeasible paths against
reference and compact formulations, uninterrupted and back-to-back transitions,
inherited unfinished transitions, horizon truncation, component minimum-up
history, and a physical-dispatch objective comparison. The separate existing
configured-plant suite checks regression behaviour of the reference and compact
defaults; it is not evidence of hourly virtual-formulation RTBM integration.

## Optional transition-capacity comparison

Run `python tools/benchmark_cc_transition_reserves.py` to reproduce a separate
paired comparison of the same one-CC day with transition capacity disabled and
enabled. Every internal hot transition is explicitly eligible for the seven
supported product/direction names in this synthetic example. OFF paths remain
ineligible. Each transition declares constant reserve-envelope bounds equal to
the smaller source/destination minimum and larger source/destination maximum.
These are synthetic technical-eligibility assumptions, not operating data.

Evidence: `output/handoff-isp/cc-transition-reserves-20261120-142332/`.
The solver settings are the same as above, with three serial runs per natural
case, alternating order, and one separate forced run per eligibility setting.

| Metric | Disabled | Enabled |
|---|---:|---:|
| Variables | 10,560 | 10,560 |
| Binaries | 2,472 | 2,472 |
| CC binaries | 552 | 552 |
| Constraints, unforced | 27,753 | 28,017 |
| Median construction seconds | 0.281 | 0.508 |
| Median MILP solve seconds | 8.460 | 10.483 |
| Median total seconds | 9.053 | 11.356 |

All eight solves reached optimality and passed original-model primal and
integrality checks. The final source reproduced both benchmark MPS files
byte-for-byte after final validation. The natural optimum remains in TWO_GT_ST
all day, so the objective and delivered energy are unchanged. The added
constraints still changed branch-and-bound search (19 versus 97 nodes).

The forced day exercises eight transition hours. Its solve time was 2.571
seconds without transition capacity and 7.099 seconds with it (single runs,
not medians). The enabled result awarded positive FCR up/down, aFRR up and mFRR
up capacity during transitions; disabled awards were all zero. Its incremental
transition cost remains EUR 2,500, charged once. Energy shortage changes from
1,064.460 to 1,063.067 MWh and the objective from EUR 13,223,894.192 to
EUR 13,200,291.493. Reserve-shortage totals are unchanged; this case's benefit
comes from redispatch of other units rather than eliminating those shortages.
Zero reserve bid prices and lower-bound procurement constraints can produce
excess capacity awards at alternative optima; these are capacity awards, not
activated energy. The shortage slacks remain a material limitation of this
reduced-fleet scenario.

Validation: 21 targeted tests passed across transition-capacity, virtual,
compact-equivalence and default-ineligibility checks. Tests include individual
eligibility, shared multi-product headroom, entry and terminal bounds, scheduled
ramp consumption, slower stable-state response rates, inherited transitions,
input validation and unchanged variable/binary counts. No full daily RTBM or
settlement run is claimed for this hourly experiment.
