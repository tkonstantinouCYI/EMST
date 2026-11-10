# EMST - Electricity Market Simulation Tool

## EMST v0.3.0

EMST v0.3 simulates the isolated Cyprus electricity market. It supports day-ahead clearing, integrated scheduling and reserve procurement, five-minute balancing, and daily and monthly settlement with UA-1 and UA-2. Multi-day simulations carry asset states forward and can resume from a checkpoint.

## Download and run the daily example

1. Open [EMST v0.3.0](https://github.com/tkonstantinouCYI/EMST/releases/tag/v0.3.0).
2. Under **Assets**, download `emst_cyprus-0.3.0-py3-none-any.whl` and `library_day.py` into the same folder.
3. Open a terminal in that folder and run these commands with Python 3.11 or later.

```sh
python -m pip install "./emst_cyprus-0.3.0-py3-none-any.whl[audit]"
python library_day.py --output results
```

The example simulates one synthetic Cyprus trading day and saves its outputs in `results`.

## Run a full month from the repository

```sh
git clone https://github.com/tkonstantinouCYI/EMST.git
cd EMST
python -m pip install ".[audit]"
python v0.3/examples/library_month.py --output month-results
```

The `audit` extra installs HiGHS. The monthly example simulates all 30 days of November 2026, saves daily checkpoints, and writes the monthly ledger to `month-results/monthly.json`. The ledger includes UA-1 (imbalance energy uplift, `S16`) and UA-2 (ancillary services uplift, `S17`). The example closes the month without supplying meter corrections. Use a new output folder for each fresh run.

To run the daily example from this same checkout, use `python v0.3/examples/library_day.py --output results`. To define your own scenario, use `from emst_v03 import Market`.

[Public API guide](v0.3/docs/12-public-library-api.md) · [Multi-day and monthly settlement guide](v0.3/docs/13-multi-day-execution.md)

## Source and earlier versions

- [EMST v0.3.0 source](https://github.com/tkonstantinouCYI/EMST/tree/v0.3.0/v0.3)
- [v0.1 source and original install configuration](https://github.com/tkonstantinouCYI/EMST/tree/de268e4)
- [Earlier library documentation](https://github.com/tkonstantinouCYI/EMST/blob/f30e419/README.md)
- [MEDPOWER 2026 participant-price case study](experiments/medpower2026/README.md)
