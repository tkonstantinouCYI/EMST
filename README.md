# EMST - Electricity Market Simulation Tool

## EMST v0.3.0

EMST v0.3 simulates the isolated Cyprus electricity market. It supports day-ahead clearing, integrated scheduling and reserve procurement, five-minute balancing, and daily and monthly settlement with UA-1 and UA-2. Multi-day simulations carry asset states forward and can resume from a checkpoint.

Download the wheel and `library_day.py` from the [v0.3.0 release](https://github.com/tkonstantinouCYI/EMST/releases/tag/v0.3.0). With Python 3.11 or later, run these commands from the download folder:

```sh
python -m pip install "./emst_cyprus-0.3.0-py3-none-any.whl[audit]"
python library_day.py --output results
```

The example runs a synthetic trading day and writes the results to the `results` folder. To define your own simulation, use `from emst_v03 import Market`. See the [public API guide](https://github.com/tkonstantinouCYI/EMST/blob/v0.3.0/v0.3/docs/12-public-library-api.md) and [multi-day guide](https://github.com/tkonstantinouCYI/EMST/blob/v0.3.0/v0.3/docs/13-multi-day-execution.md).

## Source and earlier versions

- [EMST v0.3.0 source](https://github.com/tkonstantinouCYI/EMST/tree/v0.3.0/v0.3)
- [Earlier library documentation](https://github.com/tkonstantinouCYI/EMST/blob/f30e419/README.md)
- [MEDPOWER 2026 participant-price case study](experiments/medpower2026/README.md)
