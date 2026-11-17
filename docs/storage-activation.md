# Storage activation and physical-mode binaries

ISP and RTBM use separate nonnegative upward/downward activation variables for
each storage asset and interval. With signed schedule `q` in MWh and duration
`dt` in hours:

```text
P * dt = q + upward_MWh - downward_MWh
P = discharge_MW - charge_MW
SOC_next = SOC_previous + eta_charge * charge_MW * dt
                         - discharge_MW * dt / eta_discharge
```

Positive `P` means discharge; negative means charge. Physical charge/discharge
powers remain auxiliary variables, tied exactly to the actual grid energy.

## Exact linear exclusivity

A physical binary `m` selects charging (`m=1`) or discharging (`m=0`):

```text
0 <= charge_MW    <= Pmax * m
0 <= discharge_MW <= Pmax * (1-m)
```

The activation bounds account for the accepted signed schedule:

```text
Umax = max(0, Pmax * dt - q)
Dmax = max(0, Pmax * dt + q)
0 <= upward_MWh   <= Umax * z
0 <= downward_MWh <= Dmax * (1-z)
```

When `q=0`, reuse the physical binary by setting `z=1-m`; no extra activation
binary is created. When `q` is nonzero and both directions are possible, add a
separate activation binary `z`. If a physical bound already makes one direction
impossible, its activation variable has upper bound zero and an additional
binary is unnecessary. Disabled balancing energy fixes both activations to zero.
Zero activation is permitted with either binary setting, so an idle-mode binary
is not needed.

This prevents simultaneous up/down activation independently of prices, including
equal-price offers. Different assets may still activate in opposite directions.
A full reversal from maximum scheduled charging to maximum actual discharge can
require `2 * Pmax * dt` upward activation, which these bounds preserve.

Activation direction and physical direction differ when MS is nonzero: starting
from -2 MWh, +1 MWh upward activation leaves physical charging, while +3 MWh
produces physical discharging. Separate modes preserve the appropriate SOC
efficiency on both sides of that crossing.

Offer cost remains `upward_price * upward_MWh - downward_price * downward_MWh`.
The previous signed-net/convex-offer-cost representation has been removed.
Existing SOC, power, ramp, reserve and terminal constraints remain in place.
Fixed-discrete pricing freezes both physical and additional activation binaries
before solving the pricing LP; smoothing still preserves the primary prices.
Alternative optimal solutions/pricing bases need not produce bitwise-identical
cashflows, so settlement is checked through reconciliation.

## Diagnostics and tests

Dispatch results identify
`storage_activation_formulation="separate_up_down_with_binary_exclusivity"`.
`storage_activation_binary_count` counts extra activation binaries, excluding
reused physical ones. Storage rows expose `physical_charging_mode`,
`activation_up_mode` and `activation_mode_reused`. An absent activation mode
means eligibility or bounds already force a direction/zero activation.

Daily audits verify activation and charging exclusivity in both ISP and executed
RTBM, plus `energy = MS + up - down`. These checks supplement existing SOC,
ramp, power, reserve, market-balance and settlement checks. Verification tolerance
is `1e-7` MWh for simultaneous legs and `1e-6` MWh for the energy bridge; the
mathematical restrictions are exact binary constraints.

Focused tests cover equal-price offers, unequal directional costs, idle and
nonzero schedules, physical/activation direction differences, both full-power
reversals, efficiencies and SOC, disabled eligibility, zero-MS binary reuse,
forced conflicting instructions and opposite activations across different assets.

## Daily benchmark

The benchmark uses the same 10 MW/40 MWh battery with balancing energy and
capacity enabled, 180 MW must-run procurement, forward positions, circular DAM
orders, two ISP runs and 288 executed RTBM intervals. Prices are 100/0 EUR/MWh
(up/down) and 100/100. Each price case has three fresh-process pairs with
alternating before/after order, one solver thread, module/tiny-LP warmup outside
the timer, and no concurrent simulations/tests or large exports.

Here **before** means the previous signed-activation formulation, preserved in a
local source snapshot before this change. **After** means separate up/down
variables with binary exclusivity and zero-MS reuse. It is not a comparison
against the earlier unrestricted up/down implementation on GitHub.

```sh
python tools/benchmark_storage_activation_paired.py --baseline-root BASELINE_DIR --output output/storage-separate-binaries-paired --repetitions 3
```

`BASELINE_DIR` must contain the preserved pre-change `emst/` package. Timing covers
`Market.run`, excluding market construction, process startup and exports.
`comparison.json` includes individual measurements, medians, per-stage solver
wall time and scenario/implementation fingerprints. Stage solver time excludes
Python orchestration. Forecasts, actual availability and offer/configuration
inputs must match; realized RES observations are excluded from the exogenous
comparison hash because they depend on dispatch/curtailment.

Raw/readable validation days can be exported after all measured pairs using the
worker's `--save-results` option. All runs verify daily stage audits, no security
slack, participant cash attribution and readable financial positions.


## Local benchmark and validation results

Python 3.14.8, PuLP 3.3.2, HiGHS 1.16.0, three paired daily runs per price case:

| Up/down offer EUR/MWh | Previous signed formulation median | Separate activations/binaries median | Change |
|---|---:|---:|---:|
| 100 / 0 | 11.27 s | 11.82 s | +4.9% |
| 100 / 100 | 21.87 s | 25.19 s | +15.2% |

Changed-code runs ranged from 10.92–13.91 seconds and 24.49–34.49 seconds,
respectively. These are descriptive small-sample measurements, not a guaranteed
runtime or scaling prediction. Setup, imports, warmup and exports are excluded.

Each example day has four nonzero storage MS intervals. The initial ISP adds
four activation binaries and the noon ISP adds two; the other 66 rows across
these two ISP horizons reuse their physical-mode binaries. RTBM makes the same
choice independently at each interval in its rolling horizon.

All benchmark days cleared and settled without security slack. Separate raw and
readable reruns passed all 38 daily checks, including activation exclusivity,
physical exclusivity, schedule/activation energy bridges, SOC, power, ramps,
market/reserve balances, must-run recovery and cash reconciliation. Results are
in `output/storage-separate-binaries-paired/`, including `comparison.json` and
`distinct_prices-validated-day/` / `equal_prices-validated-day/` reports.

The complete regression suites passed: 135 tests on HiGHS 1.16.0; 134 passed and
one optional dependency test skipped on HiGHS 1.15.1. No collection errors occurred.
