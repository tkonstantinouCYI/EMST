# Multi-day execution

`Market.run_period` executes consecutive, inclusive trading dates with the
existing daily horizon. It does not use next-day lookahead or finalise monthly
accounts. Every completed day includes daily settlement, uplift allocation,
audits and the same outputs as `Market.run`.

```python
scenario = {
    '2026-11-05': {
        'forecasts': {'load': demand_day1, 'solar': solar_forecast_day1},
        'actuals': {'load': actual_demand_day1, 'solar': actual_solar_day1},
        'system_forecasts': tso_day1,
        'reserve_requirements': reserves_day1,
        'historical_rtbm_prices': historical_prices,
    },
    '2026-11-06': {
        'forecasts': {'load': demand_day2, 'solar': solar_forecast_day2},
        'actuals': {'load': actual_demand_day2, 'solar': actual_solar_day2},
        'system_forecasts': tso_day2,
        'reserve_requirements': reserves_day2,
    },
}
results = market.run_period(
    start='2026-11-05', end='2026-11-06', scenario=scenario,
    checkpoint_directory='study',
)
day2 = results.days['2026-11-06']
```

Each day must explicitly provide forecasts and actuals for every RES/supplier
asset, using the same arrays or indexed-series conventions as the daily API.
Thermal/storage need no generation forecasts. Other supported daily keys are
`forwards`, `price_forecast`, `price_ranges`, `system_observations` and the
first day's optional `previous_system_observation`. Omitted forwards use the
market's configured daily contracts; supply a daily list (including an empty
list) to vary them. Other omitted run inputs retain the daily engine defaults.
Original market/scenario objects are not modified.

The runner automatically carries actual ending thermal phase, phase duration,
power, and storage power/SOC. Existing phase logic applies remaining minimum
up/down obligations from this state. It also carries the last completed system
observation and its uncorrected forecast, including the RES control add-back.
The first interval of the next day therefore uses the latest completed reading.

Settlement history uses the latest three simulated matching weekday/weekend
days. Until three are available, a supplied historical-price bootstrap fills
the remaining slots. Supply a bootstrap when first entering each day category;
the runner never substitutes weekday history for missing weekend history.
Bootstrap structure is the daily API's 48-period mapping to three six-price
sets. Persisted histories are retained in checkpoints.

## Checkpoints and resume

After a day passes its audit, the runner atomically writes:

- `YYYY-MM-DD/result.json`: the complete daily result, including ledger.
- `checkpoint-YYYY-MM-DD.json`: the checkpoint for that completed date.
- `checkpoint.json`: the latest committed checkpoint.

Files contain no pickle or executable object payload. Checkpoints contain
physical state, observation, price history, bootstrap prices, configuration and
source-code fingerprints, and relative daily-result paths with SHA-256 hashes.
Hashes detect accidental changes; they are not cryptographic authentication.

To stop after day one and resume in a new process, recreate the same market
definition and original initial states, then run:

```python
resumed = market.run_period(
    start='2026-11-06', end='2026-11-06', scenario=scenario,
    checkpoint_directory='study', resume_from='study/checkpoint.json',
)
```

Resume must start immediately after the checkpoint date and use the same
configuration and implementation. Carried states replace original states on
the working copy. Changed configuration/code, corrupted checkpoints/results,
and attempts to overwrite an existing committed run are rejected. Resume may
replace an uncommitted daily-result file left by an interrupted write sequence.
Copy the entire checkpoint directory to relocate a run. Use one writer per
checkpoint directory. `PeriodResults.days` includes prior saved days as well as
new days; `completed_days` lists all dates, and `checkpoint` is the latest path.

The runner currently retains the full period results in memory. It supports
normal 48-MTU days; DST dates are explicitly rejected. Daily accruals do not
become final monthly statements, and no future-day terminal value or SOC target
is introduced. Horizon-end behaviour remains that of the existing daily model.

## Monthly closure and uplift scope

After simulating the entire calendar month, call:

    closed = results.settle_month('2026-11')

Penalty proceeds, if present, are redistributed entirely through UA1.
No penalty-share argument is needed.
Optional systematic_penalty_rates maps eligible supplier or RES asset IDs to
absolute_rate, rms_rate, absolute_tolerance and rms_tolerance. Optional holidays
supplies dates for the settlement calendars.

The public workflow calculates UA-1 (imbalance energy residual) and UA-2
(ancillary services recovery) from simulated half-hour results. Monthly
closure aggregates these postings and applies any configured monthly penalties.

UA-3 and UA-4 are excluded from public daily and monthly settlement. UA-3
recovers financial Reserve Account costs, including financial coverage,
clearing services and eligible default losses. These costs depend on external
financial arrangements. Physical balancing reserve capacity is covered by
UA-2. UA-4 recovers the balance of the Reconciliation Account, which depends on
subsequent certified meter and settlement corrections outside the simulation
scope. UA5 (RES levy), UA6 (PSO levy) and UA7 (administration levy) are also
excluded. Transmission/distribution use charges remain separate settlement
components. The specialised accounting fixture retains its broader external-cost
inputs; this restriction applies to the public Market API. Old daily results
containing UA5–UA7 postings must be rerun before monthly closure.

Meter reconciliation is optional. If used, meter_corrections replaces
loss-adjusted MWh per asset and zero-based half-hour. Supplier values are
positive consumption. Storage uses signed injection. Apply profile allocation
and loss adjustment before supplying corrections. The separate reconciliation
ledger contains adjustments against the monthly baseline.


## Must-run procurement

Add `must_run_requirement_mw` (48 nonnegative MW values) to each daily scenario.
`run_period` defaults to `must_run_auction_frequency="monthly"`: one procurement
optimisation clears every MTU in each month before that month's daily execution.
Supply the complete month's requirements and supplier forecasts, even when first
executing only part of that month. The default fixed supplier shares use total
forecast consumption for that month; `must_run_supplier_shares` can instead supply
participant shares, identical for every day of the month.

Monthly awards, fixed supplier shares and their source are saved in checkpoints.
Resuming reuses these awards without re-clearing. Changed requirements or explicit
shares are rejected. Use `must_run_auction_frequency="daily"` for independent daily
auctions in partial studies. Physical dispatch still has no next-day lookahead;
monthly procurement uses the explicitly supplied monthly planning forecasts.
See [must-run details and limitations](must-run.md).
