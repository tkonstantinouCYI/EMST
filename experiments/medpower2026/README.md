# Sequential-market participant-price experiments

This directory contains the code, synthetic inputs and recorded results for the participant-price case study. The release `medpower2026-paper33-v3` pins the experimental materials. The manuscript PDF and LaTeX source are not distributed here. A reference to the paper will be added after publication.

## Reproduce the results

Use CPython 3.14.7 for the recorded environment. Install the pinned dependencies once from the repository root:

```sh
python -m pip install -r experiments/medpower2026/requirements-lock.txt
```

Then reproduce the seven scenarios, independent audits, resolution comparisons, PNG figures and library tests with one command:

```sh
python experiments/medpower2026/reproduce.py
```

PuLP must have a working CBC executable. The recorded results used Python 3.14.7, PuLP 3.3.2 and CBC 2.10.3 on Windows 11 with an Intel Core i5-13420H. Other solvers or versions can select different equally optimal allocations. CPU metadata collection also supports non-Windows platforms, but the reported timings are Windows measurements. One warm-up and five measured runs are made for each temporal resolution. Expect runtime differences across machines.

The driver reads the included JSON snapshot, so Excel and openpyxl are not required. Running it replaces the CSV results in this directory. The supplied CSV files contain the price and volume values used for the comparisons. To regenerate standalone PNG figures, install `matplotlib>=3.8,<4` and run `python experiments/medpower2026/plot_results.py`. No manuscript files are required.

To audit the supplied results without rerunning the solver, run only `audit_results.py`. The original 13 library tests and the transaction audit pass.

## Scenario and participant design

- Three suppliers represent industrial (IND), commercial (COM) and residential (RESI) demand. Their synthetic MTU shares sum exactly to the original aggregate demand. IND day-ahead demand is constant at 30% of mean demand. COM and RESI share the remainder using the daytime and evening weights specified in the paper and driver. The shares are frozen across forecast updates and scenarios. IND therefore need not remain flat after aggregate forecast updates.
- PV-C and PV-A each have half the PV profile and 200 MW baseline capacity. W-C and W-A each have half the wind profile and 78.5 MW baseline capacity. Matched pairs have identical forecasts.
- PV-C contracts 60% of its baseline DAM profile to COM at EUR85/MWh and 10% to RESI at EUR90/MWh. W-C contracts 60% to IND at EUR100/MWh and 10% to RESI at EUR105/MWh. The prices are illustrative. Contract schedules stay fixed across all scenarios.
- Seven re-cleared cases are baseline, low RES, high RES, high demand, no forecast revision, doubled revision and reversed revision. Low/high RES scale renewable capacity and output together and represent portfolio-size changes, not forecast changes for a fixed installed fleet. They are perturbations of one illustrative day, not seven observed days. IDA2 and IDA3 synthetically reverse part of the original IDA1 update.
- All three IDAs can trade every delivery MTU. Network constraints, realistic gate closures, full unit commitment and balancing settlement are absent.

Supplier purchases bid at the synthetic ceiling of 500 EUR/MWh in DAM and every IA. Supplier resales and renewable sales bid at 0.9 times the DAM MTU price in IAs. Renewable buybacks retain 1.1 times that price. This ceiling is a modelling assumption, not a regulatory price-limit claim.

Auction-wide VWAPs count accepted purchases once and exclude MTUs with volume at or below 1e-4 MWh. No active MTUs gives a blank VWAP. The audit exports supplier purchase shortfalls separately from excess purchases and renewable absolute gaps. All final supplier shortfalls are below 3e-6 MWh in the recorded scenarios. Internal data stage names remain IDA1–IDA3, corresponding to IA1–IA3 in the paper.

## Inputs, provenance and limits

`case_inputs.json` preserves a value-only snapshot of the original case workbook and its SHA256 hash. The source workbook's aggregate profiles were transcribed from a user-supplied image and fleet inputs were user-provided. They are not independently calibrated historical data. Original snapshot labels such as `cyprus_actual_single_day` refer to the source workbook, not a validation claim. The driver constructs the expanded twelve-participant case explicitly and does not use the snapshot's original participant list or forward schedules.

The low reference wind output means W-C's contracts cover 70% of its baseline DAM forecast but only 18.3% of its gross baseline sales after intraday revisions. IND's wind contract covers less than 1% of its purchases. Supplier comparisons combine profile and coverage effects.

The paper reports gross purchase and sales prices separately, along with volumes and remaining forecast-target gaps. Those gaps are commercial exposure, not actual unserved energy. The manuscript does not report net income, profit, or a statistical risk measure. Monetary intermediate fields remain in the raw ledger/metrics for checking quantity-weighted prices. The earlier fixed-volume price-shock exercise is not part of this release.

## Files

| File | Contents |
| --- | --- |
| `run_experiments.py` | Participant construction, seven scenarios, FM-DAM-IDA1-IDA2-IDA3 clearing, checks and timing |
| `audit_auction_prices.py` | Independent auction VWAP and disaggregated position-gap audit |
| `results/auction_vwaps.csv`, `position_gap_audit.csv` | Auction prices and separated supplier shortfalls/excess purchases |
| `audit_results.py` | Independent transaction, weighted-price and matched-profile audit |
| `plot_results.py` | Standalone PNG participant price and volume-mix figures |
| `results/forecasts.csv`, `contracts.csv` | Explicit scenario forecasts and fixed priced contracts |
| `results/trade_ledger.csv` | Participant, stage, MTU, side, accepted MWh, price and order identifier |
| `results/participant_metrics.csv` | Twelve participants, seven scenarios, ALL/FM/DAM/IDA1-IDA3 summaries |
| `results/participant_prices.csv` | The 49 supplier-buy and renewable-sell prices in the participant-price figure |
| `results/scenario_summary.csv`, `positions_prices.csv` | Market outcomes and cumulative positions |
| `results/verification.csv`, `audit.txt` | Recorded checks |
| `results/timings.csv`, `environment.json` | Raw repetitions and recorded environment/provenance |
| `SHA256SUMS.json` | Hashes of the published case-study files |

On stage rows in `participant_metrics.csv`, `final_target_mwh` and `final_gap_mwh` always refer to the final IDA3 target and position. Price means use gross accepted quantities, not an unweighted average of stage prices. The manuscript uses ALL rows for participant-wide means.

## Licence, input version and temporal resolution

The repository and experiment code use the MIT licence in the root `LICENSE`. `input_provenance.json` identifies `emst_cyprus_case_study_template.xlsx`, snapshot version 1, by its full SHA256. This snapshot version labels the preserved inputs rather than claiming a workbook-supplied version number. The JSON snapshot is sufficient for reproduction, so no workbook software is needed.

Positive transaction magnitudes are used in VWAPs, with direction stored in `side`. Cumulative positions instead use positive sales and negative purchases. CBC prices use the dual of `accepted buys - accepted sells = 0` directly in the surplus-maximising LP, without negation.

Resampling integrates piecewise-constant half-hour power: 15-minute energy is half the parent interval and 60-minute energy is the sum of two adjacent intervals. The resolution audit exports `resolution_auctions.csv` and `resolution_participants.csv`. Although energy is preserved, economic outcomes are not identical. DAM/IA1 VWAPs agree within 1e-6 EUR/MWh, while later intraday VWAPs and participant allocations differ. Equal-price allocation choices can propagate into subsequent adjustment orders.

RESI denotes the residential supplier. RES in scenario names denotes renewable energy sources. This release changes the residential participant label from RES to RESI without changing bidding or clearing logic. Recorded timings remain the original five repetitions per resolution. Running the single reproduction command replaces them with new measurements for the current machine. The exact Python package pins do not guarantee identical runtimes or equally optimal allocations on other platforms.
