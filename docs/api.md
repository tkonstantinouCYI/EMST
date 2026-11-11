# Public Python library interface

## Readable results

`results.to_readable()` returns shared stage summaries. Use
`results.to_readable(audience="participant", participant="supplier_T")` for
those summaries plus that participant's private records, or `audience="operator"`
for all participant records. `results.save_readable("readable-results")` writes
`readable_results.json` (operator-private), `shared_results.json`, and individual
participant files under `private/`. The returned path mapping identifies each
participant file. Raw results and saved source files are left intact.

The hierarchy is simulation summary, daily summaries, market stages, then stage
summary and participant records. Stage names are `forward_market`,
`day_ahead_market`, `isp`, `rtbm`, and `settlement`. Shared fields are explicitly
selected aggregates and published-price-style results. Individual bids,
contract prices, assets and cash positions appear in the private views. A
participant's settlement view includes meters for assets for which it is the
registered balancing-responsible participant. Only the operator file contains
all private records. Monetary strings preserve ledger precision, and positive
cash denotes a receipt. Cash posting `reference_price` retains the raw ledger's
price input; `cash_eur` is the assessed payment, including special pricing rules.

ISP is organised as `isp.runs[]`, with a `run_id`, a shared `summary`, and
private `participants` inside each run. Shared exports omit `participants`.
Each summary MTU includes `energy_shadow_price_eur_mwh` from the fixed-commitment
LP, `indicative_upward_offer_price_eur_mwh` (highest scheduled upward offer),
and `indicative_downward_offer_price_eur_mwh` (lowest scheduled downward offer).
Directional indicators are null when no offer exceeds the 1e-7 MWh scheduling
tolerance in that direction. These indicators describe ISP scheduling rather
than RTBM settlement prices.

Each private settlement section contains `financial_position.intervals`, with
signed cash by MTU for forward, DAM, ISP, RTBM, other settlement, UA-1 and UA-2.
ISP includes reserve capacity and startup payments. RTBM includes instructed
balancing and emergency energy payments. `settlement_cash_eur` excludes UA-1 and
UA-2 so all seven columns add to `total_cash_eur`. These are stage attributions
of ledger postings, and each posting is counted once. `totals` sums the MTU
rows. Postings without an MTU are listed separately in `non_mtu_postings` and
included in `daily_total_cash_eur`. Positive cash denotes participant receipts.

The public entry point is `from emst import Market`. Participants and assets
can have arbitrary IDs. The public daily engine does not depend on the example's
`thermal_A`, `load_T` or `battery` identifiers.

Install from `EMST`:

```powershell
python -m pip install -e ".[audit,dataframes]"
python examples/library_day.py
python examples/library_day.py --dataframes
```

The base package requires PuLP and Windows timezone data. `audit` adds HiGHS;
`dataframes` adds pandas. Array input works without pandas. The built wheel was
installed to an isolated workspace target and its public API ran a complete day.
The workstation's existing environments were not modified.

## Separate rules, participants, data and strategies

```python
from emst import Market, ThermalState, StorageState, Strategy

market = Market(ruleset={
    "id": "my_cyprus_profile_v1",
    "storage": {
        "dam": {"enabled": True, "circular_blocks": True},
        "participation_balancing_energy": False,
        "participation_balancing_capacity": False,
    },
})
index = market.time_index("2026-11-05")

market.add_participant("producer", strategies={
    "thermal": Strategy("fixed_markup", {"markup_eur_mwh": 5}),
})
market.add_participant("supplier")
market.add_participant("storage_owner")

market.add_thermal(
    "plant", "producer", capacity_mw=200, minimum_mw=20,
    marginal_cost=65, ramp_mw_hour=1000,
    initial_state=ThermalState("online", power_mw=100,
                               time_in_stage_minutes=120),
)
market.add_supplier(
    "load_portfolio", "supplier",
    demand_forecast=[100.0] * 48,
    actual_consumption=[101.0] * 48,
)
market.add_storage(
    "battery", "storage_owner", power_mw=10, capacity_mwh=40,
    initial_state=StorageState(power_mw=0, soc_mwh=20),
    strategy="fixed_circular",
    strategy_parameters={
        "buy_mwh": [2 if t in (10, 11) else 0 for t in range(48)],
        "sell_mwh": [1.805 if t in (34, 35) else 0 for t in range(48)],
    },
)

# Supplied scenario history for the source-based zero-activation fallback.
# These values are fictional; replace them for a real-data study.
history = {t: [[80.0] * 6 for _ in range(3)] for t in range(48)}
results = market.run(time_index=index, historical_rtbm_prices=history)
results.save("output/my_day")
```

`Ruleset` accepts a predefined ID, a dictionary, or a JSON path. See
[`rulesets/cyprus_isolated_v1.json`](../rulesets/cyprus_isolated_v1.json).
It contains no time-series values. Unknown keys and unsupported enabled features
are rejected rather than silently ignored. Input configurations are copied.

Participant defaults select strategies per technology; an asset may override its
owner's default. `Market.available_strategies()` lists:

| Technology/role | Strategies |
|---|---|
| Thermal | `marginal_cost`, `fixed_markup` |
| RES | `forecast_offer` |
| Supplier | `forecast_demand`, `price_responsive` |
| Storage | `fixed_circular`, `forecast_arbitrage` |

The fixed storage strategy defaults to idle if no bid profile is provided.
`forecast_arbitrage` uses the market-level `price_forecast` passed to `run`; it
does not receive future cleared prices or actual demand/RES. Strategies generate
offers. Clearing determines acceptance; delivery uses accepted schedules and any
permitted RTBM activation. High buy bids may deliberately accept an energy-loss
cycle at flat prices; that is not a claim of profitable arbitrage.

## Inputs and units

| Asset/role | Required data |
|---|---|
| Thermal | Technical/cost parameters, previous-day stage, final power and elapsed minutes in stage. Availability is 100%; no forecast/actual generation series. |
| Storage | Power/energy/efficiency parameters, previous-day signed power and SOC. No storage forecast/actual series. Optional strategy parameters define its orders. |
| RES | Generation forecast and actual available generation (default), or explicit `actual_generation_kind="metered"` readings. Optional registered capacity, voltage and grant flag. |
| Supplier | Demand forecast and actual metered consumption; voltage and small-supplier status. |

Forecasts and actuals use **MW**. Forecasts have 48 half-hour values. Actuals may
have 48 half-hour or 288 five-minute values; half-hour values are held constant
within their six RTUs. Series and single-column DataFrames must have matching,
unique timezone-aware indices. Array input is aligned to the explicit `time_index`;
288-value actual arrays use its derived five-minute index. NaNs, negative physical
values, missing values and mismatched timestamps are rejected. No implicit
interpolation or filling is performed.

Storage `buy_mwh`/`sell_mwh` are **MWh per MTU**, not forecasts or MW. Positive
storage power is injection, negative power is withdrawal. `ThermalState` uses
`offline`, `sync`, `soak`, `dispatch` or `desync`. Earlier names `startup`, `online`
and `shutdown` remain aliases for `soak`, `dispatch` and `desync`, respectively.
Each thermal unit must have a distinct participant; a second thermal unit for the
same participant is rejected.

## Thermal phase formulation

For each thermal participant/MTU, the independent market quantities are upward
and downward energy (MWh), plus capacity (MW) by product/direction. Average power
is an expression, **not an independent decision variable**:

`power_mw = (MS_mwh + energy_up_mwh - energy_down_mwh) / 0.5`

There are four thermal state binaries per MTU: `sync`, `soak`, `dispatch`,
`desync`. At most one is active; all zero means offline. The ordered cycle is
`offline -> sync -> soak -> dispatch -> desync -> offline`. Continuous transition
flow auxiliaries link one-hot states; separate on/start/stop/AGC binaries are not
needed in the public engine. `on`, `start` and `stop` exports are derived. The
specialised fixture adapter retains auxiliary startup cost-class binaries for
its existing hot/warm/cold cost inputs; those are not additional operating states.

Example technical profile (terminal MW in consecutive half-hours):

```python
market.add_thermal(
    "unit", "thermal_owner", capacity_mw=60, minimum_mw=20,
    marginal_cost=10, ramp_mw_hour=600, startup_cost=300,
    initial_state=ThermalState("offline", 0, 600),
    phase_profiles_mw={
        "sync": [0],       # 30 minutes
        "soak": [10, 20],  # 60 minutes
        "desync": [10, 0], # 60 minutes
    },
)
```

Profile lengths define exact phase durations. Empty sync/soak/desync profiles
skip those phases; transitions cannot skip a declared nonempty phase. A desync
profile must end at zero, and startup profiles must reach minimum dispatch
output. A zero-duration shutdown still cannot instantaneously remove nonzero
power under the ramp constraints. The default provides a one-MTU desync to zero.
Legacy `startup_minutes`/`shutdown_minutes` inputs generate half-hour profiles;
they must be multiples of 30 minutes.

ISP dispatch is the average power for each half-hour:
`(MS_mwh + energy_up_mwh - energy_down_mwh) / hours`.
Ramping limits the difference between consecutive average dispatch powers to
the ramp rate times the MTU duration. The first MTU uses the supplied initial
boundary power. The noon-onward rerun uses the forecast boundary state formed
from information available at its 11:00 cutoff.

ISP has no thermal endpoint, midpoint or within-MTU trajectory variables.
Reserve headroom is applied to average dispatch power. Declared transition
profiles supply fixed MTU-average power in sync, soak and desync. These averages
come from successive technical profile points. Desync starts from minimum
dispatch power for this profile conversion. If a run starts within a transition,
its first average uses the supplied boundary power and the next profile point.

RTBM retains physical ten-segment trajectories within each five-minute interval.
Prescribed transition endpoints come from unit technical profiles, and actual
terminal power carries into the next RTBM interval.

Only `dispatch` can provide reserves, including RR where the specialised adapter
supports it. All non-dispatch phase reserve bounds are zero. Startup is charged
once on leaving offline, normally into sync (or the first nonempty startup phase).
Minimum up/down times and physical limits remain enforced. Product response rates
and reserve power/SOC headroom apply in ISP only.
Minimum up time counts the connected episode, including prescribed phases.

For an initial sync/soak/desync, supply its elapsed minutes and final previous-day
power. The phase must be unfinished and elapsed time aligned to the half-hour ISP
grid. Online/offline minimum-time residuals use the supplied history. Initial
desync assumes it was legally entered before the horizon. End states advance a
completed phase to its successor at the boundary; unfinished phases remain in
`ending_states`, ready to pass into the next day's `ThermalState` constructor.
There is no artificial end-of-day shutdown requirement.

`phase`, `phase_binaries`, `phase_elapsed_before_minutes` and
`thermal_state_binary_count` describe the ISP phases.
`ramp_reference_power_mw` records the initial boundary or preceding MTU average.
`nodes_mw` and `trajectory_step_hours` describe RTBM physical trajectories.
`python examples/thermal_phases_day.py` runs a complete phase-cycle day. Specialised
startup classes, emergency procurement and historical accounting remain adapter
features; shared phase/physical kernels do not certify official equivalence.

## TSO forecasts, observations and reserves

Participant forecasts create participant offers. The TSO can provide independent
system forecasts for ISP and RTBM:

```python
results = market.run(
    time_index=index,
    system_forecasts={"demand": tso_demand_mw, "res": tso_res_mw},
    reserve_requirements={
        "FCR": {"up": fcr_up_mw, "down": fcr_down_mw},
        "aFRR": {"up": afrr_up_mw, "down": afrr_down_mw},
        "mFRR": {"up": mfrr_up_mw, "down": mfrr_down_mw},
    },
    historical_rtbm_prices=history,
)
```

All six reserve arrays contain **MW per MTU**, with 48 values on a normal day.
Indexed Series/single-column DataFrames work too. Flat names such as `mFRR_up`
remain supported; duplicate definitions are rejected. Omitted directions/products
in the flat form explicitly default to zero. Nested products must contain both
`up` and `down`. These requirements are scenario inputs, never ruleset fields.

System demand and available-RES forecasts accept either 48 half-hour or 288 five-minute
values, or matching indexed Series/DataFrames. Five-minute forecasts are averaged
for ISP while retaining their shape for RTBM. System demand excludes controllable
storage charging, and system RES excludes storage discharge; storage is represented
as signed dispatch. If system forecasts are omitted, participant forecasts are
aggregated for compatibility and that source is identified in the manifest.

`system_observations={"demand": ..., "res": ...}` optionally supplies independent
system measurements (48 held values or 288 values). Otherwise measurements are
aggregated from actual supplier/RES series. RES observations used for the next
available-output forecast add back known DAM and balancing curtailment; this
prevents instructed curtailment from masquerading as a weather forecast error. The actuals still drive participant
settlement independently. Measurement of RTU t is available at its end, so it
first affects RTU t+1. The default assumes no additional telemetry delay.

RTBM applies, separately to demand and RES:

`prediction[t] = max(0, TSO_forecast[t] + observation[t-1] - TSO_forecast[t-1])`

The comparison uses the **uncorrected** previous TSO baseline, preventing recursive
error accumulation. Clipping, baseline, correction and measurement index are
exported. At midnight, the baseline is used unless `previous_system_observation`
is supplied with `demand_mw`, `res_mw`, `forecast_demand_mw`, `forecast_res_mw`
for the preceding five-minute interval. In this seed, `res_mw` is the available-RES
estimate after adding back known control. At 11:00, the bridge to noon freezes the
latest available error; it never consumes future measurements.

`run` also accepts `price_forecast` and `historical_rtbm_prices`. The history shape
is `{mtu: [day1_six_RTU_prices, day2_six_RTU_prices, day3_six_RTU_prices]}`. It is
required for MTUs without price-forming activation; missing history raises an
error instead of inventing a price. RES actual-generation semantics are explicit: available output is curtailed by
accepted instructions, while `metered` input is never curtailed a second time.

Forward contracts use participant IDs and explicit entity allocations through
`Forward`/`add_forward`. The current public forward adapter covers thermal/RES
physical supply to supplier portfolios. Historical RR contract awards and the
specialised MM/MS accounting adapters remain in the existing runner.

## ISP offer objective

Both ISP entry points minimise the normal-market offer objective plus the
configured slack penalties:

`sum(energy_up_mwh * price_up - energy_down_mwh * price_down)`
`+ sum(reserve_mw[product, direction] * reserve_offer[product, direction])`
`+ sum(startup_cost)`

Energy is deviation from the accepted commercial schedule, in MWh. Energy offers
are EUR/MWh. Reserve offers are EUR/MW/MTU, so there is no extra half-hour factor
on the MW-times-price term. Startup costs are EUR per start (including declared
startup classes in the specialised adapter). No ISP schedule-deviation penalty,
no-load cost, storage degradation cost or RES-curtailment cost is added. RTBM's
separate objective and its existing configuration are unchanged.

For thermals, `add_thermal` accepts `energy_up_price` and `energy_down_price`.
If omitted, both equal the strategy-generated energy offer. For storage, the
strategy's `balancing_up_price` and `balancing_down_price` are used. Prices must
be finite, with upward price at least downward price to avoid an unbounded pair
of simultaneous up/down energy variables.

Both `add_thermal` and `add_storage` accept reserve price overrides, for example:

```python
reserve_prices={
    "FCR": {"up": 4.0, "down": 3.0},
    "aFRR": {"up": 5.0, "down": 4.0},
    "mFRR": {"up": 6.0, "down": 5.0},
}
```

Flat names such as `aFRR_up` are also accepted. The scalar `reserve_cost` fills
products without overrides. Capacity offers are finite nonnegative prices and
are distinct from the TSO's requirement time series and LP clearing prices.
ISP results export `objective_components_eur` with upward cost, downward credit,
reserve cost, startup cost and the reconciled total. Per-asset rows export the
energy offers and directional capacity offers used in optimisation.

Explicit emergency-contract energy in the specialised runner is included in
upward energy cost. Slack penalties are additional objective terms and are
reported separately; they are never automatically posted as participant cash.

## Penalised energy and reserve slack

The default ruleset enables energy diagnostic slacks in ISP and RTBM, and
reserve-shortage slacks in ISP only:

```python
ruleset = {
    "slack": {
        "enabled": True,
        "energy_shortage_eur_mwh": 10000.0,
        "energy_surplus_eur_mwh": 10000.0,
        "reserve_shortage_eur_mw_mtu": {
            "FCR_up": 10000.0, "FCR_down": 10000.0,
            "aFRR_up": 10000.0, "aFRR_down": 10000.0,
            "mFRR_up": 10000.0, "mFRR_down": 10000.0,
            "RR_up": 10000.0,
        },
    },
}
```

These are **synthetic model penalties**, not asserted official price caps.
Energy balance includes shortage MWh minus surplus MWh. Reserve constraints
remain `awarded MW + shortage MW >= required MW`; an inequality needs no reserve
surplus slack. Energy penalties are per MWh. Capacity penalties are per MW/MTU
and apply to the half-hour ISP. RTBM has no capacity products or capacity slack. Set `enabled=False` for hard
energy/reserve requirements. Slacks do not relax technical/SOC/phase constraints.

Per-interval `slack` and per-run `slack_by_period` export every quantity.
`objective_components_eur` adds the three penalty-cost components. Nonzero slack
sets `physical_security_feasible=False` and `solution_class="relaxed_with_slack"`.
A passing audit then verifies the relaxed equations, not physical adequacy.
The manifest reports RTBM energy shortage/surplus MWh and maximum ISP reserve
shortage MW by product and run. Planning runs are reported separately to avoid double-counting
initial/rerun ISP shortfalls as actual delivered-day energy.

Slack is never a participant, generation meter, reserve award or payment.
Supplied actual meters remain supplied data; a relaxed day is diagnostic and
must not be presented as a physically served market day. Energy LP marginals
reflect the penalty when energy slack binds. If an MTU has slack but no
price-forming activation, the supplied historical fallback is replaced by the
slack-volume-weighted interval price; the source is exported. Existing market
activation weighting otherwise remains in use.

## RES downward offers and settlement

RES now has downward energy offers and zero upward/capacity offer bounds:

```python
market.add_res(
    "solar", "solar_owner", generation_forecast=solar_forecast,
    actual_generation=solar_available_actual, capacity_mw=100,
    energy_down_price=-5.0,
    actual_generation_kind="available",  # default
)
```

With the objective `-energy_down * price_down`, a price of -5 means a 5 EUR/MWh
curtailment cost/compensation; positive downward prices are credits to the model.
Thermals provide downward offers subject to their constraints. Storage provides
energy offers only when its energy flag is enabled. Suppliers remain forecast
portfolios, without demand-response offers in this release.

TSO available-RES forecasts are allocated using participant forecast shares
(capacity/equal shares if all forecast shares are zero). The dispatch reference
is limited by both forecast availability and the accepted commercial schedule.
Only accepted reductions from that reference are balancing instructions.
Unaccepted DAM generation is separate commercial curtailment, with no balancing
payment. Forecast/weather shortfalls are not manufactured into accepted offers.

`RES_instructions` exports awarded and delivered downward MWh and any delivery
shortfall. These offers are price forming and contribute to market activation
weighting; they are separate from external PA priority-control instructions.
RES capacity remains unsupported even if requested in the ruleset.

For `available` actuals, output is limited by the DAM schedule and then by the
accepted downward instruction, capped by physically available energy. Metered
output, DAM curtailment and delivered balancing curtailment conserve available
energy. In `metered` mode, the input is authoritative already-curtailed output;
no second subtraction is applied, and delivery of the instruction is assumed
because an independent available-output meter was not supplied.

RES instructed settlement quantity is the negative delivered balancing reduction.
The remaining meter-minus-commercial-schedule difference is uninstructed energy.
This keeps weather deviations separate. Existing grant-RES payment exclusions
remain in force. External PA control remains available in the specialised
high-RES acceptance fixture; it is explicitly labelled, non-price-forming and
never relabelled as an ordinary RES market offer.

`python examples/slack_and_res_day.py` runs a market-curtailment day, a DAM-only
curtailment case, and a deliberately short system reported as relaxed.

## Balancing eligibility and actual delivery

Storage energy and capacity flags are independent and are enforced inside the
optimisation. Energy disabled fixes storage to its accepted commercial schedule.
Capacity disabled fixes every storage reserve award to zero and yields no reserve
payments. Energy enabled permits feasible upward/downward redispatch, including
reduced charging. Capacity enabled permits reserve awards, constrained by power
headroom and energy sustain requirements. A permission does not guarantee an
economic award. There is no outside-MMS FCR/FFR payment in the default profile.

RTBM is energy-only. It reoptimises the remaining five-minute intervals of the
current MTU and executes the first interval. Thermal states and prescribed
transitional trajectories come from ISP, but reserve awards are not held back
from activation. There are **no capacity decision variables, reserve requirements,
capacity prices/costs, reserve shortage slacks, or reserve-sustain SOC buffers**
in RTBM. Energy shortage/surplus slack remains available.

Thermals can use their full feasible operating range; storage can use its full
power and available SOC within its energy-participation permission. Actual ramp,
capacity, efficiency and SOC limits remain hard. RTBM no longer enforces the ISP
MTU-end storage SOC target, which previously protected future reserve inventory.
Updated actual storage states feed subsequent intervals and the 11:00/noon ISP
handover. This lookahead uses only forecasts, never future actual readings.

Reserve requirements, awards, reserve headroom, sustain requirements, LP capacity
prices and capacity settlement remain in ISP. RTBM exports `capacity_products=[]`
and empty reserve/requirement/price dictionaries; it does not create zero-bounded
capacity variables. Maximum reserve-shortage diagnostics now live under
`ISP_max_reserve_shortage_mw_by_run`, rather than an RTBM capacity field.

Thermal and storage actual energy equals the RTBM instruction. Only completed
system measurements feed the next forecast correction. Current/future actuals
never enter current bidding or dispatch. Residual physical imbalance is exported;
there is no undisclosed AGC adjustment that forces actual balance to zero.

The initial ISP is followed by chronological RTBM. At 11:00, existing awards and
cutoff-known forecasts project state to noon; the second ISP covers noon to
midnight. Existing awards remain active through 11:55. There is one forecast
vintage, with no revisions. End-of-day states use the same fields as the input
state constructors, for chaining days. Capacity settlement uses fixed-discrete
LP shadow prices. `price_ranges=True` adds optional ISP ranges without changing
the settlement price vector.

## Results and accounting boundary

`MarketResults` exposes `inputs`, `orders`, `dam`, `isp_runs`, `rtbm`, `meters`,
`ledger`, `participant_results`, `ending_states`, `manifest` and `audit`.
`save` exports JSON; `to_dataframes()` returns pandas tables. Inputs, strategies,
ruleset versions, parameter identifiers, physical approximations and code hashes
are retained. Forecast and actual arrays are separate.

The public daily ledger covers forward/DAM cash, instructed/BRP deviations,
reserve capacity, instructed startup payments, network tariffs and daily UA-1/UA-2
allocations. Zero-net activation uses offer-specific cash; period pricing uses the
existing net-activation weighting and supplied historical fallback. Default
tariffs are explicitly fictional; `settlement_parameters` replaces the dated
parameter bundle. Private and central cash are reported separately.

Use `PeriodResults.settle_month()` to close a complete simulated month and
reconcile corrected meters. Both paths share monthly account closure and Annex
reconciliation. The specialised runner also covers cumulative settlement,
MM/MS contracts, dispatch compliance, risk minima and invoice clearing.
`Market.run()` remains a daily calculation.

Interconnections and intraday auctions remain disabled and cannot yet be enabled.
The single-zone public API supports complete normal days; verified official DST
tables are not yet connected to it. All current results remain synthetic and
`current_rules_conformance_verified=False`.

### RES settlement tolerance

`settlement.res_tolerance` defines `commercial` and `grant_supported` fractions, both zero by default. Both daily settlement paths use the same instruction/meter bridge. For MS=10 MWh, downward activation=5 MWh and meter=3 MWh, zero tolerance gives INSTQ=-5 MWh and UNINSTQ=-2 MWh. A synthetic commercial tolerance of 0.02 instead gives -5.1 and -1.9 MWh. The tolerance applies to instructed generation (5 MWh), not the market schedule or activation volume. Defaults are scenario policy, not asserted approved regulatory values. Grant remuneration exclusions remain separate.

### Multi-day execution

`Market.run_period(start=..., end=..., scenario=..., checkpoint_directory=...,
resume_from=None)` runs consecutive daily horizons with automatic physical-state,
observation and settlement-price-history carryover. It writes verified daily
results and atomic checkpoints. See [multi-day execution](multi-day.md)
for input structure, resume examples and accounting/calendar limitations.
