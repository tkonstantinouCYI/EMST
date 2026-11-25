# RES availability, curtailment and settlement

RES physical output is independent of the commercial market schedule. The
public API uses the explicit baseline policy
`available_output_without_market_schedule_cap`, exported in the manifest.
This replaces the previous implicit curtailment of output above the schedule.
Both native half-hour ISP and optional hourly ISP use uncapped forecast
availability; RTBM uses current available-output inputs as described below.
The specialised ISP/RTBM offer paths also no longer cap availability at a sale.

## Available output versus delivered metering

```python
market.add_res(
    'solar', 'solar_owner',
    generation_forecast=forecast_mw,
    actual_generation=available_mw,
    actual_generation_kind='available',  # default
    energy_down_price=-5,
)
```

`available` means the energy that could physically be delivered before control.
For each executed RTBM interval:

```
delivered_MWh = available_MWh - curtailed_MWh
0 <= curtailed_MWh <= available_MWh
```

Curtailment is an explicit downward balancing decision, eligible according to
`ruleset['res']['participation_balancing_energy']`. A 20 MW commercial sale does
not limit a portfolio with 100 MW of availability to 20 MW physical output.
There is no automatic DAM/non-acceptance curtailment. Legacy commercial
curtailment fields remain in raw exports for compatibility and are zero.

Each portfolio's current interval available-output input is assumed known at
RTBM gate opening, in both five- and thirty-minute modes. For a five-minute
rolling solve, only that current actual value is held over the remaining
within-MTU optimization horizon; later actuals are not read. Demand retains
its observation-based prediction in five-minute mode and current-interval
inputs in thirty-minute mode. The 11:00 projection to noon uses forecasts and
the latest completed observation, not future actual availability. Forecast
error correction is retained as `res_forecast_prediction_mw`; the executed
`res_prediction_mw` and net demand reflect the physical RES baseline.
This is an explicit perfect-current-availability scenario assumption, not a
claim that future realized output is known in an operational forecasting system.

For historical already-delivered data, select:

```python
market.add_res(
    'solar', 'solar_owner',
    generation_forecast=available_forecast_mw,
    actual_generation=delivered_meter_mw,
    actual_generation_kind='metered',
    metered_curtailment_mw=known_executed_curtailment_mw,  # optional
)
```

Metered delivery is fixed in executed RTBM. It is not interpreted as an
uncurtailed potential and cannot be curtailed again. If no known instruction
series is provided, no instructed curtailment is inferred from a forecast or
schedule difference. If known curtailment is supplied, it is a fixed executed
instruction, included in settlement and reconstructed observation availability,
without subtracting it from delivery a second time. A metered excess that
cannot be absorbed is reported as surplus slack, rather than hidden curtailment.
Forecasts continue to describe available potential for ISP and noon projection.

All input series are MW, supplied as 48 half-hour or 288 five-minute values,
or matching indexed Series/single-column DataFrames. Fine values are averaged
in thirty-minute mode. Values must be finite/nonnegative; known curtailment
requires `metered` mode and delivered-plus-curtailed output must respect any
registered RES capacity.

In `run_period` scenarios, optional `metered_curtailment_mw` maps RES asset IDs
to that day's series. An omitted asset uses its registered default (zero if
none); a previous day's override is not silently carried forward. Current
observations, including known control, are saved in checkpoints.

## Settlement policy

The market schedule remains the commercial reference. With the default zero
RES tolerance, ordinary RES settlement uses:

```
INSTQ = -curtailed_MWh
UNINSTQ = delivered_MWh + curtailed_MWh - MSQ
```

Curtailment can exceed the scheduled sale. `MSQ - curtailment` can therefore be
negative; that is a commercial reference, not negative physical RES generation.
This keeps the available-output/commercial-position deviation separate from
instructed control. Cash pricing, grant-supported RES exclusions and UA1/UA2
allocation continue through the existing settlement paths. The same quantity
bridge is used by public daily, specialised and monthly calculations.

This is the requested simulation settlement policy, not a new assertion of
verified Cyprus rules compliance. At nonzero configured tolerance, the existing
band convention is retained for nonnegative references and extended with a
band width `abs(MSQ - curtailment) * tolerance` for negative references. Zero
tolerance implements the equations above exactly; users seeking another
nonzero-tolerance convention should specify it explicitly.

For observation updates, delivered system RES metering has known instructed
curtailment added back before comparison with its availability forecast.
`system_observations['res']`, if supplied independently, must be delivered
metering; the engine adds known portfolio control once. Unknown historical
curtailment cannot be reconstructed and is not invented. Raw results expose
metered and reconstructed observations separately; private readable RTBM
results include available energy (null for metered-only inputs), delivery and
curtailment. These are participant-private quantities, not shared portfolios.

## Reproduce the acceptance cases

```shell
python examples/res_availability_day.py
python examples/res_availability_day.py --rtbm-interval-minutes 5 --output output/res-availability-5min
python examples/res_availability_day.py --isp-interval-minutes 60 --output output/res-availability-hourly
```

The example sells 20 MW in DAM with 100 MW RES availability. It saves raw stage
JSON, readable operator/shared/private JSON and `verification.json` for each
case. Per half-hour the expected quantities are:

| Demand MW | Delivered MWh | Curtailed MWh | MSQ MWh | INSTQ MWh | UNINSTQ MWh |
| --- | --- | --- | --- | --- | --- |
| 100 | 50 | 0 | 10 | 0 | 40 |
| 60 | 30 | 20 | 10 | -20 | 40 |

Both cases have zero shortage/surplus and zero physical energy residual in
RTBM. Tests additionally cover negative commercial references, mixed available
and metered portfolios, fixed historical instructions, observation correction,
noon causality and two-day checkpoint/resume at both ISP grids.

## Validation evidence (25 November 2026)

With Python 3.14.10 and HiGHS 1.16.0, the full 223-test run completed in 667.07
seconds with 222 passes and one legacy causality expectation failure. That test
was updated so current availability is shared while only future RES actuals
differ; it passed on rerun. The expanded RES checkpoint test also passed with
five-minute RTBM and both ISP grids. The two final checks took 20.18 seconds.
No core source changed after the full run started. This is coverage across the
full run and focused reruns, rather than a claim of a clean uninterrupted run.

Logs and the consolidated `final-summary.json` are under
`output/res-availability-validation-20261125/`. All six exported acceptance days
passed their stage audits and had no RTBM security slack, with raw/readable
results under `output/res-availability-30min/`, `output/res-availability-5min/`
and `output/res-availability-hourly/`.
