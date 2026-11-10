# EMST v0.3.0

EMST models the isolated Cyprus electricity market with synthetic scenarios.
It supports daily and multi-day simulation, checkpoint resume and monthly
settlement with UA-1 and UA-2.

Download the wheel and library_day.py from the v0.3.0 GitHub release.
Install with Python 3.11 or later:

    python -m pip install "./emst_cyprus-0.3.0-py3-none-any.whl[audit]"
    python library_day.py --output results

To install from this source directory, run python -m pip install ".[audit]".

See the [public API guide](docs/12-public-library-api.md) and
[multi-day execution guide](docs/13-multi-day-execution.md).
