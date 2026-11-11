# EMST — Electricity Market Simulation Tool

EMST is a Python library for simulating the isolated Cyprus electricity market.
Define participants, thermal units, renewables, storage and demand scenarios, then
run daily or monthly simulations with market clearing and settlement.

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
installed with `python -m pip install .`.

The daily example saves the original raw stage JSON files alongside:

- `results/readable_results.json`: all stages and private participant results.
- `results/shared_results.json`: shared summaries without private participant data.
- `results/private/participant_<hash>.json`: a separate report for each participant.

In the readable report, find each participant's cashflows at
`simulation_summary.days[].market_stages.settlement.participants.<participant_id>.financial_position`.
Its `intervals` contain cash per half-hour MTU for forward contracts, DAM, ISP,
RTBM, other settlement, UA1 and UA2, plus the total. Positive amounts are
receipts; negative amounts are payments. `totals` sums the MTUs, and
`daily_total_cash_eur` also includes any separately listed non-MTU postings.
UA1 and UA2 are separate columns and are counted once in the total.
The public API excludes UA3–UA7. Network-use charges remain under other
settlement. Monthly penalty proceeds, if present, are credited through UA1;
no penalty-share configuration is required.
Only share the summaries or the intended participant's private report; the
operator report contains everyone's private data.

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
The seven cashflow columns include UA1 and UA2. Monthly totals use the closed
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
