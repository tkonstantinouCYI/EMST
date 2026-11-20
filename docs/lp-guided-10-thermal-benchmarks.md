# Ten-thermal LP-guided warm-start comparison

Synthetic 48-half-hour ISP, ten conventional thermal units, demand/RES and directional FCR/aFRR/mFRR reserves. Python 3.14.10, HiGHS 1.16.0, one thread, fresh sequential worker processes, three repetitions and alternating solve order. Every final MILP retains the same 90-second limit and zero absolute/relative gaps.

Inputs exactly reuse `tools/benchmark_relaxed_isp.py`; no ramp, transition, minimum-time, reserve or other physical constraint is relaxed in final clearing. Initial/noon final-MILP dimensions are identical across modes. Each LP-guided preparation shares a five-second requested budget across guiding LP and repair. Overhead and native-limit overshoot are included in complete elapsed time.

Previous-ISP mode uses a pre-existing identical-forecast initial ISP result; its historical seed-generation cost is retained separately in summary JSON. The noon schedule seed comes from that run's own initial ISP. This is an ideal previous-schedule test, not a learned prediction. LP mode solves the current relaxation at both ISP runs.

## Median timings

Preparation includes LP guidance, copying and feasibility repair; guiding LP is a subset, not an additional amount to add to preparation. ISP total includes construction, preparation, final MILP and fixed-discrete pricing. Independent medians need not sum exactly.

### ISP 1 — initial, 48 MTUs

| RTBM min | Start | Guiding LP s | All preparation s | Final MILP s | ISP total s | Submitted starts / attempts |
|---:|---|---:|---:|---:|---:|---:|
| 5 | cold | 0.000 | 0.000 | 7.333 | 7.826 | 0/0 |
| 5 | lp | 0.382 | 5.621 | 7.791 | 13.926 | 3/3 |
| 5 | reused | 0.000 | 0.653 | 7.984 | 9.158 | 3/3 |
| 30 | cold | 0.000 | 0.000 | 7.579 | 8.110 | 0/0 |
| 30 | lp | 0.452 | 5.777 | 8.320 | 15.031 | 3/3 |
| 30 | reused | 0.000 | 0.700 | 8.315 | 9.553 | 3/3 |

### ISP 2 — noon, 24 MTUs

| RTBM min | Start | Guiding LP s | All preparation s | Final MILP s | ISP total s | Submitted starts / attempts |
|---:|---|---:|---:|---:|---:|---:|
| 5 | cold | 0.000 | 0.000 | 2.030 | 2.336 | 0/0 |
| 5 | lp | 0.185 | 4.871 | 2.870 | 7.997 | 3/3 |
| 5 | reused | 0.000 | 0.668 | 2.657 | 3.492 | 3/3 |
| 30 | cold | 0.000 | 0.000 | 2.037 | 2.315 | 0/0 |
| 30 | lp | 0.195 | 4.721 | 2.983 | 7.975 | 3/3 |
| 30 | reused | 0.000 | 0.327 | 2.683 | 3.287 | 3/3 |

### Complete day

| RTBM min | Start | Day s |
|---:|---|---:|
| 5 | cold | 40.986 |
| 5 | lp | 48.537 |
| 5 | reused | 38.755 |
| 30 | cold | 11.259 |
| 30 | lp | 23.825 |
| 30 | reused | 13.619 |

### Paired differences

Median of each warm-minus-cold difference within the same repetition; positive means slower. Day timings vary materially across repeats. Comparing independently computed day medians can reverse the apparent direction, so use these paired differences alongside the individual run data.

| RTBM min | Warm source | Initial ISP delta s | Noon ISP delta s | Complete day delta s |
|---:|---|---:|---:|---:|
| 5 | lp | 6.100 | 5.661 | 7.551 |
| 5 | reused | 1.481 | 1.160 | 1.576 |
| 30 | lp | 6.834 | 5.345 | 12.565 |
| 30 | reused | 1.438 | 0.972 | 2.360 |

## Validation and limitations

All 18 measured daily runs passed stage audits and participant cash reconciliation. Across 12 paired comparisons, maximum ISP objective difference was EUR4.54747351e-12; dispatch difference 0 MW; RTBM price difference EUR0/MWh; settlement price difference EUR0/MWh; participant cash difference EUR0e-29.

Nineteen targeted regression/integration methods passed (prior warm-start controls/repair, LP-guided repair, configured/storage daily replay in both RTBM modes and solver/planning controls). CBC LP-guided repair also passed a smoke test. Physical input fingerprints match across modes after excluding solver options; evidence is in `output/lp-guided-10-thermal-verification.json` and `output/lp-guided-*.log`. A new full-library regression run is not claimed for this change.

The guiding LP has fractional commitment values. Rounding is only a suggestion: repair enforces the full original model before a candidate can be submitted. A repair MILP may reach its time limit with a validated feasible incumbent; that is allowed only as a starting point. Final clearing must still meet its original optimal-termination policy. LP or repair prices are never used in settlement.

The same ten-unit inputs are retained even when fine-resolution delivery needs the existing security slack. This is reported below, not hidden or removed by changing penalties. ISP energy slack and RTBM delivery slack have different meanings. These rows describe the first cold repeat for each resolution; all individual rows remain in metrics/raw files.

| RTBM min | Initial ISP shortage MWh | Noon ISP shortage MWh | RTBM shortage MWh | RTBM surplus MWh |
|---:|---:|---:|---:|---:|
| 5 | 0 | 0 | 0.00812349867 | 4.90900685e-09 |
| 30 | 0 | 0 | 0 | 0 |

All modes retain the full unrestricted MILP. Smaller LP time does not imply a faster feasible integer solution: repair itself can require another substantial integer solve. Warm starts remain disabled by default; no universal speedup is inferred.

## Replay

```sh
python tools/benchmark_isp_warm_start.py --cases thermal10 --modes cold lp reused --repetitions 3 --output output/lp-guided-10-thermal-comparison
python -m unittest discover -s tests -p test_lp_warm_start.py -v
```

Every input, raw/readable result, financial position, seed, solver option and timing is retained under `output/lp-guided-10-thermal-comparison`. See [LP-guided configuration and repair](lp-guided-warm-starts.md).
