# Optional ISP warm starts with feasibility repair

Warm starts are disabled by default. Enable them independently of configuration
formulation and RTBM resolution:

```python
from emst import Market

market = Market(solver_config={
    "stages": {
        "ISP": {
            "warm_start": True,
            "warm_start_repair_seconds": 5.0,
        }
    }
})
```

For LP-guided preferences instead of boundary/previous-schedule hints, set
`warm_start_source="lp"` in the ISP solver options. See
[LP-guided repair and the ten-thermal benchmark](lp-guided-warm-starts.md).

`warm_start` is a boolean; `warm_start_repair_seconds` is a finite positive number.
The default repair budget is five seconds. These controls affect ISP candidate
generation only. The final clearing MILP retains the configured time limit,
absolute gap and relative gap. Repair is additional work; it does not subtract
from or increase the final MILP's configured solve limit.

## Initial and noon candidates

Without a supplied initial seed, the first ISP uses a boundary-continuation hint:
keep stable configurations/thermal dispatch states, continue inherited thermal
phases and configured transitions, and suggest storage operating directions.
These are preferences, not constraints imposed on market clearing.

A previously solved raw ISP run can supply the initial candidate instead:

```python
result = market.run(
    time_index=market.time_index("2026-11-05"),
    # Other daily inputs, including reserves and historical prices, as usual.
    isp_warm_start=previous_result.isp_runs[0],
)
```

To load a saved seed:

```python
import json
from pathlib import Path

seed = json.loads(Path("previous-results/isp_runs.json").read_text())[0]
```

Seed rows are matched by half-hour `period` index, not variable names or absolute
dates. State/configuration labels must be strings; rows require unique periods
0–47 and asset mappings. Unknown assets are ignored, and missing hints use the
current boundary continuation. Supply seeds from a compatible asset definition.
Changed forecasts, power, SOC and component histories are handled by repair.
Seed dispatch, reserves, prices and cashflows are never copied into clearing.

For the second ISP, EMST automatically takes the initial ISP's remaining
schedule, periods 24–47. Repair uses the actual model boundary passed to the
noon ISP, including its projected noon power/SOC and unfinished transitions.
The first ISP's noon boundary is not substituted for the current boundary.

There is no hidden cache between `Market.run()` calls. In a multi-day run,
boundary state carryover works as before; each initial ISP can generate its
boundary candidate and each noon ISP reuses its own day's first schedule.
Warm-start hints are computational aids, not extra market information/lookahead.

## Repair and full clearing

1. Build the original ISP MILP with all current physical and market constraints.
2. Copy that model, fix suggested integer values **in the copy**, and solve a
   dispatch LP with the original economic objective.
3. If the fixed hint has no validated feasible solution, release those fixes
   **in the copy** and try a repair MILP. Its objective prioritizes avoiding the
   existing security slacks and staying near the hinted binary values, with a
   small economic tie-break term. It can change every commitment decision.
4. Check the candidate against the original bounds, all constraints and integer
   requirements. Integer residuals must be at most 1e-6; after integer snapping,
   maximum primal violation must be at most 1e-5. These are validation tolerances,
   not new physical slack variables or optimality gaps.
5. Submit the complete candidate as a MIP start to the **unrestricted original
   MILP**, whose economic objective and optimality target remain unchanged.

The fixed-hint LP and repair MILP share a requested repair budget, with elapsed
time deducted before a subsequent attempt. Model/hint preparation overhead is
measured separately. Backend construction or native presolve can overshoot a
requested limit; the reported wall times include that work. A time-limited repair incumbent
can be used only if the full original-model validation passes. It is a starting
point, never a certified market result. Without a valid candidate, clearing
falls back to a cold solve. A native start submission is not a claim that the
solver adopted that start or that it improved runtime.

The existing model's energy/reserve security slacks remain possible in a
candidate and are explicitly reported. No extra physical relaxation is added.
Final physical-security classification and all stage audits remain authoritative.
The final MILP must still terminate successfully under the existing solver policy;
a time-limited market incumbent is not admitted to settlement. Fixed-discrete LP
pricing, RTBM and settlement are unchanged.

HiGHS receives the candidate through its native `setSolution` interface. CBC uses
its MIP-start file interface. CPLEX is not a production backend in this API.

## Diagnostics and examples

When enabled, each raw ISP run has `warm_start` diagnostics: hint source/count,
repair attempts/method/status, candidate availability/objective/security slacks,
changed binary count, primal/integer residuals, submission status, repair solver
time and total preparation wall time. The primary solve record includes the same
metadata. `solver_wall_seconds_by_stage["ISP"]` includes the preparation overhead
once, alongside the original ISP solver calls. Individual primary records retain
their own final-MILP `wall_seconds`.

```sh
python examples/library_day.py --isp-warm-start --storage-energy --storage-capacity --output warm-day
python examples/configured_plant.py --configuration-formulation compact --isp-warm-start --rtbm-interval-minutes 30 --output warm-ccgt
python tools/benchmark_isp_warm_start.py --repetitions 3 --output output/isp-warm-start-replay
```

The benchmark runs fresh sequential workers with matched HiGHS/thread/time/gap
settings. It compares cold starts, boundary-generated warm starts, and warm starts
from a pre-existing identical-forecast solution, separately for five-minute and
30-minute RTBM. Seed-generation costs are recorded outside the measured reused
run. The reused case is an ideal repeated-forecast test, not evidence of prediction
quality under forecast changes. Dedicated tests exercise illegal hints, inherited
transitions, storage SOC repair, cold fallback and unrestricted final improvement.

Warm starts may be slower when the cold MILP is already easy. They also do not
eliminate presolve costs or guarantee a smaller optimality gap within a time
limit. Compare total runtime including repair, not only the final MILP time.

The optional `--cases synthetic` benchmark uses the existing configured-fleet
small fixture as an additional diagnostic. Its economic optimum uses about
0.0175 MWh of existing ISP shortage slack in one MTU, so it is explicitly labelled
as a slack-aware performance case, not a security-feasible operating day.
The ordinary storage and configured daily examples require zero security slack.

See the [measured benchmark report](isp-warm-start-benchmarks.md). Across 54
measured daily runs (three repetitions, both RTBM resolutions, three start modes
and three scenarios), certified ISP objectives matched within EUR1e-9 and RTBM
dispatch, prices and participant cashflows matched exactly. Most complete ISP
calls were slower after including repair. In the additional synthetic diagnostic,
boundary hints improved initial-ISP time but often increased noon-ISP time;
reusing the optimal historical schedule was slower. These results support keeping
the feature optional and off by default.

Warm-started two-day continuous versus checkpoint/resume validation also passed
at both RTBM resolutions. All results except timing fields matched, including
financial positions, solver outcomes and diagnostics. An unfinished midnight
transition and component clocks were preserved without repeating transition
costs. Reproduce in fresh output directories:

```sh
python tools/validate_isp_warm_resume.py --minutes 5 --output output/warm-resume-5
python tools/validate_isp_warm_resume.py --minutes 30 --output output/warm-resume-30
```

Local evidence: `output/isp-warm-resume-final-5/verification.json` and
`output/isp-warm-resume-final-30/verification.json`.

Regression evidence covers 178 cases. The initial full run passed 177; one
checkpoint result comparison crossed a live source-code update and consequently
compared different implementation fingerprints. That test passed in both RTBM
modes when rerun with frozen source. The final seven focused warm-start tests
also passed, including the zero-objective case. Original full-run evidence is
retained in `output/isp-warm-start-tests/unit-tests-highs.json` and its log;
the successful rerun is `output/isp-warm-start-tests/frozen-checkpoint-rerun.json`.
