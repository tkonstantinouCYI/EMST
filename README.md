# EMST — Electricity Market Simulation Tool

EMST is a Python library for simulating the isolated Cyprus electricity market.
Define participants, thermal units, renewables, storage and demand scenarios, then
run daily or monthly simulations with market clearing and settlement.

## Planning diagnostics and investment integration

Version 0.3.2 adds solver settings and solve records, validated RTBM smoothing
fallbacks, explicit actual-balance diagnostics, asset operating-cost/cash
reconciliation, economic-owner grouping, and optional realized terminal SOC.
Strategies now reject unknown parameters. **Optimization feasibility does not
establish actual load-loss reliability**: EENS/LOLE remain unassessed.

See [the planning integration guide](docs/planning-integration.md) for units,
accounting boundaries, independent-day/month workflows, structured failures,
endpoint choices and deferred capabilities. Raw exports now also include
`diagnostics.json` and `economics.json`; private readable reports include asset
costs and participant cash attribution.

After installation, run the paired interface pilot with:

```sh
python examples/planning_pilot.py --output planning-results
```

It includes three capacity cases, a biased forecast and a repeated baseline;
`planning-results/verification.json` records the checks. These are synthetic
interface checks, not investment recommendations or controlled speed benchmarks.

## Install from GitHub

Use Python 3.11 or later.

```sh
git clone https://github.com/tkonstantinouCYI/EMST.git
cd EMST
python -m venv .venv
```

On Windows, run:

```powershell
.venv\Scripts\python -m pip install ".[audit]"
.venv\Scripts\python examples/library_day.py --output results
```

On Linux or macOS, run:

```sh
.venv/bin/python -m pip install ".[audit]"
.venv/bin/python examples/library_day.py --output results
```

The audit extra installs the HiGHS solver. The example writes a synthetic
trading day's outputs to the results folder. The base library can also be
installed with `python -m pip install .`. On Windows use a short checkout path if
pip reports a long-path error; editable installation with
`python -m pip install -e ".[audit]"` is an alternative. Use the normal writable
Windows temporary directory when building packages.

The daily example saves the original raw stage JSON files alongside:

- `results/readable_results.json`: all stages and private participant results.
- `results/shared_results.json`: shared summaries without private participant data.
- `results/private/participant_<hash>.json`: a separate report for each participant.

In the readable report, find each participant's cashflows at
`simulation_summary.days[].market_stages.settlement.participants.<participant_id>.financial_position`.
Its `intervals` contain cash per half-hour MTU for forward contracts, DAM, ISP,
RTBM, must-run service, other settlement, UA1 and UA2, plus the total. Positive amounts are
receipts; negative amounts are payments. `totals` sums the MTUs, and
`daily_total_cash_eur` also includes any separately listed non-MTU postings.
UA1 and UA2 are separate columns and are counted once in the total.
The public API excludes UA3–UA7. Network-use charges remain under other
settlement. Monthly penalty proceeds, if present, are credited through UA1;
no penalty-share configuration is required.
Only share the summaries or the intended participant's private report; the
operator report contains everyone's private data.

## Must-run auctions

The daily and monthly synthetic examples use a **180 MW requirement per MTU**.
Eligible thermal units bid EUR/unit/MTU. The auction minimises service cost and
covers the requirement with the units' **minimum stable output**, not nameplate
capacity. It runs before forward contracts and DAM. Awarded minimum energy is
allocated to suppliers at zero direct energy price; both ISP runs and RTBM
respect the awards. Pay-as-bid service payments and supplier recovery are
separate from UA1 and UA2.

Use `must_run_participation=True` and `must_run_offer_eur_per_mtu=...` when
registering a thermal unit. Pass `must_run_requirement_mw=[180.0] * 48` to
`Market.run`, or include it in every daily scenario for `run_period`.
Omit the requirement to disable procurement. Monthly runs default to one
auction per month and require the complete month's requirement and supplier
forecast series before clearing. Partial studies can select
`must_run_auction_frequency="daily"`.

Readable JSON includes `market_stages.must_run_auction` (shared totals and
private awards, supplier allocations and cashflows), and
`financial_position.intervals[].must_run_cash_eur`. Monthly financial totals
include these payments and charges. See [configuration, settlement and
limitations](docs/must-run.md). This is a simplified MW-based research model;
it does not reproduce the official inertia-based procurement requirements.

## Run a historical Cyprus day

```sh
python examples/historical_cyprus_day.py --output historical-results
```

This separate example uses public TSOC demand, solar/wind generation, forecasts
and directional FCR/aFRR/mFRR requirements for 14 October 2026, with documented
assumptions for the representative committed fleet, bids and forward contract.
It saves raw and readable JSONs, participant financial positions with UA1/UA2,
and a stage verification report. Five-minute observations are reconstructed
from half-hour data; this is a historical-data study, not an official market replay.
See [sources, assumptions and data refresh instructions](docs/historical-cyprus-day.md).

The dataset is bundled in `examples/data/cyprus_2026_10_14.json`, so the run
requires no internet connection or Excel reader. Use the environment's Python
as shown in the installation instructions above. Open these files after the run:

- `historical-results/raw_results.json`: complete raw simulation results.
- `historical-results/raw/`: raw results split into stage/input files.
- `historical-results/readable_results.json`: readable market stages and private
  participant results, including financial positions per MTU with UA1/UA2.
- `historical-results/shared_results.json`: shared summaries only.
- `historical-results/private/participant_<hash>.json`: individual participant reports.
- `historical-results/verification.json`: stage checks and reconciliation results.
- `historical-results/scenario_inputs.json` and `assumptions.json`: public input
  series, source provenance and modelling assumptions.

## Run a full month

Using the environment's Python, run:

```sh
python examples/library_month.py --output month-results
```

On Windows use `.venv\Scripts\python` in place of `python`.
On Linux or macOS use `.venv/bin/python`.

The example simulates all 30 days of November 2026, saves daily checkpoints,
and writes monthly settlement to `month-results/monthly.json`. It calculates
UA-1 and UA-2 and closes the month without meter corrections. Use a new output
folder for each fresh run.

The full month is also saved in `month-results/readable_results.json`, containing
all 30 days and monthly participant totals at
`simulation_summary.monthly_settlement.participants.<participant_id>.financial_position`.
The eight cashflow columns include must-run service, UA1 and UA2. Monthly totals use the closed
monthly ledger; `daily_accrual_total_cash_eur` and `monthly_adjustment_cash_eur`
explain any difference from the daily reports, including month-only charges or
credits. Combined shared and participant reports are saved alongside it in
`shared_results.json` and `private/`.

Each date folder also contains `readable_results.json`, `shared_results.json`,
and `private/participant_<hash>.json`. For example, open
`month-results/2026-11-01/readable_results.json` to inspect the first day's
market stages and participant financial positions per MTU, including UA1 and
UA2. These are daily reports; `month-results/monthly.json` remains the separate
monthly closure ledger, including any month-only postings. Raw daily
`result.json` files and checkpoints remain unchanged.

## Use the library

```python
from emst import Market, ThermalState, StorageState

market = Market()
```

After a daily `market.run(...)` returns `results`, save both formats with:

```python
results.save("results")                  # Raw stage JSON files
results.save_readable("results")         # Operator, shared and private JSON files

shared = results.to_readable()
private = results.to_readable(audience="participant", participant="supplier_T")
```

For a `market.run_period(...)` result, export each day with:

```python
from pathlib import Path

for day, daily in period_results.days.items():
    daily.save_readable(Path("month-results") / day)

monthly = period_results.settle_month(
    "2026-11"
)
period_results.save_readable("month-results", monthly=monthly)
```

The library supports supplied forward contracts, day-ahead auctions and storage
blocks, integrated scheduling and reserve procurement, five-minute balancing,
daily settlement, consecutive-day state carryover and checkpoint resume.
Market rules and synthetic parameters are explicit simulation inputs.

[API guide](docs/api.md) · [Bidding strategies: parameters, defaults and examples](docs/strategies.md) · [Multi-day and monthly settlement](docs/multi-day.md)

## Releases

The main branch contains the current library. Numbered releases and Git tags
preserve previous snapshots. The package distribution is named `emst-cyprus`
and the Python import is `emst`. Earlier release assets use their documented
imports and installation instructions.

[Release history](https://github.com/tkonstantinouCYI/EMST/releases)

## Development checks

```sh
python tools/run_tests.py
```

These are synthetic rules-based simulations. Current-rules conformance and
calibration against observed Cyprus operation remain to be verified.


## Run the regression tests

From the repository root after installation:

```sh
python tools/run_tests.py
```

No PYTHONPATH workaround is needed. Results and collection errors are written to
`output/unit-tests-<backend>.json` and `.log`. Optional `--output DIRECTORY`
keeps validation runs separate. The CI matrix covers Windows/Linux,
Python 3.11/3.14 and HiGHS 1.15.1/1.16.0. See the
[maintainer response](docs/maintainer-response.md) for the review scope and limits.


Storage ISP/RTBM uses separate upward/downward activation variables with binary
exclusivity. Physical charging/discharging also remains exclusive; when MS is
zero, both direction choices reuse the same binary. SOC, efficiencies and power
constraints remain enforced. See the [formulation, tests and paired daily
benchmark](docs/storage-activation.md).


## Optional 30-minute sequential RTBM

Five-minute sequential RTBM remains the default. For faster research scenarios,
use `market.run(..., rtbm_interval_minutes=30)` or:

```sh
python examples/library_day.py --rtbm-interval-minutes 30 --output results30
python examples/library_month.py --rtbm-interval-minutes 30 --output month30
```

This runs 48 sequential intervals per normal day, using current-interval demand and RES assumed known at its start, with immediate
ramping and thermal/storage state carryover. Five-minute mode retains its existing
forecast/observation and physical-ramp behaviour. It is explicitly
labelled a **30-minute RTBM and settlement research approximation**. It does not
produce artificial five-minute prices or use full-day perfect foresight. Dispatch,
prices, cashflows and residuals can differ from native five-minute execution.
See [inputs, timing/accuracy comparison and limitations](docs/rtbm-resolution.md).

## Combined-cycle plant configurations

For an explicitly simplified **single-envelope CCGT** and one-binary ON/OFF
commitment, see [ON/OFF thermals and native hourly ISP](docs/onoff-thermal.md).
The options are independent of the configured-plant model below. Staged startup
and the half-hour ISP remain the defaults.

```sh
python examples/onoff_day.py --isp-interval-minutes 30 --output output/onoff30
python examples/onoff_day.py --isp-interval-minutes 60 --output output/onoff60
```

These synthetic examples use 30-minute sequential RTBM and save raw/readable
results with financial positions. Hourly ON/OFF runs use 24 initial and 12 noon
ISP intervals with half-hour commercial accounting. This is a physical
approximation, not an exact substitute for staged/configured commitment.

Use `Market.add_configured_thermal()` for one physical plant with mutually
exclusive operating configurations, explicit transition profiles/costs, and
component minimum up/down histories. ISP selects the configuration; both RTBM
modes follow it, including transitions crossing noon or midnight. Energy,
reserves and settlement remain at plant level.

```sh
python examples/configured_plant.py --rtbm-interval-minutes 5 --output ccgt5
python examples/configured_plant.py --rtbm-interval-minutes 30 --output ccgt30
```

See [configuration inputs, costs, settlement and limitations](docs/configured-plants.md).

Reserve capacity during technically eligible CC transitions can be enabled per
product and direction with explicit transition MW envelopes. It uses the existing
continuous reserve variables and adds no binaries. The example's eligibility and
envelopes are synthetic assumptions:

```sh
python examples/configured_plant.py --configuration-formulation compact --transition-reserves --rtbm-interval-minutes 30 --output ccgt-reserves
```

See [transition-capacity inputs and limits](docs/configured-plants.md#reserve-capacity-during-transitions).
For reproducible initial-ISP timing experiments with one three-configuration CC
plant and 24 hourly intervals, run `python tools/benchmark_cc_transition_reserves.py`.
The [configured-plant hourly experiment](docs/hourly-virtual-cc-experiment.md) remains
standalone. The public daily ISP defaults to half-hours; native hourly integration
is now available for the separate ON/OFF ordinary-thermal approximation.

## Optional compact configured-plant commitment

Set `configuration_formulation="compact"` when calling
`Market.add_configured_thermal()` to use exact transition-start indicators and
derived transition progression, with continuous exact component-event auxiliaries.
`"reference"` remains the default and retains expanded occupancy commitment.
Physical data, transitions, reserve rules and optimality requirements are unchanged.
See [equations, equivalence proof, synthetic benchmarks and validation](docs/compact-configurations.md).

## Optional ISP warm starts

Set `solver_config={"stages": {"ISP": {"warm_start": True}}}` when constructing
`Market` to repair and submit candidate schedules to both full ISP MILPs.
The final objective and optimality targets are unchanged. Warm starts default to
off, and repair overhead can outweigh savings on easy cases. The daily examples
accept `--isp-warm-start`.
See [inputs, repair, diagnostics and benchmarking](docs/isp-warm-starts.md).
The [measured comparisons](docs/isp-warm-start-benchmarks.md) include complete
ISP/day time and show why warm starts remain optional rather than a default
performance promise.

An additional `warm_start_source="lp"` option uses the current ISP's LP
relaxation to guide repair before the unrestricted MILP. See
[LP-guided warm starts and the ten-unit comparison](docs/lp-guided-warm-starts.md).

RES output is no longer capped at the accepted commercial schedule. See the
[RES availability and settlement policy](docs/res-availability.md) for available
versus metered inputs, explicit curtailment and reproducible examples:

```shell
python examples/res_availability_day.py
```
