# ISP warm-start benchmarks

Python 3.14.10, HiGHS 1.16.0, one thread, fresh sequential workers. Three measured repetitions per case/resolution/mode, alternating order. Every primary MILP retains a 90-second limit and zero absolute/relative optimality gaps. Warm runs add a five-second repair budget; total elapsed times include repair, candidate submission and all market stages.

All inputs are public synthetic examples. Storage includes RES, forwards, DAM storage blocks, must-run procurement, balancing energy/capacity and daily settlement. Configured uses the compact combined-cycle example. Synthetic uses the more demanding compact configured-fleet small case with two conventional units and directional reserve requirements. The latter is a separately labelled slack-aware diagnostic: its optimum uses 0.01749277 MWh existing energy-shortage slack in MTU27 of each ISP run, economically preferred to starting another unit. No physical constraints, input data or slack penalties were changed to remove that shortage.

`boundary` generates its initial hint from boundary states. `reused` receives a pre-existing identical-forecast initial ISP solution. Both warm modes use the current initial ISP remainder for the noon run. Historical seed generation is timed and saved separately in each summary; it is not free if the user has to solve a new case solely to create that seed. Reused-mode results do not establish performance under changed forecasts.

ISP total is the complete dispatch call, including construction, repair, final MILP and fixed-discrete pricing LP. Repair includes hint/model-copy preparation and its LP/MILP attempts. Columns are independent repeat medians and need not add exactly. Day time includes every stage and result enrichment, excluding file export and process startup.

| Case | RTBM min | Mode | ISP1 repair s | ISP1 final MILP s | ISP1 total s | ISP2 repair s | ISP2 final MILP s | ISP2 total s | Day s |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| storage | 5 | cold | 0.000 | 0.228 | 0.387 | 0.000 | 0.079 | 0.160 | 12.470 |
| storage | 5 | boundary | 0.202 | 0.145 | 0.512 | 0.099 | 0.076 | 0.258 | 12.429 |
| storage | 5 | reused | 0.200 | 0.143 | 0.515 | 0.106 | 0.079 | 0.272 | 13.272 |
| storage | 30 | cold | 0.000 | 0.262 | 0.446 | 0.000 | 0.094 | 0.183 | 2.173 |
| storage | 30 | boundary | 0.219 | 0.163 | 0.567 | 0.110 | 0.086 | 0.290 | 2.482 |
| storage | 30 | reused | 0.213 | 0.164 | 0.553 | 0.112 | 0.084 | 0.287 | 2.457 |
| configured | 5 | cold | 0.000 | 1.232 | 1.528 | 0.000 | 0.575 | 0.746 | 9.624 |
| configured | 5 | boundary | 0.330 | 1.254 | 1.901 | 0.162 | 0.423 | 0.747 | 9.953 |
| configured | 5 | reused | 0.317 | 0.947 | 1.590 | 0.172 | 0.454 | 0.822 | 9.722 |
| configured | 30 | cold | 0.000 | 1.195 | 1.500 | 0.000 | 0.587 | 0.741 | 2.595 |
| configured | 30 | boundary | 0.331 | 1.218 | 1.861 | 0.195 | 0.444 | 0.796 | 3.034 |
| configured | 30 | reused | 0.331 | 0.922 | 1.573 | 0.192 | 0.455 | 0.866 | 2.909 |
| synthetic | 5 | cold | 0.000 | 6.235 | 6.613 | 0.000 | 2.042 | 2.243 | 20.142 |
| synthetic | 5 | boundary | 0.422 | 4.614 | 5.459 | 0.219 | 2.661 | 3.094 | 18.878 |
| synthetic | 5 | reused | 0.424 | 10.846 | 11.675 | 0.216 | 2.716 | 3.121 | 25.186 |
| synthetic | 30 | cold | 0.000 | 5.255 | 5.581 | 0.000 | 1.733 | 1.899 | 7.880 |
| synthetic | 30 | boundary | 0.381 | 4.033 | 4.787 | 0.225 | 2.389 | 2.838 | 8.076 |
| synthetic | 30 | reused | 0.404 | 9.307 | 10.071 | 0.215 | 2.332 | 2.726 | 13.336 |

## Correctness

Every daily run passed all stage audits and reconciled participant cashflows. Storage and configured examples used no security slack; the additional synthetic diagnostic retains its explicitly reported existing ISP shortage. It is not a security-feasible operating-day claim. Model dimensions and certified ISP objectives match across cold/warm pairs. Across 36 paired comparisons, maximum differences were: ISP objective EUR3.60159902e-10; RTBM dispatch 0 MW; RTBM price EUR0/MWh; settlement price EUR0/MWh; participant daily cash EUR0e-23.

The final economic model is not restricted by the start. Dedicated tests verify a final MILP can improve the repaired commitment, correction of an illegal initial state, uninterrupted inherited transitions with actual entry power, storage SOC-target repair, budget-exhausted cold fallback, both configuration formulations and native HiGHS/CBC start submission. Final time-limited market incumbents remain inadmissible to settlement.

Warm-started two-day continuous/checkpoint-resumed results matched completely except clocks at both RTBM resolutions, including economics, component histories, an unfinished midnight transition and once-only transition costs. Evidence: `output/isp-warm-resume-final-5/verification.json` and `output/isp-warm-resume-final-30/verification.json`. The final focused warm-start tests also cover a zero-price constant objective, where PuLP adds an auxiliary column during solve.

Regression evidence covers 178 cases: the initial full run passed 177, with one checkpoint comparison crossing a live source-code update and comparing different implementation fingerprints. That test passed in both RTBM modes on a frozen-source rerun. The original full-run result is retained alongside `output/isp-warm-start-tests/frozen-checkpoint-rerun.json`; it has not been rewritten as a single all-green run. The final seven focused warm-start methods also passed.

Warm starts are disabled by default. Compare complete ISP/day time: candidate preparation can outweigh reductions in the final MILP. These small daily cases do not establish that warm starts solve the large-fleet HiGHS presolve bottleneck.

## Reproduction and evidence

```sh
python tools/benchmark_isp_warm_start.py --repetitions 3 --output output/isp-warm-start-benchmarks
python tools/benchmark_isp_warm_start.py --cases synthetic --repetitions 3 --output output/isp-warm-start-synthetic-slack-diagnostic
python tools/run_tests.py --output output/isp-warm-start-tests
```

Each worker saves raw/readable results (including financial positions), seed JSON, timings, full warm-start diagnostics and a worker log. Each panel saves all individual runs, paired comparisons, seed-generation costs and medians. Evidence directories: `output/isp-warm-start-benchmarks`, `output/isp-warm-start-synthetic-slack-diagnostic`.

See [configuration and repair details](isp-warm-starts.md).
