# Planning and investment integration

EMST 0.3.2 exposes operational and accounting inputs for a planning adapter.
It does not perform investment optimization, annualization, financial recovery
assessment, physical frequency-control recourse, or reliability assessment.
Passing software audits is not official-rule validation or market calibration.

## Solver controls, records and failures

```python
from emst import Market

market = Market(solver_config={
    "backend": "auto",  # highs if installed, otherwise bundled CBC
    "time_limit_seconds": 90,
    "relative_gap": 0,
    "absolute_gap": 0,
    "threads": 1,
    "logging": False,
    "stages": {"ISP": {"time_limit_seconds": 120}},
})
```

The dictionary is copied. Supported stage names are `DAM`, `ISP`, `RTBM`,
`RTBM_projection`, `must_run`, `storage_strategy`, `diagnostic`, and `unspecified`.
The noon-state projection inherits RTBM options unless explicitly overridden.
`diagnostic` controls optional dual-face ranges. The budgets are per subproblem,
not total stage/day budgets. Global options apply unless that stage overrides
them. Unknown options/stages are rejected. Arbitrary backend tolerances, random
seeds, callbacks and purpose-specific budget overrides are not exposed.

Defaults retain zero MIP gap and optimal-only acceptance. Time-limited incumbents
are **not accepted**, even when available. Setting a positive MIP gap explicitly
changes the solver's optimality criterion: optimal termination within that
configured gap may be accepted and priced through its fixed-discrete LP. This
does not permit a timeout incumbent. Use zero gaps for final comparison.

`results.diagnostics.solve_records` includes stage, subproblem purpose, model,
backend/version, effective settings, status, objective, incumbent availability,
bound/gap where the backend exposes them, termination reason, and wall seconds.
Dispatch records identify the horizon start and interval count. LP pricing is
labelled `fixed_discrete_pricing`; smoothing is `trajectory_smoothing`. Bounds
include objective constants; gap is the backend-reported gap. CBC does not expose
all HiGHS-native status/bound details through this interface; unavailable values
are null. Native dual-face records contain the applicable diagnostic subset.
`solver_wall_seconds_by_stage` sums solver calls, excluding Python orchestration
and exports; it is not total simulation runtime. RTBM projections are separate
from executed dispatch. Monthly procurement records are referenced in daily
diagnostics with `count_once_per_auction_id=True`; do not sum the same monthly
auction again for each day.

RTBM smoothing uses a primary-objective tolerance of
`max(1e-5, abs(primary_objective_eur) * 1e-9)` EUR. It validates the original
constraints/bounds to an absolute `1e-5` and objective preservation separately.
On optional smoothing failure or validation failure it restores and validates the
saved primary solution. The executed row records `succeeded`, `fallback_primary`
or `not_required`, its tolerance/deviation and any failure. A failed validation
of the primary solution remains fatal. Prices are captured from the primary
fixed-discrete pricing LP before smoothing and are never smoothing duals.

```python
evaluation = market.evaluate(**daily_inputs)
if evaluation["status"] == "failed":
    print(evaluation["failure"])
else:
    print(evaluation["diagnostics"]["actual_balance"])
```

`evaluate` returns a JSON-safe schema-1.0 contract. Failure kinds distinguish
invalid input, unsupported features, optimization infeasibility/unboundedness,
solver limits/failure, and other model/numerical failures. `run` continues to raise
exceptions; `SolveError` (a `RuntimeError`) carries a `.record`. An error is never
converted to a fictitious high-cost/infeasible investment observation. Optional
postprocessing failure with a validated fallback is a successful evaluation with
diagnostics. `results.to_evaluation()` exports the same successful contract from
an existing run.

## Forecast feasibility, actual balance and reliability

The existing `physical_security_feasible` field is retained for compatibility.
Its documented meaning is **forecast clearing without optimization slack**; it
does not assert balance against actual observations or network/dynamic security.
The manifest now states this meaning explicitly. Separate diagnostics identify
primary optimization feasibility, audited forecast balance and actual balance.

For executed five-minute intervals the residual is observed demand minus actual
delivered net injection, in MWh. Positive means demand exceeds injection;
negative means excess injection. `diagnostics.actual_balance` exports positive
and negative energy separately, net energy, directional maximum MW, maximum
absolute MWh, and counts above `actual_balance_tolerance_mwh` (default `1e-6` per
RTU). This diagnostic tolerance does not alter financial settlement or RES
tolerance rules. Equality at the tolerance is accepted. Interval residuals and timestamps
remain in `rtbm`; MW is MWh divided by interval hours. ISP plans and the noon
projection are not added to realized residuals.

Actual measurements are observed after each dispatch interval, and affect the
next interval through the existing causal error update. No automatic physical
recourse closes the current interval's mismatch. Thus `EENS_mwh` and `LOLE_hours`
are null, `reliability_interpretation_available=False`, and load-loss reliability
is `unassessed`. Residuals and optimization slack must not be called load shedding.
Forecast bias can pass the operational audit while failing actual balance. A
separate validated recourse model is required before imposing reliability
constraints in an investment optimizer.

## Asset cash attribution and realized operating costs

`results.economics` (also saved as `economics.json`) provides:

- Each asset's directly attributable net cash, split by settlement participant.
- Realized operating-cost rows for executed RTBM intervals and daily totals.
- Participant reconciliation: attributed asset cash plus unallocated participant
  cash equals the participant's complete net ledger cash.
- Economic-owner grouping and total participant cash, without altering settlement.

Attribution uses explicit public-engine posting identities for DAM, forward
seller/buyer allocations, instructed/uninstructed energy, reserve/startup income,
network charges tied to a specific metered asset, and must-run asset service and
inherent energy. It does **not** divide participant-level UA1/UA2, must-run supplier
recovery or other shared postings among assets. Those remain unallocated.
Attributing a BRP's imbalance posting to the causal asset does not transfer that
cash to its legal owner: inspect `attributed_cash_by_settlement_participant_eur`
and specify any contractual sharing in the adapter.

The physical cost convention is explicit:

| Cost | Executed-data calculation |
|---|---|
| Thermal variable | Delivered MWh × asset `marginal_cost` (not strategy markup) |
| Startup | Executed startup events × `startup_cost` |
| No load | Online hours, including transition phases, × `no_load_cost_per_hour` |
| Storage degradation | (Grid-side charge MWh + discharge MWh) × `degradation_cost` |

These are declared model costs, not independently estimated fuel costs. Shutdown
cost, capital, fixed O&M and unserved-energy costs are not modeled. RES/supplier
physical costs have no configurable cost model and are zero in this export.

ISP still uses the declared energy/reserve offer objective plus startup cost;
it does not add no-load or storage degradation. RTBM includes thermal no-load
and storage degradation in its objective; `forecast_arbitrage` also uses storage
degradation in profile construction. Cost accounting reconstructs realized costs
once, separately from bids and settlement transfers. An objective term is not an
additional ledger payment. Do not subtract degradation twice or add market
transfers to physical system costs.

`contribution_before_shared_charges_eur` is attributable cash minus the declared
physical costs. It is explicitly **not final profit or investment recovery**.
The adapter must resolve shared charges, settlement-party ownership, capital,
fixed O&M, weights and inventory value. Supplier procurement outflow already
includes any included UA1/UA2 postings; do not add them a second time. Public
settlement includes network-use charges and UA1/UA2, excludes UA3–UA7, and does
not claim a complete annual invoice. Reserve prices are EUR/MW/MTU, not
EUR/MW-hour.

Readable private settlement reports include own-asset economics and the
participant cash reconciliation. Shared reports contain aggregate balance
diagnostics, not private costs, ownership groups, bids or asset revenues.

## Ownership and policies

```python
market.add_participant("unit_1_registration", economic_owner="incumbent")
market.add_participant("unit_2_registration", economic_owner="incumbent")
market.add_participant("battery_registration", economic_owner="incumbent")
```

Register each thermal unit under its separate participant as before. Asset ID,
participant, optional BRP and economic owner remain distinct. If no owner is
given, the participant ID is its owner. Merely regrouping owners changes reporting
and provenance, not bids/clearing or legal settlement accounts. It does not grant
portfolio coordination or new information to strategies. Asset economics remain
separate, so an incumbent's existing generation cash is not automatically counted
toward a new battery's recovery.

Built-in strategy names **and parameters** are now strictly validated. Unknown
keys and nonfinite scalar values fail; Series/profile shape and physical checks
remain at the run gate. A `Strategy` instance is copied, and separate parameters
cannot be supplied alongside it. This deliberately replaces silent ignored
settings. No custom policy registry, arbitrary stage-offer API or price-maker
storage policy is included in this milestone. A future offer/context specification
must preserve gate information and eligibility checks; do not monkey-patch engines
for a production investment study.

## Independent days, terminal inventory and chronological operation

For each independent candidate/day create a fresh `Market` (or independent copy),
declare thermal phase, elapsed history and previous power; storage power and SOC;
participant/TSO forecasts; actuals; reserve requirements; and the historical-price
bootstrap. The previous system observation is optional and must be explicitly
declared if supplied. Use identical scenario information across paired candidates.
Current public forecasts have no intra-day revisions/vintages API.

Three different inventory conditions must not be confused:

1. `fixed_circular` validates its proposed profile but does not impose a cyclic
   realized endpoint by name.
2. `forecast_arbitrage` constrains its proposed DAM profile to its strategy target
   (initial SOC by default). Acceptance and subsequent balancing can change actual
   end-of-day SOC.
3. The optional daily input `storage_terminal_soc_mwh={"battery": 20.0}` explicitly
   constrains each ISP's terminal SOC and realized RTBM terminal SOC. Rolling
   reachable-energy bounds preserve the target as the day ends. Ramp/commitment
   constraints remain enforced; an infeasible combination fails, it is not clipped.

The third option defaults to None and changes the physical dispatch policy when
selected. It is separate from DAM profile construction and does not automatically
force an accepted DAM package to be cyclic. Targets can cover a subset of storage
assets and must lie within registered energy capacity. Terminal economic values
are not implemented. Without an explicit endpoint, the adapter must account for
inventory changes before comparing representative-day revenues.

`run_period` carries actual state and history chronologically and checkpoints it;
it does not concatenate independent medoids or automatically annualize weighted
days. Supply terminal targets per day in the scenario if desired. Monthly must-run
procurement uses the full month's planning requirements/forecasts or supplied
reference supplier shares; a daily auction is an explicit research variant.
Representative-day studies must declare an approximation or retain an external
monthly procurement schedule; do not pretend weighted medoids are a closed month.
Only a complete consecutive month can use public monthly closure. Daily cash is
accrual; month-only adjustments remain separate. See [multi-day](multi-day.md).

## Repeated evaluation and provenance

Raw results add `diagnostics` and `economics`; raw/readable schema version is 1.1,
while the compact evaluation contract starts at 1.0. Existing raw fields remain.
The manifest includes input/policy/implementation fingerprints and scope flags.
The implementation fingerprint hashes installed Python source, so it also works
outside a Git checkout. Inputs include capacities, owners, strategies, state,
series, solver settings, forwards and endpoint/tolerance choices. Future custom
policy code would need an additional code/version fingerprint.

Use a separate Market instance and output/checkpoint directory per candidate,
preferably in separate processes for parallel solver execution. Never resume a
different candidate or solver configuration; configuration/code fingerprints
reject that. Timings vary between runs and are not part of numerical repeatability;
compare physical/economic results and solve quality within declared tolerances.
Caches must include input, implementation and policy fingerprints plus the chosen
adapter/accounting/scenario methodology and dependency versions. Exports are not
a cryptographic authentication boundary.

## Validation and limits

After installing, run from the repository root:

```sh
python tools/run_tests.py
python examples/planning_pilot.py --output planning-results
python examples/library_month.py --output month-results
```

The pilot evaluates no battery, 10 MW/40 MWh and 20 MW/80 MWh on paired inputs,
then a biased forecast and a repeated baseline to detect state contamination.
It groups two thermals and storage under an economic owner, imposes explicit
realized cyclic storage inventory where present, and exports raw/readable results,
compact evaluations and `verification.json`. These are interface checks, not
controlled speed benchmarks, calibrated prices or investment recommendations.

The launcher establishes repository imports without a PYTHONPATH workaround and
reports collection errors separately. CI exercises it on Windows/Linux,
Python 3.11/3.14 and HiGHS 1.15.1/1.16.0. The storage regression is unchanged;
additional tests force a smoothing fallback and check price preservation,
terminal inventory, cash/cost accounting, ownership neutrality and diagnostics.

DST, interconnections, intraday auctions, actual-delivery recourse, custom
stage-aware policies and official-rule calibration remain separate workstreams.
The current normal-48-MTU-day API must not be advertised as an unrestricted
chronological calendar-year engine.
