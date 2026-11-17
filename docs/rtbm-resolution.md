# Sequential RTBM: five or 30 minutes

Five-minute sequential RTBM remains the default. An optional 30-minute mode
uses current-interval demand and RES assumed known at the start of each half-hour:

```python
results = market.run(
    time_index=index,
    reserve_requirements=reserves,
    historical_rtbm_prices=history,
    rtbm_interval_minutes=30,
)
```

Only integer `5` and `30` are supported. This is an execution option, not a
change to the current-market ruleset's declared five-minute resolution. The
30-minute mode is explicitly a **research approximation**, not an official
five-minute market replay. The normal 48-MTU-day restriction remains; this option
does not add DST support.

## Information and physical operation

Both modes clear intervals chronologically, with different information and ramping assumptions.
Five-minute execution retains the TSO forecast corrected by the last completed
interval's observed error, with physical thermal ramp trajectories.

Thirty-minute execution assumes the **current half-hour's participant demand and
RES actual inputs are known at its start**. These are the values used for clearing;
independent `system_forecasts` and `system_observations` do not override them.
This is an explicit current-interval information assumption, not a forecast of
unknown future measurements. The next half-hour's actuals are not used.

| Feature | Default five minutes | Optional 30 minutes |
|---|---|---|
| Executed normal-day intervals | 288 | 48 |
| Clearing inputs | Forecast plus last completed error | Current half-hour demand/RES inputs |
| Market energy/power conversion | MWh / (1/12 hour) | MWh / 0.5 hour |
| ISP initial/rerun coverage | Midnight–noon / noon–midnight | Same |
| 11:00 forecast bridge to noon | 12 projected steps | Two projected steps |
| RTBM ramping | Thermal trajectories and storage ramp limits | Immediate changes at interval start |

The 11:00 ISP rerun remains effective at noon. Its bridge uses forecasts and the
last completed observation, never future actuals, even in coarse mode. The
coarse bridge uses immediate ramping. Neither mode performs full-day
perfect-foresight RTBM optimization.

ISP average-power ramp constraints are unchanged. In 30-minute RTBM, thermal
phase/commitment, phase profile targets, minimum/maximum power, storage SOC,
efficiencies, activation exclusivity, charging/discharging exclusivity and
optional terminal SOC constraints remain enforced. Thermal and storage RTBM
ramp limits are bypassed: interval energy equals cleared power times 0.5 hour.
The two constant thermal `nodes_mw` entries describe the held power after the
instantaneous boundary change; they do not represent a finite ramp. Ending power
is this held power. Storage has one physical mode per half-hour and cannot
reverse within it.

## Inputs, aggregation and outputs

Trading `time_index`, participant forecasts and reserve requirements remain
48 half-hour values. Participant actuals and independent TSO inputs may contain
48 half-hour MW values or 288 correctly indexed five-minute MW values.
Five-minute participant actuals are averaged over each half-hour for coarse clearing;
averaging preserves input energy. Half-hour inputs are held constant within
each MTU when used in the five-minute model, as before. Missing values and
unsupported indices/resolutions are rejected rather than interpolated.

`inputs.actual_mw` and `system_forecast_mw` / `system_observation_mw` follow the
selected execution grid. `source_actual_mw_5min` preserves a canonical fine input
grid for provenance, and `actual_input_interval_minutes` identifies the supplied
resolution. If inputs were half-hourly, the canonical five-minute series is a
piecewise-constant expansion, not additional measured data. It is not a list of
executed coarse-mode instructions or prices.

Raw results identify `rtbm_interval_minutes`, `rtbm_execution="sequential"`,
`rtbm_research_approximation` and `settlement_resolution`. Readable day and RTBM
summaries identify the chosen interval and approximation; their RTBM intervals
number 288 or 48. Daily financial positions continue to use the common 48 MTUs.

## Prices and settlement

Native five-minute pricing/settlement is preserved. Coarse mode computes one
RTBM price/activation per half-hour, and uses that result for the half-hour
settlement calculation, retaining the existing zero-activation history and
scarcity conventions. Directional offer/LABEO summaries use actual activations
on the selected grid. It does not generate six artificial five-minute prices or
claim equivalence to their weighted outcome.

In coarse mode, historical input for each MTU can contain three corresponding
days with one price each. Existing three-day/six-five-minute-price sets are also
accepted and averaged to historical half-hour prices. Five-minute execution
requires six historical prices per day/MTU; coarse history is not expanded into
invented fine prices. No change is made to native fallback calculation.

Meters, instructed/uninstructed quantities, network charges, UA1/UA2, must-run
service and participant cashflows are calculated on the selected dispatch
outcomes, with the common 30-minute settlement accounts. Coarse results are
labelled `30min_research_approximation`. They are not official five-minute
settlement results. Asset economics and readable financial positions reconcile
to the ledger in both modes.

Coarse averaging can hide short-lived shortages/surpluses and sign reversals,
and assumes current-interval information and instantaneous physical response. An audit pass means model constraints
and accounting checks passed, not actual load-loss adequacy. Actual residual
diagnostics remain separate from optimization slack; EENS/LOLE remain unassessed.

## Multi-day execution and monthly closure

Put `rtbm_interval_minutes=30` in every daily scenario to run consecutive coarse
days. A run/checkpoint history must use one consistent resolution; changing it
within a period or on resume is rejected. Carried thermal/storage states and the
last completed observation follow the selected interval. Historical price memory
contains 288 prices/day for native runs or 48 prices/day for coarse runs.

Monthly closure supports consistently simulated coarse months, with explicit
resolution labels and UA1/UA2, without meter reconciliation by default. Mixed
five-/30-minute months are rejected. The declared ruleset is retained alongside
the research-mode flag rather than silently rewritten.

```sh
python examples/library_day.py --rtbm-interval-minutes 30 --output results30
python examples/library_month.py --rtbm-interval-minutes 30 --output month30
```

Omit the flag for the default five-minute execution.

## Reproducing the comparison

```sh
python tools/compare_rtbm_resolution.py --output output/rtbm-current-input-comparison --repetitions 3
```

The fixture is the daily example builder with a 10 MW/40 MWh battery, balancing
energy/capacity enabled, 180 MW must-run requirement, forward contracts, circular
DAM orders and the same forecast/actual/history inputs in both modes. Independent
TSO inputs fall back to participant aggregates in this fixture. Each run starts
from the same initial state and uses a fresh process; pair order alternates.
Imports and a tiny solver warmup precede timing. `Market.run` wall time excludes
construction and exports. Tests/other simulations must not run concurrently.
Large raw/readable export reruns follow all timed runs.

Native dispatch is energy-aggregated to the common half-hour grid for comparison.
Native RTBM clearing prices are time-averaged for the clearing-price comparison;
settlement prices are compared separately on their actual common MTU grid.
There is no expansion of coarse results to pretend subinterval detail. Economic
comparisons use the first timed pair; all repetitions and individual times are
retained. Initial/final state, actual energy, operating costs, each participant's
stage/UA1/UA2/total cash and balance residuals are included in `comparison.json`.
`5min-results/` and `30min-results/` contain raw/readable validation exports.

### Local results

The corrected current-input/immediate-ramping comparison is saved separately in
`output/rtbm-current-input-comparison/`. Earlier results in
`output/rtbm-resolution-comparison/` used a completed-observation lag and physical
ramps in coarse mode; they do not describe the current implementation.


Measured on Windows, Python 3.14.8, PuLP 3.3.2, HiGHS 1.16.0, one solver thread.
Three alternating fresh-process pairs; timing is `Market.run` wall time, after
module/solver warmup and before exports. No other simulations ran during timing.

| Quantity | Five minutes | Corrected 30 minutes |
|---|---:|---:|
| Median daily execution time | 12.806 s | 1.915 s |
| Executed RTBM intervals | 288 | 48 |
| Mean MTU imbalance price | EUR83.3899/MWh | EUR83.4271/MWh |
| Combined supplier net cash outflow, all accounts | EUR295,186.2142 | EUR295,186.3612 |
| Positive observed-delivery residual | 0.504897 MWh | Below 1e-12 MWh |
| Negative residual magnitude | 0.427064 MWh | Below 1e-12 MWh |

The corrected coarse case was about **6.69 times faster**. This is a local
benchmark, not a scaling guarantee. Half-hour dispatch mean absolute differences
were 0.02588 MW for plant A and 0.02980 MW for plant B, with maximum differences
about 0.25001 MW. Plant C remained off; battery differences were numerical-scale.
Closing battery SOC was about 20 MWh in both modes.

RTBM clearing-price mean absolute difference was EUR0.05556/MWh (maximum
EUR0.66667/MWh); half-hour imbalance-price mean absolute difference was
EUR0.08709/MWh (maximum EUR1.53743/MWh). Supplier combined daily outflow increased
by EUR0.14693. Producer A's total receipts changed by +EUR18.28043 and producer
B's by -EUR18.27379; all participants' stage/UA1/UA2 cash differences are retained
in `comparison.json`. Forward cash, DAM and initial ISP objective were unchanged.
The rerun ISP may change because it starts from the mode's projected noon state.

Every daily stage audit passed, no optimization security slack was used, and
all participant cashflows reconciled to the ledger. Both raw and readable daily
exports were regenerated under `5min-results/` and `30min-results/`.
The negligible coarse residual follows from the assumption of known current
inputs and immediate ramping; it does not establish better physical accuracy.
The five-minute comparison intentionally retains its existing information and
ramping assumptions. Differences therefore reflect both resolution and those
assumptions. This fixture has half-hourly demand; it does not validate accuracy
against genuine five-minute variability.


Regression validation: **141 tests passed** with HiGHS 1.16.0. With HiGHS 1.15.1,
140 passed and one optional-dependency test was skipped; neither run had failures,
errors or collection errors. Tests cover current interval averaging, immediate
thermal response despite a restrictive ramp parameter, current RES inputs,
no future-actual leakage into the noon ISP bridge, default/explicit five-minute
equivalence, and exact checkpoint/resume equivalence in coarse mode. Evidence is
in `output/rtbm-current-input-tests16/` and `output/rtbm-current-input-tests15/`.
