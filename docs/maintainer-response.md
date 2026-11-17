# Investment-integration review response — 17 November 2026

The external review evaluated `ed5f39ffc427477d9863405ac3bd09f22eed76fb`
(0.3.1). This response accompanies the 0.3.2 integration milestone. It accepts
the demonstrated defects without treating all requested planning capabilities
as existing market-model defects. No change establishes official Cyprus rules
conformance, calibration or investment optimality.

| Request | Disposition in 0.3.2 |
|---|---|
| R01 storage smoothing | Reproduced the unchanged regression on HiGHS 1.16.0 before the fix. Use scale-aware objective preservation, physical/economic validation and explicit restoration of the certified primary solution on optional failure. Preserve pricing LP prices and record smoothing outcomes. Add forced-fallback price/feasibility checks. |
| R02 actual balance/reliability | Add separate optimization/forecast/actual diagnostics, directional residual totals/maxima/counts and a declared RTU tolerance. Preserve raw intervals. Explicitly mark load-loss reliability unassessed; no physical recourse or EENS/LOLE inference. |
| R03 launcher | Establish repository imports before package/example discovery, report collection errors and actual loaded-method counts, and exercise the public launcher in CI. |
| R04 solver contract | Copy global/stage settings for backend, budget, gaps, threads and logs. Record statuses, quality, timing, bounds/gaps where available, and pricing/smoothing purposes. Retain strict optimal termination; no timeout-incumbent settlement. Arbitrary native options and purpose-specific budgets are deferred. |
| R05 economics | Export explicit asset cash attribution, unallocated participant remainder, executed operating costs and reconciliation. Keep physical cost, market transfers and investment recovery separate. Shared-charge and contractual BRP allocation remain adapter decisions. |
| R06 ownership | Add economic-owner groups above unit-specific participants, retaining asset/participant/BRP distinctions. Regrouping does not coordinate bidding. Export owner cash and separate asset economics. |
| R07 policies | Strictly validate built-in names/parameters and copy input configuration. Custom policy registration, arbitrary stage-specific offers and price-maker storage remain a separate specification; this milestone does not claim policy equivalence with the old prototype. |
| R08 study boundaries | Document independent-day and chronological/month workflows, inventory value, bootstrap/state requirements and monthly procurement assumptions. Add opt-in realized terminal SOC independently of proposed-profile targets. Terminal economic values and automatic weighted-day monthly/annual settlement are not implemented. |
| R09 evaluation contract | Add schema-versioned compact success/failure exports, source/input/policy fingerprints, solve records, economics and state. Record chronological mode; recommend process isolation and candidate-specific checkpoints/caches. Failures do not become fabricated investment observations. |
| R10 optional extensions | Keep DST, interconnections/intraday, reliability recourse and official calibration outside this milestone. Normal-day isolated-market limits remain explicit. |

The original evidence does not prove a HiGHS-version regression or that all
storage cases fail. The fixed smoothing face remains numerical postprocessing;
the explicit validated fallback is part of the supported contract, rather than
reclassifying an optional numerical failure as physical market infeasibility.

The prior exact-equality check after a causal measurement spike was also refined:
the restored RES add-back differed by about `3.64e-7` MW with the new smoothing
tolerance. The test retains exact checks before the observation becomes available
and the separate 8 MW causal-response assertion. Restored numeric predictions
are compared within `1e-6` MW, with discrete fields exact.

## Validation procedure

- Run `python tools/run_tests.py` on Windows/Python 3.14.8 with PuLP 3.3.2 and
  HiGHS 1.15.1, then with the reviewers' exact HiGHS 1.16.0 environment.
- Include the unchanged full-day storage regression and new planning tests.
- Build/install the wheel and run the daily example through the installed package.
- Run `examples/planning_pilot.py`: paired 0/10/20 MW cases, ownership grouping,
  explicit realized storage endpoints, biased forecasts, cash/cost reconciliation,
  and repeated baseline isolation. These runs are not controlled timing benchmarks.
- Run a separate complete month through UA1/UA2 closure, retaining must-run
  service payments/recovery and no meter reconciliation.
- Retain checkpoint/resume comparisons of all economic/physical/status fields;
  exclude runtime clocks only. Reject changed capacity or solver configurations
  when resuming.

Local logs and simulation exports are generated under `output/`; bulky/private
operator reports are not committed. CI repeats the public command on the declared
Windows/Linux, Python 3.11/3.14, HiGHS 1.15.1/1.16.0 matrix. CI outcomes are separate
from local validation; adding the workflow alone does not establish all matrix
combinations passed.

The public extraction/API conventions and remaining limits are detailed in
[planning integration](planning-integration.md). The investment optimizer,
annual weights, financing, final recovery thresholds and shared-account economic
allocations belong to the consuming adapter.


## Local validation results

Completed on Windows, Python 3.14.8 and PuLP 3.3.2:

| Environment/check | Result |
|---|---|
| HiGHS 1.15.1, public launcher | 129 actual methods collected; 128 passed, one optional pandas test skipped; no collection errors |
| Review environment, HiGHS 1.16.0 with pandas | All 129 actual methods passed; no collection errors or skips |
| Unchanged reported storage regression | Reproduced failure before the fix; passed after it on 1.16.0 and in both complete suites |
| Wheel build/install and isolated installed daily script | Passed; all 33 stage checks passed |
| Five-case planning pilot on 1.16.0 | Passed all cases, endpoint checks, participant accounting and repeated-baseline isolation |
| Separate full month on 1.15.1 | All 30 days and 8,640 executed RTUs passed their audits; every participant's cash attribution reconciled; no security slack |
| Monthly closure | UA1/UA2 present; must-run daily/monthly cash agrees; no meter reconciliation |

The month used one consistent implementation fingerprint throughout. Its synthetic
must-run service payments and supplier recovery were EUR144,000 and EUR-144,000.
These checks do not establish load-loss reliability, calibration, controlled
runtime comparisons, or compatibility results for CI combinations not yet run.
