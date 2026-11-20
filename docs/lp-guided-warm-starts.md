# LP-guided ISP warm starts

Enable this optional candidate source when constructing the market:

```python
from emst import Market

market = Market(solver_config={
    "stages": {"ISP": {
        "warm_start": True,
        "warm_start_source": "lp",
        "warm_start_repair_seconds": 5.0,
    }}
})
```

The default source is `"schedule"`, preserving boundary-continuation and
previous-ISP behaviour. Warm starts themselves still default to off. Selecting
`"lp"` applies to both initial and noon ISP runs and uses each run's current
forecasts, offers, commercial schedules and actual supplied boundary state.

## Candidate generation

The original full ISP is built once. On a copy of that model:

1. Relax all binary declarations to continuous bounds and solve the economic LP.
2. Retain each relaxed binary value p as a preference, with p close to 0 or 1
   providing stronger guidance than p close to 0.5.
3. Try the rounded preferences in a fixed-commitment dispatch LP on the copy.
4. If those choices are infeasible, release them in the copy and solve the
   feasibility-repair MILP, preserving every original constraint.
5. Validate integrality and original-model primal feasibility, then submit the
   repaired candidate to the original unrestricted economic MILP.

For a binary variable v, the mismatch term is
`p + (1 - 2*p)*v`, which equals `abs(v-p)` at v=0 or v=1. A relaxed preference
of 0.51 therefore does not become an extremely confident instruction to set v=1.
The existing repair objective combines these mismatch terms, original security
slack penalties and a small economic term. It generates a candidate only.

Rounding cannot bypass repair: combinations such as overlapping thermal phases,
interrupted transitions, or minimum-time violations must satisfy the actual model.
No rounded or repaired binary decision is permanently fixed in final clearing.

The guiding LP, rounded dispatch LP and repair MILP share the requested repair
budget. Native solver/model-construction overhead can overshoot requested limits;
all elapsed overhead is included in diagnostics. If the guiding LP is not
optimal, schedule hints are retained. A solver error or lack of a valid repaired
candidate makes final clearing fall back to a cold start. The full final MILP retains its configured economic
objective, time limit and absolute/relative optimality targets.

The LP objective is diagnostic only, not a market price or settlement result.
Pricing still comes from the existing fixed-discrete economic LP after certified
MILP clearing. RTBM and settlement are unchanged.

## Diagnostics

Each enabled ISP run includes the existing `warm_start` diagnostics plus:

- `requested_source`: `"schedule"` or `"lp"`.
- `source`: `"LP_relaxation"` when guiding LP values were used.
- `LP_relaxation_status`, `LP_relaxation_objective_eur` and
  `LP_fractional_binary_count`.
- `LP_relaxation_wall_seconds` and any `LP_guidance_fallback`.

`preparation_wall_seconds` includes guiding LP, repair, copying and hint work.
`repair_solver_wall_seconds` excludes the separately measured guiding LP time.
The stage timing aggregation counts total preparation once.

## Reproducible ten-thermal comparison

The benchmark uses the identical ten conventional units and 48 half-hours from
the earlier relaxed-LP experiment, with synthetic demand/RES and directional
FCR/aFRR/mFRR requirements. It compares cold starts, LP guidance and a pre-existing
initial ISP schedule on identical inputs. Each noon schedule start comes from its
own day's initial ISP; LP guidance solves the current noon LP instead.

```sh
python tools/benchmark_isp_warm_start.py --cases thermal10 --modes cold lp reused --repetitions 3 --output output/lp-guided-10-thermal-comparison
```

Workers run sequentially in fresh processes, alternate ordering across repeats,
use HiGHS with one thread, a 90-second final-MILP limit, zero absolute/relative
gaps and a five-second preparation budget. Initial source-schedule generation is
reported separately, not hidden in previous-ISP warm-start timings. That seed
uses identical forecasts, making it an ideal reuse test rather than evidence of
forecast-prediction quality. Both RTBM resolutions are checked.

Inputs and all stage outputs, including readable financial positions, are saved.
The benchmark distinguishes ISP shortages from RTBM delivery shortages; existing
slacks remain explicit rather than changing inputs or penalties to remove them.
Compare complete ISP/day times, not just the final MILP time.

## Measured result

The [complete timing and validation report](lp-guided-10-thermal-benchmarks.md)
records all repetitions. With five-minute RTBM, median initial-ISP total times
were 7.826 seconds cold, 9.158 seconds with a previous ISP, and 13.926 seconds
with LP guidance. Noon times were 2.336, 3.492 and 7.997 seconds respectively.
These include preparation and pricing; the guiding LP alone was about 0.38/0.19
seconds. Repair, rather than LP solution time, was the main additional expense.
The 30-minute comparison also favoured cold starts. Warm starts remain off by
default; this case does not show a speedup from LP-guided repair.

All 18 measured days and 12 paired comparisons passed their stage audits and
cash reconciliation, with matching dispatch, prices and participant cashflows.
Maximum ISP objective difference was EUR4.55e-12. Physical input fingerprints
match across methods after excluding computational solver options. Nineteen
targeted regression/integration methods passed, including configured plants and
storage with LP guidance in both RTBM modes. A CBC LP-guided repair smoke test
also passed.

Both ten-unit ISP runs have zero energy shortage. The five-minute RTBM retains
the original 0.0081235 MWh delivery shortage in all methods; 30-minute RTBM has
none. This delivery diagnostic is not attributed to LP-guided commitment.
Evidence: `output/lp-guided-10-thermal-verification.json`, benchmark raw/readable
outputs and the `output/lp-guided-*.log` test logs.
