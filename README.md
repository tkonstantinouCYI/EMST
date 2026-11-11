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

## Use the library

```python
from emst import Market, ThermalState, StorageState

market = Market()
```

The library supports supplied forward contracts, day-ahead auctions and storage
blocks, integrated scheduling and reserve procurement, five-minute balancing,
daily settlement, consecutive-day state carryover and checkpoint resume.
Market rules and synthetic parameters are explicit simulation inputs.

[API guide](docs/api.md) · [Multi-day and monthly settlement](docs/multi-day.md)

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
