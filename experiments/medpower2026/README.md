# Sequential-market participant-price experiments

Release `medpower2026-paper33-v4` contains MIT-licensed code, synthetic inputs and results. Manuscript PDF and LaTeX are excluded. A paper reference will be added after publication.

## Reproduce

Install the recorded dependency versions once from the repository root:

```sh
python -m pip install -r experiments/medpower2026/requirements-lock.txt
```

Then run:

```sh
python experiments/medpower2026/reproduce.py
```

This overwrites results and timings, runs seven scenarios, three resolutions, allocation/mark-up diagnostics, independent audits, four PNG figures and 17 tests (13 library plus 4 extension). Recorded environment: CPython 3.14.7, PuLP 3.3.2, CBC 2.10.3, Windows 11, Intel Core i5-13420H. CBC must be available to PuLP. Excel is not required. Package pins do not guarantee identical runtimes or block selections across platforms.

## Design

Three suppliers represent industrial (IND), commercial (COM) and residential (RESI) demand. IND baseline DAM demand is flat at 30% of mean aggregate demand. COM and RESI divide the remainder using hand-set daytime/evening weights. MTU shares remain fixed under forecast updates, when IND need not remain flat. Matched PV portfolios each have 200 MW and wind portfolios 78.5 MW. Five thermal participants retain snapshot capacities and assumed costs.

PV-C contracts 60% of its baseline DAM forecast to COM at EUR85/MWh and 10% to RESI at EUR90/MWh. W-C contracts 60% to IND at EUR100/MWh and 10% to RESI at EUR105/MWh. Schedules stay fixed across scenarios. Coverage of supplier purchases is about 0.7% for IND and 12.1% for COM, so comparisons combine profile and coverage effects.

Seven cases are baseline, low/high RES, high demand, and no/doubled/reversed revision. Low/high RES scale both capacity and output by 0.7/1.3 and represent portfolio-size changes. IA2/IA3 reverse part of IA1's update. No-revision has no material IA trading. Supplier buys bid at 500 EUR/MWh throughout. IA sales by suppliers/renewables offer (1-mu) times DAM price, renewable buybacks bid (1+mu) times DAM price, thermal upward sales offer (1+mu) times DAM price, and thermal buybacks bid at cost. Baseline mu=10%, with extra 5% and 20% tests.

## Allocation extension

The clearing library is unchanged. `tie_policy.py` uses its public solver interface for simple orders and independent binary blocks only. It sorts bids canonically, runs CBC and the library's LP pricing/block rejection, retains selected blocks, completes remaining equal-price buy/sell trades, and allocates each MTU/side/exact-price group's simple volume pro rata. Primary prices remain unchanged except newly active zero-surplus MTUs use their common bid price. Assertions check bounds, balance and unchanged bid surplus.

Alternative optimal block selections remain unresolved. `robustness_analysis.py` compares this rule with canonical CBC allocation and pro rata alone. All seven reversed-input checks pass. Allocation choices materially affect results, so this is a declared experimental policy, not a unique market prediction.

## Metrics and diagnostics

Positions start at zero before FM, with positive sales and negative purchases. VWAPs use positive magnitudes, separately for purchases/sales. Auction VWAP counts buys once and excludes MTUs at or below 1e-4 MWh. Blank VWAP means no active volume. The 504 summaries are 12 participants x 7 scenarios x (5 stages + ALL total). Stage-row final target/gap fields refer to final IA3. Internal IDs IDA1-IDA3 correspond to IA1-IA3 in the paper.

`results/signed_gaps.csv` separates above/below signed targets. Baseline material gaps are excess supplier purchases and unsold wind forecasts. `unfilled_orders.csv` shows remaining IA3 sales face lower-priced buyers. These are unfilled commercial targets, not observed physical shortages. Their settlement is not priced.

`price_decomposition.csv` attributes participant price minus DAM-wide VWAP to contract, timing and intraday terms. Each uses gross side volume as denominator. Numerators are transaction-weighted FM price minus same-MTU DAM price, same-MTU DAM price minus DAM-wide VWAP, and IA price minus same-MTU DAM price respectively. This exact identity is descriptive, not causal or a profit measure.

Low RES lowers DAM VWAP slightly with unchanged MTU buy volumes. Changed VAS_ST_H block acceptance/rejection raises prices in two MTUs and lowers them in four. This is a block/rejection effect, not volume weighting. `block_diagnostics.csv` records choices. `dual_sign_test.json` verifies a +40 EUR/MWh dual by perturbing buy minus sell equals RHS in a surplus-maximising LP, consistent with direct library dual extraction.

## Provenance, resolution and limits

`input_provenance.json` identifies `emst_cyprus_case_study_template.xlsx`, snapshot version 1, by full SHA256. `case_inputs.json` is the value-only snapshot. Profiles were transcribed from a user-supplied image and assets supplied by the user, not calibrated operator records. The expanded case constructs its own participants and contracts. VAS_ST_H is 390 MW and DKL_ICE_H 105 MW, without Moni. These inputs are not reconciled with the separate Cyprus_Grid.sqlite/FlexTool fleet. The 2025-01-01 date is a delivery label, not a calibrated working-day/holiday profile.

Half-hour power is piecewise constant. Fifteen-minute MWh values split parents equally, and hourly MWh values sum neighbours, preserving six-hour blocks. All twelve participant buy/sell VWAPs agree within 1e-6 EUR/MWh under the selected policy. This supersedes the allocation-sensitive v3 resolution result and does not establish general resolution invariance. `resolution_auctions.csv` and `resolution_participants.csv` report comparisons.

One warm-up and five repetitions per resolution time FM, bids, four auctions, pricing/rejection, allocation, transaction records and checks. Input construction/export are excluded. All IAs can trade all MTUs. Networks, full commitment, reserves, inertia, gate closures and imbalance settlement are absent. Child/circular blocks and IA blocks are implemented but not exercised. Iterative rejection is a heuristic, not globally optimal non-convex pricing or a Euphemia replica.

## Files

- `run_experiments.py`, `tie_policy.py`: scenario construction and allocation extension.
- `robustness_analysis.py`: additional policy/mark-up runs, price decomposition, gaps and clearing diagnostics.
- `audit_results.py`, `audit_auction_prices.py`, `audit_resolution.py`: independent checks.
- `test_tie_policy.py`, `plot_results.py`: focused tests and four standalone PNG figures, including DAM/IA1 renewable profiles.
- `results/trade_ledger.csv`: transaction records, retaining the legacy filename. Monetary fields support price arithmetic, not profit claims.
- `results/participant_metrics.csv`: all twelve participants and stages plus totals.
- `results/forecasts.csv`, `contracts.csv`, `positions_prices.csv`, `auction_vwaps.csv`: inputs and outcomes.
- `results/robustness_*.csv`, `tie_policy_sensitivity.csv`, `input_order_checks.csv`: additional diagnostics.
- `results/timings.csv`, `environment.json`, `audit.txt`: runtime, environment and verification.
- `requirements-lock.txt`, `SHA256SUMS.json`: dependency pins and published artifact hashes.

The root LICENSE supplies the MIT licence. No profitability claim is made.
